"""Regression tests for table-wide edge batching in the opengraph source.

The edge accumulator must span the whole table (across DLT chunks and input
files), flush one final partial batch, and preserve the flattened edge sequence
exactly (order + duplicates, no dedup).
"""

import gzip
import json
import math
from dataclasses import dataclass, field

import pytest
from dlt.extract.exceptions import ResourceExtractionError

from openhound.core.asset import BaseAsset
from openhound.core.models.entries_dataclass import (
    Edge,
    EdgePath,
)
from openhound.core.models.entries_dataclass import (
    Node as DNode,
)
from openhound.core.models.entries_dataclass import (
    NodeProperties as DNodeProperties,
)
from openhound.sources.opengraph.source import GraphResource, opengraph

# read_jsonl yields chunks of this many rows; table-wide batching spans them.
CHUNK = 1000


def _edge(idx, k=0):
    return Edge(
        kind="TEST_Edge",
        start=EdgePath(match_by="id", value=f"s{idx}-{k}"),
        end=EdgePath(match_by="id", value=f"e{idx}-{k}"),
    )


class OneEdgeAsset(BaseAsset):
    idx: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        return [_edge(self.idx)]


class MultiEdgeAsset(BaseAsset):
    idx: int
    n: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        return [_edge(self.idx, k) for k in range(self.n)]


@dataclass
class _TestNode(DNode):
    id: str = field(default="")

    def __post_init__(self):
        self.id = f"node-{self.properties.name}"


class NodeAndEdgeAsset(BaseAsset):
    idx: int

    @property
    def as_node(self):
        return _TestNode(
            kinds=["TestKind"],
            properties=DNodeProperties(
                name=f"n{self.idx}", displayname="d", environmentid="env"
            ),
        )

    @property
    def edges(self):
        return [_edge(self.idx)]


class EmptyAsset(BaseAsset):
    idx: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        return []


class FailAtAsset(BaseAsset):
    # Emits one edge per row until idx == extras["fail_at"], where it raises.
    # Used to assert accumulator state is not leaked into a re-extraction.
    idx: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        if self.idx == self._extras.get("fail_at"):
            raise RuntimeError("boom")
        return [_edge(self.idx)]


def _write_gz(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _collect(bucket, table, model, batch_size=150):
    source = opengraph(
        [GraphResource(table=table, model=model)],
        bucket_url=bucket.as_uri(),
        lookup=None,
        extras={},
        batch_size=batch_size,
    )
    return list(source.resources[f"{model.__name__.lower()}_fs"])


def _source(bucket, resources, batch_size=150):
    return opengraph(
        [GraphResource(table=t, model=m) for t, m in resources],
        bucket_url=bucket.as_uri(),
        lookup=None,
        extras={},
        batch_size=batch_size,
    )


def _split(items):
    nodes = [i for i in items if i["graph"]["entity_type"] == "node"]
    edges = [i for i in items if i["graph"]["entity_type"] == "edge"]
    flat = [e for i in edges for e in i["graph"]["content"]]
    return nodes, edges, flat


def _one_edge_rows(bucket, table, count, files=1):
    per = math.ceil(count / files)
    written = 0
    for f in range(files):
        rows = [{"idx": i} for i in range(written, min(written + per, count))]
        written += len(rows)
        _write_gz(bucket / table / f"part{f}.jsonl.gz", rows)


def test_batching_spans_rows_within_chunk(tmp_path):
    # 150 one-edge rows fit in a single chunk -> one wrapper, not 150.
    _one_edge_rows(tmp_path, "oneedgeasset", 150)
    _, edges, flat = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset))
    assert len(edges) == 1
    assert len(edges[0]["graph"]["content"]) == 150
    assert len(flat) == 150


def test_batching_crosses_1000_row_chunk_boundary(tmp_path):
    # 1001 rows cross the read_jsonl chunk boundary (1000 + 1). Table-wide, the
    # accumulator spans chunks -> exactly ceil(1001/150) == 7 wrappers, and the
    # final remainder is the only partial wrapper.
    _one_edge_rows(tmp_path, "oneedgeasset", 1001)
    _, edges, flat = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset))
    expected = math.ceil(1001 / 150)
    assert len(edges) == expected == 7
    assert [len(e["graph"]["content"]) for e in edges] == [150] * 6 + [101]
    assert len(flat) == 1001


