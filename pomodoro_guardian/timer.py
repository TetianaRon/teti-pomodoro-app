"""The Pomodoro state machine.

Deliberately pure: no threads, no sleeping, no UI, no clock of its own.
The caller feeds it wall-clock readings and it returns what changed. That
keeps the whole rhythm — including the parts that only happen after 25
minutes, or after an hour idle — testable in milliseconds, headlessly,
which matters because everything else in Phase 1 needs a real Windows
session to exercise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import DEFAULT, Config


class State(Enum):
    """Where the user is in the cycle."""

    IDLE = "idle"        # not working; watching for sustained activity
    WORK = "work"        # tracked work session running
    WARNING = "warning"  # still working, but the lock is imminent
    BREAK = "break"      # screen locked


class Event(Enum):
    """Things the app layer needs to react to.

    `update()` returns these rather than invoking callbacks so the engine
    stays free of side effects and tests can assert on the sequence.
    """

    WORK_STARTED = "work_started"
    WORK_PAUSED = "work_paused"      # stepped away; time stopped accruing
    WORK_RESUMED = "work_resumed"
    WORK_ABANDONED = "work_abandoned"  # away long enough to discard the interval
    WARNING_STARTED = "warning_started"
    BREAK_STARTED = "break_started"
    BREAK_ENDED = "break_ended"
    CYCLES_RESET = "cycles_reset"    # long-break counter cleared by an idle gap


@dataclass(frozen=True)
class Snapshot:
    """Everything the UI needs, with no access to engine internals."""

    state: State
    remaining: float          # seconds left in the current phase
    completed_cycles: int
    is_long_break: bool       # meaningful only while state is BREAK
    paused: bool              # work session stalled because you stepped away

    @property
    def locked(self) -> bool:
        return self.state is State.BREAK


class PomodoroEngine:
    """Tracks one day's work/break rhythm.

    Usage: call `update(now, last_input_at)` on a steady tick (~1s) and act
    on the returned events; call `snapshot()` to render.
    """

    def __init__(self, config: Config = DEFAULT, now: float = 0.0) -> None:
        self.config = config
        self.state = State.IDLE
        self.completed_cycles = 0

        self._last_update = now
        self._work_elapsed = 0.0     # active seconds in the current interval
        # Work is credited up to this timestamp. Tracking a watermark rather
        # than summing tick deltas is what keeps the input_gap bridge honest:
        # a short pause is paid for retroactively once you type again, while
        # genuine silence is never paid for at all.
        self._credited_through = now
        self._active_since: float | None = None  # for the start threshold
        self._break_started_at = 0.0
        self._break_is_long = False
        self._paused = False
        self._cycles_reset_pending = False  # dedupes CYCLES_RESET per idle gap

    # -- public API ---------------------------------------------------

    def update(self, now: float, last_input_at: float) -> list[Event]:
        """Advance the machine to `now`. Returns events that just occurred."""
        delta = now - self._last_update
        self._last_update = now

        # A tick far longer than expected means the machine slept or the
        # process stalled. Crediting that as work time would be wrong, and
        # so would treating it as a normal tick, so we advance the clock
        # without accruing anything and let the idle rules below decide.
        if delta < 0 or delta > self.config.max_tick:
            delta = 0.0
            # Move the watermark up too, or the first keystroke after waking
            # would retroactively buy the entire time the machine was asleep.
            self._credited_through = now

        idle_for = now - last_input_at
        is_active = idle_for <= self.config.input_gap

        events: list[Event] = []
        # A long enough absence wipes the long-break count in every state
        # except BREAK, where the lock is the reason there's no input.
        if self.state is not State.BREAK:
            events += self._maybe_reset_cycles(idle_for)

        handler = {
            State.IDLE: self._tick_idle,
            State.WORK: self._tick_working,
            State.WARNING: self._tick_working,
            State.BREAK: self._tick_break,
        }[self.state]
        events += handler(now, last_input_at, is_active)
        return events

    def snapshot(self) -> Snapshot:
        return Snapshot(
            state=self.state,
            remaining=self._remaining(),
            completed_cycles=self.completed_cycles,
            is_long_break=self._break_is_long,
            paused=self._paused,
        )

    # -- per-state handling -------------------------------------------

    def _tick_idle(
        self, now: float, last_input_at: float, is_active: bool
    ) -> list[Event]:
        if not is_active:
            self._active_since = None
            return []

        if self._active_since is None:
            # Backdate to the keystroke itself, not to the tick that noticed
            # it, so a 1s poll doesn't shave time off the threshold.
            self._active_since = last_input_at

        # Measure the span between the first and last keystroke, not up to
        # `now` — otherwise the input_gap grace period could satisfy the
        # threshold on its own and start a session you never worked for.
        span = last_input_at - self._active_since
        if span < self.config.start_threshold:
            return []

        # The qualifying stretch was real work, so it counts toward the
        # first interval rather than being discarded.
        self.state = State.WORK
        self._work_elapsed = span
        self._credited_through = last_input_at
        self._paused = False
        self._active_since = None
        return [Event.WORK_STARTED]

    def _tick_working(
        self, now: float, last_input_at: float, is_active: bool
    ) -> list[Event]:
        events: list[Event] = []
        idle_for = now - last_input_at

        if idle_for >= self.config.idle_reset_after:
            self._reset_to_idle()
            return [Event.WORK_ABANDONED]

        if is_active:
            # Credit only as far as the last real keystroke. A pause inside
            # the grace window gets paid once you resume; nothing beyond it
            # ever does, because the branch below pins the watermark to now.
            if last_input_at > self._credited_through:
                self._work_elapsed += last_input_at - self._credited_through
                self._credited_through = last_input_at
            if self._paused:
                self._paused = False
                events.append(Event.WORK_RESUMED)
        else:
            self._credited_through = now
            if idle_for >= self.config.idle_pause_after and not self._paused:
                self._paused = True
                events.append(Event.WORK_PAUSED)

        if self.state is State.WORK and self._work_elapsed >= self._warn_at():
            self.state = State.WARNING
            events.append(Event.WARNING_STARTED)

        if self._work_elapsed >= self.config.work_duration:
            events.append(self._start_break(now))

        return events

    def _tick_break(
        self, now: float, last_input_at: float, is_active: bool
    ) -> list[Event]:
        # Input during a break is being swallowed by the lock, so it can't
        # affect anything here — the break runs on wall clock alone.
        self._credited_through = now
        if now - self._break_started_at < self._break_duration():
            return []

        self.completed_cycles += 1
        # _reset_to_idle clears _active_since, so input that landed against
        # the lock screen can't hand the next session a head start.
        self._reset_to_idle()
        return [Event.BREAK_ENDED]

    # -- helpers ------------------------------------------------------

    def _start_break(self, now: float) -> Event:
        self.state = State.BREAK
        self._break_started_at = now
        self._break_is_long = (
            self.completed_cycles + 1
        ) % self.config.long_break_every == 0
        self._work_elapsed = 0.0
        self._paused = False
        return Event.BREAK_STARTED

    def _maybe_reset_cycles(self, idle_for: float) -> list[Event]:
        if idle_for < self.config.idle_reset_after:
            self._cycles_reset_pending = False
            return []
        if self._cycles_reset_pending or self.completed_cycles == 0:
            self._cycles_reset_pending = True
            return []
        self.completed_cycles = 0
        self._cycles_reset_pending = True
        return [Event.CYCLES_RESET]

    def _reset_to_idle(self) -> None:
        self.state = State.IDLE
        self._work_elapsed = 0.0
        self._paused = False
        self._active_since = None
        self._break_is_long = False

    def _warn_at(self) -> float:
        return max(0.0, self.config.work_duration - self.config.warning_lead)

    def _break_duration(self) -> float:
        return (
            self.config.long_break_duration
            if self._break_is_long
            else self.config.short_break_duration
        )

    def _remaining(self) -> float:
        if self.state is State.BREAK:
            return max(
                0.0,
                self._break_duration()
                - (self._last_update - self._break_started_at),
            )
        if self.state in (State.WORK, State.WARNING):
            return max(0.0, self.config.work_duration - self._work_elapsed)
        return 0.0
