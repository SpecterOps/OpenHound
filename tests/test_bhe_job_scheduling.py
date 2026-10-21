import base64
import gzip
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

import boto3
import dlt
import pytest
import requests
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from openhound.core.clients import bloodhound, bloodhound_enterprise
from openhound.core.clients.aws_secrets_manager import (
    AWSSecretsManager,
    SecretPermissionError,
)
from openhound.core.clients.bhe_credentials import AWSBHECredentials, BHECredentials
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
from openhound.scheduler.aws_profile import AWSAccessKeyCredentials
from openhound.scheduler.configuration import ManagedConfigurationError
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
CLAIMED_PARAMS = {"sources": {"source": {"random": {"node_count": 4}}}}
COLLECTOR_SECRETS = {
    "sources": {
        "source": {"random": {"credentials": {"token": "private-credential-value"}}}
    }
}


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
    app.state.collector_job_stateful_claims = False
    app.state.collector_job_active_id = None
    app.state.collector_job_lost_claim_responses = 0
    app.state.collector_job_claim_response = load_json(
        "collector_jobs_available_with_job.json"
    )["data"]["jobs"][0]
    app.state.managed_events = []
    app.state.collector_job_start_requests = []
    app.state.collector_job_start_statuses = []
    app.state.collector_job_heartbeat_requests = []
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
    app.state.management_available_requests = 0
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
    app.state.managed_ingest_requests = []
    app.state.managed_ingested_nodes = 0
    app.state.managed_ingested_edges = 0

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

    @app.get("/api/v2/collie-job-queue/available")
    async def collector_job_queue(request: Request):
        query_params = dict(request.query_params)
        query_params["sort_by"] = request.query_params.getlist("sort_by")
        app.state.collector_job_queue_requests.append(query_params)
        if app.state.collector_job_queue_error_status is not None:
            return Response(status_code=app.state.collector_job_queue_error_status)
        if app.state.collector_job_stateful_claims:
            jobs = [
                job
                for job in app.state.collector_job_queue_response["data"]["jobs"]
                if job["id"] != app.state.collector_job_active_id
            ]
            return {
                "count": len(jobs),
                "skip": 0,
                "limit": 1,
                "data": {"jobs": jobs[:1]},
            }
        return app.state.collector_job_queue_response

    @app.post("/api/v2/jobs/start")
    async def start_job(body: dict):
        app.state.job_started = True
        app.state.start_payload = body
        return load_json("job_start.json")

    @app.post("/api/v2/collie-job-queue/{job_id}/claim")
    async def claim_managed_job(job_id: str, request: Request):
        app.state.managed_events.append("claim")
        app.state.collector_job_claim_requests.append(
            {"job_id": job_id, "body": await request.body()}
        )
        if app.state.collector_job_claim_statuses:
            status = app.state.collector_job_claim_statuses.pop(0)
            if status != 200:
                return Response("raw-provider-secret", status_code=status)
        if app.state.collector_job_stateful_claims:
            if app.state.collector_job_active_id not in (None, job_id):
                return Response("raw-provider-secret", status_code=409)
            if (
                app.state.collector_job_active_id == job_id
                and app.state.collector_job_claim_response["status"] == "running"
            ):
                return Response("raw-provider-secret", status_code=409)
            app.state.collector_job_active_id = job_id
            if app.state.collector_job_lost_claim_responses:
                app.state.collector_job_lost_claim_responses -= 1
                # BHE has committed the assignment, but its response is lost.
                raise requests.Timeout("raw-provider-secret")
        return {"data": {"job": app.state.collector_job_claim_response}}

    @app.post("/api/v2/jobs/end")
    async def end_job(body: dict):
        app.state.job_ended = True
        app.state.end_payload = body
        return load_json("job_end.json")

    @app.post("/api/v2/collie-job-queue/{job_id}/end")
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

    @app.post("/api/v2/collie-job-queue/{job_id}/start")
    async def start_collector_job(job_id: str, request: Request):
        app.state.collector_job_start_requests.append((job_id, await request.body()))
        if app.state.collector_job_start_statuses:
            status = app.state.collector_job_start_statuses.pop(0)
            if status != 200:
                return Response("raw-provider-secret", status_code=status)
        if app.state.collector_job_stateful_claims:
            if app.state.collector_job_active_id != job_id:
                return Response("raw-provider-secret", status_code=404)
            job = app.state.collector_job_claim_response
            if job["status"] != "running":
                if job["status"] != "claimed" or datetime.fromisoformat(
                    job["claim_expires_at"]
                ) <= datetime.now(UTC):
                    return Response("raw-provider-secret", status_code=409)
                job["status"] = "running"
                job["claim_expires_at"] = (
                    datetime.now(UTC) + timedelta(minutes=15)
                ).isoformat()
        return Response(status_code=200)

    @app.post("/api/v2/collie-job-queue/{job_id}/heartbeat")
    async def heartbeat_collector_job(job_id: str, request: Request):
        app.state.collector_job_heartbeat_requests.append(
            (job_id, await request.body())
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

    @app.post("/api/v2/collie-job-queue/{job_id}/ingest")
    async def ingest_managed_job(job_id: str, request: Request):
        assert not app.state.collector_job_end_requests
        assert request.headers["content-type"] == "application/json"
        graph = Graph.model_validate_json(await request.body())
        app.state.managed_ingest_requests.append(job_id)
        app.state.managed_ingested_nodes += len(graph.graph.nodes)
        app.state.managed_ingested_edges += len(graph.graph.edges)
        return Response(status_code=202)

    @app.put("/api/v2/clients/update")
    async def update_client(body: dict):
        app.state.client_update_payload = body
        return {"status": "success"}

    @app.get("/api/v2/clients/management/available")
    async def management_available():
        app.state.management_available_requests += 1
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
            self._processes = {}

        def submit(self, *args, **kwargs):
            future = Future()
            self.submitted.append((args, kwargs, future))
            return future

        def shutdown(self, *args, **kwargs):
            return None

    def mock_request(method, url, **kwargs):
        body = kwargs.get("data")
        if body is not None and hasattr(body, "read"):
            kwargs["data"] = body.read()
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


@pytest.mark.parametrize("managed", [False, True])
def test_scheduler_start_routes_requests_by_mode(
    mock_service, mock_bloodhound_api, monkeypatch, managed
):
    mock_service.managed = managed
    state = mock_bloodhound_api.app.state
    monkeypatch.setattr(scheduler_service.signal, "signal", lambda *args: None)

    def next_poll(seconds):
        assert seconds == mock_service.interval
        mock_service.exit = True
        if mock_service.future is not None:
            mock_service.future.set_result(Result({}, mock_service.job_running))

    monkeypatch.setattr(scheduler_service.time, "sleep", next_poll)

    mock_service.start()

    if managed:
        assert state.client_update_payload is None
        assert state.management_available_requests == 0
        assert len(state.collector_job_queue_requests) == 1
        assert state.jobs_available_requests == state.jobs_current_requests == 0
    else:
        assert state.client_update_payload is not None
        assert state.management_available_requests == 1
        assert state.collector_job_queue_requests == []
        assert state.jobs_available_requests == 1


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

    client.request(method="GET", path="/test", timeout=(10, 20))

    assert refreshes == [True]
    assert len(requests) == 2
    assert requests[0]["headers"]["Authorization"] == "bhesignature old-id"
    assert requests[1]["headers"]["Authorization"] == "bhesignature new-id"
    assert [request["timeout"] for request in requests] == [(10, 20), (10, 20)]
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


def test_start_and_heartbeat_collector_job_use_bodyless_requests(
    mock_service, mock_bloodhound_api
):
    job_id = "e0d43dbd-2cdf-4d68-9a81-2f95cd58a92f"
    mock_service.client.start_collector_job(job_id)
    mock_service.client.heartbeat_collector_job(job_id)

    assert mock_bloodhound_api.app.state.collector_job_start_requests == [(job_id, b"")]
    assert mock_bloodhound_api.app.state.collector_job_heartbeat_requests == [
        (job_id, b"")
    ]


def test_start_and_heartbeat_collector_job_use_bounded_timeouts(
    mock_service, monkeypatch
):
    requests_sent = []
    monkeypatch.setattr(
        mock_service.client,
        "request",
        lambda *args, **kwargs: requests_sent.append(kwargs),
    )

    mock_service.client.start_collector_job("collector-job-123")
    mock_service.client.heartbeat_collector_job("collector-job-123")

    assert [request["timeout"] for request in requests_sent] == [
        (
            bloodhound_enterprise.MANAGED_JOB_LIFECYCLE_CONNECT_TIMEOUT_SECONDS,
            bloodhound_enterprise.MANAGED_JOB_LIFECYCLE_READ_TIMEOUT_SECONDS,
        )
    ] * 2


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
        == "/api/v2/collie-job-queue/collector-job-123/end"
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
    assert isinstance(job.id, int)
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
        {
            "limit": "1",
            "job_key": "eq:openhound-faker",
            "sort_by": ["-priority", "created_at"],
        }
    ]
    assert mock_bloodhound_api.app.state.jobs_available_requests == 0


def test_collector_job_queue_response_parses_contract_fields(
    mock_service, mock_bloodhound_api
):
    payload = load_json("collector_jobs_available_with_jobs.json")
    mock_bloodhound_api.app.state.collector_job_queue_response = payload

    response = mock_service.client.available_collector_jobs("openhound-faker")
    job = response.data.jobs[0]

    assert response.count == 2
    assert response.skip == 0
    assert response.limit == 2
    assert job == CollectorJob.model_validate(payload["data"]["jobs"][0])
    assert job.id == UUID("11111111-1111-1111-1111-111111111111")
    assert job.job_schedule_id is None
    assert job.job_profile_id is None
    assert job.job_type_id == 7
    assert isinstance(job.job_type_id, int)
    assert job.job_key == "openhound-faker"
    assert job.version == "v2"
    assert job.params == {
        "sources": {
            "source": {
                "random": {
                    "domains": ["example.com", "example.org"],
                    "include_deleted": False,
                    "nested": {"depth": 2},
                }
            }
        }
    }
    assert job.scope_client_id is None
    assert job.external_secret_key_id is None
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

    associated_job = response.data.jobs[1]
    assert associated_job.external_secret_key_id == "external-key"
    assert associated_job.id == UUID("22222222-2222-2222-2222-222222222222")
    assert associated_job.job_schedule_id == UUID(
        "44444444-4444-4444-4444-444444444444"
    )
    assert associated_job.job_profile_id == UUID("55555555-5555-5555-5555-555555555555")
    assert associated_job.job_type_id == 8
    assert isinstance(associated_job.job_type_id, int)


@pytest.mark.parametrize(
    "field",
    ["id", "job_schedule_id", "job_profile_id", "scope_client_id", "claimed_by"],
)
def test_collector_job_queue_response_rejects_invalid_uuids(field):
    payload = load_json("collector_jobs_available_with_jobs.json")

    payload["data"]["jobs"][1][field] = "not-a-uuid"

    with pytest.raises(ValueError):
        CollectorJobsAvailable.model_validate(payload)


@pytest.mark.parametrize(
    "field", ["job_schedule_id", "job_profile_id", "scope_client_id", "claimed_by"]
)
def test_collector_job_queue_response_requires_nullable_uuid_fields(field):
    payload = load_json("collector_jobs_available_with_jobs.json")

    del payload["data"]["jobs"][0][field]

    with pytest.raises(ValueError):
        CollectorJobsAvailable.model_validate(payload)


