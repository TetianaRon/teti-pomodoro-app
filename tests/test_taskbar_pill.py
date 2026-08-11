"""Tests for the taskbar countdown pill.

Its position comes from the live shell and its pixels from a screen grab,
so most of it can only be judged by looking (see the session worklog). What
is testable is the arithmetic that decides where it goes, when it hides,
and that a failure anywhere in it can never reach the tick.
"""

from __future__ import annotations

import pytest

from pomodoro_guardian import taskbar
from pomodoro_guardian.config import Config
from pomodoro_guardian.taskbar import (
    TaskbarPill,
    covered_by_fullscreen,
    taskbar_hidden,
)

BAR = (0, 1032, 1920, 1080)          # a real reading from a 1080p machine
NOTIFY = (1613, 1032, 1920, 1080)


def pill() -> TaskbarPill:
    return TaskbarPill(root=None, config=Config())


# -- where it goes ------------------------------------------------------


def test_it_sits_just_left_of_the_notification_area():
    x, _y, width, _height = pill()._geometry(BAR, NOTIFY)
    assert x + width <= NOTIFY[0], "the pill would cover the tray icons"
    assert x + width > NOTIFY[0] - 20, "the pill drifted away from the icons"


def test_it_is_centred_in_the_taskbar():
    _x, y, _width, height = pill()._geometry(BAR, NOTIFY)
    above = y - BAR[1]
    below = BAR[3] - (y + height)
    assert abs(above - below) <= 1
    assert height < BAR[3] - BAR[1], "the pill is taller than the taskbar"


def test_it_falls_back_to_the_far_edge_with_no_notification_area():
    x, _y, width, _height = pill()._geometry(BAR, None)
    assert x + width <= BAR[2]


def test_a_taller_taskbar_gives_a_taller_pill():
    tall = (0, 984, 1920, 1080)
    assert pill()._geometry(tall, None)[3] > pill()._geometry(BAR, None)[3]


def test_the_width_does_not_change_with_the_number_shown():
    """A pill that resized once a minute would jitter, and regrab its
    backdrop every time it did."""
    instance = pill()
    assert instance._geometry(BAR, NOTIFY) == instance._geometry(BAR, NOTIFY)
    assert instance._width(30) == instance._width(30)


def test_the_widest_countdown_still_fits_the_pill():
    """The width is sized from WIDEST, so nothing real can overflow it."""
    from PIL import Image, ImageDraw

    instance = pill()
    height = 30
    font = instance._font(height)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for text in ("1 min", "9 min", "25 min", "88 min"):
        left, _u, right, _l = probe.textbbox((0, 0), text, font=font)
        assert (right - left) <= probe.textbbox(
            (0, 0), taskbar.WIDEST, font=font
        )[2]


# -- when it hides ------------------------------------------------------


def test_a_full_screen_window_hides_the_pill(monkeypatch):
    _fake_foreground(monkeypatch, (0, 0, 1920, 1080))
    assert covered_by_fullscreen(BAR) is True


def test_an_ordinary_window_does_not(monkeypatch):
    _fake_foreground(monkeypatch, (100, 100, 1200, 800))
    assert covered_by_fullscreen(BAR) is False


def test_a_maximised_window_does_not_count_as_full_screen(monkeypatch):
    """Measured, not assumed: Windows inflates a maximised window's rect by
    the invisible resize border, so it reads (-8, -8, 1928, 1040) on a 1080p
    screen — over the edges, but stopping short of the taskbar strip."""
    _fake_foreground(monkeypatch, (-8, -8, 1928, 1040))
    assert covered_by_fullscreen(BAR) is False


# Each of these was measured firing the rectangle test on an ordinary
# desktop, and each one made the pill vanish.
@pytest.mark.parametrize("cls, rect", [
    ("Progman", (-1920, 0, 3840, 1200)),            # clicking the desktop
    ("WorkerW", (-1920, 0, 3840, 1200)),            # the desktop, other host
    ("Windows.UI.Core.CoreWindow", (0, 0, 1920, 1080)),   # Win+Space, Win+.
    ("XamlExplorerHostIslandWindow", (0, 0, 1920, 1080)),  # Task View
    ("Shell_TrayWnd", (0, 0, 1920, 1080)),          # the taskbar itself
])
def test_a_shell_surface_is_not_a_full_screen_app(monkeypatch, cls, rect):
    """The reported bug: the pill disappeared on clicking the desktop or
    switching language, because both put a screen-filling shell window in
    the foreground."""
    _fake_foreground(monkeypatch, rect, cls=cls)
    assert covered_by_fullscreen(BAR) is False


