import json
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator
from yaml import safe_load


class SavedSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Required for an extension
    name: str
    description: Optional[str] = None
    query: str

    @classmethod
    def from_json(cls, file_path: Path) -> "SavedSearch":
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
    def from_yaml(cls, file_path: Path) -> "SavedSearchExtended":
        with open(file_path, "r") as file_object:
            yaml_object = safe_load(file_object.read())
        return cls(**yaml_object)
