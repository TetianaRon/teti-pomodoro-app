# Issues Log — Pomodoro Guardian

Local issues log for this project (per contributor decision: a local file
Claude writes to directly, not a governed Jira/Confluence log). Logs
workflow issues Claude notices — incorrect output, workflow gaps,
redundant steps, routing failures, documentation drift, missing
guardrails — and anything worth remembering about this specific dev
environment.

Newest first.

## Open

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
