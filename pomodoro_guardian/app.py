"""Wires the three Phase 1 pieces together and runs the loop.

tkinter insists on owning the main thread, so the engine is ticked from
`root.after()` rather than a loop of our own. The activity monitor is the
only threaded part, and it only ever writes a timestamp.
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

from datetime import date, datetime

from . import caps
from . import history as history_module
from . import settings as settings_module
from . import state as state_module
from .activity import ActivityMonitor, create_monitor
from .calendar_watch import CalendarWatcher
from .config import DEFAULT, Config
from .exclusions import (
    CombinedDetector,
    Detector,
    Exclusion,
    MeetingDetector,
    NullDetector,
    create_detector,
)
from . import runtime, sounds, summary, tray, walking
from .overlay import LockOverlay, SkipOffer, SkipOption, WarningBanner
from .pill import CountdownPill
from .timer import Event, PomodoroEngine, Position, State

TICK_MS = 1000


class Application:
    def __init__(
        self,
        config: Config = DEFAULT,
        monitor: ActivityMonitor | None = None,
        dry_run: bool = False,
        detector: Detector | None = None,
        watcher: CalendarWatcher | None = None,
        state_file: Path | None = None,
        settings: settings_module.Settings | None = None,
        settings_file: Path | None = None,
    ) -> None:
        self.config = config
        self.settings = settings or settings_module.Settings(config=config)
        self.dry_run = dry_run
        self.monitor = monitor if monitor is not None else create_monitor()
        self.watcher = watcher
        device_detector = (
            detector
            if detector is not None
            else create_detector(
                camera=config.exclude_on_camera,
                microphone=config.exclude_on_microphone,
            )
        )
        # The calendar meeting skip (SPEC §4A) is just another exclusion —
        # §3 and §4A both mean "do not lock right now".
        self.detector: Detector = (
            CombinedDetector(device_detector, MeetingDetector(watcher))
            if watcher is not None and watcher.configured
            else device_detector
        )
        # `--config PATH` has to move the whole data directory, not half of
        # it. This was hardcoded to the default, so a run against a scratch
        # config wrote its state where it was told and its history into the
        # real log — which is how a test run put a zeroed snapshot and a
        # false app_stopped into a live day (2026-08-10).
        self._settings_file = settings_file or settings_module.default_path()
        self._state_file = state_file
        self._state = state_module.load(state_file)
        self.engine = PomodoroEngine(config, now=time.monotonic())
        self._excluded_since: float | None = None
        # Kept apart from `_excluded_since`, which the long-call warning
        # clears on the tick the call ends — before the event that needs it
        # is handled.
        self._exclusion_began: float | None = None
        self._exclusion_reason = ""
        self._warned_long_exclusion = False
        self._exclusion = Exclusion()
        self._last_worked_total = 0.0
        self._last_state_save = 0.0
        self._cap: caps.CapStatus | None = None
        self._announced_over = False
        self.history = history_module.History(
            history_module.history_path(self._settings_file)
        )
        self._last_snapshot = 0.0
        self._logged_day_type = ""
        # Captured when a break starts, because the engine clears both the
        # moment it ends: `_reset_to_idle` sets `_break_is_long` back to
        # False before BREAK_ENDED is ever handled, which is why every break
        # in the log up to now recorded itself as "short" — including the
        # long ones (found 2026-08-10, 17 rows deep).
        self._break_was_long = False
        self._break_length = 0.0
        self._break_input_seconds = 0.0
        self._last_input_seen = 0.0
        # After the history log exists, so the resume can be recorded there.
        self._resume_position()
        # Resolved once: a missing file should be reported at startup, not
        # discovered as silence at the moment a break lands.
        # Falls back to the first available clip when nothing is chosen, so
        # dropping files in is enough to get a chime; the startup log names
        # whichever it picked, so it is never a mystery.
        self._sound_start = sounds.resolve(
            self.settings.break_start_sound, fallback_first=True
        )
        self._sound_end = sounds.resolve(
            self.settings.break_end_sound, fallback_first=True
        )

        self._root = tk.Tk()
        self._root.withdraw()  # the controller window is never shown
        self._root.title("Pomodoro Guardian")
        self.overlay = LockOverlay(
            self._root, config,
            skip_offer=self._skip_offer,
            on_skip=self._take_skip,
            on_emergency=self._take_emergency,
            walk_state=self._walk_state,
            on_stop_walk=self._stop_walk,
        )
        self.banner = WarningBanner(self._root, config)
        self.pill = CountdownPill(self._root, config)
        self.tray_status = tray.TrayStatus()
        self.tray = tray.TrayIcon(self.tray_status)
        self.summary_window = summary.SummaryWindow(self._root)
        self.walk_prompt = walking.WalkPrompt(
            self._root,
            on_start=self._start_walk,
            on_stop=self._stop_walk,
        )

    # -- custom skip (SPEC §4B) ---------------------------------------

    def _skip_offer(self) -> SkipOffer:
        """What the hold-Escape menu should show right now."""
        budget = self.config.custom_skip_daily_budget
        options = tuple(
            SkipOption(
                seconds=seconds,
                label=f"{seconds / 60:.0f} min",
                enabled=self._state.can_skip(seconds, budget),
            )
            for seconds in self.config.custom_skip_options
        )
        return SkipOffer(
            options,
            self._state.skip_remaining(budget),
            emergency=self._emergency_option(),
            note=self._cap.describe() if self._cap else "",
        )

    def _emergency_option(self) -> SkipOption | None:
        """Emergency Mode, offered only once the cap is actually reached.

        Showing it earlier would make it look like an ordinary skip; the
        weekly budget is what keeps it rare (SPEC §5).
        """
        if self._cap is None or not self._cap.over:
            return None
        grant = self.config.emergency_grant_hours
        weekly = self.settings.emergency_hours_per_week
        left = self._state.emergency_remaining(weekly)
        return SkipOption(
            seconds=grant * 3600,
            label=f"Emergency +{grant:.0f}h   ({left:.0f}h left this week)",
            enabled=self._state.can_use_emergency(grant, weekly),
        )

    def _take_emergency(self) -> None:
        """Grant another hour on top of today's cap (SPEC §5)."""
        grant = self.config.emergency_grant_hours
        weekly = self.settings.emergency_hours_per_week
        if not self._state.can_use_emergency(grant, weekly):
            self._log("emergency mode unavailable — weekly budget spent")
            return
        self._state = self._state.with_emergency(grant)
        self._save_state()
        self.overlay.release()
        left = self._state.emergency_remaining(weekly)
        self._log(
            f"emergency mode: +{grant:.0f}h "
            f"({left:.0f}h of weekly budget left)"
        )
        self.history.record(
            history_module.EMERGENCY_USED, seconds=grant * 3600,
            detail=f"{left:.0f}h left this week",
        )

    # -- walking (SPEC §7) --------------------------------------------

    def _walk_state(self) -> tuple[bool, float]:
        """Whether a walk is running, and how long it has run.

        Read by the lock screen, which offers a key to stop it — the walk
        prompt and the tray are both unreachable behind a lock.
        """
        return self._state.walking, self._state.walked_including_current()

    def _start_walk(self) -> None:
        self._state = self._state.start_walk()
        self._save_state()
        self._log("walking started")

    def _stop_walk(self) -> None:
        before = self._state.walked_today
        self._state = self._state.stop_walk()
        self._save_state()
        seconds = self._state.walked_today - before
        total = self._state.walked_today / 60
        target = self.settings.walking_target_minutes
        # Seconds under a minute: "0 min" for a short walk reads as a bug.
        spent = (
            f"{seconds:.0f} sec" if seconds < 60 else f"{seconds / 60:.0f} min"
        )
        self._log(
            f"walking stopped — {spent} "
            f"({total:.0f} of {target:.0f} min today)"
        )
        self.history.record(
            history_module.WALK_ENDED, seconds=seconds,
            detail=f"{total:.0f} of {target:.0f} min today",
        )
        if total >= target:
            self.walk_prompt.hide()

    # -- Focus Mode (SPEC §6) -----------------------------------------

    def _start_focus(self) -> None:
        """Suppress breaks for a deep-work stretch, once a day."""
        if not self._state.can_focus(self.settings.focus_uses_per_day):
            reason = (
                "already running" if self._state.focusing
                else "already used today"
            )
            self._log(f"focus mode unavailable — {reason}")
            return
        self._state = self._state.start_focus()
        self._save_state()
        self._log(
            f"focus mode started — up to "
            f"{self.settings.focus_max_hours:.0f}h, breaks suppressed"
        )
        self.history.record(history_module.FOCUS_STARTED)

    def _stop_focus(self, expired: bool = False) -> None:
        if not self._state.focusing:
            return
        self._state = self._state.stop_focus()
        self._save_state()
        self._log(
            "focus mode ended — its two hours are up" if expired
            else "focus mode ended"
        )
        self.history.record(
            history_module.FOCUS_ENDED,
            detail="expired" if expired else "stopped",
        )

    def _update_focus(self) -> None:
        """Apply Focus Mode to the engine, and end it when its time is up."""
        if self._state.focus_expired(self.settings.focus_max_hours):
            self._stop_focus(expired=True)
        # Breaks are suppressed, but work still accrues — focus time has to
        # count against the daily cap or a 2h session would be free.
        self.engine.suppress_breaks = self._state.focusing

    # -- tray (SPEC §9) -----------------------------------------------

    def _drain_tray(self) -> None:
        """Handle menu clicks. They arrive on pystray's thread, so they are
        queued there and acted on here, where tkinter is safe to touch."""
        while True:
            try:
                action = self.tray.actions.get_nowait()
            except queue.Empty:
                return
            if action == tray.START_WALK:
                self._start_walk()
            elif action == tray.STOP_WALK:
                self._stop_walk()
            elif action == tray.START_FOCUS:
                self._start_focus()
            elif action == tray.STOP_FOCUS:
                self._stop_focus()
            elif action == tray.SHOW_HISTORY:
                self.summary_window.show()
                self.summary_window.refresh(
                    self.history, self._cap, self._state, self.settings,
                    force=True,
                )
            elif action == tray.OPEN_SETTINGS:
                self._open_settings()
            elif action == tray.TOGGLE_STARTUP:
                now_on = runtime.set_start_with_windows(
                    not runtime.starts_with_windows()
                )
                self._log(
                    f"start with Windows: {'on' if now_on else 'off'}"
                )
            elif action == tray.SET_DAY_OFF:
                self._set_override(state_module.NON_WORKING)
            elif action == tray.SET_WORKING_DAY:
                self._set_override(state_module.WORKING)
            elif action == tray.CLEAR_OVERRIDE:
                self._set_override(None)
            elif action == tray.QUIT:
                self._log("quitting from the tray")
                self._root.quit()

    def _set_override(self, kind: str | None) -> None:
        """Correct the day's classification by hand (SPEC §5a)."""
        if kind == state_module.WORKING and not self._state.can_raise(
            self.settings.override_raises_per_month
        ):
            self._log("no day-type raises left this month")
            return
        if kind == state_module.WORKING and self._state.day_type_override == kind:
            return   # already raised today; don't spend a second one
        self._state = self._state.with_override(kind)
        self._save_state()
        self._update_cap()
        self._log(f"day type: {self._cap.day_type.description}")
        self.history.record(
            history_module.OVERRIDE_SET, detail=str(kind),
        )

    def _open_settings(self) -> None:
        from .setup_dialog import run_setup

        saved = run_setup(
            self.settings, self._settings_file, first_run=False,
            parent=self._root,
        )
        if saved is None:
            return
        # Caps, walking and calendar settings are read fresh every tick, so
        # they apply at once. The rhythm and lock values are baked into the
        # engine and overlay at construction, so those need a restart.
        self.settings = saved
        self._log("settings saved — rhythm and lock changes need a restart")

    def _status_line(self, snapshot) -> str:
        """One line for the tray, saying how long until the next break.

        The question this answers is "have I done 25 minutes yet?", which
        is genuinely hard to know otherwise: work only accrues while you
        are actually typing, so a wall-clock hour of reading counts for
        very little and the interval is nothing like 25 minutes long.
        """
        if snapshot.excluded:
            reason = self._exclusion.describe()
            return f"holding off — {reason}" if reason else "holding off"
        if self._state.focusing:
            left = self._state.focus_remaining(self.settings.focus_max_hours)
            return f"focus mode — breaks off for {_short(left)}"
        if snapshot.state is State.BREAK:
            return f"on a break — {_short(snapshot.remaining)} left"
        if snapshot.state in (State.WORK, State.WARNING):
            left = _short(snapshot.remaining)
            # Naming the long break makes the cycle count visible, which it
            # otherwise never is: a long break that fails to arrive looks
            # exactly like one that was not due yet.
            long_next = self._next_break_is_long(snapshot)
            kind = "a long break" if long_next else "a break"
            if snapshot.paused:
                return f"paused — {left} to {kind} when you resume"
            return f"{left} to {kind}"
        return "watching for activity"

    def _next_break_is_long(self, snapshot) -> bool:
        every = self.config.long_break_every
        return every > 0 and (snapshot.completed_cycles + 1) % every == 0

    def _badge(self, snapshot) -> tuple[str, str]:
        """The countdown for the taskbar pill, as (minutes, tone).

        Answers "have I done 25 minutes yet?" at a glance, which is
        otherwise genuinely hard to know: work only accrues while you are
        actually typing, so a wall-clock hour of reading counts for very
        little and the interval is nothing like 25 minutes long. That was
        only in the hover tooltip, which means it was only ever read on
        purpose — and the number matters most when you are absorbed enough
        not to think of looking.

        Minutes only, rounded up, and never "0": a countdown sitting on
        zero through its last minute reads as stuck.
        """
        if self._state.focusing:
            return "", "held"          # no break is coming; nothing to count
        if snapshot.state is State.BREAK:
            return self._minutes(snapshot.remaining), "break"
        if snapshot.state not in (State.WORK, State.WARNING):
            return "", "work"          # idle: the plain tomato
        if snapshot.excluded or snapshot.paused:
            # Frozen by a call, or stopped because you stepped away. Shown
            # in a different colour rather than hidden: the number is still
            # true, it just isn't moving.
            return self._minutes(snapshot.remaining), "held"
        tone = "long" if self._next_break_is_long(snapshot) else "work"
        return self._minutes(snapshot.remaining), tone

    @staticmethod
    def _minutes(seconds: float) -> str:
        """The pill's number. Shares `ceil_minutes` with the tooltip, which
        is the only thing stopping the two disagreeing again."""
        return str(ceil_minutes(seconds))

    def _update_pill(self, snapshot) -> None:
        """The standing countdown in the bottom-right corner.

        Yields the corner whenever the warning is up: they are the same pill
        in the same place, and the warning is the one with something to say.
        Switched off entirely by `show_countdown` — unlike the warning, which
        is the app doing its job rather than a convenience.
        """
        if self.dry_run or not self.config.show_countdown:
            self.pill.hide()
            return
        if self.banner.visible:
            self.pill.hide()
            return
        badge, tone = self._badge(snapshot)
        self.pill.update(f"{badge} min" if badge else "", tone)

    def _refresh_tray(self) -> None:
        status = self.tray_status
        snapshot = self.engine.snapshot()
        walked = self._state.walked_including_current() / 60
        target = self.settings.walking_target_minutes

        status.summary = self._status_line(snapshot)
        status.cap_line = self._cap.describe() if self._cap else ""
        status.walk_line = f"walked {walked:.0f} of {target:.0f} min"
        status.walking = self._state.walking
        status.focusing = self._state.focusing
        if self._state.focusing:
            left = self._state.focus_remaining(self.settings.focus_max_hours)
            status.focus_label = f"Stop Focus Mode ({left / 60:.0f} min left)"
            status.focus_enabled = True
        elif self._state.can_focus(self.settings.focus_uses_per_day):
            status.focus_label = (
                f"Start Focus Mode (up to "
                f"{self.settings.focus_max_hours:.0f}h)"
            )
            status.focus_enabled = True
        else:
            status.focus_label = "Focus Mode — used today"
            status.focus_enabled = False
        status.override = self._state.day_type_override
        status.starts_with_windows = runtime.starts_with_windows()
        status.raises_left = max(
            0,
            self.settings.override_raises_per_month
            - self._state.raises_used_this_month(),
        )
        self.tray.refresh()

    def _update_walking(self) -> None:
        """Prompt at the configured times, and keep the window current."""
        if self.dry_run:
            return
        now = datetime.now().time()
        due = walking.due_prompt(
            now,
            list(self.settings.walking_reminder_times),
            self._state.walk_prompts_done,
        )
        if due is not None:
            self._state = self._state.with_prompt_shown(due)
            self._save_state()
            self._log(f"walking reminder ({due})")
            self.walk_prompt.show("Time to walk")

        if self.walk_prompt.visible:
            self.walk_prompt.update(
                walking=self._state.walking,
                walked_seconds=self._state.walked_including_current(),
                target_seconds=self.settings.walking_target_minutes * 60,
            )

    def _update_cap(self) -> None:
        """Recompute where the day stands, and shorten intervals if over."""
        today = date.today()
        longest = (
            self.watcher.longest_busy_hours(today)
            if self.watcher is not None
            else None
        )
        day_type = caps.classify_day(
            today,
            longest,
            self.settings.day_off_block_hours,
            self._state.day_type_override,
        )
        self._cap = caps.status(
            self._state,
            day_type,
            self.settings.working_day_cap_hours,
            self.settings.non_working_day_cap_hours,
            self.settings.walking_target_minutes,
        )
        # Past the cap the work interval shortens, so breaks become a
        # standing nudge rather than the app switching itself off.
        self.engine.overtime = self._cap.over

        # Record the classification whenever it changes. This is the log's
        # audit trail: a day misjudged — a holiday read as a working day,
        # an override that failed to apply — is otherwise invisible once
        # the day rolls over, and those are exactly the bugs that take
        # days to notice.
        description = day_type.description
        if description != self._logged_day_type:
            self._logged_day_type = description
            self.history.record(
                history_module.DAY_CLASSIFIED,
                seconds=self._cap.effective_seconds,
                detail=(
                    f"{description}; cap "
                    f"{self._cap.effective_seconds / 3600:.2f}h"
                ),
            )

        if self._cap.over and not self._announced_over:
            self._announced_over = True
            self._log(
                f"over the daily cap — {self._cap.describe()}; breaks now every "
                f"{self.config.overtime_work_duration / 60:.0f} min"
            )
            self.history.record(
                history_module.CAP_REACHED, seconds=self._cap.worked_seconds,
                detail=self._cap.describe(),
            )
        elif not self._cap.over:
            self._announced_over = False

    # -- the place in the cycle (SPEC §2.2) ---------------------------

    def _resume_position(self) -> None:
        """Pick up the previous run's place in the cycle.

        The app is restarted several times on an ordinary day here — that
        is how a change gets picked up — and the engine is built fresh each
        time. Nothing carried over, so every restart began again at the
        first of four intervals and the long break never arrived on a day
        of restarts (found in live use, 2026-08-10).

        The saved position is only honoured while it is recent, measured by
        `idle_reset_after` — the same gap that discards a part-interval and
        clears the cycle count during a run. Only the timing is restored:
        the day's work total was already banked in `state.json` as it
        accrued, so nothing here re-credits it.
        """
        position = Position(
            *self._state.resumable_position(self.config.idle_reset_after)
        )
        if position.empty:
            return
        self.engine.resume(position)
        cycle = position.completed_cycles + 1
        into = position.interval_elapsed
        detail = f"cycle {cycle} of {self.config.long_break_every}"
        self._log(
            f"resuming a previous run — {detail}"
            + (f", {into / 60:.0f} min into the interval" if into >= 60 else "")
        )
        self.history.record(
            history_module.CYCLES_RESUMED, seconds=into, detail=detail,
        )

    # -- was the break actually taken? --------------------------------

    def _track_break_input(self, snapshot) -> None:
        """Count genuine input arriving during a break.

        Only reachable when the lock is not blocking input — but then it is
        the difference between a history that means something and one that
        flatters. Without it every break that merely *elapsed* is filed as
        taken, and the log quietly becomes useless for the one question it
        is kept to answer.

        Measured from how far the input watermark advanced rather than from
        the idle window, so a single mouse nudge buys one moment rather
        than a whole active second. Capped at a tick, or waking the machine
        would credit the entire time it slept as work through the break.
        """
        last = self.monitor.last_input_at
        if snapshot.state is not State.BREAK:
            self._last_input_seen = last
            return
        if last > self._last_input_seen:
            self._break_input_seconds += min(
                TICK_MS / 1000.0, last - self._last_input_seen
            )
        self._last_input_seen = last

    def _break_was_ignored(self) -> bool:
        """Whether enough work happened during the break to call it worked."""
        if self._break_length <= 0:
            return False
        threshold = self.config.break_ignored_fraction * self._break_length
        return self._break_input_seconds >= threshold

    def _track_position(self) -> None:
        """Keep the persisted state in step with the engine's position.

        Written every tick but saved on `_record_work`'s slower cadence,
        plus once on a clean shutdown — so a hard kill loses at most half a
        minute of the current interval, and never the cycle count.
        """
        position = self.engine.position()
        self._state = self._state.with_position(
            position.completed_cycles, position.interval_elapsed
        )

    def _record_work(self, now: float) -> None:
        """Fold the engine's credited work into the persisted daily total."""
        delta = self.engine.worked_total - self._last_worked_total
        self._last_worked_total = self.engine.worked_total
        if delta > 0:
            self._state = self._state.with_work(delta)
        # Persisted periodically rather than every tick: a crash costs at
        # most this much of the tally, and the disk stays quiet.
        if now - self._last_state_save >= 30:
            self._last_state_save = now
            self._save_state()
        # A running total every few minutes, so a day survives a crash and
        # the shape of it can be seen afterwards.
        if now - self._last_snapshot >= 300:
            self._last_snapshot = now
            self.history.snapshot(
                self._state.worked_today,
                self._state.walked_including_current(),
            )

    def _save_state(self) -> None:
        try:
            state_module.save(self._state, self._state_file)
        except OSError as exc:
            self._log(f"warning: could not save state ({exc})")

    def _take_skip(self, seconds: float) -> None:
        """Spend part of the daily budget and push the break back."""
        self._state = self._state.with_skip(seconds)
        self._save_state()
        self.overlay.release()
        self.engine.defer_break(seconds, time.monotonic())
        left = self._state.skip_remaining(self.config.custom_skip_daily_budget)
        self._log(
            f"break skipped for {seconds / 60:.0f} min "
            f"({left / 60:.0f} min of budget left today)"
        )
        self.history.record(
            history_module.BREAK_SKIPPED, seconds=seconds,
            detail=f"{left / 60:.0f} min left",
        )

    def _roll_state(self) -> None:
        """Reset the day's budgets when the date changes under a long run."""
        today = date.today()
        if self._state.day != today:
            self._state = self._state.rolled_to(today)
            self._log("new day — skip budget reset")

    def run(self) -> None:
        self.monitor.start()
        if self.watcher is not None:
            self.watcher.start()
        if not self.dry_run and self.tray.start():
            self._log("tray icon ready — click it for walking, settings, quit")
        # Start and stop are recorded so a gap in the log can be told from
        # a crash: a start with no matching stop is the app dying.
        self.history.record(history_module.APP_STARTED)
        for what, path in (("break start", self._sound_start),
                           ("break end", self._sound_end)):
            self._log(
                f"{what} chime: {path.name}" if path
                else f"{what} chime: none — add a file to "
                     f"assets/{sounds.SOUNDS_DIRNAME}/"
            )
        self._log(
            f"watching for activity — "
            f"{self.config.work_duration / 60:.0f}m work / "
            f"{self.config.short_break_duration / 60:.0f}m break, "
            f"long break every {self.config.long_break_every}"
        )
        if self.dry_run:
            self._log("dry run: state changes only, the screen will not lock")
        self._root.after(TICK_MS, self._tick)
        try:
            self._root.mainloop()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        # Ordering matters: releasing the overlay is what stops input
        # suppression, so it goes first no matter how we got here.
        self.overlay.release()
        self.banner.hide()
        self.pill.hide()
        self.walk_prompt.hide()
        self.summary_window.hide()
        self.tray.stop()
        self.monitor.stop()
        if self.watcher is not None:
            self.watcher.stop()
        self._save_state()   # don't lose the tally on a clean exit
        self.history.snapshot(
            self._state.worked_today, self._state.walked_including_current()
        )
        self.history.record(history_module.APP_STOPPED)

    # -- loop ---------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        self._roll_state()
        exclusion = self.detector.check()
        self._exclusion = exclusion
        events = self.engine.update(
            now, self.monitor.last_input_at, excluded=exclusion.active
        )
        snapshot = self.engine.snapshot()
        self._drain_tray()
        self._track_break_input(snapshot)
        self._track_position()
        self._record_work(now)
        self._update_focus()
        self._update_walking()
        self._update_cap()
        self._refresh_tray()
        self._update_pill(snapshot)
        self.summary_window.refresh(
            self.history, self._cap, self._state, self.settings
        )
        notice = self._watch_long_exclusion(now, exclusion.active)
        if notice is None and self._cap is not None and self._cap.over:
            # A standing marker, so being over the cap is visible without
            # having to wait for the next break to say so.
            notice = f"Overtime — {self._cap.describe()}"

        for event in events:
            self._handle(event, snapshot)

        self._update_banner(snapshot, exclusion, notice)
        if snapshot.state is State.BREAK and self.overlay.visible:
            self.overlay.tick(snapshot.remaining)

        # The safety hold tears the window down without ending the break;
        # without this the engine would sit in BREAK with nothing on screen.
        if (
            snapshot.state is State.BREAK
            and not self.overlay.visible
            and not self.dry_run
            and self.overlay.released_early
        ):
            self._log("break released early via safety hold")
            self.engine.state = State.IDLE

        self._root.after(TICK_MS, self._tick)

    def _handle(self, event: Event, snapshot) -> None:
        if event is Event.WORK_STARTED:
            self._log("work session started")
        elif event is Event.WORK_PAUSED:
            self._log("paused — no input")
        elif event is Event.WORK_RESUMED:
            self._log("resumed")
        elif event is Event.WORK_ABANDONED:
            self._log("session dropped after a long idle gap")
        elif event is Event.CYCLES_RESET:
            self._log("long-break cycle count reset")
            self.history.record(
                history_module.CYCLES_RESET,
                detail=(
                    f"after {self.config.idle_reset_after / 60:.0f} min idle"
                ),
            )
        elif event is Event.EXCLUSION_STARTED:
            self._exclusion_began = time.monotonic()
            self._exclusion_reason = self._exclusion.describe()
            counted = (
                "counts as work" if self.config.count_exclusions_as_work
                else "not counted as work"
            )
            self._log(f"holding off — {self._exclusion_reason} ({counted})")
            self.history.record(
                history_module.EXCLUDED, detail=self._exclusion_reason
            )
        elif event is Event.EXCLUSION_ENDED:
            # Recorded with its duration, so a day's meeting hours can be
            # read rather than inferred from gaps between snapshots — which
            # is how the 82 minutes that started all this had to be found.
            held = (
                time.monotonic() - self._exclusion_began
                if self._exclusion_began is not None else 0.0
            )
            self._log(
                f"clear again after {_short(held)} — countdown resumes"
            )
            self.history.record(
                history_module.EXCLUSION_ENDED, seconds=held,
                detail=self._exclusion_reason or "clear",
            )
            self._exclusion_began = None
        elif event is Event.WARNING_STARTED:
            self._log(
                f"break in {self.engine.warning_lead() / 60:.0f} min"
            )
            if not self.dry_run:
                self.banner.show()
        elif event is Event.BREAK_STARTED:
            kind = "long break" if snapshot.is_long_break else "break"
            self._log(f"{kind} started — locking")
            # Remembered now because the engine clears both as the break
            # ends, before BREAK_ENDED is handled.
            self._break_was_long = snapshot.is_long_break
            self._break_length = snapshot.remaining
            self._break_input_seconds = 0.0
            self.banner.hide()
            if not self.dry_run:
                # remaining == the full break length at the moment it starts,
                # which the overlay needs to show a "back at HH:MM" time.
                self.overlay.lock(snapshot.is_long_break, snapshot.remaining)
            # Chimed *after* locking, because lock() decides whether to pause
            # media by listening for audio — and a chime playing first is
            # audio. That ordering un-paused a deliberately paused video.
            sounds.play(self._sound_start)
        elif event is Event.BREAK_ENDED:
            length = "long" if self._break_was_long else "short"
            worked = self._break_input_seconds
            ignored = self._break_was_ignored()
            self._log(
                f"break over (cycle {snapshot.completed_cycles}) — unlocked"
                + (f" — but worked through {worked / 60:.0f} min of it"
                   if ignored else "")
            )
            sounds.play(self._sound_end)
            # The cycle still counts towards the long break either way. The
            # break did elapse, and withholding the long one on a heuristic
            # about mouse movement would make enforcement depend on a guess.
            self.history.record(
                history_module.BREAK_IGNORED if ignored
                else history_module.BREAK_TAKEN,
                seconds=worked,
                detail=f"{length}; {worked / 60:.1f} min of input",
            )
            self.overlay.release()

    def _update_banner(self, snapshot, exclusion, notice: str | None) -> None:
        """Decide what the corner banner shows, from state rather than events.

        Driven by the current state each tick instead of by transitions: an
        earlier event-driven version hid the banner when a call started and
        had no way to bring it back if the call ended while the warning was
        still running.
        """
        if self.dry_run:
            return
        if notice is not None:
            self.banner.notice(notice)
        elif snapshot.state is State.WARNING and not exclusion.active:
            if not self.banner.visible:
                self.banner.show()
            self.banner.tick(snapshot.remaining)
        elif self.banner.visible:
            self.banner.hide()

    def _watch_long_exclusion(self, now: float, excluded: bool) -> str | None:
        """Warn if breaks have been held off for an implausibly long time.

        A stuck microphone — a conferencing app that never releases it, a
        recording tool left running — would otherwise silently switch break
        enforcement off for the rest of the day. This only warns: the
        exclusion still stands, because overriding it would mean locking
        the screen during what might be a genuine call.

        Note this measures one *continuous* stretch. Two calls with any gap
        between them each start fresh, so ordinary back-to-back meetings
        will not trigger it.
        """
        if not excluded:
            self._excluded_since = None
            self._warned_long_exclusion = False
            return None
        if self._excluded_since is None:
            self._excluded_since = now
            return None

        held = now - self._excluded_since
        if held < self.config.exclusion_warn_after:
            return None
        if not self._warned_long_exclusion:
            self._warned_long_exclusion = True
            self._log(
                f"note: breaks held off for {held / 3600:.1f}h by "
                f"{self._exclusion.describe()} — if that looks wrong, some app "
                f"is holding the device open"
            )
        return f"No breaks for {held / 3600:.0f}h — {self._exclusion.describe()}"

    def _log(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pomodoro-guardian",
        description="Phase 1 core loop: detect work, enforce breaks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log state changes without ever locking the screen",
    )
    parser.add_argument(
        "--demo",
        type=float,
        metavar="FACTOR",
        help=(
            "divide every duration by FACTOR for a quick smoke test "
            "(e.g. --demo 60 turns 25m/5m into 25s/5s)"
        ),
    )
    parser.add_argument(
        "--no-safety-unlock",
        action="store_true",
        help="disable the hold-Escape release (use once you trust the lock)",
    )
    parser.add_argument(
        "--remind-only",
        action="store_true",
        help="cover the screen for breaks without blocking input, for this run",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="open the settings window, then exit",
    )
    parser.add_argument(
        "--history",
        nargs="?",
        const=7,
        type=int,
        metavar="DAYS",
        help="print the last N days (default 7), then exit",
    )
    parser.add_argument(
        "--events",
        nargs="?",
        const=40,
        type=int,
        metavar="N",
        help="print the last N raw events — the view for chasing a bug",
    )
    parser.add_argument(
        "--test-sounds",
        action="store_true",
        help="play the break start and end chimes, then exit",
    )
    parser.add_argument(
        "--shortcuts",
        action="store_true",
        help="create Start Menu and desktop shortcuts, then exit",
    )
    parser.add_argument(
        "--no-exclusions",
        action="store_true",
        help="ignore calls and screen sharing; always enforce breaks",
    )
    parser.add_argument(
        "--exclusions",
        action="store_true",
        help="report what is currently holding breaks off, then exit",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="report what this machine can and cannot do, then exit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="use a different settings file (default: %%APPDATA%%\\"
             "PomodoroGuardian\\config.json)",
    )
    return parser


def report_doctor(path: Path | None = None) -> int:
    """`--doctor`: what this machine can do, and what the app loses if not.

    Written to be pasted somewhere useful. On a fully working Windows
    machine it is a receipt; on a half-ported Mac it is the to-do list, in
    priority order, with the consequence of each gap spelled out — because
    the app degrades quietly by design, and quiet degradation is only
    honest if something says so out loud.
    """
    from . import platform as platform_module

    print(f"Pomodoro Guardian — capabilities on {platform_module.platform_name()}")
    print(f"Python {sys.version.split()[0]}")

    # Whether blocking is *wanted* is a setting; whether it is *possible* is
    # the OS's answer below. Both are printed, because "no lock" for the two
    # reasons needs fixing in two completely different places.
    settings = settings_module.load(path)
    print(
        "Break enforcement: "
        + ("blocking input (settings: on)" if settings.config.block_input
           else "reminder only (settings: block_input is off)")
        + "\n"
    )

    missing = []
    for cap, available, detail in platform_module.report():
        mark = "ok  " if available else "MISSING"
        print(f"  [{mark}] {cap.what}")
        print(f"           {detail}")
        if not available:
            missing.append(cap)
        print()

    if not missing:
        print("Everything the app needs is available here.")
        return 0

    print(f"{len(missing)} missing, most important first:\n")
    for cap in missing:
        print(f"  {cap.what}")
        print(f"    without it : {cap.without_it}")
        print(f"    reference  : {cap.reference}")
        if cap.on_macos and platform_module.MACOS:
            print(f"    on macOS   : {cap.on_macos}")
        print()
    if platform_module.MACOS:
        print("Porting notes: docs/MAC-PORT.md")
    return 0


def report_exclusions(
    config: Config, settings, settings_file: Path | None = None
) -> int:
    """`--exclusions`: say what is holding breaks off right now, and why."""
    from .exclusions import devices_in_use

    detector = create_detector(
        camera=config.exclude_on_camera,
        microphone=config.exclude_on_microphone,
    )
    print("Never-interrupt exclusions (SPEC §3, §4A)\n")
    print(f"  camera in use by  : {_or_none(devices_in_use('webcam'))}")
    print(f"  microphone in use : {_or_none(devices_in_use('microphone'))}")

    watcher = CalendarWatcher(
        settings.calendar_url,
        day_off_hours=settings.day_off_block_hours,
        meeting_lead=settings.meeting_lead_minutes * 60,
    )
    meeting = None
    if watcher.configured:
        print("\n  fetching calendar…", flush=True)
        watcher.refresh_now()
        meeting = watcher.meeting_now()
    print(f"  calendar          : {watcher.status()}")
    if meeting is not None:
        print(
            f"  meeting now       : {meeting.hours:.2f}h block, ends "
            f"{meeting.end.astimezone():%H:%M}"
        )
    else:
        print("  meeting now       : none")

    combined = (
        CombinedDetector(detector, MeetingDetector(watcher))
        if watcher.configured
        else detector
    )
    exclusion = combined.check()
    print()
    if exclusion.active:
        print(f"  -> BREAKS HELD OFF: {exclusion.describe()}")
    else:
        print("  -> nothing blocking a break")

    # The same data directory the app itself would use, so `--config` does
    # not report one file's budget while the app spends another's.
    state = state_module.load(state_module.state_path(settings_file))
    left = state.skip_remaining(config.custom_skip_daily_budget) / 60
    print(f"\n  custom skip left today: {left:.0f} min")
    return 0


def ceil_minutes(seconds: float) -> int:
    """Whole minutes remaining, never rounding a part-minute away.

    The single rounding for every countdown the app shows. It exists
    because there were briefly two: this line's tooltip floored while the
    taskbar pill rounded up, so at 1:59 left the pill read "2 min" and its
    own tooltip read "1 min" — and they are read side by side, the tooltip
    being what you get by hovering the pill. Disagreeing about the same
    number in the same instant discredits both.

    Rounding up rather than down because a countdown that reaches "0 min"
    and then keeps going for another 59 seconds reads as stuck.
    """
    return max(1, math.ceil(max(0.0, seconds) / 60))


def _short(seconds: float) -> str:
    """A duration a glance can read: "12 min", "45 sec", "1h 20m".

    Under a minute it switches to seconds, which is more precise than the
    pill rather than in conflict with it: the pill says "1 min" for that
    last stretch because it deals only in minutes.
    """
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)} sec"
    minutes = ceil_minutes(seconds)
    if minutes >= 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours}h {rest:02d}m"
    return f"{minutes} min"


