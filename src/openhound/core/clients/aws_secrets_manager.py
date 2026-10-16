import json
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConfigNotFound,
    CredentialRetrievalError,
    HTTPClientError,
    InvalidConfigError,
    MetadataRetrievalError,
    NoCredentialsError,
    NoRegionError,
    ParamValidationError,
    PartialCredentialsError,
    ProfileNotFound,
    TokenRetrievalError,
)

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 20
_PROVIDER = "aws.secretsmanager"

# Bounded defaults for AWS control-plane requests. Client construction reuses
# this policy; injected clients retain their caller-supplied configuration.
DEFAULT_AWS_CLIENT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=20,
    retries={"mode": "standard", "total_max_attempts": 4},
)


def _configure_safe_sdk_logging() -> None:
    """Silence AWS SDK logs process-wide; they can contain requests and secrets.

    Existing children may have explicit DEBUG levels or their own handlers.
    Namespace sinks also catch newly created children, including DEBUG children.
    OpenHound's separate logger retains safe request/result context.
    """
    for namespace in ("boto3", "botocore"):
        sdk_logger = logging.getLogger(namespace)
        sdk_logger.setLevel(logging.CRITICAL + 1)
        sdk_logger.handlers.clear()
        sdk_logger.addHandler(logging.NullHandler())
        sdk_logger.propagate = False
    for name, child_logger in tuple(logging.Logger.manager.loggerDict.items()):
        if name.startswith(("boto3.", "botocore.")) and isinstance(
            child_logger, logging.Logger
        ):
            child_logger.disabled = True
            # Ancestor handlers still receive descendant records even when the
            # ancestor is disabled. Route new descendants to the namespace sink.
            child_logger.handlers.clear()
            child_logger.propagate = True


class SecretsManagerClient(Protocol):
    def get_secret_value(self, *, SecretId: str) -> Mapping[str, Any]: ...

    def batch_get_secret_value(
        self, *, SecretIdList: list[str]
    ) -> Mapping[str, Any]: ...


class SecretRetrievalError(Exception):
    """Base class for errors retrieving a secret."""

    failure_summary = "AWS secret retrieval failed"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class SecretNotFoundError(SecretRetrievalError):
    """The requested secret does not exist."""

    failure_summary = "Credential secret not found"


class SecretPermissionError(SecretRetrievalError):
    """The configured AWS identity cannot read the requested secret."""

    failure_summary = "AWS secret access denied"


class AWSConfigurationError(SecretRetrievalError):
    """The AWS SDK or Secrets Manager client is not configured correctly."""

    failure_summary = "AWS credentials unavailable"


class SecretRequestError(SecretRetrievalError):
    """AWS rejected a request for an otherwise unclassified reason."""


class SecretBatchError(SecretRetrievalError):
    """One or more requested secrets could not be retrieved."""

    def __init__(self, failures: Mapping[str, SecretRetrievalError]) -> None:
        self.failures = dict(failures)
        super().__init__(
            "; ".join(
                f"secret {secret_id}: {error}" for secret_id, error in failures.items()
            )
        )


SecretValue = str | dict[str, Any]


