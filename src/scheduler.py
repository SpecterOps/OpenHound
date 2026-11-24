import logging
import signal

import dlt
import typer

from openhound import config
from openhound.core.clients.bhe_credentials import AWSBHECredentials
from openhound.scheduler.service import Service
from openhound.scheduler.status import SchedulerStatus, serve_status

logger = logging.getLogger(__name__)

service = typer.Typer()


def _start_managed() -> None:
    status = SchedulerStatus()

    def request_shutdown(signum, frame):
        status.draining = True

    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {sig: signal.signal(sig, request_shutdown) for sig in signals}
    try:
        with serve_status(status):
            bhe_uri = dlt.config["destination.bloodhoundenterprise.url"]
            collector_name = dlt.config[
                "destination.bloodhoundenterprise.collector_name"
            ]
            managed_config = config.get_managed_bhe_config()
            credentials = AWSBHECredentials(managed_config.secret_name)
            current_credentials = credentials.poll_until_available(
                managed_config.poll_interval,
                wait_for_shutdown=status.wait_for_shutdown,
            )
            if current_credentials is None or status.draining:
                return

            logger.info("Initializing service for collector '%s'.", collector_name)
            Service(
                bhe_uri=bhe_uri,
                token_key=current_credentials.token_key,
                token_id=current_credentials.token_id,
                collector_name=collector_name,
                interval=managed_config.poll_interval,
                credential_refresh=credentials.refresh,
                managed=True,
                status=status,
            ).start()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def start():
    if config.is_managed():
        _start_managed()
        return

    bhe_uri = dlt.config["destination.bloodhoundenterprise.url"]
    collector_name = dlt.config["destination.bloodhoundenterprise.collector_name"]
    token_key = dlt.secrets["destination.bloodhoundenterprise.token_key"]
    token_id = dlt.secrets["destination.bloodhoundenterprise.token_id"]
    logger.info("Initializing service for collector '%s'.", collector_name)
    Service(
        bhe_uri=bhe_uri,
        token_key=token_key,
        token_id=token_id,
        collector_name=collector_name,
        interval=30,
        credential_refresh=None,
    ).start()


if __name__ == "__main__":
    start()
