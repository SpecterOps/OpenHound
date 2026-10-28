from abc import ABC, abstractmethod
from typing import Iterable, Type

from pydantic import BaseModel, Field, PrivateAttr, SerializeAsAny

from openhound.core.lookup import LookupManager
from openhound.core.models.entries import (
    Edge as PEdge,
)
from openhound.core.models.entries import (
    Node as PNode,
)
from openhound.core.models.entries import (
    NodeProperties as PNodeProperties,
)
from openhound.core.models.entries_dataclass import (
    Edge as DEdge,
)
from openhound.core.models.entries_dataclass import (
    Node as DNode,
)
from openhound.core.models.entries_dataclass import (
    NodeProperties as DNodeProperties,
)

ASSET_REGISTRY: dict["BaseAsset", tuple["NodeDef | None", list["EdgeDef"]]] = {}


class NodeDef(BaseModel):
    """Node definition specifying what Nodes a resource returns when converting to OpenGraph"""

    kind: str
    description: str | None = Field(
        default=None, deprecated="Define the node description in schema.json instead."
    )
    icon: str | None = Field(
        default=None, deprecated="Define the node icon in schema.json instead."
    )
    properties: (
        SerializeAsAny[Type[DNodeProperties]] | SerializeAsAny[Type[PNodeProperties]]
    )
    color: str | None = Field(
        default=None, deprecated="Define the node color in schema.json instead."
    )


class EdgeDef(BaseModel):
    """Edge definition specifying what Edges a resource returns when converting to OpenGraph"""

    start: str
    end: str
    kind: str
    description: str | None = Field(
        default=None,
        deprecated="Define the relationship description in schema.json instead.",
    )
    traversable: bool | None = Field(
        default=None,
        deprecated="Define the relationship is_traversable in schema.json instead.",
    )


class BaseAsset(BaseModel, ABC):
    """Base class for resources that return OpenGraph nodes and edges."""

    _lookup: LookupManager = PrivateAttr()
    _extras: dict = PrivateAttr()

    @property
    @abstractmethod
    def as_node(self) -> PNode | DNode | None:
        """Return the OpenGraph node representation for this asset."""
        ...

    @property
    @abstractmethod
    def edges(self) -> Iterable[PEdge] | Iterable[DEdge] | None:
        """Return the OpenGraph edges for this resource."""
        ...


def graph_asset(node: NodeDef | None = None, edges: list[EdgeDef] = []):
    """Decorator to register a resource class and its graph definitions. This is used to automatically
    generate documentation for each unique resource and implement rules/warnings when nodes/edges are returned
    which are not declared.

    Args:
        node (NodeDef | None, optional): Node defnition for the resource.
        edges (list[EdgeDef], optional): Edge definitions for the resource.
    """

    def decorator(cls):
        ASSET_REGISTRY[cls] = (node, edges)
        return cls

    return decorator
