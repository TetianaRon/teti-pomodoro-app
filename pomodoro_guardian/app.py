"""Wires the three Phase 1 pieces together and runs the loop.

tkinter insists on owning the main thread, so the engine is ticked from
`root.after()` rather than a loop of our own. The activity monitor is the
only threaded part, and it only ever writes a timestamp.
"""

from __future__ import annotations

import argparse
import queue
import time
import tkinter as tk
from pathlib import Path

from datetime import date, datetime

from . import caps
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
from . import runtime, tray, walking
from .overlay import LockOverlay, SkipOffer, SkipOption, WarningBanner
from .timer import Event, PomodoroEngine, State

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
                presenting=config.exclude_on_presenting,
            )
        )
        # The calendar meeting skip (SPEC §4A) is just another exclusion —
        # §3 and §4A both mean "do not lock right now".
        self.detector: Detector = (
            CombinedDetector(device_detector, MeetingDetector(watcher))
            if watcher is not None and watcher.configured
            else device_detector
        )
        self._state_file = state_file
        self._settings_file = settings_module.default_path()
        self._state = state_module.load(state_file)
        self.engine = PomodoroEngine(config, now=time.monotonic())
        self._excluded_since: float | None = None
        self._warned_long_exclusion = False
        self._exclusion = Exclusion()
        self._last_worked_total = 0.0
        self._last_state_save = 0.0
        self._cap: caps.CapStatus | None = None
        self._announced_over = False

        self._root = tk.Tk()
        self._root.withdraw()  # the controller window is never shown
        self._root.title("Pomodoro Guardian")
        self.overlay = LockOverlay(
            self._root, config,
            skip_offer=self._skip_offer,
            on_skip=self._take_skip,
            on_emergency=self._take_emergency,
        )
        self.banner = WarningBanner(self._root, config)
        self.tray_status = tray.TrayStatus()
        self.tray = tray.TrayIcon(self.tray_status)
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

    # -- walking (SPEC §7) --------------------------------------------

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

    def _stop_focus(self, expired: bool = False) -> None:
        if not self._state.focusing:
            return
        self._state = self._state.stop_focus()
        self._save_state()
        self._log(
            "focus mode ended — its two hours are up" if expired
            else "focus mode ended"
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

    def _refresh_tray(self) -> None:
        status = self.tray_status
        snapshot = self.engine.snapshot()
        walked = self._state.walked_including_current() / 60
        target = self.settings.walking_target_minutes

        status.summary = {
            State.IDLE: "watching for activity",
            State.WORK: "working",
            State.WARNING: "break soon",
            State.BREAK: "on a break",
        }[snapshot.state]
        if snapshot.excluded:
            status.summary = "holding off"
        elif self._state.focusing:
            status.summary = "focus mode"
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
        status.colour = (
            tray.WALKING if self._state.walking
            else tray.BREAK if snapshot.state is State.BREAK
            else tray.WORKING if snapshot.state in (State.WORK, State.WARNING)
            else tray.IDLE
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
        if self._cap.over and not self._announced_over:
            self._announced_over = True
            self._log(
                f"over the daily cap — {self._cap.describe()}; breaks now every "
                f"{self.config.overtime_work_duration / 60:.0f} min"
            )
        elif not self._cap.over:
            self._announced_over = False

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
        self.walk_prompt.hide()
        self.tray.stop()
        self.monitor.stop()
        if self.watcher is not None:
            self.watcher.stop()
        self._save_state()   # don't lose the tally on a clean exit

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
        self._record_work(now)
        self._update_focus()
        self._update_walking()
        self._update_cap()
        self._refresh_tray()
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
        elif event is Event.EXCLUSION_STARTED:
            self._log(f"holding off — {self._exclusion.describe()}")
        elif event is Event.EXCLUSION_ENDED:
            self._log("clear again — countdown resumes")
        elif event is Event.WARNING_STARTED:
            self._log(
                f"break in {self.engine.warning_lead() / 60:.0f} min"
            )
            if not self.dry_run:
                self.banner.show()
        elif event is Event.BREAK_STARTED:
            kind = "long break" if snapshot.is_long_break else "break"
            self._log(f"{kind} started — locking")
            self.banner.hide()
            if not self.dry_run:
                # remaining == the full break length at the moment it starts,
                # which the overlay needs to show a "back at HH:MM" time.
                self.overlay.lock(snapshot.is_long_break, snapshot.remaining)
        elif event is Event.BREAK_ENDED:
            self._log(
                f"break over (cycle {snapshot.completed_cycles}) — unlocked"
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
        "--setup",
        action="store_true",
        help="open the settings window, then exit",
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
        "--config",
        type=Path,
        metavar="PATH",
        help="use a different settings file (default: %%APPDATA%%\\"
             "PomodoroGuardian\\config.json)",
    )
    return parser


def report_exclusions(config: Config, settings) -> int:
    """`--exclusions`: say what is holding breaks off right now, and why."""
    from .exclusions import devices_in_use, presenting_now

    detector = create_detector(
        camera=config.exclude_on_camera,
        microphone=config.exclude_on_microphone,
        presenting=config.exclude_on_presenting,
    )
    print("Never-interrupt exclusions (SPEC §3, §4A)\n")
    print(f"  camera in use by  : {_or_none(devices_in_use('webcam'))}")
    print(f"  microphone in use : {_or_none(devices_in_use('microphone'))}")
    print(f"  presenting        : {presenting_now()}")

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

    state = state_module.load(state_module.state_path())
    left = state.skip_remaining(config.custom_skip_daily_budget) / 60
    print(f"\n  custom skip left today: {left:.0f} min")
    return 0


def _or_none(names: list[str]) -> str:
    return ", ".join(names) if names else "nobody"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.config or settings_module.default_path()

    # A packaged build has no console, so the log has to go somewhere it
    # can be read after the fact.
    if runtime.frozen():
        runtime.redirect_output()

    # Two copies would each install a global input hook and fight over the
    # lock overlay — the worst thing to get wrong in this app.
    instance = runtime.SingleInstance()
    if not instance.acquire():
        print("another copy is already running")
        return 0

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
    if args.exclusions:
        return report_exclusions(config, settings)
    if args.demo:
        config = config.scaled(args.demo)
    if args.no_safety_unlock:
        from dataclasses import replace

        config = replace(config, safety_unlock=False)

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
    )
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
        print("\nstopped")
    finally:
        instance.release()
    return 0
