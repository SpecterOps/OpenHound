"""Expose running Windows schedulers/workers to the installer's AppMutex check."""

import sys

MUTEX_NAME = r"Global\SpecterOps.OpenHound.Scheduler"
_mutex_handle: int | None = None


def register_running_process() -> None:
    """Keep a shared marker alive until process exit; do not serialize instances."""
    global _mutex_handle
    if sys.platform != "win32" or _mutex_handle is not None:
        return

    import ctypes
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("descriptor", wintypes.LPVOID),
            ("inherit", wintypes.BOOL),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    create = kernel.CreateMutexExW
    create.argtypes = [
        ctypes.POINTER(SecurityAttributes),
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    create.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID

    descriptor = wintypes.LPVOID()
    # The marker contains no data. Share it across accounts and Windows sessions
    # so another instance and the elevated installer can both open the object.
    if not convert("D:(A;;GA;;;WD)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = SecurityAttributes(
            ctypes.sizeof(SecurityAttributes), descriptor, False
        )
        handle = create(ctypes.byref(attributes), MUTEX_NAME, 0, 0x00100000)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        _mutex_handle = handle
    finally:
        kernel.LocalFree(descriptor)
    # Deliberately keep the handle open: Windows releases it when the process
    # terminates, including after the worker pool and native libraries shut down.