@pytest.mark.parametrize("operation", ["available", "claim"])
@pytest.mark.parametrize("field", ["job_schedule_id", "job_profile_id"])
@pytest.mark.parametrize(
    "value", ["not-a-uuid", 42, "42"], ids=["malformed", "integer", "numeric-string"]
)
def test_managed_queue_clients_reject_invalid_associations(
    mock_service, mock_bloodhound_api, operation, field, value
):
    state = mock_bloodhound_api.app.state
    payload = load_json("collector_jobs_available_with_job.json")
    job = payload["data"]["jobs"][0]
    job[field] = value
    state.collector_job_queue_response = payload
    state.collector_job_claim_response = job

    if operation == "available":
        with pytest.raises(
            RuntimeError, match="^Invalid managed collector queue response$"
        ):
            mock_service.client.available_collector_jobs("openhound-faker")
        assert len(state.collector_job_queue_requests) == 1
    else:
        with pytest.raises(
            RuntimeError, match="^Invalid managed collector claim response$"
        ):
            mock_service.client.claim_managed_job(job["id"])
        assert len(state.collector_job_claim_requests) == 1


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
    secret_requests = []
    monkeypatch.setattr(
        mock_service.secrets_manager, "get_secret", secret_requests.append
    )
    submitted_args = []

    def submit(*args):
        submitted_args.append(args)
        return submitted

    monkeypatch.setattr(mock_service.executor, "submit", submit)

    mock_service._poll()

    assert mock_service.job_running == 123
    assert isinstance(mock_service.job_running, int)
    assert mock_service.future is submitted
    assert submitted_args[0][1:] == ("openhound-faker", 123, None, None)
    assert isinstance(submitted_args[0][2], int)
    assert mock_bloodhound_api.app.state.job_started is True
    assert mock_bloodhound_api.app.state.start_payload == {"id": 123}
    assert mock_bloodhound_api.app.state.collector_job_queue_requests == []
    assert mock_bloodhound_api.app.state.collector_job_claim_requests == []
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []
    assert secret_requests == []


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


def test_scheduler_ingest_opengraph(
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path
):
    """Run the DLT pipeline with the openhound-faker collector + check the amount of ingested nodes + edges"""
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    monkeypatch.setattr(
        scheduler_service.dataflow,
        "Path",
        lambda value: tmp_path if value == "/tmp/openhound" else Path(value),
    )
    monkeypatch.setenv(
        "DESTINATION__BLOODHOUNDENTERPRISE__URL", "http://localhost:8000"
    )
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_KEY", "test-key")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_ID", "test-id")

    result = _subprocess_collect("faker", 123)

    assert result.job_id == 123
    assert mock_bloodhound_api.app.state.ingested_nodes == 1000
    assert mock_bloodhound_api.app.state.ingested_edges == 10000
    assert result.output_path is None
    assert result.data_files == []

    future = Future()
    future.set_result(result)
    mock_service.job_running = 123
    mock_service.future = future
    mock_service._shutdown()

    assert mock_bloodhound_api.app.state.job_ended is True
    assert mock_bloodhound_api.app.state.managed_ingest_requests == []
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []


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
    assert mock_bloodhound_api.app.state.collector_job_queue_requests == []


def test_poll_starts_job_when_no_management_work(
    mock_service, mock_bloodhound_api, monkeypatch
):
    submitted = Future()
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: submitted)

    mock_service._poll()

    assert mock_bloodhound_api.app.state.job_started is True


@pytest.mark.parametrize("managed", [False, True])
def test_poll_still_checks_jobs_when_management_endpoint_fails(
    managed_service, mock_bloodhound_api, monkeypatch, caplog, managed
):
    managed_service.managed = managed

    def fail_management():
        raise BloodHoundHTTPError("management unavailable", 503)

    monkeypatch.setattr(managed_service, "check_management", fail_management)
    with caplog.at_level(logging.ERROR):
        managed_service._poll()

    state = mock_bloodhound_api.app.state
    if managed:
        assert state.managed_events == ["claim", "retrieve", "start_handoff"]
    else:
        assert state.job_started is True
    assert ("management unavailable" in caplog.text) is (not managed)
    assert any(record.exc_info is not None for record in caplog.records) is (
        not managed
    )


def test_poll_does_not_start_a_job_when_management_work_fails(
    mock_service, mock_bloodhound_api, monkeypatch
):
    mock_bloodhound_api.app.state.management_operations = [_support_bundle_operation()]

    def fail(operation):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(mock_service, "_send_support_bundle", fail)

    mock_service._poll()

    assert mock_bloodhound_api.app.state.job_started is False
    assert mock_bloodhound_api.app.state.collector_job_queue_requests == []


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
    assert f"{failure_point} failed" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)


def test_send_support_bundle_retries_transient_part_upload_failure(
    mock_service, monkeypatch, tmp_path, caplog
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
                raise BloodHoundHTTPError("temporary upload failure", 503)
        return original_request(method, path, **kwargs)

    monkeypatch.setattr(mock_service.client, "request", flaky_request)
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    mock_service._send_support_bundle(operation)

    assert attempts == 3
    assert delays == [2, 2]
    assert "temporary upload failure" in caplog.text
    assert any(
        record.levelno == logging.WARNING and record.exc_info is not None
        for record in caplog.records
    )


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
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path, caplog
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
    assert "temporarily unavailable" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)


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
    state.collector_job_queue_response["data"]["jobs"][0]["secret_key_id"] = (
        "stale-reference"
    )
    claimed_at = datetime.now(UTC)
    state.collector_job_claim_response.update(
        secret_key_id="private-secret-reference",
        status="claimed",
        params=CLAIMED_PARAMS,
        claimed_by="33333333-3333-3333-3333-333333333333",
        claimed_at=claimed_at.isoformat(),
        claim_expires_at=(claimed_at + timedelta(minutes=15)).isoformat(),
    )
    mock_service.managed = True
    mock_service.handoffs = []
    mock_service.secret_requests = []

    def get_secret_value(*, SecretId):
        state.managed_events.append("retrieve")
        mock_service.secret_requests.append(SecretId)
        return {"SecretString": json.dumps(COLLECTOR_SECRETS)}

    mock_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=get_secret_value)
    )

    def start_handoff(prepared):
        state.managed_events.append("start_handoff")
        mock_service.handoffs.append(prepared)

    mock_service.managed_job_runner = start_handoff
    return mock_service


@pytest.mark.parametrize("has_schedule", [False, True], ids=["no-schedule", "schedule"])
@pytest.mark.parametrize("has_profile", [False, True], ids=["no-profile", "profile"])
def test_claim_request_is_bodyless_and_parses_data_job(
    managed_service, mock_bloodhound_api, monkeypatch, has_schedule, has_profile
):
    state = mock_bloodhound_api.app.state
    associated_job = load_json("collector_jobs_available_with_jobs.json")["data"][
        "jobs"
    ][1]
    schedule_id = associated_job["job_schedule_id"] if has_schedule else None
    profile_id = associated_job["job_profile_id"] if has_profile else None
    state.collector_job_claim_response.update(
        job_schedule_id=schedule_id, job_profile_id=profile_id
    )
    captured = []
    original = managed_service.client.request

    def request(**kwargs):
        captured.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(managed_service.client, "request", request)
    job_id = mock_bloodhound_api.app.state.collector_job_claim_response["id"]
    claimed = managed_service.client.claim_managed_job(job_id)

    assert claimed == CollectorJob.model_validate(
        mock_bloodhound_api.app.state.collector_job_claim_response
    )
    assert claimed.id == UUID(job_id)
    assert claimed.job_schedule_id == (UUID(schedule_id) if schedule_id else None)
    assert claimed.job_profile_id == (UUID(profile_id) if profile_id else None)
    assert claimed.job_type_id == 7
    assert isinstance(claimed.job_type_id, int)
    assert claimed.version == "v2"
    assert claimed.params == CLAIMED_PARAMS
    assert captured == [
        {
            "method": "POST",
            "path": f"/api/v2/collie-job-queue/{job_id}/claim",
            "timeout": (
                bloodhound_enterprise.MANAGED_JOB_CLAIM_CONNECT_TIMEOUT_SECONDS,
                bloodhound_enterprise.MANAGED_JOB_CLAIM_READ_TIMEOUT_SECONDS,
            ),
        }
    ]
    assert mock_bloodhound_api.app.state.collector_job_claim_requests == [
        {"job_id": job_id, "body": b""}
    ]


def test_managed_poll_claims_retrieves_and_hands_off_authoritative_job(
    managed_service, mock_bloodhound_api, caplog
):
    with caplog.at_level(logging.DEBUG):
        managed_service._poll()

    state = mock_bloodhound_api.app.state
    assert state.managed_events == ["claim", "retrieve", "start_handoff"]
    assert managed_service.secret_requests == ["private-secret-reference"]
    (prepared,) = managed_service.handoffs
    assert prepared.job.params == CLAIMED_PARAMS
    assert prepared.job.status == "claimed"
    assert prepared.credentials == COLLECTOR_SECRETS
    assert state.job_started is False
    assert state.jobs_available_requests == state.jobs_current_requests == 0
    assert state.collector_job_end_requests == []
    for message in ("Job successfully picked up", "Credential retrieval valid"):
        assert any(
            r.levelno == logging.INFO and r.getMessage() == message
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


def test_managed_job_retrieves_full_hierarchy_secret_before_dlt_resolution(
    managed_service, mock_bloodhound_api, monkeypatch, caplog
):
    service = managed_service
    bhe_credentials = (service.client.token_id, service.client.token_key)
    state = mock_bloodhound_api.app.state
    data_dir = Path(__file__).parent / "test_data"
    params = json.loads((data_dir / "managed_aws_configuration.json").read_text())
    secret_document = (data_dir / "managed_aws_secrets.json").read_text()
    service.collector_name = "aws"
    state.collector_job_queue_response["data"]["jobs"][0]["job_key"] = "aws"
    state.collector_job_claim_response.update(job_key="aws", params=params)
    requests_seen = []

    def get_secret_value(*, SecretId):
        requests_seen.append(SecretId)
        return {"SecretString": secret_document}

    service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=get_secret_value)
    )
    service.managed_job_runner = service._run_prepared_managed_job
    collector = SimpleNamespace(
        name="aws", dlt_source=SimpleNamespace(), package_version="test", metadata=None
    )
    monkeypatch.setattr(
        scheduler_service.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[collector]),
    )
    resolved = {}

    def collect(extension, output_path):
        resolved.update(
            profiles=dlt.secrets.get(
                "sources.source.aws.credentials.profile_names", list
            ),
            region=dlt.secrets.get("sources.source.aws.credentials.region_name", str),
            max_objects=dlt.config.get(
                "sources.source.aws.bucket_policy.max_objects", int
            ),
        )
        return {"collect": []}

    monkeypatch.setattr(scheduler_service.dataflow, "pipeline", collect)

    def submit(function, *args):
        future = Future()
        future.set_result(function(*args))
        return future

    monkeypatch.setattr(service.executor, "submit", submit)
    with caplog.at_level(logging.DEBUG):
        service._poll()
        service._handle_completed_job(service.future)

    assert requests_seen == ["private-secret-reference"]
    assert resolved == {
        "profiles": ["retrieved-profile-a", "retrieved-profile-b"],
        "region": "us-west-2",
        "max_objects": 50,
    }
    assert (service.client.token_id, service.client.token_key) == bhe_credentials
    assert state.collector_job_end_requests[0]["body"] == {"outcome": "succeeded"}
    for credential in ("retrieved-profile-a", "retrieved-profile-b"):
        assert credential not in caplog.text


AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.fixture
def aws_managed_service(managed_service, mock_bloodhound_api):
    state = mock_bloodhound_api.app.state
    managed_service.collector_name = "aws"
    state.collector_job_queue_response["data"]["jobs"][0].update(
        job_key="aws", external_secret_key_id="stale-discovery-key"
    )
    state.collector_job_claim_response.update(
        job_key="aws",
        external_secret_key_id=AWS_ACCESS_KEY,
        params={
            "sources": {
                "source": {"aws": {"credentials": {"region_name": "us-west-2"}}}
            }
        },
    )
    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(
            get_secret_value=lambda **kwargs: {"SecretString": AWS_SECRET_KEY}
        )
    )
    managed_service.managed_job_runner = managed_service._run_prepared_managed_job
    return managed_service


