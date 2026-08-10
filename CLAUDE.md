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

## Session practices

These were previously delegated to a `laivly-global-session` skill, which
does not exist on this machine — a hard gate no session could satisfy. The
practices it governed are inlined here instead, and no skill is required to
work on this project (2026-08-10).

1. **Keep `PLAN.md` current as you go.** Move a phase's status the moment it
   changes (`⏳` → `🔄` → `✅ complete YYYY-MM-DD`); never leave finished work
   marked in progress. Add a newest-first `## Session Worklog` entry saying
   what changed, what was decided, and what is next. If the work invalidates
   an earlier phase's notes, fix those too rather than leaving the plan
   half-current.
2. **Verify every file write landed** before building on it, and re-read
   anything written through a bridge rather than trusting the write.
3. **Declare what you did not do.** Out-of-scope findings, skipped checks and
   anything that needs the contributor's own hardware are stated plainly, not
   folded silently into a completion claim.
4. **Close with a handoff** a fresh session can start from: current state,
   how to run and test it, what is unverified, and the next move.

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

## Candidate for wider guidance (flagged, not yet actioned)

The device-bridge git-lock workaround above is generic to any Cowork
project reached through `device_bash` with a git repo — not specific to
this app. Flagged as a candidate for promoting into whatever cross-project
file-integrity guidance exists; not yet proposed or built. It stays in this
project's own instructions until then.
