"""Exercise bounded conversion recovery and DLT's retained package behavior."""

import gzip
import logging
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import Mock

import dlt
import pytest
from dlt.common import json
from dlt.common.configuration.container import Container
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.normalize.normalize import Normalize
from dlt.pipeline.exceptions import PipelineStepFailed
from dlt.pipeline.pipeline import Pipeline

from openhound.core.convert import Converter
from openhound.core.pipeline import BasePipeline
from openhound.core.recovery import can_retry_normalization, resume_pending_pipeline


def _pending_pipeline() -> Mock:
    """Represent a pipeline with one retained extraction package."""
    pipeline = Mock(spec=Pipeline)
    pipeline.pipeline_name = "test_convert"
    pipeline.working_dir = "/saved/test_convert"
    pipeline.has_pending_data = True
    pipeline.list_extracted_load_packages.return_value = ["package-1"]
    pipeline.list_normalized_load_packages.return_value = []
    return pipeline


def _failure(pipeline, *, step="normalize", cause=None) -> PipelineStepFailed:
    """Wrap a worker failure using the same exception type as DLT."""
    return PipelineStepFailed(
        pipeline,
        step,
        "package-1",
        cause if cause is not None else BrokenProcessPool("worker terminated"),
    )


def _converter(monkeypatch, tmp_path, pipeline) -> Converter:
    """Use the real converter recovery policy with a selected DLT pipeline."""
    monkeypatch.setattr(Converter, "pipeline", property(lambda self: pipeline))
    return Converter("test", tmp_path, None, tmp_path)


@pytest.mark.parametrize("step", ["extract", "load"])
def test_worker_failure_in_other_steps_is_not_recoverable(step):
    assert not can_retry_normalization(_failure(_pending_pipeline(), step=step))


def test_other_normalization_errors_are_not_recoverable():
    assert not can_retry_normalization(
        _failure(_pending_pipeline(), cause=ValueError("invalid record"))
    )


def test_missing_failed_package_is_not_recoverable():
    pipeline = _pending_pipeline()
    pipeline.list_extracted_load_packages.return_value = ["another-package"]
    assert not can_retry_normalization(_failure(pipeline))


def test_converter_resumes_without_running_source_again(monkeypatch, tmp_path, caplog):
    pipeline = _pending_pipeline()
    pipeline.run.side_effect = _failure(pipeline)
    source = object()
    with caplog.at_level(logging.INFO):
        result = _converter(monkeypatch, tmp_path, pipeline)._run(source)

    assert result is pipeline.load.return_value
    pipeline.run.assert_called_once_with(source)
    pipeline.normalize.assert_called_once_with(workers=1)
    pipeline.load.assert_called_once_with()
    pipeline.load.return_value.raise_on_failed_jobs.assert_called_once_with()
    assert "normalize_workers=1" in caplog.text
    assert "extracted_packages=['package-1']" in caplog.text
    assert "Pipeline recovery completed" in caplog.text


@pytest.mark.parametrize("failed_step", ["normalize", "load"])
def test_failed_fallback_propagates_without_second_attempt(
    monkeypatch, tmp_path, caplog, failed_step
):
    pipeline = _pending_pipeline()
    pipeline.run.side_effect = _failure(pipeline)
    recovery_error = _failure(pipeline, step=failed_step)
    getattr(pipeline, failed_step).side_effect = recovery_error

    with pytest.raises(PipelineStepFailed) as raised:
        _converter(monkeypatch, tmp_path, pipeline)._run(object())

    assert raised.value is recovery_error
    assert pipeline.run.call_count == 1
    assert pipeline.normalize.call_count == 1
    assert pipeline.load.call_count == (failed_step == "load")
    pipeline.drop_pending_packages.assert_not_called()
    assert "Preserve this directory" in caplog.text


def test_fallback_reports_terminal_load_jobs_as_failure(monkeypatch, tmp_path):
    pipeline = _pending_pipeline()
    pipeline.run.side_effect = _failure(pipeline)
    pipeline.load.return_value.raise_on_failed_jobs.side_effect = RuntimeError(
        "load job failed"
    )
    with pytest.raises(RuntimeError, match="load job failed"):
        _converter(monkeypatch, tmp_path, pipeline)._run(object())


def test_other_pipeline_types_do_not_enable_fallback():
    pipeline = _pending_pipeline()
    failure = _failure(pipeline)
    pipeline.run.side_effect = failure

    class UnchangedPipeline(BasePipeline):
        @property
        def pipeline(self):
            return pipeline

    with pytest.raises(PipelineStepFailed) as raised:
        UnchangedPipeline()._run(object())
    assert raised.value is failure
    pipeline.run.assert_called_once()
    pipeline.normalize.assert_not_called()


