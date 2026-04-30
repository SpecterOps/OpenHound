# SSO
This section describes the exported OpenGraph asset(s) for the SSO class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | :fontawesome-solid-key: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | [jamf_Contains](../../graph/edges/jamf_Contains.md) | The tenant contains this SSO integration. |
| [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_SSO_Login](../../graph/edges/jamf_SSO_Login.md) | SSO sources can map attributes to authenticate and inherit the privileges of the target. |
| [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_SSO_Login](../../graph/edges/jamf_SSO_Login.md) | SSO sources can map group attributes to authenticate and inherit the privileges of the target group. |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

