"""Validate selected packages with the embedded interpreter, without API access."""

import json
import sys
from importlib import metadata
from pathlib import Path

from openhound.scheduler.instance import InstancePaths, configure_instance


def main():
    payload = Path(sys.argv[1])
    selected = set(filter(None, sys.argv[2].split(",")))
    configure_instance(InstancePaths(Path(sys.argv[3]).resolve()))

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    from openhound.core.manager import CollectorManager

    catalog = json.loads((payload / "extensions.json").read_text())
    assert selected <= {extension["id"] for extension in catalog}, selected
    expected = {
        extension["entrypoint"] for extension in catalog if extension["id"] in selected
    }
    entrypoints = metadata.entry_points(group="openhound.sources")
    assert {entrypoint.name for entrypoint in entrypoints} == expected
    collectors = CollectorManager.from_entrypoint().collectors
    assert {collector.name for collector in collectors} == expected
    assert all(collector.metadata is not None for collector in collectors)
    for extension in catalog:
        package = extension["package"]
        if extension["id"] in selected:
            assert metadata.version(package) == extension["version"]
            matches = [ep for ep in entrypoints if ep.name == extension["entrypoint"]]
            assert len(matches) == 1
            assert canonicalize_name(matches[0].dist.name) == canonicalize_name(package)
        else:
            try:
                metadata.distribution(package)
            except metadata.PackageNotFoundError:
                pass
            else:
                raise AssertionError(f"Unselected extension is installed: {package}")
    # Verify that the shared dependency set satisfies each installed distribution.
    for distribution in metadata.distributions():
        for dependency in distribution.requires or []:
            requirement = Requirement(dependency)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            version = metadata.version(requirement.name)
            assert requirement.specifier.contains(
                version, prereleases=True
            ), f"{distribution.name} requires {requirement}; installed {version}"
    print(
        f"Installed extensions and dependencies verified: {', '.join(sorted(selected)) or '(none)'}"
    )


if __name__ == "__main__":
    main()
