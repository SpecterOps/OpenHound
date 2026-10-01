"""CI-only collector: exercise scheduler paths without network credentials."""

import os
from pathlib import Path
from types import SimpleNamespace

from openhound.core.app import OpenHound

app = OpenHound("offline")


def collect(*, output_path: Path, **kwargs):
    import dlt

    root = Path(os.environ["OPENHOUND_INSTANCE_DIR"])
    assert output_path == root / "output"
    assert Path(os.environ["DLT_DATA_DIR"]) == root / "state"
    assert Path(os.environ["DLT_PROJECT_DIR"]) == root
    assert Path(os.environ["TMP"]) == root / "temp"
    state_pipeline = dlt.pipeline(pipeline_name="offline_runtime_state")
    assert Path(state_pipeline.pipelines_dir).is_relative_to(root / "state")
    folder = output_path / "offline"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "worker-ok.txt").write_text("spawned worker used instance paths")
    return SimpleNamespace(load_packages=[])


def convert(*, input_path: Path, lookup_file: Path, **kwargs):
    root = Path(os.environ["OPENHOUND_INSTANCE_DIR"])
    assert input_path == root / "output" / "offline"
    assert lookup_file == root / "state" / "lookup.duckdb"
    assert (input_path / "worker-ok.txt").is_file()
    return SimpleNamespace(load_packages=[])


app.collector = collect
app.converter = convert
