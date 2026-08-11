"""The local history log (SPEC §8).

Two jobs, and the second one shaped the schema as much as the first.

**Seeing your own patterns.** How much you actually work, how often breaks
are skipped, whether the walking target is met — the numbers that say
whether 11h, 3h and 60 minutes were the right guesses.

**Catching slow bugs.** The failures this app is prone to are not crashes;
they are accounting mistakes that need days to surface — a budget that
doesn't roll over, a day classified wrongly, a total that drifts. Tonight's
UTC-versus-local bug was exactly that shape: right every morning, wrong
every evening. So the log records the app's own *decisions* — how each day
was classified, what cap it computed, when it started and stopped — and not
only what the user did. A wrong answer is then auditable after the fact
instead of being gone the moment the day rolls over.

SQLite rather than the JSON in `state.py`: that file is deliberately just
today's counters, rewritten constantly. This is append-only and meant to
be queried across weeks.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .settings import default_path

SCHEMA_VERSION = 1

# Discrete things worth remembering. Kept as plain strings so a row stays
# readable in any SQLite browser without consulting the source.
APP_STARTED = "app_started"
APP_STOPPED = "app_stopped"
BREAK_TAKEN = "break_taken"
BREAK_SKIPPED = "break_skipped"
# A break whose time ran out while she carried on working through it. Only
# reachable when the lock is not blocking input — but then it is the
# difference between a log that means something and one that flatters:
# without it every break reads as taken, which is exactly the sort of
# optimistic accounting this file exists to catch.
BREAK_IGNORED = "break_ignored"
# Why a long break did or did not arrive. Recorded because the count towards
# it is the one piece of app state with no visible trace of its own: a long
# break that never fires looks identical to one that was never due.
CYCLES_RESET = "cycles_reset"
CYCLES_RESUMED = "cycles_resumed"
EXCLUDED = "excluded"
EMERGENCY_USED = "emergency_used"
FOCUS_STARTED = "focus_started"
FOCUS_ENDED = "focus_ended"
WALK_ENDED = "walk_ended"
DAY_CLASSIFIED = "day_classified"
CAP_REACHED = "cap_reached"
OVERRIDE_SET = "override_set"
SNAPSHOT = "snapshot"


def history_path(settings_file: Path | None = None) -> Path:
    base = (settings_file or default_path()).parent
    return base / "history.db"


@dataclass(frozen=True)
class DaySummary:
    day: date
    worked_seconds: float = 0.0
    walked_seconds: float = 0.0
    breaks_taken: int = 0
    breaks_skipped: int = 0
    breaks_ignored: int = 0
    skipped_seconds: float = 0.0
    emergency_used: int = 0
    focus_used: int = 0
    day_type: str = ""

    def describe(self) -> str:
        parts = [
            f"{self.day}  {self.worked_seconds / 3600:.1f}h worked",
            f"{self.walked_seconds / 60:.0f} min walked",
            f"{self.breaks_taken} breaks",
        ]
        if self.breaks_skipped:
            parts.append(
                f"{self.breaks_skipped} skipped "
                f"({self.skipped_seconds / 60:.0f} min)"
            )
        # Worth its own word rather than folding into "skipped": a skip was
        # bought from a budget and consciously chosen; this is a break that
        # was simply worked through.
        if self.breaks_ignored:
            parts.append(f"{self.breaks_ignored} worked through")
        if self.emergency_used:
            parts.append(f"{self.emergency_used}x emergency")
        if self.focus_used:
            parts.append("focus")
        if self.day_type:
            parts.append(self.day_type)
        return "  ·  ".join(parts)


class History:
    """Append-only event log, plus periodic totals.

    Every write is wrapped so a logging failure can never take the app
    down: a lost row is a footnote, a crashed break enforcer is not.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or history_path()
        self.enabled = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as db:
                self._create(db)
        except sqlite3.Error:
            self.enabled = False

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _create(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                at        TEXT NOT NULL,   -- ISO 8601, UTC
                local_day TEXT NOT NULL,   -- YYYY-MM-DD, local: how days group
                kind      TEXT NOT NULL,
                seconds   REAL,
                detail    TEXT
            );
            CREATE INDEX IF NOT EXISTS events_day ON events(local_day);
            CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
            """
        )
        db.commit()

    # -- writing ------------------------------------------------------

    def record(
        self,
        kind: str,
        seconds: float | None = None,
        detail: str | dict | None = None,
        when: datetime | None = None,
    ) -> None:
        if not self.enabled:
            return
        moment = when or datetime.now(timezone.utc)
        # Grouped by *local* day, because "how long did I work on Tuesday"
        # means the Tuesday you lived, not the one UTC was having.
        local_day = moment.astimezone().date().isoformat()
        if isinstance(detail, dict):
            detail = json.dumps(detail, sort_keys=True)
        try:
            with closing(self._connect()) as db:
                db.execute(
                    "INSERT INTO events (at, local_day, kind, seconds, detail)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (moment.isoformat(), local_day, kind, seconds, detail),
                )
                db.commit()
        except sqlite3.Error:
            pass   # never let bookkeeping break enforcement

    def snapshot(self, worked: float, walked: float, detail: str = "") -> None:
        """Periodic totals, so a day survives a crash or a restart."""
        self.record(SNAPSHOT, seconds=worked, detail=json.dumps(
            {"worked": round(worked), "walked": round(walked), "note": detail},
            sort_keys=True,
        ))

    # -- reading ------------------------------------------------------

    def summary(self, day: date) -> DaySummary:
        """One day, rolled up."""
        if not self.enabled:
            return DaySummary(day)
        key = day.isoformat()
        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    "SELECT kind, seconds, detail FROM events"
                    " WHERE local_day = ? ORDER BY id",
                    (key,),
                ).fetchall()
        except sqlite3.Error:
            return DaySummary(day)

        worked = walked = skipped_seconds = 0.0
        taken = skipped = ignored = emergency = focus = 0
        day_type = ""
        for kind, seconds, detail in rows:
            if kind == SNAPSHOT:
                # Last one wins: totals are cumulative, not incremental.
                worked = seconds or worked
                try:
                    walked = json.loads(detail or "{}").get("walked", walked)
                except ValueError:
                    pass
            elif kind == BREAK_TAKEN:
                taken += 1
            elif kind == BREAK_SKIPPED:
                skipped += 1
                skipped_seconds += seconds or 0
            elif kind == BREAK_IGNORED:
                ignored += 1
            elif kind == WALK_ENDED:
                walked = max(walked, 0)
            elif kind == EMERGENCY_USED:
                emergency += 1
            elif kind == FOCUS_STARTED:
                focus += 1
            elif kind == DAY_CLASSIFIED:
                day_type = detail or ""
        return DaySummary(
            day=day,
            worked_seconds=worked,
            walked_seconds=walked,
            breaks_taken=taken,
            breaks_skipped=skipped,
            breaks_ignored=ignored,
            skipped_seconds=skipped_seconds,
            emergency_used=emergency,
            focus_used=focus,
            day_type=day_type,
        )

    def recent_days(self, count: int = 7, today: date | None = None):
        """Summaries for the last `count` days, newest first."""
        end = today or date.today()
        return [self.summary(end - timedelta(days=n)) for n in range(count)]

    def tail(self, limit: int = 40, day: date | None = None):
        """Raw events, newest first — the view for chasing a bug."""
        if not self.enabled:
            return []
        try:
            with closing(self._connect()) as db:
                if day is None:
                    return db.execute(
                        "SELECT at, kind, seconds, detail FROM events"
                        " ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return db.execute(
                    "SELECT at, kind, seconds, detail FROM events"
                    " WHERE local_day = ? ORDER BY id DESC LIMIT ?",
                    (day.isoformat(), limit),
                ).fetchall()
        except sqlite3.Error:
            return []
