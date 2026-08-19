"""Cross-platform peak RSS sampler for the BED-9372 benchmark.

Windows has no resource.getrusage peak-RSS equivalent, so a background thread
polls psutil RSS during the run and keeps the maximum observed value.
"""

from __future__ import annotations

import threading

import psutil


class PeakRSSSampler:
    def __init__(self, process: psutil.Process, interval: float = 0.05):
        self._process = process
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_rss = 0

    def _sample_once(self) -> None:
        try:
            rss = self._process.memory_info().rss
            if rss > self.peak_rss:
                self.peak_rss = rss
        except psutil.Error:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self._interval)

    def start(self) -> None:
        self._sample_once()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._sample_once()
