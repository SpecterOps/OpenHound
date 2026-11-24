import logging
from importlib.metadata import version
from pathlib import Path

import dlt
from dlt.common.pipeline import LoadInfo

import openhound
from openhound.core.app import OpenHound
from openhound.core.convert import Method
from openhound.core.progress import Progress
from openhound.core.recovery import resume_pending_pipeline
from openhound.core.runs import Phase, SavedRun
from openhound.destinations.opengraph.destination import opengraph_file

logger = logging.getLogger(__name__)


def extracted_resources(load_info: LoadInfo) -> list[str]:
    # DLT can return normally with terminal failures when deployment settings
    # disable raise_on_failed_jobs. Never checkpoint an incomplete phase.
    load_info.raise_on_failed_jobs()
    all_resources = []
    for package in load_info.load_packages:
        for job in package.jobs["completed_jobs"]:
            table = job.job_file_info.table_name
            all_resources.append(table)

    return all_resources


# TODO: Evaluate using hamilton instead of hardcoding the dataflow. Hamilton should allow easier management for dataflows.
def collect(extension: OpenHound, run: SavedRun | None = None) -> list[str]:
    load_info = extension.collector(
        output_path=run.raw if run else Path("/tmp/openhound"),  # type: ignore
        resources=[],  # type: ignore
        progress=Progress.log,  # type: ignore
    )
    return extracted_resources(load_info)


def preprocess(
    extension: OpenHound, run: SavedRun | None = None, *, recovery: bool = False
) -> list[str]:
    output_file = Path("lookup.duckdb")
    if run:
        output_file = (
            run.directory / "recovery" / "lookup.duckdb" if recovery else run.lookup
        )
    load_info = extension.preprocessor(
        input_path=(run.raw if run else Path("/tmp/openhound")) / extension.name,
        output_file=output_file,
        progress=Progress.log,  # type: ignore
    )
    return extracted_resources(load_info)


def convert(
    extension: OpenHound,
    output_path: Path | None = None,
    *,
    run: SavedRun | None = None,
    recovery: bool = False,
) -> list[str]:
    options = {}
    if output_path is not None:
        options["output_path"] = output_path
    if run:
        options["lookup_file"] = (
            run.directory / "recovery" / "lookup.duckdb"
            if recovery and "preprocess" not in run.manifest.results
            else run.lookup
        )
    load_info = extension.converter(
        input_path=(run.raw if run else Path("/tmp/openhound")) / extension.name,
        method=Method.write if output_path is not None else Method.ingest,  # type: ignore
        progress=Progress.log,  # type: ignore
        **options,
    )
    return extracted_resources(load_info)


def pipeline(
    extension: OpenHound,
    output_path: Path | None = None,
    *,
    saved_run: SavedRun | None = None,
) -> dict[str, list[str]]:
    if saved_run is not None:
        return _saved_pipeline(extension, saved_run, output_path)
    collect_result = collect(extension)

    preprocessor_result = []
    if extension.preprocessor is not None:
        preprocessor_result = preprocess(extension)

    convert_result = (
        convert(extension) if output_path is None else convert(extension, output_path)
    )
    return {
        "collect": collect_result,
        "preprocess": preprocessor_result,
        "convert": convert_result,
    }


def _saved_pipeline(
    extension: OpenHound, run: SavedRun, output_path: Path | None
) -> dict[str, list[str]]:
    """Checkpoint each original phase and retain its workspace on failure."""
    with run.locked(), run.configuration():
        if run.manifest.status != "not_started":
            raise ValueError("A saved run cannot be collected again; use runs resume")
        try:
            phases: tuple[Phase, ...] = ("collect", "preprocess", "convert")
            for phase in phases:
                run.manifest.status = "running"
                run.manifest.phase = phase
                run.save()
                if phase == "collect":
                    resources = collect(extension, run)
                elif phase == "preprocess":
                    resources = (
                        preprocess(extension, run) if extension.preprocessor else []
                    )
                else:
                    resources = convert(extension, output_path, run=run)
                run.manifest.results[phase] = resources
                run.save()
                if phase == "collect":
                    try:
                        run.write_collection_inventory()
                    except OSError as error:
                        logger.warning(
                            "Could not write collection inventory for saved run %s "
                            "(%s); continuing without it",
                            run.directory,
                            type(error).__name__,
                        )
        except Exception as error:
            run.manifest.status = "failed"
            run.manifest.error_type = type(error).__name__
            run.save()
            logger.error("Saved run retained at %s", run.directory)
            raise
        run.manifest.status = "complete"
        run.save()
        return {
            str(phase): resources for phase, resources in run.manifest.results.items()
        }


