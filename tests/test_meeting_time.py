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

import pytest

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


def test_the_interval_keeps_advancing_through_a_call():
    """Redesigned 2026-08-22: a meeting no longer freezes the rhythm, only
    the lock. Before, a long meeting looked identical to a stalled
    interval — the timer sat frozen at whatever it read when the call
    started, even a call that ran for hours. Now the interval (and the
    warning) keep pace with the wall clock exactly as ordinary work would,
    since time on a call already counts as work per this file's other
    tests; only actually raising the lock stays held off.
    """
    h = Harness()
    h.start_working()
    elapsed_before = h.engine.snapshot().remaining

    h.advance(10 * MINUTE, active=False, excluded=True)

    assert h.engine.state is State.WORK
    assert h.engine.snapshot().remaining == pytest.approx(
        elapsed_before - 10 * MINUTE, abs=2.0
    )


def test_a_break_due_mid_call_holds_rather_than_locks():
    """A call outlasting the interval must not lock — but must not reset
    or silently re-run the interval either. It waits."""
    h = Harness()
    h.start_working()

    h.advance(40 * MINUTE, active=False, excluded=True)

    assert h.engine.state in (State.WORK, State.WARNING)
    assert h.engine.snapshot().remaining == 0.0


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


# -- the post-meeting buffer (added 2026-08-22) -------------------------


def test_a_break_due_mid_call_waits_out_the_post_meeting_delay():
    """A break already due when the call ends doesn't fire instantly —
    Config.post_meeting_break_delay gives a moment to wrap up notes."""
    h = Harness(replace(Config(), post_meeting_break_delay=5 * MINUTE))
    h.start_working()
    h.advance(40 * MINUTE, active=False, excluded=True)  # break comes due mid-call
    assert h.engine.snapshot().remaining == 0.0

    h.advance(1.0, active=False, excluded=False)  # the call just ended
    assert Event.BREAK_STARTED not in h.events

    h.advance(4 * MINUTE + 58, active=False, excluded=False)
    assert Event.BREAK_STARTED not in h.events, "fired before the buffer elapsed"

    h.advance(3, active=False, excluded=False)
    assert Event.BREAK_STARTED in h.events


def test_a_zero_delay_fires_the_break_immediately_after_the_call():
    h = Harness(replace(Config(), post_meeting_break_delay=0))
    h.start_working()
    h.advance(40 * MINUTE, active=False, excluded=True)

    h.advance(1.0, active=False, excluded=False)

    assert Event.BREAK_STARTED in h.events


def test_the_delay_does_not_apply_to_a_break_that_was_not_yet_due():
    """The buffer only holds off a break that was already waiting — one
    that becomes due later, after the call, is unaffected."""
    h = Harness(replace(Config(), post_meeting_break_delay=5 * MINUTE))
    h.start_working()
    h.advance(5 * MINUTE, active=False, excluded=True)  # call ends well before due
    assert h.engine.snapshot().remaining > 0.0

    h.advance(1.0, active=False, excluded=False)
    remaining_at_call_end = h.engine.snapshot().remaining

    h.advance(remaining_at_call_end, active=True, excluded=False)

    assert Event.BREAK_STARTED in h.events
