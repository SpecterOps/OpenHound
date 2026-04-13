from pydantic import BaseModel, PrivateAttr, SerializeAsAny
from typing import Optional, Iterable
from openhound.core.lookup import LookupManager
from openhound.core.models.entries import (
    Edge as PEdge,
    Node as PNode,
    NodeProperties as PNodeProperties,
)
from openhound.core.models.entries_dataclass import (
    Node as DNode,
    Edge as DEdge,
    NodeProperties as DNodeProperties,
)
from abc import ABC, abstractmethod
from typing import Type

ASSET_REGISTRY: dict["BaseAsset", tuple["NodeDef | None", list["EdgeDef"]]] = {}


class NodeDef(BaseModel):
    """Node definition specifying what Nodes a resource returns when converting to OpenGraph"""

    kind: str
    description: str
    icon: str
    properties: (
        SerializeAsAny[Type[DNodeProperties]] | SerializeAsAny[Type[PNodeProperties]]
    )
    color: str = "#FFFFFF"


class EdgeDef(BaseModel):
    """Edge definition specifying what Edges a resource returns when converting to OpenGraph"""

    start: str
    end: str
    kind: str
    description: str
    traversable: Optional[bool] = False


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
