# Configuring graph properties

This page explains how to configure the required OpenGraph properties for your collector. When creating a new collector,
you need to define three key components:

- **Node types**: The different kinds of assets your collector will extract;
- **Node properties**: Common properties shared by all nodes from your service;
- **GUID generation**: How to uniquely identify each node.

The cookiecutter template includes a minimal `graph.py` that demonstrates the pattern to define the required OpenGraph
properties.

## 1. Define Node Types

Node types represent the different kinds of assets your collector provides. Define them as a constant in the
`kinds/nodes.py` and `kinds/edges.py` files. These strings are used in the `kinds` field of your asset models and should
be unique across all collectors to prevent collisions.

```python
# kinds/nodes.py example
COMPUTER = "jamf_Computer"
USER = "jamf_ComputerUser"
POLICY = "jamf_Policy" 
```

```python
# kinds/edges.py example
CONTAINS = "jamf_Contains"
ASSIGNED_USER = "jamf_AssignedUser"
MEMBER_OF = "jamf_MemberOf"
```

!!! info "IMPORTANT"
Use descriptive names which clearly identify both the asset type and the source service (e.g., jamf_User instead of just
User). This also prevents potential collisions with other collectors.

## 2. Define (required) node properties

Common node properties are attributes that every node from your collector/source will have regardless of its specific
node type. These should be properties unique to your service/source. During the conversion process every resource will
be checked if at these properties are present. Think of attributes like a tenant name, AD domain, etc. By inheriting
BaseProperties, both `name` and `displayname` are already included as properties as they are required fields as part of
the OpenGraph standard. Additionally, a `last_seen` property will be added for every node with the timestamp set to
current date/time (ie. when the resource was converted).

```py
@dataclass
class BaseNodeProperties(BaseProperties):
    tenant: str
    domain: str
```

Make sure to choose properties that:

- Are present on **all** nodes from your service;
- Provide useful context for identification and filtering;

Make sure *not* to choose properties that:

- Are specific only to a few nodes. Inherit from BaseNodeProperties and add those properties to the specific node model
  instead;

## 3. Implement GUID Generation

The GUID (Globally Unique Identifier) uniquely identifies each node. You must override the guid() static method and the
id computed property. This generates a repeatable unique identifier that other collectors can also use to refer to your
nodes without requiring any context/awareness or lookup database to generate edges.

### Override the guid() static method

Choose properties that uniquely identify your resource within the service:

```py
@staticmethod
def guid(
        id: str,
        node_type: str,
        tenant: str,
) -> str:
    return BaseNode.guid(id, node_type, tenant)

```

### Override the id property

Implement __post_init__ to set the node ID based on your custom guid() method with the appropriate properties:

```py
def __post_init__(self):
    self.id = self.guid(
        str(self.properties.id), self.kinds[0], self.properties.tenant
    )
```

### GUID Selection Guidelines

The properties you use for GUID generation should:

- Uniquely identify the resource across all instances;
- Be stable and not change over time;
- Be deterministic, the same input should always procude the same GUID;
- Include a service specific identifier if available.

Note: You can also generate a GUID based on pre-existing resource ID if available.

## Complete example

Here is a complete example of the Jamf collector:

```py
@dataclass
class JAMFNodeProperties(BaseProperties):
    tenant: str
    id: int
    tier: int
    environmentid: str


@dataclass
class JAMFNode(BaseNode):
    properties: JAMFNodeProperties
    id: str = field(init=False)

    @staticmethod
    def guid(
            id: str,
            node_type: str,
            tenant: str,
    ) -> str:
        return BaseNode.guid(id, node_type, tenant)

    def __post_init__(self):
        self.id = self.guid(
            str(self.properties.id), self.kinds[0], self.properties.tenant
        )
```

## Profit!

Your collector is now able to collect raw resources, perform data validation and perform the conversion to OpenGraph.
Check out the [CLI](../cli.md) page for more details on how to run your custom collector.
