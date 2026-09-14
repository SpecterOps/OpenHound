import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any, Protocol, cast

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    NoRegionError,
)

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 20
_PROVIDER = "aws.secretsmanager"


class GetSecretValueClient(Protocol):
    def get_secret_value(self, *, SecretId: str) -> Mapping[str, Any]: ...


class BatchGetSecretValueClient(Protocol):
    def batch_get_secret_value(
        self, *, SecretIdList: list[str]
    ) -> Mapping[str, Any]: ...


SecretsManagerClient = GetSecretValueClient | BatchGetSecretValueClient


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


class InvalidSecretValueError(SecretRetrievalError):
    """The retrieved secret uses an unsupported or invalid format."""


SecretValue = str | Mapping[str, Any]


class AWSSecretsManager:
    def __init__(self, client: SecretsManagerClient | None = None) -> None:
        if client is not None and not (
            callable(getattr(client, "get_secret_value", None))
            or callable(getattr(client, "batch_get_secret_value", None))
        ):
            raise AWSConfigurationError(
                "AWS Secrets Manager client does not support secret retrieval"
            )

        self._client = client

    def get_secret(self, secret_id: str) -> SecretValue:
        """Retrieve one text or JSON-object secret."""
        try:
            client = self._get_client()
            value = self._retrieve_secret(
                cast(GetSecretValueClient, client), secret_id
            )
        except SecretRetrievalError:
            _log_result("get_secret_value", "failure")
            raise
        _log_result("get_secret_value", "success")
        return value

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
            _log_result("batch_get_secret_value", "failure", len(requested_ids))
            raise SecretBatchError(failures) from None
        try:
            if not callable(getattr(client, "batch_get_secret_value", None)):
                values = self._get_secrets_individually(
                    cast(GetSecretValueClient, client), requested_ids
                )
            else:
                values = self._get_secrets_in_batches(client, requested_ids)
        except SecretBatchError:
            _log_result("batch_get_secret_value", "failure", len(requested_ids))
            raise
        _log_result("batch_get_secret_value", "success", len(requested_ids))
        return values

    def _get_secrets_in_batches(
        self, client: SecretsManagerClient, secret_ids: list[str]
    ) -> dict[str, SecretValue]:
        values: dict[str, SecretValue] = {}
        for chunk_index, chunk in enumerate(_chunks(secret_ids, MAX_BATCH_SIZE)):
            try:
                response = cast(
                    BatchGetSecretValueClient, client
                ).batch_get_secret_value(SecretIdList=chunk)
            except (ClientError, BotoCoreError) as error:
                if isinstance(error, ClientError) and _is_batch_fallback_error(error):
                    values.update(
                        self._get_secrets_individually(
                            cast(GetSecretValueClient, client),
                            secret_ids[chunk_index * MAX_BATCH_SIZE :],
                        )
                    )
                    break
                classified = _classify_sdk_error(error)
                raise SecretBatchError(
                    {secret_id: classified for secret_id in chunk}
                ) from None

            failures = _batch_response_failures(response, chunk)
            chunk_values, value_failures = _parse_batch_values(response, chunk)
            for secret_id, value_error in value_failures.items():
                failures.setdefault(secret_id, value_error)
            if failures:
                raise SecretBatchError(failures) from None
            values.update({secret_id: chunk_values[secret_id] for secret_id in chunk})

        return values

    def _get_client(self) -> SecretsManagerClient:
        if self._client is not None:
            return self._client
        try:
            return cast(SecretsManagerClient, boto3.client("secretsmanager"))
        except BotoCoreError as error:
            raise _classify_boto_core_error(error) from None

    def _retrieve_secret(
        self, client: GetSecretValueClient, secret_id: str
    ) -> SecretValue:
        try:
            if not callable(getattr(client, "get_secret_value", None)):
                raise AWSConfigurationError(
                    "AWS Secrets Manager client does not support secret retrieval"
                )
            response = client.get_secret_value(SecretId=secret_id)
            value = _parse_secret_value(response)
        except (ClientError, BotoCoreError) as error:
            raise _classify_sdk_error(error) from None
        return value

    def _get_secrets_individually(
        self, client: GetSecretValueClient, secret_ids: list[str]
    ) -> dict[str, SecretValue]:
        values: dict[str, SecretValue] = {}
        failures: dict[str, SecretRetrievalError] = {}
        for secret_id in secret_ids:
            try:
                values[secret_id] = self._retrieve_secret(client, secret_id)
            except SecretRetrievalError as error:
                failures[secret_id] = error
        if failures:
            raise SecretBatchError(failures) from None
        return values


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _parse_secret_value(response: Mapping[str, Any]) -> SecretValue:
    if not isinstance(response, Mapping):
        raise InvalidSecretValueError("AWS secret response has an invalid format")
    if response.get("SecretBinary") is not None:
        raise InvalidSecretValueError(
            "SecretBinary payloads are not supported; use SecretString"
        )

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise InvalidSecretValueError(
            "AWS secret response did not contain a valid SecretString payload"
        )

    candidate = secret_string.strip()
    if candidate.startswith(("{", "[")):
        try:
            parsed = json.loads(secret_string)
        except json.JSONDecodeError:
            raise InvalidSecretValueError(
                "AWS secret contains malformed JSON"
            ) from None
        if not isinstance(parsed, dict):
            raise InvalidSecretValueError(
                "Only JSON-object secret payloads are supported"
            )
        return parsed

    return secret_string


