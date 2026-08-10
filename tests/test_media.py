"""Tests for pausing media when the lock appears.

The guard is the point here. `VK_MEDIA_PLAY_PAUSE` is a toggle, so
sending it when nothing is playing would *start* playback on every quiet
break — worse than the problem it solves.
"""

from __future__ import annotations

from pomodoro_guardian import media


class FakeMeter:
    def __init__(self, peak):
        self._peak = peak

    def GetPeakValue(self):
        return self._peak


class FakeCtl:
    def __init__(self, peak):
        self._peak = peak

    def QueryInterface(self, _interface):
        return FakeMeter(self._peak)


class FakeSession:
    def __init__(self, peak, has_process=True, pid=4242):
        self._ctl = FakeCtl(peak)
        self.Process = object() if has_process else None
        self.ProcessId = pid


def fake_pycaw(monkeypatch, sessions):
    """Stand in for pycaw, which needs real audio hardware to say anything."""
    import sys
    import types

    module = types.ModuleType("pycaw.pycaw")
    module.AudioUtilities = type(
        "AudioUtilities", (), {"GetAllSessions": staticmethod(lambda: sessions)}
    )
    module.IAudioMeterInformation = object
    package = types.ModuleType("pycaw")
    package.pycaw = module
    monkeypatch.setitem(sys.modules, "pycaw", package)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", module)


def test_audible_sound_is_detected(monkeypatch):
    fake_pycaw(monkeypatch, [FakeSession(0.52)])
    assert media.is_audio_playing(samples=1)


def test_silence_is_not_mistaken_for_playback(monkeypatch):
    """A paused stream stays open and reads exactly zero."""
    fake_pycaw(monkeypatch, [FakeSession(0.0)])
    assert not media.is_audio_playing(samples=1)


def test_float_noise_stays_below_the_threshold(monkeypatch):
    fake_pycaw(monkeypatch, [FakeSession(0.00001)])
    assert not media.is_audio_playing(samples=1)


def test_one_loud_session_among_many_quiet_ones_counts(monkeypatch):
    fake_pycaw(
        monkeypatch,
        [FakeSession(0.0), FakeSession(0.0), FakeSession(0.4)],
    )
    assert media.is_audio_playing(samples=1)


def test_sessions_without_a_process_are_ignored(monkeypatch):
    fake_pycaw(monkeypatch, [FakeSession(0.9, has_process=False)])
    assert not media.is_audio_playing(samples=1)


def test_no_sessions_at_all_is_silence(monkeypatch):
    fake_pycaw(monkeypatch, [])
    assert not media.is_audio_playing(samples=1)


def test_missing_pycaw_reads_as_silence(monkeypatch):
    """Fail towards doing nothing, never towards starting playback."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("pycaw"):
            raise ImportError("no pycaw")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert not media.is_audio_playing(samples=1)


def test_our_own_chime_does_not_count_as_playing_media(monkeypatch):
    """The bug this fixes: a paused video started playing on a break.

    The break chime plays through this same process. Counting it as "media
    is playing" made the toggle fire, and the toggle un-paused whatever the
    user had deliberately paused.
    """
    import os

    fake_pycaw(monkeypatch, [FakeSession(0.6, pid=os.getpid())])
    assert not media.is_audio_playing(samples=1)


def test_someone_elses_audio_still_counts(monkeypatch):
    import os

    fake_pycaw(
        monkeypatch,
        [FakeSession(0.6, pid=os.getpid()), FakeSession(0.4, pid=999_999)],
    )
    assert media.is_audio_playing(samples=1)


def test_pause_is_skipped_when_nothing_is_playing(monkeypatch):
    """The whole reason the guard exists."""
    sent = []
    monkeypatch.setattr(media, "is_audio_playing", lambda: False)
    monkeypatch.setattr(media, "send_play_pause", lambda: sent.append(1) or True)

    assert not media.pause_if_playing()
    assert not sent, "a toggle was sent into silence — that starts playback"


def test_pause_is_sent_when_audio_is_playing(monkeypatch):
    sent = []
    monkeypatch.setattr(media, "is_audio_playing", lambda: True)
    monkeypatch.setattr(media, "send_play_pause", lambda: sent.append(1) or True)

    assert media.pause_if_playing()
    assert sent == [1]


def test_a_broken_session_does_not_break_detection(monkeypatch):
    class Exploding:
        Process = object()

        @property
        def _ctl(self):
            raise RuntimeError("COM died")

    fake_pycaw(monkeypatch, [Exploding(), FakeSession(0.5)])
    assert media.is_audio_playing(samples=1)
