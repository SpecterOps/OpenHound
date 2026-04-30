# OrgSecret
This section describes the exported OpenGraph asset(s) for the OrgSecret class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_OrgSecret](../../graph/nodes/GH_OrgSecret.md) | :fontawesome-solid-lock: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_OrgSecret](../../graph/nodes/GH_OrgSecret.md) | [GH_Contains](../../graph/edges/GH_Contains.md) | Org contains secret |
| [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_OrgSecret](../../graph/nodes/GH_OrgSecret.md) | [GH_HasSecret](../../graph/edges/GH_HasSecret.md) | Repository can access org secret |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

