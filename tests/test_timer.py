"""Tests for the Pomodoro state machine.

The engine takes its clock as an argument, so a 25-minute work interval, a
long break on the 4th cycle, and a one-hour idle gap all run instantly and
deterministically here — no display, no input hooks, no sleeping.
"""

from __future__ import annotations

import pytest

from pomodoro_guardian.config import Config
from pomodoro_guardian.timer import Event, PomodoroEngine, State

START = 1000.0


class Harness:
    """Drives the engine against a fake clock."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.now = START
        self.last_input = START
        self.engine = PomodoroEngine(self.config, now=START)
        self.events: list[Event] = []

    def advance(self, seconds: float, active: bool = True, step: float = 1.0):
        """Tick forward, optionally producing input on every tick."""
        target = self.now + seconds
        while self.now < target - 1e-9:
            self.now = min(self.now + step, target)
            if active:
                self.last_input = self.now
            self.events.extend(self.engine.update(self.now, self.last_input))
        return self.events

    def advance_until(self, event: Event, limit: float, active: bool = True):
        """Tick until `event` fires, or fail after `limit` seconds."""
        spent = 0.0
        while spent < limit:
            before = len(self.events)
            self.advance(1.0, active=active)
            if event in self.events[before:]:
                return self.now
            spent += 1.0
        pytest.fail(f"{event} did not fire within {limit}s")

    @property
    def state(self) -> State:
        return self.engine.state

    def drain(self) -> list[Event]:
        events, self.events = self.events, []
        return events


# -- detection (SPEC §2.1) --------------------------------------------


def test_brief_activity_does_not_start_a_session():
    h = Harness()
    h.advance(h.config.start_threshold - 5, active=True)
    assert h.state is State.IDLE
    assert Event.WORK_STARTED not in h.events


def test_sustained_activity_starts_a_session():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=h.config.start_threshold + 10)
    assert h.state is State.WORK


def test_activity_broken_by_a_gap_restarts_the_threshold():
    h = Harness()
    h.advance(h.config.start_threshold - 10, active=True)
    # Step away long enough to break the run, then come back.
    h.advance(h.config.input_gap + 5, active=False)
    h.advance(h.config.start_threshold - 10, active=True)
    assert h.state is State.IDLE, "the earlier partial run should not carry over"


# -- rhythm (SPEC §2.2, §2.3) -----------------------------------------


def test_warning_precedes_the_lock_by_the_configured_lead():
    h = Harness()
    warned = h.advance_until(Event.WARNING_STARTED, limit=2000)
    assert h.state is State.WARNING
    locked = h.advance_until(Event.BREAK_STARTED, limit=500)
    assert locked - warned == pytest.approx(h.config.warning_lead, abs=2.0)


def test_break_locks_then_ends_after_the_short_break_duration():
    h = Harness()
    started = h.advance_until(Event.BREAK_STARTED, limit=2000)
    assert h.engine.snapshot().locked
    # No input during the break — the lock is swallowing it.
    ended = h.advance_until(
        Event.BREAK_ENDED, limit=h.config.short_break_duration + 10, active=False
    )
    assert ended - started == pytest.approx(
        h.config.short_break_duration, abs=2.0
    )
    assert h.state is State.IDLE
    assert h.engine.completed_cycles == 1


def test_break_ends_on_wall_clock_even_with_no_input():
    """Break time must not depend on activity — you're meant to be away."""
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.advance(h.config.short_break_duration + 2, active=False)
    assert h.state is State.IDLE


def test_every_fourth_break_is_long():
    h = Harness()
    lengths = []
    for _ in range(h.config.long_break_every):
        started = h.advance_until(Event.BREAK_STARTED, limit=2000)
        is_long = h.engine.snapshot().is_long_break
        ended = h.advance_until(
            Event.BREAK_ENDED,
            limit=h.config.long_break_duration + 10,
            active=False,
        )
        lengths.append((is_long, ended - started))

    assert [is_long for is_long, _ in lengths] == [False, False, False, True]
    assert lengths[-1][1] == pytest.approx(
        h.config.long_break_duration, abs=2.0
    )
    assert lengths[0][1] == pytest.approx(h.config.short_break_duration, abs=2.0)
    assert h.engine.completed_cycles == 4


# -- idle handling (SPEC §2.4) ----------------------------------------


def test_work_time_stops_accruing_while_idle():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.drain()
    remaining_before = h.engine.snapshot().remaining

    h.advance(h.config.idle_pause_after + 60, active=False)

    assert Event.WORK_PAUSED in h.events
    assert h.engine.snapshot().paused
    assert h.engine.snapshot().remaining == pytest.approx(
        remaining_before, abs=2.0
    ), "idle time must not count toward the work interval"


def test_returning_from_a_short_absence_resumes_the_same_interval():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.advance(h.config.idle_pause_after + 10, active=False)
    h.drain()

    h.advance(5, active=True)

    assert Event.WORK_RESUMED in h.events
    assert h.state is State.WORK
    assert not h.engine.snapshot().paused


def test_a_long_absence_abandons_the_interval():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.drain()

    h.advance(h.config.idle_reset_after + 5, active=False)

    assert Event.WORK_ABANDONED in h.events
    assert h.state is State.IDLE


def test_a_long_absence_resets_the_long_break_counter():
    """The 'reset after an idle gap' answer to SPEC §10."""
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.advance_until(
        Event.BREAK_ENDED, limit=h.config.short_break_duration + 10, active=False
    )
    assert h.engine.completed_cycles == 1
    h.drain()

    h.advance(h.config.idle_reset_after + 5, active=False)

    assert Event.CYCLES_RESET in h.events
    assert h.engine.completed_cycles == 0


def test_cycles_reset_fires_once_per_idle_gap():
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.advance_until(
        Event.BREAK_ENDED, limit=h.config.short_break_duration + 10, active=False
    )
    h.drain()

    h.advance(h.config.idle_reset_after + 600, active=False)

    assert h.events.count(Event.CYCLES_RESET) == 1


def test_the_lock_is_not_interrupted_by_an_idle_reset():
    """A long break can outlast idle_reset_after in a compressed config."""
    config = Config(idle_reset_after=30.0)
    h = Harness(config)
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.drain()

    h.advance(config.short_break_duration - 5, active=False)

    assert h.state is State.BREAK, "the break must run its full length"
    assert Event.CYCLES_RESET not in h.events


# -- robustness -------------------------------------------------------


def test_a_sleeping_machine_is_not_credited_as_work():
    """A huge tick delta means suspend/hibernate, not four hours of typing."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    remaining_before = h.engine.snapshot().remaining

    # One tick, four hours later, with input "just now" on resume.
    h.now += 4 * 3600
    h.last_input = h.now
    h.engine.update(h.now, h.last_input)

    assert h.engine.snapshot().remaining >= remaining_before - 2.0


def test_a_backwards_clock_does_not_accrue_time():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    remaining_before = h.engine.snapshot().remaining

    h.now -= 30
    h.engine.update(h.now, h.now)

    assert h.engine.snapshot().remaining == pytest.approx(
        remaining_before, abs=1.0
    )


def test_scaled_config_preserves_the_shape_of_the_cycle():
    config = Config().scaled(60)
    assert config.work_duration == pytest.approx(25.0)
    assert config.short_break_duration == pytest.approx(5.0)
    assert config.long_break_every == 4, "cycle count is not a duration"

    h = Harness(config)
    h.advance_until(Event.BREAK_STARTED, limit=200)
    assert h.state is State.BREAK
