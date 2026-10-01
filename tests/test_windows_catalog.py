"""Build-time catalog checks, runnable without application dependencies."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.windows.catalog import load_catalog, prepare

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts/windows").mkdir(parents=True)
        self.catalog = [
            {
                "id": "future",
                "name": "Future collector",
                "package": "openhound-future",
                "extra": "future",
                "entrypoint": "future",
                "description": "A new collector",
                "url": "https://example.com/future",
            }
        ]
        (self.root / "pyproject.toml").write_text(
            '[project.optional-dependencies]\nfuture = ["openhound-future==1.2.3"]\n'
        )
        (self.root / "uv.lock").write_text(
            '[[package]]\nname = "openhound-future"\nversion = "1.2.3"\n'
            'wheels = [{hash = "sha256:' + "a" * 64 + '"}]\n'
        )
        self.write_catalog()

    def write_catalog(self):
        (self.root / "scripts/windows/extensions.json").write_text(
            json.dumps(self.catalog)
        )

    def test_repository_catalog_matches_locked_extras(self):
        catalog = load_catalog(ROOT)
        self.assertLessEqual(
            {"github", "okta", "jamf"}, {extension["id"] for extension in catalog}
        )
        self.assertTrue(all(extension["wheel_hashes"] for extension in catalog))

    def test_new_collector_generates_locked_component_without_special_cases(self):
        output = self.root / "prepared"
        prepare(self.root, output)
        self.assertEqual(
            (output / "future.lock").read_text(),
            "openhound-future==1.2.3 --hash=sha256:" + "a" * 64 + "\n",
        )
        definitions = (output / "installer-components.iss").read_text("utf-8-sig")
        self.assertIn('Name: "extensions\\future"', definitions)
        self.assertIn('DestDir: "{app}\\extensions\\future"', definitions)
        self.assertNotIn("github", definitions)
        self.assertNotIn("ProgramData", definitions)
        self.assertEqual(
            json.loads((output / "extensions.json").read_text())[0]["version"], "1.2.3"
        )

    def test_rejects_unsafe_component_paths(self):
        self.catalog[0]["id"] = "../other"
        self.write_catalog()
        with self.assertRaisesRegex(ValueError, "Invalid catalog id"):
            load_catalog(self.root)

    def test_rejects_duplicate_distributions_after_normalization(self):
        self.catalog.append(
            dict(
                self.catalog[0],
                id="another",
                entrypoint="another",
                package="OpenHound_Future",
            )
        )
        self.write_catalog()
        with self.assertRaisesRegex(ValueError, "Duplicate catalog package"):
            load_catalog(self.root)

    def test_rejects_missing_extra(self):
        self.catalog[0]["extra"] = "unknown"
        self.write_catalog()
        with self.assertRaisesRegex(ValueError, "Extra unknown"):
            load_catalog(self.root)

    def test_rejects_missing_locked_wheel(self):
        (self.root / "uv.lock").write_text(
            '[[package]]\nname = "openhound-future"\nversion = "1.2.3"\n'
        )
        with self.assertRaisesRegex(ValueError, "locked version with wheels"):
            load_catalog(self.root)

    def test_rejects_unhashed_wheel(self):
        (self.root / "uv.lock").write_text(
            '[[package]]\nname = "openhound-future"\nversion = "1.2.3"\nwheels = [{}]\n'
        )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_catalog(self.root)


if __name__ == "__main__":
    unittest.main()
