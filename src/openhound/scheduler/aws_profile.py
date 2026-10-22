"""Adapt BHE's raw IAM access-key pair to the AWS extension's named profile.

Only the worker installs AWS/DLT configuration or writes credential material.
The scheduler owns the workspace and its lifetime, including killed workers.
"""

import logging
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from configparser import ConfigParser
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.provider import ConfigProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from filelock import FileLock, Timeout

from openhound.core.clients.aws_secrets_manager import (
    SecretRetrievalError,
    SecretValue,
    _configure_safe_sdk_logging,
)
from openhound.core.clients.models.jobs import CollectorJob
from openhound.core.logging import redact_log_values
from openhound.scheduler.configuration import (
    ManagedConfigurationError,
    managed_job_configuration,
)

logger = logging.getLogger(__name__)

_CREDENTIAL_SETTINGS = {
    "region_name",
    "endpoint_url",
    "org_role_name",
    "org_role_session_name",
    "include_management_account",
    "source_account",
    "management_account_id",
    "management_role_name",
}


class AWSAccessKeyError(ManagedConfigurationError):
    """An AWS preparation error containing no supplied credential values."""

    def __init__(
        self,
        message: str,
        summary: str = "Managed AWS credentials or authentication settings are invalid",
    ) -> None:
        super().__init__(message)
        self.summary = summary


def validate_access_key_id(value: Any) -> None:
    # ASIA keys are temporary and require a session token, absent in BHE's beta
    # contract. Do not strip input: INI/SDK whitespace normalization would change it.
    if not isinstance(value, str) or re.fullmatch(r"AKIA[A-Z0-9]{16}", value) is None:
        raise AWSAccessKeyError(
            "Managed AWS job requires a long-lived IAM Access Key ID in external_secret_key_id"
        )


def validate_secret_reference(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9/_+=.@:-]{1,2048}", value) is None
    ):
        raise AWSAccessKeyError(
            "Managed AWS job requires a valid secret_key_id reference"
        )
    return value


@dataclass(frozen=True)
class AWSAccessKeyCredentials:
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_access_key_id(self.access_key_id)
        if (
            not isinstance(self.secret_access_key, str)
            or re.fullmatch(r"[A-Za-z0-9/+=]{40}", self.secret_access_key) is None
        ):
            raise AWSAccessKeyError(
                "Managed AWS credential secret must be the raw 40-character Secret Access Key"
            )


def prepare_aws_credentials(
    job: CollectorJob, load_secret: Callable[[str], SecretValue]
) -> dict[str, Any] | AWSAccessKeyCredentials:
    """Prepare authoritative claimed inputs without installing worker state.

    An explicit key ID selects the beta raw-key contract. Without it, retain
    compatibility with nested credential documents; a raw payload still needs
    a valid key ID and must never fall back to the scheduler's AWS identity.
    """
    reference = validate_secret_reference(job.secret_key_id)
    raw_key = job.external_secret_key_id is not None
    if raw_key:
        validate_access_key_id(job.external_secret_key_id)
        aws_profile_params(job.params)
    try:
        credentials = load_secret(reference)
    except SecretRetrievalError as error:
        # Reader diagnostics may include references or provider response bodies.
        # Keep the classified outcome, never the reader's supplied message.
        raise AWSAccessKeyError(
            f"Managed AWS credential retrieval failed ({type(error).__name__})",
            error.failure_summary,
        ) from None
    except Exception as error:  # noqa: BLE001 - arbitrary secret readers
        raise AWSAccessKeyError(
            f"Credential secret reader failed ({type(error).__name__})",
            "Credential secret retrieval failed",
        ) from None
    if not raw_key and isinstance(credentials, dict):
        return credentials  # Generic credential-document validation stays shared.
    return AWSAccessKeyCredentials(job.external_secret_key_id, credentials)  # type: ignore[arg-type]


def aws_profile_params(
    params: dict[str, Any], *, collector_name: str = "aws"
) -> dict[str, Any]:
    """Validate the raw-key mode and copy its full nested settings before /start."""
    if collector_name != "aws":
        raise AWSAccessKeyError("Managed AWS credentials require the AWS collector")
    if not isinstance(params, dict) or any(
        not isinstance(v, dict) for v in params.values()
    ):
        raise AWSAccessKeyError(
            "Managed AWS params must use nested configuration sections"
        )
    document = deepcopy(params)
    _validate_credential_sections(document)
    node = document
    for section in ("sources", "source", "aws"):
        node = node.setdefault(section, {})
        if not isinstance(node, dict):
            raise AWSAccessKeyError(
                "Managed AWS source configuration must be a nested object"
            )
    credentials = node.setdefault("credentials", {})
    if not isinstance(credentials, dict) or credentials.keys() - _CREDENTIAL_SETTINGS:
        raise AWSAccessKeyError(
            "Managed AWS access-key jobs cannot select another profile or authentication mode"
        )
    if any(
        key in node
        for key in ("profile_name", "profile_names", "auth_mode", "authentication_mode")
    ):
        raise AWSAccessKeyError(
            "Managed AWS access-key jobs contain conflicting authentication settings"
        )
    # An incomplete organization configuration must not fall through to the single
    # profile model and silently ignore account/role settings.
    organization = _CREDENTIAL_SETTINGS - {"region_name", "endpoint_url"}
    if credentials.keys() & organization and (
        not isinstance(credentials.get("org_role_name"), str)
        or not credentials["org_role_name"].strip()
    ):
        raise AWSAccessKeyError(
            "Managed AWS organization settings require org_role_name"
        )
    return document


