# ApplicationUser
This section describes the exported OpenGraph asset(s) for the ApplicationUser class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.






## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_AppAssignment](../../graph/edges/Okta_AppAssignment.md) | User is assigned to an application |
| [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_UserPull](../../graph/edges/Okta_UserPull.md) | User is pulled form an external application |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Application](../../graph/nodes/Okta_Application.md) | [Okta_UserPush](../../graph/edges/Okta_UserPush.md) | User is pushed to an application |
| [Okta_User](../../graph/nodes/Okta_User.md) | [User](../../graph/nodes/User.md) | [Okta_UserSync](../../graph/edges/Okta_UserSync.md) | User is synced to an Active Directory user |
| [User](../../graph/nodes/User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_UserSync](../../graph/edges/Okta_UserSync.md) | User is synced from an Active Directory user |
| [User](../../graph/nodes/User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_PasswordSync](../../graph/edges/Okta_PasswordSync.md) | Credentials are synced between AD and Okta users |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_PasswordSync](../../graph/edges/Okta_PasswordSync.md) | Credentials are synced between okta orgs |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

