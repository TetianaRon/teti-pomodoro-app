"""Never-interrupt detection (SPEC §3): calls and screen sharing.

Deliberately *not* built on process names. Chasing "is Zoom running" means
an endless list that breaks whenever a new tool appears or an old one
renames its binary, and a running app says nothing about whether a call is
actually in progress.

Instead it asks Windows which apps are holding a device, two ways:

* **The audio stack (primary for the microphone, added 2026-10-02).** Every
  active capture endpoint is asked for its audio sessions through WASAPI's
  `IAudioSessionManager2` — the public, documented interface behind the
  Volume Mixer and Settings' "apps using your microphone". It has been
  stable since Vista and every audio app depends on it, which is the whole
  point: it cannot be changed quietly. It has no camera equivalent.
* **CapabilityAccessManager's ConsentStore (fallback, and the only camera
  signal)** — the registry keys behind the privacy indicator in the tray.
  An app holding a device has `LastUsedTimeStop = 0`. This was the only
  source until 2026-10-02, and it is *not* a public interface: Windows
  updates KB5124009/KB5124010 (2026-09-22/23) stopped writing it at all on
  the contributor's laptop, for every capability, and for ten days every
  call went undetected — breaks were held off by the calendar alone, so
  any meeting that overran its slot got locked.

Either source seeing the microphone is enough. Because any one of them can
break again without warning, `detection_health.py` watches the two for
disagreement and for going silent through calendar meetings.

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

import os
import sys
import threading
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


@dataclass(frozen=True)
class MicReadings:
    """What each microphone source said on one check.

    Kept per source, not merged, so `detection_health` can notice one of
    them going quiet while the other still works. `audio` is None when the
    audio stack could not be asked at all — distinct from "nobody".
    """

    audio: tuple[str, ...] | None = None
    registry: tuple[str, ...] = ()


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
        #: The last check's per-source microphone readings, or None when the
        #: microphone isn't being watched. Read by the detection health check.
        self.last_mic: MicReadings | None = None

    def check(self) -> Exclusion:
        reasons: list[Reason] = []
        details: list[str] = []

        if self._camera:
            users = devices_in_use("webcam")
            if users:
                reasons.append(Reason.CAMERA)
                details.append(_join(users))
        if self._microphone:
            audio = capture_sessions_active()
            registry = devices_in_use("microphone")
            self.last_mic = MicReadings(
                None if audio is None else tuple(audio), tuple(registry)
            )
            users = sorted(set(audio or []) | set(registry))
            if users:
                reasons.append(Reason.MICROPHONE)
                details.append(_join(users))
        return Exclusion(tuple(reasons), "; ".join(d for d in details if d))


# -- Windows plumbing -------------------------------------------------

# WASAPI values (audiosessiontypes.h, mmdeviceapi.h).
_E_CAPTURE = 1
_DEVICE_STATE_ACTIVE = 0x1
_SESSION_ACTIVE = 1

_com_ready = threading.local()


def capture_sessions_active() -> list[str] | None:
    """Apps with an active audio-capture session on any microphone.

    Asks every active capture endpoint — built-in, USB and Bluetooth alike
    — for its sessions, and keeps those in the active state. A call that
    is muted but still holds its stream open (Meet does) stays active; one
    whose app tears the stream down on mute would not.

    Returns process names, deduplicated, never including this app. Returns
    None when the audio stack can't be asked (pycaw missing, COM failing),
    so a broken source reads as broken rather than as silence.
    """
    try:
        import comtypes
        from pycaw.api.audiopolicy import (
            IAudioSessionControl2,
            IAudioSessionManager2,
        )
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
    except ImportError:  # pragma: no cover - non-Windows or not installed
        return None

    try:
        if not getattr(_com_ready, "done", False):
            # Once per thread: the tick loop is one thread, but `--exclusions`
            # and the tests may call this from others.
            comtypes.CoInitialize()
            _com_ready.done = True
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            comtypes.CLSCTX_INPROC_SERVER,
        )
        endpoints = enumerator.EnumAudioEndpoints(
            _E_CAPTURE, _DEVICE_STATE_ACTIVE
        )
    except Exception:  # pragma: no cover - COM unavailable
        return None

    own_pid = os.getpid()
    pids: set[int] = set()
    for index in range(endpoints.GetCount()):
        try:
            manager = endpoints.Item(index).Activate(
                IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None
            ).QueryInterface(IAudioSessionManager2)
            sessions = manager.GetSessionEnumerator()
            for slot in range(sessions.GetCount()):
                control = sessions.GetSession(slot).QueryInterface(
                    IAudioSessionControl2
                )
                if control.GetState() != _SESSION_ACTIVE:
                    continue
                pid = control.GetProcessId()
                # Pid 0 is the system's idle placeholder session.
                if pid and pid != own_pid:
                    pids.add(pid)
        except Exception:
            continue  # one unreadable endpoint mustn't blind the others
    return sorted({_process_name(pid) for pid in pids})


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return f"pid {pid}"



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
