import gzip

from dlt.common import json

from openhound.destinations.opengraph.destination import (
    DEST_PART,
    _load_items,
    _write_part,
)


def _item(value: int):
    return {
        "graph": {
            "entity_type": "edge",
            "content": [{"kind": "Test", "start": value, "end": value + 1}],
        }
    }


def test_load_file_streaming_reads_each_non_aligned_jsonl_item_once(tmp_path):
    load_file = tmp_path / "test.jsonl.gz"
    expected = [_item(index) for index in range(5)]
    with gzip.open(load_file, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(expected[:2]) + "\n")
        fh.write(json.dumps(expected[2:]) + "\n")

    assert list(_load_items(str(load_file))) == expected


def test_write_part_flattens_only_the_items_provided(tmp_path):
    DEST_PART.clear()
    _write_part([_item(1), _item(2)], "test_fs", str(tmp_path))

    document = json.loads((tmp_path / "test_fs-1.json").read_text(encoding="utf-8"))
    assert document["graph"]["nodes"] == []
    assert document["graph"]["edges"] == [
        {"kind": "Test", "start": 1, "end": 2},
        {"kind": "Test", "start": 2, "end": 3},
    ]
    assert "metadata" not in document
