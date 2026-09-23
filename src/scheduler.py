import logging
from functools import partial

import dlt
import typer

from openhound import config
from openhound.core.clients.bhe_credentials import AWSBHECredentials
from openhound.scheduler.service import Service

logger = logging.getLogger(__name__)

service = typer.Typer()


def start():
    # Load BHE config and secrets
    bhe_uri = dlt.config["destination.bloodhoundenterprise.url"]
    collector_name = dlt.config["destination.bloodhoundenterprise.collector_name"]

    if config.is_managed():
        managed_config = config.get_managed_bhe_config()
        credentials = AWSBHECredentials(managed_config.secret_name)
        credential_refresh = partial(
            credentials.poll_until_available, managed_config.poll_interval
        )
        current_credentials = credential_refresh()
        token_key = current_credentials.token_key
        token_id = current_credentials.token_id
        interval = managed_config.poll_interval
    else:
        # Load BHE secrets
        token_key = dlt.secrets["destination.bloodhoundenterprise.token_key"]
        token_id = dlt.secrets["destination.bloodhoundenterprise.token_id"]
        interval = 30
        credential_refresh = None

    # Start the service
    logger.info(f"Initializing service for collector '{collector_name}'.")
    svc = Service(
        bhe_uri=bhe_uri,
        token_key=token_key,
        token_id=token_id,
        collector_name=collector_name,
        interval=interval,
        credential_refresh=credential_refresh,
    )
    svc.start()


if __name__ == "__main__":
    start()
