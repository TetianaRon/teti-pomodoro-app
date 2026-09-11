"""Fetches the curated break-chime clips from Pixabay on request.

The clips themselves are never shipped with the app — `assets/sound-effects/`
is gitignored, and ATTRIBUTION.md explains why: the Pixabay Content License
permits free use but not redistributing the files unmodified. Downloading
them here, on the user's own machine and at their own request, sidesteps
that: nothing pixabay.com serves ever passes through this repo or anything
it publishes. Only the source URLs are checked in, same as a bookmark would
be.

The URLs are Pixabay's own stable CDN download links (not session-signed),
confirmed working with a plain unauthenticated GET.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import sounds

USER_AGENT = "PomodoroGuardian/0.1 (+local personal use)"
DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True)
class Clip:
    filename: str
    url: str
    credited_to: str
    source_page: str


# Kept in sync with ATTRIBUTION.md — five short bell/notification clips,
# free for use under the Pixabay Content License, attribution voluntary.
CURATED_CLIPS: tuple[Clip, ...] = (
    Clip(
        "bell-notification-337658.mp3",
        "https://cdn.pixabay.com/download/audio/2025/05/06/audio_2fd68b9a9a.mp3"
        "?filename=alexis_gaming_cam-bell-notification-337658.mp3",
        "ALEXIS_GAMING_CAM",
        "https://pixabay.com/sound-effects/bell-notification-337658/",
    ),
    Clip(
        "notification-bell-sound-1-376885.mp3",
        "https://cdn.pixabay.com/download/audio/2025/07/18/audio_80e4d6314a.mp3"
        "?filename=dragon-studio-notification-bell-sound-1-376885.mp3",
        "DRAGON-STUDIO",
        "https://pixabay.com/sound-effects/notification-bell-sound-1-376885/",
    ),
    Clip(
        "bell-chord1-83260.mp3",
        "https://cdn.pixabay.com/download/audio/2022/03/15/audio_f0607611f8.mp3"
        "?filename=freesound_community-bell-chord1-83260.mp3",
        "pac007, via Freesound",
        "https://pixabay.com/sound-effects/bell-chord1-83260/",
    ),
    Clip(
        "bell-sound-370341.mp3",
        "https://cdn.pixabay.com/download/audio/2025/07/05/audio_6162d4eea5.mp3"
        "?filename=freesounds123-bell-sound-370341.mp3",
        "freesounds123",
        "https://pixabay.com/sound-effects/bell-sound-370341/",
    ),
    Clip(
        "bell-ring-123742.mp3",
        "https://cdn.pixabay.com/download/audio/2022/10/23/audio_72856be61f.mp3"
        "?filename=universfield-bell-ring-123742.mp3",
        "Universfield",
        "https://pixabay.com/sound-effects/bell-ring-123742/",
    ),
)


def missing(dest_dir: Path | None = None) -> list[Clip]:
    """Curated clips not already sitting in the sounds folder."""
    folder = dest_dir or sounds.sounds_dir()
    return [c for c in CURATED_CLIPS if not (folder / c.filename).is_file()]


def download_all(
    dest_dir: Path | None = None, timeout: float = DEFAULT_TIMEOUT
) -> list[tuple[Clip, bool, str]]:
    """Fetch every curated clip not already present.

    Runs synchronously — callers on a UI thread must push this to a worker
    thread themselves, same as calendar_feed.fetch(). Returns one result
    per attempted clip; already-present clips are skipped and don't appear.
    """
    folder = dest_dir or sounds.sounds_dir()
    folder.mkdir(parents=True, exist_ok=True)
    results: list[tuple[Clip, bool, str]] = []
    for clip in missing(folder):
        try:
            request = urllib.request.Request(
                clip.url, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            results.append((clip, False, f"{exc.code} {exc.reason}"))
            continue
        except urllib.error.URLError as exc:
            results.append((clip, False, str(exc.reason)))
            continue
        except OSError as exc:  # timeouts
            results.append((clip, False, str(exc)))
            continue
        (folder / clip.filename).write_bytes(data)
        results.append((clip, True, "downloaded"))
    return results
