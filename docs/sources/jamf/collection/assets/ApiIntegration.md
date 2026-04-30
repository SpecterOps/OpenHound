# ApiIntegration
This section describes the exported OpenGraph asset(s) for the ApiIntegration class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | :fontawesome-solid-plug: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Contains](../../graph/edges/jamf_Contains.md) | The tenant contains this API client. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_CreateAccounts](../../graph/edges/jamf_CreateAccounts.md) | The API client can create accounts on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_UpdateAccounts](../../graph/edges/jamf_UpdateAccounts.md) | The API client can update accounts on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_CreatePolicies](../../graph/edges/jamf_CreatePolicies.md) | The API client can create policies on the target computer. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_UpdatePolicies](../../graph/edges/jamf_UpdatePolicies.md) | The API client can update policies on the target computer. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_CreateComputerExtensions](../../graph/edges/jamf_CreateComputerExtensions.md) | The API client can create computer extensions on the target computer. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_UpdateComputerExtensions](../../graph/edges/jamf_UpdateComputerExtensions.md) | The API client can update computer extensions on the target computer. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_ScriptsNonTraversable](../../graph/edges/jamf_ScriptsNonTraversable.md) | The API Client can create or update scripts on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_Update_Recurring_Scripts](../../graph/edges/jamf_Update_Recurring_Scripts.md) | The API Client can update recurring scripts on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_CreateAPIRoles](../../graph/edges/jamf_CreateAPIRoles.md) | The API Client can create roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_Roles_Assigned_To_Self](../../graph/edges/jamf_Update_Roles_Assigned_To_Self.md) | The API Client can update roles assigned to itself on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Create_Role](../../graph/edges/jamf_Create_API_Client_and_Create_Role.md) | The API Client can create and assign roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Update_Role](../../graph/edges/jamf_Create_API_Client_and_Update_Role.md) | The API Client can create and update roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Assign_Role](../../graph/edges/jamf_Create_API_Client_and_Assign_Role.md) | The API Client can create and assign roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_Self_and_Update_Roles](../../graph/edges/jamf_Update_Self_and_Update_Roles.md) | The API Client can update roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_Self_and_Create_Roles](../../graph/edges/jamf_Update_Self_and_Create_Roles.md) | The API Client can create roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_Self_and_Assign_Roles](../../graph/edges/jamf_Update_Self_and_Assign_Roles.md) | The API Client can assign roles on the target tenant. |
| [jamf_ApiClient](../../graph/nodes/jamf_ApiClient.md) | [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | [jamf_Update_SSO_Settings](../../graph/edges/jamf_Update_SSO_Settings.md) | The API Client can update SSO settings on the target tenant. |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

