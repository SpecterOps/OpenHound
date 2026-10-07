import base64
import gzip
import hashlib
import json
import logging
import traceback
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

import pytest
import requests
from botocore.exceptions import ClientError
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from openhound.core.clients import bloodhound, bloodhound_enterprise
from openhound.core.clients.aws_secrets_manager import AWSSecretsManager
from openhound.core.clients.bhe_credentials import BHECredentials
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
    ManagedJobProcessingError,
    ManagedRuntimeUnavailableError,
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
    app.state.collector_job_end_statuses = []
    app.state.collector_job_claim_requests = []
    app.state.collector_job_claim_statuses = []
    app.state.collector_job_claim_response = load_json(
        "collector_jobs_available_with_job.json"
    )["data"]["jobs"][0]
    app.state.managed_events = []
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

    @app.post("/api/v2/collector-job-queue/{job_id}/claim")
    async def claim_managed_job(job_id: str, request: Request):
        app.state.managed_events.append("claim")
        app.state.collector_job_claim_requests.append(
            {"job_id": job_id, "body": await request.body()}
        )
        if app.state.collector_job_claim_statuses:
            status = app.state.collector_job_claim_statuses.pop(0)
            if status != 200:
                return Response("raw-provider-secret", status_code=status)
        return {"data": {"job": app.state.collector_job_claim_response}}

    @app.post("/api/v2/jobs/end")
    async def end_job(body: dict):
        app.state.job_ended = True
        app.state.end_payload = body
        return load_json("job_end.json")

    @app.post("/api/v2/collector-job-queue/{job_id}/end")
    async def end_managed_job(job_id: str, body: dict):
        app.state.managed_events.append("end")
        app.state.collector_job_end_requests.append({"job_id": job_id, "body": body})
        status = (
            app.state.collector_job_end_statuses.pop(0)
            if app.state.collector_job_end_statuses
            else 200
        )
        return Response(
            "raw-provider-secret" if status != 200 else "", status_code=status
        )

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

    return Service(
        bhe_uri="http://localhost:8000",
        token_key="test-key",
        token_id="test-id",
        collector_name="openhound-faker",
        managed=False,
    )


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


def test_managed_client_refreshes_credentials_and_retries_once_after_401(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(status_code=401, text="expired"),
            SimpleNamespace(status_code=204, text=""),
        ]
    )
    requests = []

    def mock_request(**kwargs):
        requests.append(kwargs)
        return next(responses)

    refreshes = []

    def refresh():
        refreshes.append(True)
        return BHECredentials(token_id="new-id", token_key="new-key")

    monkeypatch.setattr(bloodhound.requests, "request", mock_request)
    client = bloodhound_enterprise.BloodHoundEnterprise(
        token_key="old-key",
        token_id="old-id",
        credential_refresh=refresh,
    )

    client.request(method="GET", path="/test")

    assert refreshes == [True]
    assert len(requests) == 2
    assert requests[0]["headers"]["Authorization"] == "bhesignature old-id"
    assert requests[1]["headers"]["Authorization"] == "bhesignature new-id"
    assert client.token_id == "new-id"
    assert client.token_key == "new-key"


def test_managed_client_does_not_retry_a_second_401(monkeypatch):
    def mock_request(**kwargs):
        return SimpleNamespace(status_code=401, text="still expired")

    refreshes = []

    def refresh():
        refreshes.append(True)
        return BHECredentials(token_id="new-id", token_key="new-key")

    monkeypatch.setattr(bloodhound.requests, "request", mock_request)
    client = bloodhound_enterprise.BloodHoundEnterprise(
        token_key="old-key",
        token_id="old-id",
        credential_refresh=refresh,
    )

    with pytest.raises(BloodHoundHTTPError) as error:
        client.request(method="GET", path="/test")

    assert error.value.code == 401
    assert refreshes == [True]


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
        == "/api/v2/collector-job-queue/collector-job-123/end"
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
    assert attempts == 4
    assert delays == [bloodhound_enterprise.MANAGED_JOB_END_RETRY_DELAY_SECONDS] * (
        bloodhound_enterprise.MANAGED_JOB_END_MAX_ATTEMPTS - 1
    )


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


