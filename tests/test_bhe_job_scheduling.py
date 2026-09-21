import base64
import gzip
import hashlib
import json
import logging
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from openhound.core.clients import bloodhound, bloodhound_enterprise
from openhound.core.clients.bloodhound import BloodHoundHTTPError
from openhound.core.clients.bloodhound_enterprise import (
    JobStatus,
    ManagedJobOutcome,
)
from openhound.core.clients.models.jobs import (
    CollectorJob,
    CollectorJobsAvailable,
    ManagementOperation,
    ManagementOperationStatus,
    ManagementOperationType,
)
from openhound.core.models.graph import Graph
from openhound.scheduler import service as scheduler_service
from openhound.scheduler.service import (
    ExtensionNotFoundError,
    Result,
    Service,
    _subprocess_collect,
)

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "api" / "jobs"
MANAGEMENT_DATA_DIR = Path(__file__).parent / "test_data" / "api" / "management"


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
    app.state.collector_job_end_requests = []
    app.state.start_payload = None
    app.state.jobs_available_requests = 0
    app.state.jobs_current_requests = 0
    app.state.collector_job_queue_requests = []
    app.state.collector_job_queue_response = load_json(
        "collector_jobs_available_empty.json"
    )
    app.state.collector_job_queue_error_status = None
    app.state.client_update_payload = None
    app.state.ingested_edges = 0
    app.state.management_operations = []
    app.state.operation_started = False
    app.state.operation_ended = False
    app.state.operation_start_payload = None
    app.state.operation_end_payload = None
    app.state.operation_completed_by_artifact_upload = False
    app.state.bundle_content = None
    app.state.artifact_create_payload = None
    app.state.uploaded_parts = []
    app.state.artifact_completed = False
    app.state.ingested_nodes = 0

    @app.get("/api/v2/jobs/available")
    async def jobs_available():
        app.state.jobs_available_requests += 1
        if not app.state.job_started:
            return load_json("jobs_available_with_job.json")
        return load_json("jobs_available_empty.json")

    @app.get("/api/v2/jobs/current")
    async def jobs_current():
        app.state.jobs_current_requests += 1
        return Response(status_code=404)

    @app.get("/api/v2/collector-job-queue/available")
    async def collector_job_queue(request: Request):
        app.state.collector_job_queue_requests.append(dict(request.query_params))
        if app.state.collector_job_queue_error_status is not None:
            return Response(status_code=app.state.collector_job_queue_error_status)
        return app.state.collector_job_queue_response

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

    @app.post("/api/v2/collector-jobs/{job_id}/end")
    async def end_managed_job(job_id: str, body: dict):
        app.state.collector_job_end_requests.append(
            {"job_id": job_id, "body": body}
        )
        return Response(status_code=200)

    @app.post("/api/v2/ingest")
    async def ingest(request: Request):
        body = await request.body()
        decompressed = gzip.decompress(body)
        validate_graph = Graph.model_validate_json(decompressed)
        app.state.ingested_nodes += len(validate_graph.graph.nodes)
        app.state.ingested_edges += len(validate_graph.graph.edges)
        return {"status": "success"}

    @app.put("/api/v2/clients/update")
    async def update_client(body: dict):
        app.state.client_update_payload = body
        return {"status": "success"}

    @app.get("/api/v2/clients/management/available")
    async def management_available():
        return {"data": app.state.management_operations}

    @app.post("/api/v2/clients/management/start")
    async def start_operation(body: dict):
        app.state.operation_started = True
        app.state.operation_start_payload = body
        return {
            "data": {
                "id": body["operation_id"],
                "client_id": "client-123",
                "artifact_id": None,
                "type": "support_bundle",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "execution_time": "2026-01-01T00:00:00Z",
            }
        }

    @app.post("/api/v2/clients/management/artifacts")
    async def create_artifact_upload(body: dict):
        app.state.artifact_create_payload = body
        return {
            "data": {
                "artifact_id": "artifact-123",
                "client_id": "client-123",
                "storage_key": "client-123--openhound-faker_support_bundle_2026-01-01_00-00-00.zip",
                "status": "pending",
                "part_size": body["part_size"],
                "part_count": body["part_count"],
                "missing_parts": list(range(1, body["part_count"] + 1)),
                "management_operation": {
                    "id": body["operation_id"],
                    "client_id": "client-123",
                    "artifact_id": "artifact-123",
                    "type": "support_bundle",
                    "status": "running",
                    "requested_by_user_id": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": None,
                    "execution_time": "2026-01-01T00:00:00Z",
                },
            }
        }

    @app.post("/api/v2/clients/management/artifacts/{artifact_id}/parts/{part_number}")
    async def upload_artifact_part(
        artifact_id: str, part_number: int, request: Request
    ):
        content = await request.body()
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
        assert request.headers["content-digest"] == f"sha-256=:{checksum}:"
        app.state.uploaded_parts.append((artifact_id, part_number, content))
        return Response(status_code=200)

    @app.post("/api/v2/clients/management/artifacts/{artifact_id}/complete")
    async def complete_artifact_upload(artifact_id: str, body: dict):
        app.state.artifact_completed = body["operation_id"] is not None
        # BHE completes the associated management operation as part of this endpoint.
        app.state.operation_completed_by_artifact_upload = True
        return Response(status_code=204)

    @app.post("/api/v2/clients/management/end")
    async def end_operation(body: dict):
        app.state.operation_ended = True
        app.state.operation_end_payload = body
        return {
            "data": {
                "id": body["operation_id"],
                "client_id": "client-123",
                "artifact_id": "artifact-123",
                "type": "support_bundle",
                "status": body["status"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "execution_time": "2026-01-01T00:00:00Z",
            }
        }

    return TestClient(app)


@pytest.fixture
def mock_service(mock_bloodhound_api, monkeypatch):
    monkeypatch.setattr(bloodhound.openhound, "__version__", "0.3.0rc1")
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
        parsed_url = urlsplit(url)
        path = parsed_url.path
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"
        if method.upper() == "GET":
            return mock_bloodhound_api.get(path)
        if method.upper() == "POST":
            return mock_bloodhound_api.post(path, **kwargs)
        if method.upper() == "PUT":
            return mock_bloodhound_api.put(path, **kwargs)

        raise AssertionError(f"Unhandled method: {method}")

    monkeypatch.setattr("requests.request", mock_request)
    monkeypatch.setattr(scheduler_service, "ProcessPoolExecutor", DummyExecutor)
    monkeypatch.setattr(scheduler_service.config, "is_managed", lambda: False)

    return Service(
        bhe_uri="http://localhost:8000",
        token_key="test-key",
        token_id="test-id",
        collector_name="openhound-faker",
    )


@pytest.fixture
def managed_mode(mock_service, monkeypatch):
    monkeypatch.setattr(scheduler_service.config, "is_managed", lambda: True)


def test_client_update_sends_metadata(mock_service, mock_bloodhound_api, monkeypatch):
    monkeypatch.setattr(
        bloodhound_enterprise.socket, "gethostname", lambda: "test-host"
    )
    monkeypatch.setattr(
        bloodhound_enterprise.socket,
        "gethostbyname",
        lambda hostname: "192.0.2.10",
    )

    mock_service.client.update_client_metadata()

    assert mock_bloodhound_api.app.state.client_update_payload == {
        "Address": "192.0.2.10",
        "Hostname": "test-host",
        "Version": "v0.3.0-rc1",
    }


def test_request_forwards_connect_and_read_timeouts(monkeypatch):
    captured = {}

    class Response:
        status_code = 204
        text = ""

    def mock_request(**kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(bloodhound.requests, "request", mock_request)
    client = bloodhound.BloodHound(token_key="test-key", token_id="test-id")

    client.request(method="POST", path="/test", timeout=(1.5, 3.5))

    assert captured["timeout"] == (1.5, 3.5)


def test_upload_artifact_part_uses_bounded_timeouts(mock_service, monkeypatch):
    captured = {}

    def capture_request(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mock_service.client, "request", capture_request)

    mock_service.client.upload_artifact_part("artifact-123", 1, b"part")

    assert captured["timeout"] == (
        bloodhound_enterprise.SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
        bloodhound_enterprise.SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
    )


def test_available_collector_jobs_uses_bounded_timeouts(mock_service, monkeypatch):
    captured = {}

    def capture_request(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            json=lambda: load_json("collector_jobs_available_empty.json")
        )

    monkeypatch.setattr(mock_service.client, "request", capture_request)

    mock_service.client.available_collector_jobs("openhound-faker")

    assert captured["timeout"] == (
        bloodhound_enterprise.MANAGED_COLLECTOR_JOB_AVAILABLE_CONNECT_TIMEOUT_SECONDS,
        bloodhound_enterprise.MANAGED_COLLECTOR_JOB_AVAILABLE_READ_TIMEOUT_SECONDS,
    )


def test_client_update_uses_unknown_when_hostname_lookup_fails(
    mock_service, mock_bloodhound_api, monkeypatch
):
    def raise_error():
        raise OSError("hostname unavailable")

    monkeypatch.setattr(bloodhound_enterprise.socket, "gethostname", raise_error)

    mock_service.client.update_client_metadata()

    assert mock_bloodhound_api.app.state.client_update_payload == {
        "Address": "unknown",
        "Hostname": "unknown",
        "Version": "v0.3.0-rc1",
    }


def test_client_update_uses_unknown_when_ip_lookup_fails(
    mock_service, mock_bloodhound_api, monkeypatch
):
    monkeypatch.setattr(
        bloodhound_enterprise.socket, "gethostname", lambda: "test-host"
    )

    def raise_error(hostname: str):
        raise OSError(f"{hostname} unavailable")

    monkeypatch.setattr(bloodhound_enterprise.socket, "gethostbyname", raise_error)

    mock_service.client.update_client_metadata()

    assert mock_bloodhound_api.app.state.client_update_payload == {
        "Address": "unknown",
        "Hostname": "test-host",
        "Version": "v0.3.0-rc1",
    }


@pytest.mark.parametrize(
    ("outcome", "failure_message", "metadata", "expected_body"),
    [
        (
            ManagedJobOutcome.SUCCEEDED,
            None,
            None,
            {"outcome": "succeeded"},
        ),
        (
            ManagedJobOutcome.FAILED,
            "Collection failed",
            {"phase": "collect"},
            {
                "outcome": "failed",
                "failure_message": "Collection failed",
                "metadata": {"phase": "collect"},
            },
        ),
    ],
)
def test_end_managed_job_sends_managed_outcome(
    mock_service,
    mock_bloodhound_api,
    outcome,
    failure_message,
    metadata,
    expected_body,
):
    mock_service.client.end_managed_job(
        "collector-job-123",
        outcome,
        failure_message=failure_message,
        metadata=metadata,
    )

    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {"job_id": "collector-job-123", "body": expected_body}
    ]


def test_end_managed_job_uses_bounded_timeouts(mock_service, monkeypatch):
    captured = {}

    def capture_request(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mock_service.client, "request", capture_request)

    mock_service.client.end_managed_job(
        "collector-job-123", ManagedJobOutcome.SUCCEEDED
    )

    assert captured["timeout"] == (
        bloodhound_enterprise.MANAGED_JOB_END_CONNECT_TIMEOUT_SECONDS,
        bloodhound_enterprise.MANAGED_JOB_END_READ_TIMEOUT_SECONDS,
    )


@pytest.mark.parametrize(
    "transient_error",
    [
        BloodHoundHTTPError("server error", 500),
        requests.ConnectionError("connection failed"),
        requests.Timeout("request timed out"),
    ],
    ids=["http-500", "connection-error", "timeout"],
)
def test_end_managed_job_retries_transient_errors(
    mock_service, monkeypatch, caplog, transient_error
):
    attempts = 0
    delays = []

    def flaky_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise transient_error
        return object()

    monkeypatch.setattr(mock_service.client, "request", flaky_request)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with caplog.at_level(
        logging.DEBUG, logger="openhound.core.clients.bloodhound_enterprise"
    ):
        mock_service.client.end_managed_job(
            "collector-job-123", ManagedJobOutcome.SUCCEEDED
        )

    assert attempts == 2
    assert delays == [bloodhound_enterprise.MANAGED_JOB_END_RETRY_DELAY_SECONDS]
    assert [
        record.levelno
        for record in caplog.records
        if record.getMessage().startswith(
            (
                "Attempting to end managed collector job",
                "Managed collector job collector-job-123 end attempt failed",
                "Managed collector job collector-job-123 ended successfully",
            )
        )
    ] == [logging.INFO, logging.INFO, logging.INFO, logging.INFO]
    assert any(
        record.levelno == logging.DEBUG
        and getattr(record, "endpoint", None)
        == "/api/v2/collector-jobs/collector-job-123/end"
        for record in caplog.records
    )


def test_end_managed_job_raises_after_transient_retries_are_exhausted(
    mock_service, monkeypatch
):
    attempts = 0
    delays = []

    def unavailable(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise BloodHoundHTTPError("temporarily unavailable", 503)

    monkeypatch.setattr(mock_service.client, "request", unavailable)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with pytest.raises(BloodHoundHTTPError) as error:
        mock_service.client.end_managed_job(
            "collector-job-123", ManagedJobOutcome.FAILED
        )

    assert error.value.code == 503
    assert attempts == bloodhound_enterprise.MANAGED_JOB_END_MAX_RETRIES + 1
    assert delays == [
        bloodhound_enterprise.MANAGED_JOB_END_RETRY_DELAY_SECONDS
    ] * bloodhound_enterprise.MANAGED_JOB_END_MAX_RETRIES


def test_end_managed_job_does_not_retry_non_transient_client_error(
    mock_service, monkeypatch
):
    attempts = 0
    delays = []

    def bad_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise BloodHoundHTTPError("bad request", 400)

    monkeypatch.setattr(mock_service.client, "request", bad_request)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with pytest.raises(BloodHoundHTTPError) as error:
        mock_service.client.end_managed_job(
            "collector-job-123", ManagedJobOutcome.FAILED
        )

    assert error.value.code == 400
    assert attempts == 1
    assert delays == []


@pytest.mark.parametrize(
    "request_error",
    [
        requests.exceptions.InvalidURL("invalid URL"),
        requests.exceptions.TooManyRedirects("too many redirects"),
    ],
    ids=["invalid-url", "too-many-redirects"],
)
def test_end_managed_job_does_not_retry_non_transient_request_error(
    mock_service, monkeypatch, request_error
):
    attempts = 0
    delays = []

    def invalid_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise request_error

    monkeypatch.setattr(mock_service.client, "request", invalid_request)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with pytest.raises(type(request_error)):
        mock_service.client.end_managed_job(
            "collector-job-123", ManagedJobOutcome.FAILED
        )

    assert attempts == 1
    assert delays == []


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


def test_managed_mode_polls_collector_job_queue(
    mock_service, mock_bloodhound_api, managed_mode
):
    mock_bloodhound_api.app.state.collector_job_queue_response = load_json(
        "collector_jobs_available_with_job.json"
    )

    job = mock_service.check_jobs()

    assert job is not None
    assert job.id == "11111111-1111-1111-1111-111111111111"
    assert mock_bloodhound_api.app.state.collector_job_queue_requests
    assert mock_bloodhound_api.app.state.jobs_available_requests == 0


def test_unmanaged_mode_never_polls_collector_job_queue(
    mock_service, mock_bloodhound_api, monkeypatch
):
    monkeypatch.setattr(scheduler_service.config, "is_managed", lambda: False)

    job = mock_service.check_jobs()

    assert job is not None
    assert job.id == 123
    assert mock_bloodhound_api.app.state.collector_job_queue_requests == []
    assert mock_bloodhound_api.app.state.jobs_available_requests == 1


def test_managed_queue_request_uses_collector_key_and_first_page(
    mock_service, mock_bloodhound_api, managed_mode
):
    mock_service.check_jobs()

    assert mock_bloodhound_api.app.state.collector_job_queue_requests == [
        {"limit": "1", "job_key": "eq:openhound-faker"}
    ]


def test_collector_job_queue_response_parses_contract_fields():
    payload = load_json("collector_jobs_available_with_jobs.json")
    response = CollectorJobsAvailable.model_validate(payload)
    job = response.data.jobs[0]

    assert response.count == 2
    assert response.skip == 0
    assert response.limit == 2
    assert job == CollectorJob.model_validate(payload["data"]["jobs"][0])
    assert job.id == "11111111-1111-1111-1111-111111111111"
    assert job.job_schedule_id is None
    assert job.job_profile_id is None
    assert job.job_type_id == 7
    assert job.job_key == "openhound-faker"
    assert job.params_version == "v2"
    assert job.params == {
        "domains": ["example.com", "example.org"],
        "include_deleted": False,
        "nested": {"depth": 2},
    }
    assert job.scope_client_id is None
    assert job.secret_key_id is None
    assert job.priority == 10
    assert job.status == "running"
    assert job.run_at.isoformat() == "2026-02-20T10:00:00+00:00"
    assert job.unclaimed_deadline_at.isoformat() == "2026-02-20T10:05:00+00:00"
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.last_failure is None
    assert job.claimed_by is None
    assert job.claimed_at is None
    assert job.claim_expires_at is None
    assert job.created_at.isoformat() == "2026-02-20T09:00:00+00:00"
    assert job.updated_at.isoformat() == "2026-02-20T09:30:00+00:00"


def test_managed_mode_selects_first_queue_job_unchanged(
    mock_service, managed_mode, monkeypatch
):
    payload = load_json("collector_jobs_available_with_jobs.json")
    available_jobs = CollectorJobsAvailable.model_validate(payload)
    monkeypatch.setattr(
        mock_service.client,
        "available_collector_jobs",
        lambda job_key: available_jobs,
    )

    selected = mock_service.check_jobs()

    assert selected == CollectorJob.model_validate(payload["data"]["jobs"][0])
    assert selected != CollectorJob.model_validate(payload["data"]["jobs"][1])


def test_managed_mode_returns_no_job_for_empty_queue(
    mock_service, mock_bloodhound_api, managed_mode
):
    assert mock_service.check_jobs() is None
    assert len(mock_bloodhound_api.app.state.collector_job_queue_requests) == 1


def test_running_job_prevents_managed_queue_poll(
    mock_service, mock_bloodhound_api, managed_mode
):
    mock_service.job_running = 123

    mock_service._poll()

    assert mock_bloodhound_api.app.state.collector_job_queue_requests == []
    assert mock_bloodhound_api.app.state.jobs_current_requests == 1


def test_queue_http_error_is_logged_and_next_poll_continues(
    mock_service, mock_bloodhound_api, managed_mode, caplog
):
    mock_bloodhound_api.app.state.collector_job_queue_error_status = 503

    with caplog.at_level(logging.ERROR):
        mock_service._poll()

    queue_errors = [
        record
        for record in caplog.records
        if record.name == "openhound.core.clients.bloodhound_enterprise"
        and record.levelno == logging.ERROR
        and record.getMessage() == "Managed collector job queue request failed."
    ]
    assert len(queue_errors) == 1

    mock_bloodhound_api.app.state.collector_job_queue_error_status = None
    mock_service._poll()

    assert len(mock_bloodhound_api.app.state.collector_job_queue_requests) == 2


def test_each_queue_request_is_logged(
    mock_service, mock_bloodhound_api, managed_mode, caplog
):
    with caplog.at_level(
        logging.DEBUG, logger="openhound.core.clients.bloodhound_enterprise"
    ):
        mock_service.client.available_collector_jobs("openhound-faker")
        mock_service.client.available_collector_jobs("openhound-faker")

    queue_polls = [
        record
        for record in caplog.records
        if record.name == "openhound.core.clients.bloodhound_enterprise"
        and record.getMessage() == "Polling managed collector job queue."
    ]
    assert len(queue_polls) == 2
    assert all(record.levelno == logging.DEBUG for record in queue_polls)


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


def _support_bundle_operation() -> dict:
    return json.loads(
        (MANAGEMENT_DATA_DIR / "management_available_with_operation.json").read_text()
    )["data"][0]


def test_check_management_returns_support_bundle_operation(
    mock_service, mock_bloodhound_api
):
    mock_bloodhound_api.app.state.management_operations = [_support_bundle_operation()]

    operation = mock_service.check_management()

    assert operation is not None
    assert operation.type is ManagementOperationType.SUPPORT_BUNDLE


def test_check_management_ignores_non_queued_operations(
    mock_service, mock_bloodhound_api
):
    operation = _support_bundle_operation()
    operation["status"] = ManagementOperationStatus.RUNNING.value
    mock_bloodhound_api.app.state.management_operations = [operation]

    assert mock_service.check_management() is None


def test_poll_prioritizes_management_over_a_new_job(
    mock_service, mock_bloodhound_api, monkeypatch
):
    mock_bloodhound_api.app.state.management_operations = [_support_bundle_operation()]
    sent = []
    monkeypatch.setattr(mock_service, "_send_support_bundle", sent.append)

    mock_service._poll()

    assert len(sent) == 1
    assert mock_bloodhound_api.app.state.job_started is False


def test_poll_starts_job_when_no_management_work(
    mock_service, mock_bloodhound_api, monkeypatch
):
    submitted = Future()
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: submitted)

    mock_service._poll()

    assert mock_bloodhound_api.app.state.job_started is True


def test_poll_still_checks_jobs_when_management_endpoint_fails(
    mock_service, mock_bloodhound_api, monkeypatch
):
    submitted = Future()
    monkeypatch.setattr(mock_service, "check_management", lambda: 1 / 0)
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: submitted)

    mock_service._poll()

    assert mock_bloodhound_api.app.state.job_started is True


