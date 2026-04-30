# PersonalAccessToken
This section describes the exported OpenGraph asset(s) for the PersonalAccessToken class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_PersonalAccessToken](../../graph/nodes/GH_PersonalAccessToken.md) | :fontawesome-solid-key: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_User](../../graph/nodes/GH_User.md) | [GH_PersonalAccessToken](../../graph/nodes/GH_PersonalAccessToken.md) | [GH_HasPersonalAccessToken](../../graph/edges/GH_HasPersonalAccessToken.md) | User owns PAT |
| [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_PersonalAccessToken](../../graph/nodes/GH_PersonalAccessToken.md) | [GH_Contains](../../graph/edges/GH_Contains.md) | Org contains PAT |
| [GH_PersonalAccessToken](../../graph/nodes/GH_PersonalAccessToken.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_CanAccess](../../graph/edges/GH_CanAccess.md) | PAT can access org |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

