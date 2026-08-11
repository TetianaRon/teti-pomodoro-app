"""Global keyboard/mouse activity detection (SPEC §2.1).

The app only needs one number: **how long since any input**. Everything
else — when a session starts, whether work accrues, when a break is due —
is derived from it in `timer.py`.

There are two ways to get that number, and the difference matters more on
macOS than it ever did on Windows:

* **Ask the OS how long it has been idle.** One cheap call, no hooks, and
  no permission — elapsed idle time is not the *content* of the input, so
  there is nothing to be trusted with. `IdleTimeMonitor` wraps any function
  that can answer this; `windows_idle_seconds` is the Windows one.
* **Watch every event as it arrives.** `PynputActivityMonitor`, kept as a
  fallback for when the first is unavailable. On macOS this needs
  Accessibility permission, which a managed machine may refuse — and if it
  is refused, an app built on this backend detects nothing at all and
  silently stops working. Which is why the idle-time route is the one a
  port should implement first (see platform.py).

Both expose the same interface, so nothing downstream knows which is in use.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from typing import Protocol


class ActivityMonitor(Protocol):
    """Anything that can say when input last happened."""

    @property
    def last_input_at(self) -> float:
        """Monotonic timestamp of the most recent keyboard/mouse input."""

    def idle_seconds(self) -> float:
        """Seconds since the most recent input."""

    def start(self) -> None: ...

    def stop(self) -> None: ...


# -- asking the OS for idle time ---------------------------------------


def windows_idle_seconds() -> float:
    """Seconds since the last input, via `GetLastInputInfo`.

    Windows reports against `GetTickCount`, which wraps roughly every 49
    days; a negative reading means it wrapped between the two calls, which
    is indistinguishable from "input just happened" and harmless to treat
    as such.
    """
    import win32api

    idle_ms = win32api.GetTickCount() - win32api.GetLastInputInfo()
    return max(0.0, idle_ms / 1000.0)


#: `HIDIdleTime = 12345678` in ioreg's output, in nanoseconds.
_HID_IDLE = re.compile(r'"?HIDIdleTime"?\s*=\s*(\d+)')


def macos_idle_seconds() -> float:
    """Seconds since the last input, via IOKit's HID idle time.

    The macOS counterpart of `GetLastInputInfo`, and the reason the app can
    track work on a Mac with **no permissions granted at all**: this reports
    how long the HID system has been quiet, never what was typed, so there
    is nothing for Accessibility to gate.

    UNVERIFIED — written without a Mac to run it on. Check the raw output
    before trusting the parse::

        ioreg -c IOHIDSystem | grep HIDIdleTime

    A faster route with no subprocess is
    `CGEventSourceSecondsSinceLastEventType(1, 0xFFFFFFFF)` through pyobjc
    or ctypes, which returns the answer directly. Worth switching to once
    this is confirmed working, since this spawns a process every tick.
    """
    output = subprocess.run(
        ["ioreg", "-c", "IOHIDSystem"],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout
    match = _HID_IDLE.search(output)
    if match is None:
        raise ValueError("no HIDIdleTime in ioreg output")
    return int(match.group(1)) / 1_000_000_000.0


class IdleTimeMonitor:
    """A monitor built on an OS idle-time reading.

    Holds no state and starts no threads: each call asks the OS afresh.
    That is also what makes it robust across sleep — there is no timestamp
    of ours to go stale while the machine was suspended.
    """

    def __init__(self, source, label: str = "") -> None:
        self._source = source
        self.label = label or getattr(source, "__name__", "idle time")

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def idle_seconds(self) -> float:
        return max(0.0, self._source())

    @property
    def last_input_at(self) -> float:
        # Converted to the same monotonic clock everything else uses, rather
        # than mixing time bases downstream.
        return time.monotonic() - self.idle_seconds()


class PynputActivityMonitor:
    """Fallback backend: timestamp every key and mouse event as it arrives.

    Listens without suppressing, so this never interferes with normal use.
    Needs Accessibility permission on macOS — see the module docstring for
    why that makes it the second choice rather than the first.
    """

    def __init__(self) -> None:
        from pynput import keyboard, mouse

        self._keyboard = keyboard
        self._mouse = mouse
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._listeners: list = []

    def start(self) -> None:
        if self._listeners:
            return
        self._listeners = [
            self._keyboard.Listener(
                on_press=self._touch, on_release=self._touch
            ),
            self._mouse.Listener(
                on_move=self._touch, on_click=self._touch, on_scroll=self._touch
            ),
        ]
        for listener in self._listeners:
            listener.daemon = True
            listener.start()

    def stop(self) -> None:
        for listener in self._listeners:
            listener.stop()
        self._listeners = []

    def _touch(self, *_args) -> None:
        with self._lock:
            self._last = time.monotonic()

    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_input_at)

    @property
    def last_input_at(self) -> float:
        with self._lock:
            return self._last


class FakeActivityMonitor:
    """Scriptable monitor for tests and `--dry-run`."""

    def __init__(self, last_input_at: float | None = None) -> None:
        self._last = (
            last_input_at if last_input_at is not None else time.monotonic()
        )

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def touch(self, when: float | None = None) -> None:
        self._last = when if when is not None else time.monotonic()

    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self._last)

    @property
    def last_input_at(self) -> float:
        return self._last


#: Idle-time sources by platform, tried before the event-watching fallback.
IDLE_SOURCES = {
    "win32": windows_idle_seconds,
    "darwin": macos_idle_seconds,
}


def create_monitor() -> ActivityMonitor:
    """Best available backend for this machine.

    Prefers an idle-time reading, and proves it works by taking one before
    committing to it — an unimplemented or broken source must fall through
    to the listener rather than leaving the app blind to all input.
    """
    source = IDLE_SOURCES.get(sys.platform)
    if source is not None:
        try:
            source()
            return IdleTimeMonitor(source)
        except Exception:
            pass    # not available here; fall through to watching events
    try:
        return PynputActivityMonitor()
    except ImportError as exc:  # pragma: no cover - depends on install state
        raise RuntimeError(
            "No activity backend available — install pywin32 or pynput "
            "(see requirements.txt)."
        ) from exc


#: Kept so existing code and any notes referring to it keep working.
def Win32ActivityMonitor() -> IdleTimeMonitor:   # noqa: N802 - was a class
    return IdleTimeMonitor(windows_idle_seconds, "GetLastInputInfo")