def test_poll_does_not_start_a_job_when_management_work_fails(
    mock_service, mock_bloodhound_api, monkeypatch
):
    mock_bloodhound_api.app.state.management_operations = [_support_bundle_operation()]

    def fail(operation):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(mock_service, "_send_support_bundle", fail)

    mock_service._poll()

    assert mock_bloodhound_api.app.state.job_started is False


def test_send_support_bundle_claims_uploads_completes_and_cleans_up(
    mock_service, mock_bloodhound_api, tmp_path, monkeypatch
):
    log = tmp_path / "openhound.log"
    log.write_text("support log")
    mock_service.log_base_path = tmp_path
    created = []

    from openhound.scheduler import service as scheduler_service

    original_create = scheduler_service.create_support_bundle

    def capture_bundle(*args):
        bundle = original_create(*args)
        created.append(bundle)
        return bundle

    monkeypatch.setattr(scheduler_service, "create_support_bundle", capture_bundle)
    operation = ManagementOperation.model_validate(_support_bundle_operation())

    mock_service._send_support_bundle(operation)

    assert mock_bloodhound_api.app.state.operation_start_payload == {
        "operation_id": operation.id
    }
    assert (
        mock_bloodhound_api.app.state.artifact_create_payload["operation_id"]
        == operation.id
    )
    assert mock_bloodhound_api.app.state.uploaded_parts
    assert mock_bloodhound_api.app.state.artifact_completed is True
    assert mock_bloodhound_api.app.state.operation_completed_by_artifact_upload is True
    assert mock_bloodhound_api.app.state.operation_end_payload == {
        "operation_id": operation.id,
        "status": ManagementOperationStatus.SUCCEEDED.value,
    }
    assert created and not created[0].exists()
    assert not created[0].parent.exists()


