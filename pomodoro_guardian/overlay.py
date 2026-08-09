"""The full-screen break lock (SPEC §2.3).

This is the part that makes the app more than a reminder: a borderless,
always-on-top window on every monitor, with keyboard and mouse input
swallowed underneath so alt-tabbing away isn't an escape hatch.

What it deliberately does *not* block: Ctrl+Alt+Del. Blocking the Secure
Attention Sequence needs a kernel driver, and shipping one to enforce your
own coffee breaks is the wrong trade. It stays as the last-resort exit.

Safety: while `Config.safety_unlock` is on, holding Escape releases the
lock. Phase 1 code has never run for 25 unattended minutes on a real
desktop, and a bug here means being locked out of your own machine. Turn
it off once you've watched it behave.
"""

from __future__ import annotations

import atexit
import threading
import time
import tkinter as tk

from .config import DEFAULT, Config


class InputSuppressor:
    """Swallows global keyboard and mouse input while the lock is up.

    Uses pynput's suppressing listeners. Registers an atexit hook because
    leaving suppression on after a crash would wedge the whole machine —
    the one failure mode here that isn't recoverable by closing the app.
    """

    def __init__(self, on_safety_hold: "callable | None" = None,
                 hold_seconds: float = 3.0) -> None:
        self._on_safety_hold = on_safety_hold
        self._hold_seconds = hold_seconds
        self._listeners: list = []
        self._escape_down_at: float | None = None
        self._lock = threading.Lock()
        self._fired = False
        atexit.register(self.stop)

    def start(self) -> None:
        if self._listeners:
            return
        try:
            from pynput import keyboard, mouse
        except ImportError:
            # No pynput: the overlay still covers the screen, it just won't
            # block input underneath. Better degraded than not locking.
            return

        self._escape_key = keyboard.Key.esc
        self._fired = False
        self._listeners = [
            keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
                suppress=True,
            ),
            mouse.Listener(
                on_move=self._swallow,
                on_click=self._swallow,
                on_scroll=self._swallow,
                suppress=True,
            ),
        ]
        for listener in self._listeners:
            listener.daemon = True
            listener.start()

    def stop(self) -> None:
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:  # pragma: no cover - teardown must not raise
                pass
        self._listeners = []
        self._escape_down_at = None

    @property
    def active(self) -> bool:
        return bool(self._listeners)

    # -- internals ----------------------------------------------------

    def _swallow(self, *_args) -> None:
        """Consume the event; suppress=True does the actual blocking."""

    def _on_press(self, key) -> None:
        if self._on_safety_hold is None or key != self._escape_key:
            return
        with self._lock:
            if self._escape_down_at is None:
                self._escape_down_at = time.monotonic()
                return
            held = time.monotonic() - self._escape_down_at
            if held >= self._hold_seconds and not self._fired:
                self._fired = True
                self._on_safety_hold()

    def _on_release(self, key) -> None:
        if key == self._escape_key:
            with self._lock:
                self._escape_down_at = None


