"""Portable checks for failures in the Windows offline process controller."""

import ctypes
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from tests.windows import offline_process, wfp


class OfflineProcessFailureTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.kernel = Mock()
        self.blocker = MagicMock()
        self.blocker.__enter__.return_value = self.blocker
        self.blocker.__exit__.side_effect = lambda *args: (
            self.calls.append(("filters_closed",)) or False
        )
        self.process = Mock(pid=123, _handle=31)
        self.process.wait.side_effect = lambda **kwargs: (
            self.calls.append(("wait",)) or 1
        )

        def wait(pointer, timeout):
            event = self.events.pop(0)
            ctypes.memmove(pointer, ctypes.byref(event), ctypes.sizeof(event))
            self.calls.append(("event", event.code, event.process_id))
            return True

        def query(process, flags, image, size):
            image.value = r"C:\Temp\installer.tmp"
            return True

        self.kernel.WaitForDebugEvent.side_effect = wait
        self.kernel.QueryFullProcessImageNameW.side_effect = query
        self.kernel.TerminateProcess.side_effect = lambda handle, code: (
            self.calls.append(("terminate", handle)) or True
        )
        self.kernel.ContinueDebugEvent.side_effect = lambda pid, tid, status: (
            self.calls.append(("resume", pid)) or True
        )
        self.enterContext(
            patch.object(
                offline_process.ctypes, "WinDLL", return_value=self.kernel, create=True
            )
        )
        self.enterContext(
            patch.object(offline_process.subprocess, "Popen", return_value=self.process)
        )
        self.enterContext(
            patch.object(offline_process, "WfpBlocker", return_value=self.blocker)
        )

    def event(self, code, pid=123, handle=11):
        event = offline_process.DebugEvent()
        event.code = code
        event.process_id = pid
        event.thread_id = pid + 100
        if code == 3:
            event.info.process.process = handle
            event.info.process.thread = handle + 1
            event.info.process.file = handle + 2
        elif code in {2, 6}:
            event.info.dll_file = handle
        self.events.append(event)

    def test_filter_failure_kills_tree_then_drains_exits_before_closing_filters(self):
        self.event(3)
        self.event(3, pid=124, handle=21)
        self.event(2, handle=88)  # Thread handles are closed by Windows.
        self.event(6, handle=99)  # DLL file handles are closed by the debugger.
        self.event(5, pid=124)
        self.event(5)
        self.blocker.block.side_effect = [None, OSError("Cannot install WFP filter")]

        with self.assertRaisesRegex(OSError, "Cannot install WFP filter"):
            offline_process.run_offline_process(
                ["installer.exe"], cwd="outside", env={}, timeout=10
            )

        child_event = self.calls.index(("event", 3, 124))
        self.assertEqual(
            self.calls[child_event + 1 : child_event + 4],
            [("terminate", 31), ("terminate", 21), ("resume", 124)],
        )
        self.assertEqual(self.calls[-2:], [("wait",), ("filters_closed",)])
        self.assertFalse(self.events, "Pending debug events were not drained")
        self.kernel.CloseHandle.assert_any_call(99)
        self.assertNotIn(call(88), self.kernel.CloseHandle.call_args_list)
        self.assertNotIn(call(21), self.kernel.CloseHandle.call_args_list)

    def test_timeout_before_first_event_still_reaps_root(self):
        self.event(3)
        self.event(5)
        with (
            patch.object(
                offline_process.time, "monotonic", side_effect=[0, 2, 2, 2, 2]
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            offline_process.run_offline_process(
                ["installer.exe"], cwd="outside", env={}, timeout=1
            )
        self.assertEqual(self.calls[0], ("terminate", 31))
        self.assertEqual(self.calls[-2:], [("wait",), ("filters_closed",)])
        self.blocker.block.assert_not_called()
        self.assertFalse(self.events)


class WfpTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        for name in (
            "FwpmEngineOpen0",
            "FwpmEngineClose0",
            "FwpmSubLayerAdd0",
            "FwpmFilterAdd0",
        ):
            getattr(self.api, name).return_value = 0
        self.data = (ctypes.c_uint8 * 4)(1, 2, 3, 4)
        self.blob = wfp.ByteBlob(4, self.data)

        def app_id(image, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(wfp.ByteBlob)))[0] = (
                ctypes.pointer(self.blob)
            )
            return 0

        self.api.FwpmGetAppIdFromFileName0.side_effect = app_id
        self.enterContext(
            patch.object(wfp.ctypes, "WinDLL", return_value=self.api, create=True)
        )
        self.enterContext(
            patch.object(
                wfp.ctypes, "FormatError", return_value="Invalid parameter", create=True
            )
        )

    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "Windows x64 ABI")
    def test_native_structure_layouts(self):
        for structure, size in (
            (wfp.Guid, 16),
            (wfp.Value, 16),
            (wfp.Session, 72),
            (wfp.SubLayer, 72),
            (wfp.FilterCondition, 40),
            (wfp.Filter, 200),
        ):
            self.assertEqual(ctypes.sizeof(structure), size, structure.__name__)
        self.assertEqual(wfp.Filter.action.offset, 128)
        self.assertEqual(wfp.Filter.context.offset, 152)

    def test_dynamic_session_blocks_both_address_families_with_an_app_condition(self):
        layers = []

        def add(engine, pointer, security, output):
            filter = pointer._obj
            self.assertEqual(filter.action.type, wfp.FWP_ACTION_BLOCK)
            self.assertEqual(filter.condition_count, 1)
            condition = filter.conditions[0]
            self.assertEqual(bytes(condition.field_key), bytes(wfp.ALE_APP_ID))
            self.assertEqual(condition.value.type, wfp.FWP_BYTE_BLOB_TYPE)
            self.assertEqual(condition.value.byte_blob.contents.size, 4)
            layers.append(bytes(filter.layer_key))
            return 0

        self.api.FwpmFilterAdd0.side_effect = add
        with wfp.WfpBlocker() as blocker:
            blocker.block(r"C:\Temp\python.tmp")
            blocker.block(r"c:\temp\PYTHON.TMP")
            self.api.FwpmEngineClose0.assert_not_called()
        session = self.api.FwpmEngineOpen0.call_args.args[3]._obj
        self.assertEqual(session.flags, wfp.FWPM_SESSION_FLAG_DYNAMIC)
        self.assertEqual(layers, list(map(bytes, wfp.ALE_AUTH_CONNECT_LAYERS)))
        self.api.FwpmGetAppIdFromFileName0.assert_called_once()
        self.api.FwpmFreeMemory0.assert_called_once()
        self.api.FwpmEngineClose0.assert_called_once()

    def test_partial_filter_failure_releases_session_and_app_id(self):
        self.api.FwpmFilterAdd0.side_effect = [0, 87]
        with (
            self.assertRaisesRegex(
                OSError,
                r"FwpmFilterAdd0\(.*python.tmp\).*0x00000057.*Invalid parameter",
            ),
            wfp.WfpBlocker() as blocker,
        ):
            blocker.block(r"C:\Temp\python.tmp")
        self.api.FwpmFreeMemory0.assert_called_once()
        self.api.FwpmEngineClose0.assert_called_once()

    def test_sublayer_failure_closes_opened_session(self):
        self.api.FwpmSubLayerAdd0.return_value = 87
        with (
            self.assertRaisesRegex(OSError, "FwpmSubLayerAdd0.*0x00000057"),
            wfp.WfpBlocker(),
        ):
            self.fail("Session initialization should have failed")
        self.api.FwpmEngineClose0.assert_called_once()

    def test_session_close_error_does_not_replace_filter_failure(self):
        self.api.FwpmFilterAdd0.return_value = 87
        self.api.FwpmEngineClose0.return_value = 5
        with (
            self.assertRaisesRegex(OSError, "FwpmFilterAdd0") as caught,
            wfp.WfpBlocker() as blocker,
        ):
            blocker.block(r"C:\Temp\python.tmp")
        self.assertIn("FwpmEngineClose0", caught.exception.__notes__[0])


if __name__ == "__main__":
    unittest.main()
