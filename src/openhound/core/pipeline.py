import errno
import logging
import sys
import time
from abc import ABC, abstractmethod

from dlt.common.configuration.exceptions import ConfigFieldMissingException
from dlt.common.pipeline import LoadInfo
from dlt.extract.exceptions import ResourceExtractionError
from dlt.pipeline.exceptions import PipelineStepFailed
from dlt.pipeline.pipeline import Pipeline

from openhound.core.exceptions import ConfigException, ParseException

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
    @property
    @abstractmethod
    def pipeline(self) -> "Pipeline": ...

    def _run(self, source, **kwargs) -> LoadInfo:
        last_err: BaseException | None = None
        for attempt in range(_MAX_TRANSIENT_RETRIES):
            try:
                return self.pipeline.run(source, **kwargs)
            except PipelineStepFailed as err:
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
                    )

                if _transient_filesystem_cause(err) is None:
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
