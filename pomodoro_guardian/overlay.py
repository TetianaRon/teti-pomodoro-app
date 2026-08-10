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
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass

from . import media
from .config import DEFAULT, Config


class InputSuppressor:
    """Swallows global keyboard and mouse input while the lock is up.

    Uses pynput's suppressing listeners. Registers an atexit hook because
    leaving suppression on after a crash would wedge the whole machine —
    the one failure mode here that isn't recoverable by closing the app.
    """

    def __init__(self, on_safety_hold: "callable | None" = None,
                 hold_seconds: float = 3.0,
                 max_seconds: float | None = None,
                 on_key: "callable | None" = None,
                 on_key_release: "callable | None" = None) -> None:
        self._on_safety_hold = on_safety_hold
        self._on_key = on_key
        self._on_key_release = on_key_release
        self._hold_seconds = hold_seconds
        self._max_seconds = max_seconds
        self._listeners: list = []
        self._escape_down_at: float | None = None
        self._escape_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._fired = False
        self._stopped = threading.Event()
        atexit.register(self.stop)

    def arm_watchdog(self) -> None:
        """Release suppression unconditionally after `max_seconds`.

        A plain daemon thread, deliberately owning no reference to the UI:
        its whole job is to survive a frozen tkinter loop. Suppressed input
        with a wedged UI is the one failure here that a user cannot talk
        their way out of.
        """
        if not self._max_seconds:
            return

        def guard() -> None:
            if not self._stopped.wait(self._max_seconds):
                self.stop()

        threading.Thread(target=guard, daemon=True).start()

    def start(self) -> None:
        if self._listeners:
            return
        self._stopped.clear()
        self.arm_watchdog()
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
        self._stopped.set()   # stands the watchdog down
        with self._lock:
            timer, self._escape_timer = self._escape_timer, None
        if timer is not None:
            timer.cancel()
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
        # Forwarded to the skip menu. The key is still suppressed from every
        # other app — the menu sees it, nothing else does, which is what
        # lets a keyboard-driven menu work behind a total input block.
        if self._on_key is not None:
            try:
                self._on_key(key)
            except Exception:  # pragma: no cover - a UI slip must not wedge input
                pass

        if self._on_safety_hold is None or key != self._escape_key:
            return

        # Timed from a real timer rather than counted from repeat events.
        # The previous version recorded the first press and waited for
        # auto-repeat to tell it how long the key had been down, so a
        # keyboard that doesn't repeat Escape produced exactly one event
        # and the hold could never fire at all.
        with self._lock:
            if self._escape_timer is not None or self._fired:
                return
            self._escape_down_at = time.monotonic()
            timer = threading.Timer(self._hold_seconds, self._hold_elapsed)
            timer.daemon = True
            self._escape_timer = timer
        timer.start()

    def _hold_elapsed(self) -> None:
        """Escape stayed down for the full duration."""
        with self._lock:
            if self._fired or self._escape_timer is None:
                return
            self._fired = True
        if self._on_safety_hold is not None:
            self._on_safety_hold()

    def _on_release(self, key) -> None:
        if self._on_key_release is not None:
            try:
                self._on_key_release(key)
            except Exception:  # pragma: no cover - must not wedge input
                pass
        if key != self._escape_key:
            return
        with self._lock:
            timer, self._escape_timer = self._escape_timer, None
            self._escape_down_at = None
            self._fired = False
        if timer is not None:
            timer.cancel()


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


@dataclass(frozen=True)
class SkipOption:
    """One entry in the skip menu (SPEC §4B)."""

    seconds: float
    label: str
    enabled: bool


@dataclass(frozen=True)
class SkipOffer:
    """What the skip menu should show when it opens."""

    options: tuple[SkipOption, ...] = ()
    remaining: float = 0.0
    #: Emergency Mode (SPEC §5), offered on the same gesture rather than a
    #: second one — it is wanted at exactly the moment the skip menu is up.
    emergency: SkipOption | None = None
    note: str = ""

    @property
    def any_available(self) -> bool:
        return any(o.enabled for o in self.options)


