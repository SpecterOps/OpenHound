import importlib
import importlib.util
from pathlib import Path


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