def test_a_real_app_going_full_screen_still_hides_the_pill(monkeypatch):
    """The exclusions must not defeat the check they are narrowing."""
    _fake_foreground(monkeypatch, (0, 0, 1920, 1080), cls="Chrome_WidgetWin_1")
    assert covered_by_fullscreen(BAR) is True


# -- an auto-hiding taskbar takes the pill with it ----------------------


def test_a_docked_taskbar_is_not_hidden(monkeypatch):
    _fake_monitor(monkeypatch, (0, 0, 1920, 1080))
    assert taskbar_hidden(BAR) is False


def test_a_taskbar_parked_off_screen_is_hidden(monkeypatch):
    """Auto-hide leaves a couple of pixels showing, not a whole bar."""
    _fake_monitor(monkeypatch, (0, 0, 1920, 1080))
    assert taskbar_hidden((0, 1078, 1920, 1126)) is True


def test_no_countdown_means_no_pill():
    instance = pill()
    instance.update("", "work")
    assert not instance.visible


# -- it must never reach the tick --------------------------------------


def test_a_failure_hides_the_pill_rather_than_raising(monkeypatch):
    def explode():
        raise OSError("the shell went away")

    monkeypatch.setattr(taskbar, "taskbar_rects", explode)
    instance = pill()

    instance.update("5 min", "work")          # must not raise

    assert not instance.visible


def test_a_failure_backs_off_rather_than_hammering(monkeypatch):
    calls = []

    def explode():
        calls.append(1)
        raise OSError("the shell went away")

    monkeypatch.setattr(taskbar, "taskbar_rects", explode)
    instance = pill()

    for _ in range(10):
        instance.update("5 min", "work")

    assert len(calls) == 1, "retried a dead shell call on every tick"


def test_the_backoff_expires_so_the_pill_can_come_back(monkeypatch):
    """It used to latch for good, so one transient miss — locking the
    workstation is enough — cost the pill for the rest of the day."""
    calls = []

    def explode():
        calls.append(1)
        raise OSError("locked")

    monkeypatch.setattr(taskbar, "taskbar_rects", explode)
    instance = pill()
    instance.update("5 min", "work")

    instance._retry_at = 0.0        # as if RETRY_SECONDS had passed
    instance.update("5 min", "work")

    assert len(calls) == 2, "gave up permanently instead of retrying"


def test_a_flat_backdrop_is_refused(monkeypatch):
    """A grab of a locked session comes back black; caching it would bake a
    black rectangle into the taskbar for the rest of the session."""
    from PIL import Image, ImageGrab

    monkeypatch.setattr(
        ImageGrab, "grab", lambda **_kw: Image.new("RGB", (75, 29), (0, 0, 0))
    )
    with pytest.raises(RuntimeError, match="flat"):
        TaskbarPill._grab((0, 0, 75, 29))


def test_a_real_backdrop_is_accepted(monkeypatch):
    from PIL import Image, ImageGrab

    shot = Image.new("RGB", (75, 29), (28, 34, 48))
    shot.putpixel((0, 0), (200, 200, 200))      # anything but uniform
    monkeypatch.setattr(ImageGrab, "grab", lambda **_kw: shot)

    assert TaskbarPill._grab((0, 0, 75, 29)).size == (75, 29)


def test_no_taskbar_means_no_pill(monkeypatch):
    monkeypatch.setattr(taskbar, "taskbar_rects", lambda: (None, None))
    instance = pill()

    instance.update("5 min", "work")

    assert not instance.visible


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_there_is_no_pill_off_windows(monkeypatch, platform):
    monkeypatch.setattr(taskbar.sys, "platform", platform)
    assert taskbar.taskbar_rects() == (None, None)


def _fake_foreground(monkeypatch, rect, cls="Chrome_WidgetWin_1"):
    import win32gui

    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 4242)
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda _h: rect)
    monkeypatch.setattr(win32gui, "GetClassName", lambda _h: cls)
    monkeypatch.setattr(win32gui, "FindWindow", lambda *_a: 1)


def _fake_monitor(monkeypatch, screen):
    import win32api
    import win32gui

    monkeypatch.setattr(win32gui, "FindWindow", lambda *_a: 1)
    monkeypatch.setattr(win32api, "MonitorFromWindow", lambda *_a: 1)
    monkeypatch.setattr(win32api, "GetMonitorInfo", lambda _m: {"Monitor": screen})
