import gzip
import json
import zipfile
from concurrent.futures import Future
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from openhound.core.clients.bloodhound_enterprise import JobStatus
from openhound.core.clients.models.jobs import ManagementOperationType
from openhound.core.models.graph import Graph
from openhound.core.support_bundle import collect_log_files, create_support_bundle
from openhound.scheduler import service as scheduler_service
from openhound.scheduler.service import (
    ExtensionNotFoundError,
    Result,
    Service,
    _subprocess_collect,
)

JOBS_DATA_DIR = Path(__file__).parent / "test_data" / "api" / "jobs"
MANAGEMENT_DATA_DIR = Path(__file__).parent / "test_data" / "api" / "management"


def load_json(filename: str, data_dir: Path = JOBS_DATA_DIR) -> dict:
    with open(data_dir / filename, "r") as f:
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
    # Management / support-bundle state
    # TODO(BED-8266): Update management_operations fixture data once the real
    # GET /api/v2/clients/management/available response shape is confirmed.
    app.state.management_operations = []  # list of dicts; set per-test to inject ops
    app.state.bundle_uploaded = False
    app.state.bundle_content = b""

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

    # Endpoint path confirmed via BHADR-6 ADR (BED-8268).
    # TODO(BED-8266): Confirm response field names once GET /api/v2/clients/management
    # is fully implemented in BHE.
    @app.get("/api/v2/clients/management")
    async def management_available():
        return {"data": app.state.management_operations}

    # TODO(BED-7968): Confirm endpoint path and accepted Content-Types once
    # POST /api/v2/clients/management/artifacts is merged into BHE main.
    @app.post("/api/v2/clients/management/artifacts")
    async def upload_artifact(request: Request):
        app.state.bundle_uploaded = True
        app.state.bundle_content = await request.body()
        return Response(status_code=202)

    return TestClient(app)


