import json
import logging
import math
import os
import signal
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import openhound
import openhound.core.logging as openhound_logging
from openhound.config import is_managed
from openhound.core.clients.aws_secrets_manager import AWSSecretsManager
from openhound.core.clients.bhe_credentials import BHECredentials
from openhound.core.clients.bloodhound import BloodHoundHTTPError
from openhound.core.clients.bloodhound_enterprise import (
    BloodHoundEnterprise,
    JobStatus,
    ManagedJobOutcome,
)
from openhound.core.clients.models.jobs import (
    CollectorJob,
    Job,
    ManagementOperation,
    ManagementOperationStatus,
    ManagementOperationType,
)
from openhound.core.manager import CollectorManager
from openhound.core.support_bundle import create_support_bundle
from openhound.scheduler import dataflow

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds; fixed poll/check-in cadence, must remain below BHE's 600s client-checkin timeout
HEARTBEAT_INTERVAL = (
    300  # seconds; BHE expires managed jobs after 15 minutes without a heartbeat
)

# Explicit source registration for the collectors shipped with this PoC.
MANAGED_SOURCE_MODULES = {
    "faker": "openhound_faker.source",
    "aws": "openhound_aws.source",
}


class ExtensionNotFoundError(Exception):
    """Raised when the configured collector extension cannot be found."""


class ManagedRuntimeUnavailableError(RuntimeError):
    """Managed scheduling requires a configured collection runtime."""


class CredentialValidationError(ValueError):
    """A managed job's required credential document could not be loaded."""


class ManagedJobProcessingError(RuntimeError):
    """A sanitized managed polling failure that can be retried next poll."""


@dataclass(frozen=True)
class PreparedManagedJob:
    """Validated job and credentials for the managed collection runtime.

    Keep credentials in memory; never serialize or log them.
    """

    job: CollectorJob = field(repr=False)
    credentials: dict[str, Any] = field(repr=False)


def _usable_credential_value(value: Any) -> bool:
    """Validate generic JSON values without inventing a collector field schema."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and all(
            isinstance(key, str)
            and bool(key.strip())
            and _usable_credential_value(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return bool(value) and all(_usable_credential_value(item) for item in value)
    if isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


@dataclass
class Result:
    results: dict
    job_id: int | str


def _set_managed_source_environment(
    collector, params: dict[str, Any], secrets: dict[str, Any]
) -> None:
    """Provide one managed job's validated values to its DLT source."""
    source = collector.dlt_source
    if source is None:
        raise ValueError(f"Collector '{collector.name}' has no registered DLT source")

    section = source.section or source.__module__.rsplit(".", 1)[-1]
    source_name = source.name or source.__name__
    prefix = f"SOURCES__{section.upper()}__{source_name.upper()}"
    for name, value in params.items():
        if name.lower() == "credentials":
            raise ValueError("Managed job parameters cannot contain credentials")
        os.environ[f"{prefix}__{name.upper()}"] = (
            value if isinstance(value, str) else json.dumps(value)
        )
    for name, value in secrets.items():
        os.environ[f"{prefix}__CREDENTIALS__{name.upper()}"] = (
            value if isinstance(value, str) else json.dumps(value)
        )


