"""What this machine can actually do, named once (docs/MAC-PORT.md).

Every platform-specific thing the app needs is listed here as a
**capability**: what it is for, what the app loses without it, and how to
find out whether this machine has it. `--doctor` prints the answers.

Two reasons this exists rather than a folder of per-OS modules.

**A port needs a checklist, not a treasure hunt.** The Windows calls are
spread across seven modules, each already guarded by a `sys.platform` test
that degrades quietly — good behaviour, but it means the work a macOS port
has to do is invisible until something silently does nothing. This file is
that work, enumerated, with the reference implementation named for each
entry.

**Silent degradation is only honest if someone is told.** The app is
deliberately built to keep running with pieces missing: no chime, no
exclusions, no lock. That is the right trade every time, and it also means
a Mac could run this for a week while quietly enforcing nothing. A
capability that reports itself turns that from a mystery into a line of
output.

Deliberately *not* a relocation of the working Windows code. Moving
platform code that only a live Windows session can exercise, purely for
tidiness, is how this project has produced most of its bugs — code that
read correctly, passed its tests, and was wrong. The one thing extracted
for real is idle detection, in `activity.py`, because that is the piece a
macOS port genuinely cannot get from the existing fallback.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"


@dataclass(frozen=True)
class Capability:
    """One platform-specific thing the app wants, and how to check for it."""

    key: str
    #: What it does, in the app's terms rather than the OS's.
    what: str
    #: What the app loses when it is missing. Written so the consequence is
    #: readable by whoever runs --doctor, not only by whoever ported it.
    without_it: str
    #: Where the working implementation lives, for a port to read.
    reference: str
    #: How macOS should provide it. Empty where it already works there.
    on_macos: str = ""

    def check(self) -> tuple[bool, str]:
        """(available, detail). Never raises: a probe is not worth a crash."""
        try:
            return _PROBES[self.key]()
        except Exception as exc:      # pragma: no cover - probes vary by machine
            return False, f"probe failed: {exc}"


# -- the probes --------------------------------------------------------
#
# Each returns (available, detail). They reuse the app's own code wherever
# possible: a probe that tests a reimplementation proves nothing.


def _probe_idle() -> tuple[bool, str]:
    from .activity import create_monitor

    monitor = create_monitor()
    seconds = monitor.idle_seconds()
    return True, f"{type(monitor).__name__}, idle {seconds:.1f}s"


def _probe_event_tap() -> tuple[bool, str]:
    """Whether the OS lets this process watch input at all.

    A *non*-suppressing listener on purpose. It answers the question that
    actually decides the lock — will the OS grant an event tap — without
    blocking the keyboard of whoever is running the diagnostic. It does not
    prove suppression itself works; only a real break does that.
    """
    try:
        from pynput import keyboard
    except ImportError:
        return False, "pynput not installed"
    listener = keyboard.Listener(on_press=lambda _key: None)
    listener.daemon = True
    try:
        listener.start()
        running = listener.running
    finally:
        try:
            listener.stop()
        except Exception:
            pass
    if not running:
        return False, "the OS refused an event tap"
    return True, "event tap granted (suppression itself needs a real break)"


def _probe_monitors() -> tuple[bool, str]:
    if not WINDOWS:
        return False, "falls back to one screen the size of the primary"
    try:
        import win32api

        count = len(win32api.EnumDisplayMonitors())
    except ImportError:
        return False, "pywin32 missing; falls back to one screen"
    return True, f"{count} monitor(s) enumerated"


def _probe_click_through() -> tuple[bool, str]:
    try:
        import win32gui
    except ImportError:
        return False, "the warning banner will block clicks under it"
    # Exercised rather than merely imported: the capability is the call.
    win32gui.GetDesktopWindow()
    return True, "banner passes clicks through"


def _probe_cursor() -> tuple[bool, str]:
    try:
        import win32api

        x, y = win32api.GetCursorPos()
    except ImportError:
        return False, "the banner will not fade when reached for"
    return True, f"cursor at {x},{y}"


def _probe_audio_check() -> tuple[bool, str]:
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return False, "cannot tell whether media is playing"
    sessions = AudioUtilities.GetAllSessions()
    return True, f"{len(sessions)} audio session(s) readable"


def _probe_media_key() -> tuple[bool, str]:
    if not WINDOWS:
        return False, "the lock screen's M key will do nothing"
    return True, "media key can be injected"


def _probe_devices() -> tuple[bool, str]:
    from .exclusions import devices_in_use

    if not WINDOWS:
        return False, "breaks will fire during calls unless the calendar covers them"
    camera = devices_in_use("webcam")
    mic = devices_in_use("microphone")
    return True, f"camera: {len(camera)} in use, microphone: {len(mic)} in use"


def _probe_sounds() -> tuple[bool, str]:
    from . import sounds

    clips = sounds.available()
    if not WINDOWS:
        return False, f"{len(clips)} clip(s) present, but no way to play them"
    if not clips:
        return True, f"playable, but no clips in {sounds.sounds_dir()}"
    return True, f"{len(clips)} clip(s) playable"


def _probe_tray() -> tuple[bool, str]:
    try:
        import pystray
    except ImportError:
        return False, "no menu: no walk toggle, no settings, no quit"
    backend = getattr(pystray.Icon, "__module__", "unknown")
    if MACOS:
        # pystray's Cocoa backend wants the main thread, and tkinter already
        # has it. This is a design decision for the port, not a missing call.
        return False, f"installed ({backend}), but its macOS backend needs " \
                      f"the main thread"
    return True, f"menu available ({backend})"


def _probe_autostart() -> tuple[bool, str]:
    from . import runtime

    if not WINDOWS:
        return False, "must be started by hand every day"
    return True, "on" if runtime.starts_with_windows() else "available, currently off"


def _probe_single_instance() -> tuple[bool, str]:
    if not WINDOWS:
        return False, "two copies could run at once and fight over the lock"
    return True, "guarded by a named mutex"


def _probe_data_dir() -> tuple[bool, str]:
    from .settings import default_path

    path = default_path().parent
    return path.exists() or _writable(path), str(path)


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


_PROBES = {
    "idle": _probe_idle,
    "event_tap": _probe_event_tap,
    "monitors": _probe_monitors,
    "click_through": _probe_click_through,
    "cursor": _probe_cursor,
    "audio_check": _probe_audio_check,
    "media_key": _probe_media_key,
    "devices": _probe_devices,
    "sounds": _probe_sounds,
    "tray": _probe_tray,
    "autostart": _probe_autostart,
    "single_instance": _probe_single_instance,
    "data_dir": _probe_data_dir,
}


#: Ordered so the ones the app cannot do without come first. A port should
#: work down this list; anything below `sounds` is comfort rather than
#: function.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="idle",
        what="How long since the last keyboard or mouse input",
        without_it="nothing works — no session is ever detected, so no break "
                   "is ever due and no work is ever counted",
        reference="activity.py — windows_idle_seconds()",
        on_macos="`ioreg -c IOHIDSystem` (HIDIdleTime) or "
                 "CGEventSourceSecondsSinceLastEventType. Both report elapsed "
                 "idle time rather than the input itself, so **neither needs "
                 "Accessibility permission** — unlike the pynput listener "
                 "fallback, which does. Implement this first: it is what "
                 "makes tracking and nudging work with no permissions at all.",
    ),
    Capability(
        key="event_tap",
        what="Permission to watch, and therefore suppress, global input",
        without_it="the lock covers every screen but blocks nothing — a "
                   "full-screen reminder you can click past, which is still "
                   "a real nudge (see overlay.py)",
        reference="overlay.py — InputSuppressor",
        on_macos="pynput's suppressing listener uses a Quartz event tap, "
                 "which needs Accessibility permission granted by hand in "
                 "System Settings. A managed Mac may refuse it outright; if "
                 "so the reminder-only lock is the answer, not a workaround.",
    ),
    Capability(
        key="monitors",
        what="The rectangle of every screen, so the lock covers all of them",
        without_it="only a primary-sized area is covered; a second screen "
                   "stays visible and usable through the break",
        reference="overlay.py — monitor_rects()",
        on_macos="Tk's own `winfo_screenwidth` fallback already covers the "
                 "single-screen case. For several screens, NSScreen.screens "
                 "via pyobjc, or accept the fallback at first.",
    ),
    Capability(
        key="sounds",
        what="Playing the break start and end chimes",
        without_it="breaks arrive and end silently",
        reference="sounds.py — _play_blocking()",
        on_macos="`afplay <path>` in a thread. One subprocess call; the "
                 "easiest item on this list.",
    ),
    Capability(
        key="devices",
        what="Whether the camera or microphone is in use, so a break never "
             "lands mid-call",
        without_it="breaks can interrupt a call. The calendar meeting skip "
                   "still covers anything booked, and --no-exclusions turns "
                   "the mechanism off honestly",
        reference="exclusions.py — devices_in_use()",
        on_macos="No registry equivalent. Candidates: CoreAudio's "
                 "kAudioDevicePropertyDeviceIsRunningSomewhere for the "
                 "microphone, or parsing `log stream`. Genuinely research; "
                 "ship with the calendar skip first.",
    ),
    Capability(
        key="autostart",
        what="Starting with the machine, so it is never forgotten",
        without_it="it only runs on the days she remembers to start it — "
                   "which is the habit this app exists to replace",
        reference="runtime.py — set_start_with_windows()",
        on_macos="A LaunchAgent plist in ~/Library/LaunchAgents. Well "
                 "documented and easy to test: `launchctl load`.",
    ),
    Capability(
        key="tray",
        what="The menu bar icon — walk toggle, Focus Mode, settings, quit",
        without_it="no way to start a walk or quit except the terminal it "
                   "was launched from",
        reference="tray.py — TrayIcon",
        on_macos="pystray's Cocoa backend needs the main thread and tkinter "
                 "already owns it. A real design decision: `rumps` on the "
                 "main thread, or a different control surface. Not a "
                 "translation of the existing call.",
    ),
    Capability(
        key="click_through",
        what="Letting clicks pass through the warning banner",
        without_it="the banner blocks a small corner of the screen for two "
                   "minutes before each break",
        reference="overlay.py — WarningBanner._make_click_through()",
        on_macos="NSWindow.ignoresMouseEvents via pyobjc.",
    ),
    Capability(
        key="cursor",
        what="The pointer's position, so the banner fades when reached for",
        without_it="the banner stays opaque over whatever it covers",
        reference="overlay.py — WarningBanner._poll_hover()",
        on_macos="Tk's `winfo_pointerxy()` works everywhere and would remove "
                 "this entry entirely.",
    ),
    Capability(
        key="audio_check",
        what="Whether audio is genuinely playing, before sending a media key",
        without_it="the M key on the lock screen cannot be offered safely — "
                   "the key is a toggle, so firing it into silence starts "
                   "playback instead of stopping it",
        reference="media.py — is_audio_playing()",
        on_macos="Optional. Media pause is off by default (Config."
                 "pause_media_on_lock) precisely because guessing wrong is "
                 "worse than doing nothing. Safe to skip entirely.",
    ),
    Capability(
        key="media_key",
        what="Injecting a play/pause keystroke",
        without_it="the lock screen's M key does nothing",
        reference="media.py — _tap()",
        on_macos="`osascript` telling the frontmost player to pause, or a "
                 "synthesized NX_KEYTYPE_PLAY event. Optional, as above.",
    ),
    Capability(
        key="single_instance",
        what="Refusing to start twice",
        without_it="two copies would each install an input hook and fight "
                   "over the lock — the worst thing to get wrong here",
        reference="runtime.py — SingleInstance",
        on_macos="A lock file with fcntl.flock. Small and worth doing before "
                 "the lock is enabled.",
    ),
    Capability(
        key="data_dir",
        what="Somewhere to keep config, state and history",
        without_it="nothing persists between runs",
        reference="settings.py — default_path()",
        on_macos="Already works: with no %APPDATA% it falls back to "
                 "~/.config/pomodoro-guardian/. Moving it to "
                 "~/Library/Application Support/ is idiom, not a fix.",
    ),
)


def report() -> list[tuple[Capability, bool, str]]:
    """Every capability with its verdict on this machine."""
    return [(cap, *cap.check()) for cap in CAPABILITIES]


def platform_name() -> str:
    return {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, sys.platform)
