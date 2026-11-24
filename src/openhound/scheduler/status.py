"""Per-process status for the cloud collection operator."""

import json
import logging
import socket
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

import uvicorn

logger = logging.getLogger(__name__)
STATUS_PORT = 8081
WORKER_PHASES = ("collecting", "preprocessing", "converting")


@dataclass
class ActiveJob:
    job_id: str
    attempt: str
    state: str
    state_since: datetime
    worker_finished: bool = False

    def transition(self, state: str, at: datetime) -> None:
        if self.state != state:
            self.state = state
            self.state_since = at


class SchedulerStatus:
    def __init__(self) -> None:
        self.instance_id = str(uuid4())
        self.draining = False
        self._started = False
        self._job: ActiveJob | None = None
        self._lock = Lock()

    def wait_for_shutdown(self, seconds: float) -> bool:
        # Signal handlers only set a flag, including when they interrupt a wait.
        deadline = time.monotonic() + seconds
        while not self.draining:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.1))
        return self.draining

    def start(self) -> None:
        with self._lock:
            self._started = True

    def reserve(self, job_id: str, state: str) -> str | None:
        with self._lock:
            if self._job is not None:
                if self._job.job_id != job_id:
                    raise RuntimeError("The status slot is already reserved")
                self._job.transition(state, datetime.now(UTC))
                return self._job.attempt
            if self.draining:
                return None
            job = ActiveJob(job_id, uuid4().hex, state, datetime.now(UTC))
            self._job = job
            if self.draining:
                self._job = None
                return None
            return job.attempt

    def transition(self, job_id: str, state: str) -> None:
        with self._lock:
            if self._job is not None and self._job.job_id == job_id:
                self._job.transition(state, datetime.now(UTC))

    def worker_phase(self, attempt: str, phase: str, at: datetime) -> None:
        with self._lock:
            job = self._job
            if (
                job is None
                or job.attempt != attempt
                or job.worker_finished
                or phase not in WORKER_PHASES
                or job.state not in ("starting", *WORKER_PHASES)
            ):
                return
            phases = ("starting", *WORKER_PHASES)
            if phases.index(phase) > phases.index(job.state):
                job.transition(phase, at)

    def worker_finished(self, attempt: str, awaiting_upload: bool) -> None:
        with self._lock:
            job = self._job
            if job is not None and job.attempt == attempt and not job.worker_finished:
                job.worker_finished = True
                if job.state in ("starting", *WORKER_PHASES):
                    job.transition(
                        "awaiting_upload" if awaiting_upload else "finalizing",
                        datetime.now(UTC),
                    )

    def release(self, job_id: str) -> None:
        with self._lock:
            if self._job is not None and self._job.job_id == job_id:
                self._job = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if self.draining:
                state = "draining"
            elif not self._started:
                state = "initializing"
            else:
                state = "busy" if job else "available"
            return {
                "instance_id": self.instance_id,
                "state": state,
                "capacity": 1,
                "occupied_slots": int(job is not None),
                "available_slots": int(state == "available"),
                "jobs": {
                    job.job_id: {
                        "state": job.state,
                        "state_since": job.state_since.isoformat().replace(
                            "+00:00", "Z"
                        ),
                    }
                }
                if job
                else {},
            }

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        headers = [(b"cache-control", b"no-store")]
        if scope["path"] != "/status":
            code, body = 404, b""
        elif scope["method"] != "GET":
            code, body = 405, b""
            headers.append((b"allow", b"GET"))
        else:
            code, body = 200, json.dumps(self.snapshot()).encode("utf-8")
            headers.append((b"content-type", b"application/json"))
        await send({"type": "http.response.start", "status": code, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def report_worker_phase(queue: Any, attempt: str, phase: str) -> None:
    try:
        queue.put((attempt, phase, datetime.now(UTC)))
    except (OSError, EOFError):
        logger.warning("Could not publish worker phase")


def receive_worker_phases(queue: Any, status: SchedulerStatus) -> None:
    try:
        while (event := queue.get()) is not None:
            status.worker_phase(*event)
    except (OSError, EOFError):
        logger.warning("Worker phase queue closed unexpectedly")


@contextmanager
def serve_status(
    status: SchedulerStatus, host: str = "0.0.0.0", port: int = STATUS_PORT
) -> Iterator[int]:
    server = uvicorn.Server(
        uvicorn.Config(
            status,
            interface="asgi3",
            lifespan="off",
            ws="none",
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=2,
        )
    )
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        # On Windows Uvicorn duplicates supplied sockets across threads.
        listener.listen()
        stopped = Event()

        def run() -> None:
            try:
                server.run(sockets=[listener])
            finally:
                stopped.set()

        thread = Thread(target=run, name="openhound-status", daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                if stopped.wait(0.01) or time.monotonic() >= deadline:
                    raise RuntimeError("OpenHound status listener failed to start")
            yield int(listener.getsockname()[1])
        finally:
            server.should_exit = True
            thread.join(timeout=5)
