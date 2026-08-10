"""The tray-accessible summary view (SPEC §11.7).

The same data `--history` prints, without needing a terminal — which
matters because the terminal is exactly what the app was packaged to stop
requiring.

Deliberately not modal, unlike the settings dialog: this is something to
glance at, and possibly leave open while working, rather than a decision
to finish. It refreshes itself on a throttle so today's figures climb
while you watch, without running seven SQLite queries a second.

The recent-events list is here for the other half of the log's job —
chasing a bug — so it shows the app's own decisions alongside the user's
actions, and the file paths, since that is the first thing anyone asks
when something looks wrong.
"""

from __future__ import annotations

import time
import tkinter as tk
from datetime import date
from tkinter import ttk

REFRESH_SECONDS = 5.0
EVENT_ROWS = 14


class SummaryWindow:
    """A read-only view of the history log."""

    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        self._window: tk.Toplevel | None = None
        self._today: ttk.Label | None = None
        self._days: tk.Text | None = None
        self._events: tk.Text | None = None
        self._last_refresh = 0.0

    @property
    def visible(self) -> bool:
        return self._window is not None

    def show(self) -> None:
        """Open it, or bring an already-open one to the front."""
        if self._window is not None:
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
            return

        window = tk.Toplevel(self._root)
        window.title("Pomodoro Guardian — history")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.hide)

        outer = ttk.Frame(window, padding=16)
        outer.grid(sticky="nsew")

        ttk.Label(outer, text="Today", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._today = ttk.Label(outer, text="", justify="left")
        self._today.grid(row=1, column=0, sticky="w", pady=(2, 14))

        ttk.Label(
            outer, text="Last 7 days", font=("Segoe UI", 12, "bold")
        ).grid(row=2, column=0, sticky="w")
        self._days = self._text(outer, row=3, height=7)

        ttk.Label(
            outer, text="Recent events", font=("Segoe UI", 12, "bold")
        ).grid(row=4, column=0, sticky="w", pady=(14, 0))
        ttk.Label(
            outer,
            text=("The app's own decisions as well as yours — a day judged "
                  "wrongly shows up here."),
            foreground="#555",
        ).grid(row=5, column=0, sticky="w", pady=(0, 4))
        self._events = self._text(outer, row=6, height=EVENT_ROWS)

        self._paths = ttk.Label(outer, text="", foreground="#555",
                                justify="left")
        self._paths.grid(row=7, column=0, sticky="w", pady=(12, 0))

        ttk.Button(outer, text="Close", command=self.hide).grid(
            row=8, column=0, sticky="e", pady=(12, 0)
        )

        self._window = window
        self._last_refresh = 0.0
        self._centre(window)

    @staticmethod
    def _text(parent, row: int, height: int) -> tk.Text:
        widget = tk.Text(
            parent, height=height, width=74, font=("Consolas", 9),
            relief="flat", background="#f5f6f8", wrap="none",
            borderwidth=6, highlightthickness=0,
        )
        widget.grid(row=row, column=0, sticky="ew")
        widget.configure(state="disabled")
        return widget

    def hide(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._today = self._days = self._events = None

    def refresh(self, history, cap, state, settings, force: bool = False) -> None:
        """Redraw, throttled. Safe to call every tick."""
        if self._window is None:
            return
        now = time.monotonic()
        if not force and now - self._last_refresh < REFRESH_SECONDS:
            return
        self._last_refresh = now

        walked = state.walked_including_current() / 60
        target = settings.walking_target_minutes
        lines = [cap.describe() if cap else "no cap computed yet"]
        lines.append(f"walked {walked:.0f} of {target:.0f} min")
        if state.walking:
            lines.append("currently walking")
        if state.focusing:
            left = state.focus_remaining(settings.focus_max_hours) / 60
            lines.append(f"focus mode — {left:.0f} min left")
        self._today.configure(text="\n".join(lines))

        self._fill(
            self._days,
            [day.describe() for day in history.recent_days(7)],
            empty="nothing recorded yet",
        )
        self._fill(
            self._events,
            [
                f"{at[11:19]}  {kind:15} "
                f"{('%.0fs' % seconds) if seconds is not None else '':>7}  "
                f"{detail or ''}"
                for at, kind, seconds, detail in history.tail(EVENT_ROWS)
            ],
            empty="no events yet",
        )
        self._paths.configure(
            text=f"history: {history.path}\nlog:     {history.path.parent / 'pomodoro.log'}"
        )

    @staticmethod
    def _fill(widget: tk.Text, rows: list[str], empty: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", "\n".join(rows) if rows else empty)
        widget.configure(state="disabled")

    def _centre(self, window: tk.Toplevel) -> None:
        window.update_idletasks()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        x = (screen_w - window.winfo_width()) // 2
        y = max(0, (screen_h - window.winfo_height()) // 3)
        window.geometry(f"+{x}+{y}")


def today_summary(history) -> str:
    """One line for the tray tooltip and the menu."""
    return history.summary(date.today()).describe()
