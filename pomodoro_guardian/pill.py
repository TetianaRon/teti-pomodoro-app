"""The rounded pill: one drawing, used by the countdown and the warning.

Both the standing countdown ("5 min") and the two-minute warning are the
same object — a rounded plate, the time, and the tomato — differing only in
colour, size and where they sit. Sharing the drawing is not tidiness: they
appear in the same corner, one replacing the other, so any difference in
shape or weight would read as two unrelated things fighting for the spot.

**Why it is keyed rather than composited.** An earlier version lived inside
the taskbar and blended onto a photograph of the strip behind it, which was
possible only because that strip never changed. Over ordinary application
windows there is nothing stable to photograph, so the window declares one
colour transparent and Windows keys it out. That has a consequence worth
knowing: keying compares colours exactly, and an anti-aliased edge pixel is
a blend of pill and key colour that matches neither — it survives as a
fringe. So the alpha is **thresholded to binary** before keying, giving a
hard edge, and a slightly darker ring is drawn just inside it to soften the
staircase that leaves on the curves.

Everything degrades: without a truetype face, or off Windows where there is
no `-transparentcolor`, the pill falls back to a plain rectangle rather than
disappearing.
"""

from __future__ import annotations

import sys
import tkinter as tk

from .config import DEFAULT, Config

#: Plate colours, by what the time means.
TONES = {
    "work": (18, 22, 28),        # minutes until the next break
    "long": (34, 62, 96),        # ...and the next one is the long break
    "break": (30, 74, 46),       # minutes left of the break itself
    "held": (72, 62, 24),        # frozen: a call, or stepped away
    # The two-minute warning. Amber, but *dark* amber: a bright plate was
    # tried first and the tomato — being red — vanished into it at the size
    # the warning arrives at. What makes this one noticed is the attention
    # pass, not the loudness of the fill.
    "warn": (96, 50, 8),
}
TEXT = {
    "warn": (255, 214, 170),
}
DEFAULT_TEXT = (236, 240, 248)

#: Keyed out to leave the pill's silhouette. Nothing else may use it.
KEY = "#ff00ff"

#: Bold faces to try, most platform-native first. Pillow's built-in bitmap
#: font cannot be scaled without turning to mush, and a blurry countdown is
#: worse than a rectangular one.
FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

#: Supersample factor for the plate and the type. Applied to those only:
#: `render_icon` already supersamples internally, so drawing the tomato
#: scaled up as well made a 135px pill cost 52ms — twice the frame budget of
#: the warning's animation, on the thread that also runs the tick. It is
#: pasted at its final size after the plate comes back down instead.
SCALE = 4

#: Alpha at or above this survives the threshold. Halfway is the neutral
#: choice: lower fattens the pill, higher gnaws at the curves.
ALPHA_CUTOFF = 128


def font_for(pixels: int):
    """The first available bold face at `pixels`, or None."""
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, max(6, int(pixels)))
        except OSError:
            continue
    return None


class Layout:
    """Where everything sits inside a pill of a given height.

    One place, used by both `measure` and `render`, because they have to
    agree exactly: if the width is computed from one set of insets and the
    contents drawn with another, the text either overflows the plate or
    floats away from it.
    """

    def __init__(self, text: str, height: int) -> None:
        self.height = height = max(12, int(height))
        self.font = font_for(height * 0.46)
        # The tomato's inset. It is a circle, so it nests into the rounded
        # cap and needs no more room than this.
        self.pad = max(3, int(height * 0.16))
        self.icon = max(6, height - self.pad * 2)
        # The type's inset, deliberately larger. Letters have flat vertical
        # strokes and the cap's radius is half the pill's height, so at the
        # tomato's inset a "5" or a "B" ran straight into the curve. An
        # optical adjustment, not a symmetrical one — matching the numbers
        # is what looked wrong.
        self.text_left = max(5, int(height * 0.38))
        # And its own measure between the type and the tomato. At the plain
        # inset a long message ("Break in 0:58") ran straight into the fruit.
        self.gap = max(4, int(height * 0.20))
        self.text_width = self._measure(text)
        self.width = (
            self.text_left + self.text_width + self.gap + self.icon + self.pad
        )

    def _measure(self, text: str) -> int:
        from PIL import Image, ImageDraw

        if self.font is None:
            return int(self.height * 2.0)
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        left, _top, right, _bottom = probe.textbbox((0, 0), text, font=self.font)
        return right - left

    @property
    def icon_left(self) -> int:
        return self.width - self.pad - self.icon


def measure(text: str, height: int) -> int:
    """How wide the pill needs to be for `text` at `height`."""
    return Layout(text, height).width