@pytest.mark.parametrize(
    "failed", [False, True], ids=["successful-collection", "failed-collection"]
)
@pytest.mark.parametrize(
    "binary_secret", [False, True], ids=["secret-string", "bhe-utf8-binary"]
)
def test_active_aws_poll_uses_claimed_key_and_raw_secret_in_worker_adapter(
    aws_managed_service,
    mock_bloodhound_api,
    resolve_aws_settings,
    failed,
    binary_secret,
    monkeypatch,
    tmp_path,
    caplog,
):
    scheduler = aws_managed_service
    state = mock_bloodhound_api.app.state
    # Secrets Manager continues using the scheduler's identity. Only the worker
    # receives the customer's pair and changes its SDK file/config providers.
    client = boto3.Session(
        aws_access_key_id="scheduler-key",
        aws_secret_access_key="scheduler-secret",
        region_name="us-east-1",
    ).client("secretsmanager")
    scheduler.secrets_manager = AWSSecretsManager(client)
    handoffs, seen_files = [], []
    runner = scheduler.managed_job_runner

    def handoff(prepared):
        handoffs.append(prepared)
        assert prepared.job.external_secret_key_id == AWS_ACCESS_KEY
        assert isinstance(prepared.credentials, AWSAccessKeyCredentials)
        assert prepared.credentials.secret_access_key == AWS_SECRET_KEY
        runner(prepared)

    scheduler.managed_job_runner = handoff

    def load_collectors():
        assert state.collector_job_start_requests == [
            (state.collector_job_claim_response["id"], b"")
        ]
        model = resolve_aws_settings()["credentials"]
        assert model.profile_name.startswith("openhound-job-")
        assert model.region_name == "us-west-2"
        credentials = model.session.get_credentials()
        assert credentials.method == "shared-credentials-file"
        frozen = credentials.get_frozen_credentials()
        assert (frozen.access_key, frozen.secret_key, frozen.token) == (
            AWS_ACCESS_KEY,
            AWS_SECRET_KEY,
            None,
        )
        seen_files.append(Path(os.environ["AWS_SHARED_CREDENTIALS_FILE"]))
        return SimpleNamespace(
            collectors=[
                SimpleNamespace(
                    name="aws",
                    dlt_source=object(),
                    package_version="test",
                    metadata=None,
                )
            ]
        )

    monkeypatch.setattr(
        scheduler_service.CollectorManager, "from_entrypoint", load_collectors
    )

    def collect(extension, output_path):
        assert not seen_files[0].is_relative_to(output_path)
        if failed:
            logging.getLogger("openhound_aws.auth").error(
                "Provider failure: %s", AWS_SECRET_KEY
            )
            raise RuntimeError(AWS_SECRET_KEY)
        (output_path / "graph.json").write_text(Graph(graph={}).model_dump_json())
        return {"collect": ["account"], "convert": ["graph"]}

    monkeypatch.setattr(scheduler_service.dataflow, "pipeline", collect)

    def submit(function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as error:  # noqa: BLE001 - emulate ProcessPoolExecutor
            future.set_exception(error)
        return future

    monkeypatch.setattr(scheduler.executor, "submit", submit)
    environment = dict(os.environ)
    with Stubber(client) as stubber, caplog.at_level(logging.DEBUG):
        stubber.add_response(
            "get_secret_value",
            (
                {"SecretBinary": AWS_SECRET_KEY.encode()}
                if binary_secret
                else {"SecretString": AWS_SECRET_KEY}
            ),
            {"SecretId": "private-secret-reference"},
        )
        scheduler._poll()
        directory = scheduler._aws_profile_workspace.directory
        assert not seen_files[0].exists()  # Worker cleanup precedes parent reporting.
        assert directory.exists()
        if not failed:
            result = scheduler.future.result()
            assert AWS_SECRET_KEY not in repr(result)
            assert all(
                AWS_SECRET_KEY not in path.read_text() for path in result.data_files
            )
        scheduler._handle_completed_job(scheduler.future)
        stubber.assert_no_pending_responses()
    assert len(handoffs) == 1
    assert not directory.exists()
    assert scheduler._aws_profile_workspace is None
    assert dict(os.environ) == environment
    assert client._request_signer._credentials.access_key == "scheduler-key"
    assert state.collector_job_end_requests[0]["body"]["outcome"] == (
        "failed" if failed else "succeeded"
    )
    assert len(state.managed_ingest_requests) == (0 if failed else 1)
    assert AWS_SECRET_KEY not in repr(state.collector_job_end_requests)
    assert AWS_SECRET_KEY not in caplog.text
    assert AWS_SECRET_KEY not in repr(handoffs)
    assert all(AWS_SECRET_KEY not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.parametrize(
    "field,value",
    [
        ("external_secret_key_id", None),
        ("external_secret_key_id", ""),
        ("external_secret_key_id", "ASIAIOSFODNN7EXAMPLE"),
        ("external_secret_key_id", AWS_ACCESS_KEY + "\n"),
        ("external_secret_key_id", "not-an-access-key"),
        ("secret_key_id", None),
        ("secret_key_id", ""),
        ("secret_key_id", "  "),
        ("secret_key_id", " private-secret-reference"),
        ("secret_key_id", "reference\nother"),
    ],
)
def test_invalid_aws_metadata_fails_before_start_without_runtime_fallback(
    aws_managed_service, mock_bloodhound_api, field, value, monkeypatch, caplog
):
    monkeypatch.setenv("AWS_PROFILE", "local-profile")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "scheduler-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "scheduler-secret")
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response[field] = value
    with caplog.at_level(logging.DEBUG):
        aws_managed_service._poll()
    assert state.collector_job_start_requests == []
    assert aws_managed_service.executor.submitted == []
    assert aws_managed_service._aws_profile_workspace is None
    assert state.collector_job_end_requests[0]["body"]["outcome"] == "failed"
    assert AWS_SECRET_KEY not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"SecretString": ""},
        {"SecretString": " " + AWS_SECRET_KEY},
        {"SecretString": AWS_SECRET_KEY + "\n"},
        {"SecretString": AWS_SECRET_KEY[:-1]},
        {"SecretString": "x" * 41},
        {"SecretString": "%" * 40},
        {"SecretString": json.dumps(AWS_SECRET_KEY)},
        {"SecretString": '["key"]'},
        {"SecretString": json.dumps({"aws_secret_access_key": AWS_SECRET_KEY})},
        {"SecretString": json.dumps(COLLECTOR_SECRETS)},
        {"SecretBinary": b"\xff\xfe"},
    ],
)
def test_invalid_raw_aws_payloads_are_not_trimmed_wrapped_or_treated_as_documents(
    aws_managed_service, mock_bloodhound_api, response, caplog
):
    aws_managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=lambda **kwargs: response)
    )
    with caplog.at_level(logging.DEBUG):
        aws_managed_service._poll()
    state = mock_bloodhound_api.app.state
    assert state.collector_job_start_requests == []
    assert aws_managed_service.executor.submitted == []
    assert state.collector_job_end_requests[0]["body"]["outcome"] == "failed"
    assert AWS_SECRET_KEY not in repr(state.collector_job_end_requests)
    assert AWS_SECRET_KEY not in caplog.text


@pytest.mark.parametrize(
    "options",
    [
        {"profile_name": "local"},
        {"profile_names": ["local"]},
        {"profile_names": []},
        {"auth_mode": "workload"},
        {"authentication_mode": "profile"},
        {"aws_session_token": "unsupported-token"},
        {"aws_access_key_id": AWS_ACCESS_KEY},
        {"source_account": "999999999999"},
        {"org_role_name": ""},
        {"org_role_name": 123},
    ],
)
def test_raw_aws_authentication_conflicts_fail_before_start(
    aws_managed_service, mock_bloodhound_api, options
):
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response["params"] = {
        "sources": {"source": {"aws": {"credentials": options}}}
    }
    aws_managed_service._poll()
    assert state.collector_job_start_requests == []
    assert aws_managed_service.executor.submitted == []
    assert state.collector_job_end_requests[0]["body"]["outcome"] == "failed"


def test_missing_new_bhe_job_field_rejects_claim_before_start(
    aws_managed_service, mock_bloodhound_api
):
    state = mock_bloodhound_api.app.state
    del state.collector_job_claim_response["external_secret_key_id"]
    with pytest.raises(ManagedJobProcessingError):
        aws_managed_service._poll()
    assert state.collector_job_start_requests == []
    assert aws_managed_service.executor.submitted == []


def test_raw_aws_key_secret_not_reinterpreted_for_other_collectors(
    managed_service, mock_bloodhound_api
):
    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(
            get_secret_value=lambda **kwargs: {"SecretString": AWS_SECRET_KEY}
        )
    )
    managed_service._poll()
    assert managed_service.handoffs == []
    assert (
        mock_bloodhound_api.app.state.collector_job_end_requests[0]["body"]["outcome"]
        == "failed"
    )


def test_aws_missing_secret_reports_sanitized_failure_before_start(
    aws_managed_service, mock_bloodhound_api, caplog
):
    def missing_secret(**kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Secret does not exist",
                }
            },
            "GetSecretValue",
        )

    aws_managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=missing_secret)
    )
    with caplog.at_level(logging.DEBUG):
        aws_managed_service._poll()
    state = mock_bloodhound_api.app.state
    assert state.collector_job_start_requests == []
    assert aws_managed_service.executor.submitted == []
    assert state.collector_job_end_requests[0]["body"] == {
        "outcome": "failed",
        "failure_message": "Credential secret not found",
    }
    assert AWS_SECRET_KEY not in caplog.text


@pytest.mark.parametrize(
    "params",
    [
        {"credentials": {"profile_names": ["other"]}},
        {"sources": {"credentials": {"org_role_name": "OtherRole"}}},
        {"sources": {"source": {"credentials": {"profile_name": "other"}}}},
        {"sources": {"aws": {"credentials": {"profile_names": ["other"]}}}},
        {"profile_name": "flat-profile"},
        {"aws_collector": {"credentials": {"profile_names": ["other"]}}},
        {
            "aws_collector": {
                "sources": {
                    "source": {"aws": {"credentials": {"profile_name": "other"}}}
                }
            }
        },
    ],
)
def test_raw_aws_rejects_ambiguous_authentication_layouts_before_start(
    aws_managed_service, mock_bloodhound_api, params
):
    state = mock_bloodhound_api.app.state
    state.collector_job_claim_response["params"] = params
    aws_managed_service._poll()
    assert state.collector_job_start_requests == []
    assert aws_managed_service.executor.submitted == []
    assert state.collector_job_end_requests[0]["body"]["outcome"] == "failed"


@pytest.mark.parametrize(
    "association_index", [0, 1], ids=["null-associations", "uuid-associations"]
)
def test_managed_poll_starts_claimed_job_and_tracks_worker(
    managed_service, mock_bloodhound_api, monkeypatch, association_index
):
    service = managed_service
    state = mock_bloodhound_api.app.state
    associated_job = load_json("collector_jobs_available_with_jobs.json")["data"][
        "jobs"
    ][association_index]
    for field in ("job_schedule_id", "job_profile_id"):
        state.collector_job_queue_response["data"]["jobs"][0][field] = associated_job[
            field
        ]
        state.collector_job_claim_response[field] = associated_job[field]
    service.managed_job_runner = service._run_prepared_managed_job
    future = Future()
    submitted = []

    def submit(*args):
        assert state.collector_job_start_requests == [
            (state.collector_job_claim_response["id"], b"")
        ]
        submitted.append(args)
        return future

    monkeypatch.setattr(service.executor, "submit", submit)

    service._poll()

    job_id = state.collector_job_claim_response["id"]
    assert state.managed_events == ["claim", "retrieve"]
    assert submitted == [
        (
            scheduler_service._subprocess_collect,
            "openhound-faker",
            job_id,
            CLAIMED_PARAMS,
            COLLECTOR_SECRETS,
            True,
        )
    ]
    assert service.job_running == service.managed_job_id == job_id
    assert service.future is future

    service.next_heartbeat_at = 0
    service._poll()
    assert len(state.collector_job_claim_requests) == 1
    assert len(state.collector_job_queue_requests) == 1
    assert state.collector_job_heartbeat_requests == []

    state.collector_job_queue_response = load_json(
        "collector_jobs_available_empty.json"
    )
    future.set_result(Result(results={}, job_id=job_id))
    service._poll()
    assert state.collector_job_end_requests == [
        {"job_id": job_id, "body": {"outcome": "succeeded"}}
    ]
    assert service.job_running is service.future is None


