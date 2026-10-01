"""Writable paths for one scheduler instance. Keep this module stdlib-only."""

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

INSTANCE_ENV = "OPENHOUND_INSTANCE_DIR"


@dataclass(frozen=True)
class InstancePaths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / ".dlt"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def temp(self) -> Path:
        return self.root / "temp"


def resolve_instance(
    name: str = "default", directory: str | Path | None = None
) -> InstancePaths | None:
    """Use an override, the Windows ProgramData default, or legacy Linux paths."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in {".", ".."}:
        raise ValueError(
            "Instance name must contain only letters, digits, '.', '_' or '-'."
        )
    root = directory or os.environ.get(INSTANCE_ENV)
    if root is None and sys.platform == "win32":
        root = (
            Path(os.environ.get("ProgramData", r"C:\ProgramData"))
            / "SpecterOps"
            / "OpenHound"
            / "instances"
            / name
        )
    if root is None:
        return None
    path = Path(root).expanduser().resolve()
    return InstancePaths(path)


def configure_instance(paths: InstancePaths) -> None:
    """Set inherited environment before importing dlt or starting a worker."""
    for path in (
        paths.root,
        paths.config,
        paths.logs,
        paths.state,
        paths.output,
        paths.temp,
    ):
        path.mkdir(parents=True, exist_ok=True)
    os.environ[INSTANCE_ENV] = str(paths.root)
    os.environ["DLT_PROJECT_DIR"] = str(paths.root)
    os.environ["DLT_DATA_DIR"] = str(paths.state)
    os.environ["DLT_LOCAL_DIR"] = str(paths.temp)
    os.environ["RUNTIME__LOG_PATH"] = str(paths.logs)
    os.environ["TMP"] = str(paths.temp)
    os.environ["TEMP"] = str(paths.temp)
    os.environ["TMPDIR"] = str(paths.temp)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
