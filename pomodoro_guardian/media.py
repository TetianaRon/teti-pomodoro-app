"""Pausing media when the lock appears.

A break locks the screen and swallows every keystroke, so a video carries
on playing behind the overlay — audible, invisible, and impossible to
stop until the break ends. The lock therefore pauses whatever is playing
as it goes up.

**Why a window message rather than a media key.** The lock's input
suppression is all-or-nothing: pynput decides suppression synchronously
inside its hook while dispatching callbacks asynchronously, so a callback
cannot spare individual events. Any key this module sent would be
swallowed by the app's own hook. `WM_APPCOMMAND` is a window message, so
it never touches the keyboard at all.

**Why pause and not play/pause.** `APPCOMMAND_MEDIA_PAUSE` is idempotent:
already-paused media stays paused, and silence stays silent. The
play/pause toggle would have started music on any break where nothing was
playing, and delivering it twice — as an earlier version did, once to the
foreground window and once broadcast — cancels itself out.
"""

from __future__ import annotations

import sys

WM_APPCOMMAND = 0x0319
HWND_BROADCAST = 0xFFFF

#: "Pause. If already paused, take no action." Unlike PLAY_PAUSE (14),
#: this cannot start playback, so it is safe to broadcast widely.
APPCOMMAND_MEDIA_PAUSE = 47


def foreground_window() -> int | None:
    """The window in front right now — captured before the lock covers it."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        return ctypes.windll.user32.GetForegroundWindow() or None
    except (OSError, AttributeError):  # pragma: no cover
        return None


def pause(target: int | None = None) -> bool:
    """Ask everything playing to pause. Returns False only if unsupported.

    Sent to the remembered foreground window *and* broadcast, because the
    app making the noise may not be the one in front. Both deliveries are
    safe precisely because pause is idempotent.

    PostMessage rather than SendMessage: a broadcast that waited for every
    top-level window to answer would stall the UI thread if any of them
    were busy, and the lock's countdown has to keep ticking.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        post = ctypes.windll.user32.PostMessageW
        lparam = APPCOMMAND_MEDIA_PAUSE << 16
        if target:
            post(target, WM_APPCOMMAND, target, lparam)
        post(HWND_BROADCAST, WM_APPCOMMAND, 0, lparam)
        return True
    except (OSError, AttributeError):  # pragma: no cover
        return False