class AWSSecretsManager:
    def __init__(self, client: SecretsManagerClient | None = None) -> None:
        self._client = client

    def get_secret(self, secret_id: str) -> SecretValue:
        """Retrieve one text or JSON-object secret."""
        try:
            client = self._get_client()
        except SecretRetrievalError as error:
            _log_failure("get_secret_value", error, 1, 1)
            raise
        return self._retrieve_secret(client, secret_id)

    def get_secrets(self, secret_ids: Iterable[str]) -> dict[str, SecretValue]:
        """Retrieve any number of secrets in AWS-supported batch sizes."""
        if isinstance(secret_ids, str):
            raise TypeError("secret_ids must be an iterable of secret identifiers")
        requested_ids = list(secret_ids)
        if not requested_ids:
            return {}

        try:
            client = self._get_client()
        except SecretRetrievalError as error:
            failures = {secret_id: error for secret_id in requested_ids}
            _log_failure(
                "batch_get_secret_value",
                error,
                len(requested_ids),
                len(failures),
            )
            raise SecretBatchError(failures) from None
        return self._get_secrets_in_batches(client, requested_ids)

    def _get_secrets_in_batches(
        self, client: SecretsManagerClient, secret_ids: list[str]
    ) -> dict[str, SecretValue]:
        values: dict[str, SecretValue] = {}
        failures: dict[str, SecretRetrievalError] = {}
        for chunk_index, chunk in enumerate(_chunks(secret_ids, MAX_BATCH_SIZE)):
            _log_debug("batch_get_secret_value", "attempt", len(chunk), chunk_index + 1)
            try:
                response = client.batch_get_secret_value(SecretIdList=chunk)
            except (
                ClientError,
                BotoCoreError,
                FileNotFoundError,
                PermissionError,
                RuntimeError,
            ) as error:
                classified = _classify_aws_error(error, region=_client_region(client))
                chunk_values: dict[str, SecretValue] = {}
                chunk_failures = {secret_id: classified for secret_id in chunk}
            else:
                chunk_values, chunk_failures = _parse_batch_response(
                    response, chunk, _client_region(client)
                )

            values.update(chunk_values)
            failures.update(chunk_failures)
            if chunk_failures:
                _log_failure(
                    "batch_get_secret_value",
                    SecretBatchError(chunk_failures),
                    len(chunk),
                    len(chunk_failures),
                    chunk_index + 1,
                )
            else:
                _log_debug(
                    "batch_get_secret_value", "success", len(chunk), chunk_index + 1
                )

        if failures:
            raise SecretBatchError(failures) from None

        return {
            secret_id: values[secret_id]
            for secret_id in secret_ids
            if secret_id in values
        }

    def _get_client(self) -> SecretsManagerClient:
        _configure_safe_sdk_logging()
        if self._client is not None:
            return self._client
        try:
            return boto3.client(
                "secretsmanager",
                config=DEFAULT_AWS_CLIENT_CONFIG,
            )
        except (
            ClientError,
            BotoCoreError,
            FileNotFoundError,
            PermissionError,
            RuntimeError,
        ) as error:
            raise _classify_aws_error(error) from None

    def _retrieve_secret(
        self, client: SecretsManagerClient, secret_id: str
    ) -> SecretValue:
        _log_debug("get_secret_value", "attempt", 1)
        try:
            response = client.get_secret_value(SecretId=secret_id)
            value = _parse_secret_value(response)
        except (
            ClientError,
            BotoCoreError,
            FileNotFoundError,
            PermissionError,
            RuntimeError,
        ) as error:
            classified = _classify_aws_error(
                error, secret_id=secret_id, region=_client_region(client)
            )
            _log_failure("get_secret_value", classified, 1, 1)
            raise classified from None
        except SecretRetrievalError as error:
            _log_failure("get_secret_value", error, 1, 1)
            raise

        _log_debug("get_secret_value", "success", 1)
        return value


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _client_region(client: SecretsManagerClient) -> str | None:
    return getattr(getattr(client, "meta", None), "region_name", None)


def _parse_secret_value(response: Mapping[str, Any]) -> SecretValue:
    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        # boto3 already base64-decodes SecretBinary. BHE's secret writer uses
        # this representation for JSON documents.
        secret_binary = response.get("SecretBinary")
        if not isinstance(secret_binary, bytes):
            raise SecretRequestError(
                "AWS Secrets Manager response omitted a text payload"
            )
        try:
            secret_string = secret_binary.decode("utf-8")
        except UnicodeDecodeError:
            raise SecretRequestError("AWS secret payload is not UTF-8 text") from None

    if secret_string.lstrip().startswith("{"):
        try:
            parsed = json.loads(secret_string)
        except json.JSONDecodeError:
            return secret_string
        if isinstance(parsed, dict):
            return parsed

    return secret_string


def _parse_batch_response(
    response: Mapping[str, Any],
    requested_ids: list[str],
    region: str | None = None,
) -> tuple[dict[str, SecretValue], dict[str, SecretRetrievalError]]:
    values: dict[str, SecretValue] = {}
    failures: dict[str, SecretRetrievalError] = {}
    for entry in response.get("SecretValues", []):
        secret_id = next(
            (
                entry.get(field)
                for field in ("Name", "ARN")
                if entry.get(field) in requested_ids
            ),
            None,
        )
        if not isinstance(secret_id, str):
            continue
        try:
            values[secret_id] = _parse_secret_value(entry)
        except SecretRetrievalError as value_error:
            failures[secret_id] = value_error

    for entry in response.get("Errors", []):
        secret_id = entry.get("SecretId")
        if isinstance(secret_id, str) and secret_id in requested_ids:
            failures[secret_id] = _classify_aws_error(
                ClientError(
                    {
                        "Error": {
                            "Code": entry.get("ErrorCode"),
                            "Message": entry.get("Message", ""),
                        },
                        "ResponseMetadata": response.get("ResponseMetadata", {}),
                    },
                    "BatchGetSecretValue",
                ),
                secret_id=secret_id,
                region=region,
            )

    for secret_id in requested_ids:
        if secret_id not in values and secret_id not in failures:
            failures[secret_id] = SecretRequestError(
                "AWS batch response omitted a requested secret"
            )

    return values, failures


