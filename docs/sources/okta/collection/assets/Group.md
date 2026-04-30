# Group
This section describes the exported OpenGraph asset(s) for the Group class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [Okta_Group](../../graph/nodes/Okta_Group.md) | :fontawesome-solid-users: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Contains](../../graph/edges/Okta_Contains.md) | Organization contains group |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupPull](../../graph/edges/Okta_GroupPull.md) | Application pulls group from external source |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_MembershipSync](../../graph/edges/Okta_MembershipSync.md) | Org2org membership sync |
| [Group](../../graph/nodes/Group.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_MembershipSync](../../graph/edges/Okta_MembershipSync.md) | AD membership sync |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