@pytest.mark.parametrize(
    "committed", [False, True], ids=["before-start", "lost-response"]
)
@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout("raw-provider-secret"),
        requests.ConnectionError("raw-provider-secret"),
        BloodHoundHTTPError("raw-provider-secret", 503),
    ],
    ids=["timeout", "connection-error", "http-503"],
)
def test_managed_start_failure_retries_prepared_job_before_discovery(
    stateful_managed_service, mock_bloodhound_api, monkeypatch, caplog, committed, error
):
    service = stateful_managed_service
    state = mock_bloodhound_api.app.state
    state.collector_job_lost_claim_responses = 0
    service.managed_job_runner = service._run_prepared_managed_job
    job_id = state.collector_job_claim_response["id"]
    original = service.client.request
    starts = []

    def flaky_request(**kwargs):
        if kwargs["path"].endswith("/start"):
            assert service._pending_managed_claim == job_id
            assert service._pending_managed_start is not None
            starts.append(kwargs["path"])
            if len(starts) <= 3:
                if committed:
                    original(**kwargs)
                raise error
        return original(**kwargs)

    monkeypatch.setattr(service.client, "request", flaky_request)

    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            with pytest.raises(ManagedJobProcessingError) as raised:
                service._poll()
            assert "raw-provider-secret" not in "".join(
                traceback.format_exception(raised.value)
            )
            assert service._pending_managed_claim == job_id
            assert service._pending_managed_start is not None
            assert str(service._pending_managed_start.job.id) == job_id
            assert "private-credential-value" not in repr(
                service._pending_managed_start
            )
            assert (
                service.future is service.job_running is service.managed_job_id is None
            )
            assert service.executor.submitted == []
            assert state.collector_job_end_requests == []
            assert state.collector_job_claim_response["status"] == (
                "running" if committed else "claimed"
            )

        # Recovery must repeat start, not claim: BHE rejects re-claim of a running job.
        service._poll()

    assert starts == [f"/api/v2/collie-job-queue/{job_id}/start"] * 4
    assert state.collector_job_claim_requests == [{"job_id": job_id, "body": b""}]
    assert len(state.collector_job_queue_requests) == 1
    assert state.management_available_requests == 0
    assert service.secret_requests == ["private-secret-reference"]
    assert service._pending_managed_claim is service._pending_managed_start is None
    assert service.job_running == service.managed_job_id == job_id
    assert state.collector_job_claim_response["status"] == "running"
    (submission,) = service.executor.submitted
    args, _, future = submission
    assert args[1:] == (
        "openhound-faker",
        job_id,
        CLAIMED_PARAMS,
        COLLECTOR_SECRETS,
        True,
    )
    assert service.future is future
    for forbidden in ("raw-provider-secret", "private-credential-value"):
        assert forbidden not in caplog.text
        assert all(forbidden not in repr(record.__dict__) for record in caplog.records)

    service._poll()
    assert len(starts) == 4
    assert len(service.executor.submitted) == 1

    state.collector_job_queue_response = load_json(
        "collector_jobs_available_empty.json"
    )
    future.set_result(Result(results={}, job_id=job_id))
    service._poll()
    assert state.collector_job_end_requests == [
        {"job_id": job_id, "body": {"outcome": "succeeded"}}
    ]
    assert service.job_running is service.future is None


@pytest.mark.parametrize("status", [404, 409])
@pytest.mark.parametrize("lost_response_first", [False, True])
def test_managed_start_rejection_clears_preparation_and_allows_next_job(
    stateful_managed_service,
    mock_bloodhound_api,
    monkeypatch,
    status,
    lost_response_first,
):
    service = stateful_managed_service
    state = mock_bloodhound_api.app.state
    state.collector_job_lost_claim_responses = 0
    service.managed_job_runner = service._run_prepared_managed_job
    original = service.client.start_collector_job

    def lost_response(job_id):
        original(job_id)
        raise requests.Timeout("raw-provider-secret")

    if lost_response_first:
        monkeypatch.setattr(service.client, "start_collector_job", lost_response)
        with pytest.raises(ManagedJobProcessingError):
            service._poll()
        assert service._pending_managed_start is not None

    monkeypatch.setattr(service.client, "start_collector_job", original)
    state.collector_job_start_statuses = [status]
    with pytest.raises(ManagedJobProcessingError):
        service._poll()

    assert service._pending_managed_claim is service._pending_managed_start is None
    assert service.future is service.job_running is service.managed_job_id is None
    assert service.executor.submitted == []
    assert len(state.collector_job_queue_requests) == 1
    assert len(state.collector_job_claim_requests) == 1
    assert state.collector_job_end_requests == []

    # BHE no longer permits this start; discovery can now select another job.
    job_b = state.collector_job_queue_response["data"]["jobs"][1]
    state.collector_job_queue_response["data"]["jobs"] = [job_b]
    state.collector_job_claim_response.update(id=job_b["id"], status="claimed")
    state.collector_job_active_id = None
    service._poll()
    assert len(state.collector_job_queue_requests) == 2
    assert service.job_running == job_b["id"]
    assert len(service.executor.submitted) == 1


def test_managed_expired_claim_start_rejection_clears_preparation(
    stateful_managed_service, mock_bloodhound_api, monkeypatch
):
    service = stateful_managed_service
    state = mock_bloodhound_api.app.state
    state.collector_job_lost_claim_responses = 0
    service.managed_job_runner = service._run_prepared_managed_job
    original = service.client.start_collector_job

    def unavailable(job_id):
        raise requests.Timeout("raw-provider-secret")

    monkeypatch.setattr(service.client, "start_collector_job", unavailable)
    with pytest.raises(ManagedJobProcessingError):
        service._poll()
    assert service._pending_managed_start is not None

    state.collector_job_claim_response["claim_expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    monkeypatch.setattr(service.client, "start_collector_job", original)
    with pytest.raises(ManagedJobProcessingError):
        service._poll()
    assert service._pending_managed_claim is service._pending_managed_start is None
    assert service.executor.submitted == []
    assert state.collector_job_end_requests == []


@pytest.mark.parametrize(
    "document",
    [
        "plain-secret",
        '{"malformed":',
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
                "failure_message": "Credential document is invalid",
            },
        }
    ]
    assert managed_service.handoffs == []
    assert state.job_started is False
    assert "Credential retrieval valid" not in caplog.text
    assert "private-credential-value" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize("reference", [None, "  "])
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
        == "Credential document is invalid"
    )


@pytest.mark.parametrize(
    "error,summary",
    [
        (
            ClientError(
                {
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "The workload role cannot read openhound/job",
                    }
                },
                "GetSecretValue",
            ),
            "AWS secret access denied",
        ),
        (
            ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": "Secret does not exist in this region",
                    }
                },
                "GetSecretValue",
            ),
            "Credential secret not found",
        ),
        (
            RuntimeError("private-credential-value"),
            "AWS secret retrieval failed",
        ),
    ],
    ids=["aws-permission-error", "aws-missing-secret", "unexpected-reader-error"],
)
def test_secret_retrieval_failure_keeps_local_cause_and_reports_safe_summary(
    managed_service, mock_bloodhound_api, caplog, error, summary
):
    state = mock_bloodhound_api.app.state

    def retrieve(*, SecretId):
        state.managed_events.append("retrieve")
        raise error

    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=retrieve)
    )
    with caplog.at_level(logging.DEBUG):
        managed_service._poll()

    assert state.managed_events == ["claim", "retrieve", "end"]
    assert state.collector_job_end_requests[0]["body"] == {
        "outcome": "failed",
        "failure_message": summary,
    }
    assert managed_service.handoffs == []
    assert "Reporting this job failed" in caplog.text
    failure_log = next(
        r for r in caplog.records if "Cannot prepare managed job" in r.getMessage()
    )
    assert failure_log.secret_id == state.collector_job_claim_response["secret_key_id"]
    if isinstance(error, ClientError):
        assert error.response["Error"]["Message"] in caplog.text
        assert "GetSecretValue" in caplog.text
        assert failure_log.getMessage().count(failure_log.secret_id) == 1
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


def test_invalid_claim_response_redacts_parsing_error(
    mock_service, mock_bloodhound_api, caplog
):
    mock_bloodhound_api.app.state.collector_job_claim_response["id"] = (
        "private-credential-value"
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised:
        mock_service.client.claim_managed_job("job-id")
    assert "private-credential-value" not in "".join(
        traceback.format_exception(raised.value)
    )
    assert "private-credential-value" not in caplog.text


@pytest.mark.parametrize("resolved_status", [200, 404])
def test_exhausted_credential_failure_end_is_retained_and_retried_before_discovery(
    managed_service, mock_bloodhound_api, monkeypatch, caplog, resolved_status
):
    state = mock_bloodhound_api.app.state
    from botocore.exceptions import NoCredentialsError

    def unavailable(**kwargs):
        raise NoCredentialsError()

    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(get_secret_value=unavailable)
    )
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
    assert all(
        request["body"]["failure_message"] == "AWS credentials unavailable"
        for request in state.collector_job_end_requests
    )
    assert (
        len(state.collector_job_queue_requests)
        == len(state.collector_job_claim_requests)
        == 1
    )
    assert state.managed_events == ["claim"] + ["end"] * (max_attempts + 1)
    assert managed_service.handoffs == []
    assert delays == [bloodhound_enterprise.MANAGED_JOB_END_RETRY_DELAY_SECONDS] * (
        max_attempts - 1
    )


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


def test_managed_runtime_dependency_fails_before_claim_or_legacy_work(
    managed_service, mock_bloodhound_api
):
    managed_service.managed_job_runner = None
    with pytest.raises(
        ManagedRuntimeUnavailableError, match="Managed collection runtime is required"
    ):
        managed_service._poll()
    state = mock_bloodhound_api.app.state
    assert (
        state.collector_job_claim_requests == state.collector_job_queue_requests == []
    )
    assert state.jobs_available_requests == state.jobs_current_requests == 0
    assert state.job_started is False


def test_mode_config_routes_scheduler_to_managed_dependency(mock_service, monkeypatch):
    monkeypatch.setattr(scheduler_service, "is_managed", lambda: True)
    service = Service(
        bhe_uri="http://localhost:8000",
        token_key="test-key",
        token_id="test-id",
        collector_name="openhound-faker",
    )
    assert service.managed is True
    assert service.managed_job_runner == service._run_prepared_managed_job


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
    credentials = {
        "sources": {
            "source": {"aws": {"arbitrary_field": {"nested": ["value", False, 0, 1.5]}}}
        }
    }
    managed_service.secrets_manager = AWSSecretsManager(
        SimpleNamespace(
            get_secret_value=lambda **kwargs: {"SecretString": json.dumps(credentials)}
        )
    )
    managed_service._poll()
    assert managed_service.handoffs[0].credentials == credentials


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
    ] == [
        f"Managed scheduler poll failed; retrying next poll in {managed_service.interval} seconds."
    ]
    assert not any(
        r.getMessage() == "Managed collector job processing failed."
        for r in caplog.records
    )
    assert "raw-provider-secret" not in caplog.text
    assert all("raw-provider-secret" not in repr(r.__dict__) for r in caplog.records)


