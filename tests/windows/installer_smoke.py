"""CI-only elevated Windows test of the real offline installer and uninstaller."""

import itertools
import json
import os
import subprocess
import sys
import time
import uuid
import winreg
from pathlib import Path

from offline_process import run_offline_process

SCRIPTS = Path(__file__).resolve().parent


def snapshot(directory: Path) -> dict:
    return {
        str(path.relative_to(directory)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }


def main():
    installer, upgrade, cancellation, payload, temp = map(Path, sys.argv[1:])
    root = temp / f"OpenHound Installer Smoke {uuid.uuid4().hex}"
    root.mkdir()
    application = root / "Application With Spaces"
    outside = root / "Unrelated Working Directory"
    outside.mkdir()
    instance = root / "Persistent Instance"
    named_instance = (
        Path(os.environ["ProgramData"])
        / "SpecterOps/OpenHound/instances"
        / f"installer-smoke-{uuid.uuid4().hex}"
    )
    persisted = {}
    for instance_root in (instance, named_instance):
        for filename in (
            ".dlt/config.toml",
            ".dlt/secrets.toml",
            "state/sentinel",
            "output/sentinel",
            "logs/sentinel",
        ):
            path = instance_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installer must preserve this file\n")
            persisted[path] = path.read_bytes()

    catalog = json.loads((payload / "extensions.json").read_text())
    ids = [extension["id"] for extension in catalog]
    python = application / "python/python.exe"
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]]
    )
    log_number = itertools.count()

    def run(arguments, *, check=True, timeout=180, offline=False):
        arguments = list(map(str, arguments))
        if offline:
            result = run_offline_process(
                arguments, cwd=outside, env=environment, timeout=timeout
            )
        else:
            result = subprocess.run(
                arguments,
                cwd=outside,
                env=environment,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        if check and result.returncode:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {arguments}\n"
                f"{result.stdout}\n{result.stderr}"
            )
        return result

    def install(executable, selected=None, *, check=True):
        arguments = [
            executable,
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            f"/DIR={application}",
            f"/LOG={root / f'install-{next(log_number)}.log'}",
        ]
        if selected is not None:
            components = ["runtime"] + [f"extensions\\{name}" for name in selected]
            arguments += ["/TYPE=custom", "/COMPONENTS=" + ",".join(components)]
        return run(arguments, check=check, offline=True)

    def uninstall(*, check=True):
        return run(
            [
                application / "unins000.exe",
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/LOG={root / 'uninstall.log'}",
            ],
            check=check,
            offline=True,
        )

    def verify(selected):
        assert not (application / ".openhound-backup").exists()
        before = snapshot(application)
        run([python, "-I", "-B", "-m", "openhound.scheduler", "--help"])
        run(
            [
                python,
                "-I",
                "-B",
                SCRIPTS / "verify_runtime.py",
                application,
                ",".join(selected),
                root / "Validation Instance",
            ]
        )
        assert snapshot(application) == before, "Runtime modified application files"
        assert not list(outside.iterdir()), "Runtime wrote into the working directory"
        assert all(path.read_bytes() == content for path, content in persisted.items())

    # Successive installs exercise both adding and removing real components.
    for count in range(len(ids) + 1):
        for selected in itertools.combinations(ids, count):
            install(installer, selected)
            verify(selected)

    selected = ids[:1]
    install(installer, selected)
    install(installer)  # Omitted /COMPONENTS must restore previous selections.
    verify(selected)

    # The CI-only EXE cancels through the wizard during extraction. Inno's own
    # rollback must finish before our backup restores the previous components.
    before_cancel = {
        name: value
        for name, value in snapshot(application).items()
        if not name.startswith("unins")
    }
    result = install(cancellation, ids, check=False)
    assert (
        result.returncode == 5
    ), f"Expected extraction cancellation, got {result.returncode}"
    assert {
        name: value
        for name, value in snapshot(application).items()
        if not name.startswith("unins")
    } == before_cancel, "Cancellation did not restore the previous application files"
    verify(selected)

    obsolete = application / "python/Lib/site-packages/obsolete-upgrade-file.py"
    obsolete.write_text("obsolete = True\n")
    retired = application / "extensions/retired/site-packages/obsolete.py"
    retired.parent.mkdir(parents=True)
    retired.write_text("obsolete = True\n")
    install(upgrade)  # The second EXE has a higher installer version.
    verify(selected)
    assert not obsolete.exists() and not retired.exists()
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{44B99C67-348E-47B5-9650-CC28421A44B6}_is1",
        access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    ) as key:
        assert winreg.QueryValueEx(key, "DisplayVersion")[0] == "99.0.0"

    children = []
    try:
        # Two instances can coexist, and either one blocks installer maintenance.
        for number in range(2):
            idle = root / f"Idle Instance {number}"
            process = subprocess.Popen(
                [
                    str(python),
                    "-I",
                    "-B",
                    str(SCRIPTS / "offline_smoke.py"),
                    "--idle-child",
                    str(idle),
                ],
                cwd=outside,
                env=environment,
            )
            children.append(process)
            ready = idle / "temp/idle-ready"
            deadline = time.monotonic() + 30
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            assert ready.is_file(), "Idle scheduler failed to start"
        before = snapshot(application)
        assert install(upgrade, ids, check=False).returncode != 0
        assert uninstall(check=False).returncode != 0
        assert (
            snapshot(application) == before
        ), "Blocked maintenance changed application files"
        for number, process in enumerate(children):
            stop_file = root / f"Idle Instance {number}" / "temp/stop.request"
            stop_file.write_text("stop")
            assert process.wait(timeout=10) == 0
            assert not stop_file.exists(), "Scheduler did not consume the stop request"
            if number == 0:
                assert install(upgrade, ids, check=False).returncode != 0
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait()

    install(upgrade, ids)
    verify(ids)
    uninstall()
    assert not python.exists()
    assert not (application / "extensions").exists()
    assert all(path.read_bytes() == content for path, content in persisted.items())
    # Keep instance files and installer logs available for CI failure diagnosis.
    print("Offline selections, cancellation rollback, upgrade, mutex and uninstall: OK")


if __name__ == "__main__":
    main()
