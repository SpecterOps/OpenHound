import logging
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn, cast

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConfigNotFound,
    CredentialRetrievalError,
    EndpointConnectionError,
    InvalidConfigError,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
    ProfileNotFound,
)

from openhound.core.clients.aws_secrets_manager import (
    AWSConfigurationError,
    AWSSecretsManager,
    SecretBatchError,
    SecretNotFoundError,
    SecretPermissionError,
    SecretRequestError,
)

LOGGER_NAME = "openhound.core.clients.aws_secrets_manager"
SECRET_ID = "secret-id-that-must-not-be-logged"
SECRET_VALUE = "secret-value-that-must-not-be-logged"
PROVIDER_MESSAGE = "provider message that must not be exposed"


class FakeSecretsManagerClient:
    def __init__(
        self,
        *,
        get_response: Mapping[str, Any] | None = None,
        get_error: BaseException | None = None,
        batch_responses: Sequence[Mapping[str, Any] | BaseException] | None = None,
    ) -> None:
        self.get_response = get_response
        self.get_error = get_error
        self.batch_responses = list(batch_responses or [])
        self.get_requests: list[str] = []
        self.batch_requests: list[list[str]] = []

    def get_secret_value(self, *, SecretId: str) -> Mapping[str, Any]:
        self.get_requests.append(SecretId)
        if self.get_error is not None:
            raise self.get_error
        return self.get_response or {}

    def batch_get_secret_value(
        self, *, SecretIdList: list[str]
    ) -> Mapping[str, Any]:
        self.batch_requests.append(list(SecretIdList))
        if not self.batch_responses:
            return {"SecretValues": [], "Errors": []}
        response = self.batch_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def aws_error(
    code: str,
    message: str,
    operation: str = "GetSecretValue",
) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {
                "HTTPHeaders": {},
                "HTTPStatusCode": 400,
                "HostId": "",
                "RequestId": "",
                "RetryAttempts": 0,
            },
        },
        operation,
    )


def assert_safe_failure(error: BaseException, forbidden: set[str]) -> None:
    assert all(value not in str(error) for value in forbidden)


def assert_safe_logs(
    caplog: pytest.LogCaptureFixture, forbidden: set[str]
) -> None:
    assert all(value not in caplog.text for value in forbidden)
    assert all(
        all(value not in record.getMessage() for value in forbidden)
        and all(value not in repr(record.__dict__) for value in forbidden)
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("secret_text", "expected"),
    [
        (SECRET_VALUE, SECRET_VALUE),
        ('{"username":"user","enabled":true}', {"username": "user", "enabled": True}),
        ('{"malformed":', '{"malformed":'),
        ('[{"not": "an object result"}]', '[{"not": "an object result"}]'),
    ],
)
def test_get_secret_uses_get_secret_value_for_text_and_json_objects(
    secret_text: str, expected: str | dict[str, Any]
) -> None:
    client = FakeSecretsManagerClient(get_response={"SecretString": secret_text})

    assert AWSSecretsManager(client).get_secret(SECRET_ID) == expected
    assert client.get_requests == [SECRET_ID]
    assert client.batch_requests == []


def test_get_secrets_returns_text_and_json_objects_from_batch_values() -> None:
    json_secret = "json-secret"
    client = FakeSecretsManagerClient(
        batch_responses=[
            {
                "SecretValues": [
                    {"Name": "text-secret", "SecretString": "value-1"},
                    {"ARN": json_secret, "SecretString": '{"value": 2}'},
                ],
                "Errors": [],
            }
        ]
    )

    result = AWSSecretsManager(client).get_secrets(["text-secret", json_secret])

    assert result == {"text-secret": "value-1", json_secret: {"value": 2}}
    assert client.batch_requests == [["text-secret", json_secret]]
    assert client.get_requests == []


def test_get_secrets_splits_requests_into_chunks_of_20_and_merges_values() -> None:
    secret_ids = [f"secret-{index}" for index in range(41)]
    responses = [
        {
            "SecretValues": [
                {"Name": secret_id, "SecretString": f"value-{secret_id}"}
                for secret_id in secret_ids[start : start + 20]
            ]
        }
        for start in range(0, len(secret_ids), 20)
    ]
    client = FakeSecretsManagerClient(batch_responses=responses)

    result = AWSSecretsManager(client).get_secrets(secret_ids)

    assert result == {secret_id: f"value-{secret_id}" for secret_id in secret_ids}
    assert [len(request) for request in client.batch_requests] == [20, 20, 1]
    assert client.batch_requests == [
        secret_ids[:20],
        secret_ids[20:40],
        secret_ids[40:],
    ]