def _validate_credential_sections(
    document: dict[str, Any], path: tuple[str, ...] = ()
) -> None:
    # DLT accepts compact, less-specific and pipeline-prefixed layouts too.
    # Keep every source-credential setting in the canonical raw-key section so
    # another union alternative cannot win during pipeline-scoped resolution.
    for key, value in document.items():
        if (
            key == "credentials"
            and path != ("sources", "source", "aws")
            and "destination" not in path
        ):
            raise AWSAccessKeyError(
                "Managed AWS credential settings must use sources.source.aws.credentials"
            )
        if isinstance(value, dict):
            _validate_credential_sections(value, (*path, key))


def _workspace_root() -> Path:
    suffix = str(os.getuid()) if hasattr(os, "getuid") else "current-user"
    root = Path(tempfile.gettempdir()) / f"openhound-aws-profiles-{suffix}"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or (
        hasattr(os, "getuid")
        and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700)
    ):
        raise AWSAccessKeyError("Managed AWS credential workspace is not private")
    return root


def _lock(path: Path, timeout: float = 0) -> FileLock:
    return FileLock(path, mode=0o600, timeout=timeout, fallback_to_soft=False)


def _reap_orphans(root: Path) -> None:
    # Caller holds the root lock, also used for creation and worker acquisition.
    # OS-backed locks release on process death, unlike PID checks/age limits.
    for directory in root.glob("job-*"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            with _lock(directory / "parent.lock"), _lock(directory / "worker.lock"):
                pass
            shutil.rmtree(directory)
        except Timeout:
            continue  # A scheduler or an orphaned-but-still-running worker owns it.


def cleanup_orphaned_aws_profiles() -> None:
    root = _workspace_root()
    with _lock(root / "registry.lock", timeout=-1):
        _reap_orphans(root)


class AWSProfileWorkspace:
    """Parent-owned workspace. No credential values or files exist at creation."""

    def __init__(self) -> None:
        root = _workspace_root()
        with _lock(root / "registry.lock", timeout=-1):
            _reap_orphans(root)
            self.directory = Path(tempfile.mkdtemp(prefix="job-", dir=root))
            self._owner = _lock(self.directory / "parent.lock")
            self._owner.acquire()

    def cleanup(self) -> None:
        with _lock(self.directory.parent / "registry.lock", timeout=-1):
            self._owner.release()
            if self.directory.exists():
                shutil.rmtree(self.directory)


class _SourceCredentialFallback(ConfigProvider):
    """Keep deployment defaults except source credentials/authentication mode.

    DLT resolves union alternatives field by field. A local profile_names or
    org_role_name could otherwise select a different credential model, even with
    this job's explicit profile_name. Destination/bootstrap defaults still work.
    """

    def __init__(self, provider: ConfigProvider) -> None:
        self.provider = provider

    def get_value(
        self, key: str, hint: type[Any], pipeline_name: str, *sections: str
    ) -> tuple[Any, str]:
        if not self.provider.supports_sections and key in _CREDENTIAL_SETTINGS | {
            "profile_name",
            "profile_names",
            "credentials",
        }:
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


def _write_ini(path: Path, sections: dict[str, dict[str, str]]) -> None:
    config = ConfigParser(interpolation=None)
    config.read_dict(sections)
    # O_EXCL prevents following/reusing an existing file; permissions apply from
    # creation, independent of the process umask.
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as stream:
        config.write(stream)


@contextmanager
def _safe_sdk_logging() -> Iterator[None]:
    # Spawned workers do not inherit the scheduler's Secrets Manager log sinks.
    # SDK DEBUG responses can include the extension's temporary STS credentials.
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


@contextmanager
def managed_aws_profile(
    params: dict[str, Any], credentials: AWSAccessKeyCredentials, directory: Path
) -> Iterator[str]:
    """Install one profile before loading collectors; restore all worker state."""
    document = aws_profile_params(params)
    profile = f"openhound-job-{uuid4().hex}"
    document["sources"]["source"]["aws"]["credentials"]["profile_name"] = profile
    files = {
        "AWS_SHARED_CREDENTIALS_FILE": directory / "credentials",
        "AWS_CONFIG_FILE": directory / "config",
    }
    previous_environment = {key: os.environ.get(key) for key in files}
    providers = Container()[PluggableRunContext].providers
    previous_providers = providers.providers
    worker_lock = _lock(directory / "worker.lock")
    with _lock(directory.parent / "registry.lock", timeout=-1):
        if not directory.is_dir() or directory.is_symlink():
            raise AWSAccessKeyError("Managed AWS credential workspace is unavailable")
        worker_lock.acquire()
    try:
        with (
            redact_log_values(
                (credentials.access_key_id, credentials.secret_access_key)
            ),
            _safe_sdk_logging(),
        ):
            _write_ini(
                files["AWS_SHARED_CREDENTIALS_FILE"],
                {
                    profile: {
                        "aws_access_key_id": credentials.access_key_id,
                        "aws_secret_access_key": credentials.secret_access_key,
                    }
                },
            )
            _write_ini(files["AWS_CONFIG_FILE"], {f"profile {profile}": {}})
            os.environ.update({key: str(path) for key, path in files.items()})
            providers.providers = [
                _SourceCredentialFallback(p) for p in previous_providers
            ]
            with managed_job_configuration(document, {}):
                yield profile
    except ManagedConfigurationError:
        raise
    except Exception:  # noqa: BLE001 - sanitize arbitrary collector/SDK failures
        # Worker exception messages and remote tracebacks may contain SDK values.
        raise ManagedConfigurationError(
            "Managed AWS collection failed; check worker logs"
        ) from None
    finally:
        providers.providers = previous_providers
        for key, previous in previous_environment.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        try:
            for path in files.values():
                path.unlink(missing_ok=True)
        finally:
            worker_lock.release()
