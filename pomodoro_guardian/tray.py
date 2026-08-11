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

#: Countdown plate colours, by what the number means.
PLATE = {
    "work": (18, 22, 28),        # minutes until the next break
    "long": (34, 62, 96),        # ...and the next one is the long break
    "break": (30, 74, 46),       # minutes left of the break itself
    "held": (72, 62, 24),        # frozen: a call, or stepped away
}
PLATE_TEXT = (255, 255, 255)

#: Bold faces to try for the countdown, most platform-native first. A tray
#: slot is 16px, so the digits have to be a bold face at a real size —
#: Pillow's built-in bitmap font cannot be scaled up without turning to
#: mush, and a blurry number is worse than none.
FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _badge_font(pixels: int):
    """The largest available bold face at `pixels`, or None.

    None means the countdown is simply left off. Falling back to the
    built-in bitmap font was tried in principle and rejected: at this size
    it renders as a smear, and an unreadable number in the tray is worse
    than the plain tomato.
    """
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, pixels)
        except OSError:
            continue
    return None


def render_icon(
    walking: bool = False,
    size: int = 64,
    badge: str = "",
    tone: str = "work",
):
    """The tomato, with a green dot while walking and an optional countdown.

    Drawn rather than loaded so the tray needs no file at runtime, and
    supersampled 4× before resizing because the real target is a 16px
    tray slot, where aliasing is the difference between a tomato and a
    smudge.

    `badge` is the minutes remaining, at most two characters. Windows has no
    way to put text *beside* a tray icon — the notification area takes a
    16px image and a hover tooltip, nothing else — so the number goes on the
    icon itself, over a plate dark enough to stay readable against the
    tomato at that size. That is how battery and CPU meters do it.
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

    # The countdown plate goes down before the walking dot, so the dot is
    # never buried under it. Drawing them the other way round hid the dot
    # almost entirely, leaving only a sliver at the plate's rounded corner —
    # which was enough to keep a "they differ" test passing while the thing
    # it was checking for was invisible.
    if badge:
        _draw_badge(draw, badge, tone, box, unit)

    if walking:
        # Ringed in the background colour so the dot stays legible against
        # the tomato at 16px. Moved clear of the plate while a countdown is
        # running, since down there it would simply not be seen.
        # Inset far enough that the ring below still fits the canvas; drawn
        # past the edge it is silently clipped to a flat side.
        spot = at(164, 14, 242, 92) if badge else at(140, 140, 246, 246)
        ring = 6 * unit
        draw.ellipse(
            [spot[0] - ring, spot[1] - ring, spot[2] + ring, spot[3] + ring],
            fill=(18, 22, 28, 255),
        )
        draw.ellipse(spot, fill=WALK_BADGE + (255,))

    return image.resize((size, size), Image.LANCZOS)


def _draw_badge(draw, badge: str, tone: str, box: int, unit: float) -> None:
    """Lay the countdown across the lower half, on its own plate.

    Across the width rather than in a corner: a corner badge at 16px leaves
    about 6px for two digits, which is unreadable. Covering the tomato's
    lower half instead leaves the leaf and shoulders visible — enough to
    still read as this app — while giving the number room to be legible,
    which is the entire point of putting it there.
    """
    badge = badge[:2]
    # Sized from the plate, not the icon: two digits need about 10 of the 16
    # tray pixels in height to be read at a glance, which is most of the
    # lower two-thirds. Measured by rendering it at 16px and looking, not
    # calculated — the first attempt at 42% was legible only when zoomed.
    font = _badge_font(int(box * 0.52))
    if font is None:
        return

    plate = PLATE.get(tone, PLATE["work"])
    # The tomato keeps its top third: leaf, stem and shoulders are enough to
    # find it by in a row of tray icons, which is the only job that part of
    # the drawing still has while a countdown is running.
    top = 96
    draw.rounded_rectangle(
        [2 * unit, top * unit, 254 * unit, 252 * unit],
        radius=26 * unit, fill=plate + (255,),
    )

    # Centred on the ink's own bounding box rather than on the font's line
    # metrics: digits carry ascender and descender space they never use, and
    # centring on those left the number visibly high in the plate.
    left, upper, right, lower = draw.textbbox((0, 0), badge, font=font)
    x = (box - (right - left)) / 2 - left
    y = ((top + 252) / 2) * unit - (lower - upper) / 2 - upper
    draw.text((x, y), badge, font=font, fill=PLATE_TEXT + (255,))


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
        # The countdown drawn onto the icon: at most two characters, and ""
        # for none. See render_icon for why it is on the icon rather than
        # beside it.
        self.badge = ""
        self.badge_tone = "work"


class TrayIcon:
    """Wraps pystray so the rest of the app never imports it."""

    def __init__(self, status: TrayStatus) -> None:
        self.status = status
        self.actions: queue.Queue = queue.Queue()
        self._icon = None
        self._thread: threading.Thread | None = None
        self._available = False
        #: What the icon currently shows, so it is only redrawn on a change.
        self._drawn: tuple | None = None

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
            # every one-second tick, and the countdown changes once a
            # minute — handing Windows a new icon 59 times for nothing is
            # both wasteful and a source of visible flicker.
            look = (self.status.walking, self.status.badge,
                    self.status.badge_tone)
            if look != self._drawn:
                self._icon.icon = self._image(*look)
                self._drawn = look
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
    def _image(walking: bool = False, badge: str = "", tone: str = "work"):
        return render_icon(walking, badge=badge, tone=tone)
