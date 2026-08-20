"""Generic synthetic assets and input generation for the BED-9372 benchmark.

These assets are deliberately extension-agnostic (not Okta/SAML-specific) so the
benchmark exercises the shared opengraph source, per the ticket requirement to
use generic synthetic assets plus at least one non-Okta high-cardinality shape.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from openhound.core.asset import BaseAsset
from openhound.core.models.entries_dataclass import (
    Edge,
    EdgePath,
    Node as DNode,
    NodeProperties as DNodeProperties,
)


def _edge(idx: int, k: int = 0) -> Edge:
    return Edge(
        kind="BENCH_Relationship",
        start=EdgePath(match_by="id", value=f"start-{idx}-{k}"),
        end=EdgePath(match_by="id", value=f"end-{idx}-{k}"),
    )


class OneEdgeAsset(BaseAsset):
    """High-cardinality zero-or-one-edge shape (e.g. membership/grant rows)."""

    idx: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        return [_edge(self.idx)]


class MultiEdgeAsset(BaseAsset):
    """A row emitting several edges, to stress inner-relationship growth."""

    idx: int
    n: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        return [_edge(self.idx, k) for k in range(self.n)]


@dataclass
class _BenchNode(DNode):
    id: str = field(default="")

    def __post_init__(self):
        self.id = f"node-{self.properties.name}"


class NodeAndEdgeAsset(BaseAsset):
    """Node-bearing row that also emits one containment/ownership edge."""

    idx: int

    @property
    def as_node(self):
        return _BenchNode(
            kinds=["BENCH_Node"],
            properties=DNodeProperties(
                name=f"n{self.idx}",
                displayname=f"Node {self.idx}",
                environmentid="bench-env",
            ),
        )

    @property
    def edges(self):
        return [
            Edge(
                kind="BENCH_Relationship",
                start=EdgePath(match_by="id", value=f"node-n{self.idx}"),
                end=EdgePath(match_by="id", value=f"end-{self.idx}-0"),
            )
        ]


def _single_edge_row(idx: int, epr: int) -> dict:
    """Row builder for shapes that emit exactly one edge per row.

    These shapes cannot vary the edge count, so reject any edges_per_row other
    than 1 rather than silently discarding it.
    """
    if epr != 1:
        raise ValueError(
            f"edges_per_row={epr} is unsupported for single-edge shapes; use 1"
        )
    return {"idx": idx}


# Maps a shape name to (asset model, row builder). The row builder returns the
# raw dict that read_jsonl will feed back into the model.
ASSET_SHAPES: dict[str, tuple[type[BaseAsset], object]] = {
    "one_edge": (OneEdgeAsset, _single_edge_row),
    "multi_edge": (MultiEdgeAsset, lambda idx, epr: {"idx": idx, "n": epr}),
    "node_and_edge": (NodeAndEdgeAsset, _single_edge_row),
}


def model_for_shape(shape: str) -> type[BaseAsset]:
    return ASSET_SHAPES[shape][0]


def _write_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def write_synthetic_input(
    input_dir: Path, shape: str, rows: int, edges_per_row: int, files: int
) -> str:
    """Write `rows` synthetic rows for `shape` across `files` .jsonl.gz files.

    Returns the table (subdirectory) name used by the opengraph file_glob.
    """
    model, row_builder = ASSET_SHAPES[shape]
    table = model.__name__.lower()
    per_file = math.ceil(rows / files)
    written = 0
    for f in range(files):
        count = min(per_file, rows - written)
        if count <= 0:
            break
        batch = [row_builder(written + i, edges_per_row) for i in range(count)]
        written += count
        _write_gz(input_dir / table / f"part-{f:04d}.jsonl.gz", batch)
    return table
