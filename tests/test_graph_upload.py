"""Tests for ``openhound graph upload`` and the pieces underneath it.

A small fake BloodHound API, built with FastAPI, stands in for the real server. It implements
the four file-upload job calls the client makes: start a job, attach a file, end the job, and
list jobs. Every request is redirected to it, so the tests check the real request shape
without needing a running BloodHound.
"""

import gzip
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openhound.cli.graph import graph as graph_cli
from openhound.core.clients.bloodhound import (
    FILE_ATTACH_TIMEOUT_SECONDS,
    QUICK_REQUEST_TIMEOUT_SECONDS,
    BloodHoundJWT,
)
from openhound.core.clients.models import FileUploadJobStatus
from openhound.core.graph_upload import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_ZIP,
    CollectionNotFound,
    find_collection_files,
    upload_collection_files,
    wait_for_job,
)

EXAMPLE_DOCUMENT = {
    "metadata": {"source_kind": "GH"},
    "graph": {
        "nodes": [
            {"id": "GH:alice", "kinds": ["GH_User"], "properties": {"name": "alice"}}
        ],
        "edges": [],
    },
}

# The environment variables that could leak settings from the developer's machine into a test.
ALL_SETTING_VARIABLES = [
    f"DESTINATION__{section}__{setting}"
    for section in ("BLOODHOUND", "BLOODHOUNDENTERPRISE")
    for setting in ("URL", "TOKEN", "TOKEN_ID", "TOKEN_KEY")
]


@pytest.fixture
def mock_bloodhound_api():
    """A fake BloodHound that runs file-upload jobs in memory.

    Each job records the files attached to it. Ending a job moves it to "ingesting"; every
    later status question advances it one step, through "analyzing" to "complete", which
    lets the waiting logic be tested without real delays. A file whose name contains
    "reject" is refused with 400, which lets per-file failures be tested.
    """
    app = FastAPI()
    app.state.jobs = {}
    app.state.next_job_id = 100
    # Set to True to make the fake refuse every request to end a job.
    app.state.refuse_to_end_jobs = False

    def job_record(job_id: int) -> dict:
        job = app.state.jobs[job_id]
        return {
            "id": job_id,
            "status": job["status"],
            "status_message": job["status_message"],
            "total_files": len(job["files"]),
            "failed_files": 0,
        }

    @app.post("/api/v2/file-upload/start", status_code=201)
    async def start_job():
        job_id = app.state.next_job_id
        app.state.next_job_id += 1
        app.state.jobs[job_id] = {
            "status": int(FileUploadJobStatus.RUNNING),
            "status_message": "",
            "files": [],
            "ended": False,
        }
        return {"data": job_record(job_id)}

    @app.post("/api/v2/file-upload/{job_id}", status_code=202)
    async def attach_file(job_id: int, request: Request):
        job = app.state.jobs.get(job_id)
        if job is None:
            return Response(status_code=404, content="no such job")
        if job["ended"]:
            return Response(status_code=400, content="job must be in running status")
        name = request.headers.get("x-file-upload-name", "")
        if "reject" in name:
            return Response(status_code=400, content=f"{name} failed validation")
        job["files"].append(
            {
                "name": name,
                "content_type": request.headers.get("content-type", ""),
                "content_encoding": request.headers.get("content-encoding", ""),
                "body": await request.body(),
            }
        )
        return Response(status_code=202)

    @app.post("/api/v2/file-upload/{job_id}/end")
    async def end_job(job_id: int):
        job = app.state.jobs.get(job_id)
        if job is None:
            return Response(status_code=404, content="no such job")
        if app.state.refuse_to_end_jobs:
            return Response(status_code=500, content="could not end the job")
        job["ended"] = True
        job["status"] = int(FileUploadJobStatus.INGESTING)
        return Response(status_code=200)

    @app.get("/api/v2/file-upload")
    async def list_jobs(request: Request):
        wanted = parse_qs(request.url.query).get("id", [""])[0]
        job_id = int(wanted.removeprefix("eq:")) if wanted else None
        records = []
        for candidate_id, job in app.state.jobs.items():
            if job_id is not None and candidate_id != job_id:
                continue
            records.append(job_record(candidate_id))
            # Each look advances an ended job one step towards completion.
            if job["ended"] and job["status"] == int(FileUploadJobStatus.INGESTING):
                job["status"] = int(FileUploadJobStatus.ANALYZING)
            elif job["ended"] and job["status"] == int(FileUploadJobStatus.ANALYZING):
                job["status"] = int(FileUploadJobStatus.COMPLETE)
        return {"data": records, "count": len(records)}

    return TestClient(app)


