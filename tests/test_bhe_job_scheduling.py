import gzip
import json
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from openhound.core.clients.bloodhound_enterprise import JobStatus
from openhound.core.models.graph import Graph
from openhound.scheduler import service as scheduler_service
from openhound.scheduler.service import (
    ExtensionNotFoundError,
    Result,
    Service,
    _subprocess_collect,
)

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "api" / "jobs"


def load_json(filename: str) -> dict:
    with open(TEST_DATA_DIR / filename, "r") as f:
        return json.load(f)


@pytest.fixture
def mock_bloodhound_api():
    """Mimic the BloodHound API to fully test the requests made by the client.

    Returns:
        TestClient: A TestClient instance for the mocked BloodHound API.
    """
    app = FastAPI()

    app.state.job_started = False
    app.state.job_ended = False
    app.state.end_payload = None
    app.state.start_payload = None
    app.state.ingested_edges = 0
    app.state.ingested_nodes = 0

    @app.get("/api/v2/jobs/available")
    async def jobs_available():
        if not app.state.job_started:
            return load_json("jobs_available_with_job.json")
        return load_json("jobs_available_empty.json")

    @app.get("/api/v2/jobs/current")
    async def jobs_current():
        return Response(status_code=404)

    @app.post("/api/v2/jobs/start")
    async def start_job(body: dict):
        app.state.job_started = True
        app.state.start_payload = body
        return load_json("job_start.json")

    @app.post("/api/v2/jobs/end")
    async def end_job(body: dict):
        app.state.job_ended = True
        app.state.end_payload = body
        return load_json("job_end.json")

    @app.post("/api/v2/ingest")
    async def ingest(request: Request):
        body = await request.body()
        decompressed = gzip.decompress(body)
        validate_graph = Graph.model_validate_json(decompressed)
        app.state.ingested_nodes += len(validate_graph.graph.nodes)
        app.state.ingested_edges += len(validate_graph.graph.edges)
        return {"status": "success"}

    return TestClient(app)


@pytest.fixture
def mock_service(mock_bloodhound_api, monkeypatch):
    """Patches requests.requests so that our mocked BloodHound API will be used for testing the service.

    Args:
        mock_bloodhound_api (TestClient): A TestClient instance for the mocked BloodHound API.
        monkeypatch (pytest.MonkeyPatch): A pytest fixture for monkeypatching.
    """

    class DummyExecutor:
        def __init__(self, *args, **kwargs):
            self.submitted = []

        def submit(self, *args, **kwargs):
            future = Future()
            self.submitted.append((args, kwargs, future))
            return future

        def shutdown(self, *args, **kwargs):
            return None

    def mock_request(method, url, **kwargs):
        path = urlsplit(url).path
        if method.upper() == "GET":
            return mock_bloodhound_api.get(path)
        if method.upper() == "POST":
            return mock_bloodhound_api.post(path, **kwargs)

        raise AssertionError(f"Unhandled method: {method}")

    monkeypatch.setattr("requests.request", mock_request)
    monkeypatch.setattr(scheduler_service, "ProcessPoolExecutor", DummyExecutor)

    return Service(
        bhe_uri="http://localhost:8000",
        token_key="test-key",
        token_id="test-id",
        collector_name="openhound-faker",
    )


def test_jobs_starts_new_job(mock_service, mock_bloodhound_api):
    """Runs _check_jobs and checks if the new job is started when available."""

    job = mock_service.check_jobs()
    assert job is not None
    assert job.id == 123
    assert mock_bloodhound_api.app.state.job_started is False


def test_jobs_no_jobs_available(mock_service, mock_bloodhound_api):
    """Test that _check_jobs returns no jobs available."""

    mock_bloodhound_api.app.state.job_started = True
    assert mock_service.check_jobs() is None


def test_poll_starts_new_job(mock_service, mock_bloodhound_api, monkeypatch):
    """Similar to test_jobs_starts_new_job but using the poll method"""
    submitted = Future()
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: submitted)

    mock_service._poll()

    assert mock_service.job_running == 123
    assert mock_service.future is submitted
    assert mock_bloodhound_api.app.state.job_started is True
    assert mock_bloodhound_api.app.state.start_payload == {"id": 123}


def test_job_already_running(mock_service, monkeypatch):
    """Test that a new process is not started if a job is already running."""

    mock_service.job_running = 420
    mock_service.future = Future()

    def fail_submit(*args, **kwargs):
        raise AssertionError("submit should not be called")

    monkeypatch.setattr(mock_service.executor, "submit", fail_submit)

    mock_service._poll()

    assert mock_service.job_running == 420


def test_poll_handles_completed_job(mock_service, mock_bloodhound_api):
    """Run the _poll method and check if the job completed succesfully."""
    mock_bloodhound_api.app.state.job_started = True
    future = Future()
    future.set_result(Result(results={"collect": ["a"]}, job_id=123))
    mock_service.future = future
    mock_service.job_running = 123

    mock_service._poll()

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_bloodhound_api.app.state.job_ended is True
    assert mock_bloodhound_api.app.state.end_payload == {
        "status": JobStatus.COMPLETE.value,
        "message": "Collector 'openhound-faker' completed successfully",
    }


