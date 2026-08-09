# Pomodoro Guardian

*Working title only — rename anytime.*

A personal Windows desktop app that auto-detects active work and enforces
Pomodoro-style breaks with a real full-screen, input-blocking lock — not a
dismissible notification. A small set of deliberately capped escape hatches
(custom skip, Emergency Mode, Focus Mode) exist for genuine exceptions, and
a separate daily walking/standing-desk goal feeds back into the daily work
cap. See `docs/SPEC.md` for the full design and `PLAN.md` for build status,
architectural decisions, and the session worklog.

## Status

Project scaffold and requirements are complete; app implementation has not
started yet. Check `PLAN.md`'s Phases list for current status — phases are
built in order, starting with the core loop (activity detection → Pomodoro
timer → full-screen lock overlay).

## Structure

- `docs/SPEC.md` — full design spec (problem, core loop, exclusions,
  break-skip system, work cap + Emergency Mode, Focus Mode, walking goal,
  data tracked, tech stack, open items, build order).
- `PLAN.md` — phase tracker, architectural decisions, open items, and the
  session worklog.
- `CLAUDE.md` — instructions for Claude Code/Cowork sessions working in
  this repo.
- `pomo-task-build-phase.md` — the workflow for implementing or continuing
  a build phase.
- `ISSUES.md` — local issues log for workflow friction noticed while
  building this project.

## Tech stack

Python, packaged as a standalone Windows `.exe` via PyInstaller. Global
input activity detection (`pynput`/`pywin32`), the full-screen lock overlay
(`tkinter`/`PyQt`), Google Calendar reads (`google-api-python-client`), a
system tray icon (`pystray`), and a local SQLite history log — all local
only, no cloud sync.

## Setup

```
pip install -r requirements.txt
```

Windows only — the activity detector, lock overlay, and packaging target
Windows specifically.
