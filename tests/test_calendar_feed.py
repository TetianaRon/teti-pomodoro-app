"""Tests for reading the free/busy iCal feed.

Uses synthetic feeds shaped like the real export analysed on 2026-08-09:
every event `SUMMARY:Busy`, all timed, all UTC. The real file is
gitignored, so these can't depend on it.
"""

from __future__ import annotations

from datetime import date

import pytest

from pomodoro_guardian.calendar_feed import (
    FeedError,
    fetch,
    parse_busy_blocks,
    summarize,
    unfold,
)


def feed(*events: str, name="tetiana.ronska@laivly.com", tz="America/Toronto"):
    body = "\n".join(events)
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Google Inc//Google Calendar//EN\n"
        f"X-WR-CALNAME:{name}\nX-WR-TIMEZONE:{tz}\n{body}\nEND:VCALENDAR\n"
    )


def event(start: str, end: str, summary: str = "Busy"):
    return (
        f"BEGIN:VEVENT\nDTSTART:{start}\nDTEND:{end}\n"
        f"SUMMARY:{summary}\nEND:VEVENT"
    )


VACATION = event("20260717T040000Z", "20260718T040000Z")   # 24h, a day off
HOLIDAY = event("20260803T130000Z", "20260803T220000Z")    # 9h block
MEETING = event("20260804T140000Z", "20260804T143000Z")    # 30 min


def test_parses_each_event_as_a_busy_block():
    blocks, summaries = parse_busy_blocks(feed(VACATION, HOLIDAY, MEETING))

    assert len(blocks) == 3
    assert summaries == ["Busy", "Busy", "Busy"]
    assert [round(b.hours, 2) for b in blocks] == [24.0, 9.0, 0.5]


def test_summary_reports_range_name_and_timezone():
    summary = summarize(feed(VACATION, HOLIDAY, MEETING))

    assert summary.event_count == 3
    assert summary.first_day == date(2026, 7, 17)
    assert summary.last_day == date(2026, 8, 4)
    assert summary.calendar_name == "tetiana.ronska@laivly.com"
    assert summary.timezone_name == "America/Toronto"


def test_a_feed_of_only_busy_is_recognised_as_free_busy():
    assert summarize(feed(VACATION, MEETING)).free_busy_only


def test_a_feed_with_real_titles_is_not_free_busy():
    with_titles = feed(
        event("20260804T140000Z", "20260804T143000Z", "Sprint planning")
    )
    assert not summarize(with_titles).free_busy_only


def test_folded_lines_are_rejoined():
    """iCalendar wraps at 75 octets; continuations start with a space."""
    folded = "BEGIN:VEVENT\nSUMMA\n RY:Busy\nEND:VEVENT"
    assert "SUMMARY:Busy" in unfold(folded)


def test_an_empty_calendar_summarises_without_crashing():
    summary = summarize(feed())

    assert summary.event_count == 0
    assert summary.first_day is None
    assert "no events" in summary.describe()


def test_events_missing_an_end_are_skipped_not_fatal():
    partial = "BEGIN:VEVENT\nDTSTART:20260804T140000Z\nEND:VEVENT"
    blocks, _ = parse_busy_blocks(feed(partial, MEETING))
    assert len(blocks) == 1


def test_describe_mentions_the_free_busy_limitation():
    assert "no event titles" in summarize(feed(VACATION)).describe()


# -- fetch() input handling -------------------------------------------


@pytest.mark.parametrize("url", ["", "   ", None])
def test_empty_urls_are_rejected_before_any_network_call(url):
    with pytest.raises(FeedError, match="No URL"):
        fetch(url)


def test_a_non_url_is_rejected_with_a_useful_message():
    with pytest.raises(FeedError, match="start with https"):
        fetch("calendar.google.com/whatever")
