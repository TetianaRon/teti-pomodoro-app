"""The full-screen break lock (SPEC §2.3).

This is the part that makes the app more than a reminder: a borderless,
always-on-top window covering every monitor, with keyboard and mouse input
swallowed underneath it so alt-tabbing away isn't an escape hatch.

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


class LockOverlay:
    """The break window itself."""

    BG = "#12161c"
    FG = "#e8eef7"
    MUTED = "#7c8899"

    def __init__(self, root: tk.Tk, config: Config = DEFAULT) -> None:
        self._root = root
        self._config = config
        self._window: tk.Toplevel | None = None
        self._countdown: tk.Label | None = None
        self._suppressor: InputSuppressor | None = None
        self._released_early = False

    @property
    def visible(self) -> bool:
        return self._window is not None

    @property
    def released_early(self) -> bool:
        """True if the safety hold released this lock before time was up."""
        return self._released_early

    def lock(self, is_long_break: bool) -> None:
        if self._window is not None:
            return
        self._released_early = False

        window = tk.Toplevel(self._root)
        window.configure(bg=self.BG)
        window.overrideredirect(True)      # no title bar, no close button
        window.attributes("-topmost", True)
        window.geometry(self._virtual_screen_geometry())
        # Refuse the window-manager close path as well as the visual one.
        window.protocol("WM_DELETE_WINDOW", lambda: None)

        heading = "Long break" if is_long_break else "Break"
        tk.Label(
            window, text=heading, font=("Segoe UI", 44, "bold"),
            bg=self.BG, fg=self.FG,
        ).pack(pady=(0, 8))

        tk.Label(
            window, text="Stand up. Look away from the screen.",
            font=("Segoe UI", 17), bg=self.BG, fg=self.MUTED,
        ).pack()

        self._countdown = tk.Label(
            window, text="", font=("Consolas", 96, "bold"),
            bg=self.BG, fg=self.FG,
        )
        self._countdown.pack(pady=28)

        if self._config.safety_unlock:
            tk.Label(
                window,
                text=(
                    f"Safety release: hold Escape for "
                    f"{self._config.safety_unlock_hold:.0f}s"
                ),
                font=("Segoe UI", 11), bg=self.BG, fg=self.MUTED,
            ).pack(side="bottom", pady=18)

        # Centre the content block within the full-screen frame.
        for child in window.winfo_children():
            child.pack_configure(anchor="center")
        window.update_idletasks()
        window.lift()
        window.focus_force()

        self._window = window
        self._suppressor = InputSuppressor(
            on_safety_hold=self._safety_release
            if self._config.safety_unlock
            else None,
            hold_seconds=self._config.safety_unlock_hold,
        )
        self._suppressor.start()

    def tick(self, remaining: float) -> None:
        """Refresh the countdown and reassert always-on-top."""
        if self._window is None or self._countdown is None:
            return
        remaining = max(0.0, remaining)
        minutes, seconds = divmod(int(remaining + 0.5), 60)
        self._countdown.configure(text=f"{minutes:02d}:{seconds:02d}")
        # Something else going topmost mid-break would defeat the lock, so
        # we take the z-order back on every tick rather than trusting the
        # attribute to hold for the whole break.
        self._window.attributes("-topmost", True)
        self._window.lift()

    def release(self) -> None:
        if self._suppressor is not None:
            self._suppressor.stop()
            self._suppressor = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._countdown = None

    # -- internals ----------------------------------------------------

    def _safety_release(self) -> None:
        """Called from the listener thread — hand back to the UI thread."""
        self._released_early = True
        self._root.after(0, self.release)

    def _virtual_screen_geometry(self) -> str:
        """Cover every monitor, not just the primary one.

        Uses the virtual-screen bounding box, which is exact for the usual
        side-by-side arrangements. An L-shaped layout would leave a gap;
        per-monitor windows are the fix if that ever comes up.
        """
        try:
            import win32api
            import win32con

            width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        except ImportError:
            width = self._root.winfo_screenwidth()
            height = self._root.winfo_screenheight()
            left = top = 0
        return f"{width}x{height}+{left}+{top}"


class WarningBanner:
    """The 2-minute heads-up before the lock (SPEC §2.3).

    A small always-on-top toast in the corner — it must not steal focus or
    block anything, since the whole point is letting you wrap up first.
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
        x = window.winfo_screenwidth() - window.winfo_reqwidth() - margin
        window.geometry(f"+{x}+{margin}")
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
