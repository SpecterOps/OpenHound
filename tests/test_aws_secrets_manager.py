import base64
import errno
import json
import logging
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn, cast

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConfigNotFound,
    CredentialRetrievalError,
    EndpointConnectionError,
    HTTPClientError,
    InvalidConfigError,
    MetadataRetrievalError,
    NoCredentialsError,
    NoRegionError,
    ParamValidationError,
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
SECRET_ID = "openhound/bhe"
SECRET_VALUE = "secret-value-that-must-not-be-logged"
PROVIDER_MESSAGE = (
    "role arn:aws:iam::123456789012:role/openhound cannot read the secret"
)


@pytest.fixture(autouse=True)
def restore_sdk_logging():
    def sdk_loggers():
        return {
            name: sdk_logger
            for name, sdk_logger in logging.Logger.manager.loggerDict.copy().items()
            if name in ("boto3", "botocore") or name.startswith(("boto3.", "botocore."))
            if isinstance(sdk_logger, logging.Logger)
        }

    original = {
        name: (
            sdk_logger.level,
            sdk_logger.disabled,
            sdk_logger.propagate,
            sdk_logger.handlers[:],
        )
        for name, sdk_logger in sdk_loggers().items()
    }
    try:
        yield
    finally:
        for name, sdk_logger in sdk_loggers().items():
            level, disabled, propagate, handlers = original.get(
                name, (logging.NOTSET, False, True, [])
            )
            sdk_logger.setLevel(level)
            sdk_logger.disabled = disabled
            sdk_logger.propagate = propagate
            sdk_logger.handlers[:] = handlers


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

    def batch_get_secret_value(self, *, SecretIdList: list[str]) -> Mapping[str, Any]:
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


def assert_safe_logs(caplog: pytest.LogCaptureFixture, forbidden: set[str]) -> None:
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
                    {"ARN": json_secret, "SecretString": '{"value": 2}'},
                    {"Name": "text-secret", "SecretString": "value-1"},
                ],
                "Errors": [],
            }
        ]
    )

    result = AWSSecretsManager(client).get_secrets(["text-secret", json_secret])

    assert result == {"text-secret": "value-1", json_secret: {"value": 2}}
    assert list(result) == ["text-secret", json_secret]
    assert client.batch_requests == [["text-secret", json_secret]]
    assert client.get_requests == []


def test_get_secrets_splits_requests_into_chunks_of_20_and_merges_values() -> None:
    secret_ids = [f"secret-{index}" for index in range(41)]
    responses = [
        {
            "SecretValues": [
                {"Name": secret_id, "SecretString": f"value-{secret_id}"}
                for secret_id in reversed(secret_ids[start : start + 20])
            ]
        }
        for start in range(0, len(secret_ids), 20)
    ]
    client = FakeSecretsManagerClient(batch_responses=responses)

    result = AWSSecretsManager(client).get_secrets(secret_ids)

    assert result == {secret_id: f"value-{secret_id}" for secret_id in secret_ids}
    assert list(result) == secret_ids
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
    forbidden = {SECRET_VALUE}

    with (
        caplog.at_level(logging.DEBUG, logger=LOGGER_NAME),
        pytest.raises(SecretBatchError) as raised,
    ):
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
        if getattr(record, "outcome", None) == "failure"
    ]
    assert len(error_records) == 2
    assert first_failure in caplog.text
    assert "ResourceNotFoundException" in caplog.text
    assert PROVIDER_MESSAGE in caplog.text
    assert all(
        record.failure_category == "SecretBatchError" for record in error_records
    )
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

    assert raised.value.context["sdk_exception_type"] == type(error).__name__
    if isinstance(error, ClientError):
        assert PROVIDER_MESSAGE in str(raised.value)
    else:
        assert "https://secretsmanager.example" in str(raised.value)


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

    with pytest.raises(AWSConfigurationError) as raised:
        AWSSecretsManager().get_secret(SECRET_ID)
    assert type(sdk_error).__name__ in str(raised.value)
    assert raised.value.context["sdk_exception_type"] == type(sdk_error).__name__
    if isinstance(sdk_error, NoRegionError):
        assert "AWS_DEFAULT_REGION" in str(raised.value)
        assert "selected AWS profile" in str(raised.value)
    elif not isinstance(sdk_error, CredentialRetrievalError):
        assert str(sdk_error) in str(raised.value)


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


