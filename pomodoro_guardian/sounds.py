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

EXTENSIONS = (".mp3", ".wav", ".m4a", ".wma")

#: Clips live in their own folder rather than loose among the icons, and
#: are gitignored: they are third-party stock audio, so the app discovers
#: whatever is present instead of depending on particular files.
SOUNDS_DIRNAME = "sound-effects"

NONE_LABEL = "(silent)"


def assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def sounds_dir() -> Path:
    return assets_dir() / SOUNDS_DIRNAME


def available() -> list[Path]:
    """Every playable clip in the sounds folder, sorted by name."""
    folder = sounds_dir()
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in EXTENSIONS),
        key=lambda p: p.name.lower(),
    )


def label(path: Path) -> str:
    """A readable name for a dropdown.

    Stock filenames carry an uploader prefix and a numeric id
    ("universfield-bell-ring-123742"), which is unhelpful in a menu but
    the only thing tying the file to its source — so the id is dropped for
    display while the filename itself is what gets stored.
    """
    stem = path.stem
    parts = stem.replace("_", "-").split("-")
    while parts and parts[-1].isdigit():
        parts.pop()
    return " ".join(parts) or stem


def resolve(configured: str | None, fallback_first: bool = False) -> Path | None:
    """Turn a stored setting into a file.

    Accepts a bare filename from the sounds folder or an absolute path
    elsewhere, so a clip can live outside the repo if preferred.
    """
    if configured:
        candidate = sounds_dir() / configured
        if candidate.is_file():
            return candidate
        path = Path(configured).expanduser()
        if path.is_file():
            return path
        return None
    clips = available()
    return clips[0] if (fallback_first and clips) else None


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
