"""The countdown pill that sits in the taskbar, beside the tray icons.

**It is not really in the taskbar, because nothing can be.** The
notification area takes an icon and a hover tooltip and offers no way to
show text; the mechanism that once let a program put a control in the
taskbar (deskbands) was deprecated years ago and Windows 11 dropped
third-party toolbars altogether. So this is a small always-on-top window
positioned immediately left of the notification area — visually part of the
taskbar, architecturally a window floating over it.

Two details make it look native rather than pasted on:

* **It composites over a grab of the taskbar behind it.** Colour-keying a
  transparent background was tried first and left a magenta fringe around
  the rounded corners, because an anti-aliased edge pixel is a blend of the
  pill and the key colour and matches neither. Photographing the backdrop
  and blending onto that gives clean edges for free. The strip it covers is
  empty taskbar, so the photograph stays true until something moves.
* **Its width is fixed**, sized for the longest countdown it can show. A
  pill that resized as "25 min" became "9 min" would jitter sideways once a
  minute, and every resize costs a fresh backdrop grab.

Everything here degrades to doing nothing: off Windows, without pywin32,
without Pillow, or if the taskbar cannot be found. A missing pill is a lost
convenience, and the tray icon still carries the same number.
"""

from __future__ import annotations

import sys
import tkinter as tk

from .config import DEFAULT, Config
from .tray import PLATE, render_icon

#: Text colour on the pill. The plate colour comes from tray.PLATE, so the
#: pill and the icon badge cannot drift apart.
TEXT = (236, 240, 248)

#: Sized for this, so the pill never changes width in use. Two digits plus
#: " min" is the longest a countdown gets.
WIDEST = "88 min"


def taskbar_rects():
    """(taskbar, notification area) in screen pixels, or (None, None).

    Windows 11 no longer exposes the individual icon windows inside the
    notification area — they are XAML now — so the area as a whole is as
    precise as this can get. That is enough: the pill sits to its left,
    which reads as "next to the icons" whether or not ours is in the
    overflow.
    """
    if not sys.platform.startswith("win"):
        return None, None
    try:
        import win32gui

        bar = win32gui.FindWindow("Shell_TrayWnd", None)
        if not bar:
            return None, None
        notify = win32gui.FindWindowEx(bar, 0, "TrayNotifyWnd", None)
        return (
            win32gui.GetWindowRect(bar),
            win32gui.GetWindowRect(notify) if notify else None,
        )
    except Exception:       # pragma: no cover - depends on the live shell
        return None, None


def covered_by_fullscreen(bar) -> bool:
    """Whether something is filling the screen the taskbar is on.

    A topmost pill would otherwise float over a full-screen video or a
    presentation. Measured by rectangle rather than by asking Windows for a
    notification state: `SHQueryUserNotificationState` reports QUNS_BUSY for
    any full-screen window, this app's own lock included, and that mistake
    already cost this project once (docs/SPEC.md §3).
    """
    try:
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or hwnd == win32gui.FindWindow("Shell_TrayWnd", None):
            return False
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:       # pragma: no cover
        return False
    # The taskbar's own monitor: a window covering it edge to edge, taskbar
    # strip included, is running full screen.
    return left <= 0 and top <= 0 and right >= bar[2] and bottom >= bar[3]