def test_bhe_binary_json_secret_is_decoded_in_memory(caplog) -> None:
    client = FakeSecretsManagerClient(
        get_response={
            "SecretBinary": b'{"credential":"secret-value-that-must-not-be-logged"}'
        }
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        result = AWSSecretsManager(client).get_secret(SECRET_ID)
    assert result == {"credential": SECRET_VALUE}
    assert_safe_logs(caplog, {SECRET_ID, SECRET_VALUE})


def test_invalid_binary_secret_is_safely_classified(caplog) -> None:
    client = FakeSecretsManagerClient(
        get_response={"SecretBinary": b"\xffsecret-value-that-must-not-be-logged"}
    )
    with (
        caplog.at_level(logging.DEBUG, logger=LOGGER_NAME),
        pytest.raises(SecretRequestError) as raised,
    ):
        AWSSecretsManager(client).get_secret(SECRET_ID)
    assert_safe_failure(raised.value, {SECRET_ID, SECRET_VALUE})
    assert_safe_logs(caplog, {SECRET_ID, SECRET_VALUE})


def test_boto_client_uses_bounded_timeouts_and_retries(monkeypatch) -> None:
    captured = {}
    client = FakeSecretsManagerClient(get_response={"SecretString": SECRET_VALUE})

    def create_client(service, **kwargs):
        captured.update(service=service, **kwargs)
        return client

    monkeypatch.setattr(
        "openhound.core.clients.aws_secrets_manager.boto3.client", create_client
    )
    assert AWSSecretsManager().get_secret(SECRET_ID) == SECRET_VALUE
    config = captured["config"]
    assert captured["service"] == "secretsmanager"
    assert config.connect_timeout == 10
    assert config.read_timeout == 20
    assert config.retries == {"mode": "standard", "total_max_attempts": 4}


@pytest.mark.parametrize(
    "operation,response_kind,injected",
    [
        pytest.param("single", "string", False, id="single-string-created"),
        pytest.param("single", "binary", True, id="single-binary-injected"),
        pytest.param("single", "provider_error", False, id="single-error-created"),
        pytest.param("batch", "string", True, id="batch-string-injected"),
        pytest.param("batch", "binary", False, id="batch-binary-created"),
        pytest.param("batch", "provider_error", True, id="batch-error-injected"),
    ],
)
def test_real_sdk_http_pipeline_does_not_expose_secrets(
    monkeypatch, caplog, operation, response_kind, injected
) -> None:
    document = json.dumps({"credential": SECRET_VALUE})
    encoded_document = base64.b64encode(document.encode()).decode()
    secret = (
        {"SecretBinary": encoded_document}
        if response_kind == "binary"
        else {"SecretString": document}
    )
    if response_kind == "provider_error":
        response_body = {
            "__type": "AccessDeniedException",
            "message": f"{PROVIDER_MESSAGE}: {SECRET_ID}",
        }
        status = 400
    else:
        response_body = (
            {"SecretValues": [{"Name": SECRET_ID, **secret}], "Errors": []}
            if operation == "batch"
            else secret
        )
        status = 200
    raw_body = json.dumps(response_body).encode()
    forbidden = {
        SECRET_VALUE,
        encoded_document,
        raw_body.decode(),
    }
    requests = []
    clients = []

    class RawResponse:
        def stream(self, *_args, **_kwargs):
            yield raw_body

    def create_client(service, **kwargs):
        # This preexisting child has its own handler and explicit DEBUG level.
        logging.getLogger("botocore.endpoint").debug(
            "SDK construction %s", SECRET_ID, extra={"payload": SECRET_VALUE}
        )
        session = boto3.Session(
            aws_access_key_id="fake-access-key",
            aws_secret_access_key="fake-secret-key",
            region_name="us-east-1",
        )
        client = session.client(service, **kwargs)
        clients.append(client)
        monkeypatch.setattr(client._endpoint.http_session, "send", send)
        return client

    def send(request):
        requests.append(request)
        # These children are first created after suppression; explicit DEBUG
        # must not bypass the namespace sink, even for ERROR records.
        for namespace in ("boto3.resources", "botocore.endpoint"):
            child = logging.getLogger(
                f"{namespace}.openhound_pipeline_{operation}_{response_kind}_{injected}"
            )
            child.setLevel(logging.DEBUG)
            child.error(
                "SDK response %s", PROVIDER_MESSAGE, extra={"payload": raw_body}
            )
        return AWSResponse(
            request.url,
            status,
            {"content-type": "application/x-amz-json-1.1"},
            RawResponse(),
        )

    monkeypatch.setattr(
        "openhound.core.clients.aws_secrets_manager.boto3.client", create_client
    )
    if injected:
        # An injected real client is constructed by its caller. Only the
        # wrapper's subsequent retrieval is under test in this case.
        client = boto3.Session(
            aws_access_key_id="fake-access-key",
            aws_secret_access_key="fake-secret-key",
            region_name="us-east-1",
        ).client("secretsmanager")
        clients.append(client)
        monkeypatch.setattr(client._endpoint.http_session, "send", send)
    else:
        client = None

    for name in (
        "boto3",
        "botocore",
        "boto3.resources",
        "botocore.endpoint",
        "botocore.parsers",
    ):
        sdk_logger = logging.getLogger(name)
        sdk_logger.disabled = False
        sdk_logger.setLevel(logging.DEBUG)
        sdk_logger.addHandler(caplog.handler)
        sdk_logger.propagate = False
    caplog.clear()

    try:
        with caplog.at_level(logging.DEBUG):
            manager = AWSSecretsManager(client)
            if response_kind == "provider_error":
                error_type = (
                    SecretBatchError if operation == "batch" else SecretPermissionError
                )
                with pytest.raises(error_type) as raised:
                    if operation == "batch":
                        manager.get_secrets([SECRET_ID])
                    else:
                        manager.get_secret(SECRET_ID)
                assert_safe_failure(raised.value, forbidden)
                formatted = "".join(traceback.format_exception(raised.value))
                assert all(value not in formatted for value in forbidden)
                if operation == "batch":
                    assert isinstance(
                        raised.value.failures[SECRET_ID], SecretPermissionError
                    )
                    assert_safe_failure(raised.value.failures[SECRET_ID], forbidden)
            elif operation == "batch":
                assert manager.get_secrets([SECRET_ID]) == {
                    SECRET_ID: {"credential": SECRET_VALUE}
                }
            else:
                assert manager.get_secret(SECRET_ID) == {"credential": SECRET_VALUE}
    finally:
        for sdk_client in clients:
            sdk_client.close()

    assert len(requests) == 1
    assert SECRET_ID in requests[0].body.decode()
    assert_safe_logs(caplog, forbidden)
    records = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert [getattr(record, "outcome", None) for record in records] == [
        "attempt",
        "failure" if response_kind == "provider_error" else "success",
    ]
    assert all(record.exc_info is None for record in records)
    assert not any(
        record.name.startswith(("boto3", "botocore")) for record in caplog.records
    )


@pytest.mark.parametrize("during_construction", [False, True])
def test_sts_failure_retains_actual_operation_and_metadata(
    monkeypatch, caplog, during_construction
):
    error = aws_error(
        "AccessDenied",
        "Role trust condition did not match",
        "AssumeRoleWithWebIdentity",
    )
    error.response["ResponseMetadata"].update(
        RequestId="request-123", HTTPStatusCode=403, RetryAttempts=2
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("openhound.core.clients.aws_secrets_manager.boto3.client", fail)
    manager = AWSSecretsManager(
        None if during_construction else FakeSecretsManagerClient(get_error=error)
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(AWSConfigurationError) as raised:
        manager.get_secret(SECRET_ID)
    text = str(raised.value)
    for expected in (
        "STS AssumeRoleWithWebIdentity",
        "AccessDenied",
        "Role trust condition",
        "role trust",
        "request-123",
        "HTTP 403",
        "SDK retries 2",
    ):
        assert expected in text
    assert "Secrets Manager denied" not in text
    assert raised.value.context["aws_operation"] == "AssumeRoleWithWebIdentity"
    assert raised.value.context["RetryAttempts"] == 2
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("batch", [False, True])
def test_unrelated_runtime_errors_are_classified_without_provider_output(caplog, batch):
    error = RuntimeError(SECRET_VALUE)
    client = FakeSecretsManagerClient(get_error=error, batch_responses=[error])
    manager = AWSSecretsManager(client)
    with caplog.at_level(logging.DEBUG):
        if batch:
            with pytest.raises(SecretBatchError) as raised:
                manager.get_secrets([SECRET_ID])
            failure = raised.value.failures[SECRET_ID]
        else:
            with pytest.raises(SecretRequestError) as raised:
                manager.get_secret(SECRET_ID)
            failure = raised.value

    assert isinstance(failure, SecretRequestError)
    assert str(failure) == "AWS secret retrieval failed (RuntimeError)"
    assert failure.context["sdk_exception_type"] == "RuntimeError"
    assert SECRET_VALUE not in "".join(traceback.format_exception(raised.value))
    assert_safe_logs(caplog, {SECRET_VALUE})


@pytest.mark.parametrize(
    "code,expected_type",
    [
        ("ExpiredTokenException", AWSConfigurationError),
        ("UnrecognizedClientException", AWSConfigurationError),
        ("ThrottlingException", SecretRequestError),
        ("InternalServiceError", SecretRequestError),
    ],
)
def test_service_failures_preserve_code_message_region_and_secret(code, expected_type):
    from types import SimpleNamespace

    client = FakeSecretsManagerClient(get_error=aws_error(code, PROVIDER_MESSAGE + "."))
    client.meta = SimpleNamespace(region_name="us-west-2")
    with pytest.raises(expected_type) as raised:
        AWSSecretsManager(client).get_secret(SECRET_ID)
    for expected in (code, PROVIDER_MESSAGE, "GetSecretValue", SECRET_ID, "us-west-2"):
        assert expected in str(raised.value)
    assert raised.value.context["region"] == "us-west-2"
    assert "all attempts" not in str(raised.value)
    assert ".." not in str(raised.value)


@pytest.mark.parametrize(
    "error,explanation",
    [
        (
            CredentialRetrievalError(provider="custom-process", error_msg=SECRET_VALUE),
            "custom-process",
        ),
        (
            CredentialRetrievalError(
                provider="container-role",
                error_msg=f"Received non 200 response 403 from container metadata: {SECRET_VALUE}",
            ),
            "container-role",
        ),
        (
            ParamValidationError(report=f"Invalid WebIdentityToken: {SECRET_VALUE}"),
            "parameter validation",
        ),
        (
            MetadataRetrievalError(
                error_msg=f"Received non 200 response 401 from container metadata: {SECRET_VALUE}"
            ),
            "HTTP 401",
        ),
        (HTTPClientError(error=ValueError(SECRET_VALUE)), "ValueError"),
    ],
)
def test_unsafe_provider_and_validation_details_are_omitted(error, explanation, caplog):
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises((AWSConfigurationError, SecretRequestError)) as raised,
    ):
        AWSSecretsManager(FakeSecretsManagerClient(get_error=error)).get_secret(
            SECRET_ID
        )
    assert explanation in str(raised.value)
    assert "omitted" not in str(raised.value)
    if (
        isinstance(error, CredentialRetrievalError)
        and error.kwargs["provider"] == "container-role"
    ):
        assert "HTTP 403" in str(raised.value)
        assert raised.value.context["credential_endpoint_http_status"] == 403
    assert SECRET_VALUE not in "".join(traceback.format_exception(raised.value))
    assert_safe_logs(caplog, {SECRET_VALUE})


@pytest.mark.parametrize(
    "flag,retries,explanation",
    [
        ("MaxAttemptsReached", 3, "SDK maximum attempts reached"),
        ("RetryQuotaReached", 1, "SDK retry quota exhausted"),
    ],
)
def test_sdk_retry_exhaustion_is_reported_only_when_supplied(
    flag, retries, explanation
):
    error = aws_error("ThrottlingException", "Rate exceeded")
    error.response["ResponseMetadata"].update({"RetryAttempts": retries, flag: True})
    manager = AWSSecretsManager(FakeSecretsManagerClient(get_error=error))
    with pytest.raises(SecretRequestError) as raised:
        manager.get_secret(SECRET_ID)
    assert explanation in str(raised.value)
    assert f"SDK retries {retries}" in str(raised.value)
    assert raised.value.context[flag] is True

    del error.response["ResponseMetadata"][flag]
    with pytest.raises(SecretRequestError) as raised:
        manager.get_secret(SECRET_ID)
    assert explanation not in str(raised.value)
    assert flag not in raised.value.context


@pytest.mark.parametrize(
    "error_type,error_number,during_construction",
    [
        (FileNotFoundError, errno.ENOENT, False),
        (PermissionError, errno.EACCES, True),
    ],
)
def test_aws_file_access_failures_retain_path_and_reason_in_bootstrap_logs(
    monkeypatch, caplog, error_type, error_number, during_construction
):
    import os
    from types import SimpleNamespace

    from botocore.utils import FileWebIdentityTokenLoader

    from openhound.core.clients.bhe_credentials import AWSBHECredentials

    token_path = "/var/run/secrets/eks.amazonaws.com/serviceaccount/token"

    def fail_open(path):
        raise error_type(error_number, os.strerror(error_number), path) from ValueError(
            SECRET_VALUE
        )

    token_loader = FileWebIdentityTokenLoader(token_path, _open=fail_open)

    def fail(*args, **kwargs):
        return token_loader()

    monkeypatch.setattr("openhound.core.clients.aws_secrets_manager.boto3.client", fail)
    manager = AWSSecretsManager(
        None if during_construction else SimpleNamespace(get_secret_value=fail)
    )
    with pytest.raises(AWSConfigurationError) as raised:
        manager.get_secret(SECRET_ID)
    assert raised.value.context["file_path"] == token_path
    assert raised.value.context["os_errno"] == error_number
    assert SECRET_VALUE not in "".join(traceback.format_exception(raised.value))
    assert_safe_logs(caplog, {SECRET_VALUE})
    caplog.clear()

    def stop_polling(interval):
        assert interval == 17
        raise RuntimeError("stop bootstrap polling")

    monkeypatch.setattr(
        "openhound.core.clients.bhe_credentials.time.sleep", stop_polling
    )
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(RuntimeError, match="stop bootstrap polling"),
    ):
        AWSBHECredentials(SECRET_ID, manager).poll_until_available(17)
    assert len(caplog.records) == 1
    for expected in (
        error_type.__name__,
        token_path,
        os.strerror(error_number),
        "Retrying bootstrap retrieval in 17 seconds",
    ):
        assert expected in caplog.text
    assert "mounts and permissions" in caplog.text
    assert_safe_logs(caplog, {SECRET_VALUE})
    assert all(record.exc_info is None for record in caplog.records)
