"""Keeps the calendar feed fresh in the background (SPEC §4A).

Fetching on every tick would hammer the feed for no benefit — busy blocks
change on the order of minutes, not seconds — so the feed is refreshed on
a timer in a daemon thread and answered from cache.

**Failure direction matters here.** If the feed cannot be reached, or the
cache has gone stale, meetings stop being detected and breaks enforce
normally. The app's job is to enforce breaks; a network problem must not
silently switch that off. The cost of failing this way is an unwanted lock
during a meeting, which the custom skip and the hold-Escape gesture both
answer. The cost of failing the other way is no breaks at all, silently,
for as long as the feed is down.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .calendar_feed import BusyBlock, FeedError, Schedule, fetch

REFRESH_SECONDS = 15 * 60
# Past this, cached blocks are no longer trusted: a meeting cancelled or
# moved hours ago would otherwise keep suppressing breaks.
MAX_AGE_SECONDS = 6 * 60 * 60


class CalendarWatcher:
    """Holds the current schedule, refreshed on a timer."""

    def __init__(
        self,
        url: str | None,
        refresh_seconds: float = REFRESH_SECONDS,
        max_age_seconds: float = MAX_AGE_SECONDS,
        day_off_hours: float = 6.0,
    ) -> None:
        self._url = url
        self._refresh = refresh_seconds
        self._max_age = max_age_seconds
        self._day_off_hours = day_off_hours

        self._lock = threading.Lock()
        self._schedule: Schedule | None = None
        self._fetched_at: float | None = None
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Begin refreshing. Harmless — and a no-op — with no URL configured."""
        if self._url is None or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def refresh_now(self) -> bool:
        """Fetch once, synchronously. Returns True if it worked."""
        if self._url is None:
            return False
        try:
            schedule = Schedule.from_feed(fetch(self._url))
        except FeedError as exc:
            with self._lock:
                self._error = str(exc)
            return False
        with self._lock:
            self._schedule = schedule
            self._fetched_at = time.monotonic()
            self._error = None
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_now()
            # Wait on the event rather than sleeping, so stop() is immediate
            # even with a 15-minute refresh interval.
            self._stop.wait(self._refresh)

    # -- queries ------------------------------------------------------

    def meeting_now(self, when: datetime | None = None) -> BusyBlock | None:
        """The meeting in progress, or None — including when data is missing."""
        moment = when or datetime.now(timezone.utc)
        with self._lock:
            schedule = self._schedule
            fetched_at = self._fetched_at
        if schedule is None or fetched_at is None:
            return None
        if time.monotonic() - fetched_at > self._max_age:
            return None  # too old to trust; enforce breaks normally
        return schedule.meeting_at(moment, self._day_off_hours)

    @property
    def configured(self) -> bool:
        return self._url is not None

    def status(self) -> str:
        """One line describing the feed, for --exclusions and logging."""
        if self._url is None:
            return "no calendar configured"
        with self._lock:
            schedule = self._schedule
            fetched_at = self._fetched_at
            error = self._error
        if error and schedule is None:
            return f"calendar unavailable ({error})"
        if schedule is None or fetched_at is None:
            return "calendar not fetched yet"
        age = time.monotonic() - fetched_at
        stale = "  [STALE — ignoring]" if age > self._max_age else ""
        suffix = f", last refresh failed ({error})" if error else ""
        return (
            f"{len(schedule.blocks)} busy blocks, fetched "
            f"{age / 60:.0f} min ago{stale}{suffix}"
        )