def test_batching_across_multiple_input_files(tmp_path):
    # The whole table is one stream, so batches span input files too.
    _one_edge_rows(tmp_path, "oneedgeasset", 400, files=2)
    _, edges, flat = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset))
    assert len(edges) == math.ceil(400 / 150) == 3
    assert [len(e["graph"]["content"]) for e in edges] == [150, 150, 100]
    assert len(flat) == 400


def test_batching_across_files_and_chunks(tmp_path):
    # Multiple files each spanning several chunks still yield one table-wide
    # count with a single trailing partial wrapper.
    _one_edge_rows(tmp_path, "oneedgeasset", 1500, files=3)
    _, edges, flat = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset))
    assert len(edges) == math.ceil(1500 / 150) == 10
    assert all(len(e["graph"]["content"]) == 150 for e in edges)
    assert len(flat) == 1500


def test_flattened_sequence_preserves_order_and_duplicates(tmp_path):
    _one_edge_rows(tmp_path, "oneedgeasset", 20)
    _, _, flat = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset, batch_size=7))
    expected = [_edge(i) for i in range(20)]
    got = [(e["kind"], e["start"]["value"], e["end"]["value"]) for e in flat]
    want = [(e.kind, e.start.value, e.end.value) for e in expected]
    assert got == want  # order preserved


def test_duplicate_edges_are_not_deduplicated(tmp_path):
    # Two rows emitting identical edges must both survive.
    _write_gz(tmp_path / "oneedgeasset" / "p.jsonl.gz", [{"idx": 5}, {"idx": 5}])
    _, edges, flat = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset))
    assert len(flat) == 2
    assert flat[0] == flat[1]
    assert len(edges) == 1  # both batched into one wrapper


def test_batch_size_one_preserved(tmp_path):
    _one_edge_rows(tmp_path, "oneedgeasset", 5)
    _, edges, flat = _split(
        _collect(tmp_path, "oneedgeasset", OneEdgeAsset, batch_size=1)
    )
    assert len(edges) == 5
    assert all(len(e["graph"]["content"]) == 1 for e in edges)
    assert len(flat) == 5


@pytest.mark.parametrize("bad", [0, -1, -150])
def test_batch_size_below_one_rejected(tmp_path, bad):
    _one_edge_rows(tmp_path, "oneedgeasset", 1)
    with pytest.raises(ValueError):
        _collect(tmp_path, "oneedgeasset", OneEdgeAsset, batch_size=bad)


def test_exact_batch_size_single_row(tmp_path):
    # One row emitting exactly batch_size edges -> exactly one full wrapper.
    _write_gz(tmp_path / "multiedgeasset" / "p.jsonl.gz", [{"idx": 0, "n": 150}])
    _, edges, flat = _split(_collect(tmp_path, "multiedgeasset", MultiEdgeAsset))
    assert len(edges) == 1
    assert len(edges[0]["graph"]["content"]) == 150
    assert len(flat) == 150


def test_batch_size_plus_one_single_row(tmp_path):
    # batch_size + 1 edges -> a full wrapper plus a one-edge remainder.
    _write_gz(tmp_path / "multiedgeasset" / "p.jsonl.gz", [{"idx": 0, "n": 151}])
    _, edges, flat = _split(_collect(tmp_path, "multiedgeasset", MultiEdgeAsset))
    assert [len(e["graph"]["content"]) for e in edges] == [150, 1]
    assert len(flat) == 151


def test_nodes_unchanged_and_never_mixed_with_edges(tmp_path):
    _one_edge_rows(tmp_path, "nodeandedgeasset", 300)
    nodes, edges, flat = _split(
        _collect(tmp_path, "nodeandedgeasset", NodeAndEdgeAsset)
    )
    # One node wrapper per row, node content unchanged (single dict, not a list).
    assert len(nodes) == 300
    assert all(isinstance(n["graph"]["content"], dict) for n in nodes)
    # Edges are still batched across rows and never mixed into a node wrapper.
    assert len(edges) == math.ceil(300 / 150) == 2
    assert all(isinstance(e["graph"]["content"], list) for e in edges)
    assert len(flat) == 300


