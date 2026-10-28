"""Adapt BHE's IAM source keys to a direct-key or assume-role AWS profile.

Only the worker installs AWS/DLT configuration or writes credential material.
The scheduler owns the workspace and its lifetime, including killed workers.
"""

import logging
import os
import shutil
import stat
import tempfile
import traceback
from collections.abc import Callable, Iterator
from configparser import ConfigParser
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.provider import ConfigProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from filelock import FileLock, Timeout

from openhound.core.clients.aws_secrets_manager import (
    SecretRetrievalError,
    SecretValue,
    _classify_aws_error,
    _configure_safe_sdk_logging,
)
from openhound.core.clients.models.jobs import CollectorJob
from openhound.core.logging import redact_log_values
from openhound.scheduler.configuration import (
    ManagedConfigurationError,
    managed_job_configuration,
)

logger = logging.getLogger(__name__)

_ROLE_PROFILE_SETTINGS = {"role_arn", "role_session_name", "external_id"}
_SOURCE_ROLE_PROFILE_SETTINGS = {
    "source_role_arn": "role_arn",
    "source_role_session_name": "role_session_name",
    "source_role_external_id": "external_id",
}


class AWSAccessKeyError(ManagedConfigurationError):
    """An AWS preparation error containing no supplied credential values."""

    def __init__(
        self,
        message: str,
        summary: str = "Managed AWS credentials could not be prepared",
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.summary = summary
        self.context = context


def validate_secret_reference(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AWSAccessKeyError(
            "Managed AWS job requires a valid secret_key_id reference"
        )
    return value


@dataclass(frozen=True)
class AWSAccessKeyCredentials:
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)

    def __post_init__(self) -> None:
        # BHE's raw-key handoff needs strings for the INI writer, not AWS syntax.
        if not isinstance(self.access_key_id, str) or not isinstance(
            self.secret_access_key, str
        ):
            raise AWSAccessKeyError(
                "Managed AWS raw credentials require a key identifier and text secret payload"
            )


def prepare_aws_credentials(
    job: CollectorJob, load_secret: Callable[[str], SecretValue]
) -> dict[str, Any] | AWSAccessKeyCredentials:
    """Prepare authoritative claimed inputs without installing worker state.

    An explicit key ID selects the beta raw-key contract. Without it, retain
    compatibility with nested credential documents; a raw payload still needs
    a key ID and must never fall back to the scheduler's AWS identity.
    """
    reference = validate_secret_reference(job.secret_key_id)
    raw_key = job.external_secret_key_id is not None
    if raw_key:
        aws_profile_params(job.params)
    try:
        credentials = load_secret(reference)
    except SecretRetrievalError as error:
        # A retrieval exception's type alone does not make its text safe to log.
        message, context = error.safe_diagnostics or (
            f"Managed AWS credential retrieval failed ({type(error).__name__})",
            {},
        )
        raise AWSAccessKeyError(
            f"OpenHound runtime secret retrieval failed: {message}",
            error.failure_summary,
            **context,
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
    """Copy the DLT document and ensure the runtime injection path is an object."""
    if collector_name != "aws":
        raise AWSAccessKeyError("Managed AWS credentials require the AWS collector")
    if not isinstance(params, dict) or any(
        not isinstance(v, dict) for v in params.values()
    ):
        raise AWSAccessKeyError(
            "Managed AWS params must use nested configuration sections"
        )
    document = deepcopy(params)
    node = document
    for section in ("sources", "source", "aws"):
        node = node.setdefault(section, {})
        if not isinstance(node, dict):
            raise AWSAccessKeyError(
                "Managed AWS source configuration must be a nested object"
            )
    credentials = node.setdefault("credentials", {})
    if not isinstance(credentials, dict):
        raise AWSAccessKeyError(
            "Managed AWS credentials configuration must be a nested object"
        )
    # A partial chain must not silently collect as the intermediate role or
    # the raw-key identity. Leave AWS syntax and permissions to boto3/AWS.
    if _SOURCE_ROLE_PROFILE_SETTINGS.keys() & credentials.keys() and any(
        not isinstance(credentials.get(key), str) or not credentials[key].strip()
        for key in ("source_role_arn", "role_arn")
    ):
        raise AWSAccessKeyError(
            "Managed AWS source-role chaining requires non-empty "
            "source_role_arn and role_arn settings"
        )
    return document


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
        # Unsectioned providers cannot distinguish source credentials from other
        # defaults. Exclude them entirely rather than enumerating AWS fields.
        # The job provider itself is never wrapped.
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


def _write_ini(path: Path, sections: dict[str, dict[str, Any]]) -> None:
    config = ConfigParser(interpolation=None)
    try:
        # Only the authentication bridge calls this writer. Arbitrary extension
        # JSON remains in DLT, never in AWS INI files.
        if any(
            not isinstance(value, (str, int, float, bool))
            for settings in sections.values()
            for value in settings.values()
        ):
            raise TypeError
        config.read_dict(sections)
        stream = StringIO()
        config.write(stream)
        serialized = stream.getvalue()
        serialized.encode("utf-8")
        # Require a lossless INI round trip (e.g. multiline section injection is
        # not representable). This is serialization, not AWS field validation.
        restored = ConfigParser(interpolation=None)
        restored.read_string(serialized)
        if {s: dict(restored[s]) for s in restored.sections()} != {
            s: dict(config[s]) for s in config.sections()
        }:
            raise ValueError
    except Exception:  # noqa: BLE001 - serialization failures must omit supplied values
        raise AWSAccessKeyError("Managed AWS profile could not be serialized") from None
    # O_EXCL prevents following/reusing an existing file; permissions apply from
    # creation, independent of the process umask.
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as stream:
        stream.write(serialized)


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


def _log_collection_failure(error: Exception) -> None:
    # Collector/DLT wrappers can copy SDK messages into their own text. If an
    # SDK cause exists, report only classified diagnostics, never either message:
    # they can include temporary STS credentials not covered by pair redaction.
    cause: BaseException | None = error
    seen: set[int] = set()
    while isinstance(cause, BaseException) and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, (BotoCoreError, ClientError)):
            diagnostics = _classify_aws_error(cause).safe_diagnostics
            assert diagnostics is not None  # Always supplied by the SDK classifier.
            message, context = diagnostics
            logger.error(
                "Managed AWS collection failed (%s): %s\n"
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
    logger.exception("Managed AWS collection failed")


@contextmanager
def managed_aws_profile(
    params: dict[str, Any], credentials: AWSAccessKeyCredentials, directory: Path
) -> Iterator[str]:
    """Install direct-key, single-role, or chained-role profiles for one job."""
    document = aws_profile_params(params)
    profile = f"openhound-job-{uuid4().hex}"
    settings = document["sources"]["source"]["aws"]["credentials"]
    # These fields belong to boto3's file provider, not the extension's DLT model.
    role_settings = {
        key: settings[key] for key in _ROLE_PROFILE_SETTINGS if key in settings
    }
    source_role_settings = {
        ini_key: settings[key]
        for key, ini_key in _SOURCE_ROLE_PROFILE_SETTINGS.items()
        if key in settings
    }
    settings["profile_name"] = profile
    source_profile = f"{profile}-source" if role_settings else profile
    role_source_profile = source_profile
    config_sections = {}
    if source_role_settings:
        role_source_profile = f"{profile}-intermediate"
        config_sections[f"profile {role_source_profile}"] = {
            **source_role_settings,
            "source_profile": source_profile,
            "role_session_name": source_role_settings.get(
                "role_session_name", role_source_profile
            ),
        }
    profile_config = (
        {
            **role_settings,
            "source_profile": role_source_profile,
            "role_session_name": role_settings.get("role_session_name", profile),
        }
        if role_settings
        else {}
    )
    config_sections[f"profile {profile}"] = profile_config
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
            try:
                _write_ini(
                    files["AWS_SHARED_CREDENTIALS_FILE"],
                    {
                        source_profile: {
                            "aws_access_key_id": credentials.access_key_id,
                            "aws_secret_access_key": credentials.secret_access_key,
                        }
                    },
                )
                _write_ini(files["AWS_CONFIG_FILE"], config_sections)
                os.environ.update({key: str(path) for key, path in files.items()})
                providers.providers = [
                    _SourceCredentialFallback(p) for p in previous_providers
                ]
                with managed_job_configuration(document, {}):
                    yield profile
            except ManagedConfigurationError:
                raise
            except Exception as error:  # noqa: BLE001 - collector/SDK failures
                _log_collection_failure(error)
                # Keep remote tracebacks payload-free after logging locally
                # while the worker's redaction and SDK controls are active.
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
