"""Saved run isolation, checkpoints, and recovery without API recollection."""

import json
import os
import subprocess
import sys
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace

import dlt
import pytest
from dlt.common.configuration.container import Container
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.normalize.normalize import Normalize
from dlt.pipeline.exceptions import PipelineStepFailed
from filelock import FileLock, Timeout
from typer.testing import CliRunner

import openhound.cli.runs as runs_cli
from openhound.cli.runs import runs
from openhound.core.runs import SavedRun
from openhound.destinations.opengraph.destination import opengraph_file
from openhound.scheduler import dataflow, service


def _create(root: Path) -> SavedRun:
    """Create a run matching the local synthetic extension."""
    return SavedRun.create(
        root, collector="example", collector_version="1", job_id=42, mode="write"
    )


def _extension(calls: list, fail_at: str | None = None):
    """Use real DLT storage with synthetic local resources and graph records."""

    def collect(output_path, **kwargs):
        calls.append("collect")
        if fail_at == "collect":
            raise RuntimeError("collection interrupted")
        pipeline = dlt.pipeline(
            pipeline_name="example_collect",
            destination=dlt.destinations.filesystem(str(output_path)),
            dataset_name="example",
        )
        return pipeline.run(
            [{"id": "saved-node"}], table_name="nodes", loader_file_format="jsonl"
        )

    def preprocess(input_path, output_file, **kwargs):
        calls.append("preprocess")
        if fail_at == "preprocess":
            raise RuntimeError("preprocessing interrupted")
        pipeline = dlt.pipeline(
            pipeline_name="example_preproc",
            destination=dlt.destinations.duckdb(str(output_file)),
            dataset_name="example",
        )
        return pipeline.run([{"id": "saved-node"}], table_name="nodes")

    def convert(input_path, output_path, lookup_file, method, **kwargs):
        calls.append("convert")
        assert input_path.is_dir()
        assert lookup_file.is_file()
        assert method is dataflow.Method.write
        if fail_at == "convert":
            raise RuntimeError("conversion interrupted")

        @dlt.resource(name="nodes", max_table_nesting=0)
        def graph():
            calls.append("graph_extract")
            yield {
                "graph": {
                    "entity_type": "node",
                    "content": {
                        "id": "saved-node",
                        "kinds": ["Test"],
                        "properties": {},
                    },
                }
            }

        pipeline = dlt.pipeline(
            pipeline_name="example_convert",
            destination=opengraph_file(output_path=str(output_path)),
            dataset_name="example",
        )
        output_path.mkdir(parents=True, exist_ok=True)
        return pipeline.run(graph())

    return SimpleNamespace(
        name="example",
        package_version="1",
        collector=collect,
        preprocessor=preprocess,
        converter=convert,
    )


def test_workspace_paths_override_global_and_scoped_configuration(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PIPELINES_DIR", str(tmp_path / "wrong"))
    monkeypatch.setenv("EXAMPLE_COLLECT__PIPELINES_DIR", str(tmp_path / "also-wrong"))
    run = _create(tmp_path)
    providers = Container()[PluggableRunContext].providers
    previous = providers.providers
    calls = []
    dataflow.pipeline(_extension(calls), run.graph, saved_run=run)

    assert calls == ["collect", "preprocess", "convert", "graph_extract"]
    assert (run.directory / "pipelines" / "example_collect" / "state.json").is_file()
    assert (run.directory / "pipelines" / "example_preproc" / "state.json").is_file()
    assert not (tmp_path / "wrong").exists()
    assert not (tmp_path / "also-wrong").exists()
    assert providers.providers is previous
    assert SavedRun(run.directory).manifest.status == "complete"
    assert (run.directory.stat().st_mode & 0o777) == 0o700


def test_runs_for_the_same_job_are_isolated(tmp_path):
    first, second = _create(tmp_path), _create(tmp_path)
    assert first.directory != second.directory
    assert first.raw != second.raw


def test_lock_refuses_a_second_owner(tmp_path):
    run = _create(tmp_path)
    with FileLock(run.directory / ".lock", timeout=0), pytest.raises(Timeout):
        run.inspect()


@pytest.mark.parametrize("phase", ["collect", "preprocess", "convert"])
def test_failures_preserve_checkpoints_and_do_not_save_exception_text(tmp_path, phase):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, phase), run.graph, saved_run=run)
    manifest = SavedRun(run.directory).manifest
    assert manifest.status == "failed"
    assert manifest.phase == phase
    assert manifest.error_type == "RuntimeError"
    assert phase not in manifest.results
    assert "interrupted" not in (run.directory / "run.json").read_text()
    if phase != "collect":
        assert "collect" in manifest.results
        assert (run.raw / "example" / "nodes").is_dir()


