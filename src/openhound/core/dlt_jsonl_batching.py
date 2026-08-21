"""Correct jsonl batching for dlt callable-destination load jobs.

dlt 1.26.x re-yields accumulated items per load-file line (duplicated data)
unless buffer_max_items divides evenly by batch size. This module installs
dlt's later corrected semantics: full batches yielded with reset, one
trailing partial per file.
"""

import logging
from collections.abc import Iterable
from typing import Any

import dlt
from dlt.common import json
from dlt.common.storages import FileStorage
from dlt.common.typing import TDataItems

logger = logging.getLogger(__name__)

_AFFECTED_DLT_SERIES = (1, 26)
_INSTALLED_ATTR = "_openhound_jsonl_batching_installed"


def _jsonl_get_batches(self: Any, start_index: int) -> Iterable[TDataItems]:
    current_batch: TDataItems = []

    with FileStorage.open_zipsafe_ro(self._file_path) as f:
        for line in f:
            encoded_json = json.typed_loads(line)
            if isinstance(encoded_json, dict):
                encoded_json = [encoded_json]

            for item in encoded_json:
                if start_index > 0:
                    start_index -= 1
                    continue
                for column in self._skipped_columns:
                    item.pop(column, None)
                current_batch.append(item)
                if len(current_batch) == self._config.batch_size:
                    yield current_batch
                    current_batch = []

    if current_batch:
        yield current_batch


def ensure_dlt_jsonl_batching() -> bool:
    """Install correct get_batches on affected dlt versions."""
    if getattr(dlt, _INSTALLED_ATTR, False):
        return True

    from dlt.destinations.job_impl import DestinationJsonlLoadJob

    version = tuple(int(part) for part in dlt.__version__.split(".")[:2])
    if not hasattr(DestinationJsonlLoadJob, "get_batches") or version != _AFFECTED_DLT_SERIES:
        logger.debug("dlt %s needs no jsonl batching correction", dlt.__version__)
        return False

    DestinationJsonlLoadJob.get_batches = _jsonl_get_batches  # type: ignore[method-assign]
    setattr(dlt, _INSTALLED_ATTR, True)
    logger.info(
        "Installed OpenHound jsonl batching for dlt %s DestinationJsonlLoadJob "
        "(upstream yielded cumulative partial batches across load-file lines)",
        dlt.__version__,
    )
    return True
