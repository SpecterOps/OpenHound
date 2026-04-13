from importlib.resources.abc import Traversable

from pydantic import BaseModel
from pydantic_extra_types.semantic_version import SemanticVersion
from yaml import safe_load


class Credential(BaseModel):
    name: str
    description: str
    required: bool


class Parameters(BaseModel):
    name: str
    description: str
    required: bool
    default: str | bool | int | float | None = None


class Author(BaseModel):
    name: str
    url: str | None = None
    email: str | None = None


class Reference(BaseModel):
    name: str
    url: str


class Extension(BaseModel):
    name: str
    description: str
    authors: list[Author] | Author
    version: SemanticVersion
    type: str
    license: str
    homepage: str
    credentials: list[Credential]
    parameters: list[Parameters]
    references: list[Reference] | None
    tags: list[str] | None = None

    @classmethod
    def from_yaml(cls, path: Traversable) -> "Extension":
        if not path.is_file():
            raise FileNotFoundError(
                f"Extension metadata file not found at path: {path}"
            )
        with path.open("r") as yaml_file:
            yaml_data = safe_load(yaml_file)
        return cls(**yaml_data)
