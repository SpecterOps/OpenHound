import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from openhound.main import app

TEST_DATA_DIR = Path(__file__).parent / "test_data" / "extensions" / "saved_searches"


def test_saved_search_bundle_writes_json(tmp_path):
    output_path = tmp_path / "saved_searches.json"

    result = CliRunner().invoke(
        app,
        [
            "searches",
            "bundle",
            str(TEST_DATA_DIR),
            str(output_path),
            "--file-format",
            "json",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert output_path.suffix == ".json"

    saved_searches = json.loads(output_path.read_text())
    assert len(saved_searches) == 2


def test_saved_search_bundle_writes_zip(tmp_path):
    output_path = tmp_path / "saved_searches.zip"

    result = CliRunner().invoke(
        app,
        [
            "searches",
            "bundle",
            str(TEST_DATA_DIR),
            str(output_path),
            "--file-format",
            "json",
            "--output-format",
            "zip",
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert output_path.suffix == ".zip"

    with zipfile.ZipFile(output_path) as archive:
        archive_names = archive.namelist()
        assert len(archive_names) == 2
        assert all(name.endswith(".json") for name in archive_names)
        saved_searches = [
            json.loads(archive.read(name).decode()) for name in archive_names
        ]

    assert len(saved_searches) == 2
