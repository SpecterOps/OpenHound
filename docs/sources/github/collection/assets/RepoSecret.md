# RepoSecret
This section describes the exported OpenGraph asset(s) for the RepoSecret class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_RepoSecret](../../graph/nodes/GH_RepoSecret.md) | :fontawesome-solid-lock: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_RepoSecret](../../graph/nodes/GH_RepoSecret.md) | [GH_Contains](../../graph/edges/GH_Contains.md) | Repository contains secret |
| [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_RepoSecret](../../graph/nodes/GH_RepoSecret.md) | [GH_HasSecret](../../graph/edges/GH_HasSecret.md) | Repository has access to secret |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

