# Windows x64 installer

The `Windows installer` GitHub Action builds an Inno Setup executable from the private Windows runtime and uploads it in the `openhound-windows-x64-installer` Actions artifact. Download and extract that artifact ZIP to get `openhound-<version>-windows-x64-setup.exe`. The ZIP is only GitHub Actions transport; run the setup executable to install OpenHound. The installer requires administrator rights and defaults to `C:\Program Files\OpenHound`.

This stage installs the foreground scheduler. It does not register a Windows service or start OpenHound automatically. Service integration needs a wrapper that can ask the scheduler to stop, wait for an active worker, and coordinate upgrades with Windows Service Control Manager. Until then, stop the foreground process before running setup or uninstall. Inno Setup does not close it automatically.

## Install and configure

From an elevated PowerShell session, run the downloaded setup executable:

```powershell
& '.\openhound-<version>-windows-x64-setup.exe'
```

For unattended installation:

```powershell
& '.\openhound-<version>-windows-x64-setup.exe' /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

The installer includes the private Python interpreter, locked dependencies, OpenHound, and selectable GitHub, Okta, and Jamf collectors. See [Windows runtime](windows-runtime.md#install-and-select-extensions) for component selection and upgrade rollback behavior. Customers do not need Python, `uv`, or package downloads during installation or runtime; collection still needs access to BHE and the selected source. A current Microsoft Visual C++ 2015–2022 Redistributable (x64) remains a prerequisite pending the clean-machine DLL audit described in [Windows runtime](windows-runtime.md).

Create `%ProgramData%\SpecterOps\OpenHound\instances\default\.dlt\config.toml` and `secrets.toml` after installation, following the [configuration examples](windows-runtime.md#instance-configuration). Put the BHE URL and `collector_name = "github"` in `config.toml`; put the BHE token ID/key and GitHub token or App credentials in `secrets.toml`. Restrict access to secrets and any GitHub App PEM file to the account running OpenHound. No customer credentials are included in the installer or Actions artifact. The account running the scheduler needs read/write access to the instance directory.

Start from any working directory:

```powershell
& 'C:\Program Files\OpenHound\OpenHound.cmd'
```

For other instances, pass `--instance NAME` or `--instance-dir 'D:\OpenHound Instances\customer-a'`. Stop with Ctrl+C or Ctrl+Break, or use an absolute `--stop-file` path as described in [Windows runtime](windows-runtime.md#run-and-stop). An active worker is allowed to finish during graceful shutdown.

## Upgrade and uninstall

Install a newer setup executable over the existing installation after the scheduler has stopped. A fixed Inno Setup `AppId` identifies OpenHound across versions and reuses the previous install directory. The filename and displayed application version come from the packaged `runtime-info.json`; the executable's numeric file version uses its major, minor, and patch numbers. The setup replaces the private Python tree so obsolete dependency modules do not survive an upgrade. Run a newer version intentionally; the installer does not block downgrades.

Instance directories are outside the application directory, under `%ProgramData%\SpecterOps\OpenHound\instances\<name>` by default. Setup never packages or deletes them. Configuration, secrets, state, output, logs, and temporary files persist through upgrade and uninstall. Uninstall through Windows Installed Apps, or run `C:\Program Files\OpenHound\unins000.exe` (with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` for unattended removal). Remove an instance directory separately only if its data is no longer needed.

## Build and validation

On Windows x64 with the [runtime build requirements](windows-runtime.md#build-requirements) and Inno Setup 6 installed, run:

```powershell
./scripts/windows/build.ps1 -OutputDirectory 'dist/windows/OpenHound Payload'
./scripts/windows/build-installer.ps1 -PayloadDirectory 'dist/windows/OpenHound Payload'
```

The workflow uses `windows-2025`, which includes Inno Setup 6, and uploads only the final setup executable. Its Windows test installs a test-only earlier package with an offline collector from a path containing spaces, runs an installed scheduler worker from an unrelated working directory with Python and `uv` absent from `PATH`, upgrades in place to the release payload, checks every installed payload file against its built input and confirms the test collector was removed, then uninstalls. It verifies that instance config, secrets, state, output, logs, and temporary files persist. The earlier test package is built from a copy of the payload and is never uploaded.

Hosted CI does not verify live BHE/GitHub collection, clean-machine native DLL availability, or interactive Ctrl+C/Ctrl+Break delivery. The installer is currently unsigned; production distribution should sign the setup executable and verify its publisher before wider customer release.
