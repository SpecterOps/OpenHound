"""Run with the packaged interpreter and a CI-only collector in a copied payload."""

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from openhound.scheduler.instance import InstancePaths, configure_instance


class FakeClient:
    def __init__(self):
        self.started = []
        self.ended = []

    def start_job(self, job_id):
        self.started.append(job_id)

    def end_job(self, status, message):
        self.ended.append((status, message))

    def update_client_metadata(self):
        pass

    @property
    def management_available(self):
        return SimpleNamespace(data=[])

    @property
    def jobs_available(self):
        return SimpleNamespace(data=[])


def main():
    root = Path(sys.argv[-1]).resolve()
    configure_instance(InstancePaths(root))
    if "--idle-child" not in sys.argv:
        config_file = root / ".dlt" / "config.toml"
        if not config_file.exists():
            config_file.write_text('[windows_smoke]\nsentinel = "persisted"\n')
        import dlt

        assert dlt.config["windows_smoke.sentinel"] == "persisted"
    from openhound.core.clients.bloodhound_enterprise import JobStatus
    from openhound.core.manager import CollectorManager
    from openhound.scheduler.service import Service

    names = {
        collector.name for collector in CollectorManager.from_entrypoint().collectors
    }
    if "--idle-child" not in sys.argv:
        assert "offline" in names, names
    stop_file = root / "temp" / "stop.request"
    service = Service(
        "http://127.0.0.1:1",
        "unused",
        "unused",
        "offline",
        instance_dir=root,
        stop_file=stop_file if "--idle-child" in sys.argv else None,
    )
    if "--idle-child" in sys.argv:
        service.client = FakeClient()
        wait = service._wait_for_next_poll

        def idle_wait():
            (root / "temp" / "idle-ready").write_text("ready")
            wait()

        service._wait_for_next_poll = idle_wait
        service.start()
        return

    fake = FakeClient()
    service.client = fake
    try:
        service._start_job(SimpleNamespace(id=123))
        assert service.future is not None
        result = service.future.result(timeout=60)
        assert result.job_id == 123
        service._handle_completed_job(service.future)
        assert fake.started == [123]
        assert fake.ended[0][0] == JobStatus.COMPLETE
        assert (root / "output" / "offline" / "worker-ok.txt").is_file()
    finally:
        service._shutdown()

    idle_root = root / "idle"
    ready = idle_root / "temp" / "idle-ready"
    stop_file = idle_root / "temp" / "stop.request"
    ready.unlink(missing_ok=True)
    stop_file.unlink(missing_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", __file__, "--idle-child", str(idle_root)],
        cwd=root / "output",
        env=os.environ.copy(),
    )
    try:
        deadline = time.monotonic() + 20
        while (
            not ready.exists()
            and time.monotonic() < deadline
            and process.poll() is None
        ):
            time.sleep(0.1)
        assert ready.is_file(), "idle scheduler never became ready"
        stop_file.write_text("stop")
        assert process.wait(timeout=10) == 0, "scheduler did not stop cleanly"
        assert not stop_file.exists(), "scheduler did not consume the stop request"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    print("Packaged worker, instance paths, collector entry points and idle stop: OK")


if __name__ == "__main__":
    main()