@pytest.mark.parametrize(
    "stage,failure,expected",
    [
        ("poll", "region", "AWS_DEFAULT_REGION"),
        ("poll", "provider", "custom-process"),
        ("end", "document", "non-empty token_id"),
    ],
)
def test_managed_401_refresh_failure_is_visible_without_debug_logging(
    managed_service, mock_bloodhound_api, monkeypatch, caplog, stage, failure, expected
):
    from botocore.exceptions import CredentialRetrievalError, NoRegionError

    secret_requests = []

    def retrieve(*, SecretId):
        secret_requests.append(SecretId)
        if failure == "region":
            raise NoRegionError()
        if failure == "provider":
            raise CredentialRetrievalError(
                provider="custom-process", error_msg="private-refresh-token"
            )
        return {"SecretString": '{"token_key":"private-refresh-token"}'}

    provider = AWSBHECredentials(
        "openhound/bhe", AWSSecretsManager(SimpleNamespace(get_secret_value=retrieve))
    )
    managed_service.client._credential_refresh = provider.refresh
    requests_made = []

    def unauthorized(self, **kwargs):
        requests_made.append(kwargs["path"])
        raise BloodHoundHTTPError("API token expired", 401)

    def next_poll(seconds):
        assert seconds == managed_service.interval
        managed_service.exit = True

    monkeypatch.setattr(bloodhound.BloodHound, "request", unauthorized)
    monkeypatch.setattr(managed_service, "_poll_management", lambda: False)
    monkeypatch.setattr(scheduler_service.signal, "signal", lambda *args: None)
    monkeypatch.setattr(scheduler_service.time, "sleep", next_poll)
    if stage == "end":
        managed_service._pending_managed_failure = (
            "11111111-1111-1111-1111-111111111111"
        )

    with caplog.at_level(logging.INFO):
        managed_service.start()

    assert len(requests_made) == 1
    assert secret_requests == ["openhound/bhe"]
    failures = [
        r for r in caplog.records if "Cannot refresh BHE credentials" in r.getMessage()
    ]
    assert len(failures) == 1
    assert expected in failures[0].getMessage()
    assert "openhound/bhe" in failures[0].getMessage()
    assert all(r.exc_info is None for r in caplog.records)
    assert "private-refresh-token" not in repr([r.__dict__ for r in caplog.records])
    assert managed_service.handoffs == []
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []
    assert f"retrying next poll in {managed_service.interval} seconds" in caplog.text
    if stage == "end":
        assert managed_service._pending_managed_failure is not None


@pytest.fixture
def stateful_managed_service(managed_service, mock_bloodhound_api):
    state = mock_bloodhound_api.app.state
    state.collector_job_stateful_claims = True
    job_a = state.collector_job_queue_response["data"]["jobs"][0]
    job_b = {**job_a, "id": "22222222-2222-2222-2222-222222222222"}
    state.collector_job_queue_response["data"]["jobs"].append(job_b)
    state.collector_job_lost_claim_responses = (
        bloodhound_enterprise.MANAGED_JOB_CLAIM_MAX_ATTEMPTS
    )
    return managed_service


def test_committed_claim_with_lost_responses_is_reconciled_before_discovery(
    stateful_managed_service, mock_bloodhound_api, monkeypatch, caplog
):
    service = stateful_managed_service
    state = mock_bloodhound_api.app.state
    job_id = state.collector_job_claim_response["id"]
    delays = []
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", delays.append)

    with caplog.at_level(logging.DEBUG), pytest.raises(ManagedJobProcessingError):
        service._poll()

    assert state.collector_job_active_id == job_id
    assert service._pending_managed_claim == job_id
    assert service.secret_requests == service.handoffs == []
    assert state.collector_job_end_requests == []
    assert delays == [bloodhound_enterprise.MANAGED_JOB_CLAIM_RETRY_DELAY_SECONDS] * 3

    # A fresh discovery would offer B, which this client cannot claim while
    # holding A. Recovery must use A's retained ID instead of this result.
    available = mock_bloodhound_api.get("/api/v2/collie-job-queue/available").json()
    assert [job["id"] for job in available["data"]["jobs"]] == [
        "22222222-2222-2222-2222-222222222222"
    ]
    discovery_count = len(state.collector_job_queue_requests)
    with caplog.at_level(logging.DEBUG):
        service._poll()
    assert service._pending_managed_claim is None
    assert service.secret_requests == ["private-secret-reference"]
    (prepared,) = service.handoffs
    assert str(prepared.job.id) == job_id
    assert prepared.job.params == CLAIMED_PARAMS

    assert len(state.collector_job_queue_requests) == discovery_count
    assert state.collector_job_claim_requests == [{"job_id": job_id, "body": b""}] * 5
    assert state.collector_job_end_requests == []
    assert state.job_started is False
    assert "raw-provider-secret" not in caplog.text
    assert all("raw-provider-secret" not in repr(r.__dict__) for r in caplog.records)


@pytest.mark.parametrize("status", [404, 409])
def test_definitive_claim_rejection_clears_pending_id_without_credentials_or_end(
    stateful_managed_service, mock_bloodhound_api, monkeypatch, status
):
    service = stateful_managed_service
    state = mock_bloodhound_api.app.state
    monkeypatch.setattr(bloodhound_enterprise.time, "sleep", lambda seconds: None)
    with pytest.raises(ManagedJobProcessingError):
        service._poll()

    state.collector_job_claim_statuses = [status]
    if status == 404:
        with pytest.raises(ManagedJobProcessingError):
            service._poll()
    else:
        service._poll()

    assert service._pending_managed_claim is None
    assert service.secret_requests == service.handoffs == []
    assert state.collector_job_end_requests == []
    assert len(state.collector_job_queue_requests) == 1

    # Once BHE confirms A is no longer claimable, the next poll can discover B.
    job_b = state.collector_job_queue_response["data"]["jobs"][1]
    state.collector_job_queue_response["data"]["jobs"] = [job_b]
    state.collector_job_claim_response["id"] = job_b["id"]
    state.collector_job_active_id = None
    service._poll()
    assert len(state.collector_job_queue_requests) == 2
    assert str(service.handoffs[0].job.id) == job_b["id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "22222222-2222-2222-2222-222222222222"),
        ("job_key", "other-collector"),
        ("status", "running"),
        ("claimed_by", None),
        ("claimed_at", None),
        ("claim_expires_at", None),
        ("claim_expires_at", "2020-01-01T00:00:00Z"),
        ("claim_expires_at", "2099-01-01T00:00:00"),
    ],
)
def test_unusable_claim_response_blocks_credentials_and_retains_id(
    managed_service, mock_bloodhound_api, field, value, caplog
):
    state = mock_bloodhound_api.app.state
    job_id = state.collector_job_claim_response["id"]
    state.collector_job_claim_response[field] = value
    with caplog.at_level(logging.DEBUG), pytest.raises(ManagedJobProcessingError):
        managed_service._poll()

    assert managed_service._pending_managed_claim == job_id
    assert managed_service.secret_requests == managed_service.handoffs == []
    assert state.collector_job_end_requests == []
    assert "Managed claim response does not confirm a usable claim." in caplog.text
    assert "Credential validation failed" not in caplog.text
    assert all(
        "private-secret-reference" not in repr(r.__dict__) for r in caplog.records
    )


def test_lease_expiring_during_secret_retrieval_blocks_handoff(
    managed_service, mock_bloodhound_api, monkeypatch
):
    state = mock_bloodhound_api.app.state
    expires_at = datetime.fromisoformat(
        state.collector_job_claim_response["claim_expires_at"]
    )
    clock = iter([expires_at - timedelta(seconds=1), expires_at + timedelta(seconds=1)])
    monkeypatch.setattr(
        scheduler_service, "datetime", SimpleNamespace(now=lambda timezone: next(clock))
    )
    with pytest.raises(ManagedJobProcessingError):
        managed_service._poll()

    assert (
        managed_service._pending_managed_claim
        == state.collector_job_claim_response["id"]
    )
    assert managed_service.secret_requests == ["private-secret-reference"]
    assert managed_service.handoffs == []
    assert state.collector_job_end_requests == []


def test_malformed_committed_claim_response_keeps_id_until_reconciled(
    stateful_managed_service, mock_bloodhound_api, caplog
):
    service = stateful_managed_service
    state = mock_bloodhound_api.app.state
    job_id = state.collector_job_claim_response["id"]
    state.collector_job_lost_claim_responses = 0
    state.collector_job_claim_response["id"] = "raw-provider-secret"
    with caplog.at_level(logging.DEBUG), pytest.raises(ManagedJobProcessingError):
        service._poll()

    assert state.collector_job_active_id == job_id
    assert service._pending_managed_claim == job_id
    assert service.secret_requests == service.handoffs == []
    assert state.collector_job_end_requests == []
    assert "raw-provider-secret" not in caplog.text

    state.collector_job_claim_response["id"] = job_id
    service._poll()
    assert service._pending_managed_claim is None
    assert len(state.collector_job_queue_requests) == 1
    assert len(service.handoffs) == 1
    assert str(service.handoffs[0].job.id) == job_id


def test_managed_scheduler_skips_legacy_calls_without_collection_runtime(
    managed_service, mock_bloodhound_api, monkeypatch, caplog
):
    service = managed_service
    state = mock_bloodhound_api.app.state
    service.managed_job_runner = None
    state.management_operations = [_support_bundle_operation()]
    polls = []

    def next_poll(seconds):
        assert seconds == service.interval
        polls.append(seconds)
        if len(polls) == 2:
            service.exit = True

    monkeypatch.setattr(scheduler_service.time, "sleep", next_poll)
    monkeypatch.setattr(scheduler_service.signal, "signal", lambda *args: None)
    with caplog.at_level(logging.DEBUG):
        service.start()

    assert len(polls) == 2
    assert state.management_available_requests == 0
    assert state.client_update_payload is None
    assert state.operation_started is False
    assert state.artifact_completed is False
    assert state.operation_end_payload is None
    assert (
        state.collector_job_queue_requests == state.collector_job_claim_requests == []
    )
    assert state.jobs_available_requests == state.jobs_current_requests == 0
    assert state.job_started is False
    assert service.handoffs == []
    assert (
        "Managed collection runtime is unavailable; retrying next poll." in caplog.text
    )


@pytest.mark.parametrize("pending", ["claim", "failure"])
def test_managed_recovery_continues_without_legacy_management(
    managed_service, mock_bloodhound_api, monkeypatch, pending
):
    service = managed_service
    state = mock_bloodhound_api.app.state
    state.management_operations = [_support_bundle_operation()]
    job_id = state.collector_job_claim_response["id"]
    if pending == "claim":
        service._pending_managed_claim = job_id
    else:
        service._pending_managed_failure = job_id
    sent = []
    monkeypatch.setattr(service, "_send_support_bundle", sent.append)

    service._poll()

    assert state.management_available_requests == 0
    assert sent == []
    assert state.collector_job_queue_requests == []
    assert service._pending_managed_claim is service._pending_managed_failure is None
    assert state.managed_events == (
        ["claim", "retrieve", "start_handoff"] if pending == "claim" else ["end"]
    )

    service._poll()
    assert state.management_available_requests == 0
    assert sent == []
    assert len(state.collector_job_queue_requests) == 1


