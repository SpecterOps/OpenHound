import gzip
import json
from enum import Enum
from pathlib import Path

from openhound.core.clients.bloodhound import BloodHound
from openhound.core.clients.models.jobs import (
    JobsAvailable,
    JobsCurrent,
    JobsEnd,
    JobStart,
    ManagementAvailable,
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
        # Endpoint confirmed via BHADR-6 ADR (BED-8268).
        # TODO(BED-8266): Confirm response field names match the Go handler once
        # GET /api/v2/clients/management is fully implemented in BHE.
        path = "/api/v2/clients/management"
        response = self.request(method="GET", path=path)
        return ManagementAvailable.model_validate(response.json())

    def upload_support_bundle(self, bundle_path: Path) -> None:
        """Upload a support bundle zip file to BHE.

        Reads the entire zip file into memory and POSTs it as raw bytes with
        Content-Type: application/zip. BHE returns 202 Accepted on success.

        Args:
            bundle_path: Path to the zip file to upload.

        # TODO(BED-7968): Confirm upload endpoint path and Content-Type header once
        # POST /api/v2/clients/management/artifacts is merged and deployed.
        # TODO: For bundles >= 1 GB consider streaming instead of reading into memory.
        #       This requires refactoring BloodHound._request() to accept a file-like body
        #       since the HMAC signature currently requires the full body bytes.
        """
        path = "/api/v2/clients/management/artifacts"
        with bundle_path.open("rb") as f:
            body = f.read()
        self.request(
            method="POST",
            path=path,
            body=body,
            extra_headers={"Content-Type": "application/zip"},
        )
