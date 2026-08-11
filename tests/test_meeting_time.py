"""Tests for time on calls counting as work (SPEC §3).

The bug these exist for was found by using the app, not by reading it: on
2026-08-11 an 82-minute meeting credited **zero seconds**, and only 2.3 of
4.7 hours at the desk were counted. A day of meetings could then be followed
by a full cap's worth of tracked work on top, which defeats the cap the app
exists to enforce.

The cause was two separate needs sharing one mechanism. An exclusion means
"do not start a break now"; it had also come to mean "do not count this
time", and only the first is what it is for. Focus Mode had already made
exactly this distinction, and says so in timer.py — the same reasoning simply
never reached exclusions.
"""

from __future__ import annotations

from dataclasses import replace

from pomodoro_guardian.config import MINUTE, Config
from pomodoro_guardian.timer import Event, PomodoroEngine, State

START = 1000.0


class Harness:
    """Drives the engine against a fake clock, with a call switchable."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.now = START
        self.last_input = START
        self.engine = PomodoroEngine(self.config, now=START)
        self.events: list[Event] = []

    def advance(self, seconds, active=True, excluded=False, step=1.0):
        target = self.now + seconds
        while self.now < target - 1e-9:
            self.now = min(self.now + step, target)
            if active:
                self.last_input = self.now
            self.events.extend(
                self.engine.update(self.now, self.last_input, excluded=excluded)
            )
        return self.events

    def start_working(self):
        """Get past the start threshold so a session is genuinely running."""
        self.advance(self.config.start_threshold + 5)
        assert self.engine.state is State.WORK


# -- the reported bug --------------------------------------------------


def test_a_meeting_counts_towards_the_daily_cap():
    """82 minutes credited zero seconds before this. That was the bug."""
    h = Harness()
    h.start_working()
    before = h.engine.worked_total

    h.advance(82 * MINUTE, active=False, excluded=True)

    credited = h.engine.worked_total - before
    assert credited > 80 * MINUTE, (
        f"a 82-minute meeting credited only {credited / 60:.1f} min"
    )


def test_a_silent_call_still_counts():
    """Nobody types while listening, and it is still work."""
    h = Harness()
    h.start_working()
    before = h.engine.worked_total

    h.advance(30 * MINUTE, active=False, excluded=True)

    assert h.engine.worked_total - before > 29 * MINUTE


def test_call_time_is_tallied_separately_as_well():
    """So a day can be read as "2.3h worked, 1.4h of it on calls"."""
    h = Harness()
    h.start_working()

    h.advance(20 * MINUTE, active=False, excluded=True)

    assert h.engine.excluded_total > 19 * MINUTE
    assert h.engine.excluded_total <= h.engine.worked_total


def test_the_setting_restores_the_old_behaviour():
    """The escape hatch, for a microphone some app never releases."""
    h = Harness(replace(Config(), count_exclusions_as_work=False))
    h.start_working()
    before = h.engine.worked_total

    h.advance(30 * MINUTE, active=False, excluded=True)

    assert h.engine.worked_total == before
    assert h.engine.excluded_total == 0.0


# -- and the break still holds off, which is the point of an exclusion --


def test_the_interval_does_not_advance_during_a_call():
    """The whole purpose: no break lands mid-meeting."""
    h = Harness()
    h.start_working()
    elapsed_before = h.engine.snapshot().remaining

    h.advance(40 * MINUTE, active=False, excluded=True)

    assert h.engine.state is State.WORK
    assert h.engine.snapshot().remaining == elapsed_before


def test_no_break_fires_during_a_long_call():
    h = Harness()
    h.start_working()

    h.advance(2 * 60 * MINUTE, active=False, excluded=True)

    assert Event.BREAK_STARTED not in h.events


def test_the_countdown_resumes_where_it_left_off():
    h = Harness()
    h.start_working()
    h.advance(10 * MINUTE)                      # ten minutes of real work
    remaining = h.engine.snapshot().remaining

    h.advance(30 * MINUTE, active=False, excluded=True)
    h.advance(5.0)                              # a moment of typing after

    assert h.engine.snapshot().remaining < remaining
    assert h.engine.snapshot().remaining > remaining - 30 * MINUTE


# -- what must not be credited -----------------------------------------


def test_a_sleeping_machine_buys_nothing_during_a_call():
    """A laptop shut mid-meeting must not credit the hours it was closed.

    One enormous tick rather than many small ones: that is what the app sees
    when it wakes, and `max_tick` is what stops it counting.
    """
    h = Harness()
    h.start_working()
    before = h.engine.worked_total

    h.now += 3 * 3600
    h.engine.update(h.now, h.last_input, excluded=True)

    assert h.engine.worked_total == before


def test_a_call_is_not_credited_twice():
    """The watermark is still pinned, so the input rules cannot pay for the
    call a second time once typing resumes."""
    h = Harness()
    h.start_working()
    before = h.engine.worked_total

    h.advance(20 * MINUTE, active=False, excluded=True)
    after_call = h.engine.worked_total
    h.advance(2 * MINUTE)                       # typing again

    call_credit = after_call - before
    typing_credit = h.engine.worked_total - after_call
    assert abs(call_credit - 20 * MINUTE) < 5
    assert typing_credit < 2 * MINUTE + 5, "the call was paid for twice"


def test_a_call_does_not_start_a_session_by_itself():
    """A session should begin from real work, not from being on a call."""
    h = Harness()

    h.advance(30 * MINUTE, active=False, excluded=True)

    assert h.engine.state is State.IDLE
    assert Event.WORK_STARTED not in h.events


def test_a_call_before_any_work_still_counts_towards_the_cap():
    """A 9am meeting is work even though no session had started yet."""
    h = Harness()

    h.advance(30 * MINUTE, active=False, excluded=True)

    assert h.engine.worked_total > 29 * MINUTE
