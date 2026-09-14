import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConfigNotFound,
    CredentialRetrievalError,
    InvalidConfigError,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
    ProfileNotFound,
)

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 20
_PROVIDER = "aws.secretsmanager"


class SecretsManagerClient(Protocol):
    def get_secret_value(self, *, SecretId: str) -> Mapping[str, Any]: ...

    def batch_get_secret_value(
        self, *, SecretIdList: list[str]
    ) -> Mapping[str, Any]: ...


class SecretRetrievalError(Exception):
    """Base class for errors retrieving a secret."""


class SecretNotFoundError(SecretRetrievalError):
    """The requested secret does not exist."""


class SecretPermissionError(SecretRetrievalError):
    """The configured AWS identity cannot read the requested secret."""


class AWSConfigurationError(SecretRetrievalError):
    """The AWS SDK or Secrets Manager client is not configured correctly."""


class SecretRequestError(SecretRetrievalError):
    """AWS rejected a request for an otherwise unclassified reason."""


class SecretBatchError(SecretRetrievalError):
    """One or more requested secrets could not be retrieved."""

    def __init__(self, failures: Mapping[str, SecretRetrievalError]) -> None:
        self.failures = dict(failures)
        super().__init__("One or more AWS secret retrievals failed")


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
            except (ClientError, BotoCoreError) as error:
                classified = _classify_aws_error(error)
                chunk_values: dict[str, SecretValue] = {}
                chunk_failures = {
                    secret_id: classified for secret_id in chunk
                }
            else:
                chunk_values, chunk_failures = _parse_batch_response(response, chunk)

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

        return values

    def _get_client(self) -> SecretsManagerClient:
        if self._client is not None:
            return self._client
        try:
            return boto3.client("secretsmanager")
        except BotoCoreError as error:
            raise _classify_aws_error(error) from None

    def _retrieve_secret(
        self, client: SecretsManagerClient, secret_id: str
    ) -> SecretValue:
        _log_debug("get_secret_value", "attempt", 1)
        try:
            response = client.get_secret_value(SecretId=secret_id)
            value = _parse_secret_value(response)
        except (ClientError, BotoCoreError) as error:
            classified = _classify_aws_error(error)
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


def _parse_secret_value(response: Mapping[str, Any]) -> SecretValue:
    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise SecretRequestError(
            "AWS Secrets Manager response did not contain a SecretString payload"
        )

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
            failures[secret_id] = _classify_aws_error(entry.get("ErrorCode"))

    for secret_id in requested_ids:
        if secret_id not in values and secret_id not in failures:
            failures[secret_id] = SecretRequestError(
                "AWS batch response omitted a requested secret"
            )

    return values, failures


def _classify_aws_error(
    error: ClientError | BotoCoreError | str | None,
) -> SecretRetrievalError:
    """Map a boto error or AWS error code to a public retrieval exception."""
    if isinstance(error, ClientError):
        error = error.response.get("Error", {}).get("Code")
    elif isinstance(
        error,
        (
            ConfigNotFound,
            CredentialRetrievalError,
            InvalidConfigError,
            NoCredentialsError,
            NoRegionError,
            PartialCredentialsError,
            ProfileNotFound,
        ),
    ):
        return AWSConfigurationError(
            "AWS Secrets Manager client configuration is invalid"
        )
    if error == "ResourceNotFoundException":
        return SecretNotFoundError("AWS secret was not found")
    if error in {
        "AccessDenied",
        "AccessDeniedException",
        "AuthorizationError",
        "UnauthorizedOperation",
        "UnrecognizedClientException",
    }:
        return SecretPermissionError("AWS Secrets Manager denied access to the secret")
    return SecretRequestError("AWS Secrets Manager request could not be completed")


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
    message = "Secret retrieval attempt" if outcome == "attempt" else "Secret retrieval result"
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
    logger.error("Secret retrieval failed", extra=extra)
