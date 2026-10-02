"""Tests for call detection checking itself (SPEC §3, detection_health.py).

Written after Windows updates silently stopped the registry recording
microphone use (2026-09-22): the app kept running, every check said
"nobody", and the first anyone knew was a lock in an overrunning meeting.
"""

from __future__ import annotations

from pomodoro_guardian.detection_health import (
    BANNER_SECONDS,
    DISAGREE_AFTER,
    SEEN,
    SILENT,
    SILENT_MEETINGS_WARN,
    DetectionHealth,
    streak_from,
)
from pomodoro_guardian.exclusions import MicReadings

NOBODY = MicReadings(audio=(), registry=())
AUDIO_ONLY = MicReadings(audio=("chrome.exe",), registry=())
REGISTRY_ONLY = MicReadings(audio=(), registry=("chrome.exe",))
BOTH = MicReadings(audio=("chrome.exe",), registry=("chrome.exe",))


def run(health, mic, seconds, start=0.0, meeting=False, device_seen=False):
    """Feed one reading every second; collect warnings and verdicts."""
    warnings, verdicts = [], []
    for t in range(int(seconds)):
        found, verdict = health.observe(start + t, mic, meeting, device_seen)
        warnings.extend(found)
        if verdict:
            verdicts.append(verdict)
    return warnings, verdicts


# -- the two microphone sources disagreeing ----------------------------


def test_agreeing_sources_raise_nothing():
    health = DetectionHealth()
    assert run(health, BOTH, DISAGREE_AFTER * 3) == ([], [])
    assert run(health, NOBODY, DISAGREE_AFTER * 3) == ([], [])


def test_a_brief_disagreement_is_ignored():
    """One source notices a stream a second before the other."""
    health = DetectionHealth()
    warnings, _ = run(health, AUDIO_ONLY, DISAGREE_AFTER - 5)
    warnings += run(health, BOTH, 10, start=DISAGREE_AFTER)[0]
    assert warnings == []


def test_a_quiet_registry_is_logged_once_without_a_banner():
    """Exactly what happened 2026-09-22. The audio stack still covers every
    call, so it is worth knowing but not worth the screen."""
    health = DetectionHealth()
    warnings, _ = run(health, AUDIO_ONLY, DISAGREE_AFTER * 5)

    assert len(warnings) == 1
    assert not warnings[0].banner
    assert "chrome.exe" in warnings[0].message
    assert health.banner(DISAGREE_AFTER * 5) is None


def test_a_blind_audio_check_goes_on_the_banner():
    health = DetectionHealth()
    warnings, _ = run(health, REGISTRY_ONLY, DISAGREE_AFTER + 2)

    assert len(warnings) == 1 and warnings[0].banner
    assert health.banner(DISAGREE_AFTER + 2) == warnings[0].message


def test_the_banner_clears_after_a_while():
    health = DetectionHealth()
    run(health, REGISTRY_ONLY, DISAGREE_AFTER + 2)
    later = DISAGREE_AFTER + 2 + BANNER_SECONDS + 1
    assert health.banner(later) is None


def test_an_unavailable_audio_stack_is_reported_once():
    health = DetectionHealth()
    warnings, _ = run(health, MicReadings(audio=None, registry=()), 30)

    assert len(warnings) == 1 and warnings[0].banner
    assert "audio" in warnings[0].message


def test_no_readings_means_nothing_to_compare():
    """The microphone switched off in config, or no Windows detector."""
    assert run(DetectionHealth(), None, DISAGREE_AFTER * 3) == ([], [])


# -- calendar meetings with no device seen -----------------------------


def meeting(health, start, device_seen):
    """A ten-minute calendar meeting, then a minute clear."""
    w1, v1 = run(
        health, NOBODY, 600, start, meeting=True, device_seen=device_seen
    )
    w2, v2 = run(health, NOBODY, 60, start + 600)
    return w1 + w2, v1 + v2


def test_each_meeting_gets_one_verdict_when_it_ends():
    health = DetectionHealth()
    assert meeting(health, 0, device_seen=True)[1] == [SEEN]
    assert meeting(health, 1000, device_seen=False)[1] == [SILENT]


def test_one_device_reading_anywhere_in_the_meeting_counts():
    health = DetectionHealth()
    run(health, NOBODY, 300, meeting=True)
    run(health, NOBODY, 1, 300, meeting=True, device_seen=True)
    _, verdicts = run(health, NOBODY, 300, 301, meeting=False)
    assert verdicts == [SEEN]


def test_a_few_silent_meetings_are_not_alarming():
    """In-person meetings and calls taken on the phone are normal."""
    health = DetectionHealth()
    warnings = []
    for n in range(SILENT_MEETINGS_WARN - 1):
        warnings += meeting(health, n * 1000, device_seen=False)[0]
    assert warnings == []


def test_a_run_of_silent_meetings_warns_on_the_banner():
    """The check that catches both sources failing together."""
    health = DetectionHealth()
    warnings = []
    for n in range(SILENT_MEETINGS_WARN):
        warnings += meeting(health, n * 1000, device_seen=False)[0]

    assert len(warnings) == 1 and warnings[0].banner
    assert str(SILENT_MEETINGS_WARN) in warnings[0].message


def test_a_meeting_with_a_device_resets_the_streak():
    health = DetectionHealth(silent_streak=SILENT_MEETINGS_WARN - 1)
    meeting(health, 0, device_seen=True)
    assert health.silent_streak == 0


def test_the_streak_carries_over_from_a_previous_run():
    health = DetectionHealth(silent_streak=SILENT_MEETINGS_WARN - 1)
    warnings, _ = meeting(health, 0, device_seen=False)
    assert len(warnings) == 1


def test_the_streak_is_read_back_from_history_newest_first():
    assert streak_from([SILENT, SILENT, SEEN, SILENT]) == 2
    assert streak_from([SEEN, SILENT]) == 0
    assert streak_from([]) == 0


# -- wired into the app ------------------------------------------------


class _History:
    def __init__(self):
        self.rows = []

    def record(self, kind, seconds=None, detail=None, when=None):
        self.rows.append((kind, detail))


def _app():
    from pomodoro_guardian.app import Application

    instance = Application.__new__(Application)
    instance.detection_health = DetectionHealth()
    instance.history = _History()
    instance._device_detector = type("D", (), {"last_mic": REGISTRY_ONLY})()
    instance._log = lambda _message: None
    return instance


def test_the_app_records_each_meeting_verdict_and_every_warning():
    from pomodoro_guardian import history
    from pomodoro_guardian.exclusions import Exclusion, Reason

    app = _app()
    in_meeting = Exclusion((Reason.MEETING,), "until 12:30")
    for t in range(int(DISAGREE_AFTER) + 5):
        app._check_detection_health(float(t), in_meeting)
    app._check_detection_health(DISAGREE_AFTER + 10, Exclusion())

    kinds = [kind for kind, _ in app.history.rows]
    assert kinds == [history.DETECTION_WARNING, history.MEETING_CHECKED]
    assert app.history.rows[1][1] == SILENT
