from abc import ABC, abstractmethod

from dlt.common.configuration.exceptions import ConfigFieldMissingException
from dlt.common.pipeline import LoadInfo
from dlt.extract.exceptions import ResourceExtractionError
from dlt.pipeline.exceptions import PipelineStepFailed
from dlt.pipeline.pipeline import Pipeline

from openhound.core.exceptions import ConfigException, ParseException


class BasePipeline(ABC):
    @property
    @abstractmethod
    def pipeline(self) -> "Pipeline": ...

    def _run(self, source, **kwargs) -> LoadInfo:
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
            raise
