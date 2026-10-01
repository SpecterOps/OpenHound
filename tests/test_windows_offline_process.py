"""Portable checks for failures in the Windows offline process controller."""

import subprocess
import unittest
from unittest.mock import Mock, patch

from tests.windows import offline_process


class OfflineProcessFailureTests(unittest.TestCase):
    def test_firewall_failure_reports_captured_output(self):
        result = subprocess.CompletedProcess(
            [], 1, "firewall output", "firewall error details"
        )
        with (
            patch.dict(offline_process.os.environ, SystemRoot="C:/Windows"),
            patch.object(offline_process.subprocess, "run", return_value=result),
            self.assertRaisesRegex(
                RuntimeError,
                "(?s)failed \\(1\\).*firewall output.*firewall error details",
            ),
        ):
            offline_process._run_firewall_script("throw 'failure'")

    def test_firewall_failure_terminates_process_before_resuming_event(self):
        calls = []
        kernel = Mock()

        def wait(event, timeout):
            event = event._obj
            event.code = 3  # CREATE_PROCESS_DEBUG_EVENT
            event.process_id = 123
            event.thread_id = 456
            event.info.process.process = 11
            event.info.process.thread = 12
            return True

        def query(process, flags, image, size):
            image.value = r"C:\Temp\installer.tmp"
            return True

        kernel.WaitForDebugEvent.side_effect = wait
        kernel.QueryFullProcessImageNameW.side_effect = query
        kernel.TerminateProcess.side_effect = lambda *args: calls.append("terminate")
        kernel.ContinueDebugEvent.side_effect = lambda *args: (
            calls.append("resume") or True
        )
        process = Mock(pid=123, _handle=11)
        process.poll.return_value = None

        with (
            patch.object(
                offline_process.ctypes, "WinDLL", return_value=kernel, create=True
            ),
            patch.object(offline_process.subprocess, "Popen", return_value=process),
            patch.object(
                offline_process,
                "_add_firewall_rule",
                side_effect=RuntimeError("Cannot install firewall rule"),
            ),
            patch.object(offline_process, "_remove_firewall_rules") as cleanup,
            self.assertRaisesRegex(RuntimeError, "Cannot install firewall rule"),
        ):
            offline_process.run_offline_process(
                ["installer.exe"], cwd="outside", env={}, timeout=10
            )

        self.assertEqual(calls[:2], ["terminate", "resume"])
        cleanup.assert_called_once()
        process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