def test_get_secrets_continues_and_aggregates_typed_failures_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_ids = [f"secret-{index}" for index in range(21)]
    first_failure = secret_ids[2]
    second_failure = secret_ids[-1]
    client = FakeSecretsManagerClient(
        batch_responses=[
            {
                "SecretValues": [
                    {"Name": secret_id, "SecretString": SECRET_VALUE}
                    for secret_id in secret_ids[:20]
                    if secret_id != first_failure
                ],
                "Errors": [
                    {
                        "SecretId": first_failure,
                        "ErrorCode": "ResourceNotFoundException",
                        "Message": PROVIDER_MESSAGE,
                    }
                ],
            },
            aws_error(
                "AccessDeniedException",
                PROVIDER_MESSAGE,
                operation="BatchGetSecretValue",
            ),
        ]
    )
    forbidden = set(secret_ids) | {SECRET_VALUE, PROVIDER_MESSAGE}

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME), pytest.raises(
        SecretBatchError
    ) as raised:
        AWSSecretsManager(client).get_secrets(secret_ids)

    assert client.batch_requests == [secret_ids[:20], secret_ids[20:]]
    assert set(raised.value.failures) == {first_failure, second_failure}
    assert isinstance(raised.value.failures[first_failure], SecretNotFoundError)
    assert isinstance(raised.value.failures[second_failure], SecretPermissionError)
    assert_safe_failure(raised.value, forbidden)
    for failure in raised.value.failures.values():
        assert_safe_failure(failure, forbidden)

    attempts = [
        cast(Any, record)
        for record in caplog.records
        if getattr(record, "outcome", None) == "attempt"
    ]
    assert [record.batch_number for record in attempts] == [1, 2]
    error_records = [
        cast(Any, record)
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(error_records) == 2
    assert all(record.failure_category == "SecretBatchError" for record in error_records)
    assert_safe_logs(caplog, forbidden)


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (
            aws_error("ResourceNotFoundException", PROVIDER_MESSAGE),
            SecretNotFoundError,
        ),
        (
            aws_error("AccessDeniedException", PROVIDER_MESSAGE),
            SecretPermissionError,
        ),
        (
            EndpointConnectionError(endpoint_url="https://secretsmanager.example"),
            SecretRequestError,
        ),
    ],
)
def test_get_secret_classifies_aws_errors(
    error: BotoCoreError | ClientError,
    expected_type: type[Exception],
) -> None:
    client = FakeSecretsManagerClient(get_error=error)

    with pytest.raises(expected_type) as raised:
        AWSSecretsManager(client).get_secret(SECRET_ID)

    assert_safe_failure(raised.value, {SECRET_ID, PROVIDER_MESSAGE})


@pytest.mark.parametrize(
    "sdk_error",
    [
        NoCredentialsError(),
        PartialCredentialsError(provider="aws", cred_var="secret-key"),
        NoRegionError(),
        InvalidConfigError(error_msg=PROVIDER_MESSAGE),
        ConfigNotFound(path=PROVIDER_MESSAGE),
        ProfileNotFound(profile=PROVIDER_MESSAGE),
        CredentialRetrievalError(
            provider="credential-provider", error_msg=PROVIDER_MESSAGE
        ),
    ],
)
def test_client_creation_configuration_failures_are_classified(
    monkeypatch: pytest.MonkeyPatch,
    sdk_error: BotoCoreError,
) -> None:
    def fail_client(*_args: object, **_kwargs: object) -> NoReturn:
        raise sdk_error

    monkeypatch.setattr(
        "openhound.core.clients.aws_secrets_manager.boto3.client", fail_client
    )

    with pytest.raises(AWSConfigurationError):
        AWSSecretsManager().get_secret(SECRET_ID)


def test_successful_retrieval_logs_safe_attempt_and_result_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeSecretsManagerClient(get_response={"SecretString": SECRET_VALUE})

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        AWSSecretsManager(client).get_secret(SECRET_ID)

    records = [cast(Any, record) for record in caplog.records]
    assert [(record.levelno, record.outcome) for record in records] == [
        (logging.DEBUG, "attempt"),
        (logging.DEBUG, "success"),
    ]
    assert all(record.provider == "aws.secretsmanager" for record in records)
    assert all(record.operation == "get_secret_value" for record in records)
    assert all(record.secret_count == 1 for record in records)
    assert_safe_logs(caplog, {SECRET_ID, SECRET_VALUE})

    caplog.clear()
    batch_client = FakeSecretsManagerClient(
        batch_responses=[
            {
                "SecretValues": [
                    {"Name": "first", "SecretString": SECRET_VALUE},
                    {"Name": "second", "SecretString": "second-value"},
                ]
            }
        ]
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        AWSSecretsManager(batch_client).get_secrets(["first", "second"])

    batch_records = [cast(Any, record) for record in caplog.records]
    assert [(record.levelno, record.outcome) for record in batch_records] == [
        (logging.DEBUG, "attempt"),
        (logging.DEBUG, "success"),
    ]
    assert all(record.operation == "batch_get_secret_value" for record in batch_records)
    assert all(record.secret_count == 2 for record in batch_records)
    assert all(record.batch_number == 1 for record in batch_records)
    assert_safe_logs(
        caplog,
        {"first", "second", SECRET_VALUE, "second-value"},
    )


def test_get_secrets_rejects_a_string_instead_of_ids() -> None:
    with pytest.raises(TypeError, match="iterable of secret identifiers"):
        AWSSecretsManager(FakeSecretsManagerClient()).get_secrets(SECRET_ID)