#: Last few pills drawn, keyed on everything that changes their pixels. The
#: warning holds at one size for over a second while its clock ticks once,
#: so without this the same picture is drawn forty times a second.
_CACHE: dict[tuple, object] = {}
_CACHE_LIMIT = 24


def render(text: str, tone: str, height: int, width: int | None = None):
    key = (text, tone, int(height), width)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    image = _render(text, tone, height, width)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()      # a whole clear rather than an eviction policy:
                            # the sizes change together, so the old ones go
                            # stale together too
    _CACHE[key] = image
    return image


def _render(text: str, tone: str, height: int, width: int | None = None):
    """The pill as an RGB image, with everything outside it set to `KEY`.

    Returned flat rather than with an alpha channel: the window keys one
    colour out, so what it needs is a picture in which every pixel is
    either pill or exactly key.
    """
    from PIL import Image, ImageDraw

    from .tray import render_icon

    layout = Layout(text, height)
    height = layout.height
    width = int(width or layout.width)
    pad = layout.pad
    icon_px = layout.icon
    plate = TONES.get(tone, TONES["work"])
    ink = TEXT.get(tone, DEFAULT_TEXT)

    box = (width * SCALE, height * SCALE)
    image = Image.new("RGBA", box, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = height * SCALE // 2
    draw.rounded_rectangle(
        [0, 0, box[0] - 1, box[1] - 1], radius=radius, fill=plate + (255,)
    )
    # A darker ring just inside the edge. Once the alpha is thresholded the
    # outline is a staircase on the curves, and a mid-tone against the plate
    # is what stops the eye reading the steps.
    draw.rounded_rectangle(
        [0, 0, box[0] - 1, box[1] - 1], radius=radius, outline=tuple(
            max(0, channel - 26) for channel in plate
        ) + (255,), width=max(1, SCALE),
    )

    font = font_for(height * 0.46 * SCALE)
    if font is not None:
        # Left-aligned at the text inset rather than centred in the space
        # beside the tomato: the pill is sized from this text, so the inset
        # *is* the padding and centring can only give it away again.
        left, top, _right, bottom = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (layout.text_left * SCALE - left,
             (box[1] - (bottom - top)) / 2 - top),
            text, font=font, fill=ink + (255,),
        )

    flat = image.resize((width, height), Image.LANCZOS)
    icon = render_icon(size=icon_px)
    flat.paste(icon, (layout.icon_left, pad), icon)
    return _key_out(flat)


def _key_out(image):
    """Threshold the alpha and lay the result on the key colour.

    Every pixel ends up either fully pill or exactly `KEY`, which is what
    makes `-transparentcolor` produce a clean silhouette instead of a
    magenta fringe around the curves.
    """
    from PIL import Image

    alpha = image.getchannel("A").point(
        lambda value: 255 if value >= ALPHA_CUTOFF else 0
    )
    canvas = Image.new("RGB", image.size, KEY)
    canvas.paste(image.convert("RGB"), (0, 0), alpha)
    return canvas


