"""Offline final-image smoke check; optionally pass the expected package version."""

import json
import platform
import sys
from importlib.metadata import entry_points, version
from pathlib import Path

from packaging.version import Version

import openhound
from openhound.core.clients.bloodhound import BloodHoundJWT
from openhound.core.manager import CollectorManager


def main() -> None:
    package_version = version("openhound")
    assert package_version == str(Version(package_version)), package_version
    if len(sys.argv) > 1:
        assert package_version == str(Version(sys.argv[1])), package_version
    assert openhound.__version__ == package_version
    assert Path(openhound.__file__).resolve() == Path("/app/src/openhound/__init__.py")
    entries = entry_points(group="openhound.sources", name="aws")
    assert len(entries) == 1, entries
    entry = next(iter(entries))
    assert entry.value == "openhound_aws.main:app", entry
    app = entry.load()
    manager = CollectorManager.from_entrypoint()
    assert any(collector is app for collector in manager.collectors)
    assert app.metadata is not None

    from openhound_aws import native_policy_evaluator, source

    assert native_policy_evaluator.NativeEvaluator is not None
    assert source is not None
    user_agent = BloodHoundJWT(token="offline-smoke-test").user_agent
    print(
        json.dumps(
            {
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "openhound": package_version,
                "openhound_module": openhound.__file__,
                "openhound_aws": version("openhound-aws"),
                "boto3": version("boto3"),
                "entry_point": f"{entry.name} = {entry.value}",
                "manager_loaded_aws": True,
                "native_evaluator": native_policy_evaluator.__file__,
                "bloodhound_user_agent": user_agent,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
