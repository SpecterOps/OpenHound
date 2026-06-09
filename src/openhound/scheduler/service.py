import logging
import signal
import time
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import openhound.core.logging  # noqa: F401
from openhound.core.clients.bloodhound_enterprise import BloodHoundEnterprise, JobStatus
from openhound.core.clients.models.jobs import (
    Job,
    ManagementOperation,
    ManagementOperationType,
)
from openhound.core.logging import CustomLogger
from openhound.core.manager import CollectorManager
from openhound.core.support_bundle import create_support_bundle
from openhound.scheduler import dataflow

logger = logging.getLogger(__name__)


class ExtensionNotFoundError(Exception):
    """Raised when the configured collector extension cannot be found."""

    pass


@dataclass
class Result:
    results: dict
    job_id: int


def _subprocess_collect(collector_name: str, job_id: int) -> Result:
    """A subprocess which runs the DLT pipeline for the specified collector.

    Loads the collector by name from Python entrypoints.

    Args:
        collector_name: The name of the extension to run.
        job_id: The Job ID as returned by BHE.

    Returns:
        Result: The result of the collection, including the collected data and the job ID.

    Raises:
        ExtensionNotFoundError: If the collector cannot be found via entrypoints.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logger.info(f"Subprocess running collection '{collector_name}' for job {job_id}")
    available_collectors = CollectorManager.from_entrypoint()

    for collector in available_collectors.collectors:
        if collector.name == collector_name:  # pyright: ignore[reportAttributeAccessIssue]
            results = dataflow.pipeline(extension=collector)
            logger.info(f"Collection for job {job_id} completed successfully.")
            return Result(results=results, job_id=job_id)

    logger.error(f"Collector '{collector_name}' not found in available collectors.")
    raise ExtensionNotFoundError(f"Collector '{collector_name}' not found.")


class Service:
    """Base scheduler service that checks for available jobs in BloodHound Enterprise.

    Runs on a simple loop every X seconds. On each cycle the management endpoint
    is checked first (per BHADR-6); if a management operation is pending it is
    executed before any collection job is started.
    """

    def __init__(
        self,
        bhe_uri: str,
        token_key: str,
        token_id: str,
        collector_name: str,
        interval: int = 5,
        log_base_path: Path | None = None,
    ):
        # BHE client settings
        self.bhe_uri = bhe_uri
        self.collector_name = collector_name
        self.client = BloodHoundEnterprise(
            bhe_uri=bhe_uri, token_key=token_key, token_id=token_id
        )
        # Interval how often to check for a job
        self.interval = interval

        # Directory from which log files are collected for support bundles.
        # Falls back to the platform default if not supplied explicitly.
        self.log_base_path: Path = log_base_path or CustomLogger.default_platform_path()

        # Stores the ID of currently running BHE job
        self.job_running: int | None = None

        # Futures/results from the subprocess executor
        self.future: Future[Result] | None = None
        self.executor = ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)

        # Exit condition, changed to True when the process needs to stop
        self.exit = False

    def _exit_handler(self, sig: int, frame):
        """Handle SIGINT and SIGTERM signals. Sets self.exit to True to stop the while loop"""
        self.exit = True
        logger.warning(f"Received signal {sig}, shutting down gracefully.")

    def _shutdown(self) -> None:
        """Shut down the executor and wait for running tasks to finish. Executed when the service is shut down."""
        logger.info("Collection service stopping.")
        self.executor.shutdown(wait=True, cancel_futures=True)
        logger.info("Collection service stopped.")

    def check_management(self) -> ManagementOperation | None:
        """Check the BHE management endpoint for pending operations.

        Per BHADR-6 this is always called before checking for collection jobs so
        that management operations (e.g. support-bundle upload) take priority.

        Returns:
            The first pending support-bundle operation, or None if there are none.

        # TODO(BED-8266): Validate the response field names once
        # GET /api/v2/clients/management/available is fully implemented in BHE.
        # Endpoint path confirmed by BED-8266 ticket spec; enum values confirmed via BED-8268.
        """
        logger.info("Checking for management operations in BloodHound Enterprise.")
        management = self.client.management_available
        for operation in management.data:
            if operation.type == ManagementOperationType.SUPPORT_BUNDLE:
                logger.info(
                    f"Support bundle operation found: {operation.id}"
                )
                return operation
        return None

    def _send_support_bundle(self, operation: ManagementOperation) -> None:
        """Collect all log files, zip them, and upload the bundle to BHE.

        Runs synchronously in the poll thread so that no collection jobs are
        started while the upload is in progress (per BED-7975 acceptance criteria).
        The temporary zip file is always cleaned up regardless of upload success.

        Args:
            operation: The management operation that triggered this upload.
        """
        logger.info(
            f"Starting support bundle upload for operation {operation.id}."
        )
        bundle_path = create_support_bundle(self.collector_name, self.log_base_path)
        try:
            self.client.upload_support_bundle(bundle_path)
            logger.info(
                f"Support bundle uploaded successfully for operation {operation.id}."
            )
            # TODO(BED-8266): Determine whether OH needs to notify BHE that the
            # management operation is complete (e.g. PATCH status to "succeeded").
            # If a completion callback is required it should be called here.
        finally:
            bundle_path.unlink(missing_ok=True)
            logger.debug(f"Cleaned up temporary support bundle at {bundle_path}.")

    def check_jobs(self) -> Job | None:
        """Checks BloodHound enterprise for available jobs. These can either be new jobs or jobs currently started and not finished/stopped.

        Returns:
            Job | None: Returns a Job object if there is a new or existing job available, otherwise returns None.
        """
        logger.info("Checking for new jobs in BloodHound Enterprise.")
        new_jobs = self.client.jobs_available
        if new_jobs.data:
            logger.info(f"New job available: {new_jobs.data[0].id}")
            return new_jobs.data[0]

        # TODO: Check if we want to run jobs that are already running, this can be risky because we might pick up a job thats being processed elsewhere or
        # run a job that caused the collector to crash/stop and will cause the collector to crash/stop again.
        # try:
        #     existing_jobs = self.client.jobs_current
        #     if existing_jobs.data:
        #         logger.info(f"Resuming existing job: {existing_jobs.data.id}")
        #         return existing_jobs.data
        # except BloodHoundHTTPError as e:
        #     if e.code == 404:
        #         logger.info("No current job found.")
        #         return None
        #     raise

        return None

    def _start_job(self, job: Job) -> None:
        """Starts a BloodHound enterprise job by ID and runs the collection process in a subprocess. The results are then used to end the job in BHE with a complete status.
        this function returns no value but updates the self.futures list with the future of the subprocess and sets the currently running job ID in self.job_running

        Args:
            job (Job): The job to start as a Job object.
        """

        logger.info(f"Starting job {job.id} with collector '{self.collector_name}'")
        self.client.start_job(job.id)
        self.job_running = job.id
        self.future = self.executor.submit(
            _subprocess_collect, self.collector_name, job.id
        )

    def _handle_completed_job(self, future: Future[Result]) -> None:
        """Handles completion of a job and reports success or failure to BloodHound Enterprise

        Args:
            future (Future[Result]): Future containing the result of the collection subprocess, including the job ID.
        """
        try:
            result = future.result()
            logger.info(f"Job {result.job_id} completed successfully, notifying BHE.")
            self.client.end_job(
                JobStatus.COMPLETE,
                f"Collector '{self.collector_name}' completed successfully",
            )

        except ExtensionNotFoundError:
            logger.error(
                f"Collector '{self.collector_name}' not found. Marking job as failed."
            )
            self.client.end_job(
                JobStatus.FAILED,
                f"Collector '{self.collector_name}' not found",
            )

        except Exception:
            logger.exception("Collection subprocess failed.")
            self.client.end_job(
                JobStatus.FAILED,
                f"Unexpected error while running '{self.collector_name}' collector",
            )

        finally:
            self.future = None
            self.job_running = None

    def _poll(self) -> None:
        """Checks if jobs are completed and if a job or management operation should be run.

        Poll sequence (per BHADR-6):
        1. If a job future is done, handle its completion.
        2. If no job is currently running:
           a. Check the management endpoint FIRST.
           b. If a support-bundle operation is pending, send the bundle and
              skip the job check for this cycle (job polling resumes next cycle).
           c. Otherwise check for available collection jobs and start one if found.
        """
        # Step 1: Handle completion of any currently running job.
        try:
            if self.future is not None and self.future.done():
                self._handle_completed_job(self.future)
        except Exception:
            logger.exception("Unexpected error handling completed job.")
            self.future = None
            self.job_running = None

        if self.job_running is not None:
            return

        # Step 2a-b: Check management operations first.
        try:
            management_op = self.check_management()
            if management_op:
                self._send_support_bundle(management_op)
                return
        except Exception:
            logger.exception("Error checking or executing management operations.")

        # Step 2c: No management work — check for collection jobs.
        try:
            available_job = self.check_jobs()
            if available_job:
                self._start_job(available_job)
        except Exception:
            logger.exception("Error checking for or starting jobs.")

    def start(self) -> None:
        """Start method to initiate the process of checking for jobs and running them. This method will run indefinitely until an exit signal is received"""
        signal.signal(signal.SIGINT, self._exit_handler)
        signal.signal(signal.SIGTERM, self._exit_handler)
        logger.info(
            f"Service started, monitoring {self.bhe_uri} every {self.interval} seconds."
        )
        try:
            while not self.exit:
                self._poll()
                time.sleep(self.interval)
        finally:
            self._shutdown()
