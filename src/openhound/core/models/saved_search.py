import json
import zipfile
from enum import Enum
from io import TextIOWrapper
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator
from yaml import safe_load


class Format(str, Enum):
    json = "json"
    yaml = "yaml"


class OutputFormat(str, Enum):
    json = "json"
    zip = "zip"


class SavedSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Required for an extension
    name: str
    description: Optional[str] = None
    query: str

    @classmethod
    def from_file(cls, file_path: Path) -> "SavedSearch":
        with open(file_path, "r") as file_object:
            json_object = json.loads(file_object.read())
        return cls(**json_object)


class SavedSearchExtended(SavedSearch):
    # Additional metadata for library use later
    revision: int
    resources: Optional[Union[str, list[str]]] = None
    acknowledgements: Optional[Union[str, list[str]]] = None
    guid: str
    prebuilt: bool = False
    platforms: Union[str, list[str]]
    category: str

    @field_validator("platforms", mode="after")
    @classmethod
    def platforms_is_list(cls, value: str | list[str]) -> list[str]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @field_validator("resources", mode="after")
    @classmethod
    def resources_is_list(cls, value: str | list[str]) -> list[str]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @field_validator("acknowledgements", mode="after")
    @classmethod
    def acknowledgementsis_list(cls, value: str | list[str]) -> list[str]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @classmethod
    def from_file(cls, file_path: Path) -> "SavedSearchExtended":
        with open(file_path, "r") as file_object:
            yaml_object = safe_load(file_object.read())
        return cls(**yaml_object)


class QueryBundle:
    def __init__(self, queries: list[SavedSearchExtended | SavedSearch], file_format: Format = Format.json) -> None:
        self.queries = queries
        self.file_format = file_format

    @classmethod
    def from_paths(cls, all_files: list[Path], file_format: Format = Format.json) -> "QueryBundle":
        model_choices = {
            'yaml': SavedSearchExtended,
            'json': SavedSearch,
        }

        queries = [
            model_choices[file_format].from_file(cypher_query) for cypher_query in
            all_files
        ]
        return cls(queries, file_format)

    def _to_json(self, output_file: TextIOWrapper) -> None:
        all_objects = [query.model_dump() for query in self.queries]
        output_file.write(json.dumps(all_objects, indent=2))

    def _to_zip(self, output_file: TextIOWrapper) -> None:
        with zipfile.ZipFile(
                file=output_file.name,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
        ) as archive:
            for query in self.queries:
                archive.writestr(
                    zinfo_or_arcname=f"{query.name}.json",
                    data=query.model_dump_json().encode(),
                )

    def save(self, output_file: TextIOWrapper, output_format: OutputFormat = OutputFormat.json) -> None:
        if output_format == OutputFormat.json:
            self._to_json(output_file)
        elif output_format == OutputFormat.zip:
            self._to_zip(output_file)
