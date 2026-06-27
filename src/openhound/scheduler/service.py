import logging
import signal
import time
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

import openhound.core.logging  # noqa: F401
from openhound.core.clients.bloodhound_enterprise import BloodHoundEnterprise, JobStatus
from openhound.core.clients.models.jobs import Job
from openhound.core.manager import CollectorManager
from openhound.scheduler import dataflow
from openhound.scheduler.context import RunContext, run_context

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds; fixed poll/check-in cadence, must remain below BHE's 600s client-checkin timeout


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

    with run_context.set(RunContext(job_id=job_id)):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        logger.info(
            f"Subprocess running collection '{collector_name}' for job {job_id}"
        )
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

    Runs on a simple loop every X seconds and checks for available jobs. If a job is available,
    a subprocess is started for the configured collector to run the DLT/OpenHound pipeline.
    """

    def __init__(
        self,
        bhe_uri: str,
        token_key: str,
        token_id: str,
        collector_name: str,
    ):
        # BHE client settings
        self.bhe_uri = bhe_uri
        self.collector_name = collector_name
        self.client = BloodHoundEnterprise(
            bhe_uri=bhe_uri, token_key=token_key, token_id=token_id
        )
        # Interval how often to check for a job
        self.interval = POLL_INTERVAL

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

    def _reset_executor(self) -> None:
        """Tear down and recreate the process pool after it has entered a broken state."""
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            logger.exception("Error shutting down broken executor.")
        self.executor = ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)

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
        try:
            self.future = self.executor.submit(
                _subprocess_collect, self.collector_name, job.id
            )
        except BrokenProcessPool:
            logger.exception(
                f"Failed to submit job {job.id}: process pool is broken, resetting."
            )
            self.future = None
            self.job_running = None
            self._reset_executor()
            self.client.end_job(
                JobStatus.FAILED,
                f"Failed to start collector '{self.collector_name}': worker pool was broken",
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

        except BrokenProcessPool:
            logger.exception(
                "Collection worker was terminated abruptly; resetting process pool."
            )
            self._reset_executor()
            self.client.end_job(
                JobStatus.FAILED,
                f"Collection worker for '{self.collector_name}' was terminated abruptly",
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
        """Checks if jobs are completed and if a job should be run."""
        # If a job is currently running check if the future is done and handle completion
        try:
            if self.future is not None and self.future.done():
                self._handle_completed_job(self.future)
        except Exception:
            logger.exception("Unexpected error handling completed job.")
            self.future = None
            self.job_running = None

        # If no job is currently running, check for new jobs available and start the job
        try:
            if self.job_running is None:
                available_job = self.check_jobs()
                if available_job:
                    self._start_job(available_job)
            else:
                self.client.jobs_current
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
