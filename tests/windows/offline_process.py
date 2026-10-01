"""Block every installer/helper image before it executes, using debug events."""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

if __package__:
    from .wfp import WfpBlocker
else:
    from wfp import WfpBlocker

DEBUG_PROCESS = 0x00000001


class ExceptionRecord(ctypes.Structure):
    _fields_ = [
        ("code", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("record", wintypes.LPVOID),
        ("address", wintypes.LPVOID),
        ("parameter_count", wintypes.DWORD),
        ("parameters", ctypes.c_size_t * 15),
    ]


class ExceptionInfo(ctypes.Structure):
    _fields_ = [("record", ExceptionRecord), ("first_chance", wintypes.DWORD)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("file", wintypes.HANDLE),
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("base", wintypes.LPVOID),
        ("debug_offset", wintypes.DWORD),
        ("debug_size", wintypes.DWORD),
        ("thread_local", wintypes.LPVOID),
        ("start", wintypes.LPVOID),
        ("image_name", wintypes.LPVOID),
        ("unicode", wintypes.WORD),
    ]


class EventInfo(ctypes.Union):
    _fields_ = (
        ("exception", ExceptionInfo),
        ("process", ProcessInfo),
        ("dll_file", wintypes.HANDLE),
        ("exit_code", wintypes.DWORD),
    )


class DebugEvent(ctypes.Structure):
    _fields_ = [
        ("code", wintypes.DWORD),
        ("process_id", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
        ("info", EventInfo),
    ]


def run_offline_process(arguments, *, cwd, env, timeout):
    """Run setup/uninstall offline, including extracted .tmp images and helpers.

    DEBUG_PROCESS stops each descendant at CREATE_PROCESS_DEBUG_EVENT before
    any user code runs. Install its WFP filters before continuing that event.
    Only observed executable paths are filtered; the CI runner's other programs
    retain their normal network access.
    """
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    wait = kernel.WaitForDebugEvent
    wait.argtypes = [ctypes.POINTER(DebugEvent), wintypes.DWORD]
    wait.restype = wintypes.BOOL
    resume = kernel.ContinueDebugEvent
    resume.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    resume.restype = wintypes.BOOL
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    terminate = kernel.TerminateProcess
    terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate.restype = wintypes.BOOL

    with WfpBlocker() as blocker:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            creationflags=DEBUG_PROCESS,
        )
        # Track the root before its CREATE_PROCESS event, including when a
        # timeout or error happens before we can consume that first event.
        live = {process.pid: int(process._handle)}

        def next_event():
            event = DebugEvent()
            if not wait(ctypes.byref(event), 100):
                error = ctypes.get_last_error()
                if error == 121:  # ERROR_SEM_TIMEOUT
                    return None
                raise ctypes.WinError(error)
            return event

        def terminate_tree():
            for handle in live.values():
                terminate(handle, 1)

        def handle_event(event, *, shutting_down=False):
            status = 0x00010002  # DBG_CONTINUE
            try:
                if event.code == 3:  # CREATE_PROCESS_DEBUG_EVENT
                    info = event.info.process
                    live.setdefault(event.process_id, info.process)
                    if info.file:
                        close(info.file)
                    if shutting_down:
                        terminate(live[event.process_id], 1)
                    else:
                        size = wintypes.DWORD(32768)
                        image = ctypes.create_unicode_buffer(size.value)
                        if not query(info.process, 0, image, ctypes.byref(size)):
                            raise ctypes.WinError(ctypes.get_last_error())
                        blocker.block(image.value)
                        print(
                            f"Outbound networking blocked before launch: {image.value}"
                        )
                elif event.code == 5:  # EXIT_PROCESS_DEBUG_EVENT
                    live.pop(event.process_id, None)
                elif event.code == 6 and event.info.dll_file:  # LOAD_DLL_DEBUG_EVENT
                    close(event.info.dll_file)
                # Windows closes debug process/thread handles when their EXIT
                # events are continued. Closing them again can close reused
                # handle values belonging to other processes or filters.
                elif event.code == 1 and event.info.exception.record.code not in {
                    0x80000003,
                    0x4000001F,
                }:
                    # Handle the debugger's initial breakpoint; preserve normal
                    # structured exception handling for all other exceptions.
                    status = 0x80010001  # DBG_EXCEPTION_NOT_HANDLED
            except BaseException:
                # Fail closed: kill the suspended process tree before resuming
                # an event whose WFP filters could not be installed.
                terminate_tree()
                raise
            finally:
                if not resume(event.process_id, event.thread_id, status):
                    raise ctypes.WinError(ctypes.get_last_error())

        try:
            deadline = time.monotonic() + timeout
            while live:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                event = next_event()
                if event is not None:
                    handle_event(event)
            return subprocess.CompletedProcess(
                arguments, process.wait(timeout=10), "", ""
            )
        finally:
            if live:
                failure = sys.exception()
                terminate_tree()
                try:
                    # TerminateProcess is asynchronous. Continue every pending
                    # event through EXIT_PROCESS before waiting, or Windows can
                    # retain the executable mapping and prevent temp cleanup.
                    deadline = time.monotonic() + 10
                    while live:
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(arguments, 10)
                        event = next_event()
                        if event is not None:
                            handle_event(event, shutting_down=True)
                    process.wait(timeout=10)
                except Exception as cleanup_error:
                    if failure is None:
                        raise
                    failure.add_note(f"Debug process cleanup failed: {cleanup_error}")