def _parse_batch_values(
    response: Mapping[str, Any],
    requested_ids: list[str],
) -> tuple[dict[str, SecretValue], dict[str, SecretRetrievalError]]:
    if not isinstance(response, Mapping):
        error = InvalidSecretValueError("AWS batch response has an invalid format")
        return {}, {secret_id: error for secret_id in requested_ids}

    entries = response.get("SecretValues", [])
    if not isinstance(entries, list):
        response_error = InvalidSecretValueError(
            "AWS batch response has an invalid format"
        )
        return {}, {secret_id: response_error for secret_id in requested_ids}

    values: dict[str, SecretValue] = {}
    failures: dict[str, SecretRetrievalError] = {}
    requested_set = set(requested_ids)
    for entry in entries:
        if not isinstance(entry, Mapping):
            response_error = InvalidSecretValueError(
                "AWS batch response has an invalid format"
            )
            for secret_id in requested_ids:
                failures.setdefault(secret_id, response_error)
            break

        matched_secret_id = _matching_requested_id(entry, requested_set)
        if matched_secret_id is None:
            continue
        try:
            values[matched_secret_id] = _parse_secret_value(entry)
        except SecretRetrievalError as value_error:
            failures[matched_secret_id] = value_error

    for secret_id in requested_ids:
        if secret_id not in values and secret_id not in failures:
            failures[secret_id] = SecretRequestError(
                "AWS batch response omitted a requested secret"
            )

    return values, failures


def _batch_response_failures(
    response: Mapping[str, Any],
    requested_ids: list[str],
) -> dict[str, SecretRetrievalError]:
    if not isinstance(response, Mapping):
        return {}
    entries = response.get("Errors", [])
    if not isinstance(entries, list):
        return {
            secret_id: InvalidSecretValueError(
                "AWS batch response has an invalid format"
            )
            for secret_id in requested_ids
        }

    requested_set = set(requested_ids)
    failures: dict[str, SecretRetrievalError] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            failures[f"<unmapped-{index}>"] = InvalidSecretValueError(
                "AWS batch response has an invalid format"
            )
            continue
        secret_id = entry.get("SecretId")
        if not isinstance(secret_id, str) or secret_id not in requested_set:
            secret_id = f"<unmapped-{index}>"
        failures[secret_id] = _classify_error_code(entry.get("ErrorCode"))
    return failures


def _matching_requested_id(
    entry: Mapping[str, Any], requested_ids: set[str]
) -> str | None:
    for field in ("Name", "ARN"):
        value = entry.get(field)
        if isinstance(value, str) and value in requested_ids:
            return value
    return None


def _classify_client_error(error: ClientError) -> SecretRetrievalError:
    return _classify_error_code(_client_error_code(error))


def _classify_boto_core_error(error: BotoCoreError) -> SecretRetrievalError:
    if isinstance(error, (NoCredentialsError, NoRegionError)):
        return AWSConfigurationError(
            "AWS Secrets Manager client configuration is invalid"
        )
    return SecretRequestError("AWS Secrets Manager request could not be completed")


def _classify_sdk_error(
    error: ClientError | BotoCoreError,
) -> SecretRetrievalError:
    if isinstance(error, ClientError):
        return _classify_client_error(error)
    return _classify_boto_core_error(error)


def _is_batch_fallback_error(error: ClientError) -> bool:
    return _client_error_code(error) in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
        "UnknownOperationException",
        "InvalidAction",
    }


def _classify_error_code(error_code: Any) -> SecretRetrievalError:
    if error_code == "ResourceNotFoundException":
        return SecretNotFoundError("AWS secret was not found")
    if error_code in {
        "AccessDenied",
        "AccessDeniedException",
        "AuthorizationError",
        "UnauthorizedOperation",
        "UnrecognizedClientException",
    }:
        return SecretPermissionError("AWS Secrets Manager denied access to the secret")
    return SecretRequestError("AWS Secrets Manager request could not be completed")


def _client_error_code(error: ClientError) -> Any:
    return error.response.get("Error", {}).get("Code")


def _log_result(operation: str, outcome: str, secret_count: int | None = None) -> None:
    extra: dict[str, str | int] = {
        "provider": _PROVIDER,
        "operation": operation,
        "outcome": outcome,
    }
    if secret_count is not None:
        extra["secret_count"] = secret_count
    if outcome == "failure":
        logger.error("Secret retrieval failed", extra=extra)
    else:
        logger.debug("Secret retrieval completed", extra=extra)
