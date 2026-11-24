import errno
import logging
import sys
import time
from abc import ABC, abstractmethod
from typing import cast

from dlt.common.configuration.exceptions import ConfigFieldMissingException
from dlt.common.pipeline import LoadInfo
from dlt.extract.exceptions import ResourceExtractionError
from dlt.pipeline.exceptions import PipelineStepFailed
from dlt.pipeline.pipeline import Pipeline

from openhound.core.exceptions import ConfigException, ParseException
from openhound.core.recovery import can_retry_normalization, resume_pending_pipeline

logger = logging.getLogger(__name__)

# Windows can transiently lock freshly written load-package files, so dlt's
# per-file rename fallback raises PermissionError (WinError 5); retry the run.
_TRANSIENT_RENAME_ERRNOS = {errno.EACCES, errno.EPERM}
_MAX_TRANSIENT_RETRIES = 5
_TRANSIENT_RETRY_BACKOFF = 0.25


def _transient_filesystem_cause(err: BaseException) -> OSError | None:
    """Return the underlying transient filesystem error in the cause chain, if any."""
    seen: set[int] = set()
    current: BaseException | None = err
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in _TRANSIENT_RENAME_ERRNOS:
            return current
        nested = getattr(current, "exception", None)
        if nested is None:
            nested = current.__cause__ or current.__context__
        current = nested
    return None


class BasePipeline(ABC):
    _retry_normalization_worker_failure: bool = False

    @property
    @abstractmethod
    def pipeline(self) -> "Pipeline": ...

    def _run(self, source, **kwargs) -> LoadInfo:
        last_err: BaseException | None = None
        for attempt in range(_MAX_TRANSIENT_RETRIES):
            try:
                return self.pipeline.run(source, **kwargs)
            except PipelineStepFailed as err:
                if self._retry_normalization_worker_failure and can_retry_normalization(
                    err
                ):
                    logger.warning(
                        "Normalization worker failed pipeline=%s step=%s package=%s; "
                        "attempting one fallback with normalize_workers=1",
                        err.pipeline.pipeline_name,
                        err.step,
                        err.load_id,
                    )
                    # Resume the failed instance, not a new pipeline/source run.
                    # Exceptions from this single fallback propagate to the caller.
                    return resume_pending_pipeline(
                        cast(Pipeline, err.pipeline), normalize_workers=1
                    )

                if isinstance(err.exception, ConfigFieldMissingException):
                    config_cause: ConfigFieldMissingException = err.exception
                    raise ConfigException(
                        pipeline_name=err.pipeline.pipeline_name,
                        destination=str(err.pipeline.destination),
                        dataset_name=err.pipeline.dataset_name,
                        spec_name=config_cause.spec_name,
                        message=str(err.exception),
                    ) from None

                if isinstance(err.exception, ResourceExtractionError):
                    extract_cause: ResourceExtractionError = err.exception
                    raise ParseException(
                        pipeline_name=err.pipeline.pipeline_name,
                        destination=str(err.pipeline.destination),
                        dataset_name=err.pipeline.dataset_name,
                        step=extract_cause.pipe_name,
                        message=extract_cause.msg,
                    ) from None

                if sys.platform != "win32" or _transient_filesystem_cause(err) is None:
                    raise
                last_err = err
            except PermissionError as err:
                if sys.platform != "win32" or err.errno not in _TRANSIENT_RENAME_ERRNOS:
                    raise
                last_err = err

            if attempt + 1 >= _MAX_TRANSIENT_RETRIES:
                break

            logger.warning(
                "Transient filesystem error; retrying run (%d/%d): %s",
                attempt + 1,
                _MAX_TRANSIENT_RETRIES,
                last_err,
            )
            time.sleep(_TRANSIENT_RETRY_BACKOFF * (attempt + 1))

        if last_err is None:
            raise RuntimeError("unreachable: retry loop exited without an error")
        raise last_err
