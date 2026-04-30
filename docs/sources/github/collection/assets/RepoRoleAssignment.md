# RepoRoleAssignment
This section describes the exported OpenGraph asset(s) for the RepoRoleAssignment class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.






## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_User](../../graph/nodes/GH_User.md) | [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_HasRole](../../graph/edges/GH_HasRole.md) | User has repo role |
| [GH_Team](../../graph/nodes/GH_Team.md) | [GH_RepoRole](../../graph/nodes/GH_RepoRole.md) | [GH_HasRole](../../graph/edges/GH_HasRole.md) | Team has repo role |
| [GH_User](../../graph/nodes/GH_User.md) | [GH_Branch](../../graph/nodes/GH_Branch.md) | [GH_CanWriteBranch](../../graph/edges/GH_CanWriteBranch.md) | User can push commits to this branch via actor-level bypass allowances |
| [GH_Team](../../graph/nodes/GH_Team.md) | [GH_Branch](../../graph/nodes/GH_Branch.md) | [GH_CanWriteBranch](../../graph/edges/GH_CanWriteBranch.md) | Team can push commits to this branch via actor-level bypass allowances |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

