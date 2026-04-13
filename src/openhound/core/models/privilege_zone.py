import json
from pathlib import Path
from typing import Union

from pydantic import BaseModel, ConfigDict, field_validator
from yaml import safe_load


class PrivilegeZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required for an Extension
    name: str
    description: str
    cypher: str
    enabled: bool
    allow_disable: bool
    zone: str

    @classmethod
    def from_json(cls, file_path: Path) -> "PrivilegeZone":
        with open(file_path, "r") as file_object:
            json_object = json.loads(file_object.read())
        return cls(**json_object)


class PrivilegeZoneExtended(PrivilegeZone):
    # Additional metadata for library use later
    platforms: Union[str, list[str]]
    revision: int
    guid: str

    @field_validator("platforms", mode="after")
    @classmethod
    def platforms_is_list(cls, value: str | list[str]) -> list[str]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @classmethod
    def from_yaml(cls, file_path: Path) -> "PrivilegeZoneExtended":
        with open(file_path, "r") as file_object:
            yaml_object = safe_load(file_object.read())
        return cls(**yaml_object)
