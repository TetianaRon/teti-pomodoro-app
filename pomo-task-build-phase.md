# pomo-task-build-phase

How to implement or continue the next phase of the Pomodoro Guardian app.
One phase per session unless the contributor asks to keep going.

## 1. Orient

- Read `PLAN.md`'s Phases list — find the first `⏳` or `🔄` phase.
- Read the matching section of `docs/SPEC.md` for the design detail behind
  that phase. Read only what the current phase needs, not the whole spec.
- If any "Open items" in `PLAN.md` or `docs/SPEC.md` §10 block this
  specific phase, surface them before writing code. Non-blocking open
  items (per the last handoff) don't need to be re-litigated.

## 2. Implement

- Work directly in this repo — it is reached through the Cowork device
  bridge or a native Claude Code session, whichever is running.
- Match the architectural decisions in `PLAN.md` (tech stack: Python,
  PyInstaller packaging; `pynput`/`pywin32`, `tkinter`/`PyQt`, `pystray`,
  `sqlite3`, `google-api-python-client`).
- Do not implement later phases' mechanics early — e.g. Phase 1 (core
  loop) ships with no exclusions, skips, caps, Focus Mode, or walking
  tracking, even though the full design for those already exists in
  `docs/SPEC.md`.

## 3. Verify writes

- After every file write, verify it landed correctly before moving on
  (see `CLAUDE.md`'s session practices).
- On this repo's mount, prefer a bash heredoc or a Python whole-file write
  over line-based edits for multi-line files, and check byte count
  (`wc -c`) plus a `tail -c N | cat -A` spot check rather than trusting a
  line-based tail alone.

## 4. Test what's testable

- Add/extend automated tests for logic that doesn't depend on a live
  Windows session or a display (e.g. the Pomodoro timer state machine).
- Anything that needs a real Windows display, global input hooks, or an
  actual full-screen lock cannot be verified through this bridge — say so
  plainly, and note it as needing a manual smoke-test on the contributor's
  machine rather than claiming it's verified.

## 5. Commit

- `git add` the changed files and commit with a clear, specific message
  (what changed and why, not just "update files").
- If a git command fails with `Unable to create '.git/index.lock': File
  exists`, this repo's known environment caveat (see `CLAUDE.md`) applies —
  move the stale lock aside and retry.

## 6. Update the plan

- Mark the phase's status in `PLAN.md` (`⏳` → `🔄` → `✅ complete
  YYYY-MM-DD`) immediately when it changes — never leave a completed phase
  marked in progress.
- Add a `## Session Worklog` entry (newest-first) describing what was
  built, decisions made, and what's next — per `CLAUDE.md`'s
  plan-maintenance practice.
- If this phase's work affects an earlier, already-completed phase's
  decisions, update that phase's notes too — don't leave the plan
  half-current.
