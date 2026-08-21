"""Tests for OpenHound's dlt jsonl batching module."""

import json as jsonlib
from types import SimpleNamespace

from dlt.destinations.job_impl import DestinationJsonlLoadJob

import openhound  # noqa: F401  ensures batching is installed process-wide
from openhound.core.dlt_jsonl_batching import ensure_dlt_jsonl_batching


def _make_job(tmp_path, lines, batch_size, skipped_columns=()):
    path = tmp_path / "load.jsonl"
    path.write_text("\n".join(jsonlib.dumps(line) for line in lines) + "\n", encoding="utf-8")
    job = DestinationJsonlLoadJob.__new__(DestinationJsonlLoadJob)
    job._file_path = str(path)
    job._config = SimpleNamespace(batch_size=batch_size)
    job._skipped_columns = list(skipped_columns)
    return job


def _collect(job, start_index=0):
    return [list(batch) for batch in job.get_batches(start_index)]


def test_batching_is_installed_for_pinned_dlt():
    assert ensure_dlt_jsonl_batching() is True


def test_multi_line_files_deliver_each_item_exactly_once(tmp_path):
    lines = [list(range(offset, offset + 10)) for offset in range(0, 40, 10)]
    job = _make_job(tmp_path, lines, batch_size=7)

    batches = _collect(job)

    delivered = [item for batch in batches for item in batch]
    expected = list(range(40))
    assert delivered == expected
    assert [len(batch) for batch in batches] == [7, 7, 7, 7, 7, 5]


def test_start_index_resumes_mid_file(tmp_path):
    lines = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    job = _make_job(tmp_path, lines, batch_size=2)

    batches = _collect(job, start_index=4)

    delivered = [item for batch in batches for item in batch]
    assert delivered == [5, 6, 7, 8, 9]
    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_skipped_columns_are_removed(tmp_path):
    records = [
        [{"_dlt_id": "a", "value": 1}, {"_dlt_id": "b", "value": 2}],
        [{"_dlt_id": "c", "value": 3}],
    ]
    job = _make_job(tmp_path, records, batch_size=10, skipped_columns=("_dlt_id",))

    batches = _collect(job)

    delivered = [item for batch in batches for item in batch]
    assert delivered == [{"value": 1}, {"value": 2}, {"value": 3}]


def test_single_dict_lines_are_wrapped(tmp_path):
    job = _make_job(tmp_path, [{"n": 1}, {"n": 2}], batch_size=1)

    batches = _collect(job)

    delivered = [item for batch in batches for item in batch]
    assert delivered == [{"n": 1}, {"n": 2}]
    assert len(batches) == 2