class TaskbarPill:
    """A fixed-width countdown chip, parked beside the notification area."""

    def __init__(self, root: tk.Tk, config: Config = DEFAULT) -> None:
        self._root = root
        self._config = config
        self._window: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._photo = None          # kept alive; tkinter will not hold it
        self._rect: tuple | None = None
        self._drawn: tuple | None = None
        self._backdrop = None
        self._broken = False        # a failure here is never retried in a loop

    @property
    def visible(self) -> bool:
        return self._window is not None

    def update(self, text: str, tone: str = "work") -> None:
        """Show the pill with this countdown, moving or redrawing as needed."""
        if self._broken:
            return
        if not text:
            self.hide()
            return
        try:
            self._update(text, tone)
        except Exception:   # pragma: no cover - a chip must not break a break
            self._broken = True
            self.hide()

    def _update(self, text: str, tone: str) -> None:
        bar, notify = taskbar_rects()
        if bar is None or covered_by_fullscreen(bar):
            self.hide()
            return

        rect = self._geometry(bar, notify)
        if rect != self._rect:
            # A new position needs a new photograph of what is behind it,
            # and the pill must not be on screen while that is taken or it
            # would photograph itself.
            self.hide()
            self._rect = rect
            self._backdrop = self._grab(rect)
            self._drawn = None

        if (text, tone) != self._drawn:
            self._draw(text, tone)
            self._drawn = (text, tone)

    def _geometry(self, bar, notify) -> tuple:
        height = max(18, int((bar[3] - bar[1]) * 0.62))
        width = self._width(height)
        right = (notify[0] if notify else bar[2]) - 8
        return (right - width, bar[1] + (bar[3] - bar[1] - height) // 2,
                width, height)

    def _width(self, height: int) -> int:
        from PIL import Image, ImageDraw

        pad = max(3, int(height * 0.16))
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        font = self._font(height)
        left, _upper, right, _lower = probe.textbbox((0, 0), WIDEST, font=font)
        return int(pad * 3 + (right - left) + (height - pad * 2))

    def _font(self, height: int):
        from .tray import _badge_font

        font = _badge_font(max(9, int(height * 0.46)))
        if font is None:
            raise RuntimeError("no font for the taskbar pill")
        return font

    @staticmethod
    def _grab(rect):
        from PIL import ImageGrab

        x, y, width, height = rect
        return ImageGrab.grab(
            bbox=(x, y, x + width, y + height), all_screens=True
        ).convert("RGBA")

    def _draw(self, text: str, tone: str) -> None:
        from PIL import Image, ImageDraw, ImageTk

        x, y, width, height = self._rect
        scale = 4
        pad = max(3, int(height * 0.16))
        icon_px = height - pad * 2

        plate = PLATE.get(tone, PLATE["work"])
        image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [0, 0, width * scale - 1, height * scale - 1],
            radius=height * scale // 2, fill=plate + (255,),
        )

        # Centred in the space between the left edge and the tomato, not
        # left-aligned against the padding: the pill is a fixed width, so a
        # short countdown otherwise sat hard left with a visible gap before
        # the icon.
        font = self._font(height * scale)
        left, upper, right, lower = draw.textbbox((0, 0), text, font=font)
        room = (width - pad - icon_px) * scale
        draw.text(
            ((room - (right - left)) / 2 - left,
             (height * scale - (lower - upper)) / 2 - upper),
            text, font=font, fill=TEXT + (255,),
        )
        icon = render_icon(size=icon_px * scale)
        image.paste(
            icon, (int((width - pad - icon_px) * scale), pad * scale), icon
        )

        flat = image.resize((width, height), Image.LANCZOS)
        if self._backdrop is not None:
            flat = Image.alpha_composite(self._backdrop, flat)
        self._photo = ImageTk.PhotoImage(flat.convert("RGB"))

        if self._window is None:
            self._build(x, y, width, height)
        self._label.configure(image=self._photo)

    def _build(self, x: int, y: int, width: int, height: int) -> None:
        window = tk.Toplevel(self._root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.geometry(f"{width}x{height}+{x}+{y}")
        label = tk.Label(window, bd=0, highlightthickness=0)
        label.pack()
        window.update_idletasks()
        self._make_inert(window)
        self._window, self._label = window, label

    @staticmethod
    def _make_inert(window: tk.Toplevel) -> None:
        """Never take focus, swallow a click, or appear in Alt+Tab.

        A chip parked over the taskbar that ate clicks meant for the tray
        would be considerably worse than no chip at all.
        """
        try:
            import win32con
            import win32gui
        except ImportError:     # pragma: no cover - not Windows
            return
        hwnd = win32gui.GetParent(window.winfo_id()) or window.winfo_id()
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE,
            style
            | win32con.WS_EX_TRANSPARENT    # clicks pass through
            | win32con.WS_EX_NOACTIVATE     # never steals focus
            | win32con.WS_EX_TOOLWINDOW,    # stays out of Alt+Tab
        )

    def hide(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:   # pragma: no cover - teardown must not raise
                pass
        self._window = None
        self._label = None
        self._photo = None
        self._drawn = None
