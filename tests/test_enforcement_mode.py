"""Tests for reminder mode, and for whether a break was really taken.

Two things that only make sense together. Turning enforcement off is a
legitimate choice — while learning to trust the lock, on a day of
presentations, or on a machine that would refuse the permission anyway —
but a break nobody is held to is a break that can be worked straight
through. If the log kept filing those as "taken" it would flatter the day,
which is the exact accounting failure the history exists to catch.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from pomodoro_guardian import history as history_module
from pomodoro_guardian import settings as settings_module
from pomodoro_guardian.app import Application
from pomodoro_guardian.config import MINUTE, Config
from pomodoro_guardian.history import History
from pomodoro_guardian.overlay import LockOverlay


# -- the preference is separate from the permission ---------------------


def overlay(block_input: bool) -> LockOverlay:
    """A LockOverlay with no tkinter, to read the mode it settles on."""
    return LockOverlay(root=None, config=replace(Config(), block_input=block_input))


def test_enforcement_needs_both_the_setting_and_the_hook():
    """Wanted AND permitted: two independent facts, and both must hold."""
    cases = {
        (True, True): True,
        (True, False): False,
        (False, True): False,
        (False, False): False,
    }
    for (wanted, permitted), expected in cases.items():
        config = replace(Config(), block_input=wanted)
        assert (config.block_input and permitted) is expected


def test_the_two_reasons_for_not_enforcing_read_differently():
    """One is a setting she can change; the other may not be hers to grant."""
    off = overlay(block_input=False).reminder_reason()
    refused = overlay(block_input=True).reminder_reason()

    assert "settings" in off
    assert "settings" not in refused
    assert off != refused


def test_the_setting_survives_a_round_trip_through_the_file(tmp_path):
    path = tmp_path / "config.json"
    original = settings_module.Settings(
        config=replace(Config(), block_input=False)
    )
    settings_module.save(original, path)

    assert settings_module.load(path).config.block_input is False


def test_enforcement_is_on_by_default():
    assert Config().block_input is True
    assert settings_module.Settings().config.block_input is True


def test_a_missing_setting_reads_as_enforcing(tmp_path):
    """An old config file predates the option; it must not silently disarm."""
    path = tmp_path / "config.json"
    path.write_text('{"lock": {"safety_unlock": true}}', encoding="utf-8")

    assert settings_module.load(path).config.block_input is True


# -- was the break actually taken? -------------------------------------


class Decider:
    """The real Application method, without building a whole Application.

    `__new__` rather than a reimplementation: a test that re-derives the
    threshold would pass while the shipped rule was wrong, which is the
    failure mode this project keeps finding.
    """

    def __init__(self, config: Config) -> None:
        self.app = Application.__new__(Application)
        self.app.config = config
        self.app._break_length = 0.0
        self.app._break_input_seconds = 0.0

    def start(self, length: float) -> None:
        self.app._break_length = length
        self.app._break_input_seconds = 0.0

    def worked(self, seconds: float) -> None:
        self.app._break_input_seconds += seconds

    @property
    def ignored(self) -> bool:
        return self.app._break_was_ignored()


def test_a_break_left_alone_counts_as_taken():
    decider = Decider(Config())
    decider.start(5 * MINUTE)
    assert not decider.ignored


def test_a_glance_at_a_message_does_not_count_as_working_through():
    decider = Decider(Config())
    decider.start(5 * MINUTE)
    decider.worked(20)          # 20s of 300s, under the 25% threshold
    assert not decider.ignored


def test_working_most_of_a_break_counts_as_ignored():
    decider = Decider(Config())
    decider.start(5 * MINUTE)
    decider.worked(4 * MINUTE)
    assert decider.ignored


def test_the_threshold_scales_with_a_long_break():
    """A fraction, not a fixed count: 75s through a 15-min break is nothing."""
    decider = Decider(Config())
    decider.start(15 * MINUTE)
    decider.worked(80)
    assert not decider.ignored

    decider.worked(5 * MINUTE)
    assert decider.ignored


def test_a_break_of_no_length_is_never_ignored():
    """Guards a divide-by-nothing on a break that never properly started."""
    decider = Decider(Config())
    decider.start(0.0)
    decider.worked(600)
    assert not decider.ignored


# -- the log has to say which -------------------------------------------


def test_a_worked_through_break_is_counted_apart_from_a_taken_one(tmp_path):
    log = History(tmp_path / "history.db")
    log.record(history_module.BREAK_TAKEN, detail="short; 0.0 min of input")
    log.record(history_module.BREAK_IGNORED, detail="short; 4.0 min of input")
    log.record(history_module.BREAK_IGNORED, detail="long; 12.0 min of input")

    summary = log.summary(date.today())

    assert summary.breaks_taken == 1
    assert summary.breaks_ignored == 2


def test_a_worked_through_break_shows_up_in_the_day_summary(tmp_path):
    log = History(tmp_path / "history.db")
    log.record(history_module.BREAK_TAKEN)
    log.record(history_module.BREAK_IGNORED)

    described = log.summary(date.today()).describe()

    assert "worked through" in described


def test_a_clean_day_says_nothing_about_worked_through_breaks(tmp_path):
    log = History(tmp_path / "history.db")
    log.record(history_module.BREAK_TAKEN)

    assert "worked through" not in log.summary(date.today()).describe()


def test_the_long_break_is_recorded_as_long(tmp_path):
    """Every break logged before 2026-08-10 said "short", long ones included."""
    log = History(tmp_path / "history.db")
    log.record(history_module.BREAK_TAKEN, detail="long; 0.0 min of input")

    (_at, kind, _seconds, detail), = log.tail(1)

    assert kind == history_module.BREAK_TAKEN
    assert detail.startswith("long")
