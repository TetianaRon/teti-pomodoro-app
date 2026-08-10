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
OPEN_SETTINGS = "open_settings"
SET_DAY_OFF = "set_day_off"
SET_WORKING_DAY = "set_working_day"
CLEAR_OVERRIDE = "clear_override"
QUIT = "quit"

# Icon colours by state, so a glance at the tray says what is happening.
IDLE = (124, 136, 153)
WORKING = (143, 180, 217)
BREAK = (232, 238, 247)
WALKING = (79, 168, 112)


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
        self.override = None          # None / "working" / "non_working"
        self.raises_left = 0
        self.colour = IDLE


class TrayIcon:
    """Wraps pystray so the rest of the app never imports it."""

    def __init__(self, status: TrayStatus) -> None:
        self.status = status
        self.actions: queue.Queue = queue.Queue()
        self._icon = None
        self._thread: threading.Thread | None = None
        self._available = False

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
            icon=self._image(self.status.colour),
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
            self._icon.icon = self._image(self.status.colour)
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
            Item("Settings…", post(OPEN_SETTINGS)),
            Item("Quit", post(QUIT)),
        )

    def _walk_action(self):
        """One item that starts or stops, depending on the current state."""
        def clicked(*_):
            self.actions.put(STOP_WALK if self.status.walking else START_WALK)
        return clicked

    # -- icon ---------------------------------------------------------

    @staticmethod
    def _image(colour):
        """A plain filled circle. Recognisable at 16px, which is the point."""
        from PIL import Image, ImageDraw

        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, size - 4, size - 4), fill=colour + (255,))
        return image
