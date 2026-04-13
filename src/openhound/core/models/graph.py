from pydantic import BaseModel, ConfigDict, Field

from openhound.sources.opengraph.entries import Edge, Node


class GraphEntries(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph entries."""

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class CollectorProperties(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph collector properties."""

    model_config = ConfigDict(extra="allow")
    collection_methods: list[str] = ["dlt"]


class MetaDataCollector(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph collector metadata."""

    name: str = "openhound"
    version: str = "0.0.1"
    properties: CollectorProperties = Field(default_factory=CollectorProperties)


class MetaData(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph metadata."""

    ingest_version: str = "v1"
    collector: MetaDataCollector = Field(default_factory=MetaDataCollector)


class Graph(BaseModel):
    """Core Pydantic model specifying required fields for OpenGraph."""

    graph: GraphEntries
    metadata: MetaData = Field(default_factory=MetaData)
