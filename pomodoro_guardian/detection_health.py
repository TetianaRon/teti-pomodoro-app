"""Noticing when call detection has quietly stopped working (SPEC §3).

Every signal the app uses to see a call belongs to Windows, and any of them
can change under it. That already happened once: updates KB5124009/
KB5124010 (2026-09-22/23) stopped Windows writing the registry records the
microphone check read, and nothing said so. For ten days breaks were held
off by the calendar alone, and the first sign was the screen locking in
meetings that ran past their slot.

So nothing here tries to be update-proof. It makes a broken source *loud*,
on the same day, through three checks:

* **The two microphone sources disagree.** Over a sustained stretch, so the
  second or two between one noticing a stream and the other doesn't count.
  - Audio stack hears it, registry doesn't: the registry has gone quiet
    again. Logged once per run; no banner, because the audio stack is
    still covering every call.
  - Registry sees it, audio stack doesn't: either the audio check broke or
    an app left a stale registry entry behind. Shown on the banner — the
    primary source may be blind.
* **The audio stack can't be asked at all.** pycaw missing or COM failing.
  Banner, once.
* **Calendar meetings pass with no device seen.** Several in a row, so an
  in-person meeting or a call taken on the phone doesn't trip it. This is
  the check that catches *both* sources failing together, which the
  disagreement checks cannot. The streak survives restarts through the
  history log.

Pure logic: the app feeds it one observation per tick and acts on what it
returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exclusions import MicReadings

#: How long the sources must disagree before it counts.
DISAGREE_AFTER = 120.0

#: Calendar meetings in a row with no camera or microphone seen.
SILENT_MEETINGS_WARN = 4

#: How long a warning stays on the banner.
BANNER_SECONDS = 300.0

#: The history kind recording, per calendar meeting, whether a device was seen.
SEEN = "device seen"
SILENT = "no device seen"


@dataclass(frozen=True)
class HealthWarning:
    """Something to tell the user. `banner` says whether it needs the screen."""

    message: str
    banner: bool


class DetectionHealth:
    def __init__(self, silent_streak: int = 0) -> None:
        #: Calendar meetings in a row that passed with no device seen.
        self.silent_streak = silent_streak
        self._audio_only_since: float | None = None
        self._registry_only_since: float | None = None
        self._warned: set[str] = set()
        self._in_meeting = False
        self._device_seen_in_meeting = False
        self._banner: str | None = None
        self._banner_until = 0.0

    def observe(
        self,
        now: float,
        mic: MicReadings | None,
        meeting: bool,
        device_seen: bool,
    ) -> tuple[list[HealthWarning], str | None]:
        """One tick's readings in; new warnings and any meeting verdict out.

        `meeting` is whether the calendar is holding breaks off right now,
        `device_seen` whether the camera or microphone is. The verdict is
        SEEN or SILENT on the tick a calendar meeting ends, else None — the
        caller records it so the streak survives a restart.
        """
        warnings: list[HealthWarning] = []
        if mic is not None:
            warnings.extend(self._compare(now, mic))
        verdict = self._track_meeting(meeting, device_seen)
        if verdict == SILENT and self.silent_streak >= SILENT_MEETINGS_WARN:
            # Repeated every further silent meeting, not once: it is the
            # warning that matters most, and a restart would reset "once".
            warnings.append(HealthWarning(
                f"No call seen on the microphone or camera in the last "
                f"{self.silent_streak} calendar meetings — if you were on "
                f"calls, call detection may be broken (run --exclusions "
                f"during one)",
                banner=True,
            ))
        for warning in warnings:
            if warning.banner:
                self._banner = warning.message
                self._banner_until = now + BANNER_SECONDS
        return warnings, verdict

    def banner(self, now: float) -> str | None:
        """The warning to keep on screen, while it is still fresh."""
        if self._banner is not None and now < self._banner_until:
            return self._banner
        return None

    # -- internals ----------------------------------------------------

    def _compare(self, now: float, mic: MicReadings) -> list[HealthWarning]:
        if mic.audio is None:
            return self._once("audio-unavailable", HealthWarning(
                "Microphone check: Windows' audio system can't be asked — "
                "calls are only being seen through the registry fallback",
                banner=True,
            ))

        audio, registry = bool(mic.audio), bool(mic.registry)
        self._audio_only_since = (
            (self._audio_only_since or now) if audio and not registry else None
        )
        self._registry_only_since = (
            (self._registry_only_since or now)
            if registry and not audio else None
        )

        if self._lasted(self._audio_only_since, now):
            return self._once("registry-silent", HealthWarning(
                f"Microphone check: the audio system hears "
                f"{', '.join(mic.audio)} but Windows' privacy records "
                f"show nothing — the registry fallback has gone quiet "
                f"(the audio check is still covering calls)",
                banner=False,
            ))
        if self._lasted(self._registry_only_since, now):
            return self._once("audio-silent", HealthWarning(
                f"Microphone check: Windows' privacy records show "
                f"{', '.join(mic.registry)} on the microphone but the audio "
                f"system hears nothing — either the audio check has broken "
                f"or that app left a stale entry behind",
                banner=True,
            ))
        return []

    @staticmethod
    def _lasted(since: float | None, now: float) -> bool:
        return since is not None and now - since >= DISAGREE_AFTER

    def _once(self, key: str, warning: HealthWarning) -> list[HealthWarning]:
        if key in self._warned:
            return []
        self._warned.add(key)
        return [warning]

    def _track_meeting(self, meeting: bool, device_seen: bool) -> str | None:
        if meeting:
            if not self._in_meeting:
                self._in_meeting = True
                self._device_seen_in_meeting = False
            self._device_seen_in_meeting |= device_seen
            return None
        if not self._in_meeting:
            return None
        self._in_meeting = False
        if self._device_seen_in_meeting:
            self.silent_streak = 0
            return SEEN
        self.silent_streak += 1
        return SILENT


def streak_from(verdicts: list[str]) -> int:
    """Silent meetings in a row, from recorded verdicts newest first."""
    streak = 0
    for verdict in verdicts:
        if verdict != SILENT:
            break
        streak += 1
    return streak
