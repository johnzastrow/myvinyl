"""Background work: one Discogs lookup queue and the periodic value refresh.

All Discogs calls go through a single worker thread, so lookups run one at a time and
the API rate limit is shared fairly between new albums, refreshes, and imports.
"""

import logging
import queue
import threading
from collections.abc import Callable

log = logging.getLogger("myvinyl.lookups")


class LookupQueue:
    """Runs `process(album_id)` for each submitted album, one at a time, in order.

    With inline=True (used by tests), work runs immediately in the caller's thread.
    """

    def __init__(self, process: Callable[[int], None], inline: bool = False) -> None:
        self._process = process
        self._inline = inline
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._queued: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(self, album_id: int) -> bool:
        """Queue an album; returns False if it is already waiting."""
        if self._inline:
            self._run(album_id)
            return True
        with self._lock:
            if album_id in self._queued:
                return False
            self._queued.add(album_id)
        self._queue.put(album_id)
        return True

    def pending(self) -> int:
        with self._lock:
            return len(self._queued)

    def start(self) -> None:
        if self._inline or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="discogs-lookups", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._queue.put(None)
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while True:
            album_id = self._queue.get()
            if album_id is None:
                return
            with self._lock:
                self._queued.discard(album_id)
            self._run(album_id)

    def _run(self, album_id: int) -> None:
        try:
            self._process(album_id)
        except Exception:  # never let one album kill the worker
            log.exception("lookup failed for album %s", album_id)


class RefreshScheduler:
    """Every `interval` seconds, re-queues albums whose Discogs data is older than N days.

    A small batch per run spreads the API load out instead of refreshing everything at once.
    """

    def __init__(self, tick: Callable[[], int], interval_seconds: float = 3600) -> None:
        self._tick = tick
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="value-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        if self._stop.wait(60):  # let startup and new-album lookups go first
            return
        while True:
            try:
                queued = self._tick()
                if queued:
                    log.info("queued %d albums for scheduled value refresh", queued)
            except Exception:
                log.exception("scheduled refresh failed")
            if self._stop.wait(self._interval):
                return
