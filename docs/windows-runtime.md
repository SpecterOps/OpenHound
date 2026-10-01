# Windows x64 installer and foreground runtime

The Windows distribution packages OpenHound with a private CPython 3.13 runtime and an offline-capable Inno Setup installer. GitHub, Okta, and Jamf are optional installer components. It runs the existing BloodHound Enterprise scheduler in the foreground; keep the console open while it runs.

## Install and select extensions

Run `openhound-<version>-windows-x64-setup.exe` as an administrator. The default application directory is `C:\Program Files\OpenHound`. Choose **Runtime and all extensions**, **Runtime only**, or **Custom installation** and select any combination of:

| Component | Package | Documentation |
|---|---|---|
| GitHub | `openhound-github` | [GitHub collector](https://github.com/SpecterOps/openhound-github) |
| Okta | `openhound-okta` | [Okta collector](https://github.com/SpecterOps/openhound-okta) |
| Jamf | `openhound-jamf` | [Jamf collector](https://github.com/SpecterOps/openhound-jamf) |

The installer contains all component files and copies only the selected collector packages. The required runtime includes the combined, locked dependency libraries for every supported collector, including libraries for unselected collectors. Installation does not download packages or require Python, pip, or uv on the customer machine.

For a silent GitHub-and-Okta installation:

```powershell
$setup = Start-Process -FilePath '.\openhound-<version>-windows-x64-setup.exe' -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TYPE=custom', '/COMPONENTS="runtime,extensions\github,extensions\okta"' -Wait -PassThru
if ($setup.ExitCode -ne 0) { throw "OpenHound setup failed: $($setup.ExitCode)" }
```

Use `/TYPE=minimal /COMPONENTS="runtime"` for runtime only, or `/TYPE=full` for all collectors. A runtime-only installation can show help, but needs a collector added before scheduling collections. For troubleshooting setup itself, add `/LOG="C:\Writable Path\openhound-setup.log"`.

Rerun the installer to add or remove collectors. The wizard restores previous selections; silent setup also preserves them when `/TYPE` and `/COMPONENTS` are omitted. Deselecting a collector removes its package and discovery metadata. Upgrading to a new installer replaces the runtime and selected collectors with the versions bundled in that release. Future extensions become available through new installer releases.

Stop all scheduler instances with Ctrl+C or Ctrl+Break and wait for active collections to finish before modifying, upgrading, or uninstalling OpenHound. Setup and uninstall check a shared Windows process marker and refuse to proceed while a scheduler or collection worker is running. The installer does not terminate active collections automatically.

The `python` and `extensions` directories are installer-managed and are rebuilt during each installation to remove obsolete files. Setup moves the existing runtime, collectors and application metadata into `.openhound-backup` until replacement succeeds. If installation fails or is cancelled during extraction, it restores the previous application files; successful setup removes the backup. A backup left by an abruptly terminated installer must be recovered before rerunning setup. Keep customer files in instance directories outside the application directory. Upgrade and uninstall preserve those external instance directories, including configuration, secrets, logs, output, and pipeline state. Uninstall OpenHound through Windows **Installed apps**.

## Build requirements

Build on Windows x64 with PowerShell, Git, Python 3.13 x64, and `uv` on PATH. Install **Inno Setup 6** to compile the EXE. Network access is needed **at build time** for the [official CPython 3.13.16 embeddable archive](https://www.python.org/downloads/release/python-31316/) and the wheels recorded in `uv.lock`. The script checks the CPython SHA-256 and uses the lock file, wheel hashes, and wheel-only dependency installation. Keep a full Git checkout so `hatch-vcs` can determine the OpenHound version.

From the repository root:

```powershell
./scripts/windows/build.ps1
./scripts/windows/build-installer.ps1
```

The payload is `dist/windows/openhound-windows-x64/` and the installer is `dist/windows/installers/openhound-<version>-windows-x64-setup.exe`. Choose a different empty payload directory with `build.ps1 -OutputDirectory 'dist/windows/OpenHound Payload'`, then pass it to `build-installer.ps1 -PayloadDirectory 'dist/windows/OpenHound Payload'`. The runtime builder fails if the chosen directory already exists. The installer builder finds `ISCC.exe` on PATH or in the default Inno Setup 6 directory; override with `-CompilerPath`.

`runtime-info.json` records CPython, OpenHound and all supported collector versions, the source commit, and hashes of the CPython archive, application wheel, collector wheels and `uv.lock`. The supported-extension list describes the release contents, not the user's installed selection. `requirements.lock` records shared runtime dependencies; `extensions.json` records collector versions and wheel hashes. The payload keeps wheel `.dist-info`, entry points, package resources and native libraries. It contains all supported collectors when run directly and contains no customer configuration or credentials.

The application layout is:

```text
OpenHound/
  OpenHound.cmd
  python/Lib/site-packages/                  # Framework and shared dependencies
  extensions/github/site-packages/          # Present when selected
  extensions/okta/site-packages/             # Present when selected
  extensions/jamf/site-packages/             # Present when selected
```

The embedded Python path configuration includes each optional location, so OpenHound's existing `openhound.sources` entry-point discovery works in the main process and spawned workers. To inspect installed collectors without needing BHE configuration:

```powershell
& 'C:\Program Files\OpenHound\python\python.exe' -I -B -c 'from importlib.metadata import entry_points; print(", ".join(sorted(e.name for e in entry_points(group="openhound.sources"))))'
```

The builder uses Python and uv only on the build machine. Customers do not need Python, pip, uv, Docker, development tools, or Python on PATH. The CPython archive supplies `vcruntime140.dll` and `vcruntime140_1.dll`; an independent audit of native wheel DLL dependencies on a clean Windows image is still needed. Until that audit is complete, require a current Microsoft Visual C++ 2015–2022 Redistributable (x64) on customer machines. Windows CI checks imports on its hosted image but does not establish that every clean Windows installation already has the DLLs required by every native wheel.

## Instance configuration

Each scheduler instance runs **one configured collector**, even when several are installed. Use `collector_name = "github"`, `"okta"`, or `"jamf"` in that instance's BHE configuration. To run several collectors, launch separately configured instances, each with its own BHE settings and collector credentials. Consult the collector documentation above for Okta and Jamf credential configuration.

The default instance directory is `%ProgramData%\SpecterOps\OpenHound\instances\default`. Use `--instance NAME` for another named instance or `--instance-dir 'C:\Writable Path\Instance'` to override the root. Each instance contains `.dlt\config.toml`, `.dlt\secrets.toml`, `logs`, `state`, `output`, and `temp`. DLT pipeline state and local destination files, collection output, lookup database, logs and temporary files use those directories. The application directory is read only during execution. The account launching the scheduler needs read/write access to its instance directory; limit read access to the secrets file and any GitHub key file.

Create the instance files before starting. This example uses a GitHub organization token. Replace every placeholder locally; do not commit these files or include them in a payload:

`%ProgramData%\SpecterOps\OpenHound\instances\default\.dlt\config.toml`:

```toml
[destination.bloodhoundenterprise]
url = "https://YOUR-BHE-HOST"
collector_name = "github"
```

`%ProgramData%\SpecterOps\OpenHound\instances\default\.dlt\secrets.toml`:

```toml
[destination.bloodhoundenterprise]
token_id = "YOUR-BHE-API-TOKEN-ID"
token_key = "YOUR-BHE-API-TOKEN-KEY"

[sources.source.github.credentials]
org_name = "YOUR-GITHUB-ORG"
token = "YOUR-GITHUB-TOKEN"
```

The minimum BHE values are its URL, API token ID and API token key, plus `collector_name = "github"`. The GitHub collector supports a token with an organization name as shown, or GitHub App credentials. Give the GitHub token read access to the organization data you intend to collect. For GitHub App auth, the collector's organization form needs `client_id`, `install_id`, `org_name`, and an **absolute** `key_path` to its private PEM file. The collector also supports enterprise App credentials; use its own credential documentation for that mode. Do not log, publish, or put real tokens or PEM files in the repository or build output.

## Run and stop

From any working directory, including one unrelated to the payload:

```powershell
& 'C:\Program Files\OpenHound\OpenHound.cmd'
& 'C:\Program Files\OpenHound\OpenHound.cmd' --instance customer-a
& 'C:\Program Files\OpenHound\OpenHound.cmd' --instance-dir 'D:\OpenHound Instances\customer-a'
```

`OpenHound.cmd` starts its adjacent private `python.exe` and does not modify system Python. Leave the console open. Press **Ctrl+C** or **Ctrl+Break** to stop while idle; the scheduler closes its worker pool and exits. If a collection is running, shutdown waits for the worker to finish. Repeat the same command to restart; instance files and pipeline state persist.

For a foreground process launched without an interactive console, provide an absolute stop request path inside the instance:

```powershell
& 'C:\Program Files\OpenHound\OpenHound.cmd' --stop-file "$env:ProgramData\SpecterOps\OpenHound\instances\default\temp\stop.request"
```

To stop it from another PowerShell session, create that file with `New-Item -ItemType File -Path "$env:ProgramData\SpecterOps\OpenHound\instances\default\temp\stop.request"`. The scheduler consumes the file and exits after its current poll; an active collection is allowed to finish. The file must not already exist when starting.

For a development or Linux package installation, the equivalent foreground entry point is `openhound-scheduler --instance-dir /writable/instance`, or `python -m openhound.scheduler --instance-dir /writable/instance`. With no instance override on Linux, the Docker Enterprise launcher keeps its prior paths and configuration behavior.

For troubleshooting, inspect `logs\openhound.log`, `logs\worker-*.log`, and `logs\ext_*.log` inside the instance. Missing BHE settings produce a message naming the required key and config directory. An unknown collector prints the installed collector names. A missing GitHub credential usually appears when a job starts; check the `sources.source.github.credentials` section and permissions. If Windows reports a missing DLL, check the VC runtime prerequisite above. The scheduler polls BHE every 30 seconds, so a healthy idle process may produce no collection output until a BHE job is available.

## Validation

Windows CI builds the payload and installer. It launches setup and uninstall under the Windows debug API so each process, including Inno Setup's extracted `.tmp` executables and any helpers, receives an outbound firewall block before executing. The test controller and CI runner retain network access; temporary rules are removed afterward. CI tests all collector-selection combinations, including runtime only, with Python tools removed from PATH. It checks installed entry points, collector imports and metadata, dependency compatibility, repeated installation, adding/removing collectors, preservation of previous selections, cleanup of obsolete upgrade files, restoration after cancellation during extraction, a higher-version installer, concurrent instances, refusal to install/uninstall while running, and preservation of instance data after uninstall. A CI-only installer variant requests normal wizard cancellation during extraction; that hook is absent from release installers. Installations use paths with spaces and an unrelated current directory; verification also checks that the runtime does not modify its application directory.

CI also installs a CI-only offline collector into a **copy** of the payload. The offline test starts a scheduler job through `Service`, runs a spawned worker, checks the worker's instance paths, and verifies clean idle shutdown using a stop request file. Hosted CI does not provide a reliable interactive console for testing Ctrl+C or Ctrl+Break delivery. No external API credentials are needed; the offline collector is absent from the installer and release payload.

### Adding a future extension to a release

1. Add its pinned optional dependency to `pyproject.toml`, then refresh `uv.lock` with `uv lock`.
2. Add an entry to `scripts/windows/extensions.json` with a unique `id`, display `name`, distribution `package`, project `extra`, OpenHound `entrypoint`, `description`, and documentation `url`. Versions and wheel hashes come from the lock file.
3. Build and test on Windows. The collector and its dependency closure must have wheels compatible with the embedded Python version and Windows x64; the build refuses source compilation.
4. Publish the new installer. Catalog entries generate the component list, optional package locations, runtime search paths and validation expectations. CI automatically exercises selection combinations for the updated catalog.

To perform a live test, put valid BHE and GitHub values in a dedicated instance as above, start the foreground launcher, queue a GitHub collection job in BHE for this client, and watch the instance logs for job start, completed collection and BHE completion status. Confirm files appear under `output\github`, pipeline state appears under `state`, and no files are written to the payload directory. Stop with Ctrl+C and start it again to confirm configuration and state persist. The offline test does not verify GitHub API permissions or live BHE ingestion.

Service integration can later invoke the packaged scheduler module or the private Python executable. It must pass the same instance directory, run with an account allowed to read secrets and write instance data, preserve the application directory across launches, and provide a stop request with enough time for an active worker to finish. Installer placement and upgrades should keep the instance directory separate from the application files.
