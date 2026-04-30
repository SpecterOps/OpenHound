# Device
This section describes the exported OpenGraph asset(s) for the Device class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [Okta_Device](../../graph/nodes/Okta_Device.md) | :fontawesome-solid-mobile: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [Okta_Organization](../../graph/nodes/Okta_Organization.md) | [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_Contains](../../graph/edges/Okta_Contains.md) | Organization contains device |
| [Okta_Device](../../graph/nodes/Okta_Device.md) | [Okta_User](../../graph/nodes/Okta_User.md) | [Okta_DeviceOf](../../graph/edges/Okta_DeviceOf.md) | Device belongs to user |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

