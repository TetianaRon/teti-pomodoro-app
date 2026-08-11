"""System tray icon and menu (SPEC §9).

The tray is where anything that has to be reachable *outside* a break
lives: starting a walk when you fancy one rather than when the app asked,
correcting the day type, opening settings, quitting.

**Threading.** pystray runs its own event loop on its own thread, so menu
callbacks arrive off the UI thread. Nothing here touches tkinter: clicks
are posted to a queue that the app drains on its own tick — the same
arrangement the lock overlay uses, after calling `root.after()` from a
listener thread turned out to raise "main thread is not in main loop".

The icon degrades to nothing if pystray or Pillow are missing. A missing
tray is a lost convenience; it must not stop breaks being enforced.
"""

from __future__ import annotations

import queue
import threading

# Actions posted to the queue and handled by the app.
START_WALK = "start_walk"
STOP_WALK = "stop_walk"
START_FOCUS = "start_focus"
STOP_FOCUS = "stop_focus"
OPEN_SETTINGS = "open_settings"
SHOW_HISTORY = "show_history"
TOGGLE_STARTUP = "toggle_startup"
SET_DAY_OFF = "set_day_off"
SET_WORKING_DAY = "set_working_day"
CLEAR_OVERRIDE = "clear_override"
QUIT = "quit"

BODY = (198, 62, 48)
HIGHLIGHT = (224, 106, 92)
LEAF = (74, 138, 74)
WALK_BADGE = (46, 190, 110)


def render_icon(walking: bool = False, size: int = 64):
    """The tomato, with a green dot while walking.

    Drawn rather than loaded so the tray needs no file at runtime, and
    supersampled 4× before resizing because the real target is a 16px
    tray slot, where aliasing is the difference between a tomato and a
    smudge.

    The countdown deliberately does **not** appear here. It was drawn onto
    the icon first, over a plate, because that is the only way Windows will
    show a number in the notification area — but once `taskbar.py` put the
    same number in a legible pill beside the icon, the plate was covering
    two thirds of the tomato to say something already said better an inch
    to the left. The icon's job is being recognisable; the pill's job is
    being read.
    """
    from PIL import Image, ImageDraw

    scale = 4
    box = size * scale
    image = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = box / 256

    def at(*points):
        return [p * unit for p in points]

    draw.ellipse(at(16, 40, 240, 240), fill=BODY + (255,))
    draw.ellipse(at(44, 68, 120, 132), fill=HIGHLIGHT + (255,))
    draw.polygon(
        [(128 * unit, 44 * unit), (96 * unit, 8 * unit),
         (128 * unit, 24 * unit), (160 * unit, 8 * unit)],
        fill=LEAF + (255,),
    )

    if walking:
        # Ringed in the background colour so the dot stays legible against
        # the tomato at 16px.
        draw.ellipse(at(140, 140, 252, 252), fill=(18, 22, 28, 255))
        draw.ellipse(at(148, 148, 244, 244), fill=WALK_BADGE + (255,))

    return image.resize((size, size), Image.LANCZOS)


def _left_click_icon(pystray):
    """pystray's Icon, but opening the menu on a left click too.

    Its Windows backend pops the menu only on right-click; a left click
    activates the *default* menu item instead. Remapping the message lets
    left-click reuse pystray's own popup code rather than reimplementing
    `TrackPopupMenuEx`, which has to juggle foreground-window rules.

    Falls back to the stock Icon if the internals ever move — a tray that
    needs a right-click is a small loss next to no tray at all.
    """
    try:
        from pystray import _win32

        win32 = _win32.win32

        class LeftClickIcon(pystray.Icon):
            def _on_notify(self, wparam, lparam):
                if lparam == win32.WM_LBUTTONUP and self._menu_handle:
                    lparam = win32.WM_RBUTTONUP
                return super()._on_notify(wparam, lparam)

        return LeftClickIcon
    except Exception:  # pragma: no cover - non-Windows or changed internals
        return pystray.Icon


