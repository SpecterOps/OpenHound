# Branch
This section describes the exported OpenGraph asset(s) for the Branch class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_Branch](../../graph/nodes/GH_Branch.md) | :fontawesome-solid-code-branch: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_Branch](../../graph/nodes/GH_Branch.md) | [GH_HasBranch](../../graph/edges/GH_HasBranch.md) | Repository has branch |
| [GH_BranchProtectionRule](../../graph/nodes/GH_BranchProtectionRule.md) | [GH_Branch](../../graph/nodes/GH_Branch.md) | [GH_ProtectedBy](../../graph/edges/GH_ProtectedBy.md) | Branch is protected by rule |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

