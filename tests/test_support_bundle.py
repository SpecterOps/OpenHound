import zipfile
from pathlib import Path

from openhound.core.support_bundle import collect_log_files, create_support_bundle


def test_collect_log_files_includes_current_and_rotated_platform_and_extension_logs(
    tmp_path: Path,
):
    expected = [
        tmp_path / "openhound.log",
        tmp_path / "openhound.log.2026-05-28_10",
        tmp_path / "ext_faker.log",
    ]
    for path in expected:
        path.write_text("log")
    (tmp_path / "unrelated.txt").write_text("ignore")

    assert set(collect_log_files(tmp_path)) == set(expected)


def test_create_support_bundle_contains_collected_logs(tmp_path: Path):
    log = tmp_path / "openhound.log"
    log.write_text("log")

    bundle = create_support_bundle("openhound-faker", tmp_path)
    try:
        assert bundle.name.startswith("openhound-faker_support_bundle_")
        with zipfile.ZipFile(bundle) as archive:
            assert archive.namelist() == [log.name]
    finally:
        bundle.unlink(missing_ok=True)
