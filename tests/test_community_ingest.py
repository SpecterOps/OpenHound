"""Tests for ``openhound convert --method ingest`` against BloodHound Community Edition.

A fake BloodHound, built with FastAPI, answers the calls the converter makes. It can pretend to be
either edition: Community Edition receives data through a file-upload job, and Enterprise through
``/api/v2/ingest``. Every ``requests.request`` call is sent to it instead of the network, so the
tests check the real paths, headers and bodies. The full tests run the installed ``faker``
collector, the same one the Enterprise scheduler test uses.
"""

import gzip
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

os.environ.setdefault("RUNTIME__LOG_PATH", "/tmp/openhound-test-logs")

import pytest
import requests
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openhound.core.clients.bloodhound import BloodHoundJWT
from openhound.core.community_ingest import (
    build_document,
    community_edition_client_for_ingest,
)
from openhound.core.convert import Converter, Method
from openhound.core.manager import CollectorManager
from openhound.core.progress import Progress
from openhound.main import app as openhound_cli

# The environment variables that could leak settings from the developer's machine into a test.
ALL_SETTING_VARIABLES = [
    f"DESTINATION__{section}__{setting}"
    for section in ("BLOODHOUND", "BLOODHOUNDENTERPRISE")
    for setting in ("URL", "TOKEN", "TOKEN_ID", "TOKEN_KEY")
]


@pytest.fixture
def fake_bloodhound():
    """A fake BloodHound that records every job, attached file, and Enterprise ingest call.

    Set ``app.state.edition`` to "community" or "enterprise" to choose what /api/version reports.
    """
    fake = FastAPI()
    fake.state.edition = "community"
    fake.state.jobs = {}
    fake.state.enterprise_ingests = []

    @fake.get("/api/version")
    async def version():
        return {
            "data": {
                "API": {"current_version": "v2"},
                "server_version": "v9.8.0",
                "product_edition": fake.state.edition,
            }
        }

    @fake.post("/api/v2/file-upload/start", status_code=201)
    async def start_job():
        job_id = 100 + len(fake.state.jobs)
        fake.state.jobs[job_id] = {"files": [], "ended": False}
        return {"data": {"id": job_id, "status": 1, "status_message": ""}}

    @fake.post("/api/v2/file-upload/{job_id}", status_code=202)
    async def attach_file(job_id: int, request: Request):
        fake.state.jobs[job_id]["files"].append(
            {
                "name": request.headers.get("x-file-upload-name", ""),
                "content_type": request.headers.get("content-type", ""),
                "content_encoding": request.headers.get("content-encoding", ""),
                "body": await request.body(),
            }
        )
        return Response(status_code=202)

    @fake.post("/api/v2/file-upload/{job_id}/end")
    async def end_job(job_id: int):
        fake.state.jobs[job_id]["ended"] = True
        return Response(status_code=200)

    @fake.post("/api/v2/ingest")
    async def enterprise_ingest(request: Request):
        fake.state.enterprise_ingests.append(gzip.decompress(await request.body()))
        return Response(status_code=202)

    return TestClient(fake)


@pytest.fixture
def send_requests_to_fake(monkeypatch, fake_bloodhound):
    """Send every ``requests.request`` call to the fake BloodHound instead of the network."""

    def fake_request(method, url, **kwargs):
        parsed = urlsplit(url)
        path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
        # The OpenHound client passes the body as ``data``; the test client calls it ``content``.
        content = kwargs.pop("data", None)
        kwargs.pop("timeout", None)
        return fake_bloodhound.request(method.upper(), path, content=content, **kwargs)

    monkeypatch.setattr("requests.request", fake_request)


@pytest.fixture
def no_settings(monkeypatch, tmp_path):
    """Start from a clean slate, with dlt's working files kept inside the test folder."""
    for name in ALL_SETTING_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))


@pytest.fixture
def community_settings(no_settings, monkeypatch):
    monkeypatch.setenv("DESTINATION__BLOODHOUND__URL", "http://localhost:8080")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN", "test-bearer-token")


@pytest.fixture
def enterprise_settings(no_settings, monkeypatch):
    monkeypatch.setenv(
        "DESTINATION__BLOODHOUNDENTERPRISE__URL", "http://localhost:8000"
    )
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_ID", "test-id")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_KEY", "test-key")


