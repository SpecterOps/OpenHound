from dlt.common.exceptions import DltException


class BaseCLIException(DltException):
    pass


class ConfigException(DltException):
    def __init__(
        self,
        pipeline_name: str,
        destination: str,
        dataset_name: str,
        spec_name: str,
        message: str,
    ):
        self.pipeline_name = pipeline_name
        self.destination = destination
        self.dataset_name = dataset_name
        self.spec_name = spec_name
        self.message = message

        super().__init__(
            f"Pipeline {pipeline_name} raised a {spec_name} config validation error from dataset {dataset_name}. Reason:\n{message}"
        )


class ParseException(BaseCLIException):
    def __init__(
        self,
        pipeline_name: str,
        destination: str,
        dataset_name: str,
        step: str,
        message: str,
    ):
        self.pipeline_name = pipeline_name
        self.destination = destination
        self.dataset_name = dataset_name
        self.step = step
        self.message = message

        super().__init__(
            f"Pipeline {pipeline_name} raised a data validation error from dataset {dataset_name}. Exception raised at resource {step}. Reason:\n{message}"
        )
