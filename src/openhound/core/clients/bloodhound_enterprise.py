import base64
import gzip
import hashlib
import json
import logging
import math
import socket
import time
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

import openhound
import requests
from openhound.core.clients.bloodhound import BloodHound, BloodHoundHTTPError
from openhound.core.clients.models.jobs import (
    JobsAvailable,
    JobsCurrent,
    JobsEnd,
    JobStart,
    ArtifactUploadSession,
    ManagementAvailable,
    ManagementOperationResult,
    ManagementOperationStatus,
)

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"


SUPPORT_BUNDLE_PART_SIZE = 8 * 1024 * 1024  # 8 MiB
SUPPORT_BUNDLE_MAX_RETRIES = 3
SUPPORT_BUNDLE_RETRY_DELAY_SECONDS = 2

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
            method="GET", path="/api/v2/clients/management/available"
        )
        return ManagementAvailable.model_validate(response.json())

    def start_operation(self, operation_id: str) -> ManagementOperationResult:
        response = self._retry_support_bundle_request(
            "start management operation",
            lambda: self.request(
                method="POST",
                path="/api/v2/clients/management/start",
                body=json.dumps({"operation_id": operation_id}).encode(),
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
            ),
        )

    def complete_artifact_upload(self, artifact_id: str, operation_id: str) -> None:
        self._retry_support_bundle_request(
            "complete support bundle upload",
            lambda: self.request(
                method="POST",
                path=f"/api/v2/clients/management/artifacts/{artifact_id}/complete",
                body=json.dumps({"operation_id": operation_id}).encode(),
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
    def _is_transient_support_bundle_error(error: Exception) -> bool:
        if isinstance(error, requests.RequestException):
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
                if not self._is_transient_support_bundle_error(error):
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
