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
    EXCLUSION_STARTED = "exclusion_started"  # call/screen share holds the break
    EXCLUSION_ENDED = "exclusion_ended"
    BREAK_DEFERRED = "break_deferred"        # custom skip bought more time


@dataclass(frozen=True)
class Position:
    """Where the rhythm stands — the part that has to survive a restart.

    The engine is rebuilt from scratch every time the app starts, and
    nothing used to carry over, so the count towards the 4th-cycle long
    break restarted with it. On a day of several restarts — routine here,
    since restarting is how a change gets picked up — the long break
    therefore never arrived at all (found in live use, 2026-08-10).

    Only the position is carried, not the day's work total: that already
    lives in `state.json` and must never be re-credited from here.
    """

    completed_cycles: int = 0
    interval_elapsed: float = 0.0   # seconds already credited to the interval

    @property
    def empty(self) -> bool:
        """Nothing worth restoring — a first run, or a stale position."""
        return self.completed_cycles <= 0 and self.interval_elapsed <= 0


@dataclass(frozen=True)
class Snapshot:
    """Everything the UI needs, with no access to engine internals."""

    state: State
    remaining: float          # seconds left in the current phase
    completed_cycles: int
    is_long_break: bool       # meaningful only while state is BREAK
    paused: bool              # work session stalled because you stepped away
    excluded: bool            # frozen by a call or screen share (SPEC §3)

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
        self._excluded = False
        # Total work credited since this engine started. The app turns the
        # delta into a persisted daily total; the engine itself has no idea
        # what day it is and deliberately keeps it that way.
        self.worked_total = 0.0
        # How much of that came from calls rather than from typing. Kept
        # apart so a day's hours can be read honestly afterwards: "2.3h
        # worked, 1.4h of it on calls" says something the total alone hides.
        self.excluded_total = 0.0
        # Set by the app once the daily cap is exceeded (SPEC §5): work
        # intervals shorten so breaks arrive often enough to be a nudge.
        self.overtime = False
        # Focus Mode (SPEC §6). Deliberately *not* the same thing as an
        # exclusion: an exclusion freezes the countdown, which would make
        # focus time invisible to the daily cap and let a 2h session be
        # worked for free. Work accrues as normal here — only the break is
        # held back, and it fires as soon as focus ends.
        self.suppress_breaks = False
        # When an exclusion lifts, idle is measured from that moment rather
        # than from the last keystroke: you were at the desk for the call,
        # you just weren't typing.
        self._resume_at: float | None = None
        # A previous run's part-finished interval, waiting to be paid into
        # the next session. Held rather than applied at once — see resume().
        self._resume_elapsed = 0.0

    # -- public API ---------------------------------------------------

    def update(
        self, now: float, last_input_at: float, excluded: bool = False
    ) -> list[Event]:
        """Advance the machine to `now`. Returns events that just occurred.

        `excluded` is SPEC §3: a call or screen share is in progress, so no
        break may start and any countdown freezes.
        """
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

        events: list[Event] = []

        # A break already under way runs its course — the lock is up, so no
        # call could have started against it. Everything else freezes.
        if self.state is not State.BREAK:
            frozen = self._apply_exclusion(now, excluded, events, delta)
            if frozen:
                return events

        # Time spent on a call still counts as being at the desk, so idle is
        # measured from whichever is later: the last keystroke, or the moment
        # the call ended. Without this a two-hour meeting would look like an
        # absence and abandon the session the instant it finished.
        if self._resume_at is not None:
            last_input_at = max(last_input_at, self._resume_at)

        idle_for = now - last_input_at
        is_active = idle_for <= self.config.input_gap

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

    def defer_break(self, seconds: float, now: float) -> Event:
        """Push the break back by `seconds` — the custom skip (SPEC §4B).

        The break is delayed, not cancelled: work resumes with exactly
        `seconds` left on the interval, so the lock returns when the bought
        time runs out. That is what makes the 60-minute daily budget mean
        something — each skip spends real minutes rather than dismissing a
        break outright.

        The cycle count is untouched: a skipped break is not a taken one,
        so it must not bring the long break closer.
        """
        self.state = State.WORK
        self._work_elapsed = max(0.0, self.work_duration() - seconds)
        self._credited_through = now
        self._break_is_long = False
        self._paused = False
        self._active_since = None
        return Event.BREAK_DEFERRED

    def position(self) -> Position:
        """The place in the cycle, for persisting across a restart.

        A break in progress is deliberately not part of this. Re-raising a
        lock at launch would be a hostile way to start, and a break that
        did not run to the end never counted towards the long one anyway —
        so quitting mid-break costs that break and nothing else.
        """
        elapsed = (
            self._work_elapsed
            if self.state in (State.WORK, State.WARNING)
            else self._resume_elapsed
        )
        return Position(self.completed_cycles, elapsed)

    def resume(self, position: Position) -> None:
        """Adopt a previous run's position (SPEC §2.2).

        The cycle count is taken as read; the part-finished interval is
        not. A saved file cannot tell the app you are at the desk, so the
        interval is held as credit and paid into the next session once the
        usual start threshold has observed real work. The credit then
        expires on exactly the idle gap that clears the cycle count, so a
        restart followed by an absence resumes nothing.
        """
        self.completed_cycles = max(0, position.completed_cycles)
        self._resume_elapsed = max(0.0, position.interval_elapsed)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            state=self.state,
            remaining=self._remaining(),
            completed_cycles=self.completed_cycles,
            is_long_break=self._break_is_long,
            paused=self._paused,
            excluded=self._excluded,
        )

    def _apply_exclusion(
        self, now: float, excluded: bool, events: list[Event], delta: float = 0.0
    ) -> bool:
        """Handle SPEC §3 freezing. Returns True if the tick should stop here.

        Two separate things happen here, and conflating them was a real bug.

        **The break is held off.** That is what an exclusion is for, and the
        interval is left exactly where it was: `_work_elapsed` never moves,
        so the countdown resumes from the same place when the call ends.

        **The time still counts as work.** A call is work whether or not
        anybody is typing, and leaving it out meant a day of meetings could
        be followed by a full cap's worth of tracked work on top — the cap
        the app exists to enforce, defeated. Measured on 2026-08-11: an
        82-minute meeting credited zero seconds, and only 2.3 of 4.7 hours
        at the desk were counted.

        Credited from `delta` rather than from the input watermark, because
        the watermark is what deliberately ignores a silent call. `delta` has
        already been zeroed upstream if the machine slept or the process
        stalled, so a laptop shut mid-meeting cannot buy hours.
        """
        if excluded:
            if not self._excluded:
                self._excluded = True
                events.append(Event.EXCLUSION_STARTED)
            if self.config.count_exclusions_as_work:
                self.worked_total += delta
                self.excluded_total += delta
            # Pin the watermark either way: the call's duration must never be
            # credited a *second* time by the input rules once typing resumes.
            self._credited_through = now
            # Don't let a call build toward the start threshold either: a
            # session should begin from real work, not from being on a call.
            self._active_since = None
            return True

        if self._excluded:
            self._excluded = False
            self._resume_at = now
            events.append(Event.EXCLUSION_ENDED)
        return False

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
        # first interval rather than being discarded — as does whatever a
        # previous run had already banked against this same interval.
        self.state = State.WORK
        self._work_elapsed = span + self._resume_elapsed
        self._resume_elapsed = 0.0
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
                credited = last_input_at - self._credited_through
                self._work_elapsed += credited
                self.worked_total += credited
                self._credited_through = last_input_at
            if self._paused:
                self._paused = False
                events.append(Event.WORK_RESUMED)
        else:
            self._credited_through = now
            if idle_for >= self.config.idle_pause_after and not self._paused:
                self._paused = True
                events.append(Event.WORK_PAUSED)

        if self.suppress_breaks:
            # Focus Mode: keep counting, but neither warn nor lock. The
            # elapsed interval is left standing, so the break arrives
            # immediately once focus ends rather than being forgiven.
            return events

        if self.state is State.WORK and self._work_elapsed >= self._warn_at():
            self.state = State.WARNING
            events.append(Event.WARNING_STARTED)

        if self._work_elapsed >= self.work_duration():
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
        # An hour away wipes the whole position, not just the cycle count: a
        # part-interval carried over from a previous run is no more valid
        # after that gap than a live one would be.
        self._resume_elapsed = 0.0
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

    def work_duration(self) -> float:
        """Length of a work interval — shortened past the daily cap."""
        return (
            self.config.overtime_work_duration
            if self.overtime
            else self.config.work_duration
        )

    def warning_lead(self) -> float:
        return (
            self.config.overtime_warning_lead
            if self.overtime
            else self.config.warning_lead
        )

    def _warn_at(self) -> float:
        return max(0.0, self.work_duration() - self.warning_lead())

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
            return max(0.0, self.work_duration() - self._work_elapsed)
        return 0.0