def test_create_artifact_upload_preserves_entire_create_response(
    mock_service, tmp_path
):
    bundle = tmp_path / "support-bundle.zip"
    bundle.write_bytes(b"support bundle")

    session = mock_service.client.create_artifact_upload("operation-123", bundle)

    assert session.artifact_id == "artifact-123"
    assert session.client_id == "client-123"
    assert session.storage_key.endswith("support_bundle_2026-01-01_00-00-00.zip")
    assert session.status == "pending"
    assert session.missing_parts == [1]
    assert session.management_operation.id == "operation-123"
    assert session.management_operation.artifact_id == session.artifact_id


@pytest.mark.parametrize(
    "failure_point",
    [
        "start_operation",
        "create_support_bundle",
        "create_artifact_upload",
        "upload_artifact_part",
        "complete_artifact_upload",
    ],
)
def test_send_support_bundle_marks_operation_failed_for_each_lifecycle_failure(
    mock_service, mock_bloodhound_api, monkeypatch, caplog, failure_point
):
    operation = ManagementOperation.model_validate(_support_bundle_operation())

    def fail(*args, **kwargs):
        raise RuntimeError(f"{failure_point} failed")

    if failure_point == "create_support_bundle":
        monkeypatch.setattr(scheduler_service, "create_support_bundle", fail)
    else:
        monkeypatch.setattr(mock_service.client, failure_point, fail)

    with pytest.raises(RuntimeError, match=f"{failure_point} failed"):
        mock_service._send_support_bundle(operation)

    assert mock_bloodhound_api.app.state.operation_end_payload == {
        "operation_id": operation.id,
        "status": ManagementOperationStatus.FAILED.value,
    }
    assert "Support bundle operation" in caplog.text


