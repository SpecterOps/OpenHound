from types import SimpleNamespace

import pytest

from openhound.scheduler import dataflow, instance


def test_windows_instance_default_uses_programdata(monkeypatch, tmp_path):
    monkeypatch.delenv(instance.INSTANCE_ENV, raising=False)
    monkeypatch.setattr(instance.sys, "platform", "win32")
    monkeypatch.setenv("ProgramData", str(tmp_path / "Program Data"))

    paths = instance.resolve_instance("customer-a")

    assert paths is not None
    assert paths.root == (
        tmp_path
        / "Program Data"
        / "SpecterOps"
        / "OpenHound"
        / "instances"
        / "customer-a"
    )


def test_instance_name_rejects_path_traversal():
    with pytest.raises(ValueError, match="Instance name"):
        instance.resolve_instance("../other")


def test_dataflow_passes_instance_paths_to_every_stage(monkeypatch, tmp_path):
    root = tmp_path / "Writable Instance"
    monkeypatch.setenv(instance.INSTANCE_ENV, str(root))
    calls = {}
    load = SimpleNamespace(load_packages=[])

    def collector(**kwargs):
        calls["collector"] = kwargs
        return load

    def preprocessor(**kwargs):
        calls["preprocessor"] = kwargs
        return load

    def converter(**kwargs):
        calls["converter"] = kwargs
        return load

    extension = SimpleNamespace(
        name="sample",
        collector=collector,
        preprocessor=preprocessor,
        converter=converter,
    )
    assert dataflow.pipeline(extension) == {
        "collect": [],
        "preprocess": [],
        "convert": [],
    }
    assert calls["collector"]["output_path"] == root / "output"
    assert calls["preprocessor"]["input_path"] == root / "output" / "sample"
    assert calls["preprocessor"]["output_file"] == root / "state" / "lookup.duckdb"
    assert calls["converter"]["input_path"] == root / "output" / "sample"
    assert calls["converter"]["lookup_file"] == root / "state" / "lookup.duckdb"


def test_invalid_bhe_url_reports_actionable_error(tmp_path):
    config = tmp_path / ".dlt"
    config.mkdir()
    (config / "config.toml").write_text(
        '[destination.bloodhoundenterprise]\nurl = "invalid"\ncollector_name = "github"\n'
    )
    (config / "secrets.toml").write_text(
        '[destination.bloodhoundenterprise]\ntoken_id = "example-id"\ntoken_key = "example-key"\n'
    )
    result = subprocess.run(
        [sys.executable, "-m", "openhound.scheduler", "--instance-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2
    assert "destination.bloodhoundenterprise.url" in result.stderr
    assert "http:// or https://" in result.stderr


import subprocess
import sys
