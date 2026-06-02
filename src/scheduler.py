import logging

import dlt
import typer

from openhound.core.logging import logger_override
from openhound.scheduler.service import Service

logger = logging.getLogger(__name__)

service = typer.Typer()


def start():

    # Load BHE config and secrets
    bhe_uri = dlt.config["destination.bloodhoundenterprise.url"]
    collector_name = dlt.config["destination.bloodhoundenterprise.collector_name"]
    interval = dlt.config.get("destination.bloodhoundenterprise.interval", int)

    # Load BHE secrets
    token_key = dlt.secrets["destination.bloodhoundenterprise.token_key"]
    token_id = dlt.secrets["destination.bloodhoundenterprise.token_id"]

    # Start the service
    # logger_override.base_path is resolved during module-level setup() in logging.py
    # and reflects any log_path override set in dlt config.
    log_base_path = logger_override.base_path
    logger.info(
        f"Initializing service for collector '{collector_name}' "
        f"(log_base_path={log_base_path})."
    )
    svc = Service(
        bhe_uri=bhe_uri,
        token_key=token_key,
        token_id=token_id,
        collector_name=collector_name,
        interval=interval,
        log_base_path=log_base_path,
    )
    svc.start()


if __name__ == "__main__":
    start()