def test_poll_missing_extension(mock_service, mock_bloodhound_api):
    """Run the _poll method and check if the job fails by raising an ExtensionNotFoundError"""
    mock_bloodhound_api.app.state.job_started = True
    future = Future()
    future.set_exception(ExtensionNotFoundError("missing"))
    mock_service.future = future
    mock_service.job_running = 123

    mock_service._poll()

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_bloodhound_api.app.state.job_ended is True
    assert mock_bloodhound_api.app.state.end_payload == {
        "status": JobStatus.FAILED.value,
        "message": "Collector 'openhound-faker' not found",
    }


def test_poll_recovers_from_broken_process_pool(mock_service, mock_bloodhound_api):
    """A BrokenProcessPool surfaced via future.result() should fail the job, clear state, and rebuild the executor."""
    mock_bloodhound_api.app.state.job_started = True
    future = Future()
    future.set_exception(BrokenProcessPool("worker died"))
    mock_service.future = future
    mock_service.job_running = 123
    original_executor = mock_service.executor

    mock_service._poll()

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.executor is not original_executor
    assert mock_bloodhound_api.app.state.job_ended is True
    assert mock_bloodhound_api.app.state.end_payload == {
        "status": JobStatus.FAILED.value,
        "message": "Collection worker for 'openhound-faker' was terminated abruptly",
    }


def test_start_job_recovers_when_submit_raises_broken_pool(
    mock_service, mock_bloodhound_api, monkeypatch
):
    """If executor.submit raises BrokenProcessPool after the BHE job was started, the job should be ended FAILED, state cleared, and the executor rebuilt."""

    def broken_submit(*args, **kwargs):
        raise BrokenProcessPool("worker died before submit")

    monkeypatch.setattr(mock_service.executor, "submit", broken_submit)
    original_executor = mock_service.executor

    mock_service._poll()

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.executor is not original_executor
    assert mock_bloodhound_api.app.state.job_started is True
    assert mock_bloodhound_api.app.state.job_ended is True
    assert mock_bloodhound_api.app.state.end_payload == {
        "status": JobStatus.FAILED.value,
        "message": "Failed to start collector 'openhound-faker': worker pool was broken",
    }


def test_checkin_calls_jobs_current_when_job_running(mock_service, monkeypatch):
    """_poll() should call jobs_current via the else-branch check-in when a job is running."""
    # Simulate a job in progress with no completed future — skips the completion handler,
    # reaches the else-branch, and triggers jobs_current as a check-in heartbeat.
    mock_service.job_running = 123
    mock_service.future = None  # no completed future to handle
    called = []

    def fake_jobs_current(self):
        called.append(True)

    monkeypatch.setattr(
        mock_service.client.__class__, "jobs_current", property(fake_jobs_current)
    )

    mock_service._poll()

    assert len(called) == 1


def test_checkin_noop_when_no_job_running(mock_service, monkeypatch):
    """_poll() should not call jobs_current via the else-branch check-in when no job is running."""
    # When idle (job_running is None), _poll() takes the if-branch and calls check_jobs()
    # instead of the else-branch check-in. jobs_current should never be touched.
    assert mock_service.job_running is None
    mock_service.future = None
    called = []

    def fake_jobs_current(self):
        called.append(True)

    monkeypatch.setattr(
        mock_service.client.__class__, "jobs_current", property(fake_jobs_current)
    )
    # Stub check_jobs so _poll doesn't try to start a job; we only care the else-branch doesn't fire
    monkeypatch.setattr(mock_service, "check_jobs", lambda: None)

    mock_service._poll()

    assert len(called) == 0


def test_checkin_swallows_exception(mock_service, monkeypatch):
    """_poll() should swallow exceptions raised by jobs_current in the check-in else-branch."""
    # A transient BHE error during check-in must not crash the service loop.
    mock_service.job_running = 123
    mock_service.future = None  # no completed future to handle

    def raise_error(self):
        raise RuntimeError("BHE unreachable")

    monkeypatch.setattr(
        mock_service.client.__class__, "jobs_current", property(raise_error)
    )

    # Should not raise — _poll's except block absorbs the error
    mock_service._poll()


def test_scheduler_ingest_opengraph(mock_service, mock_bloodhound_api, monkeypatch):
    """Run the DLT pipeline with the openhound-faker collector + check the amount of ingested nodes + edges"""
    monkeypatch.setenv(
        "DESTINATION__BLOODHOUNDENTERPRISE__URL", "http://localhost:8000"
    )
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_KEY", "test-key")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_ID", "test-id")

    result = _subprocess_collect("faker", 123)

    assert result.job_id == 123
    assert mock_bloodhound_api.app.state.ingested_nodes == 1000
    assert mock_bloodhound_api.app.state.ingested_edges == 10000
