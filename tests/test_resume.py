"""Tests for picking the cycle up again after a restart (SPEC §2.2).

The bug this covers was invisible to every other test, because every other
test drives one engine from start to finish. In real use the app is
restarted several times a day to pick up a change, the engine is rebuilt
each time, and on 2026-08-10 that meant four intervals were never
accumulated and the long break did not fire once.

Two halves, tested together because either alone would look correct:
`state.py` remembers the position and ages it, `timer.py` adopts it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from pomodoro_guardian.config import Config
from pomodoro_guardian.state import AppState, load, save
from pomodoro_guardian.timer import Event, PomodoroEngine, Position, State

MONDAY = date(2026, 8, 10)
NOON = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
MINUTE = 60


def minutes_ago(n: float) -> datetime:
    return NOON - timedelta(minutes=n)


class Harness:
    """The same fake-clock driver test_timer.py uses."""

    def __init__(self, config: Config | None = None, start: float = 1000.0):
        self.config = config or Config()
        self.now = start
        self.last_input = start
        self.engine = PomodoroEngine(self.config, now=start)
        self.events: list[Event] = []

    def advance(self, seconds: float, active: bool = True, step: float = 1.0):
        target = self.now + seconds
        while self.now < target - 1e-9:
            self.now = min(self.now + step, target)
            if active:
                self.last_input = self.now
            self.events.extend(self.engine.update(self.now, self.last_input))
        return self.events

    def work_through_one_break(self) -> None:
        """A full interval plus its break, leaving one cycle completed."""
        self.advance(self.config.work_duration + 2)
        self.advance(self.config.short_break_duration + 2, active=False)


# -- what the engine hands over ---------------------------------------


def test_a_fresh_engine_has_nothing_to_hand_over():
    assert PomodoroEngine(Config(), now=0.0).position().empty


def test_the_position_reports_completed_cycles():
    h = Harness()
    h.work_through_one_break()
    assert h.engine.completed_cycles == 1
    assert h.engine.position().completed_cycles == 1


def test_the_position_reports_the_unfinished_interval():
    h = Harness()
    h.advance(10 * MINUTE)
    assert h.engine.state is State.WORK
    assert h.engine.position().interval_elapsed >= 9 * MINUTE


def test_a_break_in_progress_is_not_handed_over():
    """Quitting mid-break costs that break, rather than re-locking at launch."""
    h = Harness()
    h.advance(h.config.work_duration + 2)
    assert h.engine.state is State.BREAK
    position = h.engine.position()
    assert position.interval_elapsed == 0
    assert position.completed_cycles == 0   # the break did not finish


# -- what the engine does with one -----------------------------------


def test_a_resumed_count_brings_the_long_break_forward():
    """Three cycles restored: the very next break is the long one."""
    h = Harness()
    h.engine.resume(Position(completed_cycles=3))

    h.advance(h.config.work_duration + 2)

    assert h.engine.state is State.BREAK
    assert h.engine.snapshot().is_long_break


def test_without_a_resume_the_next_break_is_short():
    """The failure as it was: a restart put the long break four intervals off."""
    h = Harness()
    h.advance(h.config.work_duration + 2)
    assert not h.engine.snapshot().is_long_break


def test_a_restored_interval_is_paid_into_the_next_session():
    """20 minutes already worked plus 5 more is a break, not 25 more minutes."""
    config = Config()
    h = Harness(config)
    h.engine.resume(Position(interval_elapsed=20 * MINUTE))

    h.advance(5 * MINUTE + 2)

    assert h.engine.state is State.BREAK


def test_a_restored_interval_waits_for_real_work_first():
    """A saved file cannot say you are at the desk; the threshold still applies."""
    h = Harness()
    h.engine.resume(Position(interval_elapsed=24 * MINUTE))

    h.advance(h.config.start_threshold / 2)

    assert h.engine.state is State.IDLE


def test_a_restored_interval_is_credited_once_only():
    h = Harness()
    h.engine.resume(Position(interval_elapsed=20 * MINUTE))
    # The credit is spent on the first interval, which therefore ends early.
    h.advance(5 * MINUTE + 2)
    assert h.engine.state is State.BREAK
    h.advance(h.config.short_break_duration + 2, active=False)

    h.advance(10 * MINUTE)              # a second interval, starting from zero

    assert h.engine.state is State.WORK
    assert h.engine.position().interval_elapsed < 12 * MINUTE


def test_an_hour_idle_after_a_restart_discards_the_whole_position():
    """The gap that clears a live position clears a restored one too."""
    h = Harness()
    h.engine.resume(Position(completed_cycles=3, interval_elapsed=20 * MINUTE))

    h.advance(h.config.idle_reset_after + 2 * MINUTE, active=False)

    assert h.engine.completed_cycles == 0
    assert h.engine.position().empty
    h.advance(h.config.work_duration + 2)
    assert not h.engine.snapshot().is_long_break


def test_resume_ignores_impossible_values():
    engine = PomodoroEngine(Config(), now=0.0)
    engine.resume(Position(completed_cycles=-2, interval_elapsed=-500))
    assert engine.position().empty


# -- what the state file remembers -----------------------------------


def test_the_position_round_trips_through_a_file(tmp_path):
    path = tmp_path / "state.json"
    save(
        AppState(day=MONDAY).with_position(3, 20 * MINUTE, now=NOON), path
    )

    restored = load(path, today=MONDAY)

    assert restored.cycles_completed == 3
    assert restored.interval_elapsed == 20 * MINUTE
    assert restored.tracked_at == NOON


def test_a_recent_position_is_resumable():
    state = AppState(day=MONDAY).with_position(3, 600, now=minutes_ago(5))
    assert state.resumable_position(60 * MINUTE, NOON) == (3, 600)


def test_a_position_older_than_the_idle_gap_is_not():
    state = AppState(day=MONDAY).with_position(3, 600, now=minutes_ago(90))
    assert state.resumable_position(60 * MINUTE, NOON) == (0, 0.0)


def test_a_position_stamped_in_the_future_is_not_trusted():
    """The clock moved back; its age says nothing useful."""
    ahead = NOON + timedelta(hours=1)
    state = AppState(day=MONDAY).with_position(3, 600, now=ahead)
    assert state.resumable_position(60 * MINUTE, NOON) == (0, 0.0)


def test_nothing_saved_means_nothing_to_resume():
    assert AppState(day=MONDAY).resumable_position(60 * MINUTE, NOON) == (0, 0.0)


def test_an_unchanged_position_keeps_its_original_stamp():
    """Otherwise an idle evening would look like work abandoned a second ago."""
    state = AppState(day=MONDAY).with_position(2, 600, now=minutes_ago(90))
    later = state.with_position(2, 600, now=NOON)

    assert later.tracked_at == minutes_ago(90)
    assert later.resumable_position(60 * MINUTE, NOON) == (0, 0.0)


def test_the_position_survives_midnight(tmp_path):
    """An interval worked up to 23:59 is not finished by the date changing."""
    path = tmp_path / "state.json"
    save(AppState(day=MONDAY).with_position(3, 600, now=NOON), path)

    tomorrow = load(path, today=MONDAY + timedelta(days=1))

    assert tomorrow.cycles_completed == 3
    assert tomorrow.interval_elapsed == 600
    # Still aged out on its own merits, not by the calendar.
    assert tomorrow.resumable_position(60 * MINUTE, NOON) == (3, 600)


def test_a_junk_position_reads_as_none():
    state = AppState.from_dict(
        {
            "day": MONDAY.isoformat(),
            "cycles_completed": "three",
            "interval_elapsed_seconds": None,
            "position_tracked_at": "not a timestamp",
        },
        today=MONDAY,
    )
    assert state.resumable_position(60 * MINUTE, NOON) == (0, 0.0)


# -- the two halves together ------------------------------------------


def test_a_restart_mid_interval_keeps_the_long_break_on_schedule(tmp_path):
    """The reported bug, end to end: quit and relaunch, four times over."""
    path = tmp_path / "state.json"
    config = Config()
    state = AppState(day=MONDAY)
    launched_at = NOON
    long_breaks = 0

    for _ in range(4):
        # Each launch builds a fresh engine, exactly as Application does,
        # and hands it whatever the state file still considers current.
        h = Harness(config)
        h.engine.resume(
            Position(
                *state.resumable_position(config.idle_reset_after, launched_at)
            )
        )

        h.advance(config.work_duration + 2)
        assert h.engine.state is State.BREAK
        if h.engine.snapshot().is_long_break:
            long_breaks += 1
        h.advance(config.short_break_duration + 2, active=False)

        # Quit — the position is written out — then relaunch a minute later.
        position = h.engine.position()
        state = state.with_position(
            position.completed_cycles, position.interval_elapsed,
            now=launched_at,
        )
        save(state, path)
        state = load(path, today=MONDAY)
        launched_at += timedelta(minutes=1)

    assert long_breaks == 1, "the 4th break should have been the long one"