def test_recovery_refuses_incomplete_collection(tmp_path):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(ValueError, match="Collection did not complete"):
        dataflow.resume_saved_run(_extension(calls), run)
    assert calls == []


@pytest.mark.parametrize("phase", ["preprocess", "convert"])
def test_recovery_uses_saved_collection_and_returns_local_graph(tmp_path, phase):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, phase), run.graph, saved_run=run)
    original_state = (run.directory / "run.json").read_text()
    calls.clear()

    output = dataflow.resume_saved_run(_extension(calls), SavedRun(run.directory))
    assert "collect" not in calls
    assert ("preprocess" in calls) == (phase == "preprocess")
    documents = [json.loads(path.read_text()) for path in output.glob("*.json")]
    assert [
        node["id"] for document in documents for node in document["graph"]["nodes"]
    ] == ["saved-node"]
    assert SavedRun(run.directory).manifest.status == "failed"
    assert SavedRun(run.directory).manifest.recovery_status == "complete"
    assert (
        json.loads(original_state)["results"]
        == SavedRun(run.directory).manifest.results
    )

    calls.clear()
    assert (
        dataflow.resume_saved_run(_extension(calls), SavedRun(run.directory)) == output
    )
    assert calls == []


def test_failed_recovery_reuses_pending_packages_without_graph_extraction(
    monkeypatch, tmp_path
):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, "convert"), run.graph, saved_run=run)
    original_single = Normalize.map_single

    def fail(self, *args, **kwargs):
        raise BrokenProcessPool("worker terminated")

    monkeypatch.setattr(Normalize, "map_single", fail)
    with pytest.raises(PipelineStepFailed):
        dataflow.resume_saved_run(_extension(calls), run)
    assert SavedRun(run.directory).manifest.recovery_error_type == "PipelineStepFailed"
    assert SavedRun(run.directory).manifest.recovery_requested_workers == 1
    assert run.inspect()["packages"]["recovery/pipelines/example_convert"]["extracted"]
    calls.clear()
    monkeypatch.setattr(Normalize, "map_single", original_single)
    output = dataflow.resume_saved_run(_extension(calls), SavedRun(run.directory))
    assert list(output.glob("*.json"))
    assert calls == []


def test_cli_inspects_without_loading_collectors_and_refuses_locked_run(tmp_path):
    run = _create(tmp_path)
    runner = CliRunner()
    result = runner.invoke(runs, ["inspect", str(run.directory)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["job_id"] == 42
    with FileLock(run.directory / ".lock", timeout=0):
        result = runner.invoke(runs, ["inspect", str(run.directory)])
    assert result.exit_code != 0
    assert "in use" in result.output


def test_recovery_refuses_mismatched_versions_before_processing(tmp_path):
    run = _create(tmp_path)
    calls = []
    extension = _extension(calls)
    extension.package_version = "different"
    with pytest.raises(ValueError, match="same OpenHound"):
        dataflow.resume_saved_run(extension, run)
    assert calls == []


def test_scheduler_opt_in_persists_failed_run(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENHOUND__RUNS_DIRECTORY", str(tmp_path / "runs"))
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
    extension = _extension([], "convert")
    monkeypatch.setattr(
        service.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[extension]),
    )
    with pytest.raises(RuntimeError, match="conversion interrupted"):
        service._collect_in_worker("example", 42, managed=True, configured=False)
    directories = list((tmp_path / "runs").iterdir())
    assert len(directories) == 1
    run = SavedRun(directories[0])
    assert run.manifest.status == "failed"
    assert run.manifest.job_id == 42
    assert run.manifest.results.keys() == {"collect", "preprocess"}
    assert (run.raw / "example").is_dir()
    assert run.lookup.is_file()


def test_cli_resume_recovers_locally_without_collection(monkeypatch, tmp_path):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, "convert"), run.graph, saved_run=run)
    calls.clear()
    extension = _extension(calls)
    monkeypatch.setattr(
        runs_cli.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[extension]),
    )
    result = CliRunner().invoke(runs, ["resume", str(run.directory), "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert calls == ["convert", "graph_extract"]
    assert "No data was uploaded" in result.output


def test_recovery_after_workspace_is_moved(monkeypatch, tmp_path):
    run = _create(tmp_path / "original")
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, "convert"), run.graph, saved_run=run)
    original_single = Normalize.map_single

    def fail(self, *args, **kwargs):
        raise BrokenProcessPool("worker terminated")

    monkeypatch.setattr(Normalize, "map_single", fail)
    with pytest.raises(PipelineStepFailed):
        dataflow.resume_saved_run(_extension(calls), run)
    moved = tmp_path / "moved"
    run.directory.rename(moved)
    calls.clear()
    monkeypatch.setattr(Normalize, "map_single", original_single)
    output = dataflow.resume_saved_run(_extension(calls), SavedRun(moved))
    assert output == moved / "recovery" / "graph"
    assert list(output.glob("*.json"))
    assert calls == []


