"""Never-interrupt detection (SPEC §3): calls and screen sharing.

Deliberately *not* built on process names. Chasing "is Zoom running" means
an endless list that breaks whenever a new tool appears or an old one
renames its binary, and a running app says nothing about whether a call is
actually in progress.

Instead this reads the two signals Windows already maintains:

* **CapabilityAccessManager's ConsentStore** — the registry keys behind the
  camera/microphone privacy indicator in the tray. Any app currently
  holding a device has `LastUsedTimeStop = 0`. This is authoritative, cheap
  to read, and works for apps that don't exist yet.
* **SHQueryUserNotificationState** — the API Windows itself uses to decide
  whether it's rude to pop a notification. Presenting and full-screen
  states are precisely "don't interrupt me", which is the same question.

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

# SHQueryUserNotificationState return values (shellapi.h QUERY_USER_NOTIFICATION_STATE).
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7


class Reason(Enum):
    CAMERA = "camera in use"
    MICROPHONE = "microphone in use"
    PRESENTING = "presenting"


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
    """The real thing: registry device use plus the notification state."""

    def __init__(
        self,
        camera: bool = True,
        microphone: bool = True,
        presenting: bool = True,
    ) -> None:
        self._camera = camera
        self._microphone = microphone
        self._presenting = presenting

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
        if self._presenting and presenting_now():
            reasons.append(Reason.PRESENTING)

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


def presenting_now() -> bool:
    """True when Windows itself considers this a bad moment to interrupt.

    Only presentation mode and the busy/full-screen state count. Games
    (`RUNNING_D3D_FULL_SCREEN`) and ordinary full-screen apps deliberately
    do not: a maximised video player should never be able to hold breaks
    off indefinitely, and neither is work anyway.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        state = ctypes.c_int()
        result = ctypes.windll.shell32.SHQueryUserNotificationState(
            ctypes.byref(state)
        )
        if result != 0:  # not S_OK
            return False
        return state.value in (QUNS_BUSY, QUNS_PRESENTATION_MODE)
    except (OSError, AttributeError):  # pragma: no cover - API unavailable
        return False


def create_detector(
    camera: bool = True, microphone: bool = True, presenting: bool = True
) -> Detector:
    """The right detector for this machine."""
    if not sys.platform.startswith("win"):
        return NullDetector()
    return WindowsDetector(camera, microphone, presenting)
