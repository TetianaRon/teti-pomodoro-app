"""Tests for the capability registry and the idle-time seam (platform.py).

The registry's whole value is being the port's checklist, so the failure
that matters is a quiet one: an entry whose probe key is misspelled, or a
probe that raises and takes `--doctor` down with it. Neither would be
noticed by reading it.
"""

from __future__ import annotations

import time

import pytest

from pomodoro_guardian import activity as activity_module
from pomodoro_guardian import platform as platform_module
from pomodoro_guardian.activity import (
    _HID_IDLE,
    IdleTimeMonitor,
    create_monitor,
)


# -- the registry ------------------------------------------------------


def test_every_capability_has_a_probe():
    for cap in platform_module.CAPABILITIES:
        assert cap.key in platform_module._PROBES, f"{cap.key} has no probe"


def test_every_probe_is_reachable_from_the_registry():
    """An orphaned probe means a capability was renamed and left behind."""
    keys = {cap.key for cap in platform_module.CAPABILITIES}
    assert set(platform_module._PROBES) == keys


def test_capability_keys_are_unique():
    keys = [cap.key for cap in platform_module.CAPABILITIES]
    assert len(keys) == len(set(keys))


def test_idle_detection_is_listed_first():
    """It is the one without which nothing else in the app functions."""
    assert platform_module.CAPABILITIES[0].key == "idle"


@pytest.mark.parametrize("cap", platform_module.CAPABILITIES, ids=lambda c: c.key)
def test_every_capability_says_what_is_lost_and_where_to_look(cap):
    assert cap.what and not cap.what.endswith(".")
    assert cap.without_it, "a gap with no stated consequence is not actionable"
    assert ".py" in cap.reference


@pytest.mark.parametrize("cap", platform_module.CAPABILITIES, ids=lambda c: c.key)
def test_no_probe_raises(cap):
    """--doctor must survive every probe, however this machine is set up."""
    available, detail = cap.check()
    assert isinstance(available, bool)
    assert detail


def test_report_covers_every_capability():
    assert len(platform_module.report()) == len(platform_module.CAPABILITIES)


# -- the idle-time seam ------------------------------------------------


def test_an_idle_monitor_converts_idle_time_to_a_timestamp():
    monitor = IdleTimeMonitor(lambda: 12.0)
    assert monitor.idle_seconds() == 12.0
    # last_input_at is 12s ago on the monotonic clock everything else uses.
    assert time.monotonic() - monitor.last_input_at == pytest.approx(12.0, abs=0.5)


def test_a_negative_reading_reads_as_input_just_now():
    """A wrapped tick counter must not look like input from the future."""
    assert IdleTimeMonitor(lambda: -5.0).idle_seconds() == 0.0


def test_a_broken_idle_source_falls_through_to_watching_events(monkeypatch):
    """An unimplemented source must not leave the app blind to all input."""
    def unavailable():
        raise NotImplementedError

    monkeypatch.setattr(activity_module.sys, "platform", "darwin")
    monkeypatch.setitem(activity_module.IDLE_SOURCES, "darwin", unavailable)

    monitor = create_monitor()

    assert not isinstance(monitor, IdleTimeMonitor)
    assert monitor.idle_seconds() >= 0.0


def test_a_working_idle_source_is_preferred_to_watching_events(monkeypatch):
    monkeypatch.setattr(activity_module.sys, "platform", "darwin")
    monkeypatch.setitem(activity_module.IDLE_SOURCES, "darwin", lambda: 3.0)

    monitor = create_monitor()

    assert isinstance(monitor, IdleTimeMonitor)
    assert monitor.idle_seconds() == 3.0


def test_this_machine_reports_a_plausible_idle_time():
    monitor = create_monitor()
    idle = monitor.idle_seconds()
    assert 0.0 <= idle < 60 * 60 * 24


# -- the macOS parser, which has no Mac to run on ----------------------


@pytest.mark.parametrize("line, expected", [
    ('    | |   "HIDIdleTime" = 1500000000', 1.5),
    ("      HIDIdleTime = 0", 0.0),
    ('"HIDIdleTime"=12000000000', 12.0),
])
def test_the_ioreg_idle_time_is_read_in_seconds(line, expected):
    """Nanoseconds in, seconds out. Format unverified — see the docstring."""
    match = _HID_IDLE.search(line)
    assert match is not None
    assert int(match.group(1)) / 1_000_000_000.0 == expected


def test_ioreg_output_without_the_field_is_not_silently_zero():
    """Zero would read as "input just now" and keep a session alive forever."""
    assert _HID_IDLE.search("IOHIDSystem  <class IOHIDSystem>") is None
