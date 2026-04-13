from pydantic import BaseModel
from datetime import datetime
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
