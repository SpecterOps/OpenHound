"""Fetch pinned AWS source and patch only its temporary packaging metadata."""

import io
import re
import shutil
import sys
import tarfile
from importlib.metadata import version
from pathlib import Path
from urllib.request import Request, urlopen

import tomlkit
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


def patch_packaging(destination: Path, local_version: str) -> None:
    """Align AWS OpenHound and boto3 requirements with the frozen build environment."""
    metadata = destination / "pyproject.toml"
    document = tomlkit.parse(metadata.read_text())
    project = document.get("project", {})
    dependency_lists = [project.get("dependencies", [])]
    dependency_lists.extend(project.get("optional-dependencies", {}).values())
    dependency_lists.append(document.get("build-system", {}).get("requires", []))
    dependency_lists.extend(document.get("dependency-groups", {}).values())
    selected_versions = {"openhound": Version(local_version)}
    for dependencies in dependency_lists:
        for index, dependency in enumerate(dependencies):
            # Dependency groups may also contain include-group tables.
            if not isinstance(dependency, str):
                continue
            requirement = Requirement(dependency)
            name = canonicalize_name(requirement.name)
            if name in {"openhound", "boto3"}:
                if name not in selected_versions:
                    selected_versions[name] = Version(version(name))
                extras = (
                    f"[{','.join(sorted(requirement.extras))}]"
                    if requirement.extras
                    else ""
                )
                marker = f"; {requirement.marker}" if requirement.marker else ""
                dependencies[index] = (
                    f"{name}{extras}=={selected_versions[name]}{marker}"
                )
    metadata.write_text(tomlkit.dumps(document))
    # Maturin's root-level include does not place the manifest inside the Python
    # package, where CollectorManager reads it via importlib.resources.
    shutil.copyfile(
        destination / "extension.yaml",
        destination / "src/openhound_aws/extension.yaml",
    )


def main(commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("OPENHOUND_AWS_COMMIT must be a full, pinned commit SHA")
    token = Path("/run/secrets/OPENHOUND_AWS_APP_TOKEN").read_text().strip()
    if not token:
        raise ValueError("OPENHOUND_AWS_APP_TOKEN must not be empty")
    request = Request(
        f"https://api.github.com/repos/SpecterOps/openhound-aws/tarball/{commit}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenHound-AWS-image-build",
        },
    )
    with urlopen(request, timeout=120) as response:
        archive_bytes = response.read()
    destination = Path("/tmp/openhound-aws")
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            _, _, relative = member.name.partition("/")
            if relative:
                member.name = relative
                archive.extract(member, destination, filter="data")

    # Use the installed Python package version, not the BHE user-agent version.
    patch_packaging(destination, version("openhound"))
    print(f"Prepared openhound-aws {commit} for OpenHound {version('openhound')}")


if __name__ == "__main__":
    main(sys.argv[1])
