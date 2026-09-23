"""Managed BHE credential retrieval and validation."""

from dataclasses import dataclass
import logging
import time
from typing import Protocol

from .aws_secrets_manager import AWSSecretsManager, SecretValue

logger = logging.getLogger(__name__)


class SecretReader(Protocol):
    def get_secret(self, secret_id: str) -> SecretValue: ...


@dataclass(frozen=True)
class BHECredentials:
    """The HMAC credentials required by the BHE API client."""

    token_key: str
    token_id: str


class InvalidBHECredentialsSecret(ValueError):
    """The configured AWS secret cannot be used for BHE authentication."""


class AWSBHECredentials:
    """Synchronously load BHE credentials from one AWS Secrets Manager secret."""

    def __init__(
        self,
        secret_name: str,
        secrets_manager: SecretReader | None = None,
    ) -> None:
        self.secret_name = secret_name
        self._secrets_manager = secrets_manager or AWSSecretsManager()

    def refresh(self) -> BHECredentials:
        """Fetch and validate the current credentials.

        This intentionally does no background work.  A caller can refresh at
        startup or in response to a 401 and continue its original operation as
        soon as this method returns.
        """

        secret = self._secrets_manager.get_secret(self.secret_name)
        if not isinstance(secret, dict):
            raise InvalidBHECredentialsSecret(
                "BHE credentials secret must be a JSON object containing token_id and token_key"
            )

        token_id = secret.get("token_id")
        token_key = secret.get("token_key")
        if not isinstance(token_id, str) or not token_id:
            raise InvalidBHECredentialsSecret(
                "BHE credentials secret must contain a non-empty token_id"
            )
        if not isinstance(token_key, str) or not token_key:
            raise InvalidBHECredentialsSecret(
                "BHE credentials secret must contain a non-empty token_key"
            )
        return BHECredentials(token_id=token_id, token_key=token_key)

    def poll_until_available(self, interval: int) -> BHECredentials:
        """Keep checking the configured secret until it contains usable credentials."""

        while True:
            try:
                return self.refresh()
            except Exception:
                # AWS errors can be transient, and an absent or not-yet-populated
                # secret is expected while a managed deployment is provisioning.
                logger.warning(
                    "BHE credentials are unavailable; retrying in %s seconds.",
                    interval,
                )
                time.sleep(interval)
