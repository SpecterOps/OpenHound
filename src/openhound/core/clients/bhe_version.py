import re

_BHE_PACKAGE_VERSION = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:\.?rc(?P<rc>0|[1-9]\d*))?"
    r"(?:\.?dev(?P<dev>0|[1-9]\d*))?$"
)


class UnsupportedBHEVersion(ValueError):
    """Raised when the package version cannot be represented safely for BHE."""


def render_bhe_version(package_version: str) -> str:
    """Convert a supported Python package version to BHE wire format."""
    match = _BHE_PACKAGE_VERSION.fullmatch(package_version)
    if match is None:
        raise UnsupportedBHEVersion(
            "OpenHound package version "
            f"{package_version!r} cannot be reported to BloodHound Enterprise; "
            "supported forms are MAJOR.MINOR.PATCH[rcN], optionally followed "
            "by devN or .devN"
        )

    release = f"v{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
    prerelease = []
    if (rc := match.group("rc")) is not None:
        prerelease.append(f"rc{rc}")
    if (dev := match.group("dev")) is not None:
        prerelease.append(f"dev{dev}")

    return f"{release}-{'.'.join(prerelease)}" if prerelease else release
