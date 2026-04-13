from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny


class BaseModelExcludeNone(BaseModel):
    def model_dump(self, **kwargs):
        return super().model_dump(**kwargs, exclude_none=True)

    def model_dump_json(self, **kwargs):
        return super().model_dump_json(**kwargs, exclude_none=True)


class NodeProperties(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    displayname: str
    environmentid: str


class Node(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    kinds: list[str]
    # IMPORTANT!: Preserve fields from the subclass if the base model is inherited
    properties: SerializeAsAny[NodeProperties]


class Operator(str, Enum):
    equals = "equals"


class PropertyMatch(BaseModel):
    key: str
    operator: Operator
    value: str | bool | int


class ConditionalEdgePath(BaseModel):
    match_by: Literal["property"] = "property"
    property_matchers: list[PropertyMatch]
    kind: str


class EdgePath(BaseModel):
    match_by: Literal["id"] = "id"
    value: str


class EdgeProperties(BaseModel):
    model_config = ConfigDict(extra="allow")
    composed: bool = False


EdgePathType = Annotated[
    Union[EdgePath, ConditionalEdgePath], Field(discriminator="match_by")
]


class Edge(BaseModel):
    kind: str
    start: EdgePathType
    end: EdgePathType
    properties: SerializeAsAny[EdgeProperties]


class GraphEdgeContent(BaseModel):
    entity_type: Literal["edge"]
    content: list[Edge]


class GraphNodeContent(BaseModel):
    entity_type: Literal["node"]
    content: Node


class GraphContent(BaseModelExcludeNone):
    graph: GraphEdgeContent | GraphNodeContent = Field(discriminator="entity_type")
