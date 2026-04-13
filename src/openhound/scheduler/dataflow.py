from pathlib import Path

from dlt.common.pipeline import LoadInfo

from openhound.core.app import OpenHound
from openhound.core.convert import Method
from openhound.core.progress import Progress


def extracted_resources(load_info: LoadInfo) -> list[str]:
    all_resources = []
    for package in load_info.load_packages:
        for job in package.jobs["completed_jobs"]:
            table = job.job_file_info.table_name
            all_resources.append(table)

    return all_resources


# TODO: Evaluate using hamilton instead of hardcoding the dataflow. Hamilton should allow easier management for dataflows.
def collect(extension: OpenHound) -> list[str]:
    load_info = extension.collector(
        output_path=Path("/tmp/openhound"),  # type: ignore
        resources=[],  # type: ignore
        progress=Progress.log,  # type: ignore
    )
    return extracted_resources(load_info)


def preprocess(extension: OpenHound) -> list[str]:
    load_info = extension.preprocessor(
        input_path=Path("/tmp/openhound") / extension.name,
        output_file=Path("lookup.duckdb"),
        progress=Progress.log,  # type: ignore
    )
    return extracted_resources(load_info)


def convert(extension: OpenHound) -> list[str]:
    load_info = extension.converter(
        input_path=Path("/tmp/openhound") / extension.name,
        method=Method.ingest,  # type: ignore
        progress=Progress.log,  # type: ignore
    )
    return extracted_resources(load_info)


def pipeline(extension: OpenHound) -> dict[str, list[str]]:
    collect_result = collect(extension)

    preprocessor_result = []
    if extension.preprocessor is not None:
        preprocessor_result = preprocess(extension)

    convert_result = convert(extension)
    return {
        "collect": collect_result,
        "preprocess": preprocessor_result,
        "convert": convert_result,
    }
