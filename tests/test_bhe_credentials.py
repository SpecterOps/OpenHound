import pytest

from openhound.core.clients.bhe_credentials import (
    AWSBHECredentials,
    BHECredentials,
    InvalidBHECredentialsSecret,
)


class FakeSecretsManager:
    def __init__(self, secret):
        self.secret = secret
        self.requested = []

    def get_secret(self, secret_id):
        self.requested.append(secret_id)
        return self.secret


def test_aws_bhe_credentials_loads_token_pair_from_json_secret():
    manager = FakeSecretsManager({"token_id": "id", "token_key": "key"})

    result = AWSBHECredentials("production/bhe", manager).refresh()

    assert result == BHECredentials(token_id="id", token_key="key")
    assert manager.requested == ["production/bhe"]


@pytest.mark.parametrize(
    "secret",
    [
        "not-json",
        {},
        {"token_id": "id"},
        {"token_key": "key"},
        {"token_id": "", "token_key": "key"},
    ],
)
def test_aws_bhe_credentials_rejects_invalid_secret_values(secret):
    with pytest.raises(InvalidBHECredentialsSecret):
        AWSBHECredentials("production/bhe", FakeSecretsManager(secret)).refresh()
