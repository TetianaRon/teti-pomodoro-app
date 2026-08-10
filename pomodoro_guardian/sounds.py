"""Chimes for the start and end of a break.

Plays through Windows' own MCI interface via ctypes rather than adding an
audio library. MCI handles mp3 as well as wav, is present on every
Windows install, and costs nothing at import — which matters for a
background app that is silent most of the time.

Playback happens on a daemon thread. MCI's `wait` blocks until the clip
finishes, and doing that on the UI thread would freeze the countdown
mid-chime; the thread also gives somewhere to close the handle, which MCI
leaks if nobody does.

Missing or unplayable files are silently ignored. A chime is a courtesy,
and there is no version of "the sound file moved" that should stop a
break being enforced.
"""

from __future__ import annotations

import itertools
import sys
import threading
from pathlib import Path

_alias = itertools.count(1)

#: Filenames looked for in assets/ when no explicit path is configured, so
#: dropping a file in is all that's needed.
DEFAULT_START = "break-start"
DEFAULT_END = "break-end"
EXTENSIONS = (".mp3", ".wav", ".m4a", ".wma")


def assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def find_default(stem: str) -> Path | None:
    """The first assets/<stem>.<ext> that exists, or None."""
    for extension in EXTENSIONS:
        candidate = assets_dir() / f"{stem}{extension}"
        if candidate.is_file():
            return candidate
    return None


def resolve(configured: str | None, stem: str) -> Path | None:
    """A configured path if it exists, else the bundled default, else None."""
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return path
    return find_default(stem)


def play(path: Path | str | None) -> bool:
    """Start a clip. Returns False if there is nothing playable."""
    if not path:
        return False
    target = Path(path)
    if not target.is_file() or not sys.platform.startswith("win"):
        return False
    threading.Thread(target=_play_blocking, args=(target,), daemon=True).start()
    return True


def _play_blocking(path: Path) -> None:
    try:
        import ctypes

        mci = ctypes.windll.winmm.mciSendStringW
        alias = f"pomo{next(_alias)}"
        # "type mpegvideo" is MCI's name for the MPEG decoder, which covers
        # mp3 despite the name. Quoted because paths contain spaces.
        if mci(f'open "{path}" alias {alias}', None, 0, None) != 0:
            return
        try:
            mci(f"play {alias} wait", None, 0, None)
        finally:
            mci(f"close {alias}", None, 0, None)
    except Exception:  # pragma: no cover - a chime must never raise
        pass