def test_managed_collector_job_check_requests_first_queue_job(
    mock_service, mock_bloodhound_api
):
    mock_bloodhound_api.app.state.collector_job_queue_response = load_json(
        "collector_jobs_available_with_job.json"
    )

    job = mock_service.check_managed_collector_jobs()

    assert job is not None
    assert job.id == UUID("11111111-1111-1111-1111-111111111111")
    assert mock_bloodhound_api.app.state.collector_job_queue_requests == [
        {"limit": "1", "job_key": "eq:openhound-faker"}
    ]
    assert mock_bloodhound_api.app.state.jobs_available_requests == 0


def test_collector_job_queue_response_parses_contract_fields():
    payload = load_json("collector_jobs_available_with_jobs.json")
    response = CollectorJobsAvailable.model_validate(payload)
    job = response.data.jobs[0]

    assert response.count == 2
    assert response.skip == 0
    assert response.limit == 2
    assert job == CollectorJob.model_validate(payload["data"]["jobs"][0])
    assert job.id == UUID("11111111-1111-1111-1111-111111111111")
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
    assert job.status == "ready"
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


@pytest.mark.parametrize("field", ["id", "scope_client_id", "claimed_by"])
def test_collector_job_queue_response_rejects_invalid_uuids(field):
    payload = load_json("collector_jobs_available_with_jobs.json")

    payload["data"]["jobs"][1][field] = "not-a-uuid"

    with pytest.raises(ValueError):
        CollectorJobsAvailable.model_validate(payload)


@pytest.mark.parametrize("field", ["scope_client_id", "claimed_by"])
def test_collector_job_queue_response_requires_nullable_uuid_fields(field):
    payload = load_json("collector_jobs_available_with_jobs.json")

    del payload["data"]["jobs"][0][field]

    with pytest.raises(ValueError):
        CollectorJobsAvailable.model_validate(payload)


def test_managed_collector_job_check_returns_no_job_for_empty_queue(
    mock_service, mock_bloodhound_api
):
    assert mock_service.check_managed_collector_jobs() is None
    assert len(mock_bloodhound_api.app.state.collector_job_queue_requests) == 1


def test_queue_http_error_is_logged_and_re_raised(
    mock_service, mock_bloodhound_api, caplog
):
    mock_bloodhound_api.app.state.collector_job_queue_error_status = 503

    with caplog.at_level(logging.ERROR), pytest.raises(BloodHoundHTTPError):
        mock_service.check_managed_collector_jobs()

    queue_errors = [
        record
        for record in caplog.records
        if record.name == "openhound.core.clients.bloodhound_enterprise"
        and record.levelno == logging.ERROR
        and record.getMessage() == "Managed collector job queue request failed."
    ]
    assert len(queue_errors) == 1


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
        if record.getMessage().startswith(
            ("Subprocess running collection", "Collection for job")
        )
    ]
    assert len(collection_records) == 2
    for record in collection_records:
        assert record.collector_extension == "example"
        assert record.collector_extension_version == "2.3.4"
        assert record.openhound_version == "4.5.6"
        assert record.job_id == 42


@pytest.mark.parametrize("collection_error", [None, RuntimeError("collection failed")])
@pytest.mark.parametrize(
    "report_error",
    [BloodHoundHTTPError("unauthorized", 401), RuntimeError("refresh failed")],
)
def test_completed_job_retries_reporting_without_losing_outcome(
    mock_service, monkeypatch, collection_error, report_error
):
    future = Future()
    if collection_error is None:
        future.set_result(Result(results={"collect": ["a"]}, job_id=123))
        expected = JobStatus.COMPLETE
    else:
        future.set_exception(collection_error)
        expected = JobStatus.FAILED
    mock_service.future = future
    mock_service.job_running = 123
    reports = []

    def end_job(status, message):
        reports.append((status, message))
        if len(reports) == 1:
            raise report_error

    monkeypatch.setattr(mock_service.client, "end_job", end_job)
    monkeypatch.setattr(mock_service, "check_jobs", lambda: None)

    mock_service._poll()

    assert mock_service.future is future
    assert mock_service.job_running == 123
    assert [status for status, _ in reports] == [expected]

    mock_service._poll()

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert reports == [reports[0], reports[0]]


