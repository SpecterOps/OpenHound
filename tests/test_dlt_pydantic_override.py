import logging

import pytest
from dlt.common.libs import pydantic as dlt_pydantic
from dlt.common.schema.exceptions import DataValidationError
from dlt.extract.validation import PydanticValidator
from pydantic import BaseModel

from openhound.core import validate


class ExampleModel(BaseModel):
    id: int
    required_field: str


@pytest.fixture(autouse=True)
def patch_dlt_validation(monkeypatch):
    monkeypatch.setattr(dlt_pydantic, "create_list_model", validate.create_list_model)
    monkeypatch.setattr(
        dlt_pydantic,
        "_classify_validation_errors",
        validate._classify_validation_errors,
    )


def _validator(column_mode: str = "evolve", data_mode: str = "discard_row"):
    validator = PydanticValidator(ExampleModel, column_mode, data_mode)
    validator.table_name = "users"
    return validator


def test_dlt_pydantic_discard_log(caplog):
    """Checks if the validator (with discard_row) logs data type errors"""
    caplog.set_level(logging.WARNING, logger="dlt")

    result = _validator()(dict(id="not-an-int"))

    assert result is None
    assert len(caplog.records) == 1
    record = caplog.records[0]

    assert record.resource == "users"
    assert record.pydantic_errors[0]["type"] == "int_parsing"
    assert record.pydantic_errors[0]["loc"] == ("id",)
    assert record.pydantic_errors[1]["type"] == "missing"
    assert record.pydantic_errors[1]["loc"] == ("required_field",)


def test_dlt_pydantic_freeze_exception(caplog):
    """Check if DLT raises an exception instead of continuing when the data mode is set to freeze"""
    caplog.set_level(logging.WARNING, logger="dlt")

    with pytest.raises(DataValidationError) as exc_info:
        _validator(data_mode="freeze")(dict(id="not-an-int"))

    assert "id" in exc_info.value.data_item
    assert exc_info.value.data_item["id"] == "not-an-int"
    assert exc_info.value.contract_mode == "freeze"