def test_empty_input_file_yields_nothing(tmp_path):
    _write_gz(tmp_path / "oneedgeasset" / "p.jsonl.gz", [])
    assert _collect(tmp_path, "oneedgeasset", OneEdgeAsset) == []


def test_zero_output_rows_yield_nothing(tmp_path):
    _one_edge_rows(tmp_path, "emptyasset", 10)
    assert _collect(tmp_path, "emptyasset", EmptyAsset) == []


def test_minimal_wrapper_count_matches_ceiling(tmp_path):
    # Table-wide batching is strictly minimal: ceil(N / batch_size) wrappers for
    # a range of counts and batch sizes, crossing chunk and file boundaries.
    for count, batch, files in [(999, 150, 1), (1001, 150, 1), (2345, 200, 4)]:
        _one_edge_rows(tmp_path, "oneedgeasset", count, files=files)
        _, edges, flat = _split(
            _collect(tmp_path, "oneedgeasset", OneEdgeAsset, batch_size=batch)
        )
        assert len(edges) == math.ceil(count / batch)
        assert len(flat) == count
        for f in range(files):
            (tmp_path / "oneedgeasset" / f"part{f}.jsonl.gz").unlink()


def test_flattened_sequence_matches_per_row_baseline(tmp_path):
    # Parity with the pre-fix per-row wrapping: identical flattened edge sequence
    # (order + duplicates), just regrouped into fewer wrappers. Crosses chunks.
    _one_edge_rows(tmp_path, "oneedgeasset", 1500)
    _, batched, flat_after = _split(_collect(tmp_path, "oneedgeasset", OneEdgeAsset))
    _, per_row, flat_before = _split(
        _collect(tmp_path, "oneedgeasset", OneEdgeAsset, batch_size=1)
    )

    def key(e):
        return (e["kind"], e["start"]["value"], e["end"]["value"])

    assert [key(e) for e in flat_after] == [key(e) for e in flat_before]
    assert len(per_row) == 1500
    assert len(batched) == math.ceil(1500 / 150) == 10


def test_multiple_graph_resources_isolated(tmp_path):
    # Two resources with different row counts each batch table-wide with their
    # own accumulator; neither leaks edges into the other.
    _one_edge_rows(tmp_path, "oneedgeasset", 200)
    _write_gz(tmp_path / "multiedgeasset" / "p.jsonl.gz", [{"idx": 0, "n": 151}])
    source = _source(
        tmp_path,
        [("oneedgeasset", OneEdgeAsset), ("multiedgeasset", MultiEdgeAsset)],
    )

    _, one_edges, one_flat = _split(list(source.resources["oneedgeasset_fs"]))
    _, multi_edges, multi_flat = _split(list(source.resources["multiedgeasset_fs"]))

    assert len(one_edges) == math.ceil(200 / 150) == 2
    assert [len(e["graph"]["content"]) for e in one_edges] == [150, 50]
    assert len(one_flat) == 200
    assert [len(e["graph"]["content"]) for e in multi_edges] == [150, 1]
    assert len(multi_flat) == 151


def test_failure_does_not_leak_accumulator_into_retry(tmp_path):
    # A failure mid-stream aborts extraction; re-iterating builds a fresh
    # generator with a fresh accumulator, so the successful retry has no
    # duplicated or leaked pending edges.
    _one_edge_rows(tmp_path, "failatasset", 300)

    def build(fail_at):
        source = opengraph(
            [GraphResource(table="failatasset", model=FailAtAsset)],
            bucket_url=tmp_path.as_uri(),
            lookup=None,
            extras={"fail_at": fail_at},
            batch_size=150,
        )
        return source.resources["failatasset_fs"]

    with pytest.raises(ResourceExtractionError):
        list(build(fail_at=170))

    _, edges, flat = _split(list(build(fail_at=-1)))
    assert len(edges) == math.ceil(300 / 150) == 2
    assert len(flat) == 300
