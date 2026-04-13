from pydantic import BaseModel, Field


class Node(BaseModel):
    label: str
    kind: str
    kinds: list[str]
    objectId: str
    properties: dict = Field(default_factory=dict)


class Edge(BaseModel):
    source: str
    target: str
    label: str
    kind: str
    properties: dict = Field(default_factory=dict)


class GraphsCypherData(BaseModel):
    node_keys: list[str] = Field(default_factory=list)
    edge_keys: list[str] = Field(default_factory=list)
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)


class RunCypher(BaseModel):
    data: GraphsCypherData
