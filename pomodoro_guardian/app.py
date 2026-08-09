"""Wires the three Phase 1 pieces together and runs the loop.

tkinter insists on owning the main thread, so the engine is ticked from
`root.after()` rather than a loop of our own. The activity monitor is the
only threaded part, and it only ever writes a timestamp.
"""

from __future__ import annotations

import argparse
import time
import tkinter as tk

from .activity import ActivityMonitor, create_monitor
from .config import DEFAULT, Config
from .overlay import LockOverlay, WarningBanner
from .timer import Event, PomodoroEngine, State

TICK_MS = 1000


class Application:
    def __init__(
        self,
        config: Config = DEFAULT,
        monitor: ActivityMonitor | None = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.monitor = monitor if monitor is not None else create_monitor()
        self.engine = PomodoroEngine(config, now=time.monotonic())

        self._root = tk.Tk()
        self._root.withdraw()  # the controller window is never shown
        self._root.title("Pomodoro Guardian")
        self.overlay = LockOverlay(self._root, config)
        self.banner = WarningBanner(self._root)

    def run(self) -> None:
        self.monitor.start()
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

    # -- loop ---------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        events = self.engine.update(now, self.monitor.last_input_at)
        snapshot = self.engine.snapshot()

        for event in events:
            self._handle(event, snapshot)

        if snapshot.state is State.WARNING:
            self.banner.tick(snapshot.remaining)
        elif snapshot.state is State.BREAK and self.overlay.visible:
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = DEFAULT
    if args.demo:
        config = config.scaled(args.demo)
    if args.no_safety_unlock:
        from dataclasses import replace

        config = replace(config, safety_unlock=False)

    app = Application(config=config, dry_run=args.dry_run)
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
        print("\nstopped")
    return 0