class PillWindow:
    """A borderless, click-through, always-on-top pill.

    Takes no focus and swallows no clicks: it sits over whatever is being
    worked on, so anything else would make it an obstacle rather than a
    signal.
    """

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._window: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._photo = None          # tkinter will not keep a reference
        self._geometry: tuple | None = None
        self._alpha: float | None = None

    @property
    def visible(self) -> bool:
        return self._window is not None

    #: Opaque at rest. Partial alpha was tried and the window behind showed
    #: straight through the plate, which read as grime rather than as
    #: translucency and cost the countdown its legibility. Fading is for
    #: getting out of the way of the cursor, not for decoration.
    RESTING_ALPHA = 1.0

    @property
    def rect(self) -> tuple | None:
        """(left, top, width, height), or None if not on screen."""
        if self._geometry is None:
            return None
        width, height, x, y = self._geometry
        return (x, y, width, height)

    def show(self, image, x: int, y: int, alpha: float = RESTING_ALPHA) -> None:
        """Put `image` on screen at (x, y), creating the window if needed."""
        from PIL import ImageTk

        if self._window is None:
            self._build(alpha)
        # Applied on every call, not only at build. It used to be honoured
        # once, so a caller that recomputed the alpha per frame — the
        # warning does — silently got whatever the first frame had.
        self.set_alpha(alpha)
        self._photo = ImageTk.PhotoImage(image)
        self._label.configure(image=self._photo)
        # Recorded whether or not tk is asked, because `raise_above` asserts
        # this rectangle through win32 every tick and that is what actually
        # holds the window in place.
        self._geometry = (image.width, image.height, int(x), int(y))
        self._window.geometry(
            f"{image.width}x{image.height}+{int(x)}+{int(y)}"
        )

    def raise_above(self) -> None:
        """Retake the top of the z-order, and the position, without focus.

        Both every tick rather than once, for two different reasons.

        The z-order, because the shell puts its own windows above ours
        whenever the taskbar is clicked or Start is opened, and a signal that
        quietly ends up underneath is not a signal.

        The position, because **tkinter would not keep it.** Setting
        `geometry()` on a mapped `overrideredirect` window reports back
        correctly and then reverts to the window's first position once idle
        processing runs — measured while the warning animated: it asked for
        +1795+990, tk agreed, and the window stayed at the +691+448 it had
        arrived at. The result was a warning that shrank to nothing in the
        middle of the screen instead of settling in the corner. Asserting the
        rectangle here makes tk's opinion irrelevant.
        """
        if self._window is None or self._geometry is None:
            return
        try:
            import win32con
            import win32gui

            hwnd = (win32gui.GetParent(self._window.winfo_id())
                    or self._window.winfo_id())
            width, height, x, y = self._geometry
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, int(x), int(y), width, height,
                win32con.SWP_NOACTIVATE,
            )
        except ImportError:     # pragma: no cover - not Windows
            pass

    def set_alpha(self, alpha: float) -> None:
        if self._window is None or alpha == self._alpha:
            return
        try:
            self._window.attributes("-alpha", alpha)
            self._alpha = alpha
        except tk.TclError:     # pragma: no cover
            pass

    def hide(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:   # pragma: no cover - teardown must not raise
                pass
        self._window = None
        self._label = None
        self._photo = None
        self._geometry = None
        self._alpha = None

    # -- internals ----------------------------------------------------

    def _build(self, alpha: float) -> None:
        window = tk.Toplevel(self._root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=KEY)
        try:
            # Before -transparentcolor: setting alpha is what makes the
            # window layered, which the key depends on.
            window.attributes("-alpha", alpha)
            window.attributes("-transparentcolor", KEY)
            self._alpha = alpha
        except tk.TclError:     # pragma: no cover - not Windows
            pass    # a rectangular pill, rather than none
        label = tk.Label(window, bd=0, highlightthickness=0, bg=KEY)
        label.pack()
        window.update_idletasks()
        self._make_inert(window)
        self._window, self._label = window, label

    @staticmethod
    def _make_inert(window: tk.Toplevel) -> None:
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


def work_area(root: tk.Tk, rect: tuple | None = None) -> tuple:
    """The screen area a pill may sit in, excluding the taskbar.

    Asked of Windows rather than measured, so an auto-hiding taskbar, a
    taskbar moved to another edge and a second monitor all come out right
    without a special case for any of them.
    """
    if sys.platform.startswith("win"):
        try:
            import win32api
            import win32con

            point = (rect[0] + 1, rect[1] + 1) if rect else (1, 1)
            monitor = win32api.MonitorFromPoint(
                point, win32con.MONITOR_DEFAULTTOPRIMARY
            )
            return win32api.GetMonitorInfo(monitor)["Work"]
        except Exception:       # pragma: no cover - depends on the shell
            pass
    if rect is not None:
        left, top, width, height = rect
        return (left, top, left + width, top + height)
    return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())


def corner(area: tuple, width: int, height: int, margin: int = 12) -> tuple:
    """Bottom-right of `area`, inset by `margin`."""
    return (area[2] - width - margin, area[3] - height - margin)


