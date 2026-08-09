"""Tests for the input-suppression watchdog.

The real listeners are never started here — doing so would suppress input
across the whole machine, including the terminal running the tests. These
exercise the watchdog itself, which is the part that has to work when
everything else has stopped.
"""

from __future__ import annotations

import threading
import time

from pomodoro_guardian.overlay import InputSuppressor


def test_the_watchdog_releases_suppression_after_the_deadline():
    suppressor = InputSuppressor(max_seconds=0.15)
    released = threading.Event()
    suppressor.stop = released.set  # type: ignore[method-assign]

    suppressor.arm_watchdog()

    assert released.wait(2.0), "suppression outlived its deadline"


def test_stopping_first_stands_the_watchdog_down():
    """A normal release must not leave a thread waiting to fire later."""
    suppressor = InputSuppressor(max_seconds=0.15)
    suppressor.arm_watchdog()
    suppressor.stop()

    calls = []
    suppressor.stop = lambda: calls.append(1)  # type: ignore[method-assign]
    time.sleep(0.35)

    assert not calls, "the watchdog fired after an ordinary stop"


def test_no_deadline_means_no_watchdog():
    suppressor = InputSuppressor(max_seconds=None)
    calls = []
    suppressor.stop = lambda: calls.append(1)  # type: ignore[method-assign]

    suppressor.arm_watchdog()
    time.sleep(0.2)

    assert not calls


class FakeKey:
    """Stands in for pynput's Key.esc without importing pynput."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, FakeKey) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


def armed(hold_seconds=0.1):
    """A suppressor wired for hold detection, with no real listeners."""
    fired = threading.Event()
    suppressor = InputSuppressor(
        on_safety_hold=fired.set, hold_seconds=hold_seconds
    )
    suppressor._escape_key = FakeKey("esc")
    return suppressor, fired


def test_holding_escape_fires_without_any_key_repeat():
    """The bug this replaced: detection used to need auto-repeat events.

    A single press followed by silence must still fire — a keyboard that
    doesn't repeat Escape would otherwise make the gesture unreachable.
    """
    suppressor, fired = armed()

    suppressor._on_press(FakeKey("esc"))   # exactly one press, no repeats

    assert fired.wait(2.0), "hold never fired from a single press"


def test_releasing_early_cancels_the_hold():
    suppressor, fired = armed(hold_seconds=0.4)

    suppressor._on_press(FakeKey("esc"))
    time.sleep(0.05)
    suppressor._on_release(FakeKey("esc"))

    assert not fired.wait(0.7), "hold fired after Escape was released"


def test_a_quick_tap_does_not_fire():
    suppressor, fired = armed(hold_seconds=0.4)

    for _ in range(5):
        suppressor._on_press(FakeKey("esc"))
        suppressor._on_release(FakeKey("esc"))

    assert not fired.wait(0.7), "tapping Escape triggered the hold"


def test_other_keys_never_fire_the_hold():
    suppressor, fired = armed()

    for name in ("a", "shift", "f4"):
        suppressor._on_press(FakeKey(name))

    assert not fired.wait(0.4)


def test_holding_again_after_a_release_still_works():
    suppressor, fired = armed(hold_seconds=0.15)
    suppressor._on_press(FakeKey("esc"))
    suppressor._on_release(FakeKey("esc"))

    suppressor._on_press(FakeKey("esc"))

    assert fired.wait(2.0), "a second hold did not fire"


def test_stopping_cancels_a_pending_hold():
    suppressor, fired = armed(hold_seconds=0.3)
    suppressor._on_press(FakeKey("esc"))

    suppressor.stop()

    assert not fired.wait(0.6), "hold fired after the lock was released"


def test_every_press_is_forwarded_to_the_key_callback():
    """The skip menu depends on seeing keys that are otherwise suppressed."""
    seen = []
    suppressor = InputSuppressor(on_key=seen.append)
    suppressor._escape_key = FakeKey("esc")

    for name in ("a", "esc", "1"):
        suppressor._on_press(FakeKey(name))

    assert [k.name for k in seen] == ["a", "esc", "1"]


def test_a_failing_key_callback_cannot_wedge_input():
    def boom(_key):
        raise RuntimeError("UI blew up")

    suppressor = InputSuppressor(on_key=boom)
    suppressor._escape_key = FakeKey("esc")

    suppressor._on_press(FakeKey("a"))   # must not raise


def test_the_watchdog_thread_does_not_keep_the_process_alive():
    """It must be a daemon, or a long break would stall shutdown."""
    suppressor = InputSuppressor(max_seconds=60.0)
    before = set(threading.enumerate())
    suppressor.arm_watchdog()
    new = set(threading.enumerate()) - before

    assert len(new) == 1
    assert new.pop().daemon, "watchdog thread must be a daemon"
    suppressor.stop()
