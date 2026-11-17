"""Manual upload archives contain complete, validated recovery graph parts only."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import dlt
import pytest
from filelock import FileLock, Timeout
from typer.testing import CliRunner

import openhound.core.upload_package as packaging
from openhound.cli.runs import runs
from openhound.core.runs import SavedRun
from openhound.core.upload_package import package_saved_run
from openhound.destinations.opengraph.destination import opengraph_file


@pytest.fixture
def recovered_run(tmp_path):
    """Build the file destination's real graph and commit-marker layout."""
    run = SavedRun.create(
        tmp_path / "runs",
        collector="okta",
        collector_version="1",
        job_id=42,
        mode="ingest",
    )
    graph = run.directory / "recovery" / "graph"
    commits = graph / ".openhound-commits" / "load-1"
    commits.mkdir(parents=True)
    documents = {
        "nodes-0001.json": {
            "graph": {
                "nodes": [
                    {
                        "id": "node-1",
                        "kinds": ["Okta_User"],
                        "properties": {
                            "name": "Alice",
                            "displayname": "Alice",
                            "environmentid": "tenant-1",
                            "custom": ["preserve", "me"],
                        },
                    }
                ],
                "edges": [],
            }
        },
        "edges-0001.json": {
            "graph": {
                "nodes": [],
                "edges": [
                    {
                        "kind": "MemberOf",
                        "start": {"match_by": "id", "value": "node-1"},
                        "end": {"match_by": "id", "value": "group-1"},
                        "properties": {},
                    }
                ],
            }
        },
    }
    for name, document in documents.items():
        (graph / name).write_text(json.dumps(document), encoding="utf-8")
    (commits / "job.json").write_text(json.dumps({"parts": list(documents)}))
    with run.locked():
        run.manifest.recovery_status = "complete"
        run.save()
    return run


