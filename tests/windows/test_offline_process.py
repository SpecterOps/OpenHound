"""Elevated Windows regression test for Inno-style .tmp process images."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from offline_process import run_offline_process

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
    def test_tmp_image_and_descendant_are_blocked_and_rules_are_removed(self):
        address = socket.getaddrinfo(
            "github.com", 443, socket.AF_INET, socket.SOCK_STREAM
        )[0][4][0]
        with tempfile.TemporaryDirectory(prefix="OpenHound Offline Probe ") as temp:
            root = Path(temp)
            executable = root / "python.tmp"
            original = Path(sys.executable)
            shutil.copy2(original, executable)
            for dll in original.parent.glob("*.dll"):
                shutil.copy2(dll, root / dll.name)
            environment = os.environ.copy()
            environment["PYTHONHOME"] = sys.base_prefix
            arguments = [str(executable), "-B", "-c", PROBE, address]
            child = [str(original), PROBE]

            # Establish connectivity from the same image before testing the
            # firewall, so a disconnected runner cannot produce a false pass.
            subprocess.run(
                arguments + ["allowed"] + child,
                cwd=root,
                env=environment,
                check=True,
                timeout=30,
            )
            result = run_offline_process(
                arguments + ["blocked"] + child,
                cwd=root,
                env=environment,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0)
            # Reuse both paths to verify that their temporary rules were removed.
            subprocess.run(
                arguments + ["allowed"] + child,
                cwd=root,
                env=environment,
                check=True,
                timeout=30,
            )


if __name__ == "__main__":
    unittest.main()