def _subprocess_collect(
    collector_name: str,
    job_id: int | str,
    params: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
) -> Result:
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
    available_collectors = CollectorManager.from_entrypoint()

    for collector in available_collectors.collectors:
        if collector.name == collector_name:  # pyright: ignore[reportAttributeAccessIssue]
            if params is not None or secrets is not None:
                if (
                    collector.dlt_source is None
                    and collector.name in MANAGED_SOURCE_MODULES
                ):
                    collector.dlt_source = import_module(
                        MANAGED_SOURCE_MODULES[collector.name]
                    ).source
                _set_managed_source_environment(collector, params or {}, secrets or {})
            log_fields = {
                "collector_extension": collector.name,
                "collector_extension_version": (
                    collector.package_version
                    or (
                        str(collector.metadata.version)
                        if collector.metadata is not None
                        else "unknown"
                    )
                ),
                "openhound_version": openhound.__version__,
                "job_id": job_id,
            }
            logger.info(
                "Subprocess running collection '%s' for job %s",
                collector_name,
                job_id,
                extra=log_fields,
            )
            results = dataflow.pipeline(extension=collector)
            logger.info(
                "Collection for job %s completed successfully.",
                job_id,
                extra=log_fields,
            )
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
        log_base_path: Path | None = None,
        interval: int = POLL_INTERVAL,
        credential_refresh: Callable[[], BHECredentials] | None = None,
        *,
        managed: bool | None = None,
        secrets_manager: AWSSecretsManager | None = None,
        managed_job_runner: Callable[[PreparedManagedJob], None] | None = None,
    ):
        # BHE client settings
        self.bhe_uri = bhe_uri
        self.client = BloodHoundEnterprise(
            bhe_uri=bhe_uri,
            token_key=token_key,
            token_id=token_id,
            credential_refresh=credential_refresh,
        )

        # Shared scheduler settings
        self.collector_name = collector_name
        self.interval = interval
        self.log_base_path = (
            log_base_path or openhound_logging.logger_override.base_path
        )

        # Managed collection settings and recovery state
        self.managed = is_managed() if managed is None else managed
        self.secrets_manager = secrets_manager or AWSSecretsManager()
        self.managed_job_runner = managed_job_runner or self._run_prepared_managed_job
        self._pending_managed_claim: str | None = None
        self._pending_managed_failure: str | None = None

        # Stores the ID of the currently running BHE job.
        # MVP runs one job at a time.
        self.job_running: int | str | None = None
        self.managed_job_id: str | None = None
        self.next_heartbeat_at: float | None = None

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
        """Finish the active job and keep checking in until its worker stops."""
        logger.info("Collection service stopping.")
        while self.future is not None and not self.future.done():
            self._poll_running_job()
            if self.future is not None:
                time.sleep(self.interval)
        if self.future is not None:
            try:
                self._handle_completed_job(self.future)
            except Exception:
                logger.exception(
                    "Unexpected error handling completed job during shutdown."
                )
                self._clear_running_job()
        self.executor.shutdown(wait=True, cancel_futures=True)
        logger.info("Collection service stopped.")

    def _clear_running_job(self) -> None:
        self.future = None
        self.job_running = None
        self.managed_job_id = None
        self.next_heartbeat_at = None

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

    def check_managed_collector_jobs(self) -> CollectorJob | None:
        """Return the first available job from the managed collector queue."""
        logger.info("Checking for new managed collector jobs in BloodHound Enterprise.")
        available_jobs = self.client.available_collector_jobs(self.collector_name)
        for job in available_jobs.data.jobs:
            if job.job_key == self.collector_name:
                return job
        return None

    def _report_managed_credential_failure(self, job_id: str) -> None:
        # Retain the unresolved ID if reporting exhausts its retries. On the next
        # poll retry end, rather than claiming or retrieving another job.
        self._pending_managed_failure = job_id
        self.client.end_managed_job(
            job_id,
            ManagedJobOutcome.FAILED,
            failure_message="Credential validation failed",
        )
        self._pending_managed_failure = None

    def _load_managed_credentials(self, job: CollectorJob) -> dict[str, Any]:
        """Retrieve and validate credentials, exposing only a safe failure message."""
        if job.secret_key_id is None or not job.secret_key_id.strip():
            raise CredentialValidationError("Credential validation failed")

        logger.debug(
            "Retrieving managed collector credentials.",
            extra={
                "job_id": str(job.id),
                "provider": "aws.secretsmanager",
                "secret_count": 1,
            },
        )
        try:
            credentials = self.secrets_manager.get_secret(job.secret_key_id)
            if isinstance(credentials, dict) and _usable_credential_value(credentials):
                return credentials
        except Exception:  # noqa: BLE001 - redact secret reader and validation errors
            raise CredentialValidationError("Credential validation failed") from None
        raise CredentialValidationError("Credential validation failed")

    def _validate_managed_claim(self, job: CollectorJob, job_id: str) -> None:
        """Check the job identity and lease confirmed by BHE's claim response."""

        # The authenticated claim endpoint confirms the holder. Reclaiming does
        # not renew the lease, so an old response is not enough to permit work.
        lease_expires_at = job.claim_expires_at
        if (
            str(job.id) != job_id
            or job.job_key != self.collector_name
            or job.status != "claimed"
            or job.claimed_by is None
            or job.claimed_at is None
            or lease_expires_at is None
            or lease_expires_at.utcoffset() is None
            or lease_expires_at <= datetime.now(UTC)
        ):
            logger.error(
                "Managed claim response does not confirm a usable claim.",
                extra={"job_id": job_id},
            )
            raise ManagedJobProcessingError("Managed claim could not be reconciled")

    def _claim_and_validate_managed_job(self, job_id: str) -> PreparedManagedJob | None:
        """Claim a managed job and validate its credentials."""
        # A timeout can happen after BHE commits the claim. Keep the ID until we
        # reconcile it; discovery no longer lists jobs held by this collector.
        self._pending_managed_claim = job_id
        try:
            job = self.client.claim_managed_job(job_id)
        except BloodHoundHTTPError as error:
            if error.code in (404, 409):
                # An existing claimed job held by this client would return 200.
                self._pending_managed_claim = None
            if error.code == 409:
                logger.debug(
                    "Managed collector claim conflict.",
                    extra={"job_id": job_id},
                )
                return None
            raise

        self._validate_managed_claim(job, job_id)
        context = {"job_id": job_id}
        logger.info("Job successfully picked up", extra=context)
        try:
            credentials = self._load_managed_credentials(job)
        except CredentialValidationError:
            logger.error("Credential validation failed", extra=context)
            self._pending_managed_claim = None
            self._report_managed_credential_failure(str(job.id))
            return None

        # Secret retrieval may have consumed the remaining lease time.
        self._validate_managed_claim(job, job_id)
        self._pending_managed_claim = None
        logger.info("Credential retrieval valid", extra=context)
        return PreparedManagedJob(job=job, credentials=credentials)

    def _require_managed_runtime(self) -> Callable[[PreparedManagedJob], None]:
        if self.managed_job_runner is None:
            raise ManagedRuntimeUnavailableError(
                "Managed collection runtime is required for managed scheduling"
            )
        return self.managed_job_runner

    def _run_prepared_managed_job(self, prepared: PreparedManagedJob) -> None:
        self.run_claimed_job(
            str(prepared.job.id), prepared.job.params, prepared.credentials
        )

    def _poll_managed_jobs(self) -> None:
        runner = self._require_managed_runtime()
        try:
            if self._pending_managed_failure is not None:
                self._report_managed_credential_failure(self._pending_managed_failure)
                return
            job_id = self._pending_managed_claim
            if job_id is None:
                available_job = self.check_managed_collector_jobs()
                if available_job is None:
                    return
                job_id = str(available_job.id)
            prepared = self._claim_and_validate_managed_job(job_id)
            if prepared is not None:
                runner(prepared)
        except Exception:  # noqa: BLE001 - redact managed runtime errors
            raise ManagedJobProcessingError(
                "Managed collector job processing failed"
            ) from None

    def check_management(self) -> ManagementOperation | None:
        """Return the first pending support-bundle operation, if any."""
        logger.info("Checking for management operations in BloodHound Enterprise.")
        for operation in self.client.management_available.data:
            if (
                operation.type is ManagementOperationType.SUPPORT_BUNDLE
                and operation.status is ManagementOperationStatus.QUEUED
            ):
                return operation
        return None

    def _poll_management(self) -> bool:
        """Return whether a support-bundle request was handled this poll."""
        try:
            operation = self.check_management()
        except Exception:
            logger.exception("Error checking management operations.")
            return False

        if operation is None:
            return False
        try:
            self._send_support_bundle(operation)
        except Exception:
            logger.exception("Error executing management operation.")
        return True

    def _send_support_bundle(self, operation: ManagementOperation) -> None:
        """Claim, upload, and complete a support-bundle operation."""
        bundle_path: Path | None = None
        try:
            self.client.start_operation(operation.id)
            bundle_path = create_support_bundle(self.collector_name, self.log_base_path)
            self.client.upload_support_bundle(operation.id, bundle_path)
            self.client.end_operation(operation.id, ManagementOperationStatus.SUCCEEDED)
        except Exception:
            logger.exception("Support bundle operation %s failed.", operation.id)
            try:
                self.client.end_operation(
                    operation.id, ManagementOperationStatus.FAILED
                )
            except Exception:
                logger.exception(
                    "Unable to mark management operation %s as failed.", operation.id
                )
            raise
        finally:
            if bundle_path is not None:
                try:
                    bundle_path.unlink(missing_ok=True)
                    bundle_path.parent.rmdir()
                except OSError:
                    logger.exception(
                        "Unable to remove support bundle for operation %s.",
                        operation.id,
                    )

    def _start_job(self, job: Job) -> None:
        """Start an unmanaged job using the shared worker lifecycle."""
        self._launch_job(job.id)

    def run_claimed_job(
        self, job_id: str, params: dict[str, Any], secrets: dict[str, Any]
    ) -> None:
        """Start a validated managed job; the scheduler polls it until completion."""
        job_id = str(job_id)
        self._launch_job(job_id, params=params, secrets=secrets, managed=True)

    def _launch_job(
        self,
        job_id: int | str,
        *,
        params: dict[str, Any] | None = None,
        secrets: dict[str, Any] | None = None,
        managed: bool = False,
    ) -> None:
        """Notify BHE and submit one collector worker."""
        if self.job_running is not None:
            raise RuntimeError(f"Job {self.job_running} is already running")

        logger.info("Starting job %s with collector '%s'.", job_id, self.collector_name)
        if managed:
            self.client.start_collector_job(str(job_id))
        else:
            assert isinstance(job_id, int)
            self.client.start_job(job_id)

        if managed:
            logger.info("Managed collector job %s started successfully.", job_id)
        self.job_running = job_id
        self.managed_job_id = str(job_id) if managed else None
        self.next_heartbeat_at = (
            time.monotonic() + HEARTBEAT_INTERVAL if managed else None
        )
        try:
            self.future = self.executor.submit(
                _subprocess_collect,
                self.collector_name,
                job_id,
                dict(params or {}) if managed else None,
                dict(secrets or {}) if managed else None,
            )
        except BrokenProcessPool:
            logger.exception("Failed to submit job %s: process pool is broken.", job_id)
            self._reset_executor()
            try:
                self._end_job(
                    JobStatus.FAILED,
                    f"Failed to start collector '{self.collector_name}': worker pool was broken",
                )
            finally:
                self._clear_running_job()
        except Exception as error:
            logger.error(
                "Failed to submit job %s.",
                job_id,
                extra={"job_id": job_id, "error_type": type(error).__name__},
            )
            try:
                self._end_job(
                    JobStatus.FAILED,
                    f"Failed to start collector '{self.collector_name}'",
                )
            finally:
                self._clear_running_job()
            raise

    def _end_job(self, status: JobStatus, message: str) -> None:
        if self.managed_job_id is not None:
            self.client.end_managed_job(
                self.managed_job_id,
                (
                    ManagedJobOutcome.SUCCEEDED
                    if status is JobStatus.COMPLETE
                    else ManagedJobOutcome.FAILED
                ),
                failure_message=message if status is JobStatus.FAILED else None,
            )
        else:
            self.client.end_job(status, message)

    def _send_managed_heartbeat(self, job_id: str) -> None:
        try:
            self.client.heartbeat_collector_job(job_id)
        except Exception as error:  # noqa: BLE001 - transient heartbeat failures must not stop collection
            if isinstance(error, BloodHoundHTTPError) and error.code == 404:
                self._abort_managed_job(job_id)
                return
            logger.info(
                "Managed collector job %s heartbeat request failed.",
                job_id,
                extra={
                    "job_id": job_id,
                    "error_type": type(error).__name__,
                    "status_code": (
                        error.code if isinstance(error, BloodHoundHTTPError) else None
                    ),
                },
            )
        else:
            logger.info("Managed collector job %s heartbeat succeeded.", job_id)

    def _abort_managed_job(self, job_id: str) -> None:
        """Stop local work without reporting an outcome for a claim we no longer own."""
        logger.warning(
            "Managed collector job %s claim lost; stopping collection.", job_id
        )
        processes = list((self.executor._processes or {}).values())
        try:
            for process in processes:
                try:
                    process.kill()
                except OSError:
                    logger.exception("Unable to kill worker process.")
            self.executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            logger.exception("Error shutting down executor after claim loss.")
        finally:
            self._clear_running_job()
            self.executor = ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1)

    def _handle_completed_job(self, future: Future[Result]) -> None:
        """Report the completed worker's outcome through the active job API."""
        try:
            result = future.result()
            logger.info(f"Job {result.job_id} completed successfully, notifying BHE.")
        except ExtensionNotFoundError:
            logger.error(
                f"Collector '{self.collector_name}' not found. Marking job as failed."
            )
            status = JobStatus.FAILED
            message = f"Collector '{self.collector_name}' not found"
        except BrokenProcessPool:
            logger.exception(
                "Collection worker was terminated abruptly; resetting process pool."
            )
            self._reset_executor()
            status = JobStatus.FAILED
            message = (
                f"Collection worker for '{self.collector_name}' was terminated abruptly"
            )
        except Exception as error:  # noqa: BLE001 - any worker error must end the job
            logger.error(
                "Collection subprocess failed.",
                extra={"error_type": type(error).__name__},
            )
            status = JobStatus.FAILED
            message = (
                f"Unexpected error while running '{self.collector_name}' collector"
            )
        else:
            status = JobStatus.COMPLETE
            message = f"Collector '{self.collector_name}' completed successfully"
        self._end_job(status, message)
        self._clear_running_job()

    def _poll_running_job(self) -> None:
        if self.managed_job_id is not None:
            if (
                self.next_heartbeat_at is not None
                and time.monotonic() >= self.next_heartbeat_at
            ):
                self._send_managed_heartbeat(self.managed_job_id)
                if self.managed_job_id is not None:
                    self.next_heartbeat_at = time.monotonic() + HEARTBEAT_INTERVAL
        else:
            try:
                _ = self.client.jobs_current
            except Exception:
                logger.exception("Error checking in-progress job.")

    def _poll(self) -> None:
        """Checks if jobs are completed and if a job should be run."""
        try:
            if self.future is not None and self.future.done():
                self._handle_completed_job(self.future)
        except Exception:
            if self.managed:
                logger.error(
                    "Error reporting completed managed job; retaining result for retry."
                )
            else:
                logger.exception(
                    "Error reporting completed job; retaining result for retry."
                )

        # Support bundles precede new collections in either mode. An unresolved
        # managed job must be reconciled before accepting management work.
        idle = (
            self.job_running is None
            and self._pending_managed_claim is None
            and self._pending_managed_failure is None
        )
        if idle and self._poll_management():
            return

        if self.managed:
            if self.job_running is None:
                self._poll_managed_jobs()
            else:
                self._poll_running_job()
            return

        if self.job_running is None:
            try:
                available_job = self.check_jobs()
                if available_job:
                    self._start_job(available_job)
            except Exception:
                logger.exception("Error checking for or starting jobs.")
        else:
            self._poll_running_job()

    def start(self) -> None:
        """Start method to initiate the process of checking for jobs and running them. This method will run indefinitely until an exit signal is received"""
        signal.signal(signal.SIGINT, self._exit_handler)
        signal.signal(signal.SIGTERM, self._exit_handler)
        logger.info(
            f"Service started, monitoring {self.bhe_uri} every {self.interval} seconds."
        )
        try:
            self.client.update_client_metadata()

        except Exception:
            if self.managed:
                logger.error("Unable to update client metadata.")
            else:
                logger.exception("Unable to update client metadata.")

        try:
            while not self.exit:
                if self.managed:
                    try:
                        self._poll()
                    except ManagedRuntimeUnavailableError:
                        logger.error(
                            "Managed collection runtime is unavailable; continuing support-bundle polling."
                        )
                    except ManagedJobProcessingError:
                        logger.error(
                            "Managed scheduler poll failed; retrying next poll."
                        )
                else:
                    self._poll()
                time.sleep(self.interval)
        finally:
            self._shutdown()
