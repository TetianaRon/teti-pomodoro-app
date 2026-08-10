"""Tests for never-interrupt exclusions (SPEC §3).

Two halves: the engine's freezing behaviour, which is pure logic and
tested exhaustively here, and the Windows detection plumbing, where only
the parts that don't need a live camera can be exercised.
"""

from __future__ import annotations

import pytest

from pomodoro_guardian.config import Config
from pomodoro_guardian.exclusions import (
    Exclusion,
    FakeDetector,
    NullDetector,
    Reason,
    friendly_name,
)
from pomodoro_guardian.timer import Event, PomodoroEngine, State

START = 1000.0


class Harness:
    """Drives the engine with a fake clock and a scriptable exclusion."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.now = START
        self.last_input = START
        self.excluded = False
        self.engine = PomodoroEngine(self.config, now=START)
        self.events: list[Event] = []

    def advance(self, seconds: float, active: bool = True, step: float = 1.0):
        target = self.now + seconds
        while self.now < target - 1e-9:
            self.now = min(self.now + step, target)
            if active:
                self.last_input = self.now
            self.events.extend(
                self.engine.update(self.now, self.last_input, self.excluded)
            )
        return self.events

    def advance_until(self, event: Event, limit: float, active: bool = True):
        spent = 0.0
        while spent < limit:
            before = len(self.events)
            self.advance(1.0, active=active)
            if event in self.events[before:]:
                return self.now
            spent += 1.0
        pytest.fail(f"{event} did not fire within {limit}s")

    def drain(self):
        events, self.events = self.events, []
        return events

    @property
    def state(self):
        return self.engine.state


# -- engine behaviour -------------------------------------------------


def test_a_call_stops_the_break_from_arriving():
    """The headline requirement: it must not lock during a meeting."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.excluded = True
    h.drain()

    # Long past when the break would otherwise have been due.
    h.advance(h.config.work_duration * 2, active=True)

    assert Event.BREAK_STARTED not in h.events
    assert h.state is not State.BREAK
    assert h.engine.snapshot().excluded


def test_the_countdown_freezes_rather_than_running_on():
    """SPEC §3: 'an in-progress countdown pauses'."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    remaining_before = h.engine.snapshot().remaining

    h.excluded = True
    h.advance(600, active=True)

    assert h.engine.snapshot().remaining == pytest.approx(
        remaining_before, abs=2.0
    ), "work accrued during a call"


def test_the_countdown_resumes_where_it_left_off():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.excluded = True
    h.advance(600, active=True)
    remaining_during = h.engine.snapshot().remaining
    h.drain()

    h.excluded = False
    h.advance(60, active=True)

    assert Event.EXCLUSION_ENDED in h.events
    assert h.engine.snapshot().remaining == pytest.approx(
        remaining_during - 60, abs=2.0
    )


def test_a_long_call_does_not_abandon_the_session():
    """A meeting you barely type in must not read as having left the desk."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.drain()

    # Two hours on a call with no keyboard input at all — far past
    # idle_reset_after, which would otherwise discard the interval.
    h.excluded = True
    h.advance(2 * 3600, active=False)
    h.excluded = False
    h.advance(5, active=True)

    assert Event.WORK_ABANDONED not in h.events
    assert Event.CYCLES_RESET not in h.events
    assert h.state in (State.WORK, State.WARNING)


def test_the_break_arrives_once_the_call_ends():
    h = Harness()
    h.advance_until(Event.WARNING_STARTED, limit=2000)
    h.excluded = True
    h.advance(600, active=True)
    assert h.state is State.WARNING, "should still be waiting, not locked"

    h.excluded = False
    h.advance_until(Event.BREAK_STARTED, limit=300)

    assert h.state is State.BREAK


def test_exclusion_events_fire_once_each_not_every_tick():
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    h.drain()

    h.excluded = True
    h.advance(120)
    h.excluded = False
    h.advance(120)

    assert h.events.count(Event.EXCLUSION_STARTED) == 1
    assert h.events.count(Event.EXCLUSION_ENDED) == 1


def test_a_call_does_not_start_a_work_session():
    """Sitting on a call isn't the sustained input that starts tracking."""
    h = Harness()
    h.excluded = True
    h.advance(h.config.start_threshold * 3, active=True)

    assert h.state is State.IDLE
    assert Event.WORK_STARTED not in h.events


def test_a_break_already_locked_runs_its_course():
    """You can't join a call through a lock, and unlocking early is worse."""
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.drain()

    h.excluded = True
    h.advance_until(
        Event.BREAK_ENDED, limit=h.config.short_break_duration + 10, active=False
    )

    assert h.engine.completed_cycles == 1