@pytest.fixture
def mock_bh_requests(monkeypatch, mock_bloodhound_api):
    """Send every ``requests.request`` call to the fake BloodHound instead of the network."""

    def mock_request(method, url, **kwargs):
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        content = kwargs.pop("data", None)
        kwargs.pop("timeout", None)
        return mock_bloodhound_api.request(
            method.upper(), path, content=content, **kwargs
        )

    monkeypatch.setattr("requests.request", mock_request)
    # Waiting must not actually sleep in tests.
    monkeypatch.setattr("openhound.core.graph_upload.sleep", lambda seconds: None)


@pytest.fixture
def no_credentials(monkeypatch):
    for name in ALL_SETTING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def bearer_credentials(no_credentials, monkeypatch):
    monkeypatch.setenv("DESTINATION__BLOODHOUND__URL", "http://localhost:8080")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN", "test-bearer-token")


def write_document(path: Path, payload: dict | None = None) -> Path:
    path.write_text(json.dumps(payload or EXAMPLE_DOCUMENT))
    return path


def make_client() -> BloodHoundJWT:
    return BloodHoundJWT(token="t", base_uri="http://localhost:8080")


# --- Finding files -----------------------------------------------------------------------


def test_find_collection_files_accepts_a_single_file(tmp_path):
    document = write_document(tmp_path / "users-1.json")
    assert find_collection_files(str(document)) == [document]


def test_find_collection_files_lists_json_and_zip_in_a_folder(tmp_path):
    second = write_document(tmp_path / "groups-1.json")
    first = write_document(tmp_path / "computers-1.json")
    bundle = tmp_path / "sccm_collection.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("nodes-1.json", json.dumps(EXAMPLE_DOCUMENT))
    (tmp_path / "lookup.duckdb").write_bytes(b"ignored")

    assert find_collection_files(str(tmp_path)) == [first, second, bundle]


def test_find_collection_files_explains_an_empty_folder(tmp_path):
    with pytest.raises(CollectionNotFound, match="contains no .json or .zip"):
        find_collection_files(str(tmp_path))


def test_find_collection_files_explains_a_missing_path():
    with pytest.raises(CollectionNotFound, match="not a file and not a folder"):
        find_collection_files("/no/such/place")


# --- Uploading ---------------------------------------------------------------------------