def test_managed_refresh_rejects_invalid_id_and_recovers(mock_service, monkeypatch):
    from openhound.core.clients.bhe_credentials import (
        AWSBHECredentials,
        InvalidBHECredentialsSecret,
    )

    secret = {"token_id": "invalid-id", "token_key": "new-key"}
    provider = AWSBHECredentials("bhe", SimpleNamespace(get_secret=lambda _: secret))
    client = mock_service.client
    client._credential_refresh = provider.refresh
    old_pair = (client.token_id, client.token_key)
    attempts = []
    valid_id = "12345678-1234-1234-1234-123456789abc"

    def request(self, **kwargs):
        attempts.append((self.token_id, self.token_key))
        if self.token_id != valid_id:
            raise BloodHoundHTTPError("unauthorized", 401)
        return "ok"

    monkeypatch.setattr(bloodhound.BloodHound, "request", request)

    with pytest.raises(InvalidBHECredentialsSecret):
        client.request("GET", "/test")

    assert (client.token_id, client.token_key) == old_pair
    assert attempts == [old_pair]

    secret["token_id"] = valid_id
    assert client.request("GET", "/test") == "ok"
    assert attempts == [old_pair, old_pair, (valid_id, "new-key")]


@pytest.fixture
def managed_service(mock_service, mock_bloodhound_api):
    state = mock_bloodhound_api.app.state
    state.collector_job_queue_response = load_json(
        "collector_jobs_available_with_job.json"
    )
    # Discovery is intentionally stale. Only the claimed snapshot is authoritative.
    state.collector_job_queue_response["data"]["jobs"][0][
        "secret_key_id"
    ] = "stale-reference"
    state.collector_job_claim_response.update(
        secret_key_id="private-secret-reference",
        status="claimed",
        params={"claimed_parameter": True},
    )
    mock_service.managed = True
    mock_service.handoffs = []
    mock_service.secret_requests = []

    def get_secret_value(*, SecretId):
        state.managed_events.append("retrieve")
        mock_service.secret_requests.append(SecretId)
        return {"SecretString": '{"credential":"private-credential-value"}'}

    mock_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=get_secret_value)
    )

    def start_handoff(prepared):
        state.managed_events.append("start_handoff")
        mock_service.handoffs.append(prepared)

    mock_service.managed_job_runner = start_handoff
    return mock_service


def test_claim_request_is_bodyless_and_parses_data_job(
    managed_service, mock_bloodhound_api, monkeypatch
):
    captured = []
    original = managed_service.client.request

    def request(**kwargs):
        captured.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(managed_service.client, "request", request)
    job_id = mock_bloodhound_api.app.state.collector_job_claim_response["id"]
    # Reclaim of the same holder parses the same job, with no credential access.
    first = managed_service.client.claim_managed_job(job_id)
    second = managed_service.client.claim_managed_job(job_id)

    assert (
        first
        == second
        == CollectorJob.model_validate(
            mock_bloodhound_api.app.state.collector_job_claim_response
        )
    )
    assert (
        captured
        == [
            {
                "method": "POST",
                "path": f"/api/v2/collector-job-queue/{job_id}/claim",
                "timeout": (
                    bloodhound_enterprise.MANAGED_JOB_CLAIM_CONNECT_TIMEOUT_SECONDS,
                    bloodhound_enterprise.MANAGED_JOB_CLAIM_READ_TIMEOUT_SECONDS,
                ),
            }
        ]
        * 2
    )
    assert (
        mock_bloodhound_api.app.state.collector_job_claim_requests
        == [{"job_id": job_id, "body": b""}] * 2
    )
    assert managed_service.secret_requests == []


