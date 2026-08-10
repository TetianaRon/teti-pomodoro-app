"""Never-interrupt detection (SPEC §3): calls and screen sharing.

Deliberately *not* built on process names. Chasing "is Zoom running" means
an endless list that breaks whenever a new tool appears or an old one
renames its binary, and a running app says nothing about whether a call is
actually in progress.

Instead this reads the signal Windows already maintains:
**CapabilityAccessManager's ConsentStore** — the registry keys behind the
camera/microphone privacy indicator in the tray. Any app currently holding
a device has `LastUsedTimeStop = 0`. This is authoritative, cheap to read,
and works for apps that don't exist yet.

**A presenting check via `SHQueryUserNotificationState` was tried and
removed (2026-08-10).** Only `QUNS_PRESENTATION_MODE` was ever narrow
enough to trust, and that means a Vista-era Mobility Center setting the
contributor had never switched on — while the broader states held breaks
off for *any* full-screen window, this app's own lock included. In
practice screen sharing accompanies a call, so the microphone already
catches it; a scheduled presentation is caught by the calendar skip
(§4A); and presenting in person is what Focus Mode (§6) is for.

The calendar-driven meeting skip described in SPEC §4 is Phase 3 and lives
elsewhere; this module only knows about what the machine is doing now.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

CONSENT_STORE = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore"
)

class Reason(Enum):
    CAMERA = "camera in use"
    MICROPHONE = "microphone in use"
    # SPEC §4A. The calendar meeting skip is an exclusion rather than a
    # separate mechanism: §3 and §4A both mean "do not lock right now", and
    # duplicating the freezing logic would be two things to keep in step.
    MEETING = "meeting in progress"


@dataclass(frozen=True)
class Exclusion:
    """Why a break is being held off, if it is."""

    reasons: tuple[Reason, ...] = ()
    detail: str = ""

    @property
    def active(self) -> bool:
        return bool(self.reasons)

    def describe(self) -> str:
        if not self.reasons:
            return "nothing blocking a break"
        text = ", ".join(r.value for r in self.reasons)
        return f"{text} ({self.detail})" if self.detail else text


def _join(names: list[str]) -> str:
    """Readable app list for an exclusion's detail line."""
    return ", ".join(names)


class Detector(Protocol):
    def check(self) -> Exclusion: ...


class NullDetector:
    """Never excludes. Used off-Windows and by --no-exclusions."""

    def check(self) -> Exclusion:
        return Exclusion()


class CombinedDetector:
    """Union of several detectors — any one of them holds a break off."""

    def __init__(self, *detectors: Detector) -> None:
        self._detectors = [d for d in detectors if d is not None]

    def check(self) -> Exclusion:
        reasons: list[Reason] = []
        details: list[str] = []
        for detector in self._detectors:
            found = detector.check()
            for reason in found.reasons:
                if reason not in reasons:
                    reasons.append(reason)
            if found.detail:
                details.append(found.detail)
        return Exclusion(tuple(reasons), "; ".join(details))


class MeetingDetector:
    """Excludes while the calendar shows a meeting in progress (SPEC §4A).

    Unlimited and trust-based, per the spec: there is no cap on how much
    meeting time can suppress breaks.
    """

    def __init__(self, watcher) -> None:
        self._watcher = watcher

    def check(self) -> Exclusion:
        meeting = self._watcher.meeting_now()
        if meeting is None:
            return Exclusion()
        from datetime import datetime, timezone

        starts = meeting.start.astimezone()
        ends = meeting.end.astimezone()
        if datetime.now(timezone.utc) < meeting.start:
            # In the lead window rather than the meeting itself — saying
            # "until 20:00" here would misdescribe why breaks are held.
            return Exclusion((Reason.MEETING,), f"starting {starts:%H:%M}")
        return Exclusion((Reason.MEETING,), f"until {ends:%H:%M}")


class FakeDetector:
    """Scriptable detector for tests and diagnostics."""

    def __init__(self, exclusion: Exclusion | None = None) -> None:
        self.exclusion = exclusion or Exclusion()

    def set(self, *reasons: Reason, detail: str = "") -> None:
        self.exclusion = Exclusion(tuple(reasons), detail)

    def clear(self) -> None:
        self.exclusion = Exclusion()

    def check(self) -> Exclusion:
        return self.exclusion


class WindowsDetector:
    """The real thing: which apps are holding the camera or microphone."""

    def __init__(self, camera: bool = True, microphone: bool = True) -> None:
        self._camera = camera
        self._microphone = microphone

    def check(self) -> Exclusion:
        reasons: list[Reason] = []
        details: list[str] = []

        if self._camera:
            users = devices_in_use("webcam")
            if users:
                reasons.append(Reason.CAMERA)
                details.append(_join(users))
        if self._microphone:
            users = devices_in_use("microphone")
            if users:
                reasons.append(Reason.MICROPHONE)
                details.append(_join(users))
        return Exclusion(tuple(reasons), "; ".join(d for d in details if d))


# -- Windows plumbing -------------------------------------------------


def devices_in_use(store: str) -> list[str]:
    """Apps currently holding the camera or microphone.

    `store` is "webcam" or "microphone". An app that has started using the
    device but not stopped has `LastUsedTimeStop = 0`; that zero is the
    whole signal. Returns friendly names, deduplicated.
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return []

    found: list[str] = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, f"{CONSENT_STORE}\\{store}") as key:
                _collect_active(winreg, key, found)
        except OSError:
            continue  # store absent on this machine or not readable

    # Same app can appear under both hives.
    return sorted(set(found))


def _collect_active(winreg, key, found: list[str]) -> None:
    index = 0
    while True:
        try:
            name = winreg.EnumKey(key, index)
        except OSError:
            return
        index += 1
        try:
            with winreg.OpenKey(key, name) as sub:
                if name == "NonPackaged":
                    # Desktop apps live one level deeper.
                    _collect_active(winreg, sub, found)
                    continue
                try:
                    stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                except OSError:
                    continue
                if stop == 0:
                    found.append(friendly_name(name))
        except OSError:
            continue


def friendly_name(key_name: str) -> str:
    """Turn a ConsentStore key name into something readable.

    Desktop apps are stored as their full path with '#' standing in for
    the path separator; packaged apps use their package family name.
    """
    if "#" in key_name:
        return key_name.replace("#", "\\").rsplit("\\", 1)[-1]
    return key_name.split("_")[0]


def create_detector(
    camera: bool = True, microphone: bool = True
) -> Detector:
    """The right detector for this machine."""
    if not sys.platform.startswith("win"):
        return NullDetector()
    return WindowsDetector(camera, microphone)