def test_upload_attaches_every_file_to_one_job_and_ends_it(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    first = write_document(tmp_path / "a.json")
    second = write_document(tmp_path / "b.json")

    result = upload_collection_files(make_client(), [first, second])

    assert [file.outcome for file in result.files] == ["uploaded", "uploaded"]
    assert result.job_id == 100
    job = mock_bloodhound_api.app.state.jobs[100]
    assert [file["name"] for file in job["files"]] == ["a.json", "b.json"]
    assert all(file["content_type"] == CONTENT_TYPE_JSON for file in job["files"])
    assert json.loads(gzip.decompress(job["files"][0]["body"])) == EXAMPLE_DOCUMENT
    assert job["ended"] is True


def test_upload_compresses_json_files_with_gzip(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    document = write_document(tmp_path / "a.json")

    result = upload_collection_files(make_client(), [document])

    assert result.files[0].outcome == "uploaded"
    attached = mock_bloodhound_api.app.state.jobs[100]["files"][0]
    assert attached["content_encoding"] == "gzip"
    # The content type still names the file inside, not the compression.
    assert attached["content_type"] == CONTENT_TYPE_JSON
    assert json.loads(gzip.decompress(attached["body"])) == EXAMPLE_DOCUMENT


def make_large_document(nodes: int, edges: int) -> dict:
    return {
        "metadata": {"source_kind": "GH"},
        "graph": {
            "nodes": [
                {"id": f"GH:{n}", "kinds": ["GH_User"], "properties": {"name": f"u{n}"}}
                for n in range(nodes)
            ],
            "edges": [
                {
                    "start": {"match_by": "id", "value": f"GH:{e}"},
                    "end": {"match_by": "id", "value": f"GH:{e + 1}"},
                    "kind": "GH_Knows",
                }
                for e in range(edges)
            ],
        },
    }


def test_large_files_are_cut_into_parts_nodes_before_edges(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    big = tmp_path / "big.json"
    big.write_text(json.dumps(make_large_document(nodes=25, edges=12)))

    result = upload_collection_files(make_client(), [big], batch_size=10)

    assert result.files[0].outcome == "uploaded"
    assert result.files[0].detail == "sent in 5 parts"
    attached = mock_bloodhound_api.app.state.jobs[100]["files"]
    assert [f["name"] for f in attached] == [
        f"big.part-000{n}.json" for n in range(1, 6)
    ]
    parts = [json.loads(gzip.decompress(f["body"])) for f in attached]
    assert all(part["metadata"] == {"source_kind": "GH"} for part in parts)
    sizes = [
        len(p["graph"].get("nodes", [])) + len(p["graph"].get("edges", []))
        for p in parts
    ]
    assert sizes == [10, 10, 5, 10, 2]
    # Every node part comes before any edge part.
    kinds = ["nodes" if "nodes" in p["graph"] else "edges" for p in parts]
    assert kinds == ["nodes", "nodes", "nodes", "edges", "edges"]


def test_files_within_the_batch_size_are_sent_whole(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    small = write_document(tmp_path / "small.json")

    upload_collection_files(make_client(), [small], batch_size=10)

    attached = mock_bloodhound_api.app.state.jobs[100]["files"]
    assert [f["name"] for f in attached] == ["small.json"]


def test_batch_size_zero_turns_batching_off(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    big = tmp_path / "big.json"
    big.write_text(json.dumps(make_large_document(nodes=25, edges=12)))

    upload_collection_files(make_client(), [big], batch_size=0)

    assert len(mock_bloodhound_api.app.state.jobs[100]["files"]) == 1


def test_a_rejected_part_fails_the_file_but_later_parts_are_still_sent(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    # The fake server rejects any file name containing "reject".
    big = tmp_path / "reject.json"
    big.write_text(json.dumps(make_large_document(nodes=25, edges=0)))

    result = upload_collection_files(make_client(), [big], batch_size=10)

    assert result.files[0].outcome == "failed"
    assert "reject.part-0001.json" in result.files[0].detail
    assert "reject.part-0003.json" in result.files[0].detail


def test_network_errors_are_reported_per_file_not_raised(monkeypatch, tmp_path):
    def refuse(method, url, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.request", refuse)

    result = upload_collection_files(
        make_client(), [write_document(tmp_path / "a.json")]
    )

    assert result.job_id is None
    assert "connection refused" in result.detail


def test_cli_reports_losing_contact_while_waiting(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path, monkeypatch
):
    def lose_contact(*args, **kwargs):
        raise requests.ConnectionError("Connection reset by peer")

    monkeypatch.setattr("openhound.cli.graph.wait_for_job", lose_contact)

    result = CliRunner().invoke(
        graph_cli, ["upload", str(write_document(tmp_path / "a.json"))]
    )

    assert result.exit_code == 1
    assert "Lost contact with BloodHound" in result.output
    assert "Traceback" not in result.output


def test_upload_can_skip_compression(mock_bh_requests, mock_bloodhound_api, tmp_path):
    document = write_document(tmp_path / "a.json")

    upload_collection_files(make_client(), [document], compress=False)

    attached = mock_bloodhound_api.app.state.jobs[100]["files"][0]
    assert attached["content_encoding"] == ""
    assert json.loads(attached["body"]) == EXAMPLE_DOCUMENT


def test_upload_sends_zip_files_with_the_zip_content_type(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    bundle = tmp_path / "collection.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("nodes-1.json", json.dumps(EXAMPLE_DOCUMENT))

    result = upload_collection_files(make_client(), [bundle])

    assert result.files[0].outcome == "uploaded"
    attached = mock_bloodhound_api.app.state.jobs[100]["files"][0]
    assert attached["content_type"] == CONTENT_TYPE_ZIP
    assert attached["content_encoding"] == ""
    assert attached["name"] == "collection.zip"


def test_upload_rejects_bad_files_locally_before_starting_a_job(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")
    wrong_shape = write_document(tmp_path / "wrong.json", {"nodes": []})

    result = upload_collection_files(make_client(), [broken, wrong_shape])

    assert [file.outcome for file in result.files] == ["failed", "failed"]
    assert "not valid JSON" in result.files[0].detail
    assert "no top-level 'graph' section" in result.files[1].detail
    assert result.job_id is None
    assert "no job was started" in result.detail
    assert mock_bloodhound_api.app.state.jobs == {}


def test_upload_reports_a_server_rejection_and_still_ends_the_job(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    rejected = write_document(tmp_path / "reject-me.json")
    accepted = write_document(tmp_path / "fine.json")

    result = upload_collection_files(make_client(), [rejected, accepted])

    assert result.files[0].outcome == "failed"
    assert "failed validation" in result.files[0].detail
    assert result.files[1].outcome == "uploaded"
    job = mock_bloodhound_api.app.state.jobs[100]
    assert [file["name"] for file in job["files"]] == ["fine.json"]
    assert job["ended"] is True


def test_dry_run_checks_files_but_contacts_nothing(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    good = write_document(tmp_path / "good.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")

    result = upload_collection_files(client=None, files=[good, broken], dry_run=True)  # type: ignore[arg-type]

    outcomes = {file.path.name: file.outcome for file in result.files}
    assert outcomes == {"good.json": "skipped", "broken.json": "failed"}
    assert result.job_id is None
    assert mock_bloodhound_api.app.state.jobs == {}


# --- Waiting -----------------------------------------------------------------------------


def test_wait_for_job_returns_once_the_job_is_finished(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    document = write_document(tmp_path / "a.json")
    result = upload_collection_files(make_client(), [document])

    job = wait_for_job(make_client(), result.job_id, timeout_seconds=30)

    assert job is not None
    assert job.status_code is FileUploadJobStatus.COMPLETE
    assert job.status_text == "complete"


def test_wait_for_job_gives_up_after_the_timeout(
    mock_bh_requests, mock_bloodhound_api, tmp_path, monkeypatch
):
    document = write_document(tmp_path / "a.json")
    result = upload_collection_files(make_client(), [document])
    # Pretend the clock jumps past the deadline immediately after the first look.
    clock = iter([0.0, 1000.0, 2000.0])
    monkeypatch.setattr("openhound.core.graph_upload.monotonic", lambda: next(clock))

    job = wait_for_job(make_client(), result.job_id, timeout_seconds=10)

    assert job is not None
    assert job.status_code is FileUploadJobStatus.INGESTING


def test_wait_for_job_returns_none_for_an_unknown_job(
    mock_bh_requests, mock_bloodhound_api
):
    assert wait_for_job(make_client(), 999, timeout_seconds=1) is None


# --- The command line --------------------------------------------------------------------


def test_cli_uploads_waits_and_reports_the_job(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path
):
    document = write_document(tmp_path / "a.json")

    result = CliRunner().invoke(graph_cli, ["upload", str(document)])

    assert result.exit_code == 0, result.output
    assert "uploaded" in result.output
    assert "file-upload job: 100" in result.output
    assert "Job 100 is complete" in result.output


def test_cli_no_wait_points_at_the_ingest_page(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path
):
    document = write_document(tmp_path / "a.json")

    result = CliRunner().invoke(graph_cli, ["upload", str(document), "--no-wait"])

    assert result.exit_code == 0, result.output
    assert "File Ingest" in result.output
    assert "Job 100 is" not in result.output


def test_cli_does_not_wait_for_a_job_that_could_not_be_ended(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path, monkeypatch
):
    mock_bloodhound_api.app.state.refuse_to_end_jobs = True
    waited = []
    monkeypatch.setattr(
        "openhound.cli.graph.wait_for_job", lambda *args, **kwargs: waited.append(1)
    )
    document = write_document(tmp_path / "a.json")

    result = CliRunner().invoke(graph_cli, ["upload", str(document)])

    assert result.exit_code == 1
    assert "could not be ended" in result.output
    assert waited == [], (
        "an open job is never processed, so there is nothing to wait for"
    )
    assert "Watch its progress" not in result.output


def test_every_job_request_has_a_time_limit(monkeypatch):
    """A server that accepts the connection and then stays silent must not block forever."""
    time_limits = {}
    replies = {
        "/api/version": {"data": {"product_edition": "community"}},
        "/api/v2/file-upload/start": {"data": {"id": 1, "status": 1}},
        "/api/v2/file-upload": {"data": []},
    }

    def record(method, url, **kwargs):
        path = urlsplit(url).path
        time_limits[(method, path)] = kwargs.get("timeout")
        return SimpleNamespace(
            status_code=200, text="", json=lambda: replies.get(path, {})
        )

    monkeypatch.setattr("requests.request", record)
    client = make_client()

    client.product_edition()
    client.start_file_upload_job()
    client.upload_file_to_job(
        job_id=1, body=b"{}", content_type="application/json", file_name="a.json"
    )
    client.end_file_upload_job(1)
    client.file_upload_job(1)

    assert time_limits == {
        ("GET", "/api/version"): QUICK_REQUEST_TIMEOUT_SECONDS,
        ("POST", "/api/v2/file-upload/start"): QUICK_REQUEST_TIMEOUT_SECONDS,
        ("POST", "/api/v2/file-upload/1"): FILE_ATTACH_TIMEOUT_SECONDS,
        ("POST", "/api/v2/file-upload/1/end"): QUICK_REQUEST_TIMEOUT_SECONDS,
        ("GET", "/api/v2/file-upload"): QUICK_REQUEST_TIMEOUT_SECONDS,
    }


def test_cli_dry_run_needs_no_credentials(
    mock_bh_requests, mock_bloodhound_api, no_credentials, tmp_path
):
    document = write_document(tmp_path / "a.json")

    result = CliRunner().invoke(graph_cli, ["upload", str(document), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert mock_bloodhound_api.app.state.jobs == {}


def test_cli_exits_with_status_one_when_a_file_fails(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path
):
    write_document(tmp_path / "reject-me.json")
    write_document(tmp_path / "fine.json")

    result = CliRunner().invoke(graph_cli, ["upload", str(tmp_path)])

    assert result.exit_code == 1
    assert "failed validation" in result.output
    assert "uploaded" in result.output


def test_cli_explains_a_missing_path(bearer_credentials):
    result = CliRunner().invoke(graph_cli, ["upload", "/no/such/place"])

    assert result.exit_code == 1
    assert "not a file and not a folder" in result.output
