"""The first-run setup window.

A plain ttk form rather than console prompts, because a packaged .exe has
no console — and because this becomes the tray's Settings dialog in Phase
7 rather than being thrown away.

Only the values worth changing from real use are exposed. Detection
thresholds (input gap, idle timeouts) stay hand-editable in the JSON
file: they are tuning knobs, not decisions.
"""

from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import ttk

from . import calendar_feed, settings as settings_module
from .config import MINUTE
from .settings import Settings

PAD = 10


class SetupDialog:
    def __init__(
        self,
        settings: Settings,
        path: Path,
        first_run: bool,
        parent: tk.Misc | None = None,
    ) -> None:
        self._path = path
        self._start = settings
        self._result: Settings | None = None
        # A second tk.Tk() in one process misbehaves, so when the app is
        # already running this has to be a child window instead.
        self._owns_root = parent is None
        self._root = tk.Tk() if parent is None else tk.Toplevel(parent)
        self._root.title(
            "Pomodoro Guardian — Setup" if first_run
            else "Pomodoro Guardian — Settings"
        )
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self._cancel)

        outer = ttk.Frame(self._root, padding=PAD * 2)
        outer.grid(sticky="nsew")

        if first_run:
            ttk.Label(
                outer,
                text="Welcome — a few things before the first break.",
                font=("Segoe UI", 13, "bold"),
            ).grid(row=0, column=0, sticky="w", pady=(0, 2))
            ttk.Label(
                outer,
                text=("Everything here can be changed later. Skip Setup and "
                      "the defaults apply."),
                foreground="#555",
            ).grid(row=1, column=0, sticky="w", pady=(0, PAD))

        c = settings.config
        self._vars = {
            "calendar_url": tk.StringVar(value=settings.calendar_url or ""),
            "meeting_lead": tk.StringVar(
                value=_num(settings.meeting_lead_minutes)),
            "work": tk.StringVar(value=_mins(c.work_duration)),
            "short_break": tk.StringVar(value=_mins(c.short_break_duration)),
            "long_break": tk.StringVar(value=_mins(c.long_break_duration)),
            "long_break_every": tk.StringVar(value=str(c.long_break_every)),
            "warning_lead": tk.StringVar(value=_mins(c.warning_lead)),
            "safety_unlock": tk.BooleanVar(value=c.safety_unlock),
            "working_cap": tk.StringVar(
                value=_num(settings.working_day_cap_hours)),
            "non_working_cap": tk.StringVar(
                value=_num(settings.non_working_day_cap_hours)),
            "emergency": tk.StringVar(
                value=_num(settings.emergency_hours_per_week)),
            "raises": tk.StringVar(
                value=str(settings.override_raises_per_month)),
            "walking": tk.StringVar(
                value=_num(settings.walking_target_minutes)),
        }

        body = ttk.Frame(outer)
        body.grid(row=2, column=0, sticky="ew")
        self._build_calendar(body, 0)
        self._build_rhythm(body, 1)
        self._build_lock(body, 2)
        self._build_caps(body, 3)

        self._error = ttk.Label(outer, text="", foreground="#b3261e")
        self._error.grid(row=3, column=0, sticky="w", pady=(PAD, 0))

        buttons = ttk.Frame(outer)
        buttons.grid(row=4, column=0, sticky="e", pady=(PAD, 0))
        ttk.Button(
            buttons, text="Skip" if first_run else "Cancel",
            command=self._cancel,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            buttons, text="Save", command=self._save,
        ).grid(row=0, column=1)

        self._centre()

    # -- sections -----------------------------------------------------

    def _build_calendar(self, parent, row):
        frame = _section(parent, row, "Work calendar")
        ttk.Label(
            frame,
            text=("Secret iCal address — Google Calendar > Settings > "
                  "Integrate calendar.\nUsed to spot holidays, vacations and "
                  "meetings. Optional; the app runs without it."),
            foreground="#555", justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        entry = ttk.Entry(frame, textvariable=self._vars["calendar_url"],
                          width=62)
        entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self._test_button = ttk.Button(frame, text="Test", command=self._test)
        self._test_button.grid(row=1, column=1)

        self._feed_status = ttk.Label(frame, text="", foreground="#555",
                                      justify="left")
        self._feed_status.grid(row=2, column=0, columnspan=2, sticky="w",
                               pady=(6, 0))

        lead = ttk.Frame(frame)
        lead.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        _field(lead, 0, "Hold breaks before a meeting",
               self._vars["meeting_lead"], "minutes", "time to prepare")
        frame.columnconfigure(0, weight=1)

    def _build_rhythm(self, parent, row):
        frame = _section(parent, row, "Rhythm")
        _field(frame, 0, "Work", self._vars["work"], "minutes")
        _field(frame, 1, "Short break", self._vars["short_break"], "minutes")
        _field(frame, 2, "Long break", self._vars["long_break"], "minutes")
        _field(frame, 3, "Long break every", self._vars["long_break_every"],
               "cycles")
        _field(frame, 4, "Warning before lock", self._vars["warning_lead"],
               "minutes")

    def _build_lock(self, parent, row):
        frame = _section(parent, row, "Lock")
        ttk.Checkbutton(
            frame, text="Allow holding Escape to release a break early",
            variable=self._vars["safety_unlock"],
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            frame,
            text=("A safety hatch while the lock is young. Turn it off once "
                  "you trust it.\nCtrl+Alt+Del always works either way."),
            foreground="#555", justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _build_caps(self, parent, row):
        frame = _section(parent, row, "Daily limits")
        _field(frame, 0, "Working day cap", self._vars["working_cap"], "hours")
        _field(frame, 1, "Day off cap", self._vars["non_working_cap"], "hours",
               "weekends, holidays and vacations")
        _field(frame, 2, "Emergency Mode budget", self._vars["emergency"],
               "hours/week")
        _field(frame, 3, "Day-type overrides", self._vars["raises"],
               "per month", "raising a day-off cap back to a full day")
        _field(frame, 4, "Walking target", self._vars["walking"],
               "minutes/day", "missing it lowers the cap, minute for minute")

    # -- actions ------------------------------------------------------

    def _test(self) -> None:
        url = self._vars["calendar_url"].get().strip()
        if not url:
            self._feed_status.configure(
                text="Nothing to test — paste the address first.",
                foreground="#555")
            return
        self._test_button.configure(state="disabled")
        self._feed_status.configure(text="Fetching…", foreground="#555")

        def work() -> None:
            # Off the UI thread: a slow or hanging fetch must not freeze
            # the window.
            try:
                summary = calendar_feed.check(url)
                message, colour = summary.describe(), "#1b6b2f"
                if not summary.event_count:
                    colour = "#8a6d00"
            except calendar_feed.FeedError as exc:
                message, colour = str(exc), "#b3261e"
            self._root.after(0, lambda: self._show_feed(message, colour))

        threading.Thread(target=work, daemon=True).start()

    def _show_feed(self, message: str, colour: str) -> None:
        self._feed_status.configure(text=message, foreground=colour)
        self._test_button.configure(state="normal")

    def _save(self) -> None:
        try:
            work = _positive(self._vars["work"], "Work")
            short_break = _positive(self._vars["short_break"], "Short break")
            long_break = _positive(self._vars["long_break"], "Long break")
            every = int(_positive(
                self._vars["long_break_every"], "Long break every"))
            lead = _positive(self._vars["warning_lead"], "Warning")
            working_cap = _positive(self._vars["working_cap"], "Working day cap")
            day_off_cap = _positive(self._vars["non_working_cap"], "Day off cap")
            emergency = _positive(
                self._vars["emergency"], "Emergency Mode budget", allow_zero=True)
            raises = int(_positive(
                self._vars["raises"], "Day-type overrides", allow_zero=True))
            walking = _positive(
                self._vars["walking"], "Walking target", allow_zero=True)
            meeting_lead = _positive(
                self._vars["meeting_lead"], "Meeting lead", allow_zero=True)
        except ValueError as exc:
            self._error.configure(text=str(exc))
            return

        if lead >= work:
            self._error.configure(
                text="The warning must be shorter than the work interval — "
                     "otherwise it would already be showing when work starts.")
            return

        url = self._vars["calendar_url"].get().strip() or None
        config = replace(
            self._start.config,
            work_duration=work * MINUTE,
            short_break_duration=short_break * MINUTE,
            long_break_duration=long_break * MINUTE,
            long_break_every=every,
            warning_lead=lead * MINUTE,
            safety_unlock=bool(self._vars["safety_unlock"].get()),
        )
        self._result = replace(
            self._start,
            config=config,
            calendar_url=url,
            meeting_lead_minutes=meeting_lead,
            working_day_cap_hours=working_cap,
            non_working_day_cap_hours=day_off_cap,
            emergency_hours_per_week=emergency,
            override_raises_per_month=raises,
            walking_target_minutes=walking,
        )
        settings_module.save(self._result, self._path)
        self._root.destroy()

    def _cancel(self) -> None:
        self._result = None
        self._root.destroy()

    def _centre(self) -> None:
        self._root.update_idletasks()
        width = self._root.winfo_width()
        height = self._root.winfo_height()
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        self._root.geometry(
            f"+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 3)}"
        )

    def run(self) -> Settings | None:
        if self._owns_root:
            self._root.mainloop()
        else:
            # Modal against the running app: wait for this window alone
            # rather than starting a second event loop.
            self._root.grab_set()
            self._root.wait_window()
        return self._result


# -- helpers ----------------------------------------------------------


def _section(parent, row, title):
    frame = ttk.LabelFrame(parent, text=title, padding=PAD)
    frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
    parent.columnconfigure(0, weight=1)
    return frame


def _field(parent, row, label, var, suffix, hint=""):
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
    ttk.Entry(parent, textvariable=var, width=7).grid(
        row=row, column=1, sticky="w", padx=8)
    text = suffix + (f"   — {hint}" if hint else "")
    ttk.Label(parent, text=text, foreground="#555").grid(
        row=row, column=2, sticky="w")


def _mins(seconds: float) -> str:
    return _num(seconds / MINUTE)


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _positive(var, label, allow_zero=False):
    raw = var.get().strip()
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{label}: “{raw}” isn't a number.") from None
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{label} must be greater than zero.")
    return value


def run_setup(
    settings: Settings,
    path: Path,
    first_run: bool,
    parent: tk.Misc | None = None,
) -> Settings | None:
    """Show the dialog. Returns the saved settings, or None if dismissed."""
    return SetupDialog(settings, path, first_run, parent).run()
