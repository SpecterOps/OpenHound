import gzip
import json as stdlib_json
import os
import subprocess
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

import dlt
import pytest
from dlt.common import json
from dlt.common.storages.file_storage import FileStorage

from openhound.core.asset import BaseAsset
from openhound.core.models.entries_dataclass import Edge, EdgePath
from openhound.destinations.opengraph import destination as file_destination
from openhound.sources.opengraph.source import GraphResource, opengraph


class _RawEdgeAsset(BaseAsset):
    row: int

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        yield Edge(
            kind="IntegrationEdge",
            start=EdgePath(match_by="id", value=f"start-{self.row}"),
            end=EdgePath(match_by="id", value=f"end-{self.row}"),
        )


class _SecondRawEdgeAsset(_RawEdgeAsset):
    @property
    def edges(self):
        yield Edge(
            kind="SecondIntegrationEdge",
            start=EdgePath(match_by="id", value=f"second-start-{self.row}"),
            end=EdgePath(match_by="id", value=f"second-end-{self.row}"),
        )


class _VariableEdgeAsset(BaseAsset):
    row: int
    edge_count: int = 1

    @property
    def as_node(self):
        return None

    @property
    def edges(self):
        for edge in range(self.edge_count):
            yield Edge(
                kind="VariableIntegrationEdge",
                start=EdgePath(
                    match_by="id", value=f"variable-start-{self.row}-{edge}"
                ),
                end=EdgePath(match_by="id", value=f"variable-end-{self.row}-{edge}"),
            )


