# Agent
This section describes the exported OpenGraph asset(s) for the Agent class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [Okta_Agent](../../graph/nodes/Okta_Agent.md) | :fontawesome-solid-gear: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_Agent](../../graph/nodes/Okta_Agent.md) | [Okta_AgentPool](../../graph/nodes/Okta_AgentPool.md) | [Okta_AgentMemberOf](../../graph/edges/Okta_AgentMemberOf.md) | Agent belongs to agent pool |
| [User](../../graph/nodes/User.md) | [Okta_Agent](../../graph/nodes/Okta_Agent.md) | [Okta_HostsAgent](../../graph/edges/Okta_HostsAgent.md) | Computer hosts okta agent |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

