"""Running totals the app maintains — the budgets that reset (SPEC §4B, §5, §5a).

Kept apart from `settings.py` on purpose. Settings are decisions you made
and expect to persist; this is bookkeeping the app keeps and rolls over.
Mixing them would mean a corrupt tally could cost you your configuration,
and a settings edit could plausibly reset your budgets.

Three different clocks live here, and they roll over differently:

* **daily** — work time, custom-skip minutes, the day-type override
* **rolling week** — Emergency Mode's 3h pool (SPEC §5), a true rolling
  7 days rather than a calendar week, so it cannot be gamed by working
  across a Sunday boundary
* **calendar month** — the day-type override's raise budget (SPEC §5a)

Stored next to the settings file. Phase 7 adds a proper SQLite history
log; this is the state the caps need before then, and its JSON shape is
deliberately simple enough to migrate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .settings import default_path

SCHEMA_VERSION = 2

#: Day-type override values (SPEC §5a).
WORKING = "working"
NON_WORKING = "non_working"


def state_path(settings_file: Path | None = None) -> Path:
    """`state.json`, alongside whichever settings file is in use."""
    base = settings_file or default_path()
    return base.parent / "state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Grant:
    """One Emergency Mode activation (SPEC §5)."""

    at: datetime
    hours: float = 1.0


@dataclass(frozen=True)
class AppState:
    """Everything the caps need to remember."""

    day: date
    # --- daily ---
    custom_skip_used: float = 0.0      # seconds (SPEC §4B)
    worked_today: float = 0.0          # seconds of tracked work
    day_type_override: str | None = None   # WORKING / NON_WORKING / None
    walked_today: float = 0.0          # seconds on the treadmill (SPEC §7)
    # Set while a walk is in progress, so an app restart mid-walk doesn't
    # lose the session. Stored in UTC.
    walk_started_at: datetime | None = None
    # Reminder times already shown today, as "HH:MM", so a prompt is not
    # repeated every tick once its minute arrives.
    walk_prompts_done: tuple[str, ...] = field(default=())
    # --- rolling ---
    emergency_grants: tuple[Grant, ...] = field(default=())
    override_raises: tuple[date, ...] = field(default=())

    @classmethod
    def for_today(cls, today: date | None = None) -> "AppState":
        return cls(day=today or date.today())

    def rolled_to(self, today: date) -> "AppState":
        """This state if it is still today's, otherwise a fresh day.

        Rolling budgets survive the day change; only the daily counters and
        the override reset. The override is deliberately today-only
        (SPEC §5a), so it must not carry over.
        """
        if self.day == today:
            return self
        return AppState(
            day=today,
            emergency_grants=self.emergency_grants,
            override_raises=self.override_raises,
            # A walk running across midnight keeps running; stopping it
            # silently would lose the time actually spent.
            walk_started_at=self.walk_started_at,
        )

    # -- custom skip budget (SPEC §4B) --------------------------------

    def skip_remaining(self, daily_budget: float) -> float:
        return max(0.0, daily_budget - self.custom_skip_used)

    def can_skip(self, seconds: float, daily_budget: float) -> bool:
        """Whole skips only — a partial 20-minute skip would be a lie."""
        return seconds <= self.skip_remaining(daily_budget)

    def with_skip(self, seconds: float) -> "AppState":
        return replace(self, custom_skip_used=self.custom_skip_used + seconds)

    # -- work tracking (SPEC §5) --------------------------------------

    def with_work(self, seconds: float) -> "AppState":
        if seconds <= 0:
            return self
        return replace(self, worked_today=self.worked_today + seconds)

    # -- walking (SPEC §7) --------------------------------------------

    @property
    def walking(self) -> bool:
        return self.walk_started_at is not None

    def walked_including_current(self, now: datetime | None = None) -> float:
        """Total walked today, counting a walk still in progress."""
        if self.walk_started_at is None:
            return self.walked_today
        elapsed = ((now or _now()) - self.walk_started_at).total_seconds()
        return self.walked_today + max(0.0, elapsed)

    def start_walk(self, now: datetime | None = None) -> "AppState":
        if self.walking:
            return self
        return replace(self, walk_started_at=now or _now())

    def stop_walk(self, now: datetime | None = None) -> "AppState":
        if self.walk_started_at is None:
            return self
        elapsed = ((now or _now()) - self.walk_started_at).total_seconds()
        return replace(
            self,
            walked_today=self.walked_today + max(0.0, elapsed),
            walk_started_at=None,
        )

    def with_prompt_shown(self, label: str) -> "AppState":
        if label in self.walk_prompts_done:
            return self
        return replace(self, walk_prompts_done=self.walk_prompts_done + (label,))

    # -- Emergency Mode (SPEC §5) -------------------------------------

    def emergency_hours_used(self, now: datetime | None = None) -> float:
        """Hours granted in the last rolling 7 days."""
        cutoff = (now or _now()) - timedelta(days=7)
        return sum(g.hours for g in self.emergency_grants if g.at > cutoff)

    def emergency_remaining(
        self, weekly_budget: float, now: datetime | None = None
    ) -> float:
        return max(0.0, weekly_budget - self.emergency_hours_used(now))

    def can_use_emergency(
        self, hours: float, weekly_budget: float, now: datetime | None = None
    ) -> bool:
        return hours <= self.emergency_remaining(weekly_budget, now)

    def with_emergency(
        self, hours: float = 1.0, now: datetime | None = None
    ) -> "AppState":
        moment = now or _now()
        kept = tuple(
            g for g in self.emergency_grants if g.at > moment - timedelta(days=30)
        )
        return replace(self, emergency_grants=kept + (Grant(moment, hours),))

    def emergency_hours_today(self) -> float:
        """Extra hours in force right now, added on top of the base cap.

        Grants are stored in UTC but `day` is a *local* date, so the
        comparison has to convert. Comparing the UTC date directly worked
        every morning and silently failed every evening: at 20:36 in
        Toronto it is already tomorrow in UTC, so a grant just made would
        count towards a day that has not started.
        """
        return sum(
            g.hours
            for g in self.emergency_grants
            if g.at.astimezone().date() == self.day
        )

    # -- day-type override (SPEC §5a) ---------------------------------

    def raises_used_this_month(self, today: date | None = None) -> int:
        day = today or self.day
        return sum(
            1 for d in self.override_raises
            if d.year == day.year and d.month == day.month
        )

    def can_raise(self, monthly_budget: int, today: date | None = None) -> bool:
        return self.raises_used_this_month(today) < monthly_budget

    def with_override(self, kind: str | None) -> "AppState":
        """Set the override. Raising spends one of the monthly budget.

        A spent raise is not refunded when the override is cleared: without
        that, you could raise in the morning, work the long day and clear it
        at night, and the budget would mean nothing (SPEC §5a).
        """
        if kind != WORKING:
            return replace(self, day_type_override=kind)
        return replace(
            self,
            day_type_override=WORKING,
            override_raises=self.override_raises + (self.day,),
        )

    # -- serialisation ------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "day": self.day.isoformat(),
            "custom_skip_used_seconds": self.custom_skip_used,
            "worked_today_seconds": self.worked_today,
            "day_type_override": self.day_type_override,
            "walked_today_seconds": self.walked_today,
            "walk_started_at": (
                self.walk_started_at.isoformat() if self.walk_started_at else None
            ),
            "walk_prompts_done": list(self.walk_prompts_done),
            "emergency_grants": [
                {"at": g.at.isoformat(), "hours": g.hours}
                for g in self.emergency_grants
            ],
            "override_raises": [d.isoformat() for d in self.override_raises],
        }

    @classmethod
    def from_dict(cls, data: dict, today: date | None = None) -> "AppState":
        today = today or date.today()
        grants = tuple(_grants(data.get("emergency_grants")))
        raises = tuple(_dates(data.get("override_raises")))
        try:
            day = date.fromisoformat(str(data.get("day", "")))
        except ValueError:
            return cls(day=today, emergency_grants=grants, override_raises=raises)

        stored = cls(
            day=day,
            custom_skip_used=_seconds(data, "custom_skip_used_seconds"),
            worked_today=_seconds(data, "worked_today_seconds"),
            day_type_override=_override(data.get("day_type_override")),
            walked_today=_seconds(data, "walked_today_seconds"),
            walk_started_at=_moment(data.get("walk_started_at")),
            walk_prompts_done=tuple(
                str(x) for x in data.get("walk_prompts_done", [])
                if isinstance(x, str)
            ),
            emergency_grants=grants,
            override_raises=raises,
        )
        # Yesterday's daily tallies are spent; the rolling ones are not.
        return stored.rolled_to(today)


def _seconds(data: dict, key: str) -> float:
    value = data.get(key, 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value))


def _override(value) -> str | None:
    return value if value in (WORKING, NON_WORKING) else None


def _moment(value) -> datetime | None:
    """A stored timestamp, or None if it is missing or unparseable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _grants(raw):
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            at = datetime.fromisoformat(str(item.get("at")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        hours = item.get("hours", 1.0)
        if isinstance(hours, bool) or not isinstance(hours, (int, float)):
            hours = 1.0
        yield Grant(at, float(hours))


def _dates(raw):
    if not isinstance(raw, list):
        return
    for item in raw:
        try:
            yield date.fromisoformat(str(item))
        except ValueError:
            continue


# -- file access ------------------------------------------------------


def load(path: Path | None = None, today: date | None = None) -> AppState:
    """Read state, or a fresh one if there's nothing usable there.

    Like settings, a damaged file must never stop the app: losing a tally
    is trivial next to losing break enforcement.
    """
    target = path or state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppState.for_today(today)
    if not isinstance(data, dict):
        return AppState.for_today(today)
    return AppState.from_dict(data, today)


def save(state: AppState, path: Path | None = None) -> Path:
    """Write atomically, so an interrupted save can't corrupt the tally."""
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return target


#: Kept so Phase 3's code and tests keep working after the rename.
DailyState = AppState
