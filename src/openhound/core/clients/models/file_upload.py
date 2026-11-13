"""Response shapes for BloodHound's file-upload job endpoints.

A file-upload job is how BloodHound Community Edition and Enterprise both accept collected
data through the API. The client starts a job, attaches one or more files to it, and ends
it. BloodHound then reads the files ("ingesting"), runs its analysis, and marks the job
complete or failed. These models describe the job record that BloodHound returns.
"""

from enum import IntEnum

from pydantic import BaseModel


class FileUploadJobStatus(IntEnum):
    """The numeric status codes BloodHound uses for a file-upload job, with readable names."""

    INVALID = -1
    READY = 0
    RUNNING = 1
    COMPLETE = 2
    CANCELED = 3
    TIMED_OUT = 4
    FAILED = 5
    INGESTING = 6
    ANALYZING = 7
    PARTIALLY_COMPLETE = 8

    @property
    def is_finished(self) -> bool:
        """True once BloodHound will not change this job's status any further."""
        return self in (
            FileUploadJobStatus.COMPLETE,
            FileUploadJobStatus.CANCELED,
            FileUploadJobStatus.TIMED_OUT,
            FileUploadJobStatus.FAILED,
            FileUploadJobStatus.PARTIALLY_COMPLETE,
        )

    @property
    def readable(self) -> str:
        """The status as lower-case words, for messages to people."""
        return self.name.lower().replace("_", " ")


class FileUploadJob(BaseModel):
    """One file-upload job as BloodHound reports it.

    Only the fields this project reads are listed; BloodHound returns more and they are
    ignored. ``status`` is kept as the raw number so an unknown future value cannot break
    parsing; use ``status_code`` for the readable form.
    """

    id: int
    status: int
    status_message: str = ""
    total_files: int = 0
    failed_files: int = 0

    @property
    def status_code(self) -> FileUploadJobStatus | None:
        """The status as a named value, or None when BloodHound sent a number we do not know."""
        try:
            return FileUploadJobStatus(self.status)
        except ValueError:
            return None

    @property
    def status_text(self) -> str:
        """A readable status for messages, falling back to the raw number."""
        code = self.status_code
        return code.readable if code is not None else f"status {self.status}"


class FileUploadJobResponse(BaseModel):
    """The ``{"data": {...}}`` wrapper BloodHound puts around one job."""

    data: FileUploadJob


class FileUploadJobsResponse(BaseModel):
    """The ``{"data": [...]}`` wrapper BloodHound puts around a list of jobs."""

    data: list[FileUploadJob]
