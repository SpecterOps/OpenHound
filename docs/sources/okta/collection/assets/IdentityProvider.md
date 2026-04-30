# IdentityProvider
This section describes the exported OpenGraph asset(s) for the IdentityProvider class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [Okta_IdentityProvider](../../graph/nodes/Okta_IdentityProvider.md) | :fontawesome-solid-right-to-bracket: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_IdentityProvider](../../graph/nodes/Okta_IdentityProvider.md) | [Okta_Contains](../../graph/edges/Okta_Contains.md) | Organization contains identity provider |
| [Okta_IdentityProvider](../../graph/nodes/Okta_IdentityProvider.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_IdpGroupAssignment](../../graph/edges/Okta_IdpGroupAssignment.md) | Group provisioned by IDP |
| [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_IdentityProvider](../../graph/nodes/Okta_IdentityProvider.md) | [Okta_InboundOrgSSO](../../graph/edges/Okta_InboundOrgSSO.md) | Organization SSO via identity provider |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

