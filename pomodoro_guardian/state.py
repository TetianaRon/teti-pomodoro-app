"""Today's running totals — the budgets that reset (SPEC §4B).

Kept apart from `settings.py` on purpose. Settings are decisions you made
and expect to persist; this is bookkeeping the app maintains and rolls
over. Mixing them would mean a corrupt tally could cost you your
configuration, and a settings edit could plausibly reset your budgets.

Stored next to the settings file. Phase 7 adds a proper SQLite history
log; this is the small amount of state the caps need before then, and its
JSON shape is deliberately simple enough to migrate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from .settings import default_path

SCHEMA_VERSION = 1


def state_path(settings_file: Path | None = None) -> Path:
    """`state.json`, alongside whichever settings file is in use."""
    base = settings_file or default_path()
    return base.parent / "state.json"


@dataclass(frozen=True)
class DailyState:
    """Counters for a single day. Rolls over automatically on a new date."""

    day: date
    custom_skip_used: float = 0.0   # seconds of custom skip spent today

    @classmethod
    def for_today(cls, today: date | None = None) -> "DailyState":
        return cls(day=today or date.today())

    def rolled_to(self, today: date) -> "DailyState":
        """This state if it is still today's, otherwise a fresh one."""
        return self if self.day == today else DailyState(day=today)

    # -- custom skip budget (SPEC §4B) --------------------------------

    def skip_remaining(self, daily_budget: float) -> float:
        return max(0.0, daily_budget - self.custom_skip_used)

    def can_skip(self, seconds: float, daily_budget: float) -> bool:
        """Whole skips only — a partial 20-minute skip would be a lie."""
        return seconds <= self.skip_remaining(daily_budget)

    def with_skip(self, seconds: float) -> "DailyState":
        return replace(self, custom_skip_used=self.custom_skip_used + seconds)

    # -- serialisation ------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "day": self.day.isoformat(),
            "custom_skip_used_seconds": self.custom_skip_used,
        }

    @classmethod
    def from_dict(cls, data: dict, today: date | None = None) -> "DailyState":
        today = today or date.today()
        try:
            day = date.fromisoformat(str(data.get("day", "")))
        except ValueError:
            return cls(day=today)
        if day != today:
            # Yesterday's tallies are spent; today starts clean.
            return cls(day=today)
        used = data.get("custom_skip_used_seconds", 0.0)
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            used = 0.0
        return cls(day=day, custom_skip_used=max(0.0, float(used)))


def load(path: Path | None = None, today: date | None = None) -> DailyState:
    """Read today's state, or a fresh one if there's nothing usable.

    Like settings, a damaged file must never stop the app: losing a skip
    tally is trivial next to losing break enforcement.
    """
    target = path or state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DailyState.for_today(today)
    if not isinstance(data, dict):
        return DailyState.for_today(today)
    return DailyState.from_dict(data, today)


def save(state: DailyState, path: Path | None = None) -> Path:
    """Write atomically, so an interrupted save can't corrupt the tally."""
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return target
