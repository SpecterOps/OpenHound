import re

_BHE_PACKAGE_VERSION = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:rc(?P<rc>0|[1-9]\d*))?$"
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
            "supported forms are MAJOR.MINOR.PATCH and MAJOR.MINOR.PATCHrcN"
        )

    release = (
        f"v{match.group('major')}."
        f"{match.group('minor')}."
        f"{match.group('patch')}"
    )
    rc = match.group("rc")
    return f"{release}-rc{rc}" if rc is not None else release
