"""Resume retained DLT packages without extracting new source data."""

import logging
from concurrent.futures.process import BrokenProcessPool
from typing import cast

from dlt.common.configuration.container import Container
from dlt.common.configuration.providers.doc import CustomLoaderDocProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.common.pipeline import LoadInfo
from dlt.pipeline.exceptions import PipelineStepFailed
from dlt.pipeline.pipeline import Pipeline

logger = logging.getLogger(__name__)


def can_retry_normalization(error: PipelineStepFailed) -> bool:
    """Allow fallback only when the failed normalization package still exists."""
    return (
        error.step == "normalize"
        and isinstance(error.exception, BrokenProcessPool)
        and error.load_id
        in cast(Pipeline, error.pipeline).list_extracted_load_packages()
    )


def resume_pending_pipeline(
    pipeline: Pipeline, *, normalize_workers: int = 1
) -> LoadInfo:
    """Normalize and load saved packages using the pipeline's original destination.

    The caller must exclusively own the pipeline and its active job. This does
    not attach a pipeline, extract data, change destinations, or retry failures.
    """
    if normalize_workers < 1:
        raise ValueError("normalize_workers must be at least one")

    extracted = pipeline.list_extracted_load_packages()
    normalized = pipeline.list_normalized_load_packages()
    if not extracted and not normalized:
        raise ValueError("Pipeline has no pending packages to resume")

    logger.warning(
        "Resuming saved pipeline=%s extracted_packages=%s normalized_packages=%s "
        "normalize_workers=%s working_dir=%s; no new data will be extracted",
        pipeline.pipeline_name,
        extracted,
        normalized,
        normalize_workers,
        pipeline.working_dir,
    )
    try:
        if extracted:
            # In DLT 1.26, configured workers override normalize(workers=...).
            # Override only this pipeline's normalization, then restore providers
            # before loading so credentials and destination settings are untouched.
            document = {
                pipeline.pipeline_name: {"normalize": {"workers": normalize_workers}}
            }
            provider = CustomLoaderDocProvider(
                "Normalization recovery", lambda: document, supports_secrets=False
            )
            providers = Container()[PluggableRunContext].providers
            previous = providers.providers
            providers.providers = [provider, *previous]
            try:
                pipeline.normalize(workers=normalize_workers)
            finally:
                providers.providers = previous
        result = pipeline.load()
        result.raise_on_failed_jobs()
    except Exception:
        logger.error(
            "Pipeline recovery failed pipeline=%s working_dir=%s; retained DLT "
            "state and pending packages have not been explicitly deleted. "
            "Preserve this directory before starting another job. "
            "Automatic recovery will not be attempted again for this failure",
            pipeline.pipeline_name,
            pipeline.working_dir,
        )
        raise

    logger.info("Pipeline recovery completed pipeline=%s", pipeline.pipeline_name)
    return result
