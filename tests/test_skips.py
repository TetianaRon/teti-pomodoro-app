"""Tests for the break-skip system (SPEC §4).

Two paths: the unlimited calendar meeting skip, and the capped custom
skip. They are separate mechanisms — the first suspends the countdown,
the second spends budget to defer a break — so they are tested apart.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pomodoro_guardian.calendar_feed import BusyBlock, Schedule
from pomodoro_guardian.calendar_watch import CalendarWatcher
from pomodoro_guardian.config import MINUTE, Config
from pomodoro_guardian.exclusions import (
    CombinedDetector,
    FakeDetector,
    MeetingDetector,
    NullDetector,
    Reason,
)
from pomodoro_guardian.state import DailyState, load, save
from pomodoro_guardian.timer import Event, PomodoroEngine, State

UTC = timezone.utc


def at(hour: float) -> datetime:
    return datetime(2026, 8, 10, tzinfo=UTC) + timedelta(hours=hour)


def block(start_hour: float, end_hour: float) -> BusyBlock:
    return BusyBlock(at(start_hour), at(end_hour))


# -- meeting detection (SPEC §4A) -------------------------------------


def test_a_meeting_in_progress_is_found():
    schedule = Schedule((block(9, 10),))
    assert schedule.meeting_at(at(9.5)) is not None


def test_nothing_is_found_outside_a_meeting():
    schedule = Schedule((block(9, 10),))
    assert schedule.meeting_at(at(10.5)) is None


def test_a_meeting_ends_exclusively():
    """The instant a meeting ends it is over — no lingering suppression."""
    schedule = Schedule((block(9, 10),))
    assert schedule.meeting_at(at(10)) is None


def test_a_day_off_block_is_not_a_meeting():
    """The rule that stops a vacation suppressing breaks for 24 hours.

    docs/SPEC.md §5: full-day busy blocks mark a day off. Treating one as a
    meeting would hold breaks off all day — the exact opposite of the
    reduced cap's intent.
    """
    vacation = Schedule((block(0, 24),))
    assert vacation.meeting_at(at(12), day_off_hours=6.0) is None

    holiday = Schedule((block(9, 18),))   # the measured 9h holiday shape
    assert holiday.meeting_at(at(12), day_off_hours=6.0) is None


def test_a_long_but_sub_threshold_meeting_still_counts():
    schedule = Schedule((block(9, 14),))   # 5h, under the 6h threshold
    assert schedule.meeting_at(at(12), day_off_hours=6.0) is not None


def test_overlapping_meetings_suppress_until_the_last_one_ends():
    """Double-booked slots must not un-suppress at the first end time."""
    schedule = Schedule((block(9, 10), block(9.5, 11)))
    found = schedule.meeting_at(at(9.75))

    assert found is not None
    assert found.end == at(11)


# -- the watcher ------------------------------------------------------


def test_no_url_means_no_meetings_and_no_thread():
    watcher = CalendarWatcher(None)
    watcher.start()   # must be a harmless no-op

    assert not watcher.configured
    assert watcher.meeting_now() is None
    assert "no calendar" in watcher.status()


def test_an_unfetched_watcher_reports_no_meeting():
    """Missing data must enforce breaks, never silently suppress them."""
    watcher = CalendarWatcher("https://example.com/basic.ics")
    assert watcher.meeting_now(at(9.5)) is None


def test_a_stale_cache_is_ignored():
    """A meeting cancelled hours ago must stop suppressing breaks."""
    watcher = CalendarWatcher("https://example.com/basic.ics", max_age_seconds=60)
    watcher._schedule = Schedule((block(9, 10),))
    watcher._fetched_at = 0.0   # monotonic zero: far in the past

    assert watcher.meeting_now(at(9.5)) is None
    assert "STALE" in watcher.status()


def test_a_fresh_cache_is_used():
    import time

    watcher = CalendarWatcher("https://example.com/basic.ics")
    watcher._schedule = Schedule((block(9, 10),))
    watcher._fetched_at = time.monotonic()

    assert watcher.meeting_now(at(9.5)) is not None


# -- meeting skip as an exclusion -------------------------------------


class StubWatcher:
    def __init__(self, meeting=None):
        self.meeting = meeting

    def meeting_now(self, when=None):
        return self.meeting


def test_meeting_detector_excludes_and_says_when_it_ends():
    result = MeetingDetector(StubWatcher(block(9, 10))).check()

    assert result.reasons == (Reason.MEETING,)
    assert "until" in result.describe()


def test_meeting_detector_is_clear_with_no_meeting():
    assert not MeetingDetector(StubWatcher()).check().active


def test_combined_detector_merges_reasons_from_every_source():
    devices = FakeDetector()
    devices.set(Reason.MICROPHONE, detail="chrome.exe")
    combined = CombinedDetector(devices, MeetingDetector(StubWatcher(block(9, 10))))

    result = combined.check()

    assert set(result.reasons) == {Reason.MICROPHONE, Reason.MEETING}
    assert "chrome.exe" in result.describe()


def test_combined_detector_is_clear_when_all_sources_are():
    combined = CombinedDetector(NullDetector(), MeetingDetector(StubWatcher()))
    assert not combined.check().active


def test_combined_detector_does_not_repeat_a_shared_reason():
    a, b = FakeDetector(), FakeDetector()
    a.set(Reason.MICROPHONE)
    b.set(Reason.MICROPHONE)

    assert CombinedDetector(a, b).check().reasons == (Reason.MICROPHONE,)


# -- the daily skip budget (SPEC §4B) ---------------------------------


def test_budget_starts_whole_and_shrinks_as_it_is_spent():
    budget = 60 * MINUTE
    state = DailyState.for_today()

    assert state.skip_remaining(budget) == budget
    spent = state.with_skip(20 * MINUTE)
    assert spent.skip_remaining(budget) == 40 * MINUTE


def test_only_whole_skips_are_offered():
    """A partial 20-minute skip would be a lie about what you bought."""
    budget = 60 * MINUTE
    state = DailyState.for_today().with_skip(50 * MINUTE)

    assert state.can_skip(5 * MINUTE, budget)
    assert not state.can_skip(20 * MINUTE, budget)


def test_an_exhausted_budget_allows_nothing():
    budget = 60 * MINUTE
    state = DailyState.for_today().with_skip(60 * MINUTE)

    assert state.skip_remaining(budget) == 0
    assert not state.can_skip(5 * MINUTE, budget)


def test_the_budget_rolls_over_on_a_new_day():
    yesterday = DailyState(date(2026, 8, 9), custom_skip_used=60 * MINUTE)
    today = yesterday.rolled_to(date(2026, 8, 10))

    assert today.custom_skip_used == 0
    assert today.day == date(2026, 8, 10)


def test_state_from_yesterdays_file_starts_clean(tmp_path):
    path = tmp_path / "state.json"
    save(DailyState(date(2026, 8, 9), custom_skip_used=45 * MINUTE), path)

    assert load(path, today=date(2026, 8, 10)).custom_skip_used == 0


def test_state_from_todays_file_is_preserved(tmp_path):
    path = tmp_path / "state.json"
    save(DailyState(date(2026, 8, 10), custom_skip_used=45 * MINUTE), path)

    assert load(path, today=date(2026, 8, 10)).custom_skip_used == 45 * MINUTE


def test_a_corrupt_state_file_does_not_stop_the_app(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json at all", encoding="utf-8")

    assert load(path, today=date(2026, 8, 10)).custom_skip_used == 0


@pytest.mark.parametrize("bad", ["lots", None, True, -5])
def test_a_malformed_tally_reads_as_zero(tmp_path, bad):
    import json

    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"day": "2026-08-10", "custom_skip_used_seconds": bad}),
        encoding="utf-8",
    )

    assert load(path, today=date(2026, 8, 10)).custom_skip_used == 0


# -- deferring a break ------------------------------------------------


class Harness:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.now = 1000.0
        self.last_input = 1000.0
        self.engine = PomodoroEngine(self.config, now=1000.0)
        self.events: list[Event] = []

    def advance(self, seconds, active=True, step=1.0):
        target = self.now + seconds
        while self.now < target - 1e-9:
            self.now = min(self.now + step, target)
            if active:
                self.last_input = self.now
            self.events.extend(self.engine.update(self.now, self.last_input))
        return self.events

    def advance_until(self, event, limit, active=True):
        spent = 0.0
        while spent < limit:
            before = len(self.events)
            self.advance(1.0, active=active)
            if event in self.events[before:]:
                return self.now
            spent += 1.0
        pytest.fail(f"{event} did not fire within {limit}s")


def test_deferring_returns_to_work_with_exactly_the_bought_time():
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)

    h.engine.defer_break(5 * MINUTE, h.now)

    assert h.engine.state is State.WORK
    assert h.engine.snapshot().remaining == pytest.approx(5 * MINUTE, abs=1.0)


def test_the_break_returns_when_the_bought_time_runs_out():
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.engine.defer_break(5 * MINUTE, h.now)
    h.events.clear()

    h.advance_until(Event.BREAK_STARTED, limit=5 * MINUTE + 30)

    assert h.engine.state is State.BREAK


def test_a_skipped_break_does_not_count_toward_the_long_break():
    """Skipping is not taking. It must not bring the long break closer."""
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    assert h.engine.completed_cycles == 0

    h.engine.defer_break(5 * MINUTE, h.now)

    assert h.engine.completed_cycles == 0


def test_the_warning_still_precedes_a_deferred_break():
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)
    h.engine.defer_break(10 * MINUTE, h.now)
    h.events.clear()

    h.advance_until(Event.WARNING_STARTED, limit=10 * MINUTE)

    assert h.engine.state is State.WARNING


def test_deferring_longer_than_the_interval_is_clamped():
    h = Harness()
    h.advance_until(Event.BREAK_STARTED, limit=2000)

    h.engine.defer_break(99 * MINUTE, h.now)

    assert h.engine.snapshot().remaining <= h.config.work_duration + 1
