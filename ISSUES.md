# Issues Log — Pomodoro Guardian

Local issues log for this project (per contributor decision: a local file
Claude writes to directly, not a governed Jira/Confluence log). Logs
workflow issues Claude notices — incorrect output, workflow gaps,
redundant steps, routing failures, documentation drift, missing
guardrails — and anything worth remembering about this specific dev
environment.

Newest first.

## Open

### 2026-08-10 — A dev run wrote into the live history log
Verifying the cycle-resume work meant building a real `Application` against
a scratch `state.json`. It picked up the scratch state as instructed and
then opened the **live** `history.db` anyway, because
`Application.__init__` hardcoded `settings_module.default_path()` and
ignored both `state_file` and `--config`. Three rows landed in a real
working day: a `cycles_resumed`, an `app_stopped` that never happened, and
a `snapshot` of `{"worked": 0, "walked": 0}`.

The zeroed snapshot was the damaging one. `History.summary()` resolves
`walked` as last-row-wins, so today's walking total read **0 min** until
the running app wrote its next real snapshot five minutes later — the exact
class of silent accounting error the log exists to catch, introduced by the
tooling meant to check it. All three rows were deleted by id after being
printed for review, and `--history 1` was confirmed back to 4.8h / 69 min.

**Fixed in code:** `Application` now takes `settings_file`, so `--config`
moves the whole data directory rather than half of it; `report_exclusions`
had the same split and was fixed with it.

**Guardrail, not yet automated:** nothing stops the next dev run from
pointing at the default paths. Before constructing an `Application` outside
the test suite, assert its `history.path` and state file are both inside a
scratch directory — the smoke script in the scratchpad does this now, and
it is the check that would have caught this before the write, not after.

### 2026-08-09 — Git commit identity was never written to local config
The Session 2 bridge run set the commit identity per-command rather than
with `git config --local`, so the repo's local config was empty and
Session 3's commits silently fell through to the global identity
(`tetiana.ronska@laivly.com`). GitHub rejected the push with `GH007: Your
push would publish a private email address` — after the commits were
already made, not at commit time.

Fixed by writing the identity to local config and re-authoring the two
unpushed commits (`git rebase --exec 'git commit --amend --no-edit
--reset-author'`). Local config now holds
`113524945+TetianaRon@users.noreply.github.com`, so this shouldn't recur
in this clone — but a fresh clone would hit it again, since local config
isn't cloned. Worth checking `git config --local user.email` before the
first commit in any new clone.

### 2026-08-09 — No Python interpreter on the dev machine
Phase 1 was written before anyone noticed the machine had no Python — only
the Microsoft Store alias stubs on `PATH`. Installed Python 3.12.10 via
`winget` with contributor approval, plus a repo-local `.venv` (gitignored)
holding `requirements.txt` + `pytest`. Worth a `README.md` setup section so
the next machine doesn't rediscover this.

Run tests with: `.venv\Scripts\python.exe -m pytest tests`

## Resolved

### 2026-08-09 — `laivly-global-session` unavailable in native Claude Code
`CLAUDE.md` declared this skill a **hard gate** ("load it before engaging
with task content"), but it is not installed on this machine — absent from
the session's skill registry, with no `~/.claude/skills/` or
`.claude/skills/` directory anywhere. It appears to have been a
Cowork-side skill that a native Claude Code session cannot see, so the
gate was unsatisfiable exactly where the app code has to be written. Two
sessions hit it and proceeded with contributor approval.

**Resolved 2026-08-10 by the second of the two fix options** — the
contributor chose to drop the dependency rather than install the skill. The
practices it governed (plan maintenance, write verification, out-of-scope
declaration, wrap-up format) are now written out in `CLAUDE.md` under
"Session practices", and the two dangling references in
`pomo-task-build-phase.md` §3/§6 point there instead. **This project now
requires no skill to work on.**

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