@pytest.fixture
def collected_faker_data(tmp_path):
    """Run the installed faker collector and its preprocessing step into the test folder.

    Returns:
        tuple[Path, Path, str | None]: The collected data folder, the lookup file, and the
        collector's source kind.
    """
    extension = next(
        collector
        for collector in CollectorManager.from_entrypoint().collectors
        if collector.name == "faker"  # pyright: ignore[reportAttributeAccessIssue]
    )
    collected = tmp_path / "collected"
    lookup_file = tmp_path / "lookup.duckdb"
    extension.collector(output_path=collected, resources=[], progress=Progress.log)
    if extension.preprocessor is not None:
        extension.preprocessor(
            input_path=collected / extension.name,
            output_file=lookup_file,
            progress=Progress.log,
        )
    return collected / extension.name, lookup_file, extension.source_kind


def run_convert_command(collected: Path, lookup_file: Path, tmp_path: Path, *extra):
    return CliRunner().invoke(
        openhound_cli,
        [
            "convert",
            "faker",
            str(collected),
            str(tmp_path / "converted"),
            "--lookup-file",
            str(lookup_file),
            "--progress",
            "log",
            *extra,
        ],
    )


def count_entities(documents: list[dict]) -> tuple[int, int]:
    nodes = sum(len(document["graph"]["nodes"]) for document in documents)
    edges = sum(len(document["graph"]["edges"]) for document in documents)
    return nodes, edges


# --- Choosing the route ------------------------------------------------------------------


def test_community_edition_is_detected(send_requests_to_fake, community_settings):
    assert community_edition_client_for_ingest() is not None


def test_enterprise_keeps_the_enterprise_route(
    send_requests_to_fake, fake_bloodhound, community_settings
):
    fake_bloodhound.app.state.edition = "enterprise"

    assert community_edition_client_for_ingest() is None


def test_no_settings_keeps_the_enterprise_route(no_settings):
    assert community_edition_client_for_ingest() is None


