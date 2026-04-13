from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Literal


@dataclass
class NodeProperties:
    """Core model specifying required fields for OpenGraph node properties."""

    name: str
    displayname: str
    environmentid: str
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC), kw_only=True)


@dataclass
class Node(ABC):
    """Core model and abstract base for an OpenGraph node."""

    kinds: list[str]
    properties: NodeProperties

    @classmethod
    def guid(cls, name: str, node_type: Enum | str, *args: str) -> str:
        """Generate guid for a node using UUID5 and provided arguments."""
        uuid_namespace = uuid.NAMESPACE_DNS
        type_value = node_type.value if isinstance(node_type, Enum) else node_type
        resource_path = f"{name}.{type_value}.{'.'.join(args)}"
        return str(uuid.uuid5(uuid_namespace, resource_path))

    @abstractmethod
    def __post_init__(self):
        """Each source should implement a custom Node with the 'id' property."""
        raise NotImplementedError

    # @property
    # @abstractmethod
    # def id(self) -> str:
    #     """Each source should implement a custom Node with the 'id' property."""
    #     raise NotImplementedError


class Operator(str, Enum):
    equals = "equals"


@dataclass
class PropertyMatch:
    key: str
    value: str | bool | int
    operator: Operator = Operator.equals


@dataclass
class ConditionalEdgePath:
    kind: str
    property_matchers: list[PropertyMatch]
    match_by: Literal["property"] = "property"


@dataclass
class EdgePath:
    """Core model specifying required fields for an OpenGraph edge path."""

    match_by: str
    value: str


@dataclass
class EdgeProperties:
    """Core model specifying required fields for an OpenGraph edge properties."""

    composed: bool = field(default=False, kw_only=True)
    traversable: bool = field(default=False, kw_only=True)


@dataclass
class Edge:
    """Core model specifying required fields for an OpenGraph edge."""

    kind: str
    start: EdgePath | ConditionalEdgePath
    end: EdgePath | ConditionalEdgePath
    properties: EdgeProperties = field(default_factory=EdgeProperties)