def covered_by_fullscreen() -> bool:
    """Whether a real window is filling the primary screen.

    A topmost pill would otherwise sit over a full-screen video or a
    presentation. Judged by rectangle rather than by asking Windows for a
    notification state, which reports busy for *any* full-screen window
    including this app's own lock — a mistake that already cost this project
    once (docs/SPEC.md §3).

    A rectangle is not sufficient on its own either: the desktop
    (`Progman`, measured spanning every monitor) and the input-experience
    host (`Windows.UI.Core.CoreWindow`, exactly screen-sized) both satisfy
    it, so clicking the desktop or switching keyboard language used to make
    the countdown vanish. Shell surfaces are excluded by class.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import win32api
        import win32con
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or win32gui.GetClassName(hwnd) in SHELL_CLASSES:
            return False
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        screen = win32api.GetMonitorInfo(
            win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTOPRIMARY)
        )["Monitor"]
    except Exception:       # pragma: no cover - depends on the live shell
        return False
    return (left <= screen[0] and top <= screen[1]
            and right >= screen[2] and bottom >= screen[3])


#: Shell surfaces that report screen-filling bounds without being anything
#: the pill should get out of the way of. Every one was measured firing the
#: rectangle test on an ordinary desktop.
SHELL_CLASSES = frozenset({
    "Progman",                      # the desktop; Win+D or a click on it
    "WorkerW",                      # the desktop, other host
    "Shell_TrayWnd",                # the taskbar
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",   # IME, emoji picker, touch keyboard
    "XamlExplorerHostIslandWindow",  # Task View, Alt+Tab, snap layouts
    "MultitaskingViewFrame",
    "ForegroundStaging",
})


class CountdownPill:
    """The standing "5 min" pill, bottom-right above the taskbar.

    It used to live *inside* the taskbar, beside the tray icons, which
    Windows offers no way to do — so it was a floating window anchored to
    `TrayNotifyWnd` and composited over a photograph of the strip behind it.
    That bought three separate bugs (shell windows read as full-screen, the
    shell taking its z-order, screen grabs failing on a locked session) for
    a position nobody had asked to be exact. Sitting plainly above the
    taskbar instead needs no photograph, no anchoring, and no guessing where
    a tray icon went.

    Shares its drawing, its corner and its window class with the two-minute
    warning, which replaces it in the same spot when a break is due.
    """

    HEIGHT = 30

    #: Fade a little before the cursor arrives rather than exactly on it. The
    #: pill is 30px in a corner, so "on it" is a small target, and starting
    #: to clear as you reach feels like getting out of the way instead of
    #: reacting to being touched.
    HOVER_MARGIN = 10

    def __init__(self, root: tk.Tk, config: Config = DEFAULT) -> None:
        self._root = root
        self._config = config
        self._window = PillWindow(root)
        self._shown: tuple | None = None
        self._hovering = False
        self._poll_job: str | None = None

    @property
    def visible(self) -> bool:
        return self._window.visible

    def update(self, text: str, tone: str = "work") -> None:
        """Show this countdown, or hide the pill if there is nothing to show."""
        if not text or covered_by_fullscreen():
            self.hide()
            return
        try:
            image = render(text, tone, self.HEIGHT)
            area = work_area(self._root)
            x, y = corner(area, image.width, image.height)
            self._window.show(image, x, y, alpha=self._alpha())
            # Every tick, not only on a change: the shell puts its own
            # windows above ours whenever the taskbar is clicked.
            self._window.raise_above()
            self._shown = (text, tone)
            self._schedule_poll()
        except Exception:   # pragma: no cover - a chip must not break a break
            self.hide()

    def hide(self) -> None:
        self._cancel_poll()
        self._window.hide()
        self._shown = None
        self._hovering = False

    # -- getting out of the way ----------------------------------------
    #
    # Clicks already pass through — the window carries WS_EX_TRANSPARENT, so
    # Windows' own hit test at its centre returns whatever is underneath. The
    # pill can still *hide* something you are reaching for, though, which is
    # what fading is for. Polled rather than bound to <Enter>/<Leave>,
    # because a click-through window receives no mouse events at all: the
    # same reason the warning has always polled.

    def _alpha(self) -> float:
        return (
            self._config.banner_alpha_hover if self._hovering
            else self._config.banner_alpha
        )

    def _schedule_poll(self) -> None:
        """Poll on its own clock, not the app's one-second tick.

        Waiting up to a second to clear out of the way would be worse than
        not moving at all.
        """
        if self._poll_job is not None or self._root is None:
            return
        self._poll_job = self._root.after(
            int(self._config.banner_hover_poll * 1000), self._poll_hover
        )

    def _cancel_poll(self) -> None:
        if self._poll_job is None:
            return
        try:
            self._root.after_cancel(self._poll_job)
        except Exception:   # pragma: no cover - a dead job is fine
            pass
        self._poll_job = None

    def _poll_hover(self) -> None:
        self._poll_job = None
        if not self.visible:
            return
        try:
            over = self.near_cursor(self._root.winfo_pointerxy())
        except tk.TclError:     # pragma: no cover - teardown race
            return
        if over != self._hovering:
            self._hovering = over
            self._window.set_alpha(self._alpha())
        self._schedule_poll()

    def near_cursor(self, cursor: tuple) -> bool:
        """Whether the cursor is on or just outside the pill."""
        rect = self._window.rect
        if rect is None:
            return False
        x, y = cursor
        left, top, width, height = rect
        margin = self.HOVER_MARGIN
        return (left - margin <= x < left + width + margin
                and top - margin <= y < top + height + margin)