def _or_none(names: list[str]) -> str:
    return ", ".join(names) if names else "nobody"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.config or settings_module.default_path()

    # One-shot commands run before anything else: they are read-only, so
    # they must not be refused by the single-instance guard while the app
    # is running, and their output belongs on the terminal rather than in
    # the log file. Both of those were briefly wrong.
    if args.history is not None or args.events is not None:
        log = history_module.History(history_module.history_path(path))
        if not log.enabled:
            print(f"no history at {log.path}")
            return 1
        if args.history is not None:
            print(f"Last {args.history} days — {log.path}\n")
            for day in log.recent_days(args.history):
                print(" ", day.describe())
        if args.events is not None:
            print(f"\nLast {args.events} events\n")
            for at, kind, seconds, detail in log.tail(args.events):
                stamp = at[:19].replace("T", " ")
                amount = f"{seconds:.0f}s" if seconds is not None else ""
                print(f"  {stamp}  {kind:16} {amount:>8}  {detail or ''}")
        return 0

    if args.doctor:
        return report_doctor(path)

    if args.test_sounds:
        settings = settings_module.load(path)
        clips = sounds.available()
        print(f"{len(clips)} clip(s) in {sounds.sounds_dir()}\n")
        for clip in clips:
            print(f"  {sounds.label(clip):32} {clip.name}")
        print()
        for what, configured in (
            ("break start", settings.break_start_sound),
            ("break end", settings.break_end_sound),
        ):
            found = sounds.resolve(configured, fallback_first=True)
            if found is None:
                print(f"  {what:12}: none")
                continue
            print(f"  {what:12}: {found.name}  playing…")
            sounds.play(found)
            time.sleep(3.5)
        return 0

    if args.shortcuts:
        for name, path in (
            ("Start Menu", runtime.start_menu_shortcut()),
            ("Desktop", runtime.desktop_shortcut()),
        ):
            ok = runtime.create_shortcut(path)
            print(f"{name:11}: {'created' if ok else 'FAILED'}  {path}")
        return 0

    if args.exclusions:
        loaded = settings_module.load(path)
        return report_exclusions(loaded.config, loaded, path)

    # Only now, for the run that actually starts the app: without a
    # console the log has nowhere to go, which is true of a packaged build
    # and of the Startup shortcut — exactly when nobody is watching a
    # terminal anyway.
    if runtime.frozen() or not runtime.has_console():
        runtime.redirect_output()

    # Two copies would each install a global input hook and fight over the
    # lock overlay — the worst thing to get wrong in this app.
    instance = runtime.SingleInstance()
    if not instance.acquire():
        print("another copy is already running")
        return 0

    # Without a Start Menu entry the Startup shortcut is the only launcher,
    # so turning "Start with Windows" off would leave no way back in.
    runtime.ensure_start_menu_shortcut()

    # First run, or an explicit --setup: show the window before anything
    # else starts, so the settings the app runs on are the ones just saved.
    first_run = not settings_module.exists(path)
    settings = settings_module.load(path)
    if args.setup or first_run:
        from .setup_dialog import run_setup

        saved = run_setup(settings, path, first_run=first_run)
        if saved is not None:
            settings = saved
            print(f"settings saved to {path}")
        elif first_run:
            print("setup skipped — running with defaults")
        if args.setup:
            return 0

    config = settings.config
    if args.demo:
        config = config.scaled(args.demo)
    if args.no_safety_unlock or args.remind_only:
        from dataclasses import replace

        if args.no_safety_unlock:
            config = replace(config, safety_unlock=False)
        if args.remind_only:
            config = replace(config, block_input=False)

    watcher = CalendarWatcher(
        None if args.no_exclusions else settings.calendar_url,
        day_off_hours=settings.day_off_block_hours,
        meeting_lead=settings.meeting_lead_minutes * 60,
    )
    app = Application(
        config=config,
        dry_run=args.dry_run,
        detector=NullDetector() if args.no_exclusions else None,
        watcher=watcher,
        state_file=state_module.state_path(path),
        settings=settings,
        settings_file=path,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
        print("\nstopped")
    finally:
        instance.release()
    return 0
