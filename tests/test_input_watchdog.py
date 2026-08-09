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


def test_the_watchdog_thread_does_not_keep_the_process_alive():
    """It must be a daemon, or a long break would stall shutdown."""
    suppressor = InputSuppressor(max_seconds=60.0)
    before = set(threading.enumerate())
    suppressor.arm_watchdog()
    new = set(threading.enumerate()) - before

    assert len(new) == 1
    assert new.pop().daemon, "watchdog thread must be a daemon"
    suppressor.stop()
