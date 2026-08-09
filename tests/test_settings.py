"""Tests for settings persistence.

The file is meant to be hand-editable, so most of what matters here is
what happens when it has been hand-edited badly.
"""

from __future__ import annotations

import json

import pytest

from pomodoro_guardian.config import MINUTE, Config
from pomodoro_guardian.settings import Settings, exists, load, save


def test_round_trip_preserves_everything():
    original = Settings(
        config=Config(work_duration=30 * MINUTE, safety_unlock=False),
        calendar_url="https://example.com/basic.ics",
        working_day_cap_hours=9.5,
        override_raises_per_month=4,
        walking_target_minutes=45,
    )
    restored = Settings.from_dict(original.to_dict())
    assert restored == original


def test_file_round_trip(tmp_path):
    path = tmp_path / "nested" / "config.json"
    assert not exists(path)

    settings = Settings(calendar_url="https://example.com/basic.ics")
    saved_to = save(settings, path)

    assert saved_to == path
    assert exists(path)
    assert load(path).calendar_url == "https://example.com/basic.ics"


def test_the_file_is_written_in_human_units(tmp_path):
    """Minutes and hours, not seconds — someone has to read this."""
    path = tmp_path / "config.json"
    save(Settings(), path)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["rhythm_minutes"]["work"] == 25
    assert data["rhythm_minutes"]["short_break"] == 5
    assert data["caps"]["working_day_hours"] == 11
    assert data["walking"]["target_minutes"] == 60


def test_missing_file_gives_defaults(tmp_path):
    assert load(tmp_path / "absent.json") == Settings()


def test_corrupt_file_gives_defaults_rather_than_crashing(tmp_path):
    """Losing break enforcement to a stray comma would be the wrong trade."""
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert load(path) == Settings()


def test_a_partial_file_only_loses_the_keys_it_omits(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"rhythm_minutes": {"work": 50}}), encoding="utf-8"
    )
    loaded = load(path)

    assert loaded.config.work_duration == 50 * MINUTE
    assert loaded.config.short_break_duration == Config().short_break_duration
    assert loaded.working_day_cap_hours == Settings().working_day_cap_hours


@pytest.mark.parametrize("bad", ["twenty", None, [], {}, True])
def test_a_malformed_value_falls_back_to_its_default(tmp_path, bad):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"rhythm_minutes": {"work": bad, "short_break": 7}}),
        encoding="utf-8",
    )
    loaded = load(path)

    assert loaded.config.work_duration == Config().work_duration
    assert loaded.config.short_break_duration == 7 * MINUTE, (
        "one bad value must not discard its neighbours"
    )


def test_blank_calendar_url_reads_as_unset():
    assert Settings.from_dict({"calendar_url": "   "}).calendar_url is None
    assert Settings.from_dict({"calendar_url": 42}).calendar_url is None


def test_saving_is_atomic(tmp_path):
    """A half-written config must never replace a good one."""
    path = tmp_path / "config.json"
    save(Settings(), path)
    save(Settings(calendar_url="https://example.com/basic.ics"), path)

    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"
    assert load(path).calendar_url == "https://example.com/basic.ics"