def test_resume_after_dlt_finishes_before_manifest_checkpoint(tmp_path):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, "convert"), run.graph, saved_run=run)
    output = dataflow.resume_saved_run(_extension(calls), run)
    before = {path.name: path.read_bytes() for path in output.glob("*.json")}
    with run.locked():
        run.manifest.recovery_status = "running"
        run.manifest.recovery_results.pop("convert")
        run.save()
    calls.clear()
    dataflow.resume_saved_run(_extension(calls), SavedRun(run.directory))
    assert calls == []
    assert {path.name: path.read_bytes() for path in output.glob("*.json")} == before


def test_recovery_overrides_scoped_worker_settings_temporarily(monkeypatch, tmp_path):
    run = _create(tmp_path)
    calls = []
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension(calls, "convert"), run.graph, saved_run=run)
    monkeypatch.setenv("NORMALIZE__WORKERS", "3")
    monkeypatch.setenv("EXAMPLE_CONVERT__NORMALIZE__WORKERS", "3")
    original_single = Normalize.map_single
    workers = []

    def single(self, *args, **kwargs):
        workers.append(self.config.workers)
        return original_single(self, *args, **kwargs)

    monkeypatch.setattr(Normalize, "map_single", single)
    providers = Container()[PluggableRunContext].providers
    before = providers.providers
    dataflow.resume_saved_run(_extension(calls), run, normalize_workers=1)
    assert workers == [1]
    assert providers.providers is before
    assert dlt.config["normalize.workers"] == 3


def test_pending_recovery_resumes_in_a_fresh_process(monkeypatch, tmp_path):
    run = _create(tmp_path)
    with pytest.raises(RuntimeError):
        dataflow.pipeline(_extension([], "convert"), run.graph, saved_run=run)

    def fail(self, *args, **kwargs):
        raise BrokenProcessPool("worker terminated")

    monkeypatch.setattr(Normalize, "map_single", fail)
    with pytest.raises(PipelineStepFailed):
        dataflow.resume_saved_run(_extension([]), run)
    script = """
import json
import sys
from pathlib import Path
from test_saved_runs import _extension
from openhound.core.runs import SavedRun
from openhound.scheduler.dataflow import resume_saved_run
calls = []
output = resume_saved_run(_extension(calls), SavedRun(Path(sys.argv[1])))
print(json.dumps({"calls": calls, "output": str(output)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(run.directory)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(Path(__file__).parent), str(Path(__file__).parents[1] / "src")]
            ),
            "DLT_DATA_DIR": str(tmp_path / "dlt"),
            "RUNTIME__LOG_PATH": str(tmp_path / "logs"),
            "RUNTIME__DLTHUB_TELEMETRY": "false",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    recovered = json.loads(result.stdout.splitlines()[-1])
    assert recovered["calls"] == []
    assert list(Path(recovered["output"]).glob("*.json"))
    assert SavedRun(run.directory).manifest.recovery_status == "complete"
