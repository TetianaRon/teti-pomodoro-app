"""Tests for the history log (SPEC §8).

Two purposes, both covered: rolling a day up into something readable, and
keeping an audit trail of the app's own decisions so a slow accounting
bug is visible after the fact rather than gone at midnight.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pomodoro_guardian import history as H


@pytest.fixture()
def log(tmp_path):
    return H.History(tmp_path / "history.db")


def at(day: date, hour: int = 12) -> datetime:
    """A UTC instant whose *local* date is `day`, which is how rows group."""
    moment = datetime.combine(day, datetime.min.time(), timezone.utc)
    moment += timedelta(hours=hour)
    # Nudge until the local date matches, so the test holds in any timezone.
    while moment.astimezone().date() < day:
        moment += timedelta(hours=1)
    while moment.astimezone().date() > day:
        moment -= timedelta(hours=1)
    return moment


MONDAY = date(2026, 8, 10)


def test_an_empty_log_summarises_to_zero(log):
    summary = log.summary(MONDAY)
    assert summary.worked_seconds == 0
    assert summary.breaks_taken == 0


def test_breaks_are_counted(log):
    for _ in range(3):
        log.record(H.BREAK_TAKEN, detail="short", when=at(MONDAY))
    assert log.summary(MONDAY).breaks_taken == 3


def test_skips_are_counted_with_their_time(log):
    log.record(H.BREAK_SKIPPED, seconds=300, when=at(MONDAY))
    log.record(H.BREAK_SKIPPED, seconds=600, when=at(MONDAY))

    summary = log.summary(MONDAY)

    assert summary.breaks_skipped == 2
    assert summary.skipped_seconds == 900


def test_snapshots_are_cumulative_not_additive(log):
    """Totals are a running figure; adding them would double-count."""
    log.snapshot(3600, 600)
    log.snapshot(7200, 1200)

    summary = log.summary(date.today())

    assert summary.worked_seconds == 7200
    assert summary.walked_seconds == 1200


def test_days_are_grouped_by_local_date(log):
    """"How long did I work on Tuesday" means the Tuesday you lived."""
    log.record(H.BREAK_TAKEN, when=at(MONDAY))
    log.record(H.BREAK_TAKEN, when=at(MONDAY + timedelta(days=1)))

    assert log.summary(MONDAY).breaks_taken == 1
    assert log.summary(MONDAY + timedelta(days=1)).breaks_taken == 1


def test_the_day_classification_is_kept(log):
    """The audit trail: a day judged wrongly is otherwise invisible later."""
    log.record(
        H.DAY_CLASSIFIED, seconds=39600,
        detail="working day (weekday); cap 11.00h", when=at(MONDAY),
    )
    assert "working day" in log.summary(MONDAY).day_type


def test_emergency_and_focus_are_counted(log):
    log.record(H.EMERGENCY_USED, seconds=3600, when=at(MONDAY))
    log.record(H.FOCUS_STARTED, when=at(MONDAY))

    summary = log.summary(MONDAY)

    assert summary.emergency_used == 1
    assert summary.focus_used == 1


def test_recent_days_are_newest_first(log):
    days = log.recent_days(3, today=MONDAY)
    assert [d.day for d in days] == [
        MONDAY, MONDAY - timedelta(days=1), MONDAY - timedelta(days=2)
    ]


def test_the_raw_tail_is_newest_first(log):
    for n in range(5):
        log.record(H.BREAK_TAKEN, detail=str(n))

    rows = log.tail(3)

    assert len(rows) == 3
    assert [row[3] for row in rows] == ["4", "3", "2"]


def test_a_dict_detail_is_stored_readably(log):
    log.record(H.SNAPSHOT, detail={"worked": 10, "walked": 2})
    assert '"worked": 10' in log.tail(1)[0][3]


def test_describe_mentions_what_happened(log):
    log.snapshot(4 * 3600, 30 * 60)
    log.record(H.BREAK_TAKEN)
    log.record(H.BREAK_SKIPPED, seconds=600)

    described = log.summary(date.today()).describe()

    assert "4.0h worked" in described
    assert "30 min walked" in described
    assert "skipped" in described


def test_a_broken_database_never_raises(tmp_path):
    """Losing a row is a footnote; taking the app down is not."""
    path = tmp_path / "history.db"
    path.write_bytes(b"this is not a database")

    log = H.History(path)
    log.record(H.BREAK_TAKEN)          # must not raise
    log.snapshot(1.0, 2.0)             # must not raise

    assert log.summary(MONDAY).breaks_taken == 0
    assert log.tail(5) == []


def test_the_log_survives_being_reopened(tmp_path):
    path = tmp_path / "history.db"
    H.History(path).record(H.BREAK_TAKEN, when=at(MONDAY))

    assert H.History(path).summary(MONDAY).breaks_taken == 1
