import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from functools import partial
from threading import Thread
from urllib.request import urlopen

from openhound.scheduler.status import (
    SchedulerStatus,
    receive_worker_phases,
    report_worker_phase,
    serve_status,
)


def test_status_http_and_process_lifecycle():
    status = SchedulerStatus()
    with serve_status(status, "127.0.0.1", 0) as port:

        def snapshot():
            with urlopen(f"http://127.0.0.1:{port}/status", timeout=3) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                return json.load(response)

        initial = snapshot()
        assert initial == {
            "instance_id": status.instance_id,
            "state": "initializing",
            "capacity": 1,
            "occupied_slots": 0,
            "available_slots": 0,
            "jobs": {},
        }
        assert SchedulerStatus().instance_id != initial["instance_id"]
        status.start()
        assert snapshot()["available_slots"] == 1
        status.reserve("job", "claiming")
        busy = snapshot()
        assert busy["state"] == "busy"
        assert busy["occupied_slots"] == 1
        assert busy["available_slots"] == 0
        status.reserve("job", "claiming")
        assert snapshot() == busy

        status.draining = True
        draining = snapshot()
        assert draining["state"] == "draining"
        assert draining["instance_id"] == initial["instance_id"]
        assert draining["jobs"] == busy["jobs"]
        status.release("job")
        assert status.reserve("next-job", "claiming") is None
        assert snapshot()["jobs"] == {}
        assert snapshot()["available_slots"] == 0


def test_spawned_worker_phase_events_and_stale_attempts():
    status = SchedulerStatus()
    status.start()
    attempt = status.reserve("job", "starting")
    ctx = multiprocessing.get_context("spawn")
    with ctx.Manager() as manager:
        queue = manager.Queue()
        reader = Thread(target=receive_worker_phases, args=(queue, status))
        reader.start()
        try:
            with ProcessPoolExecutor(mp_context=ctx, max_workers=1) as executor:
                report = partial(report_worker_phase, queue, attempt)
                executor.submit(report, "collecting").result(timeout=10)
                executor.submit(report, "preprocessing").result(timeout=10)
                executor.submit(report, "converting").result(timeout=10)
        finally:
            queue.put(None)
            reader.join(timeout=5)
    assert not reader.is_alive()

    converting = status.snapshot()["jobs"]["job"]
    assert converting["state"] == "converting"
    assert converting["state_since"].endswith("Z")
    status.worker_phase(attempt, "collecting", datetime.now(UTC))
    assert status.snapshot()["jobs"]["job"] == converting
    status.worker_finished(attempt, awaiting_upload=True)
    assert status.snapshot()["jobs"]["job"]["state"] == "awaiting_upload"

    status.release("job")
    status.reserve("job", "starting")
    status.worker_phase(attempt, "preprocessing", datetime.now(UTC))
    status.worker_finished(attempt, awaiting_upload=False)
    assert status.snapshot()["jobs"]["job"]["state"] == "starting"