class LockOverlay:
    """The break window itself, one per monitor."""

    BG = "#12161c"        # deep charcoal, softer than pure black
    FG = "#e8eef7"
    MUTED = "#7c8899"
    FAINT = "#49525f"     # safety hint: legible, never the first thing seen
    ACCENT = "#8fb4d9"    # marks the long break as different
    KEYCAP = "#e8eef7"    # filled keycap, so a digit reads as a key

    def __init__(
        self,
        root: tk.Tk,
        config: Config = DEFAULT,
        skip_offer: "callable | None" = None,
        on_skip: "callable | None" = None,
        on_emergency: "callable | None" = None,
    ) -> None:
        self._root = root
        self._config = config
        # SPEC §4B. With no offer provided the hold-Escape gesture keeps its
        # Phase 1 meaning and simply releases the lock — used by tests and
        # by --no-exclusions style development runs.
        self._skip_offer = skip_offer
        self._on_skip = on_skip
        self._on_emergency = on_emergency
        self._windows: list[tk.Toplevel] = []
        self._countdowns: list[tk.Label] = []
        self._bodies: list[tk.Frame] = []
        self._menus: list[tk.Frame] = []
        self._offer = SkipOffer()
        self._menu_open = False
        self._swallow_escape = False
        self._suppressor: InputSuppressor | None = None
        self._released_early = False
        # Input arrives on pynput's listener thread, and tkinter must only
        # ever be touched from the thread running its loop. Even root.after()
        # is unsafe from outside — it happens to work while mainloop() is
        # running and raises "main thread is not in main loop" otherwise. So
        # nothing here calls tkinter across threads: the listener posts here
        # and tick(), which is already on the UI thread, drains it.
        self._pending: queue.Queue = queue.Queue()

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
        if self._config.pause_media_on_lock:
            # Before the suppressor starts, or our own hook would swallow
            # the media key. Only fires if something is actually playing —
            # the key is a toggle, so firing it into silence would start
            # playback instead of stopping it.
            media.pause_if_playing()
        # Anything the listener posted against a previous lock is stale.
        while not self._pending.empty():
            self._pending.get_nowait()

        heading = "Long break" if is_long_break else "Break"
        back_at = time.strftime("%H:%M", time.localtime(time.time() + duration))

        self._menu_open = False
        for rect in monitor_rects(self._root):
            window, countdown, body = self._build_window(
                rect, heading, back_at, is_long_break
            )
            self._windows.append(window)
            self._countdowns.append(countdown)
            self._bodies.append(body)

        self._suppressor = InputSuppressor(
            on_safety_hold=self._safety_hold
            if self._config.safety_unlock
            else None,
            hold_seconds=self._config.safety_unlock_hold,
            # Independent of the UI loop, so a hung tick can't leave input
            # suppressed with no way out.
            max_seconds=duration + self._config.lock_max_overrun,
            on_key=self._key_pressed,
            on_key_release=self._key_released,
        )
        self._suppressor.start()

    def tick(self, remaining: float) -> None:
        """Refresh the countdown, handle input, and reassert always-on-top."""
        self._drain()
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
        self._bodies = []
        self._menus = []
        self._menu_open = False

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
            hint = (
                f"hold Esc for {self._config.safety_unlock_hold:.0f}s "
                f"to skip this break"
                if self._skip_offer is not None
                else f"hold Esc for {self._config.safety_unlock_hold:.0f}s "
                     f"to release"
            )
            tk.Label(
                window, text=hint,
                font=("Segoe UI", pt(10)), bg=self.BG, fg=self.FAINT,
            ).place(relx=0.5, rely=0.94, anchor="center")

        window.update_idletasks()
        window.lift()
        window.focus_force()
        return window, countdown, body

    # -- the skip menu (SPEC §4B) -------------------------------------

    def _safety_hold(self) -> None:
        """Escape held for 3s. Runs on the listener thread — post, don't act."""
        self._pending.put(("hold", None, None))

    def _key_pressed(self, key) -> None:
        """A suppressed keystroke. Also the listener thread.

        Queued unconditionally, not only while the menu is open: the media
        controls answer at any point during a break.
        """
        self._pending.put(
            ("key", getattr(key, "char", None), getattr(key, "name", None))
        )

    def _key_released(self, key) -> None:
        """Key-up, so the menu can tell a fresh press from a held one."""
        self._pending.put(
            ("keyup", getattr(key, "char", None), getattr(key, "name", None))
        )

    def _drain(self) -> None:
        """Handle everything the listener posted. UI thread only."""
        while True:
            try:
                kind, char, name = self._pending.get_nowait()
            except queue.Empty:
                return
            if kind == "hold":
                if self._skip_offer is None:
                    # Phase 1 behaviour: no skip system wired up, so release.
                    self._released_early = True
                    self.release()
                    return
                self._open_menu()
            elif kind == "keyup":
                if name == "esc":
                    # Escape is free to mean "dismiss" again now that the
                    # hold which opened the menu has ended.
                    self._swallow_escape = False
            elif kind == "key":
                self._menu_key(char, name)

    def _open_menu(self) -> None:
        if self._menu_open or not self._windows:
            return
        # The hold that opened this menu is still down, and Escape
        # auto-repeats hard — measured at 606 events across one 60s lock.
        # Without this the next repeat would immediately read as "dismiss"
        # and close the menu in the same drain loop that opened it.
        self._swallow_escape = True
        self._offer = self._skip_offer()
        self._menu_open = True
        for body in self._bodies:
            self._menus.append(self._build_menu(body))

    def _build_menu(self, body: tk.Frame) -> tk.Frame:
        frame = tk.Frame(body, bg=self.BG)
        frame.pack(pady=(30, 0))

        available = self._offer.any_available
        tk.Label(
            frame,
            text=("Press a key to skip this break"
                  if available else "Skip budget used up for today"),
            font=("Segoe UI", 15), bg=self.BG, fg=self.FG,
        ).pack()

        row = tk.Frame(frame, bg=self.BG)
        row.pack(pady=(16, 0))
        for index, option in enumerate(self._offer.options, start=1):
            self._build_key(row, str(index), option)

        if self._offer.emergency is not None:
            # Visually separated: this spends a weekly budget, not the
            # daily skip one, and shouldn't read as a fourth skip length.
            tk.Frame(frame, bg=self.FAINT, height=1, width=340).pack(
                pady=(20, 0)
            )
            emergency_row = tk.Frame(frame, bg=self.BG)
            emergency_row.pack(pady=(16, 0))
            self._build_key(emergency_row, "E", self._offer.emergency)

        if self._offer.note:
            tk.Label(
                frame, text=self._offer.note, font=("Segoe UI", 11),
                bg=self.BG, fg=self.MUTED,
            ).pack(pady=(14, 0))

        remaining = self._offer.remaining / 60
        tk.Label(
            frame,
            text=(f"{remaining:.0f} min of skip left today   ·   "
                  f"release Esc, then press it again to stay on the break"),
            font=("Segoe UI", 11), bg=self.BG, fg=self.MUTED,
        ).pack(pady=(18, 0))
        return frame

    def _build_key(self, row: tk.Frame, digit: str, option: SkipOption) -> None:
        """One option, drawn so the digit reads as a key to press.

        A bare "1 · 5 min" was tried first and did not communicate that the
        number was an instruction rather than a label.
        """
        cell = tk.Frame(row, bg=self.BG)
        cell.pack(side="left", padx=14)

        enabled = option.enabled
        tk.Label(
            cell, text=f" {digit} ",
            font=("Consolas", 20, "bold"),
            bg=self.KEYCAP if enabled else self.BG,
            fg=self.BG if enabled else self.FAINT,
            relief="solid", borderwidth=1,
            highlightbackground=self.FAINT,
            padx=8, pady=2,
        ).pack()
        tk.Label(
            cell, text=option.label, font=("Segoe UI", 13),
            bg=self.BG, fg=self.FG if enabled else self.FAINT,
        ).pack(pady=(6, 0))

    def _close_menu(self) -> None:
        for menu in self._menus:
            menu.destroy()
        self._menus = []
        self._menu_open = False

    def _menu_key(self, char: str | None, name: str | None) -> None:
        if not self._menu_open:
            return
        if name == "esc":
            if self._swallow_escape:
                return   # still the hold that opened the menu
            self._close_menu()
            return
        if char and char.lower() == "e":
            emergency = self._offer.emergency
            if emergency is not None and emergency.enabled:
                if self._on_emergency is not None:
                    self._close_menu()
                    self._on_emergency()
            return
        if char and char.isdigit():
            index = int(char) - 1
            if 0 <= index < len(self._offer.options):
                option = self._offer.options[index]
                if option.enabled and self._on_skip is not None:
                    self._close_menu()
                    self._on_skip(option.seconds)


