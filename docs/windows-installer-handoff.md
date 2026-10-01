# Windows distribution handoff

Use this note when starting the next chat about a downloadable Windows installer. The repository root is `OpenHound`. Read any applicable repository instructions and inspect the current code before changing it; this note describes the state as of 2026-10-01.

## Goal for the next stage

Create a GitHub Actions workflow that builds and uploads an **installable Windows x64 artifact**. The existing workflow uploads a portable application directory as a GitHub Actions artifact; it is not an installer. The longer term goal is one installer that bundles dependencies, runs OpenHound persistently as a Windows service, supports upgrades without manual uninstall, and needs little customer maintenance. Decide and document the installer format and whether service registration belongs in this next stage before implementing it. Preserve the instance directory and customer secrets across upgrades.

## What exists now

- [`scripts/windows/build.ps1`](../scripts/windows/build.ps1) builds `dist/windows/openhound-windows-x64/` by default. It requires Windows x64, PowerShell, Git, Python 3.13 x64, `uv`, a full Git checkout, and network access **on the build machine**. It downloads and checks the SHA-256 of the official CPython 3.13.16 x64 embeddable archive, installs hashed dependencies from `uv.lock` and the `github` extra into its private `python/Lib/site-packages`, then installs an OpenHound wheel. The GitHub collector is pinned at `openhound-github==0.16.1` in `pyproject.toml`.
- The payload contains `OpenHound.cmd`, private `python/`, `requirements.lock`, and `runtime-info.json` with runtime, application, collector, source commit, and relevant hashes. `OpenHound.cmd` runs `python.exe -I -B -m openhound.scheduler`. Customers do not need Python, pip, `uv`, Docker, or development tools.
- [`.github/workflows/windows-runtime.yml`](../.github/workflows/windows-runtime.yml) builds to `dist/windows/OpenHound Payload/`, tests the payload, and uploads it as the `openhound-windows-x64` Actions artifact. This is a downloadable ZIP of files supplied by Actions, not a setup executable or MSI.
- [`src/openhound/scheduler/startup.py`](../src/openhound/scheduler/startup.py) provides the foreground scheduler CLI. The existing Docker Enterprise launcher delegates to the packaged scheduler. `--instance NAME` and `--instance-dir PATH` choose writable instance data; `--stop-file ABSOLUTE_PATH` allows a noninteractive process to request graceful shutdown by creating the file. Ctrl+C and Ctrl+Break remain available in a console.
- The default Windows instance is `%ProgramData%\SpecterOps\OpenHound\instances\default`. Configuration and secrets are in `.dlt/`; logs, pipeline state, collection output, and temporary files are inside the instance. Keep this data outside the installed application directory. The service account will need read/write access to the instance and restricted access to secrets.
- [`docs/windows-runtime.md`](windows-runtime.md) has exact build and foreground launch commands, sample BHE/GitHub configuration, troubleshooting, and live-test instructions.

## Validation already done

The user reported that the Windows runtime GitHub Action passed after its idle shutdown smoke test was changed to use `--stop-file`. That workflow tests a path containing spaces, an unrelated working directory, absence of Python tools on `PATH`, GitHub collector entry-point discovery, actionable missing-configuration errors, a spawned worker using a CI-only offline collector, repeated launches, clean idle shutdown, and no runtime writes to the application directory. The offline collector is installed only into a copy of the payload for the test; it is not in the uploaded release payload.

The passing run was reported by the user; this note does not include a run URL or independent inspection of its logs. Live BHE/GitHub collection with credentials has not been verified. A clean-machine audit of native wheel DLL dependencies remains open; [`docs/windows-runtime.md`](windows-runtime.md) currently identifies the Microsoft Visual C++ 2015–2022 Redistributable (x64) as a customer prerequisite until that audit is complete. Interactive Ctrl+C/Ctrl+Break delivery was not tested by hosted CI.

## Installer considerations

1. Use the existing builder as the input to installer packaging. Keep `uv`, build Python, and package downloads in CI only; the installed runtime must stay offline and private.
2. Choose a stable installation path and keep `%ProgramData%\SpecterOps\OpenHound\instances\<name>` separate. An upgrade must not overwrite configuration, secrets, state, output, or logs. Define how versions and upgrades are identified; `runtime-info.json` is available for provenance.
3. The runtime is foreground only today. There is no WinSW integration, service registration, setup wizard, automatic updater, or installer project. If this stage adds a service, define its account, working directory, argument list, stop request file, shutdown timeout, restart behavior, and upgrade ordering. An active worker is allowed to finish during graceful shutdown.
4. Preserve the packaged interpreter, `.dist-info`, collector resources, and native DLLs exactly as built. Test the **installed** application from a path with spaces and an unrelated working directory, with Python and `uv` absent from `PATH`. Test uninstall and in-place upgrade while confirming instance data persists.
5. Keep credentials out of the installer and Actions artifacts. Document how customers provide BHE URL/API credentials and GitHub collector credentials after installation.

The next chat should inspect the repository and current GitHub Actions conventions, implement the installer build and upload workflow, and report the actual artifact name, installation command, validation performed, and any Windows-only checks that cannot run locally.
