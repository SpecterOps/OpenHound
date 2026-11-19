"""Render the real Helm chart to verify optional saved-run storage."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deployments/helm/openhound"
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")
pytestmark = pytest.mark.skipif(HELM is None, reason="Helm is required for chart tests")


@pytest.fixture
def render(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text(
        yaml.safe_dump(
            {
                "collector": {"name": "example"},
                "config": {"existingConfigMap": "openhound-config"},
            }
        )
    )

    def run(values=None, *, succeeds=True):
        overrides = tmp_path / "overrides.yaml"
        overrides.write_text(yaml.safe_dump(values or {}))
        result = subprocess.run(
            [
                HELM,
                "template",
                "review",
                str(CHART),
                "--kube-version",
                "1.33.0",
                "-f",
                str(base),
                "-f",
                str(overrides),
            ],
            env={
                **os.environ,
                "HELM_CACHE_HOME": str(tmp_path / "cache"),
                "HELM_CONFIG_HOME": str(tmp_path / "config"),
                "HELM_DATA_HOME": str(tmp_path / "data"),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if not succeeds:
            assert result.returncode != 0
            return result.stderr
        assert result.returncode == 0, result.stderr
        documents = list(yaml.safe_load_all(result.stdout))
        deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
        return deployment, documents

    return run


def _persistence(**overrides):
    return {"enabled": True, "existingClaim": "openhound-runs", **overrides}


def _container(deployment):
    return deployment["spec"]["template"]["spec"]["containers"][0]


def test_persistence_is_opt_in(render):
    deployment, documents = render()
    assert "strategy" not in deployment["spec"]
    pod = deployment["spec"]["template"]["spec"]
    assert all("persistentVolumeClaim" not in item for item in pod["volumes"])
    assert all(
        item["name"] != "saved-runs" for item in _container(deployment)["volumeMounts"]
    )
    assert all(
        item["name"] != "OPENHOUND_DEFAULT_RUNS_DIRECTORY"
        for item in _container(deployment)["env"]
    )
    assert all(doc["kind"] != "PersistentVolumeClaim" for doc in documents)


def test_existing_claim_mount_survives_rollout_configuration(render):
    deployment, documents = render({"persistence": _persistence()})
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {
        "type": "Recreate",
        "rollingUpdate": None,
    }
    assert pod["securityContext"]["fsGroup"] == 65532
    assert {
        "name": "saved-runs",
        "persistentVolumeClaim": {"claimName": "openhound-runs"},
    } in pod["volumes"]
    assert {
        "name": "saved-runs",
        "mountPath": "/var/lib/openhound/runs",
    } in _container(deployment)["volumeMounts"]
    assert all(doc["kind"] != "PersistentVolumeClaim" for doc in documents)


def test_custom_mount_sets_only_the_image_fallback(render):
    deployment, _ = render({"persistence": _persistence(mountPath="/data/runs")})
    container = _container(deployment)
    assert {"name": "saved-runs", "mountPath": "/data/runs"} in container[
        "volumeMounts"
    ]
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["OPENHOUND_DEFAULT_RUNS_DIRECTORY"] == "/data/runs"
    assert "OPENHOUND__RUNS_DIRECTORY" not in env


@pytest.mark.parametrize("scope", ["env", "collector"])
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OPENHOUND_DEFAULT_RUNS_DIRECTORY", "/configured/runs"),
        ("OPENHOUND_DEFAULT_RUNS_DIRECTORY", ""),
        ("OPENHOUND__RUNS_DIRECTORY", "/explicit/runs"),
        ("OPENHOUND__RUNS_DIRECTORY", ""),
    ],
)
def test_explicit_environment_settings_are_preserved(render, scope, name, value):
    options = {"persistence": _persistence()}
    options[scope] = {name: value} if scope == "env" else {"env": {name: value}}
    deployment, _ = render(options)
    entries = [item for item in _container(deployment)["env"] if item["name"] == name]
    assert entries == [{"name": name, "value": value}]


def test_saved_runs_coexist_with_config_and_secret_mounts(render):
    deployment, _ = render(
        {
            "persistence": _persistence(),
            "config": {"existingSecret": "openhound-secrets"},
            "collector": {
                "extraSecretMounts": {
                    "credentials": {
                        "secretName": "collector-key",
                        "secretKey": "key.pem",
                        "mountPath": "/app/.dlt/key.pem",
                    }
                }
            },
        }
    )
    container = _container(deployment)
    mounts = {item["name"]: item for item in container["volumeMounts"]}
    assert set(mounts) == {
        "saved-runs",
        "dlt-config",
        "dlt-secrets",
        "dlt-extra-secret-credentials",
    }
    assert "readOnly" not in mounts["saved-runs"]
    assert all(
        mount["readOnly"] for name, mount in mounts.items() if name != "saved-runs"
    )
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    assert {item["name"] for item in volumes} == set(mounts)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"enabled": True}, "persistence.existingClaim is required"),
        (
            _persistence(mountPath="relative/runs"),
            "persistence.mountPath must be an absolute",
        ),
        (_persistence(mountPath="/"), "persistence.mountPath must be an absolute"),
        (_persistence(mountPath="//"), "persistence.mountPath must be an absolute"),
        (
            _persistence(mountPath="/data/.."),
            "persistence.mountPath must be an absolute",
        ),
        (_persistence(mountPath=""), "persistence.mountPath is required"),
        (_persistence(mountPath=None), "persistence.mountPath is required"),
    ],
)
def test_invalid_persistence_configuration_fails_before_deployment(
    render, options, message
):
    error = render({"persistence": options}, succeeds=False)
    assert message in error


def test_disabled_persistence_ignores_unused_storage_options(render):
    deployment, _ = render({"persistence": {"enabled": False, "mountPath": ""}})
    assert "strategy" not in deployment["spec"]


def test_missing_persistence_values_remain_backwards_compatible(render):
    deployment, _ = render({"persistence": None})
    assert "strategy" not in deployment["spec"]
    pod = deployment["spec"]["template"]["spec"]
    assert all("persistentVolumeClaim" not in item for item in pod["volumes"])


@pytest.mark.parametrize("scope", ["env", "collector"])
def test_persistence_allows_empty_environment_mappings(render, scope):
    options = {"persistence": _persistence()}
    options[scope] = None if scope == "env" else {"env": None}
    deployment, _ = render(options)
    assert {
        "name": "OPENHOUND_DEFAULT_RUNS_DIRECTORY",
        "value": "/var/lib/openhound/runs",
    } in _container(deployment)["env"]
