from uuid import UUID

import pytest
import requests

import openhound
from openhound.core.clients.bloodhound import BloodHoundHTTPError
from openhound.core.clients.bloodhound_enterprise import BloodHoundEnterprise

JOB_ID = UUID("6bc0857e-9d9b-48c6-bbd8-77a31c5d351d")


@pytest.fixture(autouse=True)
def supported_package_version(monkeypatch):
    monkeypatch.setattr(openhound, "__version__", "0.3.0rc1")


@pytest.mark.parametrize("encoding", [None, "gzip"])
def test_upload_uses_job_endpoint_and_accepts_empty_202(monkeypatch, encoding):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 202
        response._content = b""
        return response

    monkeypatch.setattr(requests, "request", request)
    client = BloodHoundEnterprise("key", "token-id", "https://bhe.example")
    data = b"collected file"

    client.upload_collector_job_data(JOB_ID, data, "application/json", encoding)

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == (
        f"https://bhe.example/api/v2/collie-job-queue/{JOB_ID}/ingest"
    )
    assert call["data"] == data
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"].get("Content-Encoding") == encoding
    assert call["headers"]["Authorization"] == "bhesignature token-id"
    assert call["timeout"] == (10, 120)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 415, 500])
def test_upload_propagates_endpoint_errors(monkeypatch, status):
    def request(**kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = b"upload rejected"
        return response

    monkeypatch.setattr(requests, "request", request)
    client = BloodHoundEnterprise("key", "token-id")

    with pytest.raises(BloodHoundHTTPError) as error:
        client.upload_collector_job_data(JOB_ID, b"{}", "application/json")

    assert error.value.code == status


def test_upload_rejects_legacy_numeric_job_id_before_request(monkeypatch):
    def unexpected_request(**kwargs):
        raise AssertionError("Legacy jobs must not use the managed ingest endpoint")

    monkeypatch.setattr(requests, "request", unexpected_request)
    client = BloodHoundEnterprise("key", "token-id")

    with pytest.raises(ValueError):
        client.upload_collector_job_data("123", b"{}", "application/json")


def test_stream_upload_signs_entire_body_and_rewinds_after_401(monkeypatch):
    import base64
    import hashlib
    import hmac
    from io import BytesIO

    from openhound.core.clients.bhe_credentials import BHECredentials

    content = b'{"graph":{"nodes":[],"edges":[]}}' * 50000
    stream = BytesIO(content)
    calls = []

    def request(**kwargs):
        # The HMAC pass must leave the file ready for the HTTP transport.
        assert kwargs["data"].tell() == 0
        uploaded = kwargs["data"].read()
        assert uploaded == content
        headers = kwargs["headers"]
        token_key = "old-key" if not calls else "new-key"
        digest = hmac.new(token_key.encode(), None, hashlib.sha256)
        digest.update(f"POST/api/v2/collie-job-queue/{JOB_ID}/ingest".encode())
        digest = hmac.new(digest.digest(), None, hashlib.sha256)
        digest.update(headers["RequestDate"][:13].encode())
        digest = hmac.new(digest.digest(), uploaded, hashlib.sha256)
        assert headers["Signature"] == base64.b64encode(digest.digest()).decode()
        calls.append(headers["Authorization"])
        response = requests.Response()
        response.status_code = 401 if len(calls) == 1 else 202
        response._content = b""
        return response

    monkeypatch.setattr(requests, "request", request)
    client = BloodHoundEnterprise(
        "old-key",
        "old-id",
        credential_refresh=lambda: BHECredentials("new-key", "new-id"),
    )
    client.upload_collector_job_data(JOB_ID, stream)
    assert calls == ["bhesignature old-id", "bhesignature new-id"]
