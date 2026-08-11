"""Tests for what the countdown says — the number, and what colour it is.

It answers "have I done 25 minutes yet?", which is otherwise hard to know —
work only accrues while you are actually typing, so a wall-clock hour of
reading counts for very little. It used to live only in the hover tooltip,
which means it was only ever read deliberately, and it matters most when
you are absorbed enough not to think of looking.

One function decides it, and `taskbar.py` renders it. It was briefly drawn
onto the tray icon as well, and that is deliberately gone: two things
showing the same number is one too many, and the plate needed to make it
legible covered most of the tomato.
"""

from __future__ import annotations

import pytest

from pomodoro_guardian.app import Application, _short, ceil_minutes
from pomodoro_guardian.config import Config
from pomodoro_guardian.state import AppState
from pomodoro_guardian.taskbar import PLATE
from pomodoro_guardian.timer import Snapshot, State
from pomodoro_guardian.tray import TrayIcon, TrayStatus, render_icon


def snap(state, remaining=0.0, cycles=0, paused=False, excluded=False,
         is_long_break=False):
    return Snapshot(
        state=state, remaining=remaining, completed_cycles=cycles,
        is_long_break=is_long_break, paused=paused, excluded=excluded,
    )


def app(focusing: bool = False, config: Config | None = None) -> Application:
    """The real _badge method without building a whole Application."""
    instance = Application.__new__(Application)
    instance.config = config or Config()
    instance._state = (
        AppState.for_today().start_focus() if focusing
        else AppState.for_today()
    )
    return instance


# -- what the number says ----------------------------------------------


def test_minutes_are_rounded_up_so_the_next_break_is_never_understated():
    assert app()._badge(snap(State.WORK, remaining=61.0))[0] == "2"


def test_it_never_shows_zero():
    """A countdown sitting on 0 for the last minute reads as stuck."""
    assert app()._badge(snap(State.WORK, remaining=0.4))[0] == "1"


def test_a_full_interval_fits_the_width_the_pill_reserves():
    """The pill is a fixed width, sized for taskbar.WIDEST, so it must."""
    from pomodoro_guardian import taskbar

    badge, _tone = app()._badge(snap(State.WORK, remaining=25 * 60))
    assert len(f"{badge} min") <= len(taskbar.WIDEST)


def test_idle_shows_no_number_at_all():
    """Nothing is counting down, so a number would be a lie."""
    assert app()._badge(snap(State.IDLE))[0] == ""


def test_focus_mode_shows_no_number():
    """Breaks are suppressed, so there is no break to count towards."""
    assert app(focusing=True)._badge(snap(State.WORK, remaining=300))[0] == ""


def test_a_break_counts_its_own_time_down():
    badge, tone = app()._badge(snap(State.BREAK, remaining=180))
    assert badge == "3"
    assert tone == "break"


# -- what the colour says ----------------------------------------------


def test_the_coming_long_break_is_a_different_colour():
    """Three cycles done, so the next break is the long one."""
    _badge, tone = app()._badge(snap(State.WARNING, remaining=90, cycles=3))
    assert tone == "long"


def test_an_ordinary_break_is_the_ordinary_colour():
    _badge, tone = app()._badge(snap(State.WORK, remaining=90, cycles=1))
    assert tone == "work"


def test_a_frozen_countdown_looks_frozen():
    """The number is still true during a call; it just isn't moving."""
    for kwargs in ({"excluded": True}, {"paused": True}):
        badge, tone = app()._badge(snap(State.WORK, remaining=300, **kwargs))
        assert badge == "5"
        assert tone == "held"


def test_every_tone_the_app_can_ask_for_has_a_colour():
    instance = app()
    asked = {
        instance._badge(s)[1]
        for s in (
            snap(State.WORK, 300), snap(State.WORK, 300, cycles=3),
            snap(State.BREAK, 300), snap(State.WORK, 300, paused=True),
        )
    }
    assert asked <= set(PLATE), f"no plate colour for {asked - set(PLATE)}"


# -- the pill and its tooltip must never contradict each other ---------


@pytest.mark.parametrize("remaining", [
    1499, 300, 121, 120, 119, 118, 90, 61, 60,
])
def test_the_pill_and_the_tooltip_agree_to_the_minute(remaining):
    """The reported bug: pill "2 min", tooltip "1 min", same instant.

    They are read together — the tooltip is what hovering the pill gives
    you — so a disagreement discredits both. It came from two roundings:
    the status line floored while the pill rounded up, which put them out
    of step for all but the exact multiples of 60.
    """
    instance = app()
    snapshot = snap(State.WORK, remaining=remaining)

    pill, _tone = instance._badge(snapshot)
    tooltip = instance._status_line(snapshot)

    assert f"{pill} min" in tooltip, (
        f"pill says {pill} min, tooltip says {tooltip!r}"
    )


@pytest.mark.parametrize("remaining", [59, 45, 1])
def test_under_a_minute_the_tooltip_is_more_precise_not_contradictory(remaining):
    """Seconds in the tooltip against "1 min" on the pill is finer detail,
    not a different answer — the pill deals only in minutes."""
    instance = app()
    snapshot = snap(State.WORK, remaining=remaining)

    assert instance._badge(snapshot)[0] == "1"
    assert "sec" in instance._status_line(snapshot)


def test_a_part_minute_is_never_rounded_away():
    """Reaching "0 min" and then running on for another 59s reads as stuck."""
    assert ceil_minutes(0.4) == 1
    assert ceil_minutes(61) == 2
    assert ceil_minutes(120) == 2


def test_hours_are_built_from_the_same_rounding():
    assert _short(3600) == "1h 00m"
    assert _short(3601) == "1h 01m"
    assert _short(4200) == "1h 10m"


# -- the icon stays a tomato -------------------------------------------


def test_the_icon_carries_no_countdown():
    """The number belongs to the pill. The icon's job is being recognised.

    Guards against the plate coming back: it took two thirds of the tomato
    to make two digits legible at 16px, to say what the pill says an inch
    to the left.
    """
    assert render_icon(size=64).tobytes() == render_icon(
        walking=False, size=64
    ).tobytes()
    with pytest.raises(TypeError):
        render_icon(size=64, badge="12")


def test_the_walking_dot_is_still_drawn():
    assert _green_pixels(render_icon(size=64, walking=True)) > 100


def test_no_walking_dot_when_not_walking():
    assert _green_pixels(render_icon(size=64)) < 100


def _green_pixels(image) -> int:
    """Pixels close to the walking dot's colour, ignoring the leaf."""
    from pomodoro_guardian.tray import WALK_BADGE

    data = image.convert("RGBA").tobytes()
    return sum(
        1
        for i in range(0, len(data), 4)
        if data[i + 3] > 128
        and all(abs(data[i + n] - WALK_BADGE[n]) < 40 for n in range(3))
    )


# -- redrawing ---------------------------------------------------------


def test_the_icon_is_not_handed_to_windows_when_nothing_changed():
    """refresh() runs every second; the icon changes only around a walk."""
    status = TrayStatus()
    tray = TrayIcon(status)
    tray._icon = _FakeIcon()

    tray.refresh()
    tray.refresh()
    tray.refresh()
    assert tray._icon.icon_sets == 1

    status.walking = True
    tray.refresh()
    assert tray._icon.icon_sets == 2


class _FakeIcon:
    def __init__(self) -> None:
        self.icon_sets = 0
        self.title = ""

    def __setattr__(self, name, value):
        if name == "icon":
            object.__setattr__(self, "icon_sets", self.icon_sets + 1)
            return
        object.__setattr__(self, name, value)

    def update_menu(self) -> None:
        pass
