"""Tests for ``openhound schema upload`` and the pieces underneath it.

A small fake BloodHound API, built with FastAPI, stands in for the real server. Every request
the OpenHound client makes is redirected to it, so the tests check the real request shape
(path, method, headers, body) without needing a running BloodHound.
"""

import json
import subprocess
import sys
import zipfile
from importlib.metadata import EntryPoint
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openhound.cli.schema import schema as schema_cli
from openhound.core.clients.bloodhound import BloodHound, BloodHoundJWT
from openhound.core.clients.from_secrets import (
    MissingBloodHoundCredentials,
    bloodhound_client_from_secrets,
)
from openhound.core.schema_upload import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_ZIP,
    SchemaNotFound,
    find_packaged_schema,
    find_schema_files,
    upload_schema_files,
)

EXAMPLE_SCHEMA = {
    "schema": {
        "name": "GH",
        "display_name": "GitHub",
        "version": "v0.0.1",
        "namespace": "GH",
    },
    "node_kinds": [
        {"name": "GH_User", "display_name": "User", "is_display_kind": True}
    ],
    "relationship_kinds": [{"name": "GH_ContributedTo", "is_traversable": True}],
}


@pytest.fixture
def mock_bloodhound_api():
    """A fake BloodHound that remembers every extension it was sent.

    It answers 201 the first time a schema name is seen and 200 afterwards, exactly like
    the real "upsert" endpoint. A body without a top-level "schema" key is rejected with 400.
    """
    app = FastAPI()
    app.state.extensions = {}
    app.state.received = []

    @app.put("/api/v2/extensions")
    async def upsert_extension(request: Request):
        content_type = request.headers.get("content-type", "")
        body = await request.body()
        app.state.received.append({"content_type": content_type, "body": body})

        if content_type.startswith(CONTENT_TYPE_ZIP):
            # The real server unpacks the bundle; here it is enough to note that it arrived.
            name = "zip-bundle"
        elif content_type.startswith(CONTENT_TYPE_JSON):
            payload = json.loads(body)
            if "schema" not in payload:
                return Response(status_code=400, content="missing schema section")
            name = payload["schema"]["name"]
        else:
            return Response(status_code=415, content="unsupported content type")

        existed = name in app.state.extensions
        app.state.extensions[name] = body
        return Response(status_code=200 if existed else 201)

    return TestClient(app)


@pytest.fixture
def mock_bh_requests(monkeypatch, mock_bloodhound_api):
    """Send every ``requests.request`` call to the fake BloodHound instead of the network."""

    def mock_request(method, url, **kwargs):
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        # The OpenHound client passes the body as ``data``; the test client calls it ``content``.
        content = kwargs.pop("data", None)
        kwargs.pop("timeout", None)
        return mock_bloodhound_api.request(
            method.upper(), path, content=content, **kwargs
        )

    monkeypatch.setattr("requests.request", mock_request)


# Every environment variable that any of the three configuration sections could contribute.
ALL_SETTING_VARIABLES = [
    f"DESTINATION__{section}__{setting}"
    for section in ("BLOODHOUND", "BLOODHOUNDENTERPRISE")
    for setting in ("URL", "TOKEN", "TOKEN_ID", "TOKEN_KEY")
]


@pytest.fixture
def no_credentials(monkeypatch):
    """Start from a clean slate so settings on the developer's machine cannot leak into a test."""
    for name in ALL_SETTING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def bearer_credentials(no_credentials, monkeypatch):
    """Configure a bearer token so the client can be built from secrets."""
    monkeypatch.setenv("DESTINATION__BLOODHOUND__URL", "http://localhost:8080")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN", "test-bearer-token")


def write_schema(path: Path, payload: dict | None = None) -> Path:
    path.write_text(json.dumps(payload or EXAMPLE_SCHEMA))
    return path


# --- Finding files -----------------------------------------------------------------------


def test_find_schema_files_accepts_a_single_file(tmp_path):
    schema_file = write_schema(tmp_path / "schema.json")
    assert find_schema_files(str(schema_file)) == [schema_file]


def test_find_schema_files_lists_json_and_zip_in_a_folder(tmp_path):
    second = write_schema(tmp_path / "b.json")
    first = write_schema(tmp_path / "a.json")
    bundle = tmp_path / "c.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("schema.json", json.dumps(EXAMPLE_SCHEMA))
    (tmp_path / "notes.txt").write_text("ignored")

    assert find_schema_files(str(tmp_path)) == [first, second, bundle]


