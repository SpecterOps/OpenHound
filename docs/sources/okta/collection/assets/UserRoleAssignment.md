# UserRoleAssignment
This section describes the exported OpenGraph asset(s) for the UserRoleAssignment class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [Okta_RoleAssignment](../../graph/nodes/Okta_RoleAssignment.md) | :fontawesome-solid-clipboard-check: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_RoleAssignment](../../graph/nodes/Okta_RoleAssignment.md) | [Okta_HasRoleAssignment](../../graph/edges/Okta_HasRoleAssignment.md) | User has a role assignment |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_RoleAssignment](../../graph/nodes/Okta_RoleAssignment.md) | [Okta_HasRoleAssignment](../../graph/edges/Okta_HasRoleAssignment.md) | Group has a role assignment |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Role](../../graph/nodes/Okta_Role.md) | [Okta_HasRole](../../graph/edges/Okta_HasRole.md) | User is assigned a built-in role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Role](../../graph/nodes/Okta_Role.md) | [Okta_HasRole](../../graph/edges/Okta_HasRole.md) | Group is assigned a built-in role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_CustomRole](../../graph/nodes/Okta_CustomRole.md) | [Okta_HasRole](../../graph/edges/Okta_HasRole.md) | User is assigned a custom role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_CustomRole](../../graph/nodes/Okta_CustomRole.md) | [Okta_HasRole](../../graph/edges/Okta_HasRole.md) | Group is assigned a custom role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_AddMember](../../graph/edges/Okta_AddMember.md) | Group can add member to groups |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_AddMember](../../graph/edges/Okta_AddMember.md) | User can add member to groups |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_AddMember](../../graph/edges/Okta_AddMember.md) | Application can add member to groups |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | Group has group admin role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | User has group admin role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | Application has group admin role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | Group has group admin role for groups |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | User has group admin role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | Application has group admin role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_AppAdmin](../../graph/edges/Okta_AppAdmin.md) | Group has app admin role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_AppAdmin](../../graph/edges/Okta_AppAdmin.md) | User has app admin role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_AppAdmin](../../graph/edges/Okta_AppAdmin.md) | Application has app admin role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupMembershipAdmin](../../graph/edges/Okta_GroupMembershipAdmin.md) | Group has app GROUP_MEMBERSHIP_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupMembershipAdmin](../../graph/edges/Okta_GroupMembershipAdmin.md) | User has GROUP_MEMBERSHIP_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupMembershipAdmin](../../graph/edges/Okta_GroupMembershipAdmin.md) | Application has GROUP_MEMBERSHIP_ADMIN role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_HelpDeskAdmin](../../graph/edges/Okta_HelpDeskAdmin.md) | Group has HELPDESK_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_HelpDeskAdmin](../../graph/edges/Okta_HelpDeskAdmin.md) | User has HELPDESK_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_HelpDeskAdmin](../../graph/edges/Okta_HelpDeskAdmin.md) | Application has HELPDESK_ADMIN role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_MobileAdmin](../../graph/edges/Okta_MobileAdmin.md) | Group has app MOBILE_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_MobileAdmin](../../graph/edges/Okta_MobileAdmin.md) | User has MOBILE_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_MobileAdmin](../../graph/edges/Okta_MobileAdmin.md) | Application has MOBILE_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Group has ORG_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | User has ORG_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Application has ORG_ADMIN role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Group has ORG_ADMIN role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Group has ORG_ADMIN role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Group has ORG_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Application has ORG_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Application has ORG_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_OrgAdmin](../../graph/edges/Okta_OrgAdmin.md) | Application has ORG_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_SuperAdmin](../../graph/edges/Okta_SuperAdmin.md) | User has SUPER_ADMIN role |
| [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_SuperAdmin](../../graph/edges/Okta_SuperAdmin.md) | Group has SUPER_ADMIN role |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_SuperAdmin](../../graph/edges/Okta_SuperAdmin.md) | Application has SUPER_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | User has GROUP_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_GroupAdmin](../../graph/edges/Okta_GroupAdmin.md) | User has GROUP_ADMIN role |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Group](../../graph/nodes/Okta_Group.md) | [Okta_ScopedTo](../../graph/edges/Okta_ScopedTo.md) | Role assignment is scoped to group |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_ScopedTo](../../graph/edges/Okta_ScopedTo.md) | Role assignment is scoped to org |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_ScopedTo](../../graph/edges/Okta_ScopedTo.md) | Role assignment is scoped to application |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

