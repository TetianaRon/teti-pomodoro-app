"""Tests for the skip menu's key handling (SPEC §4B).

These drive LockOverlay's queue directly rather than opening windows, so
the routing is testable without a display. The rendering still needs a
real lock; the logic does not.

The case that matters most is the one live testing caught: Escape
auto-repeats hard while held — 606 events across a single 60-second lock —
so the same hold that opens the menu would immediately close it again.
"""

from __future__ import annotations

from pomodoro_guardian.config import MINUTE, Config
from pomodoro_guardian.overlay import LockOverlay, SkipOffer, SkipOption


class Overlay(LockOverlay):
    """LockOverlay with the tkinter parts stubbed out."""

    def __init__(self, offer=None, on_skip=None):
        self.taken = []
        super().__init__(
            root=None,
            config=Config(),
            skip_offer=offer or self._default_offer,
            on_skip=on_skip or self.taken.append,
        )
        self._windows = [object()]        # non-empty: _open_menu checks it
        self._bodies = [object()]
        self.menus_built = 0

    @staticmethod
    def _default_offer():
        return SkipOffer(
            (
                SkipOption(5 * MINUTE, "5 min", True),
                SkipOption(10 * MINUTE, "10 min", True),
                SkipOption(20 * MINUTE, "20 min", False),
            ),
            remaining=15 * MINUTE,
        )

    def _build_menu(self, body):          # no tkinter
        self.menus_built += 1
        return object()

    def _close_menu(self):
        self._menus = []
        self._menu_open = False


def press(overlay, char=None, name=None):
    overlay._pending.put(("key", char, name))


def release(overlay, char=None, name=None):
    overlay._pending.put(("keyup", char, name))


def hold(overlay):
    overlay._pending.put(("hold", None, None))


def test_holding_escape_opens_the_menu():
    overlay = Overlay()
    hold(overlay)
    overlay._drain()

    assert overlay._menu_open
    assert overlay.menus_built == 1


def test_the_hold_that_opened_the_menu_does_not_close_it():
    """The live bug: 606 Escape repeats, menu opened and shut instantly."""
    overlay = Overlay()
    hold(overlay)
    for _ in range(50):          # still holding Escape down
        press(overlay, name="esc")
    overlay._drain()

    assert overlay._menu_open, "held Escape dismissed the menu it opened"


def test_escape_dismisses_once_it_has_been_released_and_pressed_again():
    overlay = Overlay()
    hold(overlay)
    for _ in range(20):
        press(overlay, name="esc")
    release(overlay, name="esc")
    press(overlay, name="esc")
    overlay._drain()

    assert not overlay._menu_open


def test_choosing_a_digit_takes_that_skip():
    overlay = Overlay()
    hold(overlay)
    press(overlay, char="2")
    overlay._drain()

    assert overlay.taken == [10 * MINUTE]
    assert not overlay._menu_open, "menu should close once a skip is chosen"


def test_a_disabled_option_cannot_be_chosen():
    """20 min is over the remaining budget in the default offer."""
    overlay = Overlay()
    hold(overlay)
    press(overlay, char="3")
    overlay._drain()

    assert overlay.taken == []
    assert overlay._menu_open, "menu should stay up after an unavailable choice"


def test_a_digit_with_no_matching_option_is_ignored():
    overlay = Overlay()
    hold(overlay)
    press(overlay, char="9")
    overlay._drain()

    assert overlay.taken == []
    assert overlay._menu_open


def test_keys_do_nothing_before_the_menu_is_open():
    overlay = Overlay()
    press(overlay, char="1")
    overlay._drain()

    assert overlay.taken == []
    assert not overlay._menu_open


def test_holding_twice_does_not_stack_menus():
    overlay = Overlay()
    hold(overlay)
    hold(overlay)
    overlay._drain()

    assert overlay.menus_built == 1


def test_the_menu_can_be_reopened_after_being_dismissed():
    overlay = Overlay()
    hold(overlay)
    release(overlay, name="esc")
    press(overlay, name="esc")
    overlay._drain()
    assert not overlay._menu_open

    hold(overlay)
    overlay._drain()

    assert overlay._menu_open
    assert overlay.menus_built == 2


class WalkingOverlay(Overlay):
    """Overlay with a walk in progress, and the media path stubbed out."""

    def __init__(self, walking=True, seconds=23 * 60):
        self.stopped = []
        super().__init__()
        self._walk_state = lambda: (walking, seconds)
        self._on_stop_walk = lambda: self.stopped.append(True)
        self._hints = []

    def _media_key(self, char, name):
        return False


def test_w_stops_the_treadmill_timer_during_a_break():
    """The prompt and tray are both behind the lock, so W is the only way.

    Without it, a walk that ended mid-break over-counts by up to the whole
    break — and over-counted walking raises the work cap.
    """
    overlay = WalkingOverlay()
    press(overlay, char="w")
    overlay._drain()

    assert overlay.stopped == [True]


def test_w_does_nothing_when_not_walking():
    overlay = WalkingOverlay(walking=False)
    press(overlay, char="w")
    overlay._drain()

    assert overlay.stopped == []


def test_the_hint_mentions_stopping_only_while_walking():
    assert "treadmill" in WalkingOverlay()._hint_text()
    assert "treadmill" not in WalkingOverlay(walking=False)._hint_text()


def test_the_hint_shows_how_long_the_walk_has_run():
    assert "23 min" in WalkingOverlay(seconds=23 * 60)._hint_text()


def test_w_is_still_available_with_the_skip_menu_open():
    overlay = WalkingOverlay()
    hold(overlay)
    press(overlay, char="w")
    overlay._drain()

    assert overlay.stopped == [True]
    assert overlay._menu_open, "stopping a walk should not dismiss the menu"


def test_ordinary_keys_leave_the_menu_alone():
    overlay = Overlay()
    hold(overlay)
    for char in "abcxyz":
        press(overlay, char=char)
    overlay._drain()

    assert overlay._menu_open
    assert overlay.taken == []
