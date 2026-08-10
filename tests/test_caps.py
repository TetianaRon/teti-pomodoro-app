"""Tests for the daily work cap (SPEC §5, §5a)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pomodoro_guardian.caps import HOUR, CapStatus, classify_day, status
from pomodoro_guardian.state import NON_WORKING, WORKING, AppState

MONDAY = date(2026, 8, 10)
SATURDAY = date(2026, 8, 8)
SUNDAY = date(2026, 8, 9)
HOLIDAY = date(2026, 8, 3)      # Civic Holiday, a Monday


# -- day classification -----------------------------------------------


def test_a_weekday_with_a_normal_calendar_is_a_working_day():
    day = classify_day(MONDAY, longest_busy_hours=1.5, day_off_block_hours=6.0)
    assert day.working


@pytest.mark.parametrize("weekend", [SATURDAY, SUNDAY])
def test_weekends_are_days_off_regardless_of_the_calendar(weekend):
    day = classify_day(weekend, longest_busy_hours=0.5, day_off_block_hours=6.0)
    assert not day.working
    assert day.reason == "weekend"


def test_a_long_busy_block_marks_a_day_off():
    """The measured holiday shape: one ~9h block covering the working day."""
    day = classify_day(HOLIDAY, longest_busy_hours=9.0, day_off_block_hours=6.0)
    assert not day.working
    assert "9.0h" in day.reason


def test_a_meeting_packed_day_is_still_a_working_day():
    """Ordinary weekdays never exceeded 1.5h contiguous in the real feed."""
    day = classify_day(MONDAY, longest_busy_hours=5.9, day_off_block_hours=6.0)
    assert day.working


def test_the_threshold_is_inclusive():
    assert not classify_day(MONDAY, 6.0, 6.0).working
    assert classify_day(MONDAY, 5.99, 6.0).working


def test_an_unavailable_calendar_falls_back_to_the_day_of_week():
    """A network problem must not silently grant a full day on a holiday."""
    day = classify_day(MONDAY, longest_busy_hours=None, day_off_block_hours=6.0)

    assert day.working
    assert not day.from_calendar
    assert "unavailable" in day.reason


def test_an_unavailable_calendar_still_knows_about_weekends():
    day = classify_day(SUNDAY, longest_busy_hours=None, day_off_block_hours=6.0)
    assert not day.working


# -- overrides (SPEC §5a) ---------------------------------------------


def test_an_override_can_force_a_working_day():
    day = classify_day(SUNDAY, 24.0, 6.0, override=WORKING)
    assert day.working
    assert "override" in day.reason


def test_an_override_can_force_a_day_off():
    day = classify_day(MONDAY, 0.5, 6.0, override=NON_WORKING)
    assert not day.working


def test_an_override_beats_both_the_weekend_and_the_calendar():
    assert classify_day(SATURDAY, 24.0, 6.0, override=WORKING).working


# -- the cap itself ---------------------------------------------------


def working_state(hours_worked: float) -> AppState:
    return AppState(day=MONDAY, worked_today=hours_worked * HOUR)


def test_a_working_day_gets_the_full_cap():
    st = status(working_state(3), classify_day(MONDAY, 1.0, 6.0), 11.0, 3.0)

    assert st.effective_seconds == 11 * HOUR
    assert st.remaining_seconds == 8 * HOUR
    assert not st.over


def test_a_day_off_gets_the_reduced_cap():
    st = status(working_state(1), classify_day(SUNDAY, None, 6.0), 11.0, 3.0)
    assert st.effective_seconds == 3 * HOUR


def test_going_over_is_detected_and_measured():
    st = status(working_state(12.5), classify_day(MONDAY, 1.0, 6.0), 11.0, 3.0)

    assert st.over
    assert st.remaining_seconds == 0
    assert st.overtime_seconds == pytest.approx(1.5 * HOUR)


def test_the_cap_is_reached_exactly_at_the_boundary():
    st = status(working_state(11), classify_day(MONDAY, 1.0, 6.0), 11.0, 3.0)
    assert st.over


def test_emergency_hours_raise_the_cap():
    # Derived from the instant rather than assumed: grants are stored in
    # UTC while `day` is local, so a fixed UTC midnight would land on the
    # previous day in any timezone behind UTC.
    moment = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    day = moment.astimezone().date()
    state = AppState(day=day, worked_today=11 * HOUR).with_emergency(1.0, moment)

    st = status(state, classify_day(day, 1.0, 6.0), 11.0, 3.0)

    assert st.effective_seconds == 12 * HOUR
    assert not st.over


def test_only_todays_emergency_grants_count():
    """Yesterday's extra hour must not still be extending today's cap."""
    yesterday = datetime.combine(
        MONDAY - timedelta(days=1), datetime.min.time(), timezone.utc
    )
    state = AppState(day=MONDAY, worked_today=11 * HOUR).with_emergency(
        1.0, now=yesterday
    )
    st = status(state, classify_day(MONDAY, 1.0, 6.0), 11.0, 3.0)

    assert st.effective_seconds == 11 * HOUR
    assert st.over


def test_describe_reads_sensibly_either_side_of_the_cap():
    under = CapStatus(classify_day(MONDAY, 1.0, 6.0), 11.0, 0.0, 4 * HOUR)
    over = CapStatus(classify_day(MONDAY, 1.0, 6.0), 11.0, 1.0, 13 * HOUR)

    assert "4.0h of 11.0h" in under.describe()
    assert "over" in over.describe()
    assert "emergency" in over.describe()
