import pytest

from openhound.core.clients.bhe_credentials import (
    AWSBHECredentials,
    BHECredentials,
    InvalidBHECredentialsSecret,
)


class FakeSecretsManager:
    def __init__(self, secret):
        self.secret = secret
        self.requested = []

    def get_secret(self, secret_id):
        self.requested.append(secret_id)
        return self.secret


def test_aws_bhe_credentials_loads_token_pair_from_json_secret():
    manager = FakeSecretsManager(
        {"token_id": "12345678-1234-1234-1234-123456789abc", "token_key": "key"}
    )

    result = AWSBHECredentials("production/bhe", manager).refresh()

    assert result == BHECredentials(
        token_id="12345678-1234-1234-1234-123456789abc", token_key="key"
    )
    assert manager.requested == ["production/bhe"]


@pytest.mark.parametrize(
    "secret",
    [
        "not-json",
        {"token_id": "not-a-uuid", "token_key": "key"},
        {"token_id": "   ", "token_key": "key"},
        {"token_id": 123, "token_key": "key"},
        {},
        {"token_id": "id"},
        {"token_key": "key"},
        {"token_id": "", "token_key": "key"},
    ],
)
def test_aws_bhe_credentials_rejects_invalid_secret_values(secret):
    with pytest.raises(InvalidBHECredentialsSecret):
        AWSBHECredentials("production/bhe", FakeSecretsManager(secret)).refresh()


def test_expired_sdk_refresh_logs_safe_cause(caplog):
    import logging
    from datetime import UTC, datetime, timedelta

    import boto3
    import botocore.session
    from botocore.credentials import RefreshableCredentials

    from openhound.core.clients.aws_secrets_manager import (
        AWSConfigurationError,
        AWSSecretsManager,
    )

    metadata = {
        "access_key": "private-access-key",
        "secret_key": "private-secret-key",
        "token": "private-session-token",
        "expiry_time": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    session = botocore.session.get_session()
    session._credentials = RefreshableCredentials.create_from_metadata(
        metadata=metadata, refresh_using=lambda: metadata, method="container-role"
    )
    client = boto3.Session(botocore_session=session).client(
        "secretsmanager", region_name="us-east-1"
    )
    provider = AWSBHECredentials("production/bhe", AWSSecretsManager(client))
    caplog.clear()

    with caplog.at_level(logging.INFO), pytest.raises(AWSConfigurationError) as raised:
        provider.refresh()

    assert raised.value.failure_summary == "AWS credentials unavailable"
    assert len(caplog.records) == 1
    assert "refreshed credentials are still expired" in caplog.text
    assert "production/bhe" in caplog.text
    assert "private-" not in repr([record.__dict__ for record in caplog.records])
    assert all(record.exc_info is None for record in caplog.records)


def test_unexpected_refresh_failure_logs_type_without_payload(caplog):
    import logging
    from types import SimpleNamespace

    error = ValueError("private-provider-output")

    def retrieve(secret_id):
        raise error

    provider = AWSBHECredentials("production/bhe", SimpleNamespace(get_secret=retrieve))
    with caplog.at_level(logging.INFO), pytest.raises(ValueError) as raised:
        provider.refresh()

    assert raised.value is error
    assert len(caplog.records) == 1
    assert "Unexpected credential reader failure (ValueError)" in caplog.text
    assert "production/bhe" in caplog.text
    assert "private-provider-output" not in repr(caplog.records[0].__dict__)
    assert caplog.records[0].exc_info is None


@pytest.mark.parametrize("failure", ["region", "provider", "document"])
def test_bootstrap_logs_each_failure_with_cause_and_delay(monkeypatch, caplog, failure):
    import logging
    from types import SimpleNamespace

    from botocore.exceptions import CredentialRetrievalError, NoRegionError

    from openhound.core.clients.aws_secrets_manager import AWSSecretsManager

    calls = []

    def retrieve(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            if failure == "document":
                return {"SecretString": '{"token_key":"private-token-key"}'}
            if failure == "provider":
                raise CredentialRetrievalError(
                    provider="custom-process", error_msg="private-token-key"
                )
            raise NoRegionError()
        return {
            "SecretString": '{"token_id":"12345678-1234-1234-1234-123456789abc","token_key":"private-token-key"}'
        }

    provider = AWSBHECredentials(
        "production/bhe", AWSSecretsManager(SimpleNamespace(get_secret_value=retrieve))
    )
    delays = []
    monkeypatch.setattr(
        "openhound.core.clients.bhe_credentials.time.sleep", delays.append
    )
    with caplog.at_level(logging.WARNING):
        assert provider.poll_until_available(17).token_key == "private-token-key"
    assert delays == [17, 17]
    assert len(caplog.records) == 2
    expected = {
        "region": "AWS_DEFAULT_REGION",
        "provider": "custom-process",
        "document": "non-empty token_id",
    }[failure]
    assert all(expected in record.getMessage() for record in caplog.records)
    assert all(
        "Retrying bootstrap retrieval in 17 seconds" in record.getMessage()
        for record in caplog.records
    )
    assert "production/bhe" in caplog.text
    assert "private-token-key" not in repr([r.__dict__ for r in caplog.records])
    assert all(record.exc_info is None for record in caplog.records)