def test_find_schema_files_explains_an_empty_folder(tmp_path):
    with pytest.raises(SchemaNotFound, match="contains no .json or .zip"):
        find_schema_files(str(tmp_path))


def test_find_schema_files_explains_an_unknown_target(monkeypatch):
    monkeypatch.setattr("openhound.core.schema_upload.entry_points", lambda group: [])
    with pytest.raises(SchemaNotFound, match="not the name of an installed collector"):
        find_schema_files("does-not-exist")


def test_find_packaged_schema_reads_the_collectors_own_schema(tmp_path, monkeypatch):
    """A collector registered as ``fake = openhound_fake.main:app`` ships schema.json in its package."""
    package_dir = tmp_path / "openhound_fake"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    expected = write_schema(package_dir / "schema.json")
    monkeypatch.syspath_prepend(str(tmp_path))

    fake_entry_point = EntryPoint(
        name="fake", value="openhound_fake.main:app", group="openhound.sources"
    )
    monkeypatch.setattr(
        "openhound.core.schema_upload.entry_points", lambda group: [fake_entry_point]
    )

    assert find_packaged_schema("fake") == expected
    assert find_schema_files("fake") == [expected]
    assert find_packaged_schema("other") is None


# --- Uploading ---------------------------------------------------------------------------


def test_upload_creates_then_updates(mock_bh_requests, mock_bloodhound_api, tmp_path):
    schema_file = write_schema(tmp_path / "schema.json")
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")

    first = upload_schema_files(client, [schema_file])
    second = upload_schema_files(client, [schema_file])

    assert [result.outcome for result in first] == ["created"]
    assert [result.outcome for result in second] == ["updated"]
    assert list(mock_bloodhound_api.app.state.extensions) == ["GH"]
    sent = mock_bloodhound_api.app.state.received[0]
    assert sent["content_type"] == CONTENT_TYPE_JSON
    assert json.loads(sent["body"]) == EXAMPLE_SCHEMA


def test_upload_sends_zip_bundles_with_the_zip_content_type(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("schema.json", json.dumps(EXAMPLE_SCHEMA))
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")

    results = upload_schema_files(client, [bundle])

    assert results[0].outcome == "created"
    assert mock_bloodhound_api.app.state.received[0]["content_type"] == CONTENT_TYPE_ZIP


def test_upload_reports_invalid_json_without_sending_it(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")
    good = write_schema(tmp_path / "good.json")
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")

    results = upload_schema_files(client, [broken, good])

    assert results[0].outcome == "failed"
    assert "not valid JSON" in results[0].detail
    assert results[1].outcome == "created"
    # Only the good file reached the server.
    assert len(mock_bloodhound_api.app.state.received) == 1


def test_upload_reports_server_rejections_per_file(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    rejected = write_schema(tmp_path / "rejected.json", {"node_kinds": []})
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")

    results = upload_schema_files(client, [rejected])

    assert results[0].outcome == "failed"
    assert "missing schema section" in results[0].detail


def test_upload_sends_a_bounded_timeout(monkeypatch, tmp_path):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(kwargs)
        raise requests.ConnectTimeout("timed out")

    monkeypatch.setattr("requests.request", fake_request)
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")

    upload_schema_files(client, [write_schema(tmp_path / "schema.json")])

    assert seen["timeout"] == (10, 120)


def test_upload_reports_network_errors_and_continues(monkeypatch, tmp_path):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.request", fake_request)
    client = BloodHoundJWT(token="t", base_uri="http://localhost:8080")
    first = write_schema(tmp_path / "a.json")
    second = write_schema(tmp_path / "b.json")

    results = upload_schema_files(client, [first, second])

    assert [r.outcome for r in results] == ["failed", "failed"]
    assert "connection refused" in results[0].detail
    assert len(calls) == 2  # the second file was still attempted


def test_dry_run_reads_files_but_sends_nothing(
    mock_bh_requests, mock_bloodhound_api, tmp_path
):
    schema_file = write_schema(tmp_path / "schema.json")

    results = upload_schema_files(client=None, files=[schema_file], dry_run=True)  # type: ignore[arg-type]

    assert results[0].outcome == "skipped"
    assert mock_bloodhound_api.app.state.received == []


# --- Choosing credentials ----------------------------------------------------------------


def test_client_from_secrets_prefers_the_api_token_pair(no_credentials, monkeypatch):
    monkeypatch.setenv("DESTINATION__BLOODHOUND__URL", "http://localhost:8080")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN_ID", "id")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN_KEY", "key")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN", "bearer")

    client = bloodhound_client_from_secrets()

    assert isinstance(client, BloodHound)
    assert client.base_uri == "http://localhost:8080"


def test_client_from_secrets_falls_back_to_the_bearer_token(bearer_credentials):
    client = bloodhound_client_from_secrets()
    assert isinstance(client, BloodHoundJWT)


def test_client_from_secrets_reads_the_existing_enterprise_section(
    no_credentials, monkeypatch
):
    """An Enterprise user whose ingest already works needs no new configuration."""
    monkeypatch.setenv(
        "DESTINATION__BLOODHOUNDENTERPRISE__URL", "https://tenant.example"
    )
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_ID", "id")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_KEY", "key")

    client = bloodhound_client_from_secrets()

    assert isinstance(client, BloodHound)
    assert client.base_uri == "https://tenant.example"


def test_client_from_secrets_prefers_the_bloodhound_section(
    no_credentials, monkeypatch
):
    monkeypatch.setenv("DESTINATION__BLOODHOUND__URL", "http://community")
    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN", "bearer")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__URL", "https://enterprise")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_ID", "id")
    monkeypatch.setenv("DESTINATION__BLOODHOUNDENTERPRISE__TOKEN_KEY", "key")

    client = bloodhound_client_from_secrets()

    assert client.base_uri == "http://community"


