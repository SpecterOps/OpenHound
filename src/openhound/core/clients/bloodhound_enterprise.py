import gzip
import json
import socket
from enum import Enum
from pathlib import Path

from openhound.core.clients.bloodhound import BloodHound
from openhound.core.clients.models.jobs import (
    JobsAvailable,
    JobsCurrent,
    JobsEnd,
    JobStart,
    ManagementAvailable,
    ManagementOperationResult,
    ManagementOperationStatus,
)


class JobStatus(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"


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
        response = self.request(
            method="POST",
            path="/api/v2/clients/management/start",
            body=json.dumps({"id": operation_id}).encode(),
        )
        return ManagementOperationResult.model_validate(response.json())

    def end_operation(
        self, operation_id: str, status: ManagementOperationStatus
    ) -> ManagementOperationResult:
        response = self.request(
            method="POST",
            path="/api/v2/clients/management/end",
            body=json.dumps({"id": operation_id, "status": status}).encode(),
        )
        return ManagementOperationResult.model_validate(response.json())

    def upload_support_bundle(self, bundle_path: Path) -> None:
        with bundle_path.open("rb") as bundle:
            self.request(
                method="POST",
                path="/api/v2/clients/management/artifacts",
                body=bundle.read(),
                extra_headers={"Content-Type": "application/zip"},
            )

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