def test_support_bundle_tracebacks_preserve_aws_auth_context(
    mock_service, monkeypatch, caplog
):
    secret_reference = "private-bootstrap-secret-reference"
    provider_message = "private-aws-provider-message"

    def get_secret_value(*, SecretId):
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": provider_message}},
            "GetSecretValue",
        )

    credentials = AWSBHECredentials(
        secret_reference,
        AWSSecretsManager(SimpleNamespace(get_secret_value=get_secret_value)),
    )
    mock_service.client._credential_refresh = credentials.refresh

    def expired_token(self, **kwargs):
        raise BloodHoundHTTPError("API token expired", 401)

    monkeypatch.setattr(bloodhound.BloodHound, "request", expired_token)
    operation = ManagementOperation.model_validate(_support_bundle_operation())
    with caplog.at_level(logging.DEBUG), pytest.raises(SecretPermissionError) as raised:
        mock_service._send_support_bundle(operation)

    assert "Secrets Manager GetSecretValue" in caplog.text
    assert "AccessDeniedException" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)
    formatted_error = "".join(traceback.format_exception(raised.value))
    for context in (secret_reference, provider_message):
        assert context in caplog.text
        assert context in formatted_error


def test_managed_worker_resolves_nested_configuration_before_pipeline(
    monkeypatch, caplog
):
    collector = SimpleNamespace(
        name="faker",
        dlt_source=SimpleNamespace(section="source", name="random"),
        package_version="1.0.0",
        metadata=None,
    )
    observed_configuration = {}
    monkeypatch.setattr(
        scheduler_service.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[collector]),
    )

    def capture_pipeline(extension):
        observed_configuration.update(
            node_count=dlt.config.get("sources.source.random.node_count", int),
            filters=dlt.config.get("sources.source.random.filters", list),
            token=dlt.secrets.get("sources.source.random.credentials.token", str),
        )
        return {"collect": []}

    monkeypatch.setattr(scheduler_service.dataflow, "pipeline", capture_pipeline)

    with caplog.at_level(logging.INFO, logger="openhound.scheduler.service"):
        result = _subprocess_collect(
            "faker",
            "collector-job-123",
            {
                "sources": {
                    "source": {"random": {"node_count": 4, "filters": ["one", "two"]}}
                }
            },
            {
                "sources": {
                    "source": {"random": {"credentials": {"token": "secret-value"}}}
                }
            },
        )

    assert result.job_id == "collector-job-123"
    assert observed_configuration == {
        "node_count": 4,
        "filters": ["one", "two"],
        "token": "secret-value",
    }
    assert "secret-value" not in caplog.text


@pytest.mark.parametrize(
    "params,secrets,reason",
    [
        (
            {"private-key": "private-credential-value"},
            {},
            "flat configuration is not supported",
        ),
        (
            CLAIMED_PARAMS,
            {"private-key": "private-credential-value"},
            "flat configuration is not supported",
        ),
        (
            {},
            COLLECTOR_SECRETS,
            "must be a non-empty nested JSON object",
        ),
        (
            {
                "sources": {
                    "source": {"random": {"node_count": "private-credential-value"}}
                }
            },
            {},
            "could not be resolved; check setting types and required fields",
        ),
        (
            CLAIMED_PARAMS,
            {
                "sources": {
                    "source": {"random": {"node_count": "private-credential-value"}}
                }
            },
            "could not be resolved; check setting types and required fields",
        ),
    ],
    ids=[
        "flat-params",
        "flat-secrets",
        "empty-params",
        "params-resolution",
        "secrets-resolution",
    ],
)
def test_managed_configuration_failure_is_logged_and_reported_with_the_same_reason(
    mock_service,
    mock_bloodhound_api,
    monkeypatch,
    caplog,
    params,
    secrets,
    reason,
):
    monkeypatch.setattr(
        scheduler_service.dataflow,
        "pipeline",
        lambda extension: {
            "node_count": dlt.config.get("sources.source.random.node_count", int)
        },
    )
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ManagedConfigurationError) as error,
    ):
        _subprocess_collect(
            "faker",
            "collector-job-123",
            params,
            secrets,
        )

    future = Future()
    future.set_exception(error.value)
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = "collector-job-123"
    mock_service.future = future
    mock_service._handle_completed_job(future)

    (request,) = mock_bloodhound_api.app.state.collector_job_end_requests
    assert request["body"]["outcome"] == "failed"
    message = request["body"]["failure_message"]
    assert reason in message
    (record,) = [
        record
        for record in caplog.records
        if record.name == "openhound.scheduler.service"
        and record.levelno == logging.ERROR
    ]
    assert record.getMessage() == (
        f"Managed job collector-job-123 configuration is invalid: {message}"
    )
    assert record.job_id == "collector-job-123"
    assert record.collector_extension == mock_service.collector_name
    assert record.exc_info is None
    assert (
        mock_service.future
        is mock_service.job_running
        is mock_service.managed_job_id
        is None
    )
    assert "private-key" not in caplog.text
    assert "private-credential-value" not in caplog.text
    assert all(
        "private-credential-value" not in repr(record.__dict__)
        for record in caplog.records
    )
    assert "private-credential-value" not in json.dumps(request)


def test_managed_worker_registers_real_faker_source_in_a_fresh_process(monkeypatch):
    monkeypatch.setenv("SOURCES__SOURCE__RANDOM__NODE_COUNT", "999")
    code = """
from openhound.scheduler.service import _subprocess_collect, dataflow

def inspect_collection(extension):
    return {"node_count": len(list(extension.dlt_source()))}

dataflow.pipeline = inspect_collection
result = _subprocess_collect(
    "faker", "e0d43dbd-2cdf-4d68-9a81-2f95cd58a92f",
    {"sources": {"source": {"random": {"node_count": 4}}}}, {}
)
assert result.results == {"node_count": 4}, result.results
result = _subprocess_collect(
    "faker", "second-job", {"sources": {"source": {"random": {"node_count": 2}}}}, {}
)
assert result.results == {"node_count": 2}, result.results
import dlt
assert dlt.config.get("sources.source.random.node_count", int) == 999
"""
    subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, timeout=30
    )


class FakeClock:
    def __init__(self, on_sleep=lambda now: None):
        self.now = 0
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.now += seconds
        self.on_sleep(self.now)


@pytest.fixture
def enabled_managed_heartbeats(monkeypatch):
    """Keep the deferred heartbeat implementation covered until BHE supports it."""
    monkeypatch.setattr(scheduler_service, "_MANAGED_HEARTBEATS_DISABLED", False)


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_job_uses_shared_poll_loop_without_polling_for_new_jobs(
    mock_service, mock_bloodhound_api, monkeypatch, caplog
):
    future = Future()
    submitted = []

    def submit(*args):
        assert mock_bloodhound_api.app.state.collector_job_start_requests == [
            ("collector-job-123", b"")
        ]
        submitted.append(args)
        return future

    clock = FakeClock()
    monkeypatch.setattr(mock_service.executor, "submit", submit)
    monkeypatch.setattr(scheduler_service, "time", clock)
    monkeypatch.setattr(
        mock_service,
        "check_jobs",
        lambda: pytest.fail("job polling must stop during managed collection"),
    )

    def fail_unmanaged_checkin(self):
        pytest.fail("managed jobs must use the heartbeat endpoint")

    monkeypatch.setattr(
        mock_service.client.__class__,
        "jobs_current",
        property(fail_unmanaged_checkin),
    )

    with caplog.at_level(logging.INFO, logger="openhound.scheduler.service"):
        mock_service.run_claimed_job(
            "collector-job-123", {"node_count": 4}, {"token": "secret-value"}
        )
        assert mock_service.job_running == "collector-job-123"
        for now, expected_heartbeats in (
            (0, 0),
            (299, 0),
            (300, 1),
            (599, 1),
            (600, 2),
        ):
            clock.now = now
            mock_service._poll()
            assert (
                len(mock_bloodhound_api.app.state.collector_job_heartbeat_requests)
                == expected_heartbeats
            )
        future.set_result(Result(results={"collect": []}, job_id="collector-job-123"))
        clock.now = 630
        monkeypatch.setattr(mock_service, "check_jobs", lambda: None)
        mock_service._poll()

    assert submitted[0][1:] == (
        "openhound-faker",
        "collector-job-123",
        {"node_count": 4},
        {"token": "secret-value"},
        True,
    )
    assert mock_bloodhound_api.app.state.collector_job_start_requests == [
        ("collector-job-123", b"")
    ]
    assert mock_bloodhound_api.app.state.collector_job_heartbeat_requests == [
        ("collector-job-123", b""),
        ("collector-job-123", b""),
    ]
    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {"job_id": "collector-job-123", "body": {"outcome": "succeeded"}}
    ]
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert "claimed successfully" not in caplog.text
    assert "started successfully" in caplog.text
    assert "heartbeat succeeded" in caplog.text
    assert "secret-value" not in caplog.text


def test_managed_start_failure_does_not_launch_worker(mock_service, monkeypatch):
    monkeypatch.setattr(
        mock_service.client,
        "start_collector_job",
        lambda job_id: (_ for _ in ()).throw(BloodHoundHTTPError("conflict", 409)),
    )
    monkeypatch.setattr(
        mock_service.executor,
        "submit",
        lambda *args: pytest.fail("worker must not launch after start failure"),
    )

    with pytest.raises(BloodHoundHTTPError) as error:
        mock_service.run_claimed_job("collector-job-123", {}, {})

    assert error.value.code == 409
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert mock_service.next_heartbeat_at is None


@pytest.mark.parametrize(
    ("job_id", "managed", "message"),
    [
        (123, True, "Managed job IDs must be strings"),
        ("collector-job-123", False, "Unmanaged job IDs must be integers"),
    ],
)
def test_launch_job_rejects_invalid_id_type_before_start(
    mock_service, mock_bloodhound_api, job_id, managed, message
):
    with pytest.raises(TypeError, match=message):
        mock_service._launch_job(job_id, managed=managed)

    assert mock_bloodhound_api.app.state.collector_job_start_requests == []
    assert mock_bloodhound_api.app.state.job_started is False
    assert mock_service.executor.submitted == []
    assert mock_service.future is mock_service.job_running is None


def test_managed_runtime_normalizes_uuid_at_entry(mock_service, mock_bloodhound_api):
    job_id = UUID("e0d43dbd-2cdf-4d68-9a81-2f95cd58a92f")

    mock_service.run_claimed_job(job_id, {}, {})

    assert mock_bloodhound_api.app.state.collector_job_start_requests == [
        (str(job_id), b"")
    ]
    assert mock_service.job_running == mock_service.managed_job_id == str(job_id)
    args, _, _ = mock_service.executor.submitted[0]
    assert args[2] == str(job_id)


def test_claimed_job_cannot_start_while_another_job_runs(
    mock_service, mock_bloodhound_api
):
    mock_service.job_running = 123
    mock_service.future = Future()

    with pytest.raises(RuntimeError, match="Job 123 is already running"):
        mock_service.run_claimed_job("collector-job-123", {}, {})

    assert mock_bloodhound_api.app.state.collector_job_start_requests == []
    assert mock_service.job_running == 123


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_transient_managed_heartbeat_failures_keep_collection_running(
    mock_service, mock_bloodhound_api, monkeypatch, caplog
):
    future = Future()
    attempts = []

    clock = FakeClock()
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: future)
    monkeypatch.setattr(scheduler_service, "time", clock)
    monkeypatch.setattr(mock_service, "check_jobs", lambda: None)

    def heartbeat(job_id):
        attempts.append(clock.now)
        if len(attempts) == 1:
            raise requests.Timeout("no response")
        if len(attempts) == 2:
            raise BloodHoundHTTPError("temporary failure", 503)

    monkeypatch.setattr(mock_service.client, "heartbeat_collector_job", heartbeat)

    with caplog.at_level(logging.INFO, logger="openhound.scheduler.service"):
        mock_service.run_claimed_job("collector-job-123", {}, {})
        for now in (300, 600, 900):
            clock.now = now
            mock_service._poll()
        future.set_result(Result(results={}, job_id="collector-job-123"))
        clock.now = 930
        mock_service._poll()

    assert attempts == [300, 600, 900]
    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {"job_id": "collector-job-123", "body": {"outcome": "succeeded"}}
    ]
    assert caplog.text.count("heartbeat request failed") == 2
    assert "heartbeat succeeded" in caplog.text


