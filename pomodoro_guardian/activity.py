"""Global keyboard/mouse activity detection (SPEC §2.1).

Two backends, picked at runtime:

* `Win32ActivityMonitor` — asks Windows for the system-wide idle time via
  `GetLastInputInfo`. This is the right default: it's a single cheap call,
  it sees input the app never receives focus for, and it needs no hooks.
* `PynputActivityMonitor` — a fallback that watches events directly, for
  when pywin32 isn't available.

Both expose the same one-property interface the engine needs, so nothing
downstream has to know which is in use.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class ActivityMonitor(Protocol):
    """Anything that can say when input last happened."""

    @property
    def last_input_at(self) -> float:
        """Monotonic timestamp of the most recent keyboard/mouse input."""

    def start(self) -> None: ...

    def stop(self) -> None: ...


class Win32ActivityMonitor:
    """System-wide idle time via `GetLastInputInfo`.

    Windows reports idle time against `GetTickCount`, so we convert to the
    same monotonic clock everything else uses rather than mixing bases.
    """

    def __init__(self) -> None:
        import win32api  # imported here so the module stays importable off-Windows

        self._win32api = win32api
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    @property
    def last_input_at(self) -> float:
        idle_ms = (
            self._win32api.GetTickCount() - self._win32api.GetLastInputInfo()
        )
        # GetTickCount wraps roughly every 49 days; a negative reading means
        # it wrapped between the two calls, which is indistinguishable from
        # "input just happened" and harmless to treat as such.
        if idle_ms < 0:
            idle_ms = 0
        return time.monotonic() - (idle_ms / 1000.0)


class PynputActivityMonitor:
    """Fallback backend: timestamp every key and mouse event as it arrives.

    Listens without suppressing, so this never interferes with normal use.
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

    @property
    def last_input_at(self) -> float:
        return self._last


def create_monitor() -> ActivityMonitor:
    """Best available backend for this machine."""
    try:
        return Win32ActivityMonitor()
    except ImportError:
        pass
    try:
        return PynputActivityMonitor()
    except ImportError as exc:  # pragma: no cover - depends on install state
        raise RuntimeError(
            "No activity backend available — install pywin32 or pynput "
            "(see requirements.txt)."
        ) from exc
