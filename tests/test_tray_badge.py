"""Tests for the countdown drawn onto the tray icon.

The number answers "have I done 25 minutes yet?", which is otherwise hard
to know — work only accrues while you are actually typing, so a wall-clock
hour of reading counts for very little. It used to live only in the hover
tooltip, which means it was only ever read deliberately, and it matters most
when you are absorbed enough not to think of looking.

Windows has no way to put text beside a tray icon, so it goes on the icon.
That leaves 16 pixels, which is what most of these rules are about.
"""

from __future__ import annotations

from pomodoro_guardian.app import Application
from pomodoro_guardian.config import Config
from pomodoro_guardian.state import AppState
from pomodoro_guardian.timer import Snapshot, State
from pomodoro_guardian.tray import PLATE, TrayIcon, TrayStatus, render_icon


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


def test_a_full_interval_fits_in_two_characters():
    """Anything wider than two characters is unreadable in a 16px slot."""
    badge, _tone = app()._badge(snap(State.WORK, remaining=25 * 60))
    assert len(badge) <= 2


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


# -- the drawing itself ------------------------------------------------


def test_an_icon_is_drawn_with_and_without_a_countdown():
    plain = render_icon(size=64)
    numbered = render_icon(size=64, badge="12")

    assert plain.size == numbered.size == (64, 64)
    assert plain.tobytes() != numbered.tobytes(), "the badge changed nothing"


def test_a_longer_number_is_clipped_rather_than_shrunk_to_nothing():
    """Two characters is the legible limit; three would be a smear."""
    assert render_icon(size=64, badge="123").tobytes() == \
        render_icon(size=64, badge="12").tobytes()


def test_the_walking_dot_survives_the_countdown_plate():
    """It was buried under the plate, and "the images differ" still passed —
    the plate's rounded corner left a sliver. Count the green instead.

    Deliberately smaller while a countdown runs, since it moves into the
    strip above the plate. The bar is "still plainly a dot": ~300 pixels of
    a 64px render is about 5 of the 16 real tray pixels across.
    """
    walking_only = _green_pixels(render_icon(size=64, walking=True))
    with_badge = _green_pixels(render_icon(size=64, walking=True, badge="12"))

    assert walking_only > 100, "the walking dot is not being drawn at all"
    assert with_badge > 250, (
        f"the countdown all but hid the walking dot ({with_badge} green "
        f"pixels, was {walking_only} without it)"
    )


def test_no_walking_dot_when_not_walking():
    assert _green_pixels(render_icon(size=64, badge="12")) < 100


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


def test_each_tone_draws_differently():
    drawn = {
        tone: render_icon(size=64, badge="9", tone=tone).tobytes()
        for tone in PLATE
    }
    assert len(set(drawn.values())) == len(PLATE), "two tones look identical"


def test_a_missing_font_leaves_the_plain_tomato(monkeypatch):
    """Better no number than an unreadable one — see _badge_font."""
    monkeypatch.setattr("pomodoro_guardian.tray.FONT_CANDIDATES", ())

    assert render_icon(size=64, badge="12").tobytes() == \
        render_icon(size=64).tobytes()


# -- redrawing ---------------------------------------------------------


def test_the_icon_is_not_handed_to_windows_when_nothing_changed():
    """refresh() runs every second; the number changes once a minute."""
    status = TrayStatus()
    status.badge = "12"
    tray = TrayIcon(status)
    tray._icon = _FakeIcon()

    tray.refresh()
    tray.refresh()
    tray.refresh()
    assert tray._icon.icon_sets == 1

    status.badge = "11"
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
