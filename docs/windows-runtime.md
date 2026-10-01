# Windows x64 foreground runtime

This is the first stage of the Windows distribution. It packages OpenHound with the GitHub collector and a private CPython 3.13 runtime. It runs the existing BloodHound Enterprise scheduler in the foreground. It does not register a service or install additional collectors.

## Build requirements

Build on Windows x64 with PowerShell, Git, Python 3.13 x64, and `uv` on PATH. Network access is needed **at build time** for the [official CPython 3.13.16 embeddable archive](https://www.python.org/downloads/release/python-31316/) and the wheels recorded in `uv.lock`. The script checks the CPython SHA-256 and uses the lock file, wheel hashes, and wheel-only dependency installation. Keep a full Git checkout so `hatch-vcs` can determine the OpenHound version.

From the repository root:

```powershell
./scripts/windows/build.ps1
```

The payload is `dist/windows/openhound-windows-x64/`. Choose a different empty output directory with `-OutputDirectory 'dist/windows/OpenHound Payload'`. The script will fail if the chosen directory already exists. `runtime-info.json` records CPython, OpenHound and GitHub collector versions, the source commit, and hashes of the CPython archive, application wheel and `uv.lock`; `requirements.lock` records the resolved dependency set. The payload keeps wheel `.dist-info`, entry points, package resources and native libraries. It contains no customer configuration or credentials.

The builder uses Python and uv only on the build machine. Customers do not need Python, pip, uv, Docker, development tools, or Python on PATH. The CPython archive supplies `vcruntime140.dll` and `vcruntime140_1.dll`; an independent audit of native wheel DLL dependencies on a clean Windows image is still needed. Until that audit is complete, require a current Microsoft Visual C++ 2015–2022 Redistributable (x64) on customer machines. Windows CI checks imports on its hosted image but does not establish that every clean Windows installation already has the DLLs required by every native wheel.

## Instance configuration

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

For a development or Linux package installation, the equivalent foreground entry point is `openhound-scheduler --instance-dir /writable/instance`, or `python -m openhound.scheduler --instance-dir /writable/instance`. With no instance override on Linux, the Docker Enterprise launcher keeps its prior paths and configuration behavior.

For troubleshooting, inspect `logs\openhound.log`, `logs\worker-*.log`, and `logs\ext_*.log` inside the instance. Missing BHE settings produce a message naming the required key and config directory. An unknown collector prints the installed collector names. A missing GitHub credential usually appears when a job starts; check the `sources.source.github.credentials` section and permissions. If Windows reports a missing DLL, check the VC runtime prerequisite above. The scheduler polls BHE every 30 seconds, so a healthy idle process may produce no collection output until a BHE job is available.

## Validation

Windows CI builds the payload and tests it from a path with spaces and an unrelated current directory, with Python tools removed from PATH. It checks metadata and GitHub entry point discovery, then installs a CI-only offline collector into a **copy** of the payload. The offline test starts a scheduler job through `Service`, runs a spawned worker, checks the worker's instance paths, and verifies clean idle shutdown using a console break signal. No external API credentials are needed. The release payload contains only the GitHub collector.

To perform a live test, put valid BHE and GitHub values in a dedicated instance as above, start the foreground launcher, queue a GitHub collection job in BHE for this client, and watch the instance logs for job start, completed collection and BHE completion status. Confirm files appear under `output\github`, pipeline state appears under `state`, and no files are written to the payload directory. Stop with Ctrl+C and start it again to confirm configuration and state persist. The offline test does not verify GitHub API permissions or live BHE ingestion.

Service integration can later invoke the packaged scheduler module or the private Python executable. It must pass the same instance directory, run with an account allowed to read secrets and write instance data, preserve the application directory across launches, and provide a stop signal with enough time for an active worker to finish. Installer placement and upgrades should keep the instance directory separate from the application files.
