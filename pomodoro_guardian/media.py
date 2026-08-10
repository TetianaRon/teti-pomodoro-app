"""Pausing media when the lock appears.

A break locks the screen and swallows every keystroke, so a video carries
on playing behind the overlay — audible, invisible, and impossible to
stop until the break ends.

**Why a real media keystroke.** Three other routes were measured on the
target machine and rejected:

* `WM_APPCOMMAND` broadcast to `HWND_BROADCAST` — pauses, then something
  else answers with a play and the video resumes a second later.
* `WM_APPCOMMAND` to the foreground window — the foreground when a break
  fires is whatever you are *working* in, never the media app. Measured:
  it captured VS Code every time.
* `PostMessage` rather than `SendMessage` — never arrived at all.

A media key works because Windows routes it to whatever holds the media
session, regardless of focus, which is exactly the semantics needed. It
is sent before input suppression starts, so the lock's own hook does not
swallow it.

**Why the audio check is not optional.** `VK_MEDIA_PLAY_PAUSE` is a
toggle. Fired blindly it would *start* playback on any break taken in
silence — a worse bug than the one being fixed. So the key is only sent
when something is genuinely making noise.
"""

from __future__ import annotations

import os
import sys
import time

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
KEYEVENTF_KEYUP = 0x0002

#: Peak level counting as "playing". Above float noise, below anything
#: audible — a paused stream reads exactly 0.
AUDIBLE_PEAK = 0.0005

#: A quiet passage mid-video would read 0 on a single sample, so take a
#: few. ~0.15s total, which is imperceptible against a lock appearing.
SAMPLES = 3
SAMPLE_INTERVAL = 0.05


def is_audio_playing(
    samples: int = SAMPLES,
    interval: float = SAMPLE_INTERVAL,
    threshold: float = AUDIBLE_PEAK,
) -> bool:
    """True if any application is currently rendering audible sound.

    Returns False if pycaw is unavailable, which makes the caller skip the
    media key entirely. Failing that way round is deliberate: not pausing
    a video is a mild annoyance, while starting playback unbidden on every
    break would be intolerable.
    """
    try:
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    except ImportError:  # pragma: no cover - depends on install state
        return False

    # Our own break chime must never count. It plays through this same
    # process, so without this the chime is heard as "media is playing" and
    # the toggle below un-pauses whatever the user had deliberately paused
    # — which is exactly what happened to a paused browser video.
    own_pid = os.getpid()

    for attempt in range(max(1, samples)):
        try:
            sessions = AudioUtilities.GetAllSessions()
        except Exception:  # pragma: no cover - COM can fail transiently
            return False
        for session in sessions:
            if not session.Process:
                continue
            try:
                if _pid_of(session) == own_pid:
                    continue
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                if meter.GetPeakValue() > threshold:
                    return True
            except Exception:
                continue
        if attempt < samples - 1:
            time.sleep(interval)
    return False


def _pid_of(session) -> int | None:
    """The process id behind an audio session, however pycaw exposes it."""
    for attribute in ("ProcessId", "pid"):
        value = getattr(session, attribute, None)
        if isinstance(value, int):
            return value
    process = getattr(session, "Process", None)
    return getattr(process, "pid", None)


def _tap(vk: int) -> bool:
    """Press and release a virtual key.

    Caught by this app's own input hook while a lock is up, so the caller
    has to lift keyboard suppression around it — see
    `InputSuppressor.with_keyboard_lifted`.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        return True
    except (OSError, AttributeError):  # pragma: no cover
        return False


def send_play_pause() -> bool:
    return _tap(VK_MEDIA_PLAY_PAUSE)


def send_mute() -> bool:
    return _tap(VK_VOLUME_MUTE)


def pause_if_playing() -> bool:
    """Pause media, but only if something is actually playing.

    Returns True if the key was sent. The check is what makes a toggle
    safe to use.
    """
    if not is_audio_playing():
        return False
    return send_play_pause()