def _classify_aws_error(
    error: ClientError
    | BotoCoreError
    | FileNotFoundError
    | PermissionError
    | RuntimeError,
    *,
    secret_id: str | None = None,
    region: str | None = None,
) -> SecretRetrievalError:
    """Map AWS SDK/provider failures to payload-free retrieval exceptions."""
    context: dict[str, Any] = {"sdk_exception_type": type(error).__name__}
    location = ""
    if secret_id is not None:
        context["secret_id"] = secret_id
        location += f" for secret {secret_id}"
    if region is not None:
        context["region"] = region
        location += f" in {region}"
    if isinstance(error, RuntimeError):
        # Botocore uses a plain RuntimeError for this credential refresh failure.
        expired_refresh_message = "Credentials were refreshed, but the refreshed credentials are still expired."
        if str(error) == expired_refresh_message:
            return AWSConfigurationError(
                f"{expired_refresh_message} Check the credential provider and system clock.",
                **context,
            )
        return SecretRequestError(
            f"AWS secret retrieval failed ({type(error).__name__})",
            **context,
        )
    if isinstance(error, (FileNotFoundError, PermissionError)):
        reason = error.strerror or (
            "File not found"
            if isinstance(error, FileNotFoundError)
            else "Permission denied"
        )
        if error.filename is not None:
            context["file_path"] = error.filename
            reason += f": {error.filename}"
        if error.errno is not None:
            context["os_errno"] = error.errno
        return AWSConfigurationError(
            f"AWS file access{location} failed ({type(error).__name__}): {reason}. "
            "Check the configured file path, mounts and permissions.",
            **context,
        )
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "Unknown")
        message = error.response.get("Error", {}).get("Message") or ""
        operation = error.operation_name
        context.update(aws_error_code=code, aws_operation=operation)
        metadata = error.response.get("ResponseMetadata", {})
        details = []
        for key, label in (
            ("RequestId", "Request ID"),
            ("HTTPStatusCode", "HTTP"),
            ("RetryAttempts", "SDK retries"),
        ):
            if metadata.get(key) not in (None, ""):
                context[key] = metadata[key]
                details.append(f"{label} {metadata[key]}")
        for key, label in (
            ("MaxAttemptsReached", "SDK maximum attempts reached"),
            ("RetryQuotaReached", "SDK retry quota exhausted"),
        ):
            if key in metadata:
                context[key] = metadata[key]
                if metadata[key] is True:
                    details.append(label)
        credential_operation = operation in {
            "AssumeRole",
            "AssumeRoleWithWebIdentity",
            "AssumeRoleWithSAML",
            "GetSessionToken",
            "GetRoleCredentials",
            "CreateToken",
        }
        error_type: type[SecretRetrievalError] = SecretRequestError
        guidance = ""
        if credential_operation:
            error_type = AWSConfigurationError
            service = (
                "STS "
                if operation.startswith("AssumeRole") or operation == "GetSessionToken"
                else "AWS "
            )
            action = f"Cannot acquire AWS credentials: {service}{operation}"
            if operation == "AssumeRoleWithWebIdentity" and code in {
                "AccessDenied",
                "AccessDeniedException",
            }:
                guidance = (
                    " Check workload identity configuration and role trust conditions."
                )
        else:
            # Only label the service when the operation identifies it unambiguously.
            service = (
                "Secrets Manager"
                if operation in {"GetSecretValue", "BatchGetSecretValue"}
                else "AWS"
            )
            action = f"{service} {operation}"
            if code == "ResourceNotFoundException":
                error_type = SecretNotFoundError
                guidance = " Check the secret identifier and region."
            elif code in {
                "AccessDenied",
                "AccessDeniedException",
                "AuthorizationError",
                "UnauthorizedOperation",
            }:
                error_type = SecretPermissionError
                guidance = " Check the AWS identity and resource permissions."
            elif code in {
                "UnrecognizedClientException",
                "InvalidClientTokenId",
                "ExpiredToken",
                "ExpiredTokenException",
                "InvalidSignatureException",
                "SignatureDoesNotMatch",
            }:
                error_type = AWSConfigurationError
                guidance = " Check AWS credential validity and expiration."
        explanation = f"{action}{location} returned {code}"
        if message:
            explanation += f": {message.rstrip('.')}"
        return error_type(
            f"{explanation}.{guidance}"
            + (" " + ", ".join(details) + "." if details else ""),
            **context,
        )
    if isinstance(
        error,
        (
            ConfigNotFound,
            CredentialRetrievalError,
            InvalidConfigError,
            MetadataRetrievalError,
            NoCredentialsError,
            NoRegionError,
            PartialCredentialsError,
            ProfileNotFound,
            TokenRetrievalError,
        ),
    ):
        provider = error.kwargs.get("provider")
        if isinstance(error, MetadataRetrievalError) or (
            isinstance(error, CredentialRetrievalError) and provider == "container-role"
        ):
            # Container metadata errors can embed the entire credential response body.
            status = re.search(
                r"Received non 200 response (\d{3}) from container metadata:",
                error.kwargs.get("error_msg", ""),
            )
            explanation = "AWS credential metadata retrieval failed"
            if provider is not None:
                explanation += f" from provider {provider}"
            if status is not None:
                context["credential_endpoint_http_status"] = int(status[1])
                explanation += f": credential endpoint returned HTTP {status[1]}"
            explanation += (
                ". Check the metadata endpoint and workload credential configuration."
            )
        elif (
            isinstance(error, CredentialRetrievalError) and provider == "custom-process"
        ):
            # credential_process failures can embed helper stderr.
            explanation = "AWS credential_process helper failed to provide credentials. Check the helper command and its configuration (provider custom-process)."
        elif isinstance(error, NoRegionError):
            explanation = "AWS region is missing. Set AWS_DEFAULT_REGION or configure region in the selected AWS profile."
        elif isinstance(error, NoCredentialsError):
            explanation = f"{error}. Check AWS credential provider configuration (workload role, environment or selected AWS profile)."
        else:
            explanation = str(error)
        return AWSConfigurationError(
            f"{explanation} ({type(error).__name__})", **context
        )
    if isinstance(error, ParamValidationError):
        # Validation reports can echo WebIdentityToken and other authentication inputs.
        explanation = "AWS SDK parameter validation failed; check the parameters for secret retrieval or credential acquisition."
    elif type(error) is HTTPClientError:
        # The generic HTTP wrapper echoes arbitrary nested exceptions; specific
        # SDK transport errors retain useful endpoint/TLS/timeout explanations.
        explanation = "AWS HTTP transport failed."
        transport_error = error.kwargs.get("error")
        if isinstance(transport_error, Exception):
            context["transport_exception_type"] = type(transport_error).__name__
            explanation = (
                f"AWS HTTP transport failed: {type(transport_error).__name__}."
            )
    else:
        explanation = str(error)
    return SecretRequestError(
        f"AWS secret retrieval{location} failed ({type(error).__name__}): {explanation}",
        **context,
    )


def _log_debug(
    operation: str,
    outcome: str,
    secret_count: int,
    batch_number: int | None = None,
) -> None:
    extra: dict[str, str | int] = {
        "provider": _PROVIDER,
        "operation": operation,
        "outcome": outcome,
        "secret_count": secret_count,
    }
    if batch_number is not None:
        extra["batch_number"] = batch_number
    message = (
        "Secret retrieval attempt"
        if outcome == "attempt"
        else "Secret retrieval result"
    )
    logger.debug(message, extra=extra)


def _log_failure(
    operation: str,
    error: SecretRetrievalError,
    secret_count: int,
    failure_count: int,
    batch_number: int | None = None,
) -> None:
    extra: dict[str, str | int] = {
        "provider": _PROVIDER,
        "operation": operation,
        "outcome": "failure",
        "failure_category": type(error).__name__,
        "secret_count": secret_count,
        "failure_count": failure_count,
    }
    if batch_number is not None:
        extra["batch_number"] = batch_number
    extra.update(error.context)
    # Managed credential callers report the cause at normal logging levels.
    logger.debug("AWS %s failed: %s", operation, error, extra=extra)
