"""Resolve managed job credentials for worker configuration and environment."""

# TODO: Separate credential delivery from DLT isolation and collection logging
# when refactoring the managed worker setup contract.

import logging
import os
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.provider import ConfigProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext

from openhound.core.clients.aws_secrets_manager import (
    SecretRetrievalError,
    SecretValue,
    _classify_aws_error,
    _configure_safe_sdk_logging,
)
from openhound.core.clients.models.jobs import CollectorJob
from openhound.scheduler.configuration import (
    JobEnvironment,
    ManagedConfigurationError,
    _validate_document,
    managed_job_configuration,
)

logger = logging.getLogger(__name__)

# Select temporary bindings by the extension's job_key, not BHE's numeric job_type_id.
# TODO: Replace the AWS binding with the generic credential handoff once the AWS
# extension accepts those inputs and owns SDK environment mapping and isolation.
# Keep shared secret retrieval, worker-scoped delivery and redaction in OpenHound.
_TEMPORARY_ENVIRONMENT_BINDINGS: dict[str, tuple[str, str, JobEnvironment]] = {
    "aws": (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        {
            "AWS_SESSION_TOKEN": None,
            "AWS_SECURITY_TOKEN": None,
            "AWS_CREDENTIAL_EXPIRATION": None,
            "AWS_PROFILE": None,
            "AWS_DEFAULT_PROFILE": None,
            "AWS_ROLE_ARN": None,
            "AWS_WEB_IDENTITY_TOKEN_FILE": None,
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        },
    )
}


class CredentialPreparationError(ManagedConfigurationError):
    """A managed credential preparation failure with safe diagnostics."""

    def __init__(
        self,
        message: str,
        summary: str = "Credential document is invalid",
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.summary = summary
        self.context = context


def prepare_job_credentials(
    job: CollectorJob, load_secret: Callable[[str], SecretValue]
) -> tuple[dict[str, Any], JobEnvironment]:
    """Retrieve and validate configuration secrets or bound credential inputs."""
    reference = job.secret_key_id
    if not isinstance(reference, str) or not reference.strip():
        raise CredentialPreparationError(
            "Managed job requires a valid secret_key_id reference"
        )
    binding = _TEMPORARY_ENVIRONMENT_BINDINGS.get(job.job_key)
    identified_input = binding is not None and job.external_secret_key_id is not None
    if identified_input:
        _validate_document(job.params)
    try:
        credentials = load_secret(reference)
    except SecretRetrievalError as error:
        message, context = error.safe_diagnostics or (
            f"Managed credential retrieval failed ({type(error).__name__})",
            {},
        )
        raise CredentialPreparationError(
            f"OpenHound runtime secret retrieval failed: {message}",
            error.failure_summary,
            **context,
        ) from None
    except Exception as error:  # noqa: BLE001 - arbitrary secret readers
        raise CredentialPreparationError(
            f"Credential secret reader failed ({type(error).__name__})",
            "Credential secret retrieval failed",
        ) from None
    if not identified_input and isinstance(credentials, dict):
        _validate_document(credentials, allow_empty=True)
        return credentials, {}
    if binding is None:
        raise CredentialPreparationError("Credential document must be a JSON object")
    credential_id = job.external_secret_key_id
    if (
        not isinstance(credential_id, str)
        or not credential_id.strip()
        or not isinstance(credentials, str)
        or not credentials.strip()
    ):
        raise CredentialPreparationError(
            "Managed credentials require a non-empty identifier and text secret payload"
        )
    identifier_name, secret_name, defaults = binding
    return {}, {
        **defaults,
        identifier_name: credential_id,
        secret_name: credentials,
    }


class _SourceCredentialFallback(ConfigProvider):
    """Keep deployment defaults without blending source authentication settings."""

    def __init__(self, provider: ConfigProvider) -> None:
        self.provider = provider

    def get_value(
        self, key: str, hint: type[Any], pipeline_name: str, *sections: str
    ) -> tuple[Any, str]:
        if not self.provider.supports_sections:
            return None, key
        if (not sections or sections[0] in ("sources", "credentials")) and (
            key == "credentials" or "credentials" in sections
        ):
            return None, key
        return self.provider.get_value(key, hint, pipeline_name, *sections)

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def supports_secrets(self) -> bool:
        return self.provider.supports_secrets

    @property
    def supports_sections(self) -> bool:
        return self.provider.supports_sections


@contextmanager
def _safe_sdk_logging() -> Iterator[None]:
    # Spawned workers do not inherit the scheduler's Secrets Manager log sinks.
    # SDK DEBUG responses can contain temporary STS credentials.
    loggers = [logging.getLogger("boto3"), logging.getLogger("botocore")]
    loggers.extend(
        value
        for name, value in tuple(logging.Logger.manager.loggerDict.items())
        if name.startswith(("boto3.", "botocore."))
        and isinstance(value, logging.Logger)
    )
    previous = {
        value: (value.level, value.handlers[:], value.propagate, value.disabled)
        for value in loggers
    }
    _configure_safe_sdk_logging()
    try:
        yield
    finally:
        for sdk_logger, (level, handlers, propagate, disabled) in previous.items():
            sdk_logger.setLevel(level)
            sdk_logger.handlers = handlers
            sdk_logger.propagate = propagate
            sdk_logger.disabled = disabled


def _log_collection_failure(error: Exception) -> None:
    # SDK exception text can contain temporary credentials that are not covered
    # by the supplied-key redactor. Keep classified diagnostics and stack frames.
    cause: BaseException | None = error
    seen: set[int] = set()
    while isinstance(cause, BaseException) and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, (BotoCoreError, ClientError)):
            diagnostics = _classify_aws_error(cause).safe_diagnostics
            assert diagnostics is not None
            message, context = diagnostics
            logger.error(
                "Managed collection failed (%s): %s\n"
                "Traceback (most recent call last):\n%s",
                type(error).__name__,
                message,
                "".join(traceback.format_tb(error.__traceback__)),
                extra=context,
            )
            return
        cause = (
            getattr(cause, "exception", None) or cause.__cause__ or cause.__context__
        )
    logger.exception("Managed collection failed")


@contextmanager
def managed_collection_configuration(
    params: dict[str, Any], secrets: dict[str, Any]
) -> Iterator[None]:
    """Provide job-scoped configuration with credential isolation and safe logging."""
    providers = Container()[PluggableRunContext].providers
    previous = providers.providers
    providers.providers = [_SourceCredentialFallback(p) for p in previous]
    try:
        with _safe_sdk_logging():
            try:
                with managed_job_configuration(params, secrets):
                    yield
            except ManagedConfigurationError:
                raise
            except Exception as error:  # noqa: BLE001 - sanitize collector/SDK failures
                _log_collection_failure(error)
                raise ManagedConfigurationError(
                    "Managed collection failed; check worker logs"
                ) from None
    finally:
        providers.providers = previous
