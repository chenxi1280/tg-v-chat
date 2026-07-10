from __future__ import annotations

from collections.abc import Callable

WORKER_INTERVAL_SECONDS = 30


class WorkerRunner:
    def __init__(self, workers: list[Callable[[], None]] | None = None, *, interval_seconds: int = WORKER_INTERVAL_SECONDS):
        self._workers = list(workers or [])
        self._interval_seconds = interval_seconds

    def add_worker(self, worker: Callable[[], None]) -> None:
        self._workers.append(worker)

    def run_once(self) -> None:
        if not self._workers:
            raise RuntimeError("no workers configured")
        for worker in self._workers:
            worker()

    def run_forever(self, *, should_stop: Callable[[], bool], sleep: Callable[[int], None]) -> None:
        while not should_stop():
            self.run_once()
            if not should_stop():
                sleep(self._interval_seconds)
