# Pomodoro Guardian

Personal Windows desktop app that auto-detects active work and enforces
Pomodoro-style breaks with a real full-screen, input-blocking lock — plus a
small set of deliberately capped escape hatches (custom skip, Emergency
Mode, Focus Mode) and a daily walking/standing-desk goal. Sole contributor:
Tetiana. Claude's role in this project is to implement, test, and maintain
the app's Python codebase against the phased build order below, keeping
`PLAN.md` and `docs/SPEC.md` current as design decisions evolve.

This project has no claude.ai Project surface — this repo is the canonical
home for all instructions, plan, spec, and code. There is no separate
project-knowledge copy to reconcile against.

## Production flow

Work proceeds phase by phase per `PLAN.md`'s phase list, in the build order
`docs/SPEC.md` §11 sets out. Each session picks up the next `⏳`/`🔄` phase,
implements it, verifies it, commits it, and updates `PLAN.md` before moving
on. Design decisions and open questions live in `docs/SPEC.md`; day-to-day
status and the worklog live in `PLAN.md`.

## Task routing

| Task | File |
| --- | --- |
| Implement or continue a build phase | `pomo-task-build-phase.md` |

For anything not covered by a task file — a one-off fix, a design-decision
change, a session close — work directly from `PLAN.md` / `docs/SPEC.md` and
this file's behavioral rules; no additional task file is needed for those.

## Skill invocations

- **`laivly-global-session`** — required, every session. Governs plan
  maintenance (`PLAN.md`'s worklog and phase status), file-write
  verification, out-of-scope declaration, and session wrap-up/handoff
  format. Hard gate: load it before engaging with task content.

## Behavioral rules

1. Never load a source speculatively — only what the current task requires.
2. Always confirm before creating artifacts outside this repo (cloud
   accounts, OAuth credentials, installed system dependencies).
3. Always confirm before modifying `docs/SPEC.md`, `PLAN.md`, or this file.
4. Any time a workflow issue is noticed — incorrect output, workflow gap,
   redundant step, routing failure, documentation drift, missing
   guardrail — ask whether to log it to `ISSUES.md` right away or hold it
   for later; then write the entry directly (this file is local and
   directly Claude-writable, unlike a governed Jira/Confluence issues log).

## Known environment caveat

When this repo is reached through the Cowork device bridge (not a native
Claude Code session on this machine), git commands leave a stale
`.git/index.lock` / `.git/HEAD.lock` behind because the bridge can't delete
files — only move them. If a git command fails with "Unable to create
'.git/index.lock': File exists", move the stale lock file aside (e.g. into
an untracked `.git_lock_leftovers/` folder) and retry. This does not affect
git run natively on this machine outside the bridge.