def resume_saved_run(
    extension: OpenHound, run: SavedRun, *, normalize_workers: int = 1
) -> Path:
    """Recover to local graph files, preserving original ingest/job state.

    Ingest packages cannot be redirected to file output. A separate recovery
    pipeline rebuilds graph extraction from saved resources on its first use;
    subsequent attempts reuse its own retained DLT packages when available.
    """
    if normalize_workers < 1:
        raise ValueError("normalize_workers must be at least one")
    with run.locked():
        manifest = run.manifest
        if extension.name != manifest.collector:
            raise ValueError("Collector does not match the saved run")
        if (
            manifest.openhound_version != openhound.__version__
            or manifest.dlt_version != version("dlt")
            or manifest.collector_version != getattr(extension, "package_version", None)
        ):
            raise ValueError(
                "Use the same OpenHound, DLT, and collector versions as the saved run"
            )
        if "collect" not in manifest.results:
            raise ValueError(
                "Collection did not complete; recovery will not repeat API collection"
            )
        if not (run.raw / extension.name).is_dir():
            raise ValueError("Saved collection directory is missing")
        output = run.directory / "recovery" / "graph"
        if manifest.recovery_status == "complete":
            if not output.is_dir():
                raise ValueError(
                    "Completed recovery output is missing; restore the saved workspace"
                )
            return output
        manifest.recovery_requested_workers = normalize_workers
        manifest.recovery_error_type = None
        with run.configuration(recovery=True, normalize_workers=normalize_workers):
            try:
                if extension.preprocessor and "preprocess" not in manifest.results:
                    if "preprocess" not in manifest.recovery_results:
                        manifest.recovery_status = "running"
                        manifest.recovery_phase = "preprocess"
                        run.save()
                        manifest.recovery_results["preprocess"] = preprocess(
                            extension, run, recovery=True
                        )
                        run.save()
                elif extension.preprocessor and not run.lookup.is_file():
                    raise ValueError("Saved lookup database is missing")
                manifest.recovery_status = "running"
                manifest.recovery_phase = "convert"
                run.save()
                saved_pipeline = dlt.pipeline(
                    pipeline_name=f"{extension.name}_convert",
                    dataset_name=extension.name,
                    destination=opengraph_file(output_path=str(output)),
                    progress=Progress.log.value,
                )
                if saved_pipeline.has_pending_data:
                    manifest.recovery_results["convert"] = extracted_resources(
                        resume_pending_pipeline(
                            saved_pipeline, normalize_workers=normalize_workers
                        )
                    )
                elif saved_pipeline.list_completed_load_packages():
                    # The process can die after DLT completes but before run.json
                    # is updated. DLT's package state takes precedence.
                    resources: list[str] = []
                    for load_id in saved_pipeline.list_completed_load_packages():
                        package = saved_pipeline.get_load_package_info(load_id)
                        if package.jobs["failed_jobs"]:
                            raise ValueError(
                                "Recovery has terminal failed load jobs; inspect its DLT packages"
                            )
                        resources.extend(
                            job.job_file_info.table_name
                            for job in package.jobs["completed_jobs"]
                        )
                    if not output.is_dir():
                        raise ValueError("Completed recovery output is missing")
                    manifest.recovery_results["convert"] = resources
                else:
                    manifest.recovery_results["convert"] = convert(
                        extension, output, run=run, recovery=True
                    )
            except Exception as error:
                manifest.recovery_status = "failed"
                manifest.recovery_error_type = type(error).__name__
                run.save()
                raise
        manifest.recovery_status = "complete"
        run.save()
        return output
