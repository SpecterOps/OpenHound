import importlib
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def disable_saved_runs(monkeypatch):
    """Require tests to opt into saved storage instead of reading local settings."""
    monkeypatch.setenv("OPENHOUND__RUNS_DIRECTORY", "")


def import_from_path(file_path: Path) -> None:
    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore
    spec.loader.exec_module(module)  # type: ignore


def pytest_configure(config):
    repo_root = Path(__file__).resolve().parents[1]
    sources_root = repo_root / "openhound" / "sources"
    py_files = [path for path in sources_root.rglob("**/models/*.py") if path.is_file()]
    for module in py_files:
        import_from_path(module)
