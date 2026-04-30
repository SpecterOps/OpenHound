# SecretScanningAlert
This section describes the exported OpenGraph asset(s) for the SecretScanningAlert class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_SecretScanningAlert](../../graph/nodes/GH_SecretScanningAlert.md) | :fontawesome-solid-key: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_SecretScanningAlert](../../graph/nodes/GH_SecretScanningAlert.md) | [GH_Contains](../../graph/edges/GH_Contains.md) | Org contains secret scanning alert |
| [GH_Repository](../../graph/nodes/GH_Repository.md) | [GH_SecretScanningAlert](../../graph/nodes/GH_SecretScanningAlert.md) | [GH_HasSecretScanningAlert](../../graph/edges/GH_HasSecretScanningAlert.md) | Repository has secret scanning alert |
| [GH_SecretScanningAlert](../../graph/nodes/GH_SecretScanningAlert.md) | [GH_User](../../graph/nodes/GH_User.md) | [GH_ValidToken](../../graph/edges/GH_ValidToken.md) | Alert secret is a valid PAT for this user |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

