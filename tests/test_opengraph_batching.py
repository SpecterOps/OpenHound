import pytest
from pydantic import computed_field

from openhound.core.asset import BaseAsset
from openhound.core.models.entries import Node, NodeProperties
from openhound.core.models.entries_dataclass import Edge, EdgePath
from openhound.sources.opengraph.source import _generate_graph_content, opengraph


class _Node(Node):
    value: int

    @classmethod
    def guid(cls, name: str, node_type: str, *args: str) -> str:
        return name

    @computed_field
    @property
    def id(self) -> str:
        return f"node-{self.value}"


class _Asset(BaseAsset):
    value: int
    edge_count: int = 1
    has_node: bool = False

    @property
    def as_node(self):
        if not self.has_node:
            return None
        return _Node(
            value=self.value,
            kinds=["Test"],
            properties=NodeProperties(
                name=str(self.value),
                displayname=str(self.value),
                environmentid="test",
            ),
        )

    @property
    def edges(self):
        return [
            Edge(
                kind="TestEdge",
                start=EdgePath(match_by="id", value=f"start-{self.value}-{index}"),
                end=EdgePath(match_by="id", value=f"end-{self.value}-{index}"),
            )
            for index in range(self.edge_count)
        ]


def _rows(count: int):
    return [{"value": index} for index in range(count)]


def _edges(content):
    return [
        edge
        for item in content
        if item["graph"]["entity_type"] == "edge"
        for edge in item["graph"]["content"]
    ]


def test_batches_edges_across_successive_rows_and_preserves_order():
    content = list(_generate_graph_content(_rows(1_001), _Asset, 150))
    wrappers = [item for item in content if item["graph"]["entity_type"] == "edge"]

    assert len(wrappers) == 7
    assert [len(wrapper["graph"]["content"]) for wrapper in wrappers] == [150] * 6 + [101]
    assert [edge["start"]["value"] for edge in _edges(content)] == [
        f"start-{index}-0" for index in range(1_001)
    ]


def test_page_boundary_and_file_boundary_have_explicit_count_semantics():
    # DLT calls the transformer once per read_jsonl page; 1,001 rows are 1,000 + 1.
    content = [
        *list(_generate_graph_content(_rows(1_000), _Asset, 150)),
        *list(_generate_graph_content(_rows(1_001)[1_000:], _Asset, 150)),
    ]
    wrappers = [item for item in content if item["graph"]["entity_type"] == "edge"]

    assert len(wrappers) == 8  # ceil(1000 / 150) + ceil(1 / 150)
    assert len(_edges(content)) == 1_001


def test_page_accumulators_do_not_leak_between_extraction_attempts():
    first_attempt = list(_generate_graph_content(_rows(1), _Asset, 3))
    retry_attempt = list(_generate_graph_content(_rows(1), _Asset, 3))

    assert _edges(first_attempt) == _edges(retry_attempt)
    assert len(first_attempt) == len(retry_attempt) == 1


def test_batch_size_one_preserves_one_wrapper_per_edge():
    content = list(_generate_graph_content(_rows(3), _Asset, 1))
    wrappers = [item for item in content if item["graph"]["entity_type"] == "edge"]

    assert [len(wrapper["graph"]["content"]) for wrapper in wrappers] == [1, 1, 1]


def test_mixed_assets_keep_nodes_separate_and_preserve_duplicate_edges():
    rows = [
        {"value": 0, "edge_count": 0, "has_node": True},
        {"value": 1, "edge_count": 2},
        {"value": 1, "edge_count": 2},
        {"value": 2, "edge_count": 1, "has_node": True},
    ]
    content = list(_generate_graph_content(rows, _Asset, 3))

    nodes = [item for item in content if item["graph"]["entity_type"] == "node"]
    wrappers = [item for item in content if item["graph"]["entity_type"] == "edge"]
    assert len(nodes) == 2
    assert all(not isinstance(item["graph"]["content"], list) for item in nodes)
    assert [len(item["graph"]["content"]) for item in wrappers] == [3, 2]
    assert [edge["start"]["value"] for edge in _edges(content)] == [
        "start-1-0",
        "start-1-1",
        "start-1-0",
        "start-1-1",
        "start-2-0",
    ]


@pytest.mark.parametrize("batch_size", [0, -1])
def test_invalid_batch_sizes_are_rejected(batch_size):
    with pytest.raises(ValueError, match="greater than zero"):
        opengraph([], "unused", lookup=None, batch_size=batch_size)
