# OpenGraph Model
This page describes the core OpenGraph (Pydantic) models used to represent and ingest OpenGraph data into BloodHound. These models validate the nodes and edges exported by each collector, ensuring data consistency before ingestion. The graph model defines how objects (nodes) and their relationships (edges) are structured, along with metadata about the collection process.

# Graph
A graph is a collection of nodes and edges representing the complete OpenGraph dataset. Each graph contains metadata describing the collection context, including the collector type, version and collection methods used. The graph structure consists of `GraphEntries` which contains the nodes and edges, `MetaData` which provides information about how the data was collected and `CollectorProperties` which define the specific collection methods.

::: openhound.core.models.graph
    options:
      show_root_heading: false
      members:
        - Graph
        - GraphEntries
        - MetaData
        - MetaDataCollector
        - CollectorProperties

# Nodes
A node represents an object in the graph with a set of properties/attributes. Each node contains a list of `kinds` which specify its type, a few examples are "Computer", "User", "JamfUser" etc. The first value of `kinds` represents the primary kind in BloodHound, which will also be used as the visual "title" of a node. Any additional kind can be used inside a Cypher query as part of an optional filter. Each node is expected to contain at least `displayname` and `name` as part of it's properties. Additionally each node will include an automatically generated `last_seen` as a default property.

::: openhound.core.models.entries
    options:
      show_root_heading: false
      members:
        - Node
        - NodeProperties

# Edges
An edge represents a directional relationship between two nodes in the graph. Each edge contains a `kind` which specifies the relationship type, a few examples are "MemberOf", "AdminTo", "HasSession" etc. Edges can also have properties/attributes that provide additional context about the relationship. In Cypher queries, edges are used to traverse the graph and discover attack paths between nodes. Edges require no properties, but will include `last_seen` as a default property. Edges may also contain a `composed` property indicating the edge is derived from multiple relationships and a `traversable` property indicating whether the edge can be used in an attack path.

::: openhound.core.models.entries
    options:
      show_root_heading: false
      members:
        - Edge
        - EdgeProperties