def test_send_support_bundle_retries_transient_part_upload_failure(
    mock_service, monkeypatch, tmp_path
):
    log = tmp_path / "openhound.log"
    log.write_text("support log")
    mock_service.log_base_path = tmp_path
    operation = ManagementOperation.model_validate(_support_bundle_operation())
    original_request = mock_service.client.request
    attempts = 0
    delays = []

    def flaky_request(method, path, **kwargs):
        nonlocal attempts
        if "/parts/" in path:
            attempts += 1
            if attempts < 3:
                raise BloodHoundHTTPError("temporary failure", 503)
        return original_request(method, path, **kwargs)

    monkeypatch.setattr(mock_service.client, "request", flaky_request)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    mock_service._send_support_bundle(operation)

    assert attempts == 3
    assert delays == [2, 2]


def test_support_bundle_retry_preserves_broad_request_exception_policy(
    mock_service, monkeypatch
):
    attempts = 0
    delays = []

    def request():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.exceptions.InvalidURL("invalid URL")
        return "ok"

    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    result = mock_service.client._retry_support_bundle_request(
        "test support bundle request", request
    )

    assert result == "ok"
    assert attempts == 2
    assert delays == [bloodhound_enterprise.SUPPORT_BUNDLE_RETRY_DELAY_SECONDS]


