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

from . import media, pill
from .config import DEFAULT, Config
from .pill import CountdownPill


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

    def start(self) -> bool:
        """Begin swallowing input. Says whether suppression is really live.

        The caller needs the answer, because it changes what the lock *is*:
        with suppression it enforces a break, without it the overlay is a
        reminder you can click past. Both are useful; being unable to tell
        them apart is not.

        Nothing here is allowed to raise. `lock()` runs from the tick, and
        the tick's last statement is the `after()` call that schedules the
        next one — so an exception escaping this method would stop the loop
        rescheduling and leave a borderless, always-on-top window on every
        screen with nothing alive to take it down. A lock that fails to
        block input is a far better outcome than one that cannot end.

        `ImportError` alone does not cover it: on macOS pynput imports
        cleanly and then fails to create the event tap when Accessibility
        permission has not been granted.
        """
        if self._listeners:
            return True
        self._stopped.clear()
        self.arm_watchdog()
        try:
            from pynput import keyboard

            self._escape_key = keyboard.Key.esc
            self._fired = False
            listeners = [self._keyboard_listener(), self._mouse_listener()]
            for listener in listeners:
                listener.daemon = True
                listener.start()
        except Exception:
            # No pynput, or the OS refused the hook: the overlay still
            # covers the screen, it just won't block input underneath.
            self._listeners = []
            return False

        self._listeners = listeners
        return True

    def _keyboard_listener(self):
        from pynput import keyboard

        return keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=True,
        )

    def _mouse_listener(self):
        from pynput import mouse

        return mouse.Listener(
            on_move=self._swallow,
            on_click=self._swallow,
            on_scroll=self._swallow,
            suppress=True,
        )

    def with_keyboard_lifted(self, action) -> bool:
        """Run `action` with keyboard suppression down for that instant.

        Needed because a media key this app injects is caught by its own
        hook like any other. Only the *keyboard* listener is dropped — the
        mouse stays blocked — and only for the few milliseconds it takes to
        post one keystroke, on a deliberate keypress from the lock screen.

        That is a real if tiny hole in enforcement, and the honest trade:
        the alternative is either media that cannot be paused at all, or
        guessing whether to pause it, which is what caused a paused video
        to start playing.
        """
        if not self._listeners:
            action()
            return True
        keyboard_listener = self._listeners[0]
        try:
            keyboard_listener.stop()
            self._listeners = self._listeners[1:]
            action()
        finally:
            try:
                replacement = self._keyboard_listener()
                replacement.daemon = True
                replacement.start()
                self._listeners.insert(0, replacement)
            except Exception:  # pragma: no cover - must never leave it off
                pass
        return True

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
        walk_state: "callable | None" = None,
        on_stop_walk: "callable | None" = None,
    ) -> None:
        self._root = root
        self._config = config
        # SPEC §4B. With no offer provided the hold-Escape gesture keeps its
        # Phase 1 meaning and simply releases the lock — used by tests and
        # by --no-exclusions style development runs.
        self._skip_offer = skip_offer
        self._on_skip = on_skip
        self._on_emergency = on_emergency
        # Walking keeps counting through a break — you are still walking —
        # but the prompt and the tray are both behind the lock, so without
        # a key here a walk that ended mid-break would over-count by up to
        # the whole break. That error raises the work cap, which is the
        # direction that undermines the goal.
        self._walk_state = walk_state
        self._on_stop_walk = on_stop_walk
        self._windows: list[tk.Toplevel] = []
        self._countdowns: list[tk.Label] = []
        self._hints: list[tk.Label] = []
        self._bodies: list[tk.Frame] = []
        self._menus: list[tk.Frame] = []
        self._notices: list[tk.Label] = []
        self._offer = SkipOffer()
        self._menu_open = False
        self._swallow_escape = False
        self._suppressor: InputSuppressor | None = None
        self._released_early = False
        # When the Escape hold began, so the bottom caption can count down
        # live instead of staying silent for the full 3s — reported
        # 2026-08-22 as reading like the menu needed a *release* to appear
        # at all, when actually nothing acknowledged the hold was even
        # registered until it finished. None while no hold is in progress.
        self._hold_started_at: float | None = None
        self._hold_captions: list[tk.Label] = []
        # Whether the current lock is actually blocking input, or is only a
        # full-screen reminder. Drives what the screen admits to, and
        # whether the z-order is fought for — see _bind_local_keys.
        self._suppressing = False
        self._local_hold: str | None = None
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

    @property
    def suppressing(self) -> bool:
        """Whether the current lock is enforcing, or only reminding."""
        return self._suppressing

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
        self._hold_started_at = None
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
        # Wanted *and* permitted. The preference is asked first so a machine
        # that would refuse the hook is never even asked, which keeps a
        # deliberate reminder mode free of the OS's permission prompt.
        self._suppressing = (
            self._config.block_input and self._suppressor.start()
        )
        if not self._suppressing:
            self._admit_reminder_only()

    def tick(self, remaining: float) -> None:
        """Refresh the countdown, handle input, and reassert always-on-top."""
        self._drain()
        if not self._windows:
            return
        minutes, seconds = divmod(int(max(0.0, remaining) + 0.5), 60)
        text = f"{minutes:02d}:{seconds:02d}"
        for countdown in self._countdowns:
            countdown.configure(text=text)
        # Refreshed here as well: the walking total climbs during a break,
        # and a "W to stop" hint that never appeared would be useless if the
        # walk started after the lock went up.
        if not self._menu_open:
            hint = self._hint_text()
            for label in self._hints:
                if label.cget("text") != hint:
                    label.configure(text=hint)
            # Live feedback while Escape is held, counting down to the same
            # caption once it either finishes or is let go — see
            # _hold_progress_caption for why this exists.
            caption = self._hold_progress_caption() or self._skip_caption()
            for label in self._hold_captions:
                if label.cget("text") != caption:
                    label.configure(text=caption)
        # Something else going topmost mid-break would defeat the lock, so
        # we take the z-order back on every tick rather than trusting the
        # attribute to hold for the whole break.
        #
        # Not when input isn't blocked, though. Then this same loop is
        # actively harmful: it would drag a window she is allowed to leave
        # back over whatever she switched to, once a second, for the whole
        # break — unmissable *and* inescapable, which is the one combination
        # worse than either. A reminder has to be leaveable.
        if not self._suppressing:
            return
        for window in self._windows:
            window.attributes("-topmost", True)
            window.lift()

    def release(self) -> None:
        if self._suppressor is not None:
            self._suppressor.stop()
            self._suppressor = None
        self._cancel_local_hold()
        self._suppressing = False
        for window in self._windows:
            window.destroy()
        self._windows = []
        self._countdowns = []
        self._hints = []
        self._hold_captions = []
        self._bodies = []
        self._menus = []
        self._notices = []
        self._menu_open = False
        self._hold_started_at = None

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

        # Offered, not fired automatically: see _media_key.
        hint = tk.Label(
            body, text=self._hint_text(),
            font=("Segoe UI", pt(12)), bg=self.BG, fg=self.MUTED,
        )
        hint.pack(pady=(28, 0))
        self._hints.append(hint)

        if self._config.safety_unlock:
            caption = tk.Label(
                window, text=self._skip_caption(),
                font=("Segoe UI", pt(10)), bg=self.BG, fg=self.FAINT,
            )
            caption.place(relx=0.5, rely=0.94, anchor="center")
            self._hold_captions.append(caption)

        # Filled in only when input turns out not to be blocked, so the
        # screen never claims to be enforcing something it isn't.
        notice = tk.Label(
            window, text="", font=("Segoe UI", pt(11)),
            bg=self.BG, fg=self.MUTED,
        )
        notice.place(relx=0.5, rely=0.06, anchor="center")
        self._notices.append(notice)

        self._bind_local_keys(window)
        window.update_idletasks()
        window.lift()
        window.focus_force()
        return window, countdown, body

    # -- keys without a global hook ------------------------------------

    #: tkinter keysym -> the name pynput reports. Only the keys the lock
    #: actually answers to need an entry.
    _KEYSYMS = {"Escape": "esc"}

    def _bind_local_keys(self, window: tk.Toplevel) -> None:
        """Route this window's own key events into the same queue.

        A permission-free path to every lock-screen gesture, and the reason
        a lock that failed to suppress input is still usable rather than a
        trap: without it, a macOS session that was refused Accessibility
        permission gets a borderless always-on-top window on every screen,
        no title bar, `WM_DELETE_WINDOW` refused, and no key handling — for
        the full fifteen minutes of a long break.

        It is **self-selecting**, which is why it is installed
        unconditionally rather than only when suppression is known to have
        failed. While suppression is live the keystroke is swallowed before
        any window sees it, so these bindings are inert on a working lock;
        they come into play only when nothing is being suppressed. That
        matters because the *interesting* failure is the silent one — a hook
        that starts cleanly and then receives nothing — which no return
        value from `start()` can detect. Self-selection covers it without
        having to.

        Everything is posted to `_pending` in pynput's own shape, so the
        skip menu, the media key and the walk key all behave identically
        through either path with no second implementation to keep in step.
        """
        window.bind("<KeyPress>", self._local_key_press)
        window.bind("<KeyRelease>", self._local_key_release)

    def _local_key_press(self, event) -> None:
        char, name = self._translate(event)
        if name == "esc":
            self._start_local_hold()
        self._pending.put(("key", char, name))

    def _local_key_release(self, event) -> None:
        char, name = self._translate(event)
        if name == "esc":
            self._cancel_local_hold()
        self._pending.put(("keyup", char, name))

    @classmethod
    def _translate(cls, event) -> tuple[str | None, str | None]:
        """A tkinter event as the (char, name) pair the queue expects."""
        # None rather than "" for a key with no text, matching what pynput
        # reports — the queue is drained by one code path for both.
        char = getattr(event, "char", "") or ""
        return (char if char and char.isprintable() else None), cls._KEYSYMS.get(
            getattr(event, "keysym", "")
        )

    def _start_local_hold(self) -> None:
        """Time an Escape hold on tkinter's clock.

        Started once and then left alone: Escape auto-repeats hard — 606
        events across one measured 60-second lock — so restarting the timer
        on each repeat would push the deadline past the end of the break.
        Mirrors the global path, which learned this the same way.
        """
        if not self._config.safety_unlock or self._local_hold is not None:
            return
        if self._root is None:      # stubbed in tests
            return
        self._local_hold = self._root.after(
            int(self._config.safety_unlock_hold * 1000), self._local_hold_fired
        )

    def _local_hold_fired(self) -> None:
        self._local_hold = None
        self._pending.put(("hold", None, None))

    def _cancel_local_hold(self) -> None:
        if self._local_hold is None:
            return
        try:
            self._root.after_cancel(self._local_hold)
        except Exception:   # pragma: no cover - a dead job must not raise
            pass
        self._local_hold = None

    def reminder_reason(self) -> str:
        """Why this break is not enforcing, in the words that fit the cause.

        The two causes need different words. Chosen is a setting she can
        change; refused is a permission she may not be allowed to grant.
        Reporting them the same way would send her to the wrong place.
        """
        if not self._config.block_input:
            return "reminder mode — enforcement is switched off in settings"
        return "reminder only — this system will not let input be blocked"

    def _admit_reminder_only(self) -> None:
        """Say on screen that this break is not being enforced.

        Silence here would be the dishonest option: an identical-looking
        lock that happens not to block anything teaches her the app is
        broken rather than that it is deliberately, or unavoidably, only
        reminding.
        """
        release = (
            f"hold Esc {self._config.safety_unlock_hold:.0f}s"
            if self._config.safety_unlock
            else "click away"
        )
        text = f"{self.reminder_reason()}  ·  {release} to dismiss"
        for notice in self._notices:
            try:
                notice.configure(text=text)
            except tk.TclError:   # pragma: no cover - window already gone
                continue

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
                self._hold_started_at = None
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
                    # Let go before the full hold — the countdown caption
                    # reverts to its resting text rather than freezing.
                    self._hold_started_at = None
            elif kind == "key":
                # The very first press of a hold, seen well before the
                # timer that opens the menu fires (SPEC §4B) — this is what
                # lets the caption count down live instead of staying silent
                # for the whole 3s, which read as the menu needing a
                # *release* to appear at all (reported 2026-08-22).
                if (
                    name == "esc" and not self._menu_open
                    and self._hold_started_at is None
                ):
                    self._hold_started_at = time.monotonic()
                if self._walk_key(char):
                    continue
                if not self._media_key(char, name):
                    self._menu_key(char, name)

    def _media_key(self, char: str | None, name: str | None) -> bool:
        """M, or a real media key, toggles playback. True if consumed.

        Offered rather than fired automatically. Deciding for the user
        meant guessing whether media was playing, and a wrong guess starts
        something that was deliberately paused — a toggle cannot tell the
        difference. Pressing it yourself always means what you intended.
        """
        wanted = (char or "").lower() == "m" or name in (
            "media_play_pause", "media_volume_mute",
        )
        if not wanted or self._suppressor is None:
            return False
        mute = name == "media_volume_mute"
        self._suppressor.with_keyboard_lifted(
            media.send_mute if mute else media.send_play_pause
        )
        self._flash("muted" if mute else "media toggled")
        return True

    def _skip_caption(self) -> str:
        """The resting bottom caption naming the Escape-hold gesture."""
        if self._skip_offer is not None:
            return (
                f"hold Esc for {self._config.safety_unlock_hold:.0f}s "
                f"to skip this break"
            )
        return f"hold Esc for {self._config.safety_unlock_hold:.0f}s to release"

    def _hold_progress_caption(self) -> str | None:
        """Live countdown while Escape is held, or None to show the resting
        caption instead.

        Reported 2026-08-22: the skip menu appeared to need a *release* to
        show up, because nothing on screen acknowledged the hold for the
        full 3 seconds it actually takes — the menu itself was always timed
        from the press, not the release, but silence for three whole
        seconds reads exactly like nothing is happening. This is what fills
        that silence: counted from `_hold_started_at`, set the instant the
        first Escape keydown of a hold is seen (see `_drain`).
        """
        if self._hold_started_at is None:
            return None
        remaining = self._config.safety_unlock_hold - (
            time.monotonic() - self._hold_started_at
        )
        if remaining <= 0:
            return None
        return f"still holding — opening in {remaining:.0f}s"

    def _hint_text(self) -> str:
        """The key hints, including the walking line only while walking."""
        parts = ["M   pause or resume media"]
        walking, seconds = self._walking_now()
        if walking:
            parts.append(
                f"W   stop the treadmill timer  ({seconds / 60:.0f} min so far)"
            )
        return "\n".join(parts)

    def _walking_now(self) -> tuple[bool, float]:
        if self._walk_state is None:
            return False, 0.0
        try:
            return self._walk_state()
        except Exception:  # pragma: no cover - a hint must not break the lock
            return False, 0.0

    def _walk_key(self, char: str | None) -> bool:
        """W stops the treadmill timer. True if the key was consumed."""
        if (char or "").lower() != "w":
            return False
        walking, _ = self._walking_now()
        if not walking or self._on_stop_walk is None:
            return False
        self._on_stop_walk()
        self._flash("treadmill timer stopped")
        return True

    def _flash(self, message: str) -> None:
        """Confirm a keypress on screen, since nothing else acknowledges it."""
        for label in self._hints:
            try:
                label.configure(text=message)
            except tk.TclError:
                continue

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
        # Otherwise the countdown caption freezes on whatever it last said
        # ("opening in 1s"...) for as long as the menu stays open, since
        # tick() stops refreshing it once _menu_open is set.
        caption = self._skip_caption()
        for label in self._hold_captions:
            label.configure(text=caption)
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
    """The two-minute heads-up before the lock (SPEC §2.3).

    Shown on **every** monitor, not just the primary one. First real-use
    feedback was that the warning fired correctly and went unnoticed
    entirely: it was a small toast in one corner of one screen while the work
    was happening on another.

    So it arrives with an **attention pass** — large and centred on each
    screen, then shrinking as it travels to the bottom-right corner, where it
    settles at exactly the size and place of the standing countdown and takes
    its spot. Motion in the middle of the screen is hard to miss; a static
    corner toast plainly was not.

    It is the *same pill* as the countdown (`pill.py`), in a different
    colour. That is deliberate rather than economical: they occupy the same
    corner, one replacing the other, so any difference in shape or weight
    would read as two unrelated things competing for the position.

    It must not steal focus or block anything — the whole point is letting
    you wrap up first — so it is click-through, and it fades when the cursor
    comes near so you can see whatever it is sitting over. Because a
    click-through window receives no mouse events, `<Enter>` and `<Leave>`
    never fire; hover is found by polling the cursor against each rectangle.
    """

    #: The attention pass. Long enough to register while looking elsewhere,
    #: short enough not to sit over the work being wrapped up.
    HOLD_SECONDS = 1.4
    TRAVEL_SECONDS = 0.9
    BIG_SCALE = 4.5
    #: 30fps rather than 40: a pill has to be redrawn to change size, and at
    #: the largest scale that costs about 22ms. The extra headroom keeps the
    #: animation off the tick's back.
    FRAME_MS = 33

    def __init__(self, root: tk.Tk, config: Config = DEFAULT) -> None:
        self._root = root
        self._config = config
        self._windows: list[pill.PillWindow] = []
        self._areas: list[tuple] = []
        self._rects: list[tuple] = []
        self._poll_job: str | None = None
        self._anim_job: str | None = None
        self._started_at = 0.0
        self._text = "Break in 0:00"
        self._tone = "warn"
        self._hovering = False

    # -- what the app calls -------------------------------------------

    @property
    def visible(self) -> bool:
        return bool(self._windows)

    def show(self, tone: str = "warn") -> None:
        """Open on every monitor and begin the attention pass."""
        if self._windows:
            return
        self._tone = tone
        self._started_at = time.monotonic()
        self._areas = [
            pill.work_area(self._root, rect)
            for rect in monitor_rects(self._root)
        ]
        self._windows = [pill.PillWindow(self._root) for _ in self._areas]
        self._rects = [(0, 0, 0, 0)] * len(self._areas)
        self._hovering = False
        self._draw()
        self._schedule_poll()
        self._animate()

    def set_text(self, text: str) -> None:
        """Change the message on every screen, keeping each anchored."""
        if text == self._text:
            return
        self._text = text
        if self._windows:
            self._draw()

    def tick(self, remaining: float) -> None:
        minutes, seconds = divmod(int(max(0.0, remaining) + 0.5), 60)
        self.set_text(f"Break in {minutes}:{seconds:02d}")

    def notice(self, text: str) -> None:
        """Show a standing message rather than a countdown.

        Used for the over-the-cap marker and the stuck-device warning
        (SPEC §3, §5), which would otherwise only reach a console nobody is
        looking at. Drawn in the frozen colour rather than the warning one:
        nothing is about to happen, so it should not read like it.
        """
        if not self._windows:
            self.show(tone="held")
        elif self._tone != "held":
            self._tone = "held"
            self._draw()
        self.set_text(text)

    def hide(self) -> None:
        for job in (self._poll_job, self._anim_job):
            if job is not None:
                try:
                    self._root.after_cancel(job)
                except Exception:   # pragma: no cover - a dead job is fine
                    pass
        self._poll_job = self._anim_job = None
        for window in self._windows:
            window.hide()
        self._windows = []
        self._areas = []
        self._rects = []

    # -- the attention pass -------------------------------------------

    def _eased(self) -> float:
        """0 while held large and centred, 1 once settled in the corner.

        Ease-out, so it leaves the centre briskly and arrives gently.
        """
        progress = (
            (time.monotonic() - self._started_at - self.HOLD_SECONDS)
            / self.TRAVEL_SECONDS
        )
        if progress <= 0:
            return 0.0
        if progress >= 1:
            return 1.0
        return 1 - (1 - progress) ** 3

    def _animate(self) -> None:
        if not self._windows:
            return
        if self._eased() >= 1.0:
            self._draw()            # settled: resident size and place
            self._anim_job = None
            return
        self._draw()
        self._anim_job = self._root.after(self.FRAME_MS, self._animate)

    def _draw(self) -> None:
        """Render at the current size and place it on every screen."""
        eased = self._eased()
        scale = self.BIG_SCALE + (1.0 - self.BIG_SCALE) * eased
        height = max(
            CountdownPill.HEIGHT, int(round(CountdownPill.HEIGHT * scale))
        )
        image = pill.render(self._text, self._tone, height)

        alpha = (
            self._config.banner_alpha_hover if self._hovering
            else self._config.banner_alpha
        )
        rects = []
        for index, (window, area) in enumerate(zip(self._windows, self._areas)):
            x, y = self._place(area, image.width, image.height, eased)
            window.show(image, x, y, alpha=alpha)
            window.raise_above()
            rects.append((x, y, image.width, image.height))
        self._rects = rects

    @staticmethod
    def _place(area, width: int, height: int, eased: float) -> tuple:
        """Interpolate between the centre of `area` and its bottom-right."""
        centre_x = area[0] + (area[2] - area[0] - width) // 2
        centre_y = area[1] + (area[3] - area[1] - height) // 2
        corner_x, corner_y = pill.corner(area, width, height)
        return (int(centre_x + (corner_x - centre_x) * eased),
                int(centre_y + (corner_y - centre_y) * eased))

    # -- fading out of the way ----------------------------------------

    def _schedule_poll(self) -> None:
        self._poll_job = self._root.after(
            int(self._config.banner_hover_poll * 1000), self._poll_hover
        )

    def _poll_hover(self) -> None:
        """Fade every banner once the cursor is over any of them.

        The cursor can only be on one screen, but the pill is small and in a
        corner: the reason to fade is that you are reaching for what is
        underneath, and on the screen you are reaching on that is the only
        one that matters. Faded together rather than individually, because a
        30px pill has nothing left to hide once you are near it.
        """
        if not self._windows:
            return
        try:
            x, y = self._root.winfo_pointerxy()
        except tk.TclError:     # pragma: no cover - teardown race
            self._schedule_poll()
            return

        over = any(
            left <= x < left + width and top <= y < top + height
            for left, top, width, height in self._rects
        )
        if over != self._hovering:
            self._hovering = over
            alpha = (
                self._config.banner_alpha_hover if over
                else self._config.banner_alpha
            )
            for window in self._windows:
                window.set_alpha(alpha)
        self._schedule_poll()
