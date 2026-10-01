"""Block every installer/helper image before it executes, using debug events."""

import base64
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


def _run_firewall_script(script):
    powershell = str(
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    encoded = base64.b64encode(
        ("$ErrorActionPreference = 'Stop';\n" + script).encode("utf-16-le")
    ).decode("ascii")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Windows Firewall command failed ({result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


def _powershell_literal(value):
    return "'" + value.replace("'", "''") + "'"


def _add_firewall_rule(group, image):
    # netsh validates executable suffixes and rejects Inno's extracted .tmp
    # images. The Firewall COM API accepts their actual executable paths.
    name = f"{group} {uuid.uuid4().hex}"
    _run_firewall_script(
        f"""
$rule = New-Object -ComObject HNetCfg.FwRule
$rule.Name = {_powershell_literal(name)}
$rule.Grouping = {_powershell_literal(group)}
$rule.ApplicationName = {_powershell_literal(image)}
$rule.Direction = 2
$rule.Action = 0
$rule.Enabled = $true
$rule.Profiles = 2147483647
$policy = New-Object -ComObject HNetCfg.FwPolicy2
$policy.Rules.Add($rule)
"""
    )


def _remove_firewall_rules(group):
    _run_firewall_script(
        f"""
$policy = New-Object -ComObject HNetCfg.FwPolicy2
foreach ($rule in @($policy.Rules)) {{
    if ($rule.Grouping -eq {_powershell_literal(group)}) {{
        $policy.Rules.Remove($rule.Name)
    }}
}}
"""
    )


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
                        _add_firewall_rule(rule, image.value)
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
            except BaseException:
                # Fail closed: kill the suspended process tree before resuming
                # an event whose firewall rule could not be installed.
                for handle in live.values():
                    terminate(handle, 1)
                raise
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
        _remove_firewall_rules(rule)
