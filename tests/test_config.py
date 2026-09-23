import dlt
import pytest
from dlt.common.configuration.exceptions import ConfigValueCannotBeCoercedException

from openhound.config import (
    DEFAULT_MANAGED_BHE_POLL_INTERVAL,
    ManagedBHEConfig,
    ModeDefaults,
    get_managed_bhe_config,
    get_mode_defaults,
    is_managed,
)


def test_is_managed_defaults_to_unmanaged(monkeypatch):
    monkeypatch.setattr(dlt.config, "get", lambda field, expected_type: None)

    assert is_managed() is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("false", False),
    ],
)
def test_is_managed_resolves_environment_configuration(monkeypatch, value, expected):
    monkeypatch.setenv("OPENHOUND__MANAGED", value)

    assert is_managed() is expected


def test_is_managed_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("OPENHOUND__MANAGED", "maybe")

    with pytest.raises(ConfigValueCannotBeCoercedException):
        is_managed()


def test_get_mode_defaults_selects_mode_specific_log_formats(monkeypatch):
    monkeypatch.setattr("openhound.config.is_managed", lambda: False)
    unmanaged = get_mode_defaults()

    monkeypatch.setattr("openhound.config.is_managed", lambda: True)
    managed = get_mode_defaults()

    assert isinstance(unmanaged, ModeDefaults)
    assert isinstance(managed, ModeDefaults)
    assert unmanaged is not managed
    assert unmanaged.log_format == "text"
    assert managed.log_format == "json"


def test_get_managed_bhe_config_uses_configured_secret_and_default_interval(
    monkeypatch,
):
    def get(field, expected_type):
        values = {
            "openhound.aws_secrets_manager.secret_name": "production/bhe",
            "openhound.aws_secrets_manager.poll_interval": None,
        }
        return values[field]

    monkeypatch.setattr(dlt.config, "get", get)

    assert get_managed_bhe_config() == ManagedBHEConfig(
        secret_name="production/bhe",
        poll_interval=DEFAULT_MANAGED_BHE_POLL_INTERVAL,
    )


def test_get_managed_bhe_config_rejects_missing_secret_name(monkeypatch):
    monkeypatch.setattr(dlt.config, "get", lambda field, expected_type: None)

    with pytest.raises(ValueError, match="secret_name"):
        get_managed_bhe_config()


def test_get_managed_bhe_config_rejects_non_positive_interval(monkeypatch):
    def get(field, expected_type):
        return "production/bhe" if field.endswith("secret_name") else 0

    monkeypatch.setattr(dlt.config, "get", get)

    with pytest.raises(ValueError, match="greater than zero"):
        get_managed_bhe_config()