@pytest.mark.parametrize("during_shutdown", [False, True], ids=["poll", "shutdown"])
@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_heartbeat_claim_loss_stops_worker_without_reporting_outcome(
    mock_service, mock_bloodhound_api, monkeypatch, during_shutdown
):
    future = Future()
    future.set_running_or_notify_cancel()
    events = []
    executor = mock_service.executor

    class Worker:
        def kill(self):
            events.append("kill")
            future.set_exception(BrokenProcessPool("worker killed"))

    executor._processes = {123: Worker()}
    monkeypatch.setattr(executor, "submit", lambda *args: future)

    def shutdown(*, wait, cancel_futures):
        assert wait is True
        assert cancel_futures is True
        events.append("join")

    monkeypatch.setattr(executor, "shutdown", shutdown)
    clock = FakeClock()
    monkeypatch.setattr(scheduler_service, "time", clock)

    def lose_claim(job_id):
        raise BloodHoundHTTPError("claim lost", 404)

    monkeypatch.setattr(mock_service.client, "heartbeat_collector_job", lose_claim)
    mock_service.run_claimed_job("collector-job-123", {}, {})
    clock.now = 300
    if during_shutdown:
        mock_service._shutdown()
    else:
        mock_service._poll()
        monkeypatch.setattr(mock_service, "check_jobs", lambda: None)
        mock_service._poll()

    assert events == ["kill", "join"]
    assert future.cancelled() is False  # A running future cannot simply be cancelled.
    assert mock_service.executor is not executor
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert mock_service.next_heartbeat_at is None
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []
    assert mock_bloodhound_api.app.state.job_ended is False


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_heartbeat_claim_loss_kills_a_real_running_process(
    mock_service, mock_bloodhound_api, monkeypatch
):
    executor = ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)
    original_submit = executor.submit
    mock_service.executor = executor
    monkeypatch.setattr(
        executor, "submit", lambda *args: original_submit(time.sleep, 60)
    )

    def lose_claim(job_id):
        raise BloodHoundHTTPError("claim lost", 404)

    monkeypatch.setattr(mock_service.client, "heartbeat_collector_job", lose_claim)
    try:
        mock_service.run_claimed_job("collector-job-123", {}, {})
        future = mock_service.future
        deadline = time.monotonic() + 10
        while not future.running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert future.running()
        workers = list(executor._processes.values())
        assert workers and all(worker.is_alive() for worker in workers)

        mock_service.next_heartbeat_at = 0
        mock_service._poll()

        assert all(not worker.is_alive() for worker in workers)
        assert future.done()
        assert mock_service.future is None
        assert mock_service.executor is not executor
        assert mock_bloodhound_api.app.state.collector_job_end_requests == []
    finally:
        for worker in list((executor._processes or {}).values()):
            worker.kill()
        executor.shutdown(wait=True, cancel_futures=True)
        mock_service.executor.shutdown(wait=True, cancel_futures=True)


def test_managed_worker_failure_ends_job_failed(
    mock_service, mock_bloodhound_api, monkeypatch
):
    future = Future()
    future.set_exception(RuntimeError("worker error"))
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: future)
    monkeypatch.setattr(mock_service, "check_jobs", lambda: None)

    mock_service.run_claimed_job("collector-job-123", {}, {})
    mock_service._poll()

    assert mock_bloodhound_api.app.state.collector_job_heartbeat_requests == []
    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {
            "job_id": "collector-job-123",
            "body": {
                "outcome": "failed",
                "failure_message": "Unexpected error while running 'openhound-faker' collector",
            },
        }
    ]


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_shutdown_keeps_heartbeating_until_managed_worker_finishes(
    mock_service, mock_bloodhound_api, monkeypatch
):
    future = Future()

    def finish_worker(now):
        if now >= 630 and not future.done():
            future.set_result(Result(results={}, job_id="collector-job-123"))

    clock = FakeClock(finish_worker)
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: future)
    monkeypatch.setattr(scheduler_service, "time", clock)

    mock_service.run_claimed_job("collector-job-123", {}, {})
    mock_service._shutdown()

    assert mock_bloodhound_api.app.state.collector_job_heartbeat_requests == [
        ("collector-job-123", b""),
        ("collector-job-123", b""),
    ]
    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {"job_id": "collector-job-123", "body": {"outcome": "succeeded"}}
    ]
    assert mock_service.future is None


@pytest.mark.parametrize(
    "failure_phase", ["upload", "success_report", "failure_report"]
)
@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_shutdown_retries_completion_and_renews_claim(
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path, caplog, failure_phase
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    files = [output / f"part-{index}.json" for index in range(2)]
    for index, path in enumerate(files):
        path.write_bytes(f"file-{index}".encode())
    result = Result(results={}, job_id=job_id, output_path=output, data_files=files)
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future
    mock_service.interval = 300
    mock_service.next_heartbeat_at = 300
    clock = FakeClock()
    monkeypatch.setattr(scheduler_service, "time", clock)
    uploads = []
    ends = []
    original_end = mock_service.client.end_managed_job

    def upload(job, data):
        assert job == job_id
        uploads.append(data.read())
        if len(uploads) == 2:
            if failure_phase == "upload":
                raise requests.Timeout("sensitive-response-body")
            if failure_phase == "failure_report":
                raise BloodHoundHTTPError("sensitive-response-body", 415)

    def end(job, outcome, **kwargs):
        ends.append(outcome)
        if failure_phase != "upload" and len(ends) == 1:
            raise BloodHoundHTTPError("sensitive-response-body", 500)
        return original_end(job, outcome, **kwargs)

    def check_retry_state(now):
        assert now == 300  # Only one retry delay should be needed.
        assert mock_service.future is future
        assert mock_service.job_running == job_id
        assert result.uploaded_files == (2 if failure_phase == "success_report" else 1)
        assert output.exists()
        assert mock_bloodhound_api.app.state.collector_job_end_requests == []

    clock.on_sleep = check_retry_state
    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "end_managed_job", end)

    mock_service._shutdown()

    expected_outcome = (
        ManagedJobOutcome.FAILED
        if failure_phase == "failure_report"
        else ManagedJobOutcome.SUCCEEDED
    )
    assert uploads == [b"file-0", b"file-1"] + (
        [b"file-1"] if failure_phase == "upload" else []
    )
    assert ends == [expected_outcome] * (1 if failure_phase == "upload" else 2)
    body = {"outcome": expected_outcome.value}
    if failure_phase == "failure_report":
        body["failure_message"] = "Collector data upload rejected (HTTP 415)"
    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {"job_id": job_id, "body": body}
    ]
    assert mock_bloodhound_api.app.state.collector_job_heartbeat_requests == [
        (job_id, b"")
    ]
    assert mock_bloodhound_api.app.state.job_ended is False
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert not output.exists()
    assert "sensitive-response-body" not in caplog.text


def test_managed_completion_fails_job_when_remaining_staged_file_disappears(
    mock_service, monkeypatch, tmp_path
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    files = [output / f"part-{index}.json" for index in range(2)]
    for index, path in enumerate(files):
        path.write_bytes(f"file-{index}".encode())
    result = Result(results={}, job_id=job_id, output_path=output, data_files=files)
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future
    uploads = []
    ends = []
    monkeypatch.setattr(scheduler_service, "time", FakeClock())
    monkeypatch.setattr(mock_service, "_poll_management", lambda: False)
    monkeypatch.setattr(mock_service, "_poll_managed_jobs", lambda: None)

    def upload(job, data):
        assert job == job_id
        uploads.append(data.read())
        # Lose the remaining file after the first upload has been accepted.
        files[1].unlink()

    def end(job, outcome, **kwargs):
        ends.append((job, outcome, kwargs))

    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "end_managed_job", end)

    mock_service._poll()
    mock_service._poll()

    assert uploads == [b"file-0"]
    assert len(ends) == 1, (
        "Missing staged data must fail the job instead of retrying forever"
    )
    assert ends[0][:2] == (job_id, ManagedJobOutcome.FAILED)
    assert ends[0][2]["failure_message"]
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert not output.exists()


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_poll_cleans_partial_upload_output_after_heartbeat_claim_loss(
    mock_service, monkeypatch, tmp_path
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    files = [output / f"part-{index}.json" for index in range(2)]
    for index, path in enumerate(files):
        path.write_bytes(f"file-{index}".encode())
    result = Result(results={}, job_id=job_id, output_path=output, data_files=files)
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future
    mock_service.next_heartbeat_at = 300
    clock = FakeClock()
    clock.now = 300
    monkeypatch.setattr(scheduler_service, "time", clock)
    monkeypatch.setattr(mock_service, "_poll_management", lambda: False)
    monkeypatch.setattr(mock_service, "_poll_managed_jobs", lambda: None)
    events = []
    ends = []

    def upload(job, data):
        assert job == job_id
        content = data.read()
        events.append(content)
        if content == b"file-1":
            raise requests.Timeout("upload interrupted")

    def lose_claim(job):
        assert job == job_id
        events.append("heartbeat")
        raise BloodHoundHTTPError("claim lost", 404)

    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "heartbeat_collector_job", lose_claim)
    monkeypatch.setattr(
        mock_service.client,
        "end_managed_job",
        lambda *args, **kwargs: ends.append((args, kwargs)),
    )

    mock_service._poll()
    mock_service._poll()

    assert events == [b"file-0", b"file-1", "heartbeat"]
    assert result.uploaded_files == 1
    assert ends == []
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert mock_service.next_heartbeat_at is None
    assert not output.exists(), (
        "Claim loss must also remove partially uploaded staged data"
    )


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_shutdown_retries_upload_across_multiple_heartbeat_intervals(
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    files = [output / f"part-{index}.json" for index in range(2)]
    for index, path in enumerate(files):
        path.write_bytes(f"file-{index}".encode())
    result = Result(results={}, job_id=job_id, output_path=output, data_files=files)
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future
    mock_service.interval = 30
    mock_service.next_heartbeat_at = 300
    clock = FakeClock()
    monkeypatch.setattr(scheduler_service, "time", clock)
    uploads = []
    heartbeats = []
    retry_times = []
    original_heartbeat = mock_service.client.heartbeat_collector_job

    def upload(job, data):
        assert job == job_id
        content = data.read()
        uploads.append((clock.now, content))
        if content == b"file-1" and clock.now < 930:
            raise BloodHoundHTTPError("temporary outage", 503)

    def heartbeat(job):
        heartbeats.append(clock.now)
        return original_heartbeat(job)

    def check_retry_state(now):
        assert now <= 930, "Shutdown should finish once the outage ends"
        retry_times.append(now)
        assert mock_service.future is future
        assert mock_service.job_running == job_id
        assert result.uploaded_files == 1
        assert output.exists()
        assert mock_bloodhound_api.app.state.collector_job_end_requests == []

    clock.on_sleep = check_retry_state
    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "heartbeat_collector_job", heartbeat)

    mock_service._shutdown()

    assert retry_times == list(range(30, 931, 30))
    assert uploads == [(0, b"file-0")] + [(now, b"file-1") for now in range(0, 931, 30)]
    assert heartbeats == [300, 600, 900]
    assert mock_bloodhound_api.app.state.collector_job_end_requests == [
        {"job_id": job_id, "body": {"outcome": "succeeded"}}
    ]
    assert result.uploaded_files == 2
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert not output.exists()


