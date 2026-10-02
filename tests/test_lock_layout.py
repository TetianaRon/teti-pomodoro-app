"""The lock follows the monitor layout mid-break (reported 2026-10-02).

Unplugging the main screen during a break left the laptop showing windows
sized for a layout that no longer existed. The lock now rebuilds its windows
when the monitors change, keeping everything else about the break as it was.
"""

from __future__ import annotations

from pomodoro_guardian import overlay as overlay_module
from pomodoro_guardian.config import Config
from pomodoro_guardian.overlay import LockOverlay, SkipOffer

DOCKED = [(0, 0, 1920, 1080), (1920, 0, 1280, 800)]
LAPTOP_ONLY = [(0, 0, 1280, 800)]


class _Window:
    def __init__(self, rect):
        self.rect = rect
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class _Label:
    def __init__(self):
        self.text = ""

    def configure(self, text=None, **_kwargs):
        if text is not None:
            self.text = text


class Overlay(LockOverlay):
    """Builds stand-in windows that record the rect they were made for."""

    def __init__(self, monkeypatch, layout):
        super().__init__(root=None, config=Config(), skip_offer=SkipOffer)
        self.layout = layout
        monkeypatch.setattr(
            overlay_module, "monitor_rects", lambda _root: list(self.layout)
        )
        self.menus_built = 0
        self.notices_admitted = 0
        self._lock_args = ("Break", "12:40", False)
        self._build_windows()

    def _build_window(self, rect, heading, back_at, is_long_break):
        label = _Label()
        self._hold_captions.append(label)
        self._notices.append(label)
        return _Window(rect), _Label(), object()

    def _build_menu(self, body):
        self.menus_built += 1
        return object()

    def _admit_reminder_only(self):
        self.notices_admitted += 1


def rects(overlay):
    return [window.rect for window in overlay._windows]


def test_an_unchanged_layout_keeps_the_same_windows(monkeypatch):
    overlay = Overlay(monkeypatch, DOCKED)
    before = list(overlay._windows)

    overlay._follow_layout()

    assert overlay._windows == before


def test_unplugging_a_screen_rebuilds_for_the_one_left(monkeypatch):
    overlay = Overlay(monkeypatch, DOCKED)
    old = list(overlay._windows)

    overlay.layout = LAPTOP_ONLY
    overlay._follow_layout()

    assert all(window.destroyed for window in old)
    assert rects(overlay) == LAPTOP_ONLY
    assert len(overlay._countdowns) == 1


def test_plugging_a_screen_back_in_covers_it_too(monkeypatch):
    overlay = Overlay(monkeypatch, LAPTOP_ONLY)

    overlay.layout = DOCKED
    overlay._follow_layout()

    assert rects(overlay) == DOCKED


def test_an_open_skip_menu_is_redrawn_on_the_new_windows(monkeypatch):
    overlay = Overlay(monkeypatch, DOCKED)
    overlay._menu_open = True

    overlay.layout = LAPTOP_ONLY
    overlay._follow_layout()

    assert overlay._menu_open
    assert overlay.menus_built == 1
    assert len(overlay._menus) == 1


def test_a_reminder_only_lock_still_says_so_after_a_rebuild(monkeypatch):
    overlay = Overlay(monkeypatch, DOCKED)
    overlay._suppressing = False

    overlay.layout = LAPTOP_ONLY
    overlay._follow_layout()

    assert overlay.notices_admitted == 1


def test_an_enforcing_lock_keeps_its_suppressor_through_a_rebuild(monkeypatch):
    overlay = Overlay(monkeypatch, DOCKED)
    overlay._suppressing = True
    suppressor = object()
    overlay._suppressor = suppressor

    overlay.layout = LAPTOP_ONLY
    overlay._follow_layout()

    assert overlay._suppressor is suppressor
    assert overlay._suppressing
    assert overlay.notices_admitted == 0


# -- the "break in 2 min" banner, likewise -----------------------------


class _Pill:
    def __init__(self, _root):
        self.hidden = False

    def hide(self):
        self.hidden = True


def _banner(monkeypatch, layout):
    from pomodoro_guardian import pill
    from pomodoro_guardian.overlay import WarningBanner

    state = {"layout": layout}
    monkeypatch.setattr(
        overlay_module, "monitor_rects", lambda _root: list(state["layout"])
    )
    monkeypatch.setattr(pill, "PillWindow", _Pill)
    monkeypatch.setattr(
        pill, "work_area",
        lambda _root, r: (r[0], r[1], r[0] + r[2], r[1] + r[3] - 48),
    )
    banner = WarningBanner(root=None)
    banner._draw = lambda: None
    banner._build_windows(layout)
    return banner, state


def test_the_banner_rebuilds_when_a_screen_is_unplugged(monkeypatch):
    banner, state = _banner(monkeypatch, DOCKED)
    old = list(banner._windows)

    state["layout"] = LAPTOP_ONLY
    banner.set_text("Break in 1:59")

    assert all(window.hidden for window in old)
    assert banner._areas == [(0, 0, 1280, 752)]
    assert len(banner._windows) == 1


def test_a_standing_notice_follows_the_layout_too(monkeypatch):
    """A notice repeats the same text every tick, which `set_text` would
    otherwise skip as unchanged — and with it the layout check."""
    banner, state = _banner(monkeypatch, DOCKED)
    banner.set_text("Overtime")

    state["layout"] = LAPTOP_ONLY
    banner.set_text("Overtime")

    assert len(banner._windows) == 1


def test_an_unchanged_layout_keeps_the_banner_windows(monkeypatch):
    banner, _ = _banner(monkeypatch, DOCKED)
    before = list(banner._windows)

    banner.set_text("Break in 1:58")

    assert banner._windows == before
