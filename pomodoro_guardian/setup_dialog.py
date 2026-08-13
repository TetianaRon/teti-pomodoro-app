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

from . import calendar_feed, settings as settings_module, sound_pack, sounds
from .config import MINUTE
from .settings import Settings
from .walking import parse_times

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
        # Populated by _build_sounds; empty when no clips are installed, so
        # saving still works and simply keeps the setting blank.
        self._clip_by_label: dict[str, str] = {}
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
            "block_input": tk.BooleanVar(value=c.block_input),
            "show_countdown": tk.BooleanVar(value=c.show_countdown),
            "skip_budget": tk.StringVar(
                value=_mins(c.custom_skip_daily_budget)),
            "overtime": tk.StringVar(
                value=_mins(c.overtime_work_duration)),
            "focus_hours": tk.StringVar(value=_num(settings.focus_max_hours)),
            "focus_uses": tk.StringVar(value=str(settings.focus_uses_per_day)),
            "walk_times": tk.StringVar(
                value=", ".join(settings.walking_reminder_times)),
            "sound_start": tk.StringVar(
                value=_clip_label(settings.break_start_sound)),
            "sound_end": tk.StringVar(
                value=_clip_label(settings.break_end_sound)),
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
        # Two columns below a full-width calendar row. In one column this
        # reached 994px, which fits the 1080p monitor but runs off the
        # bottom of the 1280x800 laptop panel — taking the Save button
        # with it.
        self._build_calendar(body, 0, columnspan=2)
        left = ttk.Frame(body)
        left.grid(row=1, column=0, sticky="nw", padx=(0, 14))
        right = ttk.Frame(body)
        right.grid(row=1, column=1, sticky="nw")

        self._build_rhythm(left, 0)
        self._build_breaks(left, 1)
        self._build_sounds(left, 2)
        self._build_caps(right, 0)
        self._build_focus(right, 1)
        self._build_walking(right, 2)

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

    def _build_calendar(self, parent, row, columnspan=1):
        frame = _section(parent, row, "Work calendar", columnspan)
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
        ttk.Checkbutton(
            frame, text="Show the countdown in the bottom-right corner",
            variable=self._vars["show_countdown"],
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(
            frame,
            text=("A small pill saying how long until the next break. The "
                  "warning before a break always appears\nin the same place, "
                  "whether this is on or off."),
            foreground="#555", justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _build_breaks(self, parent, row):
        frame = _section(parent, row, "Skipping a break")
        ttk.Checkbutton(
            frame, text="Block the keyboard and mouse during a break",
            variable=self._vars["block_input"],
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            frame,
            text=("Off: breaks still cover every screen with a countdown, but "
                  "you can click away and keep working.\nUseful while you are "
                  "learning to trust it, or on a day of presentations. Breaks "
                  "worked through are recorded as such either way."),
            foreground="#555", justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))
        ttk.Checkbutton(
            frame, text="Hold Escape during a break to open the skip menu",
            variable=self._vars["safety_unlock"],
        ).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(
            frame,
            text=("Hold it for 3 seconds and choose 5, 10 or 20 minutes; the "
                  "time comes off the daily budget below.\nTurn this off and "
                  "a break cannot be skipped at all. Ctrl+Alt+Del always "
                  "works either way."),
            foreground="#555", justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 8))
        _field(frame, 4, "Skip budget", self._vars["skip_budget"],
               "minutes/day", "shared across every skip")

    def _build_caps(self, parent, row):
        frame = _section(parent, row, "Daily limits")
        _field(frame, 0, "Working day cap", self._vars["working_cap"], "hours")
        _field(frame, 1, "Day off cap", self._vars["non_working_cap"], "hours",
               "weekends, holidays and vacations")
        _field(frame, 2, "Emergency Mode budget", self._vars["emergency"],
               "hours/week", "+1h each, when you are past the cap")
        _field(frame, 3, "Day-type overrides", self._vars["raises"],
               "per month", "raising a day-off cap back to a full day")
        _field(frame, 4, "Breaks past the cap", self._vars["overtime"],
               "minutes", "work interval shortens instead of stopping")

    def _build_sounds(self, parent, row):
        self._sounds_parent = parent
        self._sounds_row = row
        frame = _section(parent, row, "Chimes")
        self._sounds_frame = frame
        clips = sounds.available()
        next_row = 0
        if not clips:
            ttk.Label(
                frame,
                text=("No clips yet. Download the starter pack below, or "
                      f"drop your own .mp3/.wav files into\n"
                      f"assets/{sounds.SOUNDS_DIRNAME}/ and reopen this "
                      "window."),
                foreground="#555", justify="left",
            ).grid(row=next_row, column=0, columnspan=3, sticky="w")
            next_row += 1
        else:
            # Labels are shown, filenames are stored — the numeric id in a
            # stock filename is meaningless in a menu but ties it to its
            # source.
            self._clip_by_label = {sounds.NONE_LABEL: ""}
            for clip in clips:
                self._clip_by_label[sounds.label(clip)] = clip.name
            choices = list(self._clip_by_label)

            for text, key in (
                ("Break starts", "sound_start"), ("Break ends", "sound_end")
            ):
                ttk.Label(frame, text=text).grid(
                    row=next_row, column=0, sticky="w", pady=3)
                ttk.Combobox(
                    frame, textvariable=self._vars[key], values=choices,
                    state="readonly", width=26,
                ).grid(row=next_row, column=1, sticky="w", padx=8)
                ttk.Button(
                    frame, text="Play", width=6,
                    command=lambda k=key: self._play_choice(k),
                ).grid(row=next_row, column=2, sticky="w")
                next_row += 1

        pending = sound_pack.missing()
        if pending:
            label = (
                f"Download starter pack ({len(pending)} clip"
                f"{'s' if len(pending) != 1 else ''}, from pixabay.com)"
            )
            self._pack_button = ttk.Button(
                frame, text=label, command=self._download_pack,
            )
            self._pack_button.grid(
                row=next_row, column=0, columnspan=3, sticky="w",
                pady=(8 if clips else 4, 0))
            next_row += 1
        else:
            self._pack_button = None
        self._pack_status = ttk.Label(frame, text="", foreground="#555",
                                      justify="left")
        self._pack_status.grid(row=next_row, column=0, columnspan=3,
                               sticky="w", pady=(4, 0))

    def _play_choice(self, key: str) -> None:
        """Preview the selected clip, so levels can be judged before a break."""
        name = self._clip_by_label.get(self._vars[key].get(), "")
        if name:
            sounds.play(sounds.sounds_dir() / name)

    def _download_pack(self) -> None:
        """Fetch the curated clips straight from pixabay.com.

        Never shipped in the repo — see ATTRIBUTION.md — so this is the
        only route to them beyond grabbing the links by hand.
        """
        if self._pack_button is not None:
            self._pack_button.configure(state="disabled")
        self._pack_status.configure(
            text="Downloading from pixabay.com…", foreground="#555")

        def work() -> None:
            # Off the UI thread: a slow or hanging fetch must not freeze
            # the window.
            results = sound_pack.download_all()
            self._root.after(0, lambda: self._finish_pack_download(results))

        threading.Thread(target=work, daemon=True).start()

    def _finish_pack_download(
        self, results: list[tuple[sound_pack.Clip, bool, str]]
    ) -> None:
        ok = sum(1 for _clip, success, _msg in results if success)
        failed = [(clip, msg) for clip, success, msg in results if not success]
        if failed:
            detail = "; ".join(f"{clip.filename}: {msg}" for clip, msg in failed)
            text, colour = f"{ok} of {len(results)} downloaded — {detail}", "#b3261e"
        elif ok:
            text = f"Downloaded {ok} clip{'s' if ok != 1 else ''}."
            colour = "#1b6b2f"
        else:
            text, colour = "Already had every clip in the starter pack.", "#555"
        self._sounds_frame.destroy()
        self._build_sounds(self._sounds_parent, self._sounds_row)
        self._pack_status.configure(text=text, foreground=colour)

    def _build_focus(self, parent, row):
        frame = _section(parent, row, "Focus Mode")
        ttk.Label(
            frame,
            text=("Suppresses breaks entirely for deep work. Started from "
                  "the tray; the time still counts\ntowards your daily cap."),
            foreground="#555", justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        _field(frame, 1, "Longest session", self._vars["focus_hours"], "hours")
        _field(frame, 2, "Uses", self._vars["focus_uses"], "per day")

    def _build_walking(self, parent, row):
        frame = _section(parent, row, "Walking")
        _field(frame, 0, "Daily target", self._vars["walking"], "minutes",
               "missing it lowers the work cap, minute for minute")
        ttk.Label(frame, text="Remind me at").grid(
            row=1, column=0, sticky="w", pady=2)
        ttk.Entry(
            frame, textvariable=self._vars["walk_times"], width=22,
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(
            frame, text="24-hour times, comma separated — blank for no reminders",
            foreground="#555",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

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
            skip_budget = _positive(
                self._vars["skip_budget"], "Skip budget", allow_zero=True)
            overtime = _positive(self._vars["overtime"], "Breaks past the cap")
            focus_hours = _positive(
                self._vars["focus_hours"], "Longest focus session")
            focus_uses = int(_positive(
                self._vars["focus_uses"], "Focus uses", allow_zero=True))
        except ValueError as exc:
            self._error.configure(text=str(exc))
            return

        if lead >= work:
            self._error.configure(
                text="The warning must be shorter than the work interval — "
                     "otherwise it would already be showing when work starts.")
            return

        raw_times = self._vars["walk_times"].get().strip()
        walk_times = parse_times(
            [part.strip() for part in raw_times.split(",") if part.strip()]
        )
        if raw_times and not walk_times:
            self._error.configure(
                text="Reminder times need to look like 11:40, 15:20 "
                     "(24-hour clock).")
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
            block_input=bool(self._vars["block_input"].get()),
            show_countdown=bool(self._vars["show_countdown"].get()),
            custom_skip_daily_budget=skip_budget * MINUTE,
            overtime_work_duration=overtime * MINUTE,
        )
        self._result = replace(
            self._start,
            config=config,
            calendar_url=url,
            meeting_lead_minutes=meeting_lead,
            focus_max_hours=focus_hours,
            focus_uses_per_day=focus_uses,
            walking_reminder_times=tuple(walk_times),
            break_start_sound=self._clip_by_label.get(
                self._vars["sound_start"].get(), ""),
            break_end_sound=self._clip_by_label.get(
                self._vars["sound_end"].get(), ""),
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
        self._bring_to_front()

    def _bring_to_front(self) -> None:
        """Force this window above whatever currently has focus.

        The app's own root is withdrawn — there is no visible main window,
        just a tray icon — so Windows' focus-stealing prevention treats a
        freshly opened Toplevel as a background window and leaves it behind
        whatever the user was looking at. A brief topmost flip is the usual
        workaround: it doesn't need the foreground-switch permission that
        `focus_force` alone can be denied, and is released right after so
        the window doesn't stay pinned above everything else forever.
        """
        self._root.deiconify()
        self._root.lift()
        self._root.attributes("-topmost", True)
        self._root.after(150, lambda: self._root.attributes("-topmost", False))
        self._root.focus_force()

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


def _section(parent, row, title, columnspan=1):
    frame = ttk.LabelFrame(parent, text=title, padding=PAD)
    frame.grid(
        row=row, column=0, columnspan=columnspan, sticky="ew", pady=(0, PAD)
    )
    parent.columnconfigure(0, weight=1)
    return frame


def _field(parent, row, label, var, suffix, hint=""):
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
    ttk.Entry(parent, textvariable=var, width=7).grid(
        row=row, column=1, sticky="w", padx=8)
    text = suffix + (f"   — {hint}" if hint else "")
    ttk.Label(parent, text=text, foreground="#555").grid(
        row=row, column=2, sticky="w")


def _clip_label(configured: str) -> str:
    """The dropdown label for a stored filename, resolving the default."""
    found = sounds.resolve(configured, fallback_first=not configured)
    return sounds.label(found) if found else sounds.NONE_LABEL


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
