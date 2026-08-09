# Issues Log — Pomodoro Guardian

Local issues log for this project (per contributor decision: a local file
Claude writes to directly, not a governed Jira/Confluence log). Logs
workflow issues Claude notices — incorrect output, workflow gaps,
redundant steps, routing failures, documentation drift, missing
guardrails — and anything worth remembering about this specific dev
environment.

Newest first.

## Open

### 2026-08-09 — `laivly-global-session` unavailable in native Claude Code
`CLAUDE.md` declares this skill a **hard gate** ("load it before engaging
with task content"), but it is not installed on this machine — absent from
the session's skill registry, with no `~/.claude/skills/` or
`.claude/skills/` directory anywhere. It appears to have been a
Cowork-side skill that a native Claude Code session cannot see, so the
gate is unsatisfiable exactly where the app code has to be written.

Session 3 proceeded with contributor approval, following the skill's
practices as already documented in `CLAUDE.md` and
`pomo-task-build-phase.md` §3/§6 (plan maintenance, post-write
verification, out-of-scope declaration, wrap-up format).

**Fix options:** install the skill to `~/.claude/skills/`, or inline the
handful of practices it governs into `CLAUDE.md` and drop the hard-gate
wording. Until one happens, every native session hits this wall on its
first move.

### 2026-08-09 — No Python interpreter on the dev machine
Phase 1 was written before anyone noticed the machine had no Python — only
the Microsoft Store alias stubs on `PATH`. Installed Python 3.12.10 via
`winget` with contributor approval, plus a repo-local `.venv` (gitignored)
holding `requirements.txt` + `pytest`. Worth a `README.md` setup section so
the next machine doesn't rediscover this.

Run tests with: `.venv\Scripts\python.exe -m pytest tests`

## Resolved

### 2026-08-09 — Cowork device-bridge git lock friction
Every git command run against this repo through the Cowork device bridge
(`device_bash`) leaves a stale `.git/index.lock` (and sometimes
`.git/HEAD.lock`) behind, because the bridge cannot delete files — only
move them. The next git command then fails with `Unable to create
'.git/index.lock': File exists` until the stale lock is moved aside.
Workaround documented in `CLAUDE.md` and `pomo-task-build-phase.md`.
Not expected to affect git run natively on this machine outside the
bridge — worth confirming the first time a native Claude Code session
runs git here.

**Confirmed bridge-only (Session 3, native Claude Code).** Roughly a dozen
git commands ran natively — `status`, `log`, `push`, `add`, `commit` —
and left no new lock files. The caveat in `CLAUDE.md` can stay as-is for
bridge sessions; it does not apply natively.

One carry-over, though: the bridge's *last* session left a stale
`.git/HEAD.lock` behind that survived into this one. Read-only commands
and even `git push` ignored it, so it stayed invisible until the first
`git commit` would have failed on it. Moved to
`.git_lock_leftovers/HEAD.lock.native-session3`. Worth a glance at
`ls .git/*.lock` at the start of any session that follows a bridge
session, rather than waiting for a commit to trip over it.
