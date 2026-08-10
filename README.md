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

## Running it every day

Use the tray menu's **Start with Windows**. That drops a shortcut in your
Startup folder pointing at the venv's `pythonw.exe`, so it launches at login
with no console window, and picks up code changes on the next start with
nothing to rebuild.

An unsigned PyInstaller build was tried first and blocked outright by the
machine's security tooling — a well-known false positive, since the
bootloader pattern is shared with real packers. `pomodoro-guardian.spec` is
kept and does work, but only matters if the app ever has to run somewhere
without a Python environment.

The tray icon starts in the overflow area (`^`). Drag it onto the taskbar to
pin it; Windows remembers that across restarts. Apps cannot promote
themselves out of the overflow — that choice is deliberately the user's.

Logs go to the console when run from a terminal, and to
`%APPDATA%\PomodoroGuardian\pomodoro.log` when there isn't one.

## Break chimes

Drop `.mp3` or `.wav` files into `assets/sound-effects/` and pick one for the
start and end of a break in the settings window, each with a Play button to
check the level. `--test-sounds` lists what it found and plays both.

That folder is gitignored: the clips are third-party stock audio, so the app
discovers whatever is present rather than depending on particular files. With
none installed, breaks are simply silent.

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
