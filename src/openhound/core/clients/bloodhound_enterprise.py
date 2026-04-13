import gzip
import json
from enum import Enum

from openhound.core.clients.bloodhound import BloodHound
from openhound.core.clients.models.jobs import (
    JobsAvailable,
    JobsCurrent,
    JobsEnd,
    JobStart,
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
