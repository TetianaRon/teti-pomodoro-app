"""Tests for the walking goal (SPEC §7): tracking, prompts and the cap."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from pomodoro_guardian.caps import HOUR, classify_day, status
from pomodoro_guardian.state import AppState, load, save
from pomodoro_guardian.walking import due_prompt, parse_times

MONDAY = date(2026, 8, 10)
NOON = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


# -- reminder times ---------------------------------------------------


def test_times_are_normalised_and_sorted():
    assert parse_times(["15:20", "11:40", "09:05"]) == ["09:05", "11:40", "15:20"]


def test_duplicate_times_collapse():
    assert parse_times(["11:40", "11:40"]) == ["11:40"]


@pytest.mark.parametrize("junk", ["25:00", "banana", "", "11:60", 1140, None])
def test_unusable_times_are_dropped_not_fatal(junk):
    assert parse_times([junk, "11:40"]) == ["11:40"]


def test_a_reminder_fires_at_its_minute():
    assert due_prompt(time(11, 40), ["11:40"], ()) == "11:40"


def test_a_reminder_fires_shortly_after_in_case_a_tick_was_missed():
    """The machine can sleep through the exact minute."""
    assert due_prompt(time(11, 43), ["11:40"], ()) == "11:40"


def test_a_reminder_does_not_fire_long_after_its_window():
    assert due_prompt(time(11, 50), ["11:40"], ()) is None


def test_a_reminder_does_not_fire_early():
    assert due_prompt(time(11, 39), ["11:40"], ()) is None


def test_a_reminder_already_shown_is_not_repeated():
    assert due_prompt(time(11, 40), ["11:40"], ("11:40",)) is None


def test_the_second_reminder_still_fires_after_the_first():
    assert due_prompt(time(15, 20), ["11:40", "15:20"], ("11:40",)) == "15:20"


# -- session tracking -------------------------------------------------


def test_a_walk_accumulates_when_stopped():
    state = AppState(day=MONDAY).start_walk(NOON)
    assert state.walking

    state = state.stop_walk(NOON + timedelta(minutes=20))

    assert not state.walking
    assert state.walked_today == pytest.approx(20 * 60)


def test_a_walk_in_progress_counts_towards_the_total():
    """The cap must respond while you are still on the treadmill."""
    state = AppState(day=MONDAY).start_walk(NOON)
    partway = state.walked_including_current(NOON + timedelta(minutes=12))

    assert partway == pytest.approx(12 * 60)
    assert state.walked_today == 0, "not banked until you stop"


def test_walks_add_up_across_sessions():
    state = (
        AppState(day=MONDAY)
        .start_walk(NOON)
        .stop_walk(NOON + timedelta(minutes=25))
        .start_walk(NOON + timedelta(hours=3))
        .stop_walk(NOON + timedelta(hours=3, minutes=35))
    )
    assert state.walked_today == pytest.approx(60 * 60)


def test_starting_twice_does_not_restart_the_clock():
    state = AppState(day=MONDAY).start_walk(NOON)
    again = state.start_walk(NOON + timedelta(minutes=5))
    assert again.walk_started_at == NOON


def test_stopping_when_not_walking_is_harmless():
    state = AppState(day=MONDAY).stop_walk(NOON)
    assert state.walked_today == 0


def test_a_walk_survives_the_day_rolling_over():
    """Stopping it silently at midnight would lose time actually spent."""
    state = AppState(day=MONDAY, walked_today=600).start_walk(NOON)
    rolled = state.rolled_to(MONDAY + timedelta(days=1))

    assert rolled.walking
    assert rolled.walked_today == 0, "yesterday's tally still resets"


def test_a_walk_survives_a_restart(tmp_path):
    path = tmp_path / "state.json"
    save(AppState(day=MONDAY).start_walk(NOON), path)

    restored = load(path, today=MONDAY)

    assert restored.walking
    assert restored.walked_including_current(
        NOON + timedelta(minutes=10)
    ) == pytest.approx(10 * 60)


# -- the effect on the cap (SPEC §7) ----------------------------------


def cap_for(walked_minutes: float, worked_hours: float = 0.0):
    state = AppState(
        day=MONDAY,
        worked_today=worked_hours * HOUR,
        walked_today=walked_minutes * 60,
    )
    return status(state, classify_day(MONDAY, 1.0, 6.0), 11.0, 3.0, 60.0)


def test_not_walking_costs_a_full_hour_of_cap():
    assert cap_for(0).effective_seconds == 10 * HOUR


def test_every_minute_walked_buys_a_minute_back():
    assert cap_for(20).effective_seconds == pytest.approx(10 * HOUR + 20 * 60)
    assert cap_for(45).effective_seconds == pytest.approx(10 * HOUR + 45 * 60)


def test_hitting_the_target_restores_the_whole_cap():
    assert cap_for(60).effective_seconds == 11 * HOUR


def test_walking_beyond_the_target_does_not_add_cap():
    """The formula is max(0, target − walked): no credit for overshooting."""
    assert cap_for(90).effective_seconds == 11 * HOUR


def test_walking_can_unblock_you_the_same_day():
    """SPEC §7: go for a walk and the ceiling rises immediately."""
    blocked = cap_for(0, worked_hours=10.5)
    assert blocked.over, "10.5h worked against a 10h cap"

    # 45 minutes walked lifts the cap to 10.75h, clearing the 10.5h worked.
    after_walking = cap_for(45, worked_hours=10.5)
    assert not after_walking.over


def test_the_shortfall_is_named_in_the_description():
    assert "unwalked" in cap_for(10).describe()
    assert "unwalked" not in cap_for(60).describe()