def test_managed_poll_claims_retrieves_and_hands_off_authoritative_job(
    managed_service, mock_bloodhound_api, caplog
):
    with caplog.at_level(logging.DEBUG):
        managed_service._poll()

    state = mock_bloodhound_api.app.state
    assert state.managed_events == ["claim", "retrieve", "start_handoff"]
    assert managed_service.secret_requests == ["private-secret-reference"]
    (prepared,) = managed_service.handoffs
    assert prepared.job.params == {"claimed_parameter": True}
    assert prepared.job.status == "claimed"
    assert prepared.credentials == {"credential": "private-credential-value"}
    assert state.job_started is False
    assert state.jobs_available_requests == state.jobs_current_requests == 0
    assert state.collector_job_end_requests == []
    for message in ("Job successfully picked up", "Credential retrieval valid"):
        assert any(
            r.levelno == logging.INFO and r.getMessage() == message
            for r in caplog.records
        )
    assert any(
        r.levelno == logging.DEBUG
        and r.getMessage() == "Sending managed collector claim request."
        for r in caplog.records
    )
    assert any(
        r.levelno == logging.DEBUG
        and getattr(r, "operation", None) == "get_secret_value"
        for r in caplog.records
    )
    for forbidden in (
        "private-secret-reference",
        "private-credential-value",
        "test-key",
        "test-id",
    ):
        assert forbidden not in caplog.text
        assert forbidden not in repr(prepared)
        assert all(forbidden not in repr(r.__dict__) for r in caplog.records)


def test_managed_claim_refreshes_bhe_token_after_401_before_retrieving_job_secret(
    managed_service, mock_bloodhound_api, monkeypatch
):
    original_request = bloodhound.BloodHound.request
    claims = []
    refreshes = []
    state = mock_bloodhound_api.app.state

    def request(self, method, path, **kwargs):
        if path.endswith("/claim"):
            claims.append((self.token_id, kwargs["timeout"]))
            if len(claims) == 1:
                raise BloodHoundHTTPError("expired token", 401)
        return original_request(self, method, path, **kwargs)

    def refresh():
        refreshes.append(True)
        state.managed_events.append("refresh_bhe_token")
        return BHECredentials(token_id="new-token-id", token_key="new-token-key")

    monkeypatch.setattr(bloodhound.BloodHound, "request", request)
    managed_service.client._credential_refresh = refresh

    managed_service._poll()

    claim_timeout = (
        bloodhound_enterprise.MANAGED_JOB_CLAIM_CONNECT_TIMEOUT_SECONDS,
        bloodhound_enterprise.MANAGED_JOB_CLAIM_READ_TIMEOUT_SECONDS,
    )
    assert claims == [
        ("test-id", claim_timeout),
        ("new-token-id", claim_timeout),
    ]
    assert refreshes == [True]
    assert state.managed_events == [
        "refresh_bhe_token",
        "claim",
        "retrieve",
        "start_handoff",
    ]
    assert managed_service.secret_requests == ["private-secret-reference"]
    assert state.collector_job_end_requests == []


@pytest.mark.parametrize(
    "document",
    [
        "",
        "plain-secret",
        '{"malformed":',
        "[]",
        "{}",
        '{"value":"  "}',
        '{"value":null}',
        '{"value":{}}',
        '{"value":[]}',
        '{"value":{"nested":""}}',
        '{"value":NaN}',
        '{" ":"private-credential-value"}',
    ],
)
def test_invalid_managed_credentials_end_failed_without_start(
    managed_service, mock_bloodhound_api, document, caplog
):
    state = mock_bloodhound_api.app.state

    def retrieve(*, SecretId):
        state.managed_events.append("retrieve")
        return {"SecretString": document}

    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=retrieve)
    )
    with caplog.at_level(logging.DEBUG):
        managed_service._poll()

    assert state.managed_events == ["claim", "retrieve", "end"]
    assert state.collector_job_end_requests == [
        {
            "job_id": state.collector_job_claim_response["id"],
            "body": {
                "outcome": "failed",
                "failure_message": "Credential validation failed",
            },
        }
    ]
    assert managed_service.handoffs == []
    assert state.job_started is False
    assert "Credential retrieval valid" not in caplog.text
    assert "private-credential-value" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    assert any(
        r.levelno == logging.DEBUG
        and r.getMessage() == "Sending managed collector end-job request."
        for r in caplog.records
    )


