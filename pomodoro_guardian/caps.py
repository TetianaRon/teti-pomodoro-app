"""The daily work cap (SPEC §5, §5a).

Pure logic, like `timer.py`: it takes the day, the calendar and the
running totals as arguments and returns a verdict. Nothing here touches
the clock, the network or the screen, so every rule below — including the
ones that only matter on a holiday or at the eleventh hour of a day — is
testable directly.

What the cap does *not* do is switch the app off. SPEC §5's literal
wording ("you're done for the day as far as the app is concerned") would
mean no further work sessions and therefore **no further breaks** — less
protection exactly when you are most tired. Instead, going over the cap
shortens the work interval sharply (`Config.overtime_work_duration`), so
breaks arrive often enough to be a standing nudge to stop, with room to
wrap up or reach for Emergency Mode. Decided with the contributor
2026-08-09.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .state import NON_WORKING, WORKING, AppState

HOUR = 3600
SATURDAY = 5
SUNDAY = 6


@dataclass(frozen=True)
class DayType:
    """Whether today is a working day, and how that was decided."""

    working: bool
    reason: str
    from_calendar: bool = False   # False when the feed could not be consulted

    @property
    def description(self) -> str:
        kind = "working day" if self.working else "day off"
        return f"{kind} ({self.reason})"


def classify_day(
    day: date,
    longest_busy_hours: float | None,
    day_off_block_hours: float,
    override: str | None = None,
) -> DayType:
    """Decide whether `day` is a working day.

    `longest_busy_hours` is the longest contiguous busy stretch from the
    calendar, or **None** when the feed was unavailable. Missing data falls
    back to the day-of-week rule rather than guessing, and says so — a
    network problem must not silently hand you a full 11 hours on a public
    holiday, nor silently take one away.
    """
    if override == WORKING:
        return DayType(True, "override: treated as a working day")
    if override == NON_WORKING:
        return DayType(False, "override: treated as a day off")

    if day.weekday() in (SATURDAY, SUNDAY):
        return DayType(False, "weekend")

    if longest_busy_hours is None:
        return DayType(True, "weekday, calendar unavailable", from_calendar=False)

    if longest_busy_hours >= day_off_block_hours:
        return DayType(
            False,
            f"calendar busy for {longest_busy_hours:.1f}h straight",
            from_calendar=True,
        )
    return DayType(True, "weekday", from_calendar=True)


@dataclass(frozen=True)
class CapStatus:
    """Where the day stands against its cap."""

    day_type: DayType
    base_hours: float
    emergency_hours: float
    worked_seconds: float
    walked_seconds: float = 0.0
    walking_target_seconds: float = 0.0

    @property
    def walking_shortfall_seconds(self) -> float:
        """How much cap the walking goal is currently costing (SPEC §7).

        `max(0, target − walked)`, recalculated live: every minute walked
        raises the ceiling back minute for minute, so going for a walk
        un-blocks you the same day rather than tomorrow.
        """
        return max(0.0, self.walking_target_seconds - self.walked_seconds)

    @property
    def effective_seconds(self) -> float:
        """The cap actually in force: base − walking shortfall + emergency."""
        base = self.base_hours * HOUR - self.walking_shortfall_seconds
        return max(0.0, base) + self.emergency_hours * HOUR

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.effective_seconds - self.worked_seconds)

    @property
    def over(self) -> bool:
        return self.worked_seconds >= self.effective_seconds

    @property
    def overtime_seconds(self) -> float:
        return max(0.0, self.worked_seconds - self.effective_seconds)

    def describe(self) -> str:
        worked = self.worked_seconds / HOUR
        cap = self.effective_seconds / HOUR
        extra = (
            f" (+{self.emergency_hours:.0f}h emergency)"
            if self.emergency_hours
            else ""
        )
        shortfall = self.walking_shortfall_seconds
        if shortfall:
            extra += f" (−{shortfall / 60:.0f} min unwalked)"
        if self.over:
            return (
                f"{worked:.1f}h worked — {self.overtime_seconds / HOUR:.1f}h "
                f"over a {cap:.1f}h cap{extra}"
            )
        return f"{worked:.1f}h of {cap:.1f}h{extra}"


def status(
    state: AppState,
    day_type: DayType,
    working_day_hours: float,
    non_working_day_hours: float,
    walking_target_minutes: float = 0.0,
) -> CapStatus:
    """Combine the day's classification with what has been worked so far."""
    base = working_day_hours if day_type.working else non_working_day_hours
    return CapStatus(
        day_type=day_type,
        base_hours=base,
        emergency_hours=state.emergency_hours_today(),
        worked_seconds=state.worked_today,
        walked_seconds=state.walked_including_current(),
        walking_target_seconds=walking_target_minutes * 60,
    )
