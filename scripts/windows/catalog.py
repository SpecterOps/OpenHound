"""Prepare locked Windows components and Inno definitions using only the stdlib."""

import argparse
import json
import re
import tomllib
from pathlib import Path

MANAGED_FILES = (
    "OpenHound.cmd",
    "runtime-info.json",
    "requirements.lock",
    "extensions.json",
    "LICENSE.md",
    "windows-runtime.md",
)


def normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_catalog(root: Path) -> list[dict]:
    catalog = json.loads((root / "scripts/windows/extensions.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())
    lock = tomllib.loads((root / "uv.lock").read_text())
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("The extension catalog must be a nonempty list.")
    seen = {field: set() for field in ("id", "package", "entrypoint")}
    for extension in catalog:
        if not isinstance(extension, dict):
            raise TypeError("Each catalog entry must be an object.")
        for field in (
            "id",
            "name",
            "package",
            "extra",
            "entrypoint",
            "description",
            "url",
        ):
            value = extension.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Catalog entry requires a nonempty {field}.")
            if any(char in value for char in '\r\n";{}'):
                raise ValueError(f"Unsupported characters in catalog {field}.")
        for field in ("id", "extra", "entrypoint"):
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", extension[field]):
                raise ValueError(f"Invalid catalog {field}: {extension[field]}")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", extension["package"]):
            raise ValueError("Invalid distribution name.")
        for field, values in seen.items():
            value = extension[field]
            if field == "package":
                value = normalized_name(value)
            if value in values:
                raise ValueError(f"Duplicate catalog {field}: {value}")
            values.add(value)
        package_name = normalized_name(extension["package"])
        if package_name == "openhound":
            raise ValueError("OpenHound itself cannot be an optional extension.")
        extra = project["project"]["optional-dependencies"].get(extension["extra"], [])
        if not any(
            normalized_name(re.split(r"[\s\[<>=!~;@]", requirement)[0]) == package_name
            for requirement in extra
        ):
            raise ValueError(f"Extra {extension['extra']} must include {package_name}.")
        packages = [
            package
            for package in lock["package"]
            if normalized_name(package["name"]) == package_name
        ]
        if len(packages) != 1 or not packages[0].get("wheels"):
            raise ValueError(
                f"{package_name} must have one locked version with wheels."
            )
        package = packages[0]
        if not all(
            re.fullmatch(r"sha256:[a-f0-9]{64}", wheel.get("hash", ""))
            for wheel in package["wheels"]
        ):
            raise ValueError(f"{package_name} requires SHA-256 wheel hashes.")
        extension["version"] = package["version"]
        extension["wheel_hashes"] = [wheel["hash"] for wheel in package["wheels"]]
    return catalog


def installer_components(catalog: list[dict]) -> str:
    lines = [
        "; Generated from extensions.json; do not edit.",
        "[Components]",
        'Name: "runtime"; Description: "OpenHound Runtime"; Types: full minimal custom; Flags: fixed',
        'Name: "extensions"; Description: "Extensions"; Types: full',
    ]
    for extension in catalog:
        lines.append(
            f'Name: "extensions\\{extension["id"]}"; '
            f'Description: "{extension["name"]} — {extension["description"]}"; '
            "Types: full; Flags: disablenouninstallwarning"
        )
    lines.extend(
        [
            "",
            "[Files]",
            (
                'Source: "{#PayloadDir}\\python\\*"; DestDir: "{app}\\python"; '
                "Components: runtime; Flags: ignoreversion recursesubdirs createallsubdirs"
            ),
        ]
    )
    for filename in MANAGED_FILES:
        lines.append(
            f'Source: "{{#PayloadDir}}\\{filename}"; DestDir: "{{app}}"; '
            "Components: runtime; Flags: ignoreversion"
        )
    for extension in catalog:
        component = extension["id"]
        lines.append(
            f'Source: "{{#PayloadDir}}\\extensions\\{component}\\*"; '
            f'DestDir: "{{app}}\\extensions\\{component}"; '
            f"Components: extensions\\{component}; "
            "Flags: ignoreversion recursesubdirs createallsubdirs"
        )
    lines.append("")
    return "\n".join(lines)


def installer_managed_paths() -> str:
    paths = ("python", "extensions", *MANAGED_FILES)
    lines = [
        "{ Generated from the installer file list; do not edit. }",
        "procedure GetManagedPaths(var Paths: TArrayOfString);",
        "begin",
        f"  SetArrayLength(Paths, {len(paths)});",
    ]
    lines.extend(f"  Paths[{index}] := '{path}';" for index, path in enumerate(paths))
    lines.extend(["end;", ""])
    return "\n".join(lines)


def prepare(root: Path, output: Path) -> None:
    catalog = load_catalog(root)
    output.mkdir(parents=True, exist_ok=True)
    for extension in catalog:
        hashes = " ".join(f"--hash={value}" for value in extension["wheel_hashes"])
        (output / f"{extension['id']}.lock").write_text(
            f"{extension['package']}=={extension['version']} {hashes}\n"
        )
    (output / "extensions.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (output / "installer-components.iss").write_text(
        installer_components(catalog), encoding="utf-8-sig"
    )
    (output / "installer-managed-paths.iss").write_text(
        installer_managed_paths(), encoding="utf-8-sig"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root, args.output)
