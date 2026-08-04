from pydantic import BaseModel
from datetime import datetime
from enum import StrEnum


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
    type: ManagementOperationType
    status: ManagementOperationStatus
    created_at: datetime
    requested_by_user_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    execution_time: datetime | None = None


class ManagementAvailable(BaseModel):
    data: list[ManagementOperation]


class ManagementOperationResult(BaseModel):
    data: ManagementOperation