def test_client_from_secrets_reads_the_url_from_config_toml(no_credentials, tmp_path):
    """The address may live in config.toml, as OpenHound's example configurations show.

    dlt reads its settings files once per process, so this check runs a fresh interpreter in a
    folder that holds only a config.toml with the address and a secrets.toml with the token.
    """
    settings_dir = tmp_path / ".dlt"
    settings_dir.mkdir()
    (settings_dir / "config.toml").write_text(
        '[destination.bloodhound]\nurl = "http://from-config-toml"\n'
    )
    (settings_dir / "secrets.toml").write_text(
        '[destination.bloodhound]\ntoken = "from-secrets-toml"\n'
    )
    program = (
        "from openhound.core.clients.from_secrets import bloodhound_client_from_secrets\n"
        "client = bloodhound_client_from_secrets()\n"
        "print(type(client).__name__, client.base_uri)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.strip() == "BloodHoundJWT http://from-config-toml"


def test_client_from_secrets_explains_missing_settings(no_credentials, monkeypatch):
    with pytest.raises(MissingBloodHoundCredentials, match="No BloodHound credentials"):
        bloodhound_client_from_secrets()

    monkeypatch.setenv("DESTINATION__BLOODHOUND__TOKEN", "bearer")
    with pytest.raises(MissingBloodHoundCredentials, match="has no 'url'"):
        bloodhound_client_from_secrets()


# --- The command line --------------------------------------------------------------------


def test_cli_uploads_and_prints_one_line_per_file(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path
):
    schema_file = write_schema(tmp_path / "schema.json")

    result = CliRunner().invoke(schema_cli, ["upload", str(schema_file)])

    assert result.exit_code == 0, result.output
    assert "created" in result.output
    assert list(mock_bloodhound_api.app.state.extensions) == ["GH"]


def test_cli_dry_run_needs_no_credentials(
    mock_bh_requests, mock_bloodhound_api, no_credentials, tmp_path
):
    schema_file = write_schema(tmp_path / "schema.json")

    result = CliRunner().invoke(schema_cli, ["upload", str(schema_file), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert mock_bloodhound_api.app.state.received == []


def test_cli_exits_with_status_one_when_a_file_fails(
    mock_bh_requests, mock_bloodhound_api, bearer_credentials, tmp_path
):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")

    result = CliRunner().invoke(schema_cli, ["upload", str(broken)])

    assert result.exit_code == 1
    assert "not valid JSON" in result.output


def test_cli_explains_an_unknown_target(bearer_credentials, monkeypatch):
    monkeypatch.setattr("openhound.core.schema_upload.entry_points", lambda group: [])

    result = CliRunner().invoke(schema_cli, ["upload", "nope"])

    assert result.exit_code == 1
    assert "not the name of an installed collector" in result.output