@pytest.mark.parametrize(
    ("step", "cause", "packages"),
    [
        ("extract", BrokenProcessPool(), ["package-1"]),
        ("load", BrokenProcessPool(), ["package-1"]),
        ("normalize", ValueError("bad data"), ["package-1"]),
        ("normalize", BrokenProcessPool(), []),
    ],
)
def test_converter_does_not_retry_ineligible_failures(
    monkeypatch, tmp_path, step, cause, packages
):
    pipeline = _pending_pipeline()
    pipeline.list_extracted_load_packages.return_value = packages
    failure = _failure(pipeline, step=step, cause=cause)
    pipeline.run.side_effect = failure

    with pytest.raises(PipelineStepFailed) as raised:
        _converter(monkeypatch, tmp_path, pipeline)._run(object())
    assert raised.value is failure
    pipeline.run.assert_called_once()
    pipeline.normalize.assert_not_called()
    pipeline.load.assert_not_called()


def test_resume_normalized_packages_skips_normalization():
    pipeline = _pending_pipeline()
    pipeline.list_extracted_load_packages.return_value = []
    pipeline.list_normalized_load_packages.return_value = ["package-1"]

    assert resume_pending_pipeline(pipeline) is pipeline.load.return_value
    pipeline.normalize.assert_not_called()
    pipeline.run.assert_not_called()


def test_resume_refuses_empty_pipeline():
    pipeline = _pending_pipeline()
    pipeline.list_extracted_load_packages.return_value = []
    with pytest.raises(ValueError, match="no pending packages"):
        resume_pending_pipeline(pipeline)
    pipeline.normalize.assert_not_called()
    pipeline.load.assert_not_called()


def test_resume_refuses_invalid_worker_count():
    pipeline = _pending_pipeline()
    with pytest.raises(ValueError, match="at least one"):
        resume_pending_pipeline(pipeline, normalize_workers=0)
    pipeline.normalize.assert_not_called()
    pipeline.load.assert_not_called()


@pytest.mark.parametrize("fallback_fails", [False, True])
def test_real_dlt_retained_package_recovery(monkeypatch, tmp_path, fallback_fails):
    """Force the parallel normalizer to fail, then process its real saved files."""
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
    monkeypatch.setenv("NORMALIZE__WORKERS", "3")
    monkeypatch.setenv("SAVED_CONVERT__NORMALIZE__WORKERS", "3")
    pipeline = dlt.pipeline(
        pipeline_name="saved_convert",
        pipelines_dir=str(tmp_path / "pipelines"),
        destination=dlt.destinations.filesystem(str(tmp_path / "output")),
        dataset_name="saved",
    )
    source_calls = []
    workers = []
    original_single = Normalize.map_single

    @dlt.resource(name="nodes")
    def source():
        source_calls.append("extract")
        yield {"id": "node-1", "name": "Saved node"}

    def fail_parallel(self, schema, load_id, files):
        workers.append(self.config.workers)
        raise BrokenProcessPool("simulated worker termination")

    def single(self, schema, load_id, files):
        workers.append(self.config.workers)
        if fallback_fails:
            raise BrokenProcessPool("simulated fallback failure")
        return original_single(self, schema, load_id, files)

    monkeypatch.setattr(Normalize, "map_parallel", fail_parallel)
    monkeypatch.setattr(Normalize, "map_single", single)
    converter = _converter(monkeypatch, tmp_path, pipeline)
    providers = Container()[PluggableRunContext].providers
    original_providers = providers.providers

    if fallback_fails:
        with pytest.raises(PipelineStepFailed):
            converter._run(source(), loader_file_format="jsonl")
        assert pipeline.list_extracted_load_packages()
        assert not list((tmp_path / "output").rglob("*.jsonl.gz"))
    else:
        result = converter._run(source(), loader_file_format="jsonl")
        result.raise_on_failed_jobs()
        assert not pipeline.has_pending_data
        rows = []
        for path in (tmp_path / "output" / "saved" / "nodes").glob("*.jsonl.gz"):
            with gzip.open(path, "rt") as stream:
                rows.extend(json.loads(line) for line in stream)
        assert [(row["id"], row["name"]) for row in rows] == [("node-1", "Saved node")]

    assert source_calls == ["extract"]
    assert workers == [3, 1]
    assert providers.providers is original_providers
    assert dlt.config["normalize.workers"] == 3
