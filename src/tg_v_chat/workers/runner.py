from __future__ import annotations

from collections.abc import Callable


class WorkerRunner:
    def __init__(self, workers: list[Callable[[], None]] | None = None):
        self._workers = list(workers or [])

    def add_worker(self, worker: Callable[[], None]) -> None:
        self._workers.append(worker)

    def run_once(self) -> None:
        if not self._workers:
            raise RuntimeError("no workers configured")
        for worker in self._workers:
            worker()
