"""Tests for the pill, and for the corner both users of it share.

The countdown and the two-minute warning are the same drawing in different
colours, settling in the same place — the warning replacing the countdown
when a break is due. Most of what matters here is arithmetic (where it goes,
how big it is) and the keying that gives it a silhouette; how it *looks* was
settled by putting it on screen and photographing it, which no test can do.
"""

from __future__ import annotations

import pytest

from pomodoro_guardian import pill
from pomodoro_guardian.config import Config
from pomodoro_guardian.overlay import WarningBanner
from pomodoro_guardian.pill import (
    KEY,
    TONES,
    CountdownPill,
    corner,
    covered_by_fullscreen,
    measure,
    render,
    work_area,
)

SCREEN = (0, 0, 1920, 1032)         # a work area: taskbar already excluded


# -- the drawing -------------------------------------------------------


def test_the_pill_is_keyed_out_at_its_corners():
    """The window makes one colour transparent, so the shape has to be a
    silhouette against exactly that colour — no blend survives keying."""
    image = render("5 min", "work", 30)
    assert image.getpixel((0, 0)) == (255, 0, 255)
    assert image.mode == "RGB", "an alpha channel would not survive keying"


def test_the_plate_is_the_tone_it_was_asked_for():
    image = render("5 min", "break", 30)
    # Above the type, which is vertically centred.
    assert image.getpixel((image.width // 2, 3)) == TONES["break"]


def test_no_partly_keyed_pixels_survive():
    """A blend of plate and key colour is what left a magenta fringe round
    the curves, so the alpha is thresholded before keying."""
    image = render("12 min", "long", 30)
    pixels = image.load()
    magenta_ish = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if (px := pixels[x, y]) != (255, 0, 255)
        and px[0] > 150 and px[2] > 150 and px[1] < 90
    ]
    assert not magenta_ish, f"{len(magenta_ish)} half-keyed pixels remain"


@pytest.mark.parametrize("tone", sorted(TONES))
def test_every_tone_draws_and_differs(tone):
    drawn = {t: render("9 min", t, 30).tobytes() for t in TONES}
    assert len(set(drawn.values())) == len(TONES), "two tones look identical"
    assert drawn[tone]


def test_a_wider_message_makes_a_wider_pill():
    assert measure("Break in 1:23", 30) > measure("5 min", 30)


def test_the_pill_matches_the_height_it_was_asked_for():
    for height in (24, 30, 96, 135):
        assert render("5 min", "warn", height).height == height


def test_the_same_pill_is_not_drawn_twice():
    """The warning holds at one size for over a second while its clock ticks
    once; without a cache that is the same picture forty times a second."""
    first = render("7 min", "work", 30)
    assert render("7 min", "work", 30) is first


def test_a_missing_font_still_leaves_a_pill(monkeypatch):
    """Rectangular and wrong is recoverable; nothing on screen is not."""
    monkeypatch.setattr(pill, "FONT_CANDIDATES", ())
    image = render("5 min", "work", 30)
    assert image.height == 30
    assert image.getpixel((image.width // 2, 3)) == TONES["work"]


# -- where it goes -----------------------------------------------------


def test_it_sits_in_the_bottom_right_of_the_work_area():
    x, y = corner(SCREEN, 113, 30)
    assert x + 113 < SCREEN[2] and y + 30 < SCREEN[3]
    assert SCREEN[2] - (x + 113) == SCREEN[3] - (y + 30), "unequal insets"


def test_the_work_area_excludes_the_taskbar():
    """Asked of Windows, so an auto-hiding or relocated taskbar needs no
    special case. On this machine the taskbar is 48px at the bottom."""
    area = work_area(None)
    assert area[2] > 0 and area[3] > 0


def test_a_screen_rect_falls_back_to_itself_off_windows(monkeypatch):
    monkeypatch.setattr(pill.sys, "platform", "darwin")
    assert work_area(None, (0, 0, 1280, 800)) == (0, 0, 1280, 800)


# -- the warning settles exactly where the countdown sits ---------------


def test_the_warning_ends_at_the_countdown_s_size():
    """They occupy the same corner one after the other, so a different
    height would read as two unrelated things."""
    assert WarningBanner.BIG_SCALE > 1
    settled = max(
        CountdownPill.HEIGHT,
        int(round(CountdownPill.HEIGHT
                  * (WarningBanner.BIG_SCALE + (1 - WarningBanner.BIG_SCALE)))),
    )
    assert settled == CountdownPill.HEIGHT


def test_the_warning_starts_centred_and_ends_in_the_corner():
    big = WarningBanner._place(SCREEN, 500, 135, eased=0.0)
    settled = WarningBanner._place(SCREEN, 113, 30, eased=1.0)

    assert abs(big[0] - (SCREEN[2] - 500) // 2) <= 1, "not centred at the start"
    assert settled == corner(SCREEN, 113, 30)


def test_the_travel_is_monotonic_towards_the_corner():
    places = [
        WarningBanner._place(SCREEN, 113, 30, eased=e / 10) for e in range(11)
    ]
    xs = [x for x, _y in places]
    assert xs == sorted(xs), "the warning wandered on its way to the corner"


def test_it_holds_still_before_it_travels():
    banner = WarningBanner.__new__(WarningBanner)
    banner._started_at = _clock() - 0.1
    assert banner._eased() == 0.0


def test_it_has_arrived_once_the_travel_time_has_passed():
    banner = WarningBanner.__new__(WarningBanner)
    banner._started_at = (
        _clock() - WarningBanner.HOLD_SECONDS - WarningBanner.TRAVEL_SECONDS - 1
    )
    assert banner._eased() == 1.0


def _clock() -> float:
    import time

    return time.monotonic()


# -- when the countdown gets out of the way ----------------------------


class Stub:
    """CountdownPill with the window replaced, to read its decisions."""

    def __init__(self):
        self.pill = CountdownPill(root=None)
        self.shown: list = []
        self.hidden = 0
        outer = self

        class Window:
            visible = False

            def show(self, image, x, y, alpha=1.0):
                outer.shown.append((image.width, image.height, x, y))

            def raise_above(self):
                pass

            def hide(self):
                outer.hidden += 1

        self.pill._window = Window()


def test_nothing_to_count_means_no_pill():
    stub = Stub()
    stub.pill.update("", "work")
    assert stub.shown == []
    assert stub.hidden == 1


def test_a_full_screen_window_takes_the_countdown_away(monkeypatch):
    monkeypatch.setattr(pill, "covered_by_fullscreen", lambda: True)
    stub = Stub()
    stub.pill.update("5 min", "work")
    assert stub.shown == []


def test_otherwise_it_is_drawn_in_the_corner(monkeypatch):
    monkeypatch.setattr(pill, "covered_by_fullscreen", lambda: False)
    stub = Stub()
    stub.pill.update("5 min", "work")

    assert len(stub.shown) == 1
    width, height, x, y = stub.shown[0]
    assert height == CountdownPill.HEIGHT
    area = work_area(None)
    assert (x, y) == corner(area, width, height)


# -- shell windows are not full-screen apps ----------------------------


@pytest.mark.parametrize("cls, rect", [
    ("Progman", (-1920, 0, 3840, 1200)),                    # the desktop
    ("WorkerW", (-1920, 0, 3840, 1200)),
    ("Windows.UI.Core.CoreWindow", (0, 0, 1920, 1080)),     # Win+Space, Win+.
    ("XamlExplorerHostIslandWindow", (0, 0, 1920, 1080)),   # Task View
    ("Shell_TrayWnd", (0, 0, 1920, 1080)),                  # the taskbar
])
def test_a_shell_surface_does_not_hide_the_pill(monkeypatch, cls, rect):
    """Each of these was measured firing a plain rectangle test, and each
    made the countdown vanish — on clicking the desktop, or on switching
    keyboard language."""
    _fake_foreground(monkeypatch, rect, cls)
    assert covered_by_fullscreen() is False


def test_a_real_app_going_full_screen_does_hide_it(monkeypatch):
    _fake_foreground(monkeypatch, (0, 0, 1920, 1080), "Chrome_WidgetWin_1")
    assert covered_by_fullscreen() is True


def test_a_maximised_window_is_not_full_screen(monkeypatch):
    """Measured: Windows inflates a maximised window's rect by the invisible
    resize border to (-8, -8, 1928, 1040) on a 1080p screen."""
    _fake_foreground(monkeypatch, (-8, -8, 1928, 1040), "Chrome_WidgetWin_1")
    assert covered_by_fullscreen() is False


def _fake_foreground(monkeypatch, rect, cls):
    import win32api
    import win32gui

    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 4242)
    monkeypatch.setattr(win32gui, "GetClassName", lambda _h: cls)
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda _h: rect)
    monkeypatch.setattr(win32api, "MonitorFromWindow", lambda *_a: 1)
    monkeypatch.setattr(
        win32api, "GetMonitorInfo", lambda _m: {"Monitor": (0, 0, 1920, 1080)}
    )


# -- the toggle --------------------------------------------------------


def test_the_countdown_is_on_by_default():
    assert Config().show_countdown is True


def test_the_countdown_setting_round_trips_through_a_file(tmp_path):
    from dataclasses import replace

    from pomodoro_guardian import settings as settings_module

    path = tmp_path / "config.json"
    settings_module.save(
        settings_module.Settings(config=replace(Config(), show_countdown=False)),
        path,
    )
    assert settings_module.load(path).config.show_countdown is False


def test_an_older_config_still_shows_the_countdown(tmp_path):
    from pomodoro_guardian import settings as settings_module

    path = tmp_path / "config.json"
    path.write_text('{"lock": {"safety_unlock": true}}', encoding="utf-8")
    assert settings_module.load(path).config.show_countdown is True


def test_the_key_colour_is_not_a_colour_the_pill_uses():
    """It is keyed out, so anything drawn in it would be a hole."""
    assert KEY == "#ff00ff"
    keyed = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
    assert keyed not in TONES.values()
