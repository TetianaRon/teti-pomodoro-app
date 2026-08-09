"""Reading the work calendar's free/busy iCal feed.

Only what setup needs to *validate* a URL lives here: fetch it, parse the
busy blocks, and describe what came back. The day-off and meeting-skip
rules built on top of this are Phase 3/4 work (docs/SPEC.md §5).

The feed carries no titles, event types or all-day flags — every event is
`SUMMARY:Busy` — so this deliberately parses only the fields that are
actually present rather than pulling in a full iCalendar library.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone

USER_AGENT = "PomodoroGuardian/0.1 (+local personal use)"
DEFAULT_TIMEOUT = 15.0


class FeedError(Exception):
    """The URL could not be fetched, or what came back wasn't a calendar."""


@dataclass(frozen=True)
class BusyBlock:
    start: datetime
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@dataclass(frozen=True)
class FeedSummary:
    """What a fetched feed contains — enough to tell a good URL from a bad one."""

    event_count: int
    first_day: date | None
    last_day: date | None
    calendar_name: str | None
    timezone_name: str | None
    free_busy_only: bool   # every event is "Busy", i.e. no titles are exposed

    def describe(self) -> str:
        if not self.event_count:
            return "Fetched, but it contains no events."
        span = (
            f"{self.first_day} to {self.last_day}"
            if self.first_day and self.last_day
            else "unknown range"
        )
        detail = (
            "free/busy only, no event titles"
            if self.free_busy_only
            else "includes event titles"
        )
        return (
            f"{self.event_count} events, {span}\n"
            f"timezone {self.timezone_name or 'not stated'} — {detail}"
        )


def fetch(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Download the feed. Raises FeedError with something readable."""
    url = (url or "").strip()
    if not url:
        raise FeedError("No URL given.")
    if url.startswith("webcal://"):
        # Google hands these out for calendar subscriptions; the payload is
        # plain HTTPS.
        url = "https://" + url[len("webcal://"):]
    if not url.startswith(("http://", "https://")):
        raise FeedError("That doesn't look like a URL — it should start with https://")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FeedError(
                "404 — not found. Check the address was copied whole; a secret "
                "iCal URL ends in /basic.ics"
            ) from exc
        raise FeedError(f"Server returned {exc.code} {exc.reason}.") from exc
    except urllib.error.URLError as exc:
        raise FeedError(f"Could not reach it: {exc.reason}") from exc
    except OSError as exc:  # timeouts, TLS failures
        raise FeedError(f"Could not reach it: {exc}") from exc

    text = raw.decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        # Nearly always the ?cid= "add this calendar" link, which redirects to
        # a sign-in page rather than returning data.
        hint = (
            " That looks like a sign-in page — a link needing you to be logged "
            "in can't be read by an app. Use the *secret* iCal address from "
            "Settings > Integrate calendar."
            if "<html" in text[:2000].lower()
            else ""
        )
        raise FeedError("That URL didn't return calendar data." + hint)
    return text


def unfold(text: str) -> list[str]:
    """iCalendar wraps long lines; continuations start with a space or tab."""
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _property(lines: list[str], name: str) -> str | None:
    prefix = name + ":"
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def parse_busy_blocks(text: str) -> tuple[list[BusyBlock], list[str]]:
    """Every VEVENT as a UTC busy interval, plus the raw unfolded lines."""
    lines = unfold(text)
    blocks: list[BusyBlock] = []
    start = end = None
    summaries: list[str] = []
    inside = False

    for line in lines:
        if line == "BEGIN:VEVENT":
            inside, start, end = True, None, None
        elif line == "END:VEVENT":
            if inside and start and end:
                blocks.append(BusyBlock(start, end))
            inside = False
        elif inside and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";")[0]
            if key == "DTSTART":
                start = _parse_dt(value)
            elif key == "DTEND":
                end = _parse_dt(value)
            elif key == "SUMMARY":
                summaries.append(value)

    return blocks, summaries


def _parse_dt(value: str) -> datetime | None:
    """Parse DTSTART/DTEND. All-day values arrive as a bare date."""
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        parsed = datetime.strptime(value, "%Y%m%d")
        return parsed.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def summarize(text: str) -> FeedSummary:
    """Describe a fetched feed well enough to confirm it's the right one."""
    lines = unfold(text)
    blocks, summaries = parse_busy_blocks(text)
    days = sorted({b.start.date() for b in blocks})
    return FeedSummary(
        event_count=len(blocks),
        first_day=days[0] if days else None,
        last_day=days[-1] if days else None,
        calendar_name=_property(lines, "X-WR-CALNAME"),
        timezone_name=_property(lines, "X-WR-TIMEZONE"),
        # A free/busy feed reports every event as "Busy" and nothing else.
        free_busy_only=bool(summaries) and set(summaries) == {"Busy"},
    )


def check(url: str, timeout: float = DEFAULT_TIMEOUT) -> FeedSummary:
    """Fetch and describe, for the setup dialog's Test button."""
    return summarize(fetch(url, timeout))
