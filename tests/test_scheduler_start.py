import scheduler
from openhound.config import ManagedBHEConfig
from openhound.core.clients.bhe_credentials import BHECredentials


def test_managed_scheduler_loads_aws_secret_before_starting_service(monkeypatch):
    constructed = []
    started = []

    class FakeCredentials:
        def __init__(self, secret_name):
            constructed.append(secret_name)

        def poll_until_available(self, interval):
            assert interval == 45
            return BHECredentials(token_id="aws-id", token_key="aws-key")

    class FakeService:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("started")

    monkeypatch.setattr(scheduler.config, "is_managed", lambda: True)
    monkeypatch.setattr(
        scheduler.config,
        "get_managed_bhe_config",
        lambda: ManagedBHEConfig(secret_name="production/bhe", poll_interval=45),
    )
    monkeypatch.setattr(
        scheduler.dlt,
        "config",
        {
            "destination.bloodhoundenterprise.url": "https://bhe.example",
            "destination.bloodhoundenterprise.collector_name": "collector",
        },
    )
    monkeypatch.setattr(scheduler, "AWSBHECredentials", FakeCredentials)
    monkeypatch.setattr(scheduler, "Service", FakeService)

    scheduler.start()

    assert constructed == ["production/bhe"]
    assert started[0]["bhe_uri"] == "https://bhe.example"
    assert started[0]["token_key"] == "aws-key"
    assert started[0]["token_id"] == "aws-id"
    assert started[0]["collector_name"] == "collector"
    assert started[0]["interval"] == 45
    assert started[0]["credential_refresh"]() == BHECredentials(
        token_id="aws-id", token_key="aws-key"
    )
    assert started[1] == "started"


def test_unmanaged_scheduler_uses_dlt_secrets_without_credential_refresh(monkeypatch):
    started = []

    class FakeService:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("started")

    def unexpected_aws_credentials(*args, **kwargs):
        raise AssertionError("unmanaged scheduler must not load AWS credentials")

    monkeypatch.setattr(scheduler.config, "is_managed", lambda: False)
    monkeypatch.setattr(
        scheduler.dlt,
        "config",
        {
            "destination.bloodhoundenterprise.url": "https://bhe.example",
            "destination.bloodhoundenterprise.collector_name": "collector",
        },
    )
    monkeypatch.setattr(
        scheduler.dlt,
        "secrets",
        {
            "destination.bloodhoundenterprise.token_key": "dlt-key",
            "destination.bloodhoundenterprise.token_id": "dlt-id",
        },
    )
    monkeypatch.setattr(scheduler, "AWSBHECredentials", unexpected_aws_credentials)
    monkeypatch.setattr(scheduler, "Service", FakeService)

    scheduler.start()

    assert started == [
        {
            "bhe_uri": "https://bhe.example",
            "token_key": "dlt-key",
            "token_id": "dlt-id",
            "collector_name": "collector",
            "interval": 30,
            "credential_refresh": None,
        },
        "started",
    ]
