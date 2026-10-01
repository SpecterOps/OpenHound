"""Elevated Windows regression test for Inno-style .tmp process images."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from offline_process import WfpBlocker, run_offline_process

PROBE = """
import socket
import subprocess
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
    connection.settimeout(10)
    try:
        connection.connect((sys.argv[1], 443))
    except OSError as error:
        if sys.argv[2] != "blocked" or error.winerror != 10013:
            raise
    else:
        assert sys.argv[2] == "allowed", "Outbound networking was not blocked"

if len(sys.argv) > 3:
    subprocess.run(
        [sys.argv[3], "-I", "-B", "-c", sys.argv[4], sys.argv[1], sys.argv[2]],
        check=True,
        timeout=30,
    )
"""


@unittest.skipUnless(sys.platform == "win32", "Requires elevated Windows")
class OfflineProcessTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(
            tempfile.TemporaryDirectory(prefix="OpenHound Offline Probe ")
        )
        self.root = Path(temp)
        self.executable = self.root / "python.tmp"
        self.original = Path(sys.executable)
        shutil.copy2(self.original, self.executable)
        for dll in self.original.parent.glob("*.dll"):
            shutil.copy2(dll, self.root / dll.name)
        self.environment = os.environ.copy()
        self.environment["PYTHONHOME"] = sys.base_prefix

    def test_tmp_image_and_descendant_are_blocked_and_rules_are_removed(self):
        address = socket.getaddrinfo(
            "github.com", 443, socket.AF_INET, socket.SOCK_STREAM
        )[0][4][0]
        arguments = [str(self.executable), "-B", "-c", PROBE, address]
        child = [str(self.original), PROBE]

        # Establish connectivity from the same image before testing the
        # filters, so a disconnected runner cannot produce a false pass.
        subprocess.run(
            arguments + ["allowed"] + child,
            cwd=self.root,
            env=self.environment,
            check=True,
            timeout=30,
        )
        result = run_offline_process(
            arguments + ["blocked"] + child,
            cwd=self.root,
            env=self.environment,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        # Reuse both paths to verify that their temporary filters were removed.
        subprocess.run(
            arguments + ["allowed"] + child,
            cwd=self.root,
            env=self.environment,
            check=True,
            timeout=30,
        )

    def test_filter_failure_releases_tmp_image_without_executing_user_code(self):
        marker = self.root / "executed"
        with (
            patch.object(WfpBlocker, "block", side_effect=OSError("Injected failure")),
            self.assertRaisesRegex(OSError, "Injected failure"),
        ):
            run_offline_process(
                [
                    str(self.executable),
                    "-B",
                    "-c",
                    "from pathlib import Path; Path('executed').touch()",
                ],
                cwd=self.root,
                env=self.environment,
                timeout=30,
            )
        self.assertFalse(marker.exists(), "Unfiltered process executed user code")
        self.executable.unlink()  # No retries: debugger cleanup must release it.

    def test_timeout_releases_tmp_image(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_offline_process(
                [str(self.executable), "-B", "-c", "import time; time.sleep(60)"],
                cwd=self.root,
                env=self.environment,
                timeout=1,
            )
        self.executable.unlink()


if __name__ == "__main__":
    unittest.main()
