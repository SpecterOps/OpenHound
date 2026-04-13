import importlib.resources as resources
from importlib.metadata import entry_points
from unittest.mock import MagicMock

import pytest
from dlt.extract.validation import PydanticValidator

from openhound.core.app import OpenHound
from openhound.core.collect import CollectContext
from openhound.core.models.extension import Extension


def loaded_extensions():
    discover_extension = entry_points(group="openhound.sources")
    return [pytest.param(ext, ext.load(), id=ext.name) for ext in discover_extension]


def fully_initialize_extension(ext_module: OpenHound) -> "OpenHound":
    ext_module.transformer

    mock_context = MagicMock(spec=CollectContext)
    if ext_module.collector is not None:
        try:
            ext_module.collector(mock_context)
        except Exception:
            pass

    return ext_module


@pytest.mark.parametrize("ext_name,ext_module", loaded_extensions())
def test_extension_is_openhound(ext_name, ext_module):
    assert isinstance(ext_module, OpenHound), (
        f"Extension is not an instance of OpenHound: {ext_name}"
    )


@pytest.mark.parametrize("ext,ext_module", loaded_extensions())
def test_extension_has_valid_metadata(ext, ext_module):
    root_extension_name = ext.module.split(".")[0]
    extension_files = resources.files(root_extension_name)
    metadata_path = extension_files / "extension.yaml"
    assert metadata_path.is_file(), (
        f"Extension {ext.name} is missing 'extension.yaml' file"
    )
    assert Extension.from_yaml(metadata_path), (
        f"Extension {ext.name} has invalid 'extension.yaml' file"
    )


@pytest.mark.parametrize("ext,ext_module", loaded_extensions())
def test_extensions_contains_collect(ext, ext_module):
    assert ext_module.collector is not None, (
        f"Extension {ext.name} does not contain @app.collect decorator"
    )


@pytest.mark.parametrize("ext,ext_module", loaded_extensions())
def test_extensions_contains_convert(ext, ext_module):
    assert ext_module.converter is not None, (
        f"Extension does not contain @app.convert decorator: {ext.name}"
    )


@pytest.mark.parametrize("ext,ext_module", loaded_extensions())
def test_extension_resources_use_models(ext, ext_module, subtests):
    fully_initialize_extension(ext_module)
    for dlt_resource in ext_module.dlt_resources:
        with subtests.test(extension=ext.name, resource=dlt_resource.name):
            columns = dlt_resource.columns
            validator = isinstance(dlt_resource.validator, PydanticValidator)
            assert columns and validator, (
                f"Extension resource '{dlt_resource.name}' must have columns and use Pydantic models for validation"
            )


# TODO: Change this test to check of the @app.asset inherits BaseAsset instead. Sometimes a DLT resource is used without it being a node/edge, such as fetching
# users and then getting the actual details via a transformer
# @pytest.mark.parametrize("ext,ext_module", loaded_extensions())
# def test_extension_resources_use_assets(ext, ext_module, subtests):
#     fully_initialize_extension(ext_module)
#     for dlt_resource in ext_module.dlt_resources:
#         with subtests.test(extension=ext.name, resource=dlt_resource.name):
#             model_used = (
#                 dlt_resource.validator.model
#                 if isinstance(dlt_resource.validator, PydanticValidator)
#                 else None
#             )
#             is_model_an_asset = model_used in ext_module.assets
#             assert is_model_an_asset, (
#                 f"Extension resource '{dlt_resource.name}' must have a corresponding OpenGraph asset defined for its Pydantic model"
#             )
