import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, computed_field


class NodeProperties(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph node properties."""

    model_config = ConfigDict(extra="allow")
    name: str
    displayname: str
    environmentid: str
    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Node(BaseModel, ABC):
    """Core Pydantic model and abstract base, specifying required fields for an OpenGraph node and which abstractmethods are required
    to be implemented by a custom source."""

    model_config = ConfigDict(extra="allow")
    kinds: list[str]
    # IMPORTANT!: Preserve fields from the subclass if the base model is inherited
    properties: SerializeAsAny[NodeProperties]

    @classmethod
    @abstractmethod
    def guid(cls, name: str, node_type: Enum | str, *args) -> str:
        """Generate guid for a node. This method should overwritten by source-specific
        arguments with additional (unique) arguments.

        Args:
            name (str): The original name or display name of the resource (as defined by the source).
            node_type (Enum | str): Node kind or value

        Returns:
            str: UUID5 string based on the provided arguments.
        """
        uuid_namespace = uuid.NAMESPACE_DNS
        type_value = node_type.value if isinstance(node_type, Enum) else node_type
        resource_path = f"{name}.{type_value}.{'.'.join(args)}"
        return str(uuid.uuid5(uuid_namespace, resource_path))

    @computed_field  # type: ignore[prop-decorator]
    @property
    @abstractmethod
    def id(self) -> str:
        """Abstractmethod specifying that each source should implement a custom Node with the 'id' property."""
        ...


class Operator(str, Enum):
    equals = "equals"


class PropertyMatch(BaseModel):
    key: str
    value: str | bool | int
    operator: Operator = Operator.equals


class ConditionalEdgePath(BaseModel):
    match_by: Literal["property"] = "property"
    property_matchers: list[PropertyMatch]
    kind: str


class EdgePath(BaseModel):
    value: str
    match_by: str


class EdgeProperties(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph edge properties."""

    model_config = ConfigDict(extra="allow")
    composed: bool = False
    traversable: bool = False


class Edge(BaseModel):
    """Core Pydantic model specifying required fields for an OpenGraph edge."""

    kind: str
    start: EdgePath | ConditionalEdgePath
    end: EdgePath | ConditionalEdgePath
    properties: EdgeProperties = Field(default_factory=EdgeProperties)
