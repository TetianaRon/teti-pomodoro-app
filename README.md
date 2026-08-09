# Pomodoro Guardian

*Name confirmed 2026-08-09 — no longer a placeholder.*

A personal Windows desktop app that auto-detects active work and enforces
Pomodoro-style breaks with a real full-screen, input-blocking lock — not a
dismissible notification. A small set of deliberately capped escape hatches
(custom skip, Emergency Mode, Focus Mode) exist for genuine exceptions, and
a separate daily walking/standing-desk goal feeds back into the daily work
cap. See `docs/SPEC.md` for the full design and `PLAN.md` for build status,
architectural decisions, and the session worklog.

## Status

**Phase 1 (core loop) is complete:** activity detection, the Pomodoro timer,
and the full-screen lock overlay all work. None of the escape hatches exist
yet — no exclusions, skips, work cap, Focus Mode, or walking tracking, so
right now every break locks the screen unconditionally. Check `PLAN.md`'s
Phases list for what's next.

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

Windows only — the activity detector, lock overlay, and packaging all target
Windows specifically. Needs Python 3.12 (`winget install Python.Python.3.12`
if the machine has none; note that `python.exe` on `PATH` may be nothing but
a Microsoft Store alias stub).

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pytest pyflakes
```

## Running

```
.venv\Scripts\python.exe -m pomodoro_guardian                 # for real
.venv\Scripts\python.exe -m pomodoro_guardian --dry-run       # log only, never locks
.venv\Scripts\python.exe -m pomodoro_guardian --demo 60       # 25s work / 5s break
.venv\Scripts\python.exe -m pomodoro_guardian --setup         # settings window
```

The first run opens a setup window: the work calendar's iCal address (with
a Test button that fetches it and reports what it found), the rhythm, the
lock's safety release, the daily caps and the walking target. Skip it and
the defaults apply.

Settings persist to `%APPDATA%\PomodoroGuardian\config.json`, in minutes
and hours so the file can be edited by hand — `--config PATH` points
somewhere else. Detection thresholds (input gap, idle timeouts) live only
in that file, not in the window.

⚠️ The calendar's **secret iCal address is a credential** — anyone holding
it can read the whole calendar. It is stored outside the repo for exactly
that reason; don't paste it into a file here.

**The lock is real** — it covers every monitor and blocks keyboard and mouse.
Ctrl+Alt+Del still works and always will. While `--no-safety-unlock` is off
(the default), holding Escape for 3 seconds also releases it; that hatch
exists because the lock is young code, and is meant to be turned off once
you trust it.

## Tests

```
.venv\Scripts\python.exe -m pytest tests
```

The Pomodoro state machine takes its clock as an argument, so the whole
rhythm — 25-minute intervals, 4th-cycle long breaks, hour-long idle gaps —
is covered without a display or a wait. The lock overlay and the global
input hooks need a real Windows session and are smoke-tested by hand.

Worth running too, since this codebase has rarely-executed branches that
only fire on a live call or a real lock:

```
.venv\Scripts\python.exe -m pyflakes pomodoro_guardian tests
```
