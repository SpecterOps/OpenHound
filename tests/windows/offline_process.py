"""Block every installer/helper image before it executes, using debug events."""

import ctypes
import os
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path

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
    any user code runs. Install its firewall rule before continuing that event.
    The CI runner and this controller retain their normal network access.
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

    netsh = str(Path(os.environ["SystemRoot"]) / "System32/netsh.exe")
    rule = f"OpenHound Offline Smoke {uuid.uuid4().hex}"
    blocked = set()
    handles = []
    live = {}
    process = subprocess.Popen(
        arguments,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        creationflags=DEBUG_PROCESS,
    )
    try:
        deadline = time.monotonic() + timeout
        while process.poll() is None or live:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(arguments, timeout)
            event = DebugEvent()
            if not wait(ctypes.byref(event), 100):
                error = ctypes.get_last_error()
                if error == 121:  # ERROR_SEM_TIMEOUT
                    continue
                raise ctypes.WinError(error)
            status = 0x00010002  # DBG_CONTINUE
            try:
                if event.code == 3:  # CREATE_PROCESS_DEBUG_EVENT
                    info = event.info.process
                    live[event.process_id] = info.process
                    handles.extend([info.process, info.thread])
                    if info.file:
                        close(info.file)
                    size = wintypes.DWORD(32768)
                    image = ctypes.create_unicode_buffer(size.value)
                    if not query(info.process, 0, image, ctypes.byref(size)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if image.value.casefold() not in blocked:
                        subprocess.run(
                            [
                                netsh,
                                "advfirewall",
                                "firewall",
                                "add",
                                "rule",
                                f"name={rule}",
                                "dir=out",
                                "action=block",
                                "enable=yes",
                                "profile=any",
                                f"program={image.value}",
                            ],
                            check=True,
                            capture_output=True,
                            timeout=30,
                        )
                        blocked.add(image.value.casefold())
                        print(
                            f"Outbound networking blocked before launch: {image.value}"
                        )
                elif event.code == 5:  # EXIT_PROCESS_DEBUG_EVENT
                    live.pop(event.process_id, None)
                    if event.process_id == process.pid:
                        process.returncode = event.info.exit_code
                elif event.code in {2, 6} and event.info.dll_file:
                    # CREATE_THREAD and LOAD_DLL supply a thread/file handle.
                    close(event.info.dll_file)
                elif event.code == 1 and event.info.exception.record.code not in {
                    0x80000003,
                    0x4000001F,
                }:
                    # Handle the debugger's initial breakpoint; preserve normal
                    # structured exception handling for all other exceptions.
                    status = 0x80010001  # DBG_EXCEPTION_NOT_HANDLED
            finally:
                if not resume(event.process_id, event.thread_id, status):
                    raise ctypes.WinError(ctypes.get_last_error())
        return subprocess.CompletedProcess(arguments, process.returncode, "", "")
    finally:
        for handle in live.values():
            terminate(handle, 1)
        if process.poll() is None:
            process.kill()
        for handle in handles:
            if handle != int(process._handle):
                close(handle)
        if blocked:
            subprocess.run(
                [netsh, "advfirewall", "firewall", "delete", "rule", f"name={rule}"],
                check=True,
                capture_output=True,
                timeout=30,
            )
