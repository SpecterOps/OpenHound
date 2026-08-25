import json

import dlt

import openhound.destinations.opengraph.destination as destination_module


def _item(index: int) -> dict:
    return {
        "graph": {
            "entity_type": "edge",
            "content": [
                {
                    "kind": "Validation",
                    "start": {"match_by": "id", "value": f"start-{index}"},
                    "end": {"match_by": "id", "value": f"end-{index}"},
                    "properties": {"sequence": index},
                }
            ],
        }
    }


def _edge_sequences(output_dir) -> list[int]:
    values = []
    for path in sorted(output_dir.glob("validation_fs-*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        values.extend(edge["properties"]["sequence"] for edge in document["graph"]["edges"])
    return values


def test_destination_retry_republishes_no_duplicate_parts(tmp_path, monkeypatch):
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    output_dir = tmp_path / "graph"
    output_dir.mkdir()
    items = [_item(index) for index in range(1_001)]

    @dlt.resource(name="validation_fs", max_table_nesting=0)
    def non_aligned_source():
        for start, end in ((0, 2), (2, 155), (155, 304), (304, 1_001)):
            yield items[start:end]

    original_write_part = destination_module._write_part
    write_attempts = 0

    def fail_after_first_part(*args, **kwargs):
        nonlocal write_attempts
        original_write_part(*args, **kwargs)
        write_attempts += 1
        if write_attempts == 1:
            raise RuntimeError("intentional destination failure")

    monkeypatch.setattr(destination_module, "_write_part", fail_after_first_part)
    pipeline = dlt.pipeline(
        pipeline_name="destination_retry_validation",
        dataset_name="destination_retry_validation",
        destination=destination_module.opengraph_file(
            output_path=str(output_dir), source_kind="test"
        ),
    )

    # DLT retries the transient destination job in this call. The destination
    # must discard its staged first attempt and publish one complete part set.
    pipeline.run(non_aligned_source())

    sequences = _edge_sequences(output_dir)
    assert sequences == list(range(1_001))
    assert write_attempts == 3
    assert not list((output_dir / ".openhound-staging").rglob("*.json"))
    assert len(list((output_dir / ".openhound-commits").rglob("*.json"))) == 1
