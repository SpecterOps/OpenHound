# Account
This section describes the exported OpenGraph asset(s) for the Account class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [jamf_Account](../../graph/nodes/jamf_Account.md) | :fontawesome-solid-user-lock: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Contains](../../graph/edges/jamf_Contains.md) | The tenant contains this account. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_AdminTo](../../graph/edges/jamf_AdminTo.md) | The account has administrative permissions on the Jamf tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Site](../../graph/nodes/jamf_Site.md) | [jamf_AdminToSite](../../graph/edges/jamf_AdminToSite.md) | The account has administrative permissions for the target site. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_CreateAccounts](../../graph/edges/jamf_CreateAccounts.md) | The account can create accounts on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_UpdateAccounts](../../graph/edges/jamf_UpdateAccounts.md) | The account can update accounts on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_CreatePolicies](../../graph/edges/jamf_CreatePolicies.md) | The account can create policies on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_UpdatePolicies](../../graph/edges/jamf_UpdatePolicies.md) | The account can update policies on the target computer. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_CreateComputerExtensions](../../graph/edges/jamf_CreateComputerExtensions.md) | The account can create computer extensions on the target computer. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_UpdateComputerExtensions](../../graph/edges/jamf_UpdateComputerExtensions.md) | The account can update computer extensions on the target computer. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_ScriptsNonTraversable](../../graph/edges/jamf_ScriptsNonTraversable.md) | The account can create or update scripts on the target. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_Update_Recurring_Scripts](../../graph/edges/jamf_Update_Recurring_Scripts.md) | The account can update recurring scripts on the target computer. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_CreateAPIRoles](../../graph/edges/jamf_CreateAPIRoles.md) | The target can create API Roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_UpdateAPIRoles](../../graph/edges/jamf_UpdateAPIRoles.md) | The account can update API Roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Create_Role](../../graph/edges/jamf_Create_API_Client_and_Create_Role.md) | The account can create API clients and roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Update_Role](../../graph/edges/jamf_Create_API_Client_and_Update_Role.md) | The account can create API clients and update roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Assign_Role](../../graph/edges/jamf_Create_API_Client_and_Assign_Role.md) | The account can create API clients and assign roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_API_Client_and_Update_Roles](../../graph/edges/jamf_Update_API_Client_and_Update_Roles.md) | The account can update API clients and assign roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_API_Client_and_Create_Roles](../../graph/edges/jamf_Update_API_Client_and_Create_Roles.md) | The account can update API clients and assign roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_API_Client_and_Assign_Role](../../graph/edges/jamf_Update_API_Client_and_Assign_Role.md) | The account can update API clients and assign roles on the target tenant. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | [jamf_Update_SSO_Settings](../../graph/edges/jamf_Update_SSO_Settings.md) | The account can update SSO settings on the target tenant. |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

