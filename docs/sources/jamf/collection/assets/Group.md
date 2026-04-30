# Group
This section describes the exported OpenGraph asset(s) for the Group class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [jamf_Group](../../graph/nodes/jamf_Group.md) | :fontawesome-solid-people-group: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Contains](../../graph/edges/jamf_Contains.md) | The tenant contains this group. |
| [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_MemberOf](../../graph/edges/jamf_MemberOf.md) | The source account is a member of the group. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_AdminTo](../../graph/edges/jamf_AdminTo.md) | The group has administrative permissions on the tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Site](../../graph/nodes/jamf_Site.md) | [jamf_AdminToSite](../../graph/edges/jamf_AdminToSite.md) | The group has administrative permissions for the target site. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_CreateAccounts](../../graph/edges/jamf_CreateAccounts.md) | The group can create accounts on the target tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_UpdateAccounts](../../graph/edges/jamf_UpdateAccounts.md) | The group can update accounts on the target tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_CreatePolicies](../../graph/edges/jamf_CreatePolicies.md) | The group can create policies on the target computer. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_UpdatePolicies](../../graph/edges/jamf_UpdatePolicies.md) | The group can update policies on the target computer. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_CreateComputerExtensions](../../graph/edges/jamf_CreateComputerExtensions.md) | The group can create extensions on the target computer. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_UpdateComputerExtensions](../../graph/edges/jamf_UpdateComputerExtensions.md) | The group can update extensions on the target computer. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_ScriptsNonTraversable](../../graph/edges/jamf_ScriptsNonTraversable.md) | The group can create or update scripts on the target. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_Update_Recurring_Scripts](../../graph/edges/jamf_Update_Recurring_Scripts.md) | The group can create or update scripts on the target. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_CreateAPIRoles](../../graph/edges/jamf_CreateAPIRoles.md) | The group can create API Roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_UpdateAPIRoles](../../graph/edges/jamf_UpdateAPIRoles.md) | The group can update API Roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Create_Role](../../graph/edges/jamf_Create_API_Client_and_Create_Role.md) | The group can create API clients and roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Update_Role](../../graph/edges/jamf_Create_API_Client_and_Update_Role.md) | The group can create API clients and update roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Create_API_Client_and_Assign_Role](../../graph/edges/jamf_Create_API_Client_and_Assign_Role.md) | The group can create API clients and assign roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_API_Client_and_Update_Roles](../../graph/edges/jamf_Update_API_Client_and_Update_Roles.md) | The group can update API clients and update roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_API_Client_and_Create_Roles](../../graph/edges/jamf_Update_API_Client_and_Create_Roles.md) | The group can update API clients and create roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_Tenant](../../graph/nodes/jamf_Tenant.md) | [jamf_Update_API_Client_and_Assign_Role](../../graph/edges/jamf_Update_API_Client_and_Assign_Role.md) | The group can update API clients and assign roles in the Jamf tenant. |
| [jamf_Group](../../graph/nodes/jamf_Group.md) | [jamf_SSOIntegration](../../graph/nodes/jamf_SSOIntegration.md) | [jamf_Update_SSO_Settings](../../graph/edges/jamf_Update_SSO_Settings.md) | The group can update SSO settings in the Jamf tenant. |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

