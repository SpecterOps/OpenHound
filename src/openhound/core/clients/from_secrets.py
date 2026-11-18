"""Build a BloodHound API client from the settings stored in OpenHound's configuration.

OpenHound keeps settings in dlt's two stores. Plain settings such as the BloodHound address
live in ``.dlt/config.toml``; credentials live in ``.dlt/secrets.toml``. Both can also be
given as environment variables. This module looks in two sections, in this order, and uses
the first one that holds credentials:

``[destination.bloodhound]``
    The section that OpenHound's saved-search and privilege-zone uploads already use. It is
    the home for BloodHound Community Edition settings. Holds ``url`` plus either an API token
    pair (``token_id`` and ``token_key``, created under "My Profile > API Key Management") or
    a bearer ``token`` from a signed-in session.

``[destination.bloodhoundenterprise]``
    The section that OpenHound's Enterprise ingest and scheduler already use. Holds ``url``
    plus ``token_id`` and ``token_key``. Nothing about it changes; it is read here so that an
    Enterprise user needs no extra configuration.

Example, with the address in ``config.toml``::

    [destination.bloodhound]
    url = "http://localhost:8080"

and the credentials in ``secrets.toml``::

    [destination.bloodhound]
    token_id = "..."
    token_key = "..."

As environment variables the same settings are ``DESTINATION__BLOODHOUND__URL``,
``DESTINATION__BLOODHOUND__TOKEN_ID``, and so on.
"""

from dataclasses import dataclass

import dlt

from openhound.core.clients.bloodhound import (
    BloodHound,
    BloodHoundClient,
    BloodHoundJWT,
)

# The configuration sections that may hold BloodHound settings, in the order they are tried.
SECTION_BLOODHOUND = "destination.bloodhound"
SECTION_ENTERPRISE = "destination.bloodhoundenterprise"
SECTIONS_IN_ORDER = (SECTION_BLOODHOUND, SECTION_ENTERPRISE)

# The setting names inside each section.
SETTING_URL = "url"
SETTING_TOKEN_ID = "token_id"
SETTING_TOKEN_KEY = "token_key"
SETTING_BEARER_TOKEN = "token"


class MissingBloodHoundCredentials(Exception):
    """Raised when the configuration does not contain enough information to reach BloodHound."""


@dataclass
class BloodHoundSettings:
    """The settings found in one configuration section.

    Attributes:
        section: The section they came from, for example ``destination.bloodhound``.
        url: The BloodHound address, or None when absent.
        token_id: First half of an API token pair, or None.
        token_key: Second half of an API token pair, or None.
        bearer_token: A session token, or None.
    """

    section: str
    url: str | None
    token_id: str | None
    token_key: str | None
    bearer_token: str | None

    @property
    def has_token_pair(self) -> bool:
        return bool(self.token_id and self.token_key)

    @property
    def has_credentials(self) -> bool:
        return self.has_token_pair or bool(self.bearer_token)


def read_settings(section: str) -> BloodHoundSettings:
    """Read one section's settings from the two stores.

    The address is read through the configuration store because that store sees
    ``config.toml``, ``secrets.toml``, and environment variables. Credentials are read through
    the secrets store, which sees ``secrets.toml`` and environment variables only, so a token
    accidentally placed in ``config.toml`` is not picked up.
    """
    url: str | None = dlt.config.get(f"{section}.{SETTING_URL}")
    token_id: str | None = dlt.secrets.get(f"{section}.{SETTING_TOKEN_ID}")
    token_key: str | None = dlt.secrets.get(f"{section}.{SETTING_TOKEN_KEY}")
    bearer_token: str | None = dlt.secrets.get(f"{section}.{SETTING_BEARER_TOKEN}")
    return BloodHoundSettings(
        section=section,
        url=url,
        token_id=token_id,
        token_key=token_key,
        bearer_token=bearer_token,
    )


def bloodhound_client_from_secrets() -> BloodHoundClient:
    """Create the right kind of BloodHound client from whichever section holds credentials.

    The sections are tried in the order of ``SECTIONS_IN_ORDER``. Within a section the API
    token pair is preferred, because API tokens do not expire the way session tokens do. When
    no section holds credentials, or the chosen section has no address, a plain-English error
    names exactly which settings are needed.

    Returns:
        BloodHoundClient: A client that signs every request with the configured credentials.
    """
    for section in SECTIONS_IN_ORDER:
        settings = read_settings(section)
        if not settings.has_credentials:
            continue

        if not settings.url:
            raise MissingBloodHoundCredentials(
                f"Credentials were found in [{section}] but it has no 'url'. Add "
                f"'{section}.url' to .dlt/config.toml, or set the "
                f"{environment_variable_name(section, SETTING_URL)} environment variable."
            )

        if settings.has_token_pair:
            # The checks above guarantee both halves are present; the assertions tell the
            # type checker the same thing.
            assert settings.token_id is not None and settings.token_key is not None
            return BloodHound(
                token_key=settings.token_key,
                token_id=settings.token_id,
                bhe_uri=settings.url,
            )

        assert settings.bearer_token is not None
        return BloodHoundJWT(token=settings.bearer_token, base_uri=settings.url)

    raise MissingBloodHoundCredentials(
        "No BloodHound credentials are configured. Add a "
        f"[{SECTION_BLOODHOUND}] section with '{SETTING_URL}' in .dlt/config.toml and either "
        f"'{SETTING_TOKEN_ID}' and '{SETTING_TOKEN_KEY}' or '{SETTING_BEARER_TOKEN}' in "
        f".dlt/secrets.toml. An existing [{SECTION_ENTERPRISE}] section is also accepted. "
        "The same settings can be environment variables such as "
        f"{environment_variable_name(SECTION_BLOODHOUND, SETTING_URL)}."
    )


def environment_variable_name(section: str, setting: str) -> str:
    """Turn ``destination.bloodhound`` and ``url`` into ``DESTINATION__BLOODHOUND__URL``.

    This is dlt's naming rule: dots become double underscores and everything is upper case.
    """
    return f"{section}.{setting}".replace(".", "__").upper()
