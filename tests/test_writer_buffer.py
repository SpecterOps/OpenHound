"""Tests for BED-9372 writer-buffer coordination."""

import inspect
import math

import pytest

from openhound.core.convert import Converter, Method
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
