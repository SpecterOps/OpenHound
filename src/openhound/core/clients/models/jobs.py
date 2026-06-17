from pydantic import BaseModel
from datetime import datetime
from enum import StrEnum
from typing import Union


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
    created_at: Union[datetime, DateAt]
    updated_at: Union[datetime, DateAt]
    deleted_at: Union[datetime, DateAt]
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


# Values match the CHECK constraint on collector_management_operations.type in BHE.
# See: lib/go/database/migration/migrations/20260529140822_v9_collector_artifacts.sql
class ManagementOperationType(StrEnum):
    SUPPORT_BUNDLE = "support_bundle"


# Values match the CHECK constraint on collector_management_operations.status in BHE.
# See: lib/go/database/migration/migrations/20260529140822_v9_collector_artifacts.sql
class ManagementOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"
    EXPIRED = "expired"


class ManagementOperation(BaseModel):
    # Fields sourced from the collector_management_operations DB schema (BED-8268).
    # TODO(BED-8266): Confirm all field names match the actual BHE JSON response shape
    # once GET /api/v2/clients/management is implemented in BHE.
    id: str
    type: str
    status: str
    created_at: datetime
    requested_by_user_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    execution_time: datetime | None = None


class ManagementAvailable(BaseModel):
    data: list[ManagementOperation]


class ManagementOperationResult(BaseModel):
    """Response wrapper returned by POST /start and POST /end."""
    data: ManagementOperation