@pytest.mark.usefixtures("enabled_managed_heartbeats")
def test_managed_shutdown_cleans_staged_output_when_heartbeat_loses_claim(
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    data_file = tmp_path / "output" / "graph.json"
    data_file.parent.mkdir()
    data_file.write_bytes(b"graph")
    future = Future()
    future.set_result(
        Result(
            results={},
            job_id=job_id,
            output_path=data_file.parent,
            data_files=[data_file],
        )
    )
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future
    mock_service.next_heartbeat_at = 0
    monkeypatch.setattr(scheduler_service, "time", FakeClock())

    def lose_claim(job):
        assert job == job_id
        raise BloodHoundHTTPError("claim lost", 404)

    def unexpected_upload(*args, **kwargs):
        raise AssertionError("A lost claim must not upload data")

    monkeypatch.setattr(mock_service.client, "heartbeat_collector_job", lose_claim)
    monkeypatch.setattr(
        mock_service.client, "upload_collector_job_data", unexpected_upload
    )
    mock_service._shutdown()

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert not data_file.parent.exists()
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []


@pytest.mark.parametrize("end_fails", [False, True])
def test_unmanaged_shutdown_waits_for_worker_and_attempts_completion_once(
    mock_service, mock_bloodhound_api, monkeypatch, end_fails
):
    future = Future()
    mock_service.job_running = 123
    mock_service.future = future
    ends = []
    checkins = []
    original_end = mock_service.client.end_job
    original_checkin = mock_service._poll_running_job

    def finish_worker(now):
        assert now == mock_service.interval
        future.set_result(Result(results={}, job_id=123))

    clock = FakeClock(finish_worker)
    monkeypatch.setattr(scheduler_service, "time", clock)

    def end(status, message):
        assert future.done()
        ends.append(status)
        if end_fails:
            raise BloodHoundHTTPError("temporary failure", 500)
        return original_end(status, message)

    def checkin():
        checkins.append(clock.now)
        original_checkin()

    def unexpected_managed_completion():
        raise AssertionError("Unmanaged jobs must retain their existing shutdown path")

    monkeypatch.setattr(mock_service.client, "end_job", end)
    monkeypatch.setattr(mock_service, "_poll_running_job", checkin)
    monkeypatch.setattr(
        mock_service, "_finish_managed_job_on_shutdown", unexpected_managed_completion
    )
    mock_service._shutdown()

    assert ends == [JobStatus.COMPLETE]
    assert checkins == [0]
    assert mock_bloodhound_api.app.state.job_ended is (not end_fails)
    assert mock_bloodhound_api.app.state.collector_job_heartbeat_requests == []
    assert mock_bloodhound_api.app.state.collector_job_end_requests == []
    assert mock_service.future is None
    assert mock_service.job_running is None


@pytest.mark.parametrize(
    "upload_error",
    [
        BloodHoundHTTPError("temporary error", 500),
        BloodHoundHTTPError("rate limited", 429),
        requests.ConnectionError("connection interrupted"),
        requests.Timeout("upload timed out"),
    ],
)
def test_managed_completion_retries_remaining_uploads_before_ending(
    mock_service, monkeypatch, tmp_path, upload_error
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    files = [output / "nodes.json", output / "edges.json"]
    for index, path in enumerate(files):
        path.write_bytes(f"file-{index}".encode())
    result = Result(results={}, job_id=job_id, output_path=output, data_files=files)
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = job_id
    mock_service.managed_job_id = job_id
    mock_service.future = future
    calls = []

    def upload(job, data):
        assert job == job_id
        calls.append(data.read())
        if len(calls) == 2:
            raise upload_error

    def end(job, outcome, **kwargs):
        calls.append((job, outcome))

    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "end_managed_job", end)

    with pytest.raises(type(upload_error)):
        mock_service._handle_completed_job(future)
    assert result.uploaded_files == 1
    assert mock_service.future is future
    assert mock_service.job_running == job_id
    assert output.exists()

    mock_service._handle_completed_job(future)
    assert calls == [
        b"file-0",
        b"file-1",
        b"file-1",
        (str(job_id), ManagedJobOutcome.SUCCEEDED),
    ]
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert not output.exists()


@pytest.mark.parametrize("status", [400, 401, 403, 415])
@pytest.mark.parametrize("retry_end", [False, True])
def test_permanent_upload_rejection_ends_failed_without_retrying_upload(
    mock_service, monkeypatch, tmp_path, caplog, status, retry_end
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    files = [output / f"part-{index}.json" for index in range(3)]
    for index, path in enumerate(files):
        path.write_bytes(f"file-{index}".encode())
    result = Result(results={}, job_id=job_id, output_path=output, data_files=files)
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future
    uploads = []
    ends = []

    def upload(job, data):
        uploads.append((job, data.read()))
        if len(uploads) == 2:
            raise BloodHoundHTTPError("sensitive-response-body", status)

    def end(job, outcome, **kwargs):
        ends.append((job, outcome, kwargs))
        if retry_end and len(ends) == 1:
            raise BloodHoundHTTPError("temporary error", 500)

    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "end_managed_job", end)

    if retry_end:
        with pytest.raises(BloodHoundHTTPError):
            mock_service._handle_completed_job(future)
        assert mock_service.future is future
        assert mock_service.job_running == job_id
        assert output.exists()

    mock_service._handle_completed_job(future)

    assert uploads == [(job_id, b"file-0"), (job_id, b"file-1")]
    assert ends == [
        (
            job_id,
            ManagedJobOutcome.FAILED,
            {"failure_message": f"Collector data upload rejected (HTTP {status})"},
        )
    ] * (2 if retry_end else 1)
    assert "sensitive-response-body" not in caplog.text
    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert not output.exists()


@pytest.mark.parametrize("status", [404, 409])
def test_upload_for_missing_or_stopped_job_aborts_without_reporting_outcome(
    mock_service, monkeypatch, tmp_path, caplog, status
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    data_file = output / "graph.json"
    data_file.write_bytes(b"graph")
    future = Future()
    future.set_result(
        Result(results={}, job_id=job_id, output_path=output, data_files=[data_file])
    )
    mock_service.managed = True
    mock_service.job_running = mock_service.managed_job_id = job_id
    mock_service.future = future

    def upload(job, data):
        raise BloodHoundHTTPError("sensitive-response-body", status)

    def unexpected_end(*args, **kwargs):
        raise AssertionError("A missing or stopped job must not receive an outcome")

    monkeypatch.setattr(mock_service.client, "upload_collector_job_data", upload)
    monkeypatch.setattr(mock_service.client, "end_managed_job", unexpected_end)
    mock_service._handle_completed_job(future)

    assert mock_service.future is None
    assert mock_service.job_running is None
    assert mock_service.managed_job_id is None
    assert mock_service.next_heartbeat_at is None
    assert not output.exists()
    assert "sensitive-response-body" not in caplog.text


def test_managed_end_retry_does_not_upload_accepted_files_again(
    mock_service, monkeypatch, tmp_path
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    data_file = output / "nodes.json"
    data_file.write_bytes(b"graph")
    result = Result(
        results={}, job_id=job_id, output_path=output, data_files=[data_file]
    )
    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = job_id
    mock_service.managed_job_id = job_id
    mock_service.future = future
    uploads = []
    ends = []
    monkeypatch.setattr(
        mock_service.client,
        "upload_collector_job_data",
        lambda job, data: uploads.append(data.read()),
    )

    def end(job, outcome, **kwargs):
        ends.append((job, outcome))
        if len(ends) == 1:
            raise BloodHoundHTTPError("temporary error", 500)

    monkeypatch.setattr(mock_service.client, "end_managed_job", end)
    with pytest.raises(BloodHoundHTTPError):
        mock_service._handle_completed_job(future)
    assert output.exists()
    assert mock_service.future is future
    mock_service._handle_completed_job(future)
    assert uploads == [b"graph"]
    assert ends == [(str(job_id), ManagedJobOutcome.SUCCEEDED)] * 2
    assert not output.exists()


def test_managed_collection_failure_ends_failed_without_upload(
    mock_service, monkeypatch
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    future = Future()
    future.set_exception(ExtensionNotFoundError("missing collector"))
    mock_service.managed = True
    mock_service.job_running = job_id
    mock_service.managed_job_id = job_id
    mock_service.future = future
    ends = []
    monkeypatch.setattr(
        mock_service.client,
        "end_managed_job",
        lambda *args, **kwargs: ends.append((args, kwargs)),
    )

    def unexpected_upload(*args, **kwargs):
        raise AssertionError("Failed collections must not upload")

    monkeypatch.setattr(
        mock_service.client, "upload_collector_job_data", unexpected_upload
    )
    mock_service._handle_completed_job(future)
    assert ends[0][0] == (str(job_id), ManagedJobOutcome.FAILED)
    assert ends[0][1]["failure_message"] == "Collector 'openhound-faker' not found"
    assert mock_service.future is None
    assert mock_service.job_running is None


def test_managed_collection_stages_then_uploads_before_job_end(
    mock_service, mock_bloodhound_api, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    monkeypatch.setattr(
        scheduler_service.dataflow,
        "Path",
        lambda value: tmp_path if value == "/tmp/openhound" else Path(value),
    )
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    result = _subprocess_collect("faker", job_id, managed=True)
    assert result.data_files
    assert mock_bloodhound_api.app.state.managed_ingest_requests == []
    assert mock_bloodhound_api.app.state.ingested_nodes == 0
    assert mock_bloodhound_api.app.state.ingested_edges == 0

    future = Future()
    future.set_result(result)
    mock_service.managed = True
    mock_service.job_running = job_id
    mock_service.managed_job_id = job_id
    mock_service.future = future
    mock_service._handle_completed_job(future)

    state = mock_bloodhound_api.app.state
    assert state.managed_ingest_requests == [str(job_id)] * len(result.data_files)
    assert state.managed_ingested_nodes == 1000
    assert state.managed_ingested_edges == 10000
    assert state.collector_job_end_requests == [
        {"job_id": str(job_id), "body": {"outcome": "succeeded"}}
    ]
    assert state.job_ended is False
    assert not result.output_path.exists()


def test_failed_managed_conversion_cleans_up_staged_files(monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(
        scheduler_service.tempfile, "mkdtemp", lambda **kwargs: str(output)
    )
    collector = SimpleNamespace(name="example", package_version="1", metadata=None)
    monkeypatch.setattr(
        scheduler_service.CollectorManager,
        "from_entrypoint",
        lambda: SimpleNamespace(collectors=[collector]),
    )

    def fail(extension, output_path):
        (output_path / "partial.json").write_text("partial")
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(scheduler_service.dataflow, "pipeline", fail)
    with pytest.raises(RuntimeError, match="conversion failed"):
        _subprocess_collect(
            "example", "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d", managed=True
        )
    assert not output.exists()


def test_managed_completion_streams_graph_to_api_before_ending(
    mock_service, mock_bloodhound_api, tmp_path
):
    job_id = "6bc0857e-9d9b-48c6-bbd8-77a31c5d351d"
    output = tmp_path / "output"
    output.mkdir()
    data_file = output / "graph.json"
    payload = json.loads(
        (Path(__file__).parent / "test_data" / "sample_graph.json").read_text()
    )
    for node in payload["graph"]["nodes"]:
        node["properties"]["environmentid"] = "test-environment"
    content = json.dumps(payload).encode()
    data_file.write_bytes(content)
    graph = Graph.model_validate_json(content)
    future = Future()
    future.set_result(
        Result(results={}, job_id=job_id, output_path=output, data_files=[data_file])
    )
    mock_service.managed = True
    mock_service.job_running = job_id
    mock_service.managed_job_id = job_id
    mock_service.future = future

    mock_service._handle_completed_job(future)

    state = mock_bloodhound_api.app.state
    assert state.managed_ingest_requests == [str(job_id)]
    assert state.managed_ingested_nodes == len(graph.graph.nodes)
    assert state.managed_ingested_edges == len(graph.graph.edges)
    assert state.collector_job_end_requests == [
        {"job_id": str(job_id), "body": {"outcome": "succeeded"}}
    ]
    assert state.ingested_nodes == 0
    assert state.ingested_edges == 0
    assert state.job_ended is False
    assert not output.exists()
