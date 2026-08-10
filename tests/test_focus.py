"""Tests for Focus Mode (SPEC §6).

The distinction that matters most: Focus Mode suppresses the *break* but
keeps accruing work. Modelling it as an exclusion would freeze the
countdown, making a two-hour session invisible to the daily cap and
therefore free.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pomodoro_guardian.config import Config
from pomodoro_guardian.state import AppState, load, save
from pomodoro_guardian.timer import Event, PomodoroEngine, State

MONDAY = date(2026, 8, 10)
NOON = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


# -- the budget -------------------------------------------------------


def test_focus_is_available_once_a_day():
    state = AppState(day=MONDAY)
    assert state.can_focus(1)

    state = state.start_focus(NOON).stop_focus()
    assert not state.can_focus(1)


def test_the_use_is_spent_on_starting_not_finishing():
    """Otherwise stopping early and restarting would be unlimited focus."""
    state = AppState(day=MONDAY).start_focus(NOON)
    assert state.focus_uses_today == 1

    state = state.stop_focus()
    assert not state.can_focus(1)


def test_focus_cannot_be_started_twice():
    state = AppState(day=MONDAY).start_focus(NOON)
    again = state.start_focus(NOON + timedelta(minutes=5))

    assert again.focus_started_at == NOON
    assert again.focus_uses_today == 1


def test_the_daily_use_resets_tomorrow():
    state = AppState(day=MONDAY).start_focus(NOON).stop_focus()
    tomorrow = state.rolled_to(MONDAY + timedelta(days=1))

    assert tomorrow.can_focus(1)


def test_a_session_running_at_midnight_carries_over():
    """Its own two-hour limit ends it soon enough."""
    state = AppState(day=MONDAY).start_focus(NOON)
    tomorrow = state.rolled_to(MONDAY + timedelta(days=1))

    assert tomorrow.focusing


# -- the two-hour limit -----------------------------------------------


def test_time_remaining_counts_down():
    state = AppState(day=MONDAY).start_focus(NOON)
    left = state.focus_remaining(2.0, NOON + timedelta(minutes=30))

    assert left == pytest.approx(90 * 60)


def test_a_session_expires_after_its_limit():
    state = AppState(day=MONDAY).start_focus(NOON)

    assert not state.focus_expired(2.0, NOON + timedelta(hours=1, minutes=59))
    assert state.focus_expired(2.0, NOON + timedelta(hours=2, minutes=1))


def test_nothing_remains_when_not_focusing():
    assert AppState(day=MONDAY).focus_remaining(2.0, NOON) == 0
    assert not AppState(day=MONDAY).focus_expired(2.0, NOON)


def test_focus_survives_a_restart(tmp_path):
    path = tmp_path / "state.json"
    save(AppState(day=MONDAY).start_focus(NOON), path)

    restored = load(path, today=MONDAY)

    assert restored.focusing
    assert restored.focus_uses_today == 1
    assert restored.focus_remaining(
        2.0, NOON + timedelta(minutes=15)
    ) == pytest.approx(105 * 60)


# -- effect on the engine ---------------------------------------------


class Harness:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.now = 1000.0
        self.last_input = 1000.0
        self.engine = PomodoroEngine(self.config, now=1000.0)
        self.events: list[Event] = []

    def advance(self, seconds, active=True, step=1.0):
        target = self.now + seconds
        while self.now < target - 1e-9:
            self.now = min(self.now + step, target)
            if active:
                self.last_input = self.now
            self.events.extend(self.engine.update(self.now, self.last_input))
        return self.events

    def advance_until(self, event, limit, active=True):
        spent = 0.0
        while spent < limit:
            before = len(self.events)
            self.advance(1.0, active=active)
            if event in self.events[before:]:
                return self.now
            spent += 1.0
        pytest.fail(f"{event} did not fire within {limit}s")


def test_focus_stops_the_break_arriving():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.engine.suppress_breaks = True

    h.advance(h.config.work_duration * 2, active=True)

    assert Event.BREAK_STARTED not in h.events
    assert Event.WARNING_STARTED not in h.events
    assert h.engine.state is State.WORK


def test_work_still_accrues_during_focus():
    """The whole reason focus is not modelled as an exclusion: a two-hour
    session must still count against the daily cap."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    before = h.engine.worked_total
    h.engine.suppress_breaks = True

    h.advance(600, active=True)

    assert h.engine.worked_total - before == pytest.approx(600, abs=2.0)


def test_the_break_arrives_immediately_once_focus_ends():
    """The overdue interval is held, not forgiven."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.engine.suppress_breaks = True
    h.advance(h.config.work_duration * 2, active=True)
    h.events.clear()

    h.engine.suppress_breaks = False
    h.advance(2, active=True)

    assert Event.BREAK_STARTED in h.events


def test_an_exclusion_still_freezes_even_during_focus():
    """A call is a call: no work should accrue for it either way."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.engine.suppress_breaks = True
    before = h.engine.worked_total

    for _ in range(60):
        h.now += 1
        h.engine.update(h.now, h.last_input, excluded=True)

    assert h.engine.worked_total == before
