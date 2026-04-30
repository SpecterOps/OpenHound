# ExternalIdentity
This section describes the exported OpenGraph asset(s) for the ExternalIdentity class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_ExternalIdentity](../../graph/nodes/GH_ExternalIdentity.md) | :fontawesome-solid-arrows-left-right: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_SamlIdentityProvider](../../graph/nodes/GH_SamlIdentityProvider.md) | [GH_ExternalIdentity](../../graph/nodes/GH_ExternalIdentity.md) | [GH_HasExternalIdentity](../../graph/edges/GH_HasExternalIdentity.md) | IdP has external identity |
| [GH_ExternalIdentity](../../graph/nodes/GH_ExternalIdentity.md) | [GH_User](../../graph/nodes/GH_User.md) | [GH_MapsToUser](../../graph/edges/GH_MapsToUser.md) | External identity maps to a user |
| [GH_ExternalIdentity](../../graph/nodes/GH_ExternalIdentity.md) | [GH_User](../../graph/nodes/GH_User.md) | [GH_SyncedTo](../../graph/edges/GH_SyncedTo.md) | Foreign IdP user is synced to a GitHub user |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

