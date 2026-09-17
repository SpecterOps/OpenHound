import base64
import gzip
import hashlib
import json
import logging
import math
import socket
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote

import requests

from openhound.core.clients.bloodhound import BloodHound, BloodHoundHTTPError
from openhound.core.clients.models.jobs import (
    ArtifactUploadSession,
    CollectorJobsAvailable,
    JobsAvailable,
    JobsCurrent,
    JobsEnd,
    JobStart,
    ManagementAvailable,
    ManagementOperationResult,
    ManagementOperationStatus,
)

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"


class ManagedJobOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


MANAGED_JOB_END_MAX_RETRIES = 3
MANAGED_JOB_END_RETRY_DELAY_SECONDS = 2
MANAGED_JOB_END_CONNECT_TIMEOUT_SECONDS = 10
MANAGED_JOB_END_READ_TIMEOUT_SECONDS = 120
SUPPORT_BUNDLE_PART_SIZE = 8 * 1024 * 1024  # 8 MiB
SUPPORT_BUNDLE_MAX_RETRIES = 3
SUPPORT_BUNDLE_RETRY_DELAY_SECONDS = 2
SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS = 10
SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS = 120

T = TypeVar("T")


class BloodHoundEnterprise(BloodHound):
    @property
    def jobs_available(self) -> JobsAvailable:
        path = "/api/v2/jobs/available"
        response = self.request(method="GET", path=path)
        return JobsAvailable.model_validate(response.json())

    @property
    def jobs_current(self) -> JobsCurrent:
        path = "/api/v2/jobs/current"
        response = self.request(method="GET", path=path)
        return JobsCurrent.model_validate(response.json())

    def available_collector_jobs(self, job_key: str) -> CollectorJobsAvailable:
        encoded_job_key = quote(f"eq:{job_key}", safe="")
        path = (
            "/api/v2/collector-job-queue/available?limit=1&job_key="
            f"{encoded_job_key}"
        )
        logger.debug(
            "Polling managed collector job queue.",
            extra={"endpoint": path, "job_key": job_key},
        )
        try:
            response = self.request(method="GET", path=path)
        except (BloodHoundHTTPError, requests.RequestException):
            logger.exception(
                "Managed collector job queue request failed.",
                extra={"endpoint": path, "job_key": job_key},
            )
            raise
        return CollectorJobsAvailable.model_validate(response.json())

    def start_job(self, job_id: int) -> JobStart:
        path = "/api/v2/jobs/start"
        body = json.dumps({"id": job_id})
        response = self.request(method="POST", path=path, body=body.encode())
        return JobStart.model_validate(response.json())

    def end_job(self, status: JobStatus, message: str) -> JobsEnd:
        path = "/api/v2/jobs/end"
        payload = {"status": status.value, "message": message}
        job_content = json.dumps(payload)
        response = self.request(method="POST", path=path, body=job_content.encode())
        return JobsEnd.model_validate(response.json())

    def end_managed_job(
        self,
        job_id: str,
        outcome: ManagedJobOutcome,
        failure_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """End a claimed managed collector job."""
        path = f"/api/v2/collector-jobs/{job_id}/end"
        payload: dict[str, Any] = {"outcome": outcome.value}
        if failure_message is not None:
            payload["failure_message"] = failure_message
        if metadata is not None:
            payload["metadata"] = metadata
        body = json.dumps(payload).encode()
        max_attempts = MANAGED_JOB_END_MAX_RETRIES + 1

        for attempt in range(1, max_attempts + 1):
            log_context = {
                "job_id": job_id,
                "outcome": outcome.value,
                "attempt": attempt,
                "max_attempts": max_attempts,
            }
            logger.info(
                "Attempting to end managed collector job %s (%s/%s).",
                job_id,
                attempt,
                max_attempts,
                extra=log_context,
            )
            logger.debug(
                "Sending managed collector end-job request.",
                extra={
                    **log_context,
                    "endpoint": path,
                    "has_failure_message": failure_message is not None,
                    "has_metadata": metadata is not None,
                },
            )

            try:
                self.request(
                    method="POST",
                    path=path,
                    body=body,
                    timeout=(
                        MANAGED_JOB_END_CONNECT_TIMEOUT_SECONDS,
                        MANAGED_JOB_END_READ_TIMEOUT_SECONDS,
                    ),
                )
            except Exception as error:
                retryable = self._is_transient_request_error(
                    error,
                    (requests.ConnectionError, requests.Timeout),
                )
                logger.info(
                    "Managed collector job %s end attempt failed.",
                    job_id,
                    extra={**log_context, "retryable": retryable},
                )
                logger.debug(
                    "Managed collector job end request failed.",
                    extra={**log_context, "retryable": retryable},
                    exc_info=True,
                )

                if not retryable or attempt == max_attempts:
                    raise

                logger.debug(
                    "Retrying managed collector job end request in %s seconds.",
                    MANAGED_JOB_END_RETRY_DELAY_SECONDS,
                    extra={
                        **log_context,
                        "retry_delay_seconds": MANAGED_JOB_END_RETRY_DELAY_SECONDS,
                    },
                )
                time.sleep(MANAGED_JOB_END_RETRY_DELAY_SECONDS)
            else:
                logger.info(
                    "Managed collector job %s ended successfully.",
                    job_id,
                    extra=log_context,
                )
                return

    def ingest(self, data: str) -> None:
        path = "/api/v2/ingest"
        headers = {
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        }
        compressed_data = gzip.compress(data.encode())
        self.request(
            method="POST", path=path, body=compressed_data, extra_headers=headers
        )

    @property
    def management_available(self) -> ManagementAvailable:
        response = self.request(
            method="GET",
            path="/api/v2/clients/management/available",
            timeout=(
                SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
                SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
            ),
        )
        return ManagementAvailable.model_validate(response.json())

    def start_operation(self, operation_id: str) -> ManagementOperationResult:
        response = self._retry_support_bundle_request(
            "start management operation",
            lambda: self.request(
                method="POST",
                path="/api/v2/clients/management/start",
                body=json.dumps({"operation_id": operation_id}).encode(),
                timeout=(
                    SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
                    SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
                ),
            ),
        )
        return ManagementOperationResult.model_validate(response.json())

    def end_operation(
        self, operation_id: str, status: ManagementOperationStatus
    ) -> ManagementOperationResult:
        response = self._retry_support_bundle_request(
            "end management operation",
            lambda: self.request(
                method="POST",
                path="/api/v2/clients/management/end",
                body=json.dumps(
                    {"operation_id": operation_id, "status": status}
                ).encode(),
                timeout=(
                    SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
                    SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
                ),
            ),
        )
        return ManagementOperationResult.model_validate(response.json())

    def create_artifact_upload(
        self, operation_id: str, bundle_path: Path
    ) -> ArtifactUploadSession:
        total_size = bundle_path.stat().st_size

        logger.info("Total size of the support bundle: %s", total_size)
        if total_size <= 0:
            raise ValueError("Support bundle must not be empty.")

        part_size = SUPPORT_BUNDLE_PART_SIZE
        checksum = self._file_checksum(bundle_path)
        response = self._retry_support_bundle_request(
            "create support bundle upload",
            lambda: self.request(
                method="POST",
                path="/api/v2/clients/management/artifacts",
                body=json.dumps(
                    {
                        "operation_id": operation_id,
                        "artifact_type": "support_bundle",
                        "total_size": total_size,
                        "part_size": part_size,
                        "part_count": math.ceil(total_size / part_size),
                        "content_type": "application/zip",
                        "checksum_algorithm": "sha256",
                        "checksum": checksum,
                    }
                ).encode(),
                timeout=(
                    SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
                    SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
                ),
            ),
        )
        return ArtifactUploadSession.model_validate(response.json()["data"])

    def upload_artifact_part(
        self, artifact_id: str, part_number: int, content: bytes
    ) -> None:
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
        self._retry_support_bundle_request(
            f"upload support bundle part {part_number}",
            lambda: self.request(
                method="POST",
                path=f"/api/v2/clients/management/artifacts/{artifact_id}/parts/{part_number}",
                body=content,
                extra_headers={
                    "Content-Length": str(len(content)),
                    "Content-Type": "application/zip",
                    "Content-Digest": f"sha-256=:{checksum}:",
                },
                timeout=(
                    SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
                    SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
                ),
            ),
        )

    def complete_artifact_upload(self, artifact_id: str, operation_id: str) -> None:
        self._retry_support_bundle_request(
            "complete support bundle upload",
            lambda: self.request(
                method="POST",
                path=f"/api/v2/clients/management/artifacts/{artifact_id}/complete",
                body=json.dumps({"operation_id": operation_id}).encode(),
                timeout=(
                    SUPPORT_BUNDLE_CONNECT_TIMEOUT_SECONDS,
                    SUPPORT_BUNDLE_READ_TIMEOUT_SECONDS,
                ),
            ),
        )

    def upload_support_bundle(self, operation_id: str, bundle_path: Path) -> None:
        """Create an upload session, transfer every ZIP part, then complete it."""
        session = self.create_artifact_upload(operation_id, bundle_path)
        with bundle_path.open("rb") as bundle:
            for part_number in range(1, session.part_count + 1):
                part = bundle.read(session.part_size)
                if not part:
                    raise ValueError(f"Support bundle ended before part {part_number}.")
                self.upload_artifact_part(session.artifact_id, part_number, part)
            if bundle.read(1):
                raise ValueError("Support bundle grew while it was being uploaded.")
        self.complete_artifact_upload(session.artifact_id, operation_id)

    @staticmethod
    def _file_checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as bundle:
            for chunk in iter(lambda: bundle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_transient_request_error(
        error: Exception,
        retryable_request_errors: tuple[type[requests.RequestException], ...],
    ) -> bool:
        if isinstance(error, retryable_request_errors):
            return True
        return isinstance(error, BloodHoundHTTPError) and error.code in {
            408,
            429,
            500,
            502,
            503,
            504,
        }

    def _retry_support_bundle_request(
        self, description: str, request: Callable[[], T]
    ) -> T:
        for retry in range(SUPPORT_BUNDLE_MAX_RETRIES + 1):
            try:
                return request()
            except Exception as error:
                if not self._is_transient_request_error(
                    error,
                    (requests.RequestException,),
                ):
                    raise
                if retry == SUPPORT_BUNDLE_MAX_RETRIES:
                    raise
                logger.warning(
                    "%s failed transiently; retrying in %s seconds (%s/%s).",
                    description,
                    SUPPORT_BUNDLE_RETRY_DELAY_SECONDS,
                    retry + 1,
                    SUPPORT_BUNDLE_MAX_RETRIES,
                    exc_info=True,
                )
                time.sleep(SUPPORT_BUNDLE_RETRY_DELAY_SECONDS)

        raise AssertionError("Support bundle retry loop exited unexpectedly.")

    def update_client_metadata(self) -> None:
        path = "/api/v2/clients/update"
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = "unknown"

        if hostname == "unknown":
            ip_address = "unknown"
        else:
            try:
                ip_address = socket.gethostbyname(hostname)
            except OSError:
                ip_address = "unknown"

        payload = {
            "Address": ip_address,
            "Hostname": hostname,
            "Version": self.bhe_version,
        }
        body = json.dumps(payload)

        self.request(method="PUT", path=path, body=body.encode())
