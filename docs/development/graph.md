# Configuring graph properties

This page explains how to configure the required OpenGraph properties for your collector. When creating a new collector, you need to define three key components:

- **Node types**: The different kinds of assets your collector will extract;
- **Node properties**: Common properties shared by all nodes from your service;
- **GUID generation**: How to uniquely identify each node.

 The cookiecutter template includes a minimal `graph.py` that demonstrates the pattern to define the required OpenGraph properties.


## 1. Define Node Types

Node types represent the different kinds of assets your collector provides. Define them as an enumeration in the NodeTypes class.

```py
class NodeTypes(str, Enum):
    Asset = "ExampleAsset"
```

!!! info "IMPORTANT"
    Use descriptive names which clearly identify both the asset type and the source service (e.g., JamfUser instead of just User). This also prevents potential collisions with other collectors.

## 2. Define (required) node properties
Common node properties are attributes that every node from your collector/source will have regardless of its specific node type. These should be properties unique to your service/source. During the conversion process every resource will be checked if at these properties are present. Think of attributes like a tenant name, AD domain, etc. By inheriting BaseProperties, both `name` and `displayname` are already included as properties as they are required fields as part of the OpenGraph standard. Additionally, a `last_seen` property will be added for every node with the timestamp set to current date/time (ie. when the resource was converted).


```py
class NodeProperties(BaseProperties):
    model_config = ConfigDict(extra="allow")
    tenant: str
    domain: str
```

Make sure to choose properties that:

- Are present on **all** nodes from your service;
- Provide useful context for identification and filtering;

Make sure *not* to choose properties that:

- Are specific only to a few nodes. Use the `ExtendedProperties` class to add additional properties to specific nodes types.


## 3. Implement GUID Generation
The GUID (Globally Unique Identifier) uniquely identifies each node. You must override the guid() static method and the id computed property. This generates a repeatable unique identifier that other collectors can also use to refer to your nodes without requiring any context/awareness or lookup database to generate edges.

### Override the guid() static method
Choose properties that uniquely identify your resource within the service:

```py
@staticmethod
def guid(name, node_type, tenant_id, organization) -> str:
    return BaseNode.guid(name, node_type, tenant_id, organization)
```

Example for JAMF:
```py
@staticmethod
def guid(name, node_type, jamf_id) -> str:
    return BaseNode.guid(name, node_type, jamf_id)
```

### Override the id property
Implement the id property to call your guid() method with the appropriate properties:

```py
@computed_field
@property
def id(self) -> str:
    dyn_uid = self.guid(
        self.properties.name,
        self.kinds[0],
        self.properties.tenant_id,
        self.properties.organization
    )
    return dyn_uid
```

### GUID Selection Guidelines
The properties you use for GUID generation should:

- Uniquely identify the resource across all instances;
- Be stable and not change over time;
- Be deterministic, the same input should always procude the same GUID;
- Include a service specific identifier if available.

Note: You can also generate a GUID based on pre-existing resource ID if available. However, other collectors will have no knowledge how the GUID is generated and will not be able to create edges to your resource without having a lookup database available.

## Complete example
Here is a complete example of the AWS collector:

```py

class NodeTypes(str, Enum):
    AWSUser = "AWSUser"
    AWSGroup = "AWSGroup"
    AWSRole = "AWSRole"
    AWSIdentityProvider = "AWSIdentityProvider"
    AWSEC2Instance = "AWSEC2Instance"
    AWSPolicy = "AWSPolicy"
    AWSInlinePolicy = "AWSInlinePolicy"
    AWSEKSCluster = "AWSEKSCluster"
    AWSResource = "AWSResource"


class NodeProperties(BaseProperties):
    model_config = ConfigDict(extra="allow")
    arn: Optional[str] = None
    aws_account_id: str
    aws_region: str


class Node(BaseNode):
    properties: NodeProperties

    @staticmethod
    def guid(name, node_type, account_id, scope) -> str:
        return BaseNode.guid(name, node_type, account_id, scope)

    @computed_field
    @property
    def id(self) -> str:
        primary_kind = self.kinds[0]
        return self.guid(
            self.properties.arn if self.properties.arn else self.properties.name,
            sprimary_kind,
            account_id=self.properties.aws_account_id,
            scope=self.properties.aws_region,
        )
```

## Profit!
Your collector is now able to collect raw resources, perform data validation and perform the conversion to OpenGraph. Check out the [CLI](../cli.md) page for more details on how to run your custom collector.