def monitor_rects(root: tk.Tk) -> list[tuple[int, int, int, int]]:
    """Every monitor as (left, top, width, height).

    Per-monitor rectangles rather than one bounding box: a single spanning
    window centres its content on the *seam* between two side-by-side
    screens, and leaves gaps on L-shaped or mismatched-resolution layouts.
    """
    try:
        import win32api

        rects = []
        for _handle, _hdc, rect in win32api.EnumDisplayMonitors():
            left, top, right, bottom = rect
            rects.append((left, top, right - left, bottom - top))
        if rects:
            return rects
    except ImportError:
        pass
    return [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


def primary_rect(root: tk.Tk) -> tuple[int, int, int, int]:
    """The primary monitor, which Windows always anchors at the origin."""
    rects = monitor_rects(root)
    for rect in rects:
        if rect[0] == 0 and rect[1] == 0:
            return rect
    return rects[0]


class LockOverlay:
    """The break window itself, one per monitor."""

    BG = "#12161c"        # deep charcoal, softer than pure black
    FG = "#e8eef7"
    MUTED = "#7c8899"
    FAINT = "#49525f"     # safety hint: legible, never the first thing seen
    ACCENT = "#8fb4d9"    # marks the long break as different

    def __init__(self, root: tk.Tk, config: Config = DEFAULT) -> None:
        self._root = root
        self._config = config
        self._windows: list[tk.Toplevel] = []
        self._countdowns: list[tk.Label] = []
        self._suppressor: InputSuppressor | None = None
        self._released_early = False

    @property
    def visible(self) -> bool:
        return bool(self._windows)

    @property
    def released_early(self) -> bool:
        """True if the safety hold released this lock before time was up."""
        return self._released_early

    def lock(self, is_long_break: bool, duration: float) -> None:
        if self._windows:
            return
        self._released_early = False

        heading = "Long break" if is_long_break else "Break"
        back_at = time.strftime("%H:%M", time.localtime(time.time() + duration))

        for rect in monitor_rects(self._root):
            window, countdown = self._build_window(rect, heading, back_at,
                                                   is_long_break)
            self._windows.append(window)
            self._countdowns.append(countdown)

        self._suppressor = InputSuppressor(
            on_safety_hold=self._safety_release
            if self._config.safety_unlock
            else None,
            hold_seconds=self._config.safety_unlock_hold,
        )
        self._suppressor.start()

    def tick(self, remaining: float) -> None:
        """Refresh the countdown and reassert always-on-top."""
        if not self._windows:
            return
        minutes, seconds = divmod(int(max(0.0, remaining) + 0.5), 60)
        text = f"{minutes:02d}:{seconds:02d}"
        for countdown in self._countdowns:
            countdown.configure(text=text)
        # Something else going topmost mid-break would defeat the lock, so
        # we take the z-order back on every tick rather than trusting the
        # attribute to hold for the whole break.
        for window in self._windows:
            window.attributes("-topmost", True)
            window.lift()

    def release(self) -> None:
        if self._suppressor is not None:
            self._suppressor.stop()
            self._suppressor = None
        for window in self._windows:
            window.destroy()
        self._windows = []
        self._countdowns = []

    # -- internals ----------------------------------------------------

    def _build_window(self, rect, heading, back_at, is_long_break):
        left, top, width, height = rect
        # Type sized for a 1080p screen is oversized on a smaller laptop
        # panel, so scale it to the monitor it is actually drawn on.
        scale = max(0.6, min(1.0, height / 1080))
        pt = lambda size: max(8, int(round(size * scale)))

        window = tk.Toplevel(self._root)
        window.configure(bg=self.BG)
        window.overrideredirect(True)      # no title bar, no close button
        window.attributes("-topmost", True)
        window.geometry(f"{width}x{height}+{left}+{top}")
        # Refuse the window-manager close path as well as the visual one.
        window.protocol("WM_DELETE_WINDOW", lambda: None)

        # place() rather than pack() so the block sits at the true centre of
        # *this* monitor. Packed children stack from the top edge instead.
        body = tk.Frame(window, bg=self.BG)
        body.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            body, text=heading, font=("Segoe UI", pt(34)),
            bg=self.BG, fg=self.ACCENT if is_long_break else self.MUTED,
        ).pack()

        countdown = tk.Label(
            body, text="", font=("Consolas", pt(120), "bold"),
            bg=self.BG, fg=self.FG,
        )
        countdown.pack(pady=(pt(10), pt(22)))

        tk.Label(
            body, text="Stand up. Look away from the screen.",
            font=("Segoe UI", pt(17)), bg=self.BG, fg=self.FG,
        ).pack()

        tk.Label(
            body, text=f"back at {back_at}", font=("Segoe UI", pt(13)),
            bg=self.BG, fg=self.MUTED,
        ).pack(pady=(pt(12), 0))

        if self._config.safety_unlock:
            tk.Label(
                window,
                text=(
                    f"hold Esc for "
                    f"{self._config.safety_unlock_hold:.0f}s to release"
                ),
                font=("Segoe UI", pt(10)), bg=self.BG, fg=self.FAINT,
            ).place(relx=0.5, rely=0.94, anchor="center")

        window.update_idletasks()
        window.lift()
        window.focus_force()
        return window, countdown

    def _safety_release(self) -> None:
        """Called from the listener thread — hand back to the UI thread."""
        self._released_early = True
        self._root.after(0, self.release)


class WarningBanner:
    """The 2-minute heads-up before the lock (SPEC §2.3).

    A small always-on-top toast in the corner of the primary monitor — it
    must not steal focus or block anything, since the whole point is
    letting you wrap up first.
    """

    BG = "#2b2113"
    FG = "#ffd8a8"

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._window: tk.Toplevel | None = None
        self._label: tk.Label | None = None

    def show(self) -> None:
        if self._window is not None:
            return
        window = tk.Toplevel(self._root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=self.BG)

        self._label = tk.Label(
            window, text="", font=("Segoe UI", 14, "bold"),
            bg=self.BG, fg=self.FG, padx=22, pady=14,
        )
        self._label.pack()

        window.update_idletasks()
        margin = 24
        left, top, width, _height = primary_rect(self._root)
        x = left + width - window.winfo_reqwidth() - margin
        window.geometry(f"+{x}+{top + margin}")
        self._window = window

    def tick(self, remaining: float) -> None:
        if self._label is None or self._window is None:
            return
        minutes, seconds = divmod(int(max(0.0, remaining) + 0.5), 60)
        self._label.configure(text=f"Break in {minutes}:{seconds:02d}")
        self._window.attributes("-topmost", True)

    def hide(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._label = None
