"""Background work: one job queue for Discogs calls, plus periodic tasks.

All Discogs calls go through a single worker thread, so jobs run one at a time and the
API rate limit is shared fairly between new albums, refreshes, imports, wishlist price
checks, and cover downloads.
"""

import logging
import queue
import threading
from collections.abc import Callable, Hashable

log = logging.getLogger("myvinyl.lookups")


class JobQueue:
    """Runs `process(job)` for each submitted job, one at a time, in order.

    Jobs are hashable keys such as ("album", 5); a job already waiting is not queued twice.
    With inline=True (used by tests), work runs immediately in the caller's thread.
    """

    def __init__(self, process: Callable[[Hashable], None], inline: bool = False) -> None:
        self._process = process
        self._inline = inline
        self._queue: queue.Queue = queue.Queue()
        self._queued: set = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(self, job: Hashable) -> bool:
        """Queue a job; returns False if it is already waiting."""
        if self._inline:
            self._run(job)
            return True
        with self._lock:
            if job in self._queued:
                return False
            self._queued.add(job)
        self._queue.put(job)
        return True

    def pending(self, kind: str | None = None) -> int:
        with self._lock:
            if kind is None:
                return len(self._queued)
            return sum(1 for j in self._queued if isinstance(j, tuple) and j[0] == kind)

    def start(self) -> None:
        if self._inline or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="discogs-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._queue.put(None)
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            with self._lock:
                self._queued.discard(job)
            self._run(job)

    def _run(self, job: Hashable) -> None:
        try:
            self._process(job)
        except Exception:  # never let one job kill the worker
            log.exception("background job failed: %r", job)


class PeriodicTask:
    """Calls `tick()` every `interval` seconds on a daemon thread, after an initial delay."""

    def __init__(
        self,
        name: str,
        tick: Callable[[], object],
        interval_seconds: float,
        initial_delay: float = 60,
    ) -> None:
        self._name = name
        self._tick = tick
        self._interval = interval_seconds
        self._initial_delay = initial_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        if self._stop.wait(self._initial_delay):
            return
        while True:
            try:
                result = self._tick()
                if result:
                    log.info("%s: %s", self._name, result)
            except Exception:
                log.exception("%s failed", self._name)
            if self._stop.wait(self._interval):
                return
