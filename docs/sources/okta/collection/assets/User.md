# User
This section describes the exported OpenGraph asset(s) for the User class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [Okta_User](../../graph/nodes/Okta_User.md) | :fontawesome-solid-user: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_Contains](../../graph/edges/Okta_Contains.md) | Organization contains user |
| [Okta_Realm](../../graph/nodes/Okta_Realm.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_RealmContains](../../graph/edges/Okta_RealmContains.md) | Realm contains user |
| [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_ManagerOf](../../graph/edges/Okta_ManagerOf.md) | User is a manager of another user |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

