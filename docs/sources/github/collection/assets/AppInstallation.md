# AppInstallation
This section describes the exported OpenGraph asset(s) for the AppInstallation class. Each resource wrapped with the @app.asset decorator will export documentation for an OpenGraph node, multiple edges or a combination of both.


## Node
| Name | Icon |
|------|------|
| [GH_AppInstallation](../../graph/nodes/GH_AppInstallation.md) | :fontawesome-solid-plug: |





## Edges

| Start | End | Kind | Description |
|-------|-----|------|-------------|
| [GH_Organization](../../graph/nodes/GH_Organization.md) | [GH_AppInstallation](../../graph/nodes/GH_AppInstallation.md) | [GH_Contains](../../graph/edges/GH_Contains.md) | Org contains app installation |
| [GH_App](../../graph/nodes/GH_App.md) | [GH_AppInstallation](../../graph/nodes/GH_AppInstallation.md) | [GH_InstalledAs](../../graph/edges/GH_InstalledAs.md) | App is installed as this installation |





## Resource attributes
This section describes the data collected and available fields as present in the exported jsonl/parquet files.