def test_send_support_bundle_fails_after_transient_retries_are_exhausted(
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path
):
    log = tmp_path / "openhound.log"
    log.write_text("support log")
    mock_service.log_base_path = tmp_path
    operation = ManagementOperation.model_validate(_support_bundle_operation())
    original_request = mock_service.client.request
    attempts = 0
    delays = []

    def unavailable_part_upload(method, path, **kwargs):
        nonlocal attempts
        if "/parts/" in path:
            attempts += 1
            raise BloodHoundHTTPError("temporarily unavailable", 503)
        return original_request(method, path, **kwargs)

    monkeypatch.setattr(mock_service.client, "request", unavailable_part_upload)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with pytest.raises(BloodHoundHTTPError):
        mock_service._send_support_bundle(operation)

    assert attempts == 4
    assert delays == [2, 2, 2]
    assert mock_bloodhound_api.app.state.operation_end_payload == {
        "operation_id": operation.id,
        "status": ManagementOperationStatus.FAILED.value,
    }


def test_collection_logs_include_extension_and_openhound_versions(monkeypatch, caplog):
    collector = SimpleNamespace(
        name="example",
        package_version="2.3.4",
        metadata=SimpleNamespace(version="1.2.3"),
    )
    monkeypatch.setattr(
        scheduler_service.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[collector]),
    )
    monkeypatch.setattr(
        scheduler_service.dataflow, "pipeline", lambda extension: {"collect": []}
    )
    monkeypatch.setattr(scheduler_service.openhound, "__version__", "4.5.6")

    with caplog.at_level(logging.INFO, logger="openhound.scheduler.service"):
        _subprocess_collect("example", 42)

    collection_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith(("Subprocess running collection", "Collection for job"))
    ]
    assert len(collection_records) == 2
    for record in collection_records:
        assert record.collector_extension == "example"
        assert record.collector_extension_version == "2.3.4"
        assert record.openhound_version == "4.5.6"
        assert record.job_id == 42
