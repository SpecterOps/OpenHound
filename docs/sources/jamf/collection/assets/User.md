# User
This section describes the exported OpenGraph asset(s) for the User class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [jamf_ComputerUser](../../graph/nodes/jamf_ComputerUser.md) | :fontawesome-solid-user: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [jamf_Computer](../../graph/nodes/jamf_Computer.md) | [jamf_ComputerUser](../../graph/nodes/jamf_ComputerUser.md) | [jamf_AssignedUser](../../graph/edges/jamf_AssignedUser.md) | The specified user is assigned to the source computer. |
| [jamf_ComputerUser](../../graph/nodes/jamf_ComputerUser.md) | [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_MatchedEmail](../../graph/edges/jamf_MatchedEmail.md) | The Jamf principal email attribute matched the Jamf account email indicating it is likely the same account. |
| [jamf_ComputerUser](../../graph/nodes/jamf_ComputerUser.md) | [jamf_Account](../../graph/nodes/jamf_Account.md) | [jamf_MatchedName](../../graph/edges/jamf_MatchedName.md) | The Jamf principal name or displayname attributes matched the Jamf account name. |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