@pytest.fixture
def mock_service(mock_bloodhound_api, monkeypatch, tmp_path):
    """Patches requests.request so that our mocked BloodHound API will be used for testing the service.

    Also provides a temporary log directory so support bundle tests have a
    real path without touching the host filesystem.

    Args:
        mock_bloodhound_api (TestClient): A TestClient instance for the mocked BloodHound API.
        monkeypatch (pytest.MonkeyPatch): A pytest fixture for monkeypatching.
        tmp_path (Path): A temporary directory provided by pytest.
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
        interval=1,
        collector_name="openhound-faker",
        log_base_path=tmp_path,
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



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_support_bundle_op() -> dict:
    """Return a management operation dict that represents a queued support-bundle request.

    # TODO(BED-8266): Update field names and enum values once the real
    # GET /api/v2/clients/management/available response shape is confirmed.
    """
    return load_json(
        "management_available_with_operation.json", data_dir=MANAGEMENT_DATA_DIR
    )["data"][0]


def _create_log_files(log_dir: Path) -> list[Path]:
    """Write a small set of log files into log_dir and return their paths."""
    platform_log = log_dir / "openhound.log"
    platform_log.write_text('{"level": "INFO", "message": "started"}')

    rotated_platform = log_dir / "openhound.log.2026-05-28_10"
    rotated_platform.write_text('{"level": "INFO", "message": "rotated"}')

    ext_log = log_dir / "ext_faker.log"
    ext_log.write_text('{"level": "INFO", "message": "collection started"}')

    return [platform_log, rotated_platform, ext_log]


# ---------------------------------------------------------------------------
# Management endpoint unit tests
# ---------------------------------------------------------------------------

def test_check_management_returns_support_bundle_op(mock_service, mock_bloodhound_api):
    """check_management() returns the first support-bundle operation when one is queued."""
    mock_bloodhound_api.app.state.management_operations = [_make_support_bundle_op()]

    op = mock_service.check_management()

    assert op is not None
    assert op.type == ManagementOperationType.SUPPORT_BUNDLE


def test_check_management_returns_none_when_empty(mock_service, mock_bloodhound_api):
    """check_management() returns None when the management endpoint returns an empty list."""
    mock_bloodhound_api.app.state.management_operations = []

    assert mock_service.check_management() is None


# ---------------------------------------------------------------------------
# Poll loop ordering tests
# ---------------------------------------------------------------------------

def test_poll_checks_management_before_jobs(mock_service, mock_bloodhound_api, monkeypatch):
    """_poll() performs the support-bundle upload before starting a collection job.

    Both a management operation and a collection job are available; only the
    bundle upload should happen this cycle (job is skipped).
    """
    mock_bloodhound_api.app.state.management_operations = [_make_support_bundle_op()]

    # Stub _send_support_bundle so we don't need real log files for this test.
    bundle_sent = []
    monkeypatch.setattr(
        mock_service, "_send_support_bundle", lambda op: bundle_sent.append(op)
    )

    mock_service._poll()

    assert len(bundle_sent) == 1
    assert bundle_sent[0].type == ManagementOperationType.SUPPORT_BUNDLE
    # Job check must NOT have been triggered this cycle.
    assert mock_bloodhound_api.app.state.job_started is False


def test_poll_proceeds_to_jobs_when_management_empty(mock_service, mock_bloodhound_api, monkeypatch):
    """_poll() falls through to job check when there are no management operations."""
    mock_bloodhound_api.app.state.management_operations = []

    submitted = Future()
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: submitted)

    mock_service._poll()

    assert mock_service.job_running == 123
    assert mock_bloodhound_api.app.state.job_started is True


def test_poll_proceeds_to_jobs_when_management_check_fails(
    mock_service, mock_bloodhound_api, monkeypatch
):
    """_poll() falls through to job check if the management endpoint raises an exception.

    This guards against BED-8266 not being deployed yet or transient errors.
    """
    # Patch check_management on the instance level so that it raises.
    monkeypatch.setattr(
        mock_service,
        "check_management",
        lambda: (_ for _ in ()).throw(RuntimeError("endpoint not found")),
    )

    submitted = Future()
    monkeypatch.setattr(mock_service.executor, "submit", lambda *args: submitted)

    # Should not raise; error is caught and logged.
    mock_service._poll()

    assert mock_service.job_running == 123
    assert mock_bloodhound_api.app.state.job_started is True


def test_poll_skips_job_while_management_op_runs(mock_service, mock_bloodhound_api, monkeypatch):
    """_poll() does not start a collection job in the same cycle as a bundle upload."""
    mock_bloodhound_api.app.state.management_operations = [_make_support_bundle_op()]

    monkeypatch.setattr(mock_service, "_send_support_bundle", lambda op: None)

    def fail_submit(*args, **kwargs):
        raise AssertionError("submit should not be called during management cycle")

    monkeypatch.setattr(mock_service.executor, "submit", fail_submit)

    mock_service._poll()  # must not raise


# ---------------------------------------------------------------------------
# Support-bundle creation tests
# ---------------------------------------------------------------------------

def test_collect_log_files_finds_all_logs(tmp_path):
    """collect_log_files() finds the platform log, rotated backups, and job run logs."""
    created = _create_log_files(tmp_path)

    found = collect_log_files(tmp_path)

    assert set(found) == set(created)


def test_collect_log_files_ignores_non_log_files(tmp_path):
    """collect_log_files() does not include unrelated files in the log directory."""
    _create_log_files(tmp_path)
    (tmp_path / "unrelated.txt").write_text("ignore me")
    (tmp_path / "openhound.db").write_bytes(b"\x00\x01")

    found = collect_log_files(tmp_path)

    names = {f.name for f in found}
    assert "unrelated.txt" not in names
    assert "openhound.db" not in names


def test_collect_log_files_returns_empty_for_missing_dir(tmp_path):
    """collect_log_files() returns an empty list when the log directory does not exist."""
    assert collect_log_files(tmp_path / "nonexistent") == []


def test_create_support_bundle_filename_format(tmp_path):
    """create_support_bundle() produces a zip with the correct filename format.

    Expected: <collector_name>_support_bundle_YYYY-MM-DD-HH-MM-SS.zip
    """
    _create_log_files(tmp_path)

    bundle = create_support_bundle("openhound-faker", tmp_path)
    try:
        assert bundle.suffix == ".zip"
        assert bundle.stem.startswith("openhound-faker_support_bundle_")
        # Timestamp portion: YYYY-MM-DD-HH-MM-SS (19 chars)
        timestamp_part = bundle.stem[len("openhound-faker_support_bundle_"):]
        assert len(timestamp_part) == 19, f"Unexpected timestamp: {timestamp_part}"
    finally:
        bundle.unlink(missing_ok=True)


def test_create_support_bundle_contains_log_files(tmp_path):
    """create_support_bundle() zips all collected log files with flat names inside the zip."""
    created = _create_log_files(tmp_path)

    bundle = create_support_bundle("openhound-faker", tmp_path)
    try:
        with zipfile.ZipFile(bundle, "r") as zf:
            zip_names = set(zf.namelist())
        expected_names = {f.name for f in created}
        assert zip_names == expected_names
    finally:
        bundle.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# End-to-end upload tests (via mock API)
# ---------------------------------------------------------------------------

def test_send_support_bundle_uploads_to_bhe(mock_service, mock_bloodhound_api, tmp_path):
    """_send_support_bundle() creates a zip and POSTs it to the artifacts endpoint."""
    _create_log_files(tmp_path)
    mock_service.log_base_path = tmp_path

    from openhound.core.clients.models.jobs import ManagementOperation, ManagementOperationStatus
    from datetime import datetime, UTC
    op = ManagementOperation(
        id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        type=ManagementOperationType.SUPPORT_BUNDLE,
        status=ManagementOperationStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    mock_service._send_support_bundle(op)

    assert mock_bloodhound_api.app.state.bundle_uploaded is True
    # Verify BHE received a valid zip
    with zipfile.ZipFile(
        __import__("io").BytesIO(mock_bloodhound_api.app.state.bundle_content)
    ) as zf:
        assert len(zf.namelist()) > 0


def test_send_support_bundle_cleans_up_temp_file(mock_service, mock_bloodhound_api, tmp_path, monkeypatch):
    """_send_support_bundle() deletes the temp zip even when the upload succeeds."""
    _create_log_files(tmp_path)
    mock_service.log_base_path = tmp_path

    captured_bundle_path = []

    original_create = scheduler_service.create_support_bundle

    def capturing_create(collector_name, log_base_path):
        path = original_create(collector_name, log_base_path)
        captured_bundle_path.append(path)
        return path

    monkeypatch.setattr(scheduler_service, "create_support_bundle", capturing_create)

    from openhound.core.clients.models.jobs import ManagementOperation, ManagementOperationStatus
    from datetime import datetime, UTC
    op = ManagementOperation(
        id="test-op-id",
        type=ManagementOperationType.SUPPORT_BUNDLE,
        status=ManagementOperationStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    mock_service._send_support_bundle(op)

    assert len(captured_bundle_path) == 1
    assert not captured_bundle_path[0].exists(), "Temp bundle file was not cleaned up"


def test_send_support_bundle_cleans_up_on_upload_failure(mock_service, monkeypatch, tmp_path):
    """_send_support_bundle() deletes the temp zip even when the upload raises."""
    _create_log_files(tmp_path)
    mock_service.log_base_path = tmp_path

    captured_bundle_path = []

    original_create = scheduler_service.create_support_bundle

    def capturing_create(collector_name, log_base_path):
        path = original_create(collector_name, log_base_path)
        captured_bundle_path.append(path)
        return path

    monkeypatch.setattr(scheduler_service, "create_support_bundle", capturing_create)
    monkeypatch.setattr(
        mock_service.client,
        "upload_support_bundle",
        lambda path: (_ for _ in ()).throw(RuntimeError("upload failed")),
    )

    from openhound.core.clients.models.jobs import ManagementOperation, ManagementOperationStatus
    from datetime import datetime, UTC
    op = ManagementOperation(
        id="test-op-id",
        type=ManagementOperationType.SUPPORT_BUNDLE,
        status=ManagementOperationStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    # _send_support_bundle itself re-raises; the outer _poll() catch swallows it.
    # Here we just verify the cleanup happened.
    try:
        mock_service._send_support_bundle(op)
    except RuntimeError:
        pass

    assert len(captured_bundle_path) == 1
    assert not captured_bundle_path[0].exists(), "Temp bundle file was not cleaned up after failure"