def test_an_unanswered_edition_question_keeps_the_enterprise_route(
    monkeypatch, community_settings
):
    def refuse(method, url, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.request", refuse)

    assert community_edition_client_for_ingest() is None


# --- Building each file ------------------------------------------------------------------


def test_documents_carry_the_source_kind_in_their_metadata():
    items = [
        {"graph": {"entity_type": "node", "content": {"id": "a"}}},
        {"graph": {"entity_type": "edge", "content": [{"kind": "K"}, {"kind": "K"}]}},
    ]

    document = build_document(items, source_kind="GHBase")

    assert document["metadata"] == {"source_kind": "GHBase"}
    assert document["graph"] == {"nodes": [{"id": "a"}], "edges": [{"kind": "K"}] * 2}


def test_documents_without_a_source_kind_have_no_metadata():
    assert "metadata" not in build_document([], source_kind=None)


# --- Running a conversion ----------------------------------------------------------------


def test_convert_ingest_sends_everything_through_one_community_edition_job(
    send_requests_to_fake,
    fake_bloodhound,
    community_settings,
    collected_faker_data,
    tmp_path,
):
    collected, lookup_file, source_kind = collected_faker_data

    result = run_convert_command(collected, lookup_file, tmp_path, "--method", "ingest")

    assert result.exit_code == 0, result.output
    jobs = fake_bloodhound.app.state.jobs
    assert list(jobs) == [100], "exactly one job per run"
    job = jobs[100]
    assert job["ended"] is True
    assert job["files"], "at least one file attached"
    assert all(file["content_type"] == "application/json" for file in job["files"])
    assert all(file["content_encoding"] == "gzip" for file in job["files"])
    assert all(file["name"].endswith(".json") for file in job["files"])

    documents = [json.loads(gzip.decompress(file["body"])) for file in job["files"]]
    assert source_kind, "the faker collector declares a source kind"
    assert all(doc["metadata"] == {"source_kind": source_kind} for doc in documents)
    # The faker collector produces the same counts the Enterprise scheduler test checks.
    assert count_entities(documents) == (1000, 10000)
    assert fake_bloodhound.app.state.enterprise_ingests == []
    assert not (tmp_path / "converted").exists(), "ingest writes no output files"


def test_convert_ingest_to_enterprise_is_unchanged(
    send_requests_to_fake,
    fake_bloodhound,
    enterprise_settings,
    collected_faker_data,
    tmp_path,
):
    fake_bloodhound.app.state.edition = "enterprise"
    collected, lookup_file, _ = collected_faker_data

    result = run_convert_command(collected, lookup_file, tmp_path, "--method", "ingest")

    assert result.exit_code == 0, result.output
    assert fake_bloodhound.app.state.jobs == {}, "Enterprise opens no file-upload job"
    documents = [
        json.loads(body) for body in fake_bloodhound.app.state.enterprise_ingests
    ]
    assert all("metadata" not in document for document in documents)
    assert count_entities(documents) == (1000, 10000)


def test_convert_without_method_still_writes_files(
    send_requests_to_fake,
    fake_bloodhound,
    community_settings,
    collected_faker_data,
    tmp_path,
):
    collected, lookup_file, _ = collected_faker_data

    result = run_convert_command(collected, lookup_file, tmp_path)

    assert result.exit_code == 0, result.output
    assert list((tmp_path / "converted").glob("*.json"))
    assert fake_bloodhound.app.state.jobs == {}
    assert fake_bloodhound.app.state.enterprise_ingests == []


def test_the_job_is_ended_even_when_the_conversion_fails(
    send_requests_to_fake, fake_bloodhound, monkeypatch, tmp_path
):
    def fail(self, source, **kwargs):
        raise RuntimeError("conversion broke")

    monkeypatch.setattr(
        "openhound.core.convert.logger_override.set_handler", lambda n: None
    )
    monkeypatch.setattr(Converter, "_run", fail)
    converter = Converter(
        name="test",
        input_path=tmp_path,
        lookup=None,
        output_path=tmp_path / "unused",
        source_kind="TestBase",
        progress=Progress.log,
        method=Method.ingest,
        community_edition_client=BloodHoundJWT(
            token="t", base_uri="http://localhost:8080"
        ),
    )

    with pytest.raises(RuntimeError, match="conversion broke"):
        converter.run(
            SimpleNamespace(resources={}), graph_resources=[], extra_context={}
        )

    assert fake_bloodhound.app.state.jobs[100]["ended"] is True


def test_a_failure_to_end_the_job_does_not_hide_the_original_error(
    monkeypatch, tmp_path
):
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")
    monkeypatch.setattr(client, "start_file_upload_job", lambda: SimpleNamespace(id=7))

    def end_fails(job_id):
        raise requests.ConnectionError("BloodHound went away")

    def conversion_fails(self, source, **kwargs):
        raise RuntimeError("conversion broke")

    monkeypatch.setattr(client, "end_file_upload_job", end_fails)
    monkeypatch.setattr(
        "openhound.core.convert.logger_override.set_handler", lambda n: None
    )
    monkeypatch.setattr(Converter, "_run", conversion_fails)
    converter = Converter(
        name="test",
        input_path=tmp_path,
        lookup=None,
        output_path=tmp_path / "unused",
        source_kind=None,
        progress=Progress.log,
        method=Method.ingest,
        community_edition_client=client,
    )

    with pytest.raises(RuntimeError, match="conversion broke"):
        converter.run(
            SimpleNamespace(resources={}), graph_resources=[], extra_context={}
        )


@pytest.fixture
def record_edition_checks_and_skip_conversion(monkeypatch):
    """Record every edition check and every converter, and skip the conversion itself.

    The edition check is replaced where ``app.py`` looks it up, because ``app.py`` imports it
    by name; replacing it in ``community_ingest`` would not be seen there.

    Returns:
        dict: ``edition_checks`` counts the checks; ``converters`` holds each converter that ran.
    """
    record = {"edition_checks": 0, "converters": []}

    def check_edition():
        # Returning nothing means "take the Enterprise route", like a real Enterprise answer.
        record["edition_checks"] += 1

    def skip_conversion(self, source_object, graph_resources, extra_context, **kwargs):
        record["converters"].append(self)
        return "skipped"

    monkeypatch.setattr(
        "openhound.core.app.community_edition_client_for_ingest", check_edition
    )
    monkeypatch.setattr(Converter, "run", skip_conversion)
    return record


def test_the_scheduler_path_never_asks_for_the_edition(
    record_edition_checks_and_skip_conversion, collected_faker_data
):
    """The Enterprise scheduler calls the collector's converter directly, like this."""
    collected, lookup_file, _ = collected_faker_data
    extension = next(
        collector
        for collector in CollectorManager.from_entrypoint().collectors
        if collector.name == "faker"  # pyright: ignore[reportAttributeAccessIssue]
    )

    extension.converter(
        input_path=collected,
        lookup_file=lookup_file,
        method=Method.ingest,
        progress=Progress.log,
    )

    record = record_edition_checks_and_skip_conversion
    assert record["edition_checks"] == 0
    assert record["converters"][0].sends_to_community_edition is False


def test_the_command_line_asks_for_the_edition(
    record_edition_checks_and_skip_conversion, collected_faker_data, tmp_path
):
    """The control for the test above: the command line does ask, so the check is watched."""
    collected, lookup_file, _ = collected_faker_data

    result = run_convert_command(collected, lookup_file, tmp_path, "--method", "ingest")

    assert result.exit_code == 0, result.output
    assert record_edition_checks_and_skip_conversion["edition_checks"] == 1
