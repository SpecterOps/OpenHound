"""OpenHound managed-mode configuration."""

from dataclasses import dataclass

import dlt


@dataclass(frozen=True)
class ModeDefaults:
    """Configuration defaults that differ between OpenHound modes."""

    log_format: str


@dataclass(frozen=True)
class ManagedBHEConfig:
    """Managed-mode settings for BHE credentials and scheduler polling."""

    secret_name: str
    poll_interval: int


_UNMANAGED_DEFAULTS = ModeDefaults(log_format="text")
_MANAGED_DEFAULTS = ModeDefaults(log_format="json")
DEFAULT_MANAGED_BHE_POLL_INTERVAL = 30


def is_managed() -> bool:
    """Return whether OpenHound is configured to run in managed mode.

    The value is resolved on every call so all platform code observes the current
    DLT configuration. Missing configuration defaults to unmanaged mode. Invalid
    boolean values raise DLT's configuration coercion error.
    """

    configured = dlt.config.get("openhound.managed", bool)
    return configured if configured is not None else False


def get_mode_defaults() -> ModeDefaults:
    """Return the defaults profile for the configured mode."""

    return _MANAGED_DEFAULTS if is_managed() else _UNMANAGED_DEFAULTS


def get_managed_bhe_config() -> ManagedBHEConfig:
    """Return AWS secret and scheduler settings used by managed BHE clients.

    ``openhound.aws_secrets_manager.secret_name`` is deliberately required in
    managed mode: there is no safe default credential source.  The scheduler
    cadence defaults to 30 seconds, matching the historical fixed interval.
    """

    secret_name = dlt.config.get("openhound.aws_secrets_manager.secret_name", str)
    if not secret_name:
        raise ValueError(
            "Managed mode requires "
            "openhound.aws_secrets_manager.secret_name to be configured"
        )

    poll_interval = dlt.config.get(
        "openhound.aws_secrets_manager.poll_interval", int
    )
    if poll_interval is None:
        poll_interval = DEFAULT_MANAGED_BHE_POLL_INTERVAL
    if poll_interval <= 0:
        raise ValueError(
            "openhound.aws_secrets_manager.poll_interval must be greater than zero"
        )

    return ManagedBHEConfig(
        secret_name=secret_name,
        poll_interval=poll_interval,
    )
