# OrgRole
This section describes the exported OpenGraph asset(s) for the OrgRole class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | :fontawesome-solid-user-tie: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Contains](../../graph/edges/GH_Contains.md) | Org contains role |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_HasBaseRole](../../graph/edges/GH_HasBaseRole.md) | Role inherits base role |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_CreateRepository](../../graph/edges/GH_CreateRepository.md) | Role can create repositories in the organization |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_InviteMember](../../graph/edges/GH_InviteMember.md) | Role can invite members to the organization |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_AddCollaborator](../../graph/edges/GH_AddCollaborator.md) | Role can add outside collaborators to repositories |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_CreateTeam](../../graph/edges/GH_CreateTeam.md) | Role can create teams in the organization |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_TransferRepository](../../graph/edges/GH_TransferRepository.md) | Role can transfer repositories out of the organization |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_ViewSecretScanningAlerts](../../graph/edges/GH_ViewSecretScanningAlerts.md) | Role can view secret scanning alerts for the organization |
| [GH_OrgRole](../../graph/nodes/GH_OrgRole.md) | [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_ResolveSecretScanningAlerts](../../graph/edges/GH_ResolveSecretScanningAlerts.md) | Role can resolve secret scanning alerts for the organization |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

