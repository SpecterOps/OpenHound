"""Collection inventories are optional metadata, never recovery inputs."""

import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from openhound.core.runs import SavedRun
from openhound.core.upload_package import package_saved_run
from openhound.scheduler import dataflow
from tests.test_saved_runs import _create, _extension


def test_inventory_records_sorted_relative_paths_and_sizes_without_reading_data(
    monkeypatch, tmp_path
):
    run = _create(tmp_path / "runs")
    collected = {
        "raw/example/users/nested/part-2.jsonl.gz": b"collected data",
        "raw/example/groups/part-1.jsonl.gz": b"",
    }
    for name, content in collected.items():
        path = run.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (run.raw / "notes.json").write_text("not a collected resource")
    (run.directory / "lookup.duckdb").write_bytes(b"not part of the inventory")

    def unexpected_read(*args, **kwargs):
        raise AssertionError("Inventory creation must not read file contents")

    with run.locked(), monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", unexpected_read)
        run.write_collection_inventory()

    inventory_path = run.directory / "collection-inventory.json"
    assert json.loads(inventory_path.read_text()) == {
        "schema_version": 1,
        "run_id": run.manifest.run_id,
        "files": [
            {"path": name, "size_bytes": len(collected[name])}
            for name in sorted(collected)
        ],
    }
    if os.name != "nt":
        assert inventory_path.stat().st_mode & 0o777 == 0o600
    assert not list(run.directory.glob(".collection-inventory-*.tmp"))


def test_empty_collection_has_an_empty_inventory(tmp_path):
    run = _create(tmp_path)
    with run.locked():
        run.write_collection_inventory()
    inventory = json.loads((run.directory / "collection-inventory.json").read_text())
    assert inventory["files"] == []


@pytest.mark.parametrize("failure", ["flush", "publication"])
def test_inventory_write_failure_preserves_previous_file_and_cleans_up(
    monkeypatch, tmp_path, failure
):
    run = _create(tmp_path)
    with run.locked():
        run.write_collection_inventory()
    inventory_path = run.directory / "collection-inventory.json"
    before = inventory_path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("simulated inventory write failure")

    if failure == "flush":
        monkeypatch.setattr("openhound.core.runs.os.fsync", fail)
    else:
        monkeypatch.setattr(Path, "replace", fail)
    with run.locked(), pytest.raises(OSError, match="simulated inventory"):
        run.write_collection_inventory()
    assert inventory_path.read_bytes() == before
    assert not list(run.directory.glob(".collection-inventory-*.tmp"))


def test_inventory_is_recorded_before_preprocessing_and_survives_later_failure(
    tmp_path,
):
    run = _create(tmp_path)
    with pytest.raises(RuntimeError, match="preprocessing interrupted"):
        dataflow.pipeline(_extension([], "preprocess"), run.graph, saved_run=run)

    inventory = json.loads((run.directory / "collection-inventory.json").read_text())
    assert inventory["files"]
    assert {entry["path"] for entry in inventory["files"]} == {
        path.relative_to(run.directory).as_posix()
        for path in run.raw.rglob("*.jsonl.gz")
        if path.is_file()
    }
    assert run.manifest.results.keys() == {"collect"}
    assert run.manifest.phase == "preprocess"


def test_failed_collection_does_not_create_inventory(tmp_path):
    run = _create(tmp_path)
    with pytest.raises(RuntimeError, match="collection interrupted"):
        dataflow.pipeline(_extension([], "collect"), run.graph, saved_run=run)
    assert not (run.directory / "collection-inventory.json").exists()


def test_inventory_creation_failure_does_not_fail_the_pipeline(
    monkeypatch, tmp_path, caplog
):
    run = _create(tmp_path)

    def fail(self):
        raise OSError("simulated inventory error")

    monkeypatch.setattr(SavedRun, "write_collection_inventory", fail)
    calls = []
    dataflow.pipeline(_extension(calls), run.graph, saved_run=run)
    assert calls == ["collect", "preprocess", "convert", "graph_extract"]
    assert run.manifest.status == "complete"
    assert "continuing without it" in caplog.text
    assert not (run.directory / "collection-inventory.json").exists()


@pytest.mark.parametrize("inventory_state", ["missing", "invalid"])
def test_recovery_and_packaging_do_not_depend_on_inventory(tmp_path, inventory_state):
    run = _create(tmp_path)
    with pytest.raises(RuntimeError, match="conversion interrupted"):
        dataflow.pipeline(_extension([], "convert"), run.graph, saved_run=run)
    inventory = run.directory / "collection-inventory.json"
    assert inventory.is_file()
    if inventory_state == "missing":
        inventory.unlink()
    else:
        inventory.write_text("not valid JSON")

    calls = []
    dataflow.resume_saved_run(_extension(calls), run)
    archive, _ = package_saved_run(run, tmp_path / "recovered.zip")
    assert calls == ["convert", "graph_extract"]
    with ZipFile(archive) as bundle:
        assert bundle.namelist()
        assert "collection-inventory.json" not in bundle.namelist()
    if inventory_state == "missing":
        assert not inventory.exists()
    else:
        assert inventory.read_text() == "not valid JSON"
