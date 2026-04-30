# Modelling resources

This page explains how OpenHound models resources using Pydantic classes and the custom @app.asset decorator. The cookiecutter template includes a minimal `asset.py` that demonstrates the pattern to define a resource model for data validation and OpenGraph node/edge mapping. Each `@app.asset` (as defined in source.py) should specify a model as part of the `columns` configuration, ex `@app.resource(name="asset", columns=Asset)`.

## Pydantic resource model
Individual resources are Pydantic models that extend the `BaseAsset`. These models represent the fields that your resource yields and provides a mapping into OpenGraph nodes and
edges. In the example below, the id, name and hostname fields are required. If the DLT resource does not return any of these fields, the application will exit with an error. More information on how to define fields using Pydantic can be found [here](https://docs.pydantic.dev/latest/concepts/fields/).

```py
@app.asset(kind=NodeTypes.Asset,description="Example Asset",
    edges=[
        EdgeDef(
            start=NodeTypes.Asset,
            end=NodeTypes.Group,
            kind=EdgeTypes.MemberOf,
            description="Asset belongs to group",
        )
    ]
)
class Asset(BaseAsset):
    id: int # ID should be an integer
    name: str # Name should be a string
    hostname: str # hostname should be a string
    groups: list[str] # contains a list of groups (value string)

    @property
    def as_node(self) -> "ExtendedNode":
        properties = ExtendedProperties(
            name=self.name, displayname=self.name, hostname=self.hostname
        )
        return ExtendedNode(properties=properties)

    @property
    def _groups_memberships(self) -> Iterator[Edge]:
        # Assuming the groups are also retrieved and the group name is the reference used
        # to generate the ID
        for group in self.groups:
            start = EdgePath(value=self.as_node.id, match_by="id")
            end = EdgePath(value=Node.guid(name=group, node_type=NodeTypes.Group), match_by="id")
            yield Edge(kind=EdgeTypes.MemberOf, start=start, end=end)

    @property
    def edges(self) -> Iterator[Edge]:
        # Can yield multiple different edges
        yield from self._groups_memberships
        yield from ...
        yield from ...


```

**Key points:**

- The `@app.asset` decorator registers the model as an OpenGraph resource.
- The (`id`, `name`, `hostname`) fields should be included in the data yielded by the `@app.resource`. The fields defined by our model are not optional and should match the corresponding data types.
- `as_node` maps the fields into a node as used by OpenGraph.
- `edges` yields relationships to other nodes (empty in the template).


## Extending node properties

OpenGraph nodes have standard properties (like `name` and `displayname`). You can extend
them with resource-specific fields by defining your own ExtendedProperties and inheriting `NodeProperties`.

```py
class ExtendedProperties(NodeProperties):
    hostname: str


class ExtendedNode(Node):
    properties: ExtendedProperties
```

This allows you to add additional data to a node without changing the base OpenGraph schema.

## TLDR; workflow

1. For every resource, create a dedicated model in `models/` and decorate it with `@app.asset`.
2. Define resource-specific fields for the Pydantic model. These can either be required or optional fields.
3. Extend `NodeProperties` if you want to include custom node properties for the resource.
4. Implement `as_node` and `edges` for OpenGraph output. The pipeline will refuse to start unless these methods have been implemented.

Next up, and the final step, is configuring the "core" [opengraph model](graph.md) for your source.
