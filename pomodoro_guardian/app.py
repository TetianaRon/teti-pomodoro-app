"""Wires the three Phase 1 pieces together and runs the loop.

tkinter insists on owning the main thread, so the engine is ticked from
`root.after()` rather than a loop of our own. The activity monitor is the
only threaded part, and it only ever writes a timestamp.
"""

from __future__ import annotations

import argparse
import time
import tkinter as tk
from pathlib import Path

from datetime import date

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
    ) -> None:
        self.config = config
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
        self._state = state_module.load(state_file)
        self.engine = PomodoroEngine(config, now=time.monotonic())
        self._excluded_since: float | None = None
        self._warned_long_exclusion = False
        self._exclusion = Exclusion()

        self._root = tk.Tk()
        self._root.withdraw()  # the controller window is never shown
        self._root.title("Pomodoro Guardian")
        self.overlay = LockOverlay(
            self._root, config,
            skip_offer=self._skip_offer,
            on_skip=self._take_skip,
        )
        self.banner = WarningBanner(self._root, config)

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
        return SkipOffer(options, self._state.skip_remaining(budget))

    def _take_skip(self, seconds: float) -> None:
        """Spend part of the daily budget and push the break back."""
        self._state = self._state.with_skip(seconds)
        try:
            state_module.save(self._state, self._state_file)
        except OSError as exc:
            # Losing the tally is bad but not worth refusing the skip over.
            self._log(f"warning: could not save skip budget ({exc})")
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
        self.monitor.stop()
        if self.watcher is not None:
            self.watcher.stop()

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
        notice = self._watch_long_exclusion(now, exclusion.active)

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
                f"break in {self.config.warning_lead / 60:.0f} min"
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
    )
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
        print("\nstopped")
    return 0
