"""Tests for the rolling budgets in state.py (SPEC §5, §5a).

Three clocks with different rollover rules share one file, which is
exactly where a bug would hide: daily counters, Emergency Mode's rolling
7-day pool, and the override's calendar-month budget.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pomodoro_guardian.state import NON_WORKING, WORKING, AppState, load, save

MONDAY = date(2026, 8, 10)
NOON = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
HOUR = 3600


def days_ago(n: float) -> datetime:
    return NOON - timedelta(days=n)


# -- Emergency Mode: a rolling week, not a calendar week ---------------


def test_the_weekly_pool_starts_whole():
    assert AppState(day=MONDAY).emergency_remaining(3.0, NOON) == 3.0


def test_each_activation_spends_an_hour():
    state = AppState(day=MONDAY).with_emergency(1.0, days_ago(0))
    assert state.emergency_remaining(3.0, NOON) == 2.0


def test_three_activations_exhaust_the_pool():
    state = AppState(day=MONDAY)
    for n in (0, 1, 2):
        state = state.with_emergency(1.0, days_ago(n))

    assert state.emergency_remaining(3.0, NOON) == 0
    assert not state.can_use_emergency(1.0, 3.0, NOON)


def test_grants_older_than_seven_days_no_longer_count():
    state = AppState(day=MONDAY).with_emergency(1.0, days_ago(8))
    assert state.emergency_remaining(3.0, NOON) == 3.0


def test_the_window_rolls_rather_than_resetting_weekly():
    """A calendar week could be gamed across a Sunday boundary."""
    state = AppState(day=MONDAY)
    for n in (6.5, 3, 1):
        state = state.with_emergency(1.0, days_ago(n))
    assert state.emergency_remaining(3.0, NOON) == 0

    # One day later the oldest grant has aged out and one hour returns.
    later = NOON + timedelta(days=1)
    assert state.emergency_remaining(3.0, later) == 1.0


def test_only_todays_grants_extend_todays_cap():
    today = NOON.astimezone().date()
    state = AppState(day=today).with_emergency(1.0, days_ago(3))
    assert state.emergency_hours_today() == 0

    state = state.with_emergency(1.0, NOON)
    assert state.emergency_hours_today() == 1.0


def test_a_grant_made_right_now_counts_towards_today():
    """Grants are stored in UTC, `day` is local — the comparison must convert.

    Comparing UTC dates directly worked all morning and failed all evening:
    once local time passes UTC midnight, a grant just made lands on
    tomorrow's date and stops extending today's cap.
    """
    state = AppState.for_today().with_emergency(1.0)
    assert state.emergency_hours_today() == 1.0


def test_a_grant_late_in_the_local_evening_still_counts():
    moment = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    local_day = moment.astimezone().date()

    state = AppState(day=local_day).with_emergency(1.0, moment)

    assert state.emergency_hours_today() == 1.0


def test_grants_survive_the_day_rolling_over():
    """The daily counters reset; the weekly pool must not."""
    state = AppState(day=MONDAY, worked_today=5 * HOUR).with_emergency(1.0, NOON)
    rolled = state.rolled_to(MONDAY + timedelta(days=1))

    assert rolled.worked_today == 0
    assert rolled.emergency_remaining(3.0, NOON + timedelta(days=1)) == 2.0


# -- the day-type override (SPEC §5a) ---------------------------------


def test_lowering_the_cap_is_unlimited():
    state = AppState(day=MONDAY)
    for _ in range(10):
        state = state.with_override(NON_WORKING)

    assert state.day_type_override == NON_WORKING
    assert state.raises_used_this_month() == 0


def test_raising_spends_one_of_the_monthly_budget():
    state = AppState(day=MONDAY).with_override(WORKING)

    assert state.day_type_override == WORKING
    assert state.raises_used_this_month() == 1
    assert state.can_raise(2)


def test_the_monthly_raise_budget_runs_out():
    state = AppState(day=MONDAY).with_override(WORKING).with_override(WORKING)

    assert state.raises_used_this_month() == 2
    assert not state.can_raise(2)


def test_clearing_a_raise_does_not_refund_it():
    """Otherwise: raise in the morning, work the day, clear it at night."""
    state = AppState(day=MONDAY).with_override(WORKING).with_override(None)

    assert state.day_type_override is None
    assert state.raises_used_this_month() == 1


def test_raises_from_last_month_do_not_count():
    state = AppState(day=MONDAY, override_raises=(date(2026, 7, 20),))
    assert state.raises_used_this_month() == 0
    assert state.can_raise(2)


def test_the_override_does_not_survive_the_day():
    """SPEC §5a: today only, expiring at local midnight."""
    state = AppState(day=MONDAY).with_override(WORKING)
    rolled = state.rolled_to(MONDAY + timedelta(days=1))

    assert rolled.day_type_override is None
    assert rolled.raises_used_this_month(MONDAY + timedelta(days=1)) == 1


# -- work tracking and persistence ------------------------------------


def test_work_accumulates_and_ignores_negative_deltas():
    state = AppState(day=MONDAY).with_work(600).with_work(300).with_work(-50)
    assert state.worked_today == 900


def test_everything_round_trips_through_a_file(tmp_path):
    path = tmp_path / "state.json"
    original = (
        AppState(day=MONDAY, worked_today=4 * HOUR, custom_skip_used=600)
        .with_emergency(1.0, NOON)
        .with_override(WORKING)
    )
    save(original, path)

    restored = load(path, today=MONDAY)

    assert restored.worked_today == 4 * HOUR
    assert restored.custom_skip_used == 600
    assert restored.day_type_override == WORKING
    assert restored.emergency_hours_today() == 1.0
    assert restored.raises_used_this_month() == 1


def test_a_new_day_keeps_rolling_budgets_but_clears_daily_ones(tmp_path):
    path = tmp_path / "state.json"
    save(
        AppState(day=MONDAY, worked_today=9 * HOUR, custom_skip_used=1200)
        .with_emergency(1.0, NOON)
        .with_override(NON_WORKING),
        path,
    )

    tomorrow = load(path, today=MONDAY + timedelta(days=1))

    assert tomorrow.worked_today == 0
    assert tomorrow.custom_skip_used == 0
    assert tomorrow.day_type_override is None
    assert tomorrow.emergency_remaining(3.0, NOON + timedelta(days=1)) == 2.0


@pytest.mark.parametrize("junk", ["nonsense", 42, None, [1, 2]])
def test_malformed_grant_lists_are_ignored(junk):
    state = AppState.from_dict(
        {"day": MONDAY.isoformat(), "emergency_grants": junk}, today=MONDAY
    )
    assert state.emergency_grants == ()


def test_an_unparseable_grant_does_not_discard_the_good_ones():
    state = AppState.from_dict(
        {
            "day": MONDAY.isoformat(),
            "emergency_grants": [
                {"at": "not-a-date"},
                {"at": NOON.isoformat(), "hours": 1.0},
            ],
        },
        today=MONDAY,
    )
    assert len(state.emergency_grants) == 1


def test_an_unknown_override_value_reads_as_none():
    state = AppState.from_dict(
        {"day": MONDAY.isoformat(), "day_type_override": "sideways"},
        today=MONDAY,
    )
    assert state.day_type_override is None