def _write_jsonl(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_opengraph_dlt_batches_across_rows_and_flushes_per_input_file(
    monkeypatch, tmp_path
):
    """Exercise the DLT filesystem reader, not just the batching helper.

    The first file crosses DLT's 1,000-row read_jsonl page boundary and the
    second is a separate input file.  With a page-scoped batch size of 150,
    this must produce ceil(1000 / 150) + ceil(1 / 150) == 8 edge wrappers.
    """
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    raw_dir = tmp_path / "raw" / "assets"
    raw_dir.mkdir(parents=True)
    _write_jsonl(raw_dir / "one.jsonl.gz", ({"row": row} for row in range(1_000)))
    _write_jsonl(raw_dir / "two.jsonl.gz", [{"row": 1_000}])

    captured = []

    @dlt.destination(skip_dlt_columns_and_tables=True, batch_size=0)
    def capture_normalized_file(items: str, table):
        with FileStorage.open_zipsafe_ro(items) as fh:
            for line in fh:
                decoded = json.typed_loads(line)
                captured.extend([decoded] if isinstance(decoded, dict) else decoded)

    pipeline = dlt.pipeline(
        pipeline_name="opengraph_dlt_page_boundary",
        dataset_name="opengraph_dlt_page_boundary",
        destination=capture_normalized_file(),
        pipelines_dir=str(tmp_path / "pipelines"),
    )
    pipeline.run(
        opengraph(
            [GraphResource(table="assets", model=_RawEdgeAsset)],
            bucket_url=str(tmp_path / "raw"),
            lookup=None,
            batch_size=150,
        )
    )

    edge_wrappers = [
        item for item in captured if item["graph"]["entity_type"] == "edge"
    ]
    flattened = [
        edge for wrapper in edge_wrappers for edge in wrapper["graph"]["content"]
    ]
    assert sorted(len(wrapper["graph"]["content"]) for wrapper in edge_wrappers) == [
        1,
        100,
        150,
        150,
        150,
        150,
        150,
        150,
    ]
    assert sorted(
        int(edge["start"]["value"].removeprefix("start-")) for edge in flattened
    ) == list(range(1_001))


def test_opengraph_dlt_keeps_multiple_graph_resources_isolated(monkeypatch, tmp_path):
    """Each GraphResource receives a fresh table-specific accumulator."""
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    raw_root = tmp_path / "raw"
    for table_name, rows in {"first": range(3), "second": range(10, 13)}.items():
        table_dir = raw_root / table_name
        table_dir.mkdir(parents=True)
        _write_jsonl(table_dir / "rows.jsonl.gz", ({"row": row} for row in rows))

    captured: defaultdict[str, list[dict]] = defaultdict(list)

    @dlt.destination(skip_dlt_columns_and_tables=True, batch_size=0)
    def capture_normalized_file(items: str, table):
        with FileStorage.open_zipsafe_ro(items) as fh:
            for line in fh:
                decoded = json.typed_loads(line)
                captured[table["name"]].extend(
                    [decoded] if isinstance(decoded, dict) else decoded
                )

    pipeline = dlt.pipeline(
        pipeline_name="opengraph_dlt_multiple_resources",
        dataset_name="opengraph_dlt_multiple_resources",
        destination=capture_normalized_file(),
        pipelines_dir=str(tmp_path / "pipelines"),
    )
    pipeline.run(
        opengraph(
            [
                GraphResource(table="first", model=_RawEdgeAsset),
                GraphResource(table="second", model=_SecondRawEdgeAsset),
            ],
            bucket_url=str(raw_root),
            lookup=None,
            batch_size=2,
        )
    )

    first = captured["_rawedgeasset_fs"]
    second = captured["_secondrawedgeasset_fs"]
    assert [len(item["graph"]["content"]) for item in first] == [2, 1]
    assert [len(item["graph"]["content"]) for item in second] == [2, 1]
    assert [
        edge["start"]["value"] for item in first for edge in item["graph"]["content"]
    ] == ["start-0", "start-1", "start-2"]
    assert [
        edge["start"]["value"] for item in second for edge in item["graph"]["content"]
    ] == ["second-start-10", "second-start-11", "second-start-12"]


@pytest.mark.parametrize(
    ("rows", "batch_size", "expected_lengths", "expected_starts"),
    [
        (None, 3, [], []),
        (
            [{"row": 0, "edge_count": 0}, {"row": 1, "edge_count": 0}],
            3,
            [],
            [],
        ),
        (
            [{"row": 0}, {"row": 1}, {"row": 2}],
            3,
            [3],
            ["variable-start-0-0", "variable-start-1-0", "variable-start-2-0"],
        ),
        (
            [{"row": 0}, {"row": 1}, {"row": 2}, {"row": 3}],
            3,
            [3, 1],
            [
                "variable-start-0-0",
                "variable-start-1-0",
                "variable-start-2-0",
                "variable-start-3-0",
            ],
        ),
        (
            [
                {"row": 0, "edge_count": 2},
                {"row": 1, "edge_count": 2},
                {"row": 2, "edge_count": 1},
            ],
            3,
            [3, 2],
            [
                "variable-start-0-0",
                "variable-start-0-1",
                "variable-start-1-0",
                "variable-start-1-1",
                "variable-start-2-0",
            ],
        ),
    ],
)
def test_opengraph_dlt_boundary_cases(
    monkeypatch, tmp_path, rows, batch_size, expected_lengths, expected_starts
):
    """Exercise boundary cases through the DLT filesystem and load pipeline."""
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    raw_dir = tmp_path / "raw" / "assets"
    raw_dir.mkdir(parents=True)
    if rows is not None:
        _write_jsonl(raw_dir / "rows.jsonl.gz", rows)

    captured: list[dict] = []

    @dlt.destination(skip_dlt_columns_and_tables=True, batch_size=0)
    def capture_normalized_file(items: str, table):
        if table["name"] != "_variableedgeasset_fs":
            return
        with FileStorage.open_zipsafe_ro(items) as fh:
            for line in fh:
                decoded = json.typed_loads(line)
                captured.extend([decoded] if isinstance(decoded, dict) else decoded)

    pipeline = dlt.pipeline(
        pipeline_name="opengraph_dlt_boundary_cases",
        dataset_name="opengraph_dlt_boundary_cases",
        destination=capture_normalized_file(),
        pipelines_dir=str(tmp_path / "pipelines"),
    )
    pipeline.run(
        opengraph(
            [GraphResource(table="assets", model=_VariableEdgeAsset)],
            bucket_url=str(tmp_path / "raw"),
            lookup=None,
            batch_size=batch_size,
        )
    )

    edge_wrappers = [
        item for item in captured if item["graph"]["entity_type"] == "edge"
    ]
    assert [
        len(wrapper["graph"]["content"]) for wrapper in edge_wrappers
    ] == expected_lengths
    assert [
        edge["start"]["value"]
        for wrapper in edge_wrappers
        for edge in wrapper["graph"]["content"]
    ] == expected_starts


def test_opengraph_file_destination_retries_staged_load_without_duplicates(
    monkeypatch, tmp_path
):
    """A failed destination publish must resume without source re-extraction.

    The first load attempt writes all destination parts to the job staging area,
    then fails before publishing them. DLT retries the pending load job from
    the normalized package. The retry must publish one complete file, not lose
    pending edges or create a second delivery.
    """
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    raw_dir = tmp_path / "raw" / "assets"
    raw_dir.mkdir(parents=True)
    _write_jsonl(raw_dir / "assets.jsonl.gz", ({"row": row} for row in range(1_001)))

    output_dir = tmp_path / "graph"
    original_publish = file_destination._publish_parts
    fail_once = True

    def fail_after_staging(staging, committed, output_path):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("simulated destination publish failure")
        original_publish(staging, committed, output_path)

    monkeypatch.setattr(file_destination, "_publish_parts", fail_after_staging)
    pipeline = dlt.pipeline(
        pipeline_name="opengraph_dlt_restart",
        dataset_name="opengraph_dlt_restart",
        destination=file_destination.opengraph_file(output_path=str(output_dir)),
        pipelines_dir=str(tmp_path / "pipelines"),
    )
    source = opengraph(
        [GraphResource(table="assets", model=_RawEdgeAsset)],
        bucket_url=str(tmp_path / "raw"),
        lookup=None,
        batch_size=150,
    )

    pipeline.run(source)

    assert fail_once is False

    published_parts = list(output_dir.glob("_rawedgeasset_fs-*.json"))
    assert len(published_parts) == 1
    document = stdlib_json.loads(published_parts[0].read_text(encoding="utf-8"))
    assert len(document["graph"]["edges"]) == 1_001
    assert {edge["start"]["value"] for edge in document["graph"]["edges"]} == {
        f"start-{row}" for row in range(1_001)
    }


def test_opengraph_file_destination_cold_restart_resumes_pending_job(tmp_path):
    """A new Python process resumes a failed staged destination load exactly once."""
    raw_dir = tmp_path / "raw" / "assets"
    raw_dir.mkdir(parents=True)
    _write_jsonl(raw_dir / "assets.jsonl.gz", ({"row": row} for row in range(5)))

    runner = tmp_path / "cold_restart_runner.py"
    runner.write_text(
        textwrap.dedent(
            """
            import os
            import sys

            import dlt
            from openhound.core.asset import BaseAsset
            from openhound.core.models.entries_dataclass import Edge, EdgePath
            from openhound.destinations.opengraph import destination as file_destination
            from openhound.sources.opengraph.source import GraphResource, opengraph


            class Asset(BaseAsset):
                row: int

                @property
                def as_node(self):
                    return None

                @property
                def edges(self):
                    yield Edge(
                        kind="ColdRestartEdge",
                        start=EdgePath(match_by="id", value=f"start-{self.row}"),
                        end=EdgePath(match_by="id", value=f"end-{self.row}"),
                    )


            def main():
                if os.environ.get("FAIL_DESTINATION_PUBLISH"):
                    def fail_publish(staging, committed, output_path):
                        raise RuntimeError("simulated persistent publish failure")

                    file_destination._publish_parts = fail_publish

                pipeline = dlt.pipeline(
                    pipeline_name="opengraph_cold_restart",
                    dataset_name="opengraph_cold_restart",
                    destination=file_destination.opengraph_file(
                        output_path=os.environ["GRAPH_OUTPUT"]
                    ),
                    pipelines_dir=os.environ["PIPELINES_DIR"],
                )
                if sys.argv[1] == "initial":
                    pipeline.run(
                        opengraph(
                            [GraphResource(table="assets", model=Asset)],
                            bucket_url=os.environ["RAW_ROOT"],
                            lookup=None,
                            batch_size=3,
                        )
                    )
                else:
                    pipeline.run()


            if __name__ == "__main__":
                main()
            """
        ),
        encoding="utf-8",
    )
    project_root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "DLT_DATA_DIR": str(tmp_path / ".dlt"),
        "GRAPH_OUTPUT": str(tmp_path / "graph"),
        "PIPELINES_DIR": str(tmp_path / "pipelines"),
        "RAW_ROOT": str(tmp_path / "raw"),
        "PYTHONPATH": str(project_root / "src"),
    }
    failed = subprocess.run(
        [sys.executable, str(runner), "initial"],
        cwd=project_root,
        env={**environment, "FAIL_DESTINATION_PUBLISH": "1"},
        capture_output=True,
        check=False,
        text=True,
    )
    assert failed.returncode != 0, failed.stderr

    resumed = subprocess.run(
        [sys.executable, str(runner), "resume"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    assert resumed.returncode == 0, resumed.stderr

    published_parts = list((tmp_path / "graph").glob("asset_fs-*.json"))
    assert len(published_parts) == 1
    document = stdlib_json.loads(published_parts[0].read_text(encoding="utf-8"))
    assert [edge["start"]["value"] for edge in document["graph"]["edges"]] == [
        f"start-{row}" for row in range(5)
    ]
