import logging
from typing import Any

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
)

from openhound.core.clients.aws_secrets_manager import (
    AWSConfigurationError,
    AWSSecretsManager,
    InvalidSecretValueError,
    SecretBatchError,
    SecretNotFoundError,
    SecretPermissionError,
    SecretRequestError,
)


class FakeSecretsManagerClient:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error=None,
        batch_response: dict[str, Any] | None = None,
        batch_error=None,
    ) -> None:
        self.response = response
        self.error = error
        self.batch_response = batch_response
        self.batch_error = batch_error
        self.requested_secret_id = None
        self.batch_requests: list[list[str]] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        self.requested_secret_id = SecretId
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    def batch_get_secret_value(self, *, SecretIdList: list[str]) -> dict[str, Any]:
        self.batch_requests.append(SecretIdList)
        if self.batch_error is not None:
            raise self.batch_error
        assert self.batch_response is not None
        return self.batch_response


def aws_error(code: str, message: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "GetSecretValue",
    )


def test_get_secret_returns_plain_text_and_passes_secret_id() -> None:
    client = FakeSecretsManagerClient({"SecretString": "secret-value"})

    result = AWSSecretsManager(client).get_secret("secret-id")

    assert result == "secret-value"
    assert client.requested_secret_id == "secret-id"


def test_get_secret_returns_json_object() -> None:
    client = FakeSecretsManagerClient({"SecretString": '{"username":"user"}'})

    result = AWSSecretsManager(client).get_secret("secret-id")

    assert result == {"username": "user"}


@pytest.mark.parametrize(
    ("error_code", "exception_type"),
    [
        ("ResourceNotFoundException", SecretNotFoundError),
        ("AccessDeniedException", SecretPermissionError),
    ],
)
def test_get_secret_maps_aws_errors_without_leaking_values(
    error_code: str, exception_type: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    secret_value = "do-not-leak-this-value"
    client = FakeSecretsManagerClient(error=aws_error(error_code, secret_value))

    with caplog.at_level(logging.DEBUG), pytest.raises(exception_type) as raised:
        AWSSecretsManager(client).get_secret("secret-id")

    assert secret_value not in str(raised.value)
    assert secret_value not in caplog.text
    assert "secret-id" not in caplog.text
    assert "2d4e223a1833" not in caplog.text
    assert (
        sum(
            record.levelno == logging.ERROR
            and record.getMessage() == "Failure to retrieve secret"
            for record in caplog.records
        )
        == 1
    )
    messages = [record.getMessage() for record in caplog.records]
    assert "Secret retrieval attempt" in messages
    assert "Secret retrieval result" in messages


def test_get_secret_rejects_malformed_json_without_leaking_value() -> None:
    secret_value = '{"password": "do-not-leak-this-value"'
    client = FakeSecretsManagerClient({"SecretString": secret_value})

    with pytest.raises(InvalidSecretValueError) as raised:
        AWSSecretsManager(client).get_secret("secret-id")

    assert secret_value not in str(raised.value)


def test_get_secret_rejects_binary_payload() -> None:
    client = FakeSecretsManagerClient({"SecretBinary": b"secret-value"})

    with pytest.raises(InvalidSecretValueError, match="SecretBinary"):
        AWSSecretsManager(client).get_secret("secret-id")


def test_get_secret_classifies_network_failures_as_request_errors() -> None:
    client = FakeSecretsManagerClient(
        error=EndpointConnectionError(endpoint_url="https://secretsmanager.example")
    )

    with pytest.raises(SecretRequestError):
        AWSSecretsManager(client).get_secret("secret-id")


def test_get_secrets_returns_values_mapped_to_requested_ids() -> None:
    client = FakeSecretsManagerClient(
        batch_response={
            "SecretValues": [
                {"Name": "second", "SecretString": '{"value":2}'},
                {"Name": "first", "SecretString": "value-1"},
            ]
        }
    )

    result = AWSSecretsManager(client).get_secrets(["first", "second"])

    assert result == {"first": "value-1", "second": {"value": 2}}
    assert client.batch_requests == [["first", "second"]]


def test_get_secrets_falls_back_when_batch_api_is_unavailable() -> None:
    class LegacyClient:
        def __init__(self) -> None:
            self.requested_ids: list[str] = []

        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            self.requested_ids.append(SecretId)
            return {"SecretString": f"value-{SecretId}"}

    client = LegacyClient()

    result = AWSSecretsManager(client).get_secrets(["first", "second"])

    assert result == {"first": "value-first", "second": "value-second"}
    assert client.requested_ids == ["first", "second"]


def test_get_secrets_falls_back_when_batch_permission_is_missing() -> None:
    class BatchPermissionClient(FakeSecretsManagerClient):
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            self.requested_secret_id = SecretId
            return {"SecretString": f"value-{SecretId}"}

    client = BatchPermissionClient(
        batch_error=aws_error("AccessDeniedException", "batch permission required")
    )

    result = AWSSecretsManager(client).get_secrets(["first", "second"])

    assert result == {"first": "value-first", "second": "value-second"}
    assert client.batch_requests == [["first", "second"]]
    assert client.requested_secret_id == "second"


def test_get_secrets_splits_requests_into_chunks_of_20() -> None:
    secret_ids = [f"secret-{index}" for index in range(43)]

    def response_for(requested_ids: list[str]) -> dict[str, Any]:
        return {
            "SecretValues": [
                {"Name": secret_id, "SecretString": secret_id}
                for secret_id in reversed(requested_ids)
            ]
        }

    class ChunkingClient(FakeSecretsManagerClient):
        def batch_get_secret_value(self, *, SecretIdList: list[str]) -> dict[str, Any]:
            self.batch_requests.append(SecretIdList)
            return response_for(SecretIdList)

    client = ChunkingClient()

    result = AWSSecretsManager(client).get_secrets(secret_ids)

    assert list(result) == secret_ids
    assert [len(request) for request in client.batch_requests] == [20, 20, 3]


def test_get_secrets_raises_typed_error_for_partial_batch_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_value = "do-not-log-this-value"
    client = FakeSecretsManagerClient(
        batch_response={
            "SecretValues": [{"Name": "available", "SecretString": secret_value}],
            "Errors": [
                {
                    "SecretId": "missing",
                    "ErrorCode": "ResourceNotFoundException",
                    "ErrorMessage": secret_value,
                }
            ],
        }
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(SecretBatchError) as raised:
        AWSSecretsManager(client).get_secrets(["available", "missing"])

    assert isinstance(raised.value.failures["missing"], SecretNotFoundError)
    assert secret_value not in str(raised.value)
    assert secret_value not in caplog.text
    assert "missing" not in caplog.text
    assert "ffa63583dfa6" not in caplog.text
    assert (
        sum(
            record.levelno == logging.ERROR
            and record.getMessage() == "Failure to retrieve secret"
            for record in caplog.records
        )
        == 1
    )
    for record in caplog.records:
        record_text = repr(record.__dict__)
        assert "available" not in record_text
        assert "missing" not in record_text
        assert "ffa63583dfa6" not in record_text
        assert record.provider == "aws.secretsmanager"
        assert record.operation == "batch_get_secret_value"
        assert record.secret_count == 2


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NoCredentialsError(), AWSConfigurationError),
        (NoRegionError(), AWSConfigurationError),
        (
            EndpointConnectionError(endpoint_url="https://secretsmanager.example"),
            SecretRequestError,
        ),
        (aws_error("SomeOtherError", "do-not-leak-this-value"), SecretRequestError),
    ],
)
def test_get_secrets_classifies_sdk_failures(error, expected) -> None:
    client = FakeSecretsManagerClient(batch_error=error)

    with pytest.raises(SecretBatchError) as raised:
        AWSSecretsManager(client).get_secrets(["secret-id"])

    assert isinstance(raised.value.failures["secret-id"], expected)


