# This is a patched version of DLT's model validator which adds logging when resources fail
# pydantic validation and the schema contract is set to discard_row
# https://github.com/dlt-hub/dlt/blob/devel/dlt/common/libs/pydantic.py

import logging
from typing import Any, Type

from dlt.common.schema import DataValidationError
from dlt.common.schema.typing import TSchemaEvolutionMode
from dlt.common.typing import TDataItem
from pydantic import BaseModel, ValidationError, create_model
from pydantic.functional_validators import WrapValidator
from typing_extensions import Annotated

logger = logging.getLogger("dlt")


def create_list_model(
    model: type[BaseModel],
    column_mode: TSchemaEvolutionMode = "freeze",
    data_mode: TSchemaEvolutionMode = "freeze",
) -> type[BaseModel]:
    """Creates a model from `model` for validating list of items in batch."""
    if column_mode == "discard_row" or data_mode == "discard_row":

        def _lenient_item_validator(value: Any, handler: Any) -> BaseModel | None:
            try:
                return handler(value)
            except ValidationError as val_err:
                for err in val_err.errors():
                    if err["type"] == "model_type":
                        raise

                logger.warning(
                    "DLT discarded row during listed Pydantic validation",
                    extra={
                        "resource": model.__name__,
                        "pydantic_errors": val_err.errors(
                            include_input=False, include_context=False
                        ),
                    },
                )
                return None
            except Exception:
                return None

        item_type = Annotated[model | None, WrapValidator(_lenient_item_validator)]  # type: ignore[valid-type]
        return create_model(
            "LenientList" + model.__name__,
            items=(list[item_type], ...),  # type: ignore[valid-type]
        )

    return create_model(
        "List" + model.__name__,
        items=(list[model], ...),  # type: ignore[valid-type]
    )


def _classify_validation_errors(
    table_name: str,
    model: Type[BaseModel],
    item: TDataItem,
    exc: ValidationError,
    column_mode: TSchemaEvolutionMode,
    data_mode: TSchemaEvolutionMode,
) -> None:
    """Classifies validation errors and raises DataValidationError for freeze mode.

    For discard_row mode, returns without raising so the caller can discard the item.
    For model_type errors (item is not a mapping), always re-raises.
    """
    for err in exc.errors():
        if err["type"] == "model_type":
            raise exc
        if err["type"] == "extra_forbidden":
            if column_mode == "freeze":
                raise DataValidationError(
                    None,
                    table_name,
                    str(err["loc"]),
                    "columns",
                    "freeze",
                    model,
                    {"columns": "freeze"},
                    item,
                    err["msg"],
                ) from exc
            elif column_mode == "discard_row":
                logger.warning(
                    "DLT discarded row during Pydantic validation",
                    extra={
                        "resource": table_name,
                        "pydantic_errors": exc.errors(
                            include_input=False, include_context=False
                        ),
                    },
                )
                return
            raise NotImplementedError(
                f"`{column_mode=:}` not implemented for Pydantic validation"
            )
        else:
            if data_mode == "freeze":
                raise DataValidationError(
                    None,
                    table_name,
                    str(err["loc"]),
                    "data_type",
                    "freeze",
                    model,
                    {"data_type": "freeze"},
                    item,
                    err["msg"],
                ) from exc
            elif data_mode == "discard_row":
                logger.warning(
                    "DLT discarded row during Pydantic validation",
                    extra={
                        "resource": table_name,
                        "pydantic_errors": exc.errors(
                            include_input=False, include_context=False
                        ),
                    },
                )
                return
            raise NotImplementedError(
                f"`{data_mode=:}` not implemented for Pydantic validation"
            )
