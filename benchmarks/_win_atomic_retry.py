"""Benchmark-local retry shim for DLT's atomic file replace on Windows.

``os.replace`` in ``FileStorage.save_atomic`` intermittently raises WinError 5
when a handle on the just-written file lingers (NTFS releases rename locks late;
see CPython gh-90161 and dlt-hub/dlt PR #3853). At the batch_size=1 baseline the
state file is committed ~1000x, hitting the race often enough to exhaust DLT's
retries. Applied here (not in shipped code) since it only affects this synthetic
worst case; a genuine permission error still surfaces once the budget is spent.
"""

from __future__ import annotations

import time

_MAX_ATTEMPTS = 20
_BACKOFF_SECONDS = 0.02


def install() -> None:
    """Idempotently wrap FileStorage.save_atomic with a WinError 5 retry loop."""
    from dlt.common.storages import file_storage as fs

    if getattr(fs.FileStorage.save_atomic, "_win_atomic_retry", False):
        return

    original = fs.FileStorage.save_atomic

    def save_atomic_with_retry(
        storage_path: str, relative_path: str, data, file_type: str = "t"
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return original(storage_path, relative_path, data, file_type=file_type)
            except PermissionError as exc:  # WinError 5 on the os.replace
                last_exc = exc
                if attempt == _MAX_ATTEMPTS - 1:
                    break
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    save_atomic_with_retry._win_atomic_retry = True  # type: ignore[attr-defined]
    fs.FileStorage.save_atomic = staticmethod(save_atomic_with_retry)