@pytest.mark.parametrize("reference", [None, "", "  "])
def test_missing_managed_secret_reference_fails_without_local_fallback(
    managed_service, mock_bloodhound_api, monkeypatch, reference
):
    monkeypatch.setenv("SOURCES__SOURCE__FAKER__CREDENTIALS", "local-credentials")
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response["secret_key_id"] = reference
    managed_service._poll()

    assert state.managed_events == ["claim", "end"]
    assert managed_service.secret_requests == managed_service.handoffs == []
    assert (
        state.collector_job_end_requests[0]["body"]["failure_message"]
        == "Credential validation failed"
    )


def test_aws_retrieval_failure_is_redacted_and_ends_without_start(
    managed_service, mock_bloodhound_api, caplog
):
    def retrieve(*, SecretId):
        mock_bloodhound_api.app.state.managed_events.append("retrieve")
        raise ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "private-credential-value",
                }
            },
            "GetSecretValue",
        )

    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=retrieve)
    )
    with caplog.at_level(logging.DEBUG):
        managed_service._poll()

    assert mock_bloodhound_api.app.state.managed_events == ["claim", "retrieve", "end"]
    assert managed_service.handoffs == []
    assert "private-credential-value" not in caplog.text
    assert all(
        "private-credential-value" not in repr(r.__dict__) for r in caplog.records
    )


@pytest.mark.parametrize("status", [409, 404, 400, 503])
def test_failed_claim_does_not_retrieve_or_end(
    managed_service, mock_bloodhound_api, monkeypatch, caplog, status
):
    state = mock_bloodhound_api.app.state
    attempts = (
        bloodhound_enterprise.MANAGED_JOB_CLAIM_MAX_ATTEMPTS if status == 503 else 1
    )
    state.collector_job_claim_statuses = [status] * attempts
    delays = []
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with caplog.at_level(logging.DEBUG):
        if status == 409:
            managed_service._poll()
        else:
            with pytest.raises(ManagedJobProcessingError) as raised:
                managed_service._poll()
            assert "raw-provider-secret" not in "".join(
                traceback.format_exception(raised.value)
            )

    assert state.managed_events == ["claim"] * attempts
    assert delays == [bloodhound_enterprise.MANAGED_JOB_CLAIM_RETRY_DELAY_SECONDS] * (
        attempts - 1
    )
    assert managed_service.secret_requests == managed_service.handoffs == []
    assert state.collector_job_end_requests == []
    assert "Job successfully picked up" not in caplog.text
    assert "Credential validation failed" not in caplog.text
    assert "raw-provider-secret" not in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        BloodHoundHTTPError("raw-provider-secret", 429),
        BloodHoundHTTPError("raw-provider-secret", 502),
        requests.ConnectionError("raw-provider-secret"),
        requests.Timeout("raw-provider-secret"),
    ],
)
def test_claim_retries_ambiguous_transient_failure_for_same_id(
    managed_service, mock_bloodhound_api, monkeypatch, error
):
    original = managed_service.client.request
    claims = []
    delays = []

    def flaky_request(**kwargs):
        if kwargs["path"].endswith("/claim"):
            claims.append(kwargs["path"])
            if len(claims) == 1:
                raise error
        return original(**kwargs)

    monkeypatch.setattr(managed_service.client, "request", flaky_request)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)
    managed_service._poll()

    assert len(claims) == 2 and claims[0] == claims[1]
    assert delays == [bloodhound_enterprise.MANAGED_JOB_CLAIM_RETRY_DELAY_SECONDS]
    assert mock_bloodhound_api.app.state.managed_events == [
        "claim",
        "retrieve",
        "start_handoff",
    ]


