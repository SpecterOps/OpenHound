"""OpenHound managed-mode configuration."""

from dataclasses import dataclass

import dlt


@dataclass(frozen=True)
class ModeDefaults:
    """Configuration defaults that differ between OpenHound modes."""

    log_format: str


_UNMANAGED_DEFAULTS = ModeDefaults(log_format="text")
_MANAGED_DEFAULTS = ModeDefaults(log_format="json")


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
