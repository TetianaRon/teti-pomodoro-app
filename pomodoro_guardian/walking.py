"""The walking / standing-desk prompt (SPEC §7).

Tracking is a manual toggle by design — camera detection was ruled out on
hardware grounds, not effort — so the app's job is to *ask* at sensible
times and make starting and stopping a single click.

The window is small and always-on-top but never modal and never
input-blocking: unlike a break, this is a nudge, not enforcement. It can
be dismissed, and it will not ask again about the same reminder time.

There is no verification that a walk really happened, and deliberately
none: the contributor's point is that a goal you can trivially fake is
still worth having if you don't intend to fake it. What the app *can* do
honestly is keep the tally and let §7's shortfall formula act on it.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, time as time_of_day


def parse_times(values) -> list[str]:
    """Normalise reminder times to sorted, valid "HH:MM" strings."""
    out = []
    for value in values or ():
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.strptime(value.strip(), "%H:%M").time()
        except ValueError:
            continue
        out.append(parsed.strftime("%H:%M"))
    return sorted(set(out))


def due_prompt(
    now: time_of_day, times: list[str], already_shown, window_minutes: int = 5
) -> str | None:
    """The reminder that should fire now, if any.

    A window rather than an exact match, because the app ticks once a
    second but can miss a minute entirely if the machine sleeps. Anything
    already shown today is skipped, so a reminder asks once.
    """
    minutes_now = now.hour * 60 + now.minute
    for label in times:
        if label in already_shown:
            continue
        hour, minute = (int(part) for part in label.split(":"))
        due = hour * 60 + minute
        if 0 <= minutes_now - due < window_minutes:
            return label
    return None


class WalkPrompt:
    """Small window offering a single start/stop button."""

    BG = "#12161c"
    FG = "#e8eef7"
    MUTED = "#7c8899"
    GO = "#2f7d4f"
    STOP = "#8a4b2a"

    def __init__(self, root: tk.Tk, on_start, on_stop, on_dismiss=None) -> None:
        self._root = root
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_dismiss = on_dismiss
        self._window: tk.Toplevel | None = None
        self._button: tk.Button | None = None
        self._status: tk.Label | None = None

    @property
    def visible(self) -> bool:
        return self._window is not None

    def show(self, headline: str) -> None:
        if self._window is not None:
            return
        window = tk.Toplevel(self._root)
        window.title("Time to walk")
        window.configure(bg=self.BG)
        window.attributes("-topmost", True)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._dismiss)

        # Padding tuples belong on pack(), not on the widget itself — a
        # widget's own pady takes a single value.
        tk.Label(
            window, text=headline, font=("Segoe UI", 15, "bold"),
            bg=self.BG, fg=self.FG, padx=26,
        ).pack(pady=(18, 4))

        self._status = tk.Label(
            window, text="", font=("Segoe UI", 11), bg=self.BG, fg=self.MUTED,
        )
        self._status.pack(pady=(0, 12))

        self._button = tk.Button(
            window, text="Start walking", font=("Segoe UI", 13, "bold"),
            bg=self.GO, fg=self.FG, activebackground=self.GO,
            activeforeground=self.FG, relief="flat", padx=26, pady=10,
            cursor="hand2", command=self._toggle,
        )
        self._button.pack(pady=(0, 12))

        tk.Label(
            window, text="Not now", font=("Segoe UI", 10), bg=self.BG,
            fg=self.MUTED, cursor="hand2",
        ).pack(pady=(0, 14))
        window.winfo_children()[-1].bind("<Button-1>", lambda _e: self._dismiss())

        self._window = window
        self._place(window)

    def update(self, walking: bool, walked_seconds: float,
               target_seconds: float) -> None:
        """Refresh the button and tally. Safe to call every tick."""
        if self._window is None or self._button is None:
            return
        walked = walked_seconds / 60
        target = target_seconds / 60
        if walking:
            self._button.configure(text="Stop walking", bg=self.STOP,
                                   activebackground=self.STOP)
            self._status.configure(text=f"walking — {walked:.0f} of {target:.0f} min")
        else:
            self._button.configure(text="Start walking", bg=self.GO,
                                   activebackground=self.GO)
            left = max(0.0, target - walked)
            self._status.configure(
                text=(f"{walked:.0f} of {target:.0f} min today"
                      + (f" — {left:.0f} min still owed" if left else " — done"))
            )
        self._window.attributes("-topmost", True)

    def hide(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._button = None
        self._status = None

    # -- internals ----------------------------------------------------

    def _toggle(self) -> None:
        # The app decides which way this goes and calls back into update();
        # this class deliberately holds no session state of its own.
        if self._button is not None and "Stop" in self._button.cget("text"):
            self._on_stop()
        else:
            self._on_start()

    def _dismiss(self) -> None:
        self.hide()
        if self._on_dismiss is not None:
            self._on_dismiss()

    def _place(self, window: tk.Toplevel) -> None:
        """Bottom-right of the primary monitor, clear of the break banner."""
        from .overlay import primary_rect

        window.update_idletasks()
        left, top, width, height = primary_rect(self._root)
        margin = 32
        x = left + width - window.winfo_reqwidth() - margin
        y = top + height - window.winfo_reqheight() - margin - 40
        window.geometry(f"+{x}+{y}")