class WarningBanner:
    """The 2-minute heads-up before the lock (SPEC §2.3).

    A small always-on-top toast in the corner of the primary monitor. It
    must not steal focus or block anything — the whole point is letting you
    wrap up first — so it is made **click-through**: every click passes
    straight to whatever is underneath. It stays clearly readable by
    default and *fades* when the cursor moves over it, so you can see what
    it is covering at the moment you reach for it.

    Because a click-through window receives no mouse events, `<Enter>` and
    `<Leave>` never fire on it. Hover is therefore detected by polling the
    global cursor position against the banner's rectangle.
    """

    BG = "#2b2113"
    FG = "#ffd8a8"

    def __init__(self, root: tk.Tk, config: Config = DEFAULT) -> None:
        self._root = root
        self._config = config
        self._window: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._poll_job: str | None = None
        self._hovering = False

    def show(self) -> None:
        if self._window is not None:
            return
        window = tk.Toplevel(self._root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=self.BG)

        # Seeded with representative text, not "": the window is measured to
        # place it, and an empty label measures as bare padding. Positioning
        # off that width put the banner's right edge past the monitor and
        # spilled it onto the next screen once the real text arrived.
        self._label = tk.Label(
            window, text="Break in 0:00", font=("Segoe UI", 14, "bold"),
            bg=self.BG, fg=self.FG, padx=22, pady=14,
        )
        self._label.pack()

        # Assigned before positioning: _reposition() reads self._window and
        # would silently do nothing if this came later.
        self._window = window
        window.update_idletasks()
        self._reposition()

        # -alpha must be set before WS_EX_TRANSPARENT is added: setting it is
        # what makes the window layered in the first place.
        window.attributes("-alpha", self._config.banner_alpha)
        self._make_click_through(window)
        self._hovering = False
        self._schedule_poll()

    @property
    def visible(self) -> bool:
        return self._window is not None

    def set_text(self, text: str) -> None:
        """Change the message, keeping it anchored on screen."""
        if self._label is None or self._window is None:
            return
        before = self._window.winfo_reqwidth()
        self._label.configure(text=text)
        self._window.update_idletasks()
        # Widths are stable for the usual M:SS values, but a longer message
        # widens the text — re-anchor rather than let the banner grow off
        # the edge of the screen.
        if self._window.winfo_reqwidth() != before:
            self._reposition()
        self._window.attributes("-topmost", True)

    def tick(self, remaining: float) -> None:
        minutes, seconds = divmod(int(max(0.0, remaining) + 0.5), 60)
        self.set_text(f"Break in {minutes}:{seconds:02d}")

    def notice(self, text: str) -> None:
        """Show a standing message rather than a countdown.

        Used for the stuck-device warning (SPEC §3), which would otherwise
        only reach a console nobody is looking at.
        """
        if self._window is None:
            self.show()
        self.set_text(text)

    def hide(self) -> None:
        if self._poll_job is not None:
            self._root.after_cancel(self._poll_job)
            self._poll_job = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._label = None

    # -- internals ----------------------------------------------------

    def _reposition(self) -> None:
        """Anchor to the top-right of the primary monitor, fully on-screen."""
        if self._window is None:
            return
        margin = 24
        left, top, width, _height = primary_rect(self._root)
        x = left + width - self._window.winfo_reqwidth() - margin
        self._window.geometry(f"+{x}+{top + margin}")

    @staticmethod
    def _make_click_through(window: tk.Toplevel) -> None:
        """Pass every click through to whatever is underneath.

        Degrades to a normal (still translucent) window if pywin32 is
        missing — the banner blocking a small area is a far smaller problem
        than it failing to appear.
        """
        try:
            import win32con
            import win32gui
        except ImportError:
            return
        hwnd = win32gui.GetParent(window.winfo_id()) or window.winfo_id()
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd,
            win32con.GWL_EXSTYLE,
            style
            | win32con.WS_EX_LAYERED       # already set by -alpha; harmless
            | win32con.WS_EX_TRANSPARENT   # the click-through part
            | win32con.WS_EX_NOACTIVATE,   # never steal focus
        )

    def _schedule_poll(self) -> None:
        self._poll_job = self._root.after(
            int(self._config.banner_hover_poll * 1000), self._poll_hover
        )

    def _poll_hover(self) -> None:
        """Fade the banner while the cursor is over it."""
        if self._window is None:
            return
        try:
            import win32api

            x, y = win32api.GetCursorPos()
            wx, wy = self._window.winfo_rootx(), self._window.winfo_rooty()
            over = (
                wx <= x < wx + self._window.winfo_width()
                and wy <= y < wy + self._window.winfo_height()
            )
        except (ImportError, tk.TclError):
            over = False

        if over != self._hovering:
            self._hovering = over
            self._window.attributes(
                "-alpha",
                self._config.banner_alpha_hover
                if over
                else self._config.banner_alpha,
            )
        self._schedule_poll()
