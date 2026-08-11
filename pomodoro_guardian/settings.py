"""Persistent settings, and where they live on disk.

`Config` (config.py) is what the timer engine consumes: seconds, no
opinions about anything else. `Settings` wraps it with the app-level
values later phases read — caps, the walking target, the calendar URL —
and round-trips the lot through JSON.

The file is written in **minutes and hours**, not seconds, because it is
meant to be hand-editable. Conversion happens here so the engine never
has to think about units.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from .config import MINUTE, Config

SCHEMA_VERSION = 1
APP_DIR_NAME = "PomodoroGuardian"

HOUR = 60 * MINUTE


def default_path() -> Path:
    """%APPDATA%\\PomodoroGuardian\\config.json, or an XDG-ish path elsewhere.

    Deliberately not next to the executable: config beside the binary
    breaks on upgrade and is unwritable under Program Files.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME / "config.json"
    return Path.home() / ".config" / "pomodoro-guardian" / "config.json"


@dataclass(frozen=True)
class Settings:
    """Everything the app remembers between runs."""

    config: Config = Config()

    # Phase 3 — the free/busy feed. None means "not set up yet"; the app
    # runs fine without it, just with no calendar awareness.
    calendar_url: str | None = None

    # Phase 4 — daily caps (docs/SPEC.md §5), in hours.
    working_day_cap_hours: float = 11.0
    non_working_day_cap_hours: float = 3.0
    # A contiguous busy block at least this long marks a non-working day.
    # Measured: ordinary weekdays never exceed 1.5h, holidays run 8.5-12.5h.
    day_off_block_hours: float = 6.0
    # Breaks are held off this long *before* a meeting as well as during it,
    # leaving room to prepare. A lock landing three minutes before a call is
    # worse than one during it: no time to get ready, and no way back.
    meeting_lead_minutes: float = 10.0
    emergency_hours_per_week: float = 3.0
    override_raises_per_month: int = 2

    # Phase 5 — Focus Mode (docs/SPEC.md §6).
    focus_max_hours: float = 2.0
    focus_uses_per_day: int = 1

    # Chimes for the start and end of a break. Empty means "look for
    # assets/break-start.* and assets/break-end.*", so dropping a file in
    # is enough; missing files are simply silent.
    break_start_sound: str = ""
    break_end_sound: str = ""

    # Walking goal (docs/SPEC.md §7), in minutes.
    walking_target_minutes: float = 60.0
    # When to ask. Tracking is a manual toggle, so the app's job is to
    # prompt at sensible moments rather than to detect anything.
    walking_reminder_times: tuple[str, ...] = ("11:40", "15:20")

    # -- serialisation ------------------------------------------------

    def to_dict(self) -> dict:
        c = self.config
        return {
            "version": SCHEMA_VERSION,
            "calendar_url": self.calendar_url,
            "rhythm_minutes": {
                "work": c.work_duration / MINUTE,
                "short_break": c.short_break_duration / MINUTE,
                "long_break": c.long_break_duration / MINUTE,
                "warning_lead": c.warning_lead / MINUTE,
                "long_break_every": c.long_break_every,
            },
            "detection": {
                "input_gap_seconds": c.input_gap,
                "start_threshold_minutes": c.start_threshold / MINUTE,
                "idle_pause_minutes": c.idle_pause_after / MINUTE,
                "idle_reset_minutes": c.idle_reset_after / MINUTE,
            },
            "exclusions": {
                # Time on a call counts towards the daily cap. Turn this off
                # if a microphone held open by some app starts inflating the
                # day — the risk this setting exists for.
                "count_as_work": c.count_exclusions_as_work,
            },
            "lock": {
                "block_input": c.block_input,
                "safety_unlock": c.safety_unlock,
                "safety_unlock_hold_seconds": c.safety_unlock_hold,
                "banner_alpha": c.banner_alpha,
                "banner_alpha_hover": c.banner_alpha_hover,
                "break_ignored_fraction": c.break_ignored_fraction,
            },
            "caps": {
                "working_day_hours": self.working_day_cap_hours,
                "non_working_day_hours": self.non_working_day_cap_hours,
                "day_off_block_hours": self.day_off_block_hours,
                "meeting_lead_minutes": self.meeting_lead_minutes,
                "emergency_hours_per_week": self.emergency_hours_per_week,
                "override_raises_per_month": self.override_raises_per_month,
            },
            "focus": {
                "max_hours": self.focus_max_hours,
                "uses_per_day": self.focus_uses_per_day,
            },
            "walking": {
                "target_minutes": self.walking_target_minutes,
                "reminder_times": list(self.walking_reminder_times),
            },
            "sounds": {
                "break_start": self.break_start_sound,
                "break_end": self.break_end_sound,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """Build from stored JSON, falling back to defaults field by field.

        Every lookup is defensive: a hand-edited file with a missing or
        malformed key should cost you that one value, not the whole file.
        """
        base = Settings()
        rhythm = _section(data, "rhythm_minutes")
        detection = _section(data, "detection")
        lock = _section(data, "lock")
        caps = _section(data, "caps")
        exclusions = _section(data, "exclusions")
        focus = _section(data, "focus")
        walking = _section(data, "walking")
        d = base.config

        config = replace(
            d,
            work_duration=_num(rhythm, "work", d.work_duration / MINUTE) * MINUTE,
            short_break_duration=_num(
                rhythm, "short_break", d.short_break_duration / MINUTE) * MINUTE,
            long_break_duration=_num(
                rhythm, "long_break", d.long_break_duration / MINUTE) * MINUTE,
            warning_lead=_num(
                rhythm, "warning_lead", d.warning_lead / MINUTE) * MINUTE,
            long_break_every=int(
                _num(rhythm, "long_break_every", d.long_break_every)),
            input_gap=_num(detection, "input_gap_seconds", d.input_gap),
            start_threshold=_num(
                detection, "start_threshold_minutes",
                d.start_threshold / MINUTE) * MINUTE,
            idle_pause_after=_num(
                detection, "idle_pause_minutes",
                d.idle_pause_after / MINUTE) * MINUTE,
            idle_reset_after=_num(
                detection, "idle_reset_minutes",
                d.idle_reset_after / MINUTE) * MINUTE,
            count_exclusions_as_work=bool(
                exclusions.get(
                    "count_as_work", d.count_exclusions_as_work)),
            block_input=bool(
                lock.get("block_input", d.block_input)),
            break_ignored_fraction=_num(
                lock, "break_ignored_fraction", d.break_ignored_fraction),
            safety_unlock=bool(
                lock.get("safety_unlock", d.safety_unlock)),
            safety_unlock_hold=_num(
                lock, "safety_unlock_hold_seconds", d.safety_unlock_hold),
            banner_alpha=_num(lock, "banner_alpha", d.banner_alpha),
            banner_alpha_hover=_num(
                lock, "banner_alpha_hover", d.banner_alpha_hover),
        )

        url = data.get("calendar_url")
        return Settings(
            config=config,
            calendar_url=(url.strip() or None) if isinstance(url, str) else None,
            working_day_cap_hours=_num(
                caps, "working_day_hours", base.working_day_cap_hours),
            non_working_day_cap_hours=_num(
                caps, "non_working_day_hours", base.non_working_day_cap_hours),
            day_off_block_hours=_num(
                caps, "day_off_block_hours", base.day_off_block_hours),
            meeting_lead_minutes=_num(
                caps, "meeting_lead_minutes", base.meeting_lead_minutes),
            emergency_hours_per_week=_num(
                caps, "emergency_hours_per_week", base.emergency_hours_per_week),
            override_raises_per_month=int(_num(
                caps, "override_raises_per_month",
                base.override_raises_per_month)),
            focus_max_hours=_num(focus, "max_hours", base.focus_max_hours),
            focus_uses_per_day=int(
                _num(focus, "uses_per_day", base.focus_uses_per_day)),
            walking_target_minutes=_num(
                walking, "target_minutes", base.walking_target_minutes),
            walking_reminder_times=_times(
                walking.get("reminder_times"), base.walking_reminder_times),
            break_start_sound=_text(_section(data, "sounds"), "break_start"),
            break_end_sound=_text(_section(data, "sounds"), "break_end"),
        )


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _text(section: dict, key: str) -> str:
    value = section.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _times(raw, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Reminder times, keeping the defaults if the list is unusable."""
    from .walking import parse_times

    if not isinstance(raw, list):
        return fallback
    parsed = parse_times(raw)
    return tuple(parsed) if parsed else ()


def _num(section: dict, key: str, fallback: float) -> float:
    """A number from JSON, or the default if it's missing or unusable."""
    value = section.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value)


# -- file access ------------------------------------------------------


def exists(path: Path | None = None) -> bool:
    return (path or default_path()).is_file()


def load(path: Path | None = None) -> Settings:
    """Read settings, or return defaults if there's nothing usable there.

    A corrupt file must never stop the app starting — losing your break
    enforcement because of a stray comma would be the wrong trade.
    """
    target = path or default_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    return Settings.from_dict(data)


def save(settings: Settings, path: Path | None = None) -> Path:
    """Write settings, creating the directory. Returns where it landed.

    Writes to a temporary file first and replaces, so an interrupted save
    can't leave a half-written config behind.
    """
    target = path or default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(settings.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, target)
    return target