def test_invalid_claim_response_is_sanitized_without_retrieval_or_end(
    managed_service, mock_bloodhound_api, caplog
):
    mock_bloodhound_api.app.state.collector_job_claim_response["id"] = (
        "private-credential-value"
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised:
        managed_service.client.claim_managed_job("job-id")
    assert "private-credential-value" not in "".join(
        traceback.format_exception(raised.value)
    )
    assert "private-credential-value" not in caplog.text
    assert managed_service.secret_requests == []
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []


@pytest.mark.parametrize("resolved_status", [200, 404])
def test_exhausted_credential_failure_end_is_retained_and_retried_before_discovery(
    managed_service, mock_bloodhound_api, monkeypatch, caplog, resolved_status
):
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response["secret_key_id"] = None
    max_attempts = bloodhound_enterprise.MANAGED_JOB_END_MAX_ATTEMPTS
    state.collector_job_end_statuses = [503] * max_attempts
    delays = []
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ManagedJobProcessingError) as raised,
    ):
        managed_service._poll()
    assert (
        managed_service._pending_managed_failure
        == state.collector_job_claim_response["id"]
    )
    assert "raw-provider-secret" not in "".join(
        traceback.format_exception(raised.value)
    )
    assert "raw-provider-secret" not in caplog.text
    assert all("raw-provider-secret" not in repr(r.__dict__) for r in caplog.records)
    assert delays == [bloodhound_enterprise.MANAGED_JOB_END_RETRY_DELAY_SECONDS] * (
        max_attempts - 1
    )

    state.collector_job_end_statuses = [resolved_status]
    managed_service._poll()
    assert managed_service._pending_managed_failure is None
    assert (
        len(state.collector_job_queue_requests)
        == len(state.collector_job_claim_requests)
        == 1
    )
    assert state.managed_events == ["claim"] + ["end"] * (max_attempts + 1)
    assert managed_service.handoffs == []


def test_end_404_discards_state_immediately_without_retries(
    managed_service, mock_bloodhound_api, monkeypatch
):
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response["secret_key_id"] = None
    state.collector_job_end_statuses = [404]
    delays = []
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)
    managed_service._poll()
    assert managed_service._pending_managed_failure is None
    assert delays == []
    assert state.managed_events == ["claim", "end"]


def test_managed_poll_skips_nonmatching_queue_entries(
    managed_service, mock_bloodhound_api
):
    state = mock_bloodhound_api.app.state
    jobs = state.collector_job_queue_response["data"]["jobs"]
    jobs.insert(
        0,
        {
            **jobs[0],
            "job_key": "other-collector",
            "id": "22222222-2222-2222-2222-222222222222",
        },
    )
    managed_service._poll()
    assert (
        state.collector_job_claim_requests[0]["job_id"]
        == "11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.parametrize("entry", ["_poll", "start"])
def test_managed_runtime_dependency_fails_before_claim_or_legacy_work(
    managed_service, mock_bloodhound_api, entry
):
    managed_service.managed_job_runner = None
    with pytest.raises(
        ManagedRuntimeUnavailableError, match="Managed collection runtime is required"
    ):
        getattr(managed_service, entry)()
    state = mock_bloodhound_api.app.state
    assert (
        state.collector_job_claim_requests == state.collector_job_queue_requests == []
    )
    assert state.jobs_available_requests == state.jobs_current_requests == 0
    assert state.job_started is False


def test_unmanaged_poll_does_not_access_managed_queue_or_secrets(
    mock_service, mock_bloodhound_api
):
    mock_service._poll()
    state = mock_bloodhound_api.app.state
    assert state.job_started is True
    assert state.start_payload == {"id": 123}
    assert (
        state.collector_job_queue_requests == state.collector_job_claim_requests == []
    )
    assert state.collector_job_end_requests == []


def test_mode_config_routes_scheduler_to_managed_dependency(mock_service, monkeypatch):
    monkeypatch.setattr(scheduler_service, "is_managed", lambda: True)
    service = Service(
        bhe_uri="http://localhost:8000",
        token_key="test-key",
        token_id="test-id",
        collector_name="openhound-faker",
    )
    assert service.managed is True
    with pytest.raises(
        ManagedRuntimeUnavailableError, match="Managed collection runtime is required"
    ):
        service._poll()


def test_runtime_failure_is_sanitized_and_not_reported_as_credential_failure(
    managed_service, mock_bloodhound_api, caplog
):
    def fail_start(prepared):
        raise ValueError(str(prepared.credentials))

    managed_service.managed_job_runner = fail_start
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ManagedJobProcessingError) as raised,
    ):
        managed_service._poll()
    assert "private-credential-value" not in "".join(
        traceback.format_exception(raised.value)
    )
    assert "private-credential-value" not in caplog.text
    assert "Credential validation failed" not in caplog.text
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []


