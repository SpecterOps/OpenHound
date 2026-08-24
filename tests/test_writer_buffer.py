"""Tests for BED-9372 writer-buffer coordination."""

import inspect
import math

import dlt
import pytest

import openhound.core.dlt_jsonl_batching as dlt_batching
from openhound.core.convert import Converter, Method
from openhound.core.dlt_jsonl_batching import (
    _INSTALLED_ATTR,
    ensure_dlt_jsonl_batching,
    jsonl_duplication_defect_active,
)
from openhound.sources.opengraph.source import (
    DEFAULT_EDGE_BATCH_SIZE,
    DLT_BUFFERED_EDGE_BUDGET,
    DLT_DEFAULT_BUFFER_MAX_ITEMS,
    opengraph,
    writer_buffer_max_items,
)

ENV_VAR = "DATA_WRITER__BUFFER_MAX_ITEMS"


def test_source_signature_default_matches_constant():
    sig = inspect.signature(opengraph)
    assert sig.parameters["batch_size"].default == DEFAULT_EDGE_BATCH_SIZE


def test_buffer_formula_bounds_buffered_edges():
    items = writer_buffer_max_items(DEFAULT_EDGE_BATCH_SIZE)
    assert items == math.floor(DLT_BUFFERED_EDGE_BUDGET / DEFAULT_EDGE_BATCH_SIZE)
    assert items * DEFAULT_EDGE_BATCH_SIZE <= DLT_BUFFERED_EDGE_BUDGET

    assert writer_buffer_max_items(1) == DLT_DEFAULT_BUFFER_MAX_ITEMS
    assert writer_buffer_max_items(10**9) == 1
    for batch_size in (1, 2, 7, 149, 150, 151, 1_000, 100_000):
        assert (
            writer_buffer_max_items(batch_size) * batch_size
            <= DLT_BUFFERED_EDGE_BUDGET + batch_size
        )


def test_buffer_formula_rejects_invalid_batch_size():
    for bad in (0, -1):
        with pytest.raises(ValueError, match="batch_size must be >= 1"):
            writer_buffer_max_items(bad)


@pytest.mark.parametrize("version", ["1.26.0", "1.26.5"])
def test_buffer_falls_back_when_stock_defect_active(monkeypatch, caplog, version):
    # Simulate the correction failing to install while a defect-carrying
    # 1.26.x runs (e.g., dlt restructured job_impl so the patch bails out).
    monkeypatch.setattr(dlt, _INSTALLED_ATTR, False)
    monkeypatch.setattr(dlt, "__version__", version)
    monkeypatch.setattr(dlt_batching, "ensure_dlt_jsonl_batching", lambda: False)

    assert jsonl_duplication_defect_active() is True
    assert writer_buffer_max_items(DEFAULT_EDGE_BATCH_SIZE) == DLT_DEFAULT_BUFFER_MAX_ITEMS
    assert any(
        record.levelname == "WARNING" and "duplication defect" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("version", ["1.26.0", "1.26.5"])
def test_defect_not_active_once_correction_installed(monkeypatch, version):
    # Same series, successful install: tuned buffer stays safe.
    monkeypatch.setattr(dlt, _INSTALLED_ATTR, False)
    monkeypatch.setattr(dlt, "__version__", version)

    assert jsonl_duplication_defect_active() is False
    assert getattr(dlt, _INSTALLED_ATTR) is True


@pytest.mark.parametrize("version", ["1.25.3", "1.27.0", "2.0.0"])
def test_buffer_tuned_when_series_audited_good(monkeypatch, version):
    monkeypatch.setattr(dlt, _INSTALLED_ATTR, False)
    monkeypatch.setattr(dlt, "__version__", version)

    assert jsonl_duplication_defect_active() is False
    assert writer_buffer_max_items(DEFAULT_EDGE_BATCH_SIZE) == math.floor(
        DLT_BUFFERED_EDGE_BUDGET / DEFAULT_EDGE_BATCH_SIZE
    )


@pytest.mark.parametrize("version", ["1.26.0rc1", "1.26.9.dev1"])
def test_prerelease_126_versions_still_patch(monkeypatch, version):
    monkeypatch.setattr(dlt, _INSTALLED_ATTR, False)
    monkeypatch.setattr(dlt, "__version__", version)

    assert ensure_dlt_jsonl_batching() is True
    assert getattr(dlt, _INSTALLED_ATTR) is True
    assert writer_buffer_max_items(DEFAULT_EDGE_BATCH_SIZE) == math.floor(
        DLT_BUFFERED_EDGE_BUDGET / DEFAULT_EDGE_BATCH_SIZE
    )


def test_unparseable_version_degrades_without_crashing(monkeypatch):
    monkeypatch.setattr(dlt, _INSTALLED_ATTR, False)
    monkeypatch.setattr(dlt, "__version__", "not-a-version")

    assert ensure_dlt_jsonl_batching() is False
    # Not numerically identifiable as 1.26.x -> audited-good default applies.
    assert jsonl_duplication_defect_active() is False


@pytest.mark.parametrize("bad", [1.5, True])
def test_buffer_formula_rejects_non_integer_batch_size(bad):
    with pytest.raises(TypeError, match="batch_size must be an integer"):
        writer_buffer_max_items(bad)


def test_converter_applies_env_default(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    converter = Converter(
        name="t",
        input_path=None,
        lookup=None,
        output_path=None,
        source_kind="test",
        method=Method.write,
    )
    converter._coordinate_writer_buffer()
    import os

    assert os.environ[ENV_VAR] == str(writer_buffer_max_items())


def test_converter_respects_explicit_override(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "777")
    converter = Converter(
        name="t",
        input_path=None,
        lookup=None,
        output_path=None,
        source_kind="test",
        method=Method.write,
    )
    converter._coordinate_writer_buffer()
    import os

    assert os.environ[ENV_VAR] == "777"