def test_package_preserves_graph_bytes_and_excludes_unrelated_files(
    recovered_run, tmp_path
):
    run = recovered_run
    graph = run.directory / "recovery" / "graph"
    (graph / "uncommitted.json").write_text("do not include")
    (graph / ".openhound-staging").mkdir()
    (graph / ".openhound-staging" / "partial.json").write_text("do not include")
    (run.directory / "secrets.toml").write_text("secret = 'do not include'")
    before = (run.directory / "run.json").read_bytes()
    archive, receipt = package_saved_run(run, tmp_path / "okta.zip")
    manifest = json.loads(receipt.read_text())
    assert manifest["run_id"] == run.manifest.run_id
    assert manifest["job_id"] == 42
    assert manifest["collector"] == "okta"
    assert manifest["node_count"] == 1
    assert manifest["edge_count"] == 1
    assert (
        manifest["package_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    assert archive.stat().st_mode & 0o777 == 0o600
    assert receipt.stat().st_mode & 0o777 == 0o600
    with ZipFile(archive) as bundle:
        assert bundle.namelist() == ["edges-0001.json", "nodes-0001.json"]
        assert bundle.testzip() is None
        for entry in manifest["files"]:
            content = bundle.read(entry["name"])
            assert content == (graph / entry["name"]).read_bytes()
            assert entry["sha256"] == hashlib.sha256(content).hexdigest()
            assert entry["size_bytes"] == len(content)
    assert (run.directory / "run.json").read_bytes() == before


@pytest.mark.parametrize("status", ["not_started", "running", "failed"])
def test_package_refuses_incomplete_recovery(recovered_run, tmp_path, status):
    with recovered_run.locked():
        recovered_run.manifest.recovery_status = status
        recovered_run.save()
    with pytest.raises(ValueError, match="Recovery must complete"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")


def test_package_refuses_missing_committed_file(recovered_run, tmp_path):
    (recovered_run.directory / "recovery/graph/nodes-0001.json").unlink()
    with pytest.raises(ValueError, match="Missing or non-regular"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")
    assert not (tmp_path / "okta.zip").exists()


@pytest.mark.parametrize(
    "content", ["not json", '{"graph":{"nodes":[{}]}}', '{"graph":{"edges":[{}]}}']
)
def test_invalid_graph_does_not_publish_partial_package(
    recovered_run, tmp_path, content
):
    (recovered_run.directory / "recovery/graph/nodes-0001.json").write_text(content)
    with pytest.raises(ValueError, match="Invalid OpenGraph"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")
    assert not (tmp_path / "okta.zip").exists()
    assert not (tmp_path / "okta.zip.manifest.json").exists()
    assert not list(tmp_path.glob(".openhound-package-*"))


@pytest.mark.parametrize(
    "parts",
    [
        ["../run.json"],
        ["nested/file.json"],
        ["..\\run.json"],
        [".hidden.json"],
        [1],
        ["nodes-0001.json", "nodes-0001.json"],
    ],
)
def test_invalid_commit_inventory_is_rejected(recovered_run, tmp_path, parts):
    marker = (
        recovered_run.directory / "recovery/graph/.openhound-commits/load-1/job.json"
    )
    marker.write_text(json.dumps({"parts": parts}))
    with pytest.raises(ValueError, match="Invalid or duplicate"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")


def test_package_refuses_symlinked_graph_part(recovered_run, tmp_path):
    part = recovered_run.directory / "recovery/graph/nodes-0001.json"
    outside = tmp_path / "outside.json"
    part.rename(outside)
    part.symlink_to(outside)
    with pytest.raises(ValueError, match="Missing or non-regular"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")


@pytest.mark.parametrize("existing", ["okta.zip", "okta.zip.manifest.json"])
def test_existing_outputs_are_preserved(recovered_run, tmp_path, existing):
    target = tmp_path / existing
    target.write_bytes(b"existing user file")
    with pytest.raises(FileExistsError):
        package_saved_run(recovered_run, tmp_path / "okta.zip")
    assert target.read_bytes() == b"existing user file"


def test_failed_archive_publication_removes_new_sidecar(
    recovered_run, tmp_path, monkeypatch
):
    link = packaging.os.link

    def fail_archive(source, target):
        if Path(target).suffix == ".zip":
            raise OSError("simulated publication failure")
        link(source, target)

    monkeypatch.setattr(packaging.os, "link", fail_archive)
    with pytest.raises(OSError, match="publication failure"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")
    assert not (tmp_path / "okta.zip").exists()
    assert not (tmp_path / "okta.zip.manifest.json").exists()


def test_packaging_respects_run_lock(recovered_run, tmp_path):
    with FileLock(recovered_run.directory / ".lock", timeout=0), pytest.raises(Timeout):
        package_saved_run(recovered_run, tmp_path / "okta.zip")


def test_cli_package_creates_upload_zip_without_loading_collectors(
    recovered_run, tmp_path, monkeypatch
):
    def unexpected(*args, **kwargs):
        raise AssertionError("Packaging must not load a collector or run recovery")

    monkeypatch.setattr(
        "openhound.cli.runs.CollectorManager.from_entrypoint", unexpected
    )
    monkeypatch.setattr("openhound.cli.runs.resume_saved_run", unexpected)
    output = tmp_path / "okta.zip"
    result = CliRunner().invoke(
        runs, ["package", str(recovered_run.directory), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert "No data was uploaded" in result.output
    assert "Manifest (keep locally)" in result.output


def test_cli_package_refuses_locked_run(recovered_run, tmp_path):
    with FileLock(recovered_run.directory / ".lock", timeout=0):
        result = CliRunner().invoke(
            runs,
            ["package", str(recovered_run.directory), "-o", str(tmp_path / "okta.zip")],
        )
    assert result.exit_code != 0
    assert "in use" in result.output


def test_package_accepts_actual_file_destination_output(tmp_path):
    run = SavedRun.create(
        tmp_path / "runs",
        collector="example",
        collector_version="1",
        job_id=42,
        mode="ingest",
    )
    output = run.directory / "recovery" / "graph"
    node = {
        "id": "node-1",
        "kinds": ["Example_User"],
        "properties": {
            "name": "Alice",
            "displayname": "Alice",
            "environmentid": "tenant-1",
        },
    }
    with run.locked(), run.configuration(recovery=True):
        output.mkdir(parents=True)
        pipeline = dlt.pipeline(
            pipeline_name="example_convert",
            dataset_name="example",
            destination=opengraph_file(output_path=str(output)),
        )
        resource = dlt.resource(
            [{"graph": {"entity_type": "node", "content": node}}],
            name="nodes",
            max_table_nesting=0,
        )
        pipeline.run(resource).raise_on_failed_jobs()
        run.manifest.recovery_status = "complete"
        run.save()

    archive, _ = package_saved_run(SavedRun(run.directory), tmp_path / "graph.zip")
    with ZipFile(archive) as bundle:
        assert len(bundle.namelist()) == 1
        document = json.loads(bundle.read(bundle.namelist()[0]))
    assert document["graph"]["nodes"] == [node]


def test_package_refuses_empty_graph(recovered_run, tmp_path):
    graph = recovered_run.directory / "recovery" / "graph"
    for name in ("nodes-0001.json", "edges-0001.json"):
        (graph / name).write_text('{"graph":{"nodes":[],"edges":[]}}')
    with pytest.raises(ValueError, match="no nodes or edges"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")
    assert not (tmp_path / "okta.zip").exists()


def test_package_requires_commit_markers(recovered_run, tmp_path):
    marker = (
        recovered_run.directory / "recovery/graph/.openhound-commits/load-1/job.json"
    )
    marker.unlink()
    with pytest.raises(ValueError, match="no committed graph files"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")


@pytest.mark.parametrize("content", ["invalid-json", "[]", '{"parts":"invalid"}'])
def test_package_refuses_invalid_marker(recovered_run, tmp_path, content):
    marker = (
        recovered_run.directory / "recovery/graph/.openhound-commits/load-1/job.json"
    )
    marker.write_text(content)
    with pytest.raises(ValueError, match="Invalid recovery commit marker"):
        package_saved_run(recovered_run, tmp_path / "okta.zip")


def test_package_refuses_output_inside_graph_directory(recovered_run):
    output = recovered_run.directory / "recovery/graph/package.zip"
    with pytest.raises(ValueError, match="outside the recovery graph directory"):
        package_saved_run(recovered_run, output)


def test_package_requires_zip_extension(recovered_run, tmp_path):
    with pytest.raises(ValueError, match=".zip extension"):
        package_saved_run(recovered_run, tmp_path / "package.json")