def test_retrieval_logs_attempt_and_result_without_secret_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text_value = "plain-secret-value"
    json_value = '{"password":"json-secret-value"}'
    client = FakeSecretsManagerClient(
        batch_response={
            "SecretValues": [
                {"Name": "text-secret", "SecretString": text_value},
                {"Name": "json-secret", "SecretString": json_value},
            ]
        }
    )

    with caplog.at_level(logging.DEBUG):
        AWSSecretsManager(client).get_secrets(["text-secret", "json-secret"])

    messages = [record.getMessage() for record in caplog.records]
    assert "Secret retrieval attempt" in messages
    assert "Secret retrieval result" in messages
    forbidden_values = {
        "text-secret",
        "json-secret",
        text_value,
        json_value,
        "a5353f2b7585",
        "9bda9177a37d",
    }
    for record in caplog.records:
        record_text = repr(record.__dict__)
        assert all(value not in record_text for value in forbidden_values)
        assert record.provider == "aws.secretsmanager"
        assert record.operation == "batch_get_secret_value"
        assert record.secret_count == 2

    assert text_value not in caplog.text
    assert json_value not in caplog.text
    assert "Secret retrieval attempt" in messages
    assert "Secret retrieval result" in messages


def test_constructor_rejects_invalid_client() -> None:
    with pytest.raises(AWSConfigurationError, match="does not support"):
        AWSSecretsManager(object())


def test_constructor_does_not_create_an_aws_client(monkeypatch) -> None:
    monkeypatch.setattr(
        "openhound.core.clients.aws_secrets_manager.boto3.client",
        lambda *_: pytest.fail(),
    )

    AWSSecretsManager()
