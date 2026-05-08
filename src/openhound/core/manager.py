import importlib.resources as resources
import logging
import warnings
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from types import ModuleType
from typing import Iterable

from pydantic import ValidationError
from yaml import safe_load

from openhound.core.app import OpenHound
from openhound.core.models.collector import Collector
from openhound.core.models.extension import Extension

logger = logging.getLogger(__name__)


class CollectorManager:
    def __init__(self, collectors: list[OpenHound]):
        self.collectors = collectors

    @staticmethod
    @warnings.deprecated(
        "This function is deprecated. Extensions should be loaded via entry points and not from the filesystem. This method will be removed in a future release.",
    )
    def _load_collector_spec(spec_path: Path) -> "Collector":
        with open(spec_path, "r") as spec_file:
            spec_content = spec_file.read()
            return Collector(**safe_load(spec_content))

    @staticmethod
    def _discover_sources(base_path: Path) -> Iterable[Path]:
        for item in base_path.iterdir():
            if item.is_file():
                continue

            if (item / "collector.yml").is_file():
                yield item

    @staticmethod
    @warnings.deprecated(
        "This function is deprecated. Extensions should be loaded via entry points and not from the filesystem. This method will be removed in a future release.",
    )
    def _load_collector(
        base_path: Path, entrypoint: Path, is_addon: bool = False
    ) -> "ModuleType":
        collector = CollectorManager._load_collector_spec(base_path / "collector.yml")
        module_name = f"openhound.sources.{collector.name}.{entrypoint}"
        if is_addon:
            # TODO: This should be variable
            module_name = f"addons.{collector.name}.{entrypoint}"
        return import_module(module_name)

    @classmethod
    @warnings.deprecated(
        "This function is deprecated. Extensions should be loaded via entry points and not from the filesystem. This method will be removed in a future release.",
    )
    def from_path(cls, path: Path) -> "CollectorManager":
        collectors: list[OpenHound] = []
        for item in cls._discover_sources(path):
            module = cls._load_collector(item, Path("main"))
            collectors.append(module.app)
        return cls(collectors=collectors)

    @staticmethod
    def validate_extension(module: OpenHound, extension_name: str) -> bool:
        """Validate that an extension module implements required methods.

        Args:
            module (OpenHound): The loaded extension module
            extension_name (str): Name of the extension

        Returns:
            bool: True if the module is valid (ie. contains collect, convert, and optionally preprocess methods)
        """

        valid_module = True
        if not isinstance(module, OpenHound):
            valid_module = False
            logger.error(
                f"Extension '{extension_name}' is not an instance of OpenHound",
                extra={"extension": extension_name, "phase": "extension_validation"},
            )

        if module.collector is None:
            valid_module = False
            logger.error(
                f"Extension '{extension_name}' is missing @app.collect() decorator",
                extra={"extension": extension_name, "phase": "extension_validation"},
            )

        if module.converter is None:
            valid_module = False
            logger.warning(
                f"Extension '{extension_name}' is missing @app.convert() decorator",
                extra={"extension": extension_name, "phase": "extension_validation"},
            )

        return valid_module

    @staticmethod
    def validate_metadata(extension: EntryPoint) -> tuple[bool, Extension | None]:
        """Validate that an extension's metadata contains the required fields.

        Args:
            extension (EntryPoint): The extension to validate

        Returns:
            tuple[bool, Extension | None]: (is_valid, extension_object or None)
        """

        root_extension_name = extension.module.split(".")[0]
        extension_files = resources.files(root_extension_name)
        metadata_path = extension_files / "extension.yaml"
        try:
            extension_metadata = Extension.from_yaml(metadata_path)
            logger.info(
                f"Extension '{extension.name}' has valid metadata",
                extra={"extension": extension.name, "phase": "metadata_validation"},
            )
            return True, extension_metadata

        except FileNotFoundError:
            logger.error(
                f"Extension '{extension.name}' is missing 'extension.yaml' file",
                extra={"extension": extension.name, "phase": "metadata_validation"},
            )
            return False, None

        except ValidationError as e:
            logger.error(
                f"Extension '{extension.name}' has invalid metadata: {e}",
                extra={"extension": extension.name, "phase": "metadata_validation"},
            )
            return False, None

    @classmethod
    def from_entrypoint(
        cls,
        group: str = "openhound.sources",
        load_sources: bool = False,
    ) -> "CollectorManager":
        discover_extension = entry_points(group=group)
        extensions: list[OpenHound] = []
        for extension in discover_extension:
            is_valid_metadata, metadata = cls.validate_metadata(extension)
            if not is_valid_metadata:
                logger.warning(
                    f"Extension '{extension.name}' does not have a valid metadata file",
                    extra={"extension": extension.name, "phase": "extension_loading"},
                )

            load_extension: OpenHound = extension.load()
            if load_sources:
                cls._load_extension_source(extension)

            is_valid_extension = cls.validate_extension(load_extension, extension.name)
            if is_valid_extension:
                load_extension.metadata = metadata
                extensions.append(load_extension)
                logger.info(
                    f"Loaded extension '{extension.name}' from entry point '{group}'",
                    extra={"extension": extension.name, "phase": "extension_loading"},
                )
            else:
                logger.warning(
                    f"Extension '{extension.name}' skipped due to invalid structure",
                    extra={"extension": extension.name, "phase": "extension_loading"},
                )
        return cls(collectors=extensions)

    @staticmethod
    def _load_extension_source(extension: EntryPoint) -> None:
        parent_module_name = extension.module.rsplit(".", 1)[0]
        source_module_name = f"{parent_module_name}.source"
        try:
            import_module(source_module_name)
        except ModuleNotFoundError as err:
            if err.name != source_module_name:
                raise
            logger.warning(
                f"Extension '{extension.name}' does not have a source module '{source_module_name}'",
                extra={"extension": extension.name, "phase": "extension_loading"},
            )
