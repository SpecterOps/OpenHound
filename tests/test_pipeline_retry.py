import errno

import pytest
from dlt.pipeline.exceptions import PipelineStepFailed

from openhound.core.pipeline import _MAX_TRANSIENT_RETRIES, BasePipeline


class _DummyDltPipeline:
    pipeline_name = "test_pipeline"
    has_pending_data = True


def _wrap_in_step_failed(exc: BaseException) -> PipelineStepFailed:
    """Wrap an exception the way dlt does when a load/normalize step fails."""
    return PipelineStepFailed(
        pipeline=_DummyDltPipeline(),
        step="load",
        load_id="1782853056",
        exception=exc,
    )


class _FakeDlt:
    """Stand-in for a dlt Pipeline whose run() replays a list of side effects."""

    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.calls = 0

    def run(self, source, **kwargs):
        self.calls += 1
        effect = self._side_effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


class _FakePipeline(BasePipeline):
    def __init__(self, fake_dlt: _FakeDlt):
        self._fake = fake_dlt

    @property
    def pipeline(self):
        return self._fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("openhound.core.pipeline.time.sleep", lambda *_: None)


def _winerror5() -> PermissionError:
    return PermissionError(errno.EACCES, "Access is denied")


def test_run_retries_transient_permission_error_wrapped():
    sentinel = object()
    fake = _FakeDlt(
        [
            _wrap_in_step_failed(_winerror5()),
            _wrap_in_step_failed(_winerror5()),
            sentinel,
        ]
    )
    pipeline = _FakePipeline(fake)

    assert pipeline._run(source=None) is sentinel
    assert fake.calls == 3


def test_run_retries_bare_permission_error(monkeypatch):
    monkeypatch.setattr("openhound.core.pipeline.sys.platform", "win32")
    sentinel = object()
    fake = _FakeDlt([_winerror5(), sentinel])
    pipeline = _FakePipeline(fake)

    assert pipeline._run(source=None) is sentinel
    assert fake.calls == 2


def test_run_does_not_retry_bare_permission_error_on_non_windows(monkeypatch):
    monkeypatch.setattr("openhound.core.pipeline.sys.platform", "linux")
    fake = _FakeDlt([_winerror5()])
    pipeline = _FakePipeline(fake)

    with pytest.raises(PermissionError):
        pipeline._run(source=None)

    assert fake.calls == 1


def test_run_retries_implicit_chained_permission_error():
    """PermissionError on __context__ (raised without 'from') should be detected and retried."""
    sentinel = object()
    # Simulate: some intermediate error raised inside `except PermissionError:` (no 'from'),
    # so __context__ is set to the PermissionError but __cause__ is not.
    perm_err = _winerror5()
    intermediate = RuntimeError("intermediate")
    intermediate.__context__ = perm_err
    # dlt wraps the intermediate error in PipelineStepFailed
    step_failed = _wrap_in_step_failed(intermediate)

    fake = _FakeDlt([step_failed, sentinel])
    pipeline = _FakePipeline(fake)

    assert pipeline._run(source=None) is sentinel
    assert fake.calls == 2


def test_run_reraises_after_max_retries():
    fake = _FakeDlt([_wrap_in_step_failed(_winerror5())] * _MAX_TRANSIENT_RETRIES)
    pipeline = _FakePipeline(fake)

    with pytest.raises(PipelineStepFailed):
        pipeline._run(source=None)

    assert fake.calls == _MAX_TRANSIENT_RETRIES


def test_run_does_not_retry_non_transient_permission_error():
    fake = _FakeDlt([PermissionError(errno.ENOTEMPTY, "Directory not empty")])
    pipeline = _FakePipeline(fake)

    with pytest.raises(PermissionError):
        pipeline._run(source=None)

    assert fake.calls == 1


def test_run_does_not_retry_other_pipeline_step_failures():
    fake = _FakeDlt([_wrap_in_step_failed(RuntimeError("boom"))])
    pipeline = _FakePipeline(fake)

    with pytest.raises(PipelineStepFailed):
        pipeline._run(source=None)

    assert fake.calls == 1
