from unittest.mock import Mock

import pytest

from openhound.core.clients import bloodhound
from openhound.core.clients.bhe_version import (
    UnsupportedBHEVersion,
    render_bhe_version,
)
from openhound.core.clients.bloodhound import BloodHound, BloodHoundJWT


@pytest.mark.parametrize(
    ("package_version", "reported_version"),
    [
        ("0.3.0", "v0.3.0"),
        ("0.3.0rc1", "v0.3.0-rc1"),
        ("0.3.0dev1", "v0.3.0-dev1"),
        ("0.3.0.dev0", "v0.3.0-dev0"),
        ("0.3.0rc2.dev3", "v0.3.0-rc2.dev3"),
        ("12.34.56rc10", "v12.34.56-rc10"),
    ],
)
def test_render_bhe_version(package_version, reported_version):
    assert render_bhe_version(package_version) == reported_version


@pytest.mark.parametrize(
    "package_version",
    [
        "unknown",
        "",
        "0.3",
        "v0.3.0",
        "0.3.0-rc1",
        "0.3.0a1",
        "0.3.0b1",
        "0.3.0.post1",
        "0.3.0+container.1",
        "1!0.3.0",
        "01.3.0",
    ],
)
def test_render_bhe_version_rejects_unsupported_versions(package_version):
    with pytest.raises(
        UnsupportedBHEVersion,
        match="supported forms are MAJOR.MINOR.PATCH",
    ):
        render_bhe_version(package_version)


@pytest.mark.parametrize(
    ("client_factory", "authorization"),
    [
        (
            lambda: BloodHound(token_key="key", token_id="id"),
            "bhesignature id",
        ),
        (
            lambda: BloodHoundJWT(token="jwt"),
            "Bearer jwt",
        ),
    ],
)
def test_bhe_clients_use_reported_version(monkeypatch, client_factory, authorization):
    monkeypatch.setattr(bloodhound.openhound, "__version__", "0.3.0rc1")
    request = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(bloodhound.requests, "request", request)

    client_factory().request("GET", "/test")

    headers = request.call_args.kwargs["headers"]
    assert headers["User-Agent"] == "openhound/v0.3.0-rc1"
    assert headers["Authorization"] == authorization


def test_bhe_client_rejects_unsupported_version_before_request(monkeypatch):
    monkeypatch.setattr(bloodhound.openhound, "__version__", "unknown")
    request = Mock()
    monkeypatch.setattr(bloodhound.requests, "request", request)

    with pytest.raises(UnsupportedBHEVersion, match="'unknown'"):
        BloodHound(token_key="key", token_id="id")

    request.assert_not_called()