class TrayStatus:
    """What the menu should currently show. Plain data, set by the app."""

    def __init__(self) -> None:
        self.summary = "starting…"
        self.cap_line = ""
        self.walk_line = ""
        self.walking = False
        self.focusing = False
        self.focus_label = "Focus Mode"
        self.focus_enabled = True
        self.override = None          # None / "working" / "non_working"
        self.raises_left = 0
        self.starts_with_windows = False


class TrayIcon:
    """Wraps pystray so the rest of the app never imports it."""

    def __init__(self, status: TrayStatus) -> None:
        self.status = status
        self.actions: queue.Queue = queue.Queue()
        self._icon = None
        self._thread: threading.Thread | None = None
        self._available = False
        #: What the icon currently shows, so it is only redrawn on a change.
        self._drawn: bool | None = None

    # -- lifecycle ----------------------------------------------------

    def start(self) -> bool:
        """Show the icon. Returns False if the tray isn't available."""
        try:
            import pystray
        except ImportError:
            return False

        icon_class = _left_click_icon(pystray)
        self._icon = icon_class(
            "Pomodoro Guardian",
            icon=self._image(self.status.walking),
            title="Pomodoro Guardian",
            menu=self._menu(pystray),
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._available = True
        return True

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception:  # pragma: no cover - tray failure must not crash us
            self._available = False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # pragma: no cover
                pass
            self._icon = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def refresh(self) -> None:
        """Redraw icon and menu after the app changes the status."""
        if self._icon is None:
            return
        try:
            # Only when what is drawn would actually differ. This runs on
            # every one-second tick, and the icon changes only when a walk
            # starts or stops — handing Windows a new one every second is
            # both wasteful and a source of visible flicker.
            if self.status.walking != self._drawn:
                self._icon.icon = self._image(self.status.walking)
                self._drawn = self.status.walking
            self._icon.title = f"Pomodoro Guardian — {self.status.summary}"
            self._icon.update_menu()
        except Exception:  # pragma: no cover - a redraw is never critical
            pass

    # -- menu ---------------------------------------------------------

    def _menu(self, pystray):
        Item, Menu = pystray.MenuItem, pystray.Menu

        def post(action):
            # Runs on pystray's thread — post, never act.
            return lambda *_: self.actions.put(action)

        return Menu(
            Item(lambda _: self.status.summary, None, enabled=False),
            Item(lambda _: self.status.cap_line, None, enabled=False),
            Item(lambda _: self.status.walk_line, None, enabled=False),
            Menu.SEPARATOR,
            Item(
                lambda _: (
                    "Stop walking" if self.status.walking else "Start walking"
                ),
                self._walk_action(),
            ),
            Item(
                lambda _: self.status.focus_label,
                self._focus_action(),
                enabled=lambda _: self.status.focus_enabled,
            ),
            Menu.SEPARATOR,
            Item("Treat today as a day off", post(SET_DAY_OFF),
                 checked=lambda _: self.status.override == "non_working",
                 radio=True),
            Item(
                lambda _: (
                    f"Treat today as a working day "
                    f"({self.status.raises_left} left this month)"
                ),
                post(SET_WORKING_DAY),
                checked=lambda _: self.status.override == "working",
                radio=True,
                enabled=lambda _: (
                    self.status.raises_left > 0
                    or self.status.override == "working"
                ),
            ),
            Item("Use the calendar's answer", post(CLEAR_OVERRIDE),
                 checked=lambda _: self.status.override is None, radio=True),
            Menu.SEPARATOR,
            Item("History…", post(SHOW_HISTORY)),
            Item("Settings…", post(OPEN_SETTINGS)),
            Item(
                "Start with Windows", post(TOGGLE_STARTUP),
                checked=lambda _: self.status.starts_with_windows,
            ),
            Item("Quit", post(QUIT)),
        )

    def _walk_action(self):
        """One item that starts or stops, depending on the current state."""
        def clicked(*_):
            self.actions.put(STOP_WALK if self.status.walking else START_WALK)
        return clicked

    def _focus_action(self):
        def clicked(*_):
            self.actions.put(STOP_FOCUS if self.status.focusing else START_FOCUS)
        return clicked

    # -- icon ---------------------------------------------------------

    @staticmethod
    def _image(walking: bool = False):
        return render_icon(walking)
