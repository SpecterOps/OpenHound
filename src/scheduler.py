import logging

import dlt
import typer

from openhound.scheduler.service import Service

logger = logging.getLogger(__name__)

service = typer.Typer()


def start():

    # Load BHE config and secrets
    bhe_uri = dlt.config["destination.bloodhoundenterprise.url"]
    collector_name = dlt.config["destination.bloodhoundenterprise.collector_name"]

    # Load BHE secrets
    token_key = dlt.secrets["destination.bloodhoundenterprise.token_key"]
    token_id = dlt.secrets["destination.bloodhoundenterprise.token_id"]

    # Start the service
    logger.info(f"Initializing service for collector '{collector_name}'.")
    svc = Service(
        bhe_uri=bhe_uri,
        token_key=token_key,
        token_id=token_id,
        collector_name=collector_name,
    )
    svc.start()


if __name__ == "__main__":
    start()
