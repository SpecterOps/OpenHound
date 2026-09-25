from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class Job(BaseModel):
    id: int
    client_id: str
    client_name: str
    event_id: int | None
    execution_time: datetime
    status: int
    status_message: str
    session_collection: bool
    local_group_collection: bool
    ad_structure_collection: bool
    cert_services_collection: bool
    ca_registry_collection: bool
    dc_registry_collection: bool
    all_trusted_domains: bool
    domain_controller: str | None


class DateAt(BaseModel):
    Time: datetime
    Valid: bool


class JobStartData(Job):
    start_time: datetime
    end_time: datetime
    created_at: datetime | DateAt
    updated_at: datetime | DateAt
    deleted_at: datetime | DateAt
    log_path: str | None
    event_title: str
    last_ingest: datetime


class JobStart(BaseModel):
    data: JobStartData


class JobsAvailable(BaseModel):
    data: list[Job]


class JobsCurrent(BaseModel):
    data: Job


class JobsEnd(BaseModel):
    data: Job


class CollectorJob(BaseModel):
    id: UUID
    job_schedule_id: int | None
    job_profile_id: int | None
    job_type_id: int
    job_key: str
    params_version: str
    params: dict[str, Any]
    scope_client_id: UUID | None
    secret_key_id: str | None
    priority: int
    status: str
    run_at: datetime
    unclaimed_deadline_at: datetime
    attempts: int
    max_attempts: int
    last_failure: str | None
    claimed_by: UUID | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CollectorJobsAvailableData(BaseModel):
    jobs: list[CollectorJob]


class CollectorJobsAvailable(BaseModel):
    count: int
    skip: int
    limit: int
    data: CollectorJobsAvailableData


class ManagementOperationType(StrEnum):
    SUPPORT_BUNDLE = "support_bundle"


class ManagementOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ManagementOperation(BaseModel):
    id: str
    client_id: str
    artifact_id: str | None = None
    type: ManagementOperationType
    status: ManagementOperationStatus
    created_at: datetime
    updated_at: datetime
    requested_by_user_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    execution_time: datetime


class ManagementAvailable(BaseModel):
    data: list[ManagementOperation]


class ManagementOperationResult(BaseModel):
    data: ManagementOperation


class ArtifactPart(BaseModel):
    part_number: int
    size: int
    checksum: str
    offset_start: int
    storage_key: str
    created_at: datetime
    completed_at: datetime | None = None


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"
    FINALIZING = "finalizing"


class ArtifactUploadSession(BaseModel):
    """Response data from creating a management artifact upload session.

    This models every field returned by
    ``POST /api/v2/clients/management/artifacts``. The upload flow currently
    needs only ``artifact_id``, ``part_size``, and ``part_count``, but callers
    can use the remaining session and operation metadata without consulting
    the API implementation.
    """

    artifact_id: str
    client_id: str
    storage_key: str
    status: ArtifactStatus
    part_size: int
    part_count: int
    missing_parts: list[int]
    management_operation: ManagementOperation
