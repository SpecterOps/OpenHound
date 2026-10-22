"""Fetch pinned AWS source and patch only its temporary packaging metadata."""

import io
import re
import shutil
import sys
import tarfile
from importlib.metadata import version
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.version import Version


def main(commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("OPENHOUND_AWS_COMMIT must be a full, pinned commit SHA")
    token = Path("/run/secrets/OPENHOUND_AWS_GITHUB_TOKEN").read_text().strip()
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
        # GitHub archives contain one top-level directory. Strip it without
        # depending on the abbreviated SHA used in that directory's name.
        for member in archive.getmembers():
            _, _, relative = member.name.partition("/")
            if relative:
                member.name = relative
                archive.extract(member, destination, filter="data")

    # Read the actual package metadata, not the BHE/HTTP display version.
    # An exact, dynamically derived requirement also prevents the resolver from
    # replacing this branch's development build with an OpenHound PyPI release.
    local_version = str(Version(version("openhound")))
    metadata = destination / "pyproject.toml"
    text = metadata.read_text()
    replacements = {
        '"openhound==0.2.13"': (f'"openhound=={local_version}"', 2),
        '"boto3==1.43.68"': ('"boto3>=1.43.90"', 1),
    }
    for old, (new, expected_count) in replacements.items():
        if text.count(old) != expected_count:
            raise ValueError(f"Unexpected AWS packaging metadata: {old}")
        text = text.replace(old, new)
    metadata.write_text(text)
    # Maturin's root-level include does not place the manifest inside the Python
    # package, where CollectorManager reads it via importlib.resources.
    shutil.copyfile(
        destination / "extension.yaml",
        destination / "src/openhound_aws/extension.yaml",
    )
    print(f"Prepared openhound-aws {commit} for OpenHound {local_version}")


if __name__ == "__main__":
    main(sys.argv[1])