def test_time_on_a_call_is_never_retroactively_credited():
    """The watermark must not pay out the call's duration on resume."""
    h = Harness()
    h.advance_until(Event.WORK_STARTED, limit=200)
    remaining_before = h.engine.snapshot().remaining

    h.excluded = True
    h.advance(1800, active=False)   # half an hour, no typing
    h.excluded = False
    h.advance(1, active=True)       # first keystroke back

    assert h.engine.snapshot().remaining >= remaining_before - 3.0


# -- detectors --------------------------------------------------------


def test_null_detector_never_excludes():
    assert not NullDetector().check().active


def test_fake_detector_scripts_a_reason():
    fake = FakeDetector()
    assert not fake.check().active

    fake.set(Reason.CAMERA, detail="Teams.exe")
    assert fake.check().active
    assert "camera" in fake.check().describe()
    assert "Teams.exe" in fake.check().describe()

    fake.clear()
    assert not fake.check().active


def test_an_empty_exclusion_describes_itself_plainly():
    assert Exclusion().describe() == "nothing blocking a break"


def test_multiple_reasons_are_all_reported():
    described = Exclusion((Reason.CAMERA, Reason.MICROPHONE)).describe()
    assert "camera" in described and "microphone" in described


def _patch_devices(monkeypatch, *, webcam=(), microphone=()):
    from pomodoro_guardian import exclusions

    monkeypatch.setattr(
        exclusions,
        "devices_in_use",
        lambda store: list(webcam if store == "webcam" else microphone),
    )
    return exclusions


def test_windows_detector_names_the_app_holding_the_microphone(monkeypatch):
    """The live path. Nothing exercised this before, and it was broken.

    `_join` was referenced but never defined, so the real detector raised
    NameError the moment any device was actually in use — invisible to
    every test using FakeDetector, and to any check run while idle.
    """
    exclusions = _patch_devices(monkeypatch, microphone=["Zoom.exe"])
    result = exclusions.WindowsDetector().check()

    assert result.active
    assert result.reasons == (Reason.MICROPHONE,)
    assert "Zoom.exe" in result.describe()


def test_windows_detector_reports_camera_and_microphone_together(monkeypatch):
    exclusions = _patch_devices(
        monkeypatch, webcam=["chrome.exe"], microphone=["chrome.exe"]
    )
    result = exclusions.WindowsDetector().check()

    assert set(result.reasons) == {Reason.CAMERA, Reason.MICROPHONE}
    assert "chrome.exe" in result.describe()


def test_windows_detector_lists_several_apps_on_one_device(monkeypatch):
    exclusions = _patch_devices(
        monkeypatch, microphone=["Zoom.exe", "chrome.exe"]
    )
    described = exclusions.WindowsDetector().check().describe()

    assert "Zoom.exe" in described and "chrome.exe" in described


def test_windows_detector_is_clear_when_nothing_holds_a_device(monkeypatch):
    exclusions = _patch_devices(monkeypatch)
    assert not exclusions.WindowsDetector().check().active


def test_a_full_screen_window_no_longer_holds_breaks_off(monkeypatch):
    """The presenting check is gone (2026-08-10).

    It read QUNS_BUSY, which means "a full-screen application is running
    *or* presentation settings are applied" — so any full-screen window
    tripped it, this app's own lock overlay included. The real log showed
    it firing three times in eight seconds with nothing being presented.
    Calls are caught by the microphone, scheduled ones by the calendar.
    """
    exclusions = _patch_devices(monkeypatch)
    assert not exclusions.WindowsDetector().check().active


def test_disabled_signals_are_not_consulted(monkeypatch):
    exclusions = _patch_devices(
        monkeypatch, webcam=["chrome.exe"], microphone=["Zoom.exe"],
    )
    detector = exclusions.WindowsDetector(camera=False, microphone=False)

    assert not detector.check().active


@pytest.mark.parametrize(
    "key_name,expected",
    [
        # Desktop apps are stored as a path with # for the separator.
        (r"C:#Program Files#Zoom#bin#Zoom.exe", "Zoom.exe"),
        (r"C:#Users#t#AppData#Local#Microsoft#Teams#Teams.exe", "Teams.exe"),
        # Packaged apps use a package family name.
        ("Microsoft.WindowsCamera_8wekyb3d8bbwe", "Microsoft.WindowsCamera"),
        ("SomethingPlain", "SomethingPlain"),
    ],
)
def test_registry_key_names_are_made_readable(key_name, expected):
    assert friendly_name(key_name) == expected