def test_valid_generic_nested_json_is_handed_off_without_field_schema(managed_service):
    credentials = {"arbitrary_field": {"nested": ["value", False, 0, 1.5]}}
    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(
            get_secret_value=lambda **kwargs: {"SecretString": json.dumps(credentials)}
        )
    )
    managed_service._poll()
    assert managed_service.handoffs[0].credentials == credentials


def test_unexpected_secret_reader_failure_is_redacted_and_reported_as_failed(
    managed_service, mock_bloodhound_api, caplog
):
    state = mock_bloodhound_api.app.state

    def retrieve(*, SecretId):
        state.managed_events.append("retrieve")
        raise RuntimeError("private-credential-value")

    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=retrieve)
    )
    with caplog.at_level(logging.DEBUG):
        managed_service._poll()

    assert state.managed_events == ["claim", "retrieve", "end"]
    assert state.collector_job_end_requests[0]["body"] == {
        "outcome": "failed",
        "failure_message": "Credential validation failed",
    }
    assert managed_service.handoffs == []
    assert all(
        "private-credential-value" not in repr(r.__dict__) for r in caplog.records
    )
    assert "private-credential-value" not in caplog.text


def test_managed_scheduler_logs_failure_once_and_retries_reporting_next_poll(
    managed_service, mock_bloodhound_api, monkeypatch, caplog
):
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response["secret_key_id"] = None
    original_end = managed_service.client.end_managed_job
    reports = []
    pending_at_sleep = []

    def end_job(*args, **kwargs):
        reports.append((args, kwargs))
        if len(reports) == 1:
            raise requests.Timeout("raw-provider-secret")
        return original_end(*args, **kwargs)

    def next_poll(seconds):
        assert seconds == managed_service.interval
        pending_at_sleep.append(managed_service._pending_managed_failure)
        if len(pending_at_sleep) == 2:
            managed_service.exit = True

    monkeypatch.setattr(managed_service.client, "end_managed_job", end_job)
    monkeypatch.setattr(scheduler_service.time, "sleep", next_poll)
    monkeypatch.setattr(scheduler_service.signal, "signal", lambda *args: None)

    with caplog.at_level(logging.DEBUG):
        managed_service.start()

    assert reports == [reports[0], reports[0]]
    assert pending_at_sleep == [state.collector_job_claim_response["id"], None]
    assert (
        len(state.collector_job_queue_requests)
        == len(state.collector_job_claim_requests)
        == 1
    )
    assert managed_service.handoffs == []
    assert [
        r.getMessage() for r in caplog.records if "poll failed" in r.getMessage()
    ] == ["Managed scheduler poll failed; retrying next poll."]
    assert not any(
        r.getMessage() == "Managed collector job processing failed."
        for r in caplog.records
    )
    assert "raw-provider-secret" not in caplog.text
    assert all("raw-provider-secret" not in repr(r.__dict__) for r in caplog.records)
