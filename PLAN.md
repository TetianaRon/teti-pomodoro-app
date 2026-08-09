# Pomodoro Guardian — Project Plan

## North Star

A Windows desktop app that auto-detects active work and enforces Pomodoro-style breaks with a real full-screen lock, with a small number of deliberately capped escape hatches (custom skip, Emergency Mode, Focus Mode) so the app — not in-the-moment willpower — is what actually holds the line. Also tracks a daily standing-desk/walking goal, independent of the break cycle.

## Phases

1. ✅ Core loop: activity detection → Pomodoro timer → full-screen lock overlay — complete 2026-08-09
2. ⏳ Exclusions: video call / screen share detection
3. ⏳ Break-skip system (calendar meeting skip + capped custom skip)
4. ⏳ Daily work cap + Emergency Mode
5. ⏳ Focus Mode
6. ⏳ Walking/standing-desk manual tracking (toggle + 60 min/day tally + live effective work-cap formula)
7. ⏳ Local history log + tray summary view
8. ⏳ Packaging as Windows `.exe`, first-run setup flow

## Architectural decisions

- **Tech stack:** Python, packaged via PyInstaller to a standalone `.exe`.
- **Key libraries:** `pynput`/`pywin32` (activity detection), `tkinter`/`PyQt` (lock overlay), `google-api-python-client` (Calendar), `pystray` (tray icon/controls), `sqlite3` (local history).
- **Data:** local-only, no cloud sync.
- **Calendar provider:** Google Calendar.
- **Walking/standing-desk detection:** manual toggle only. Tapo-camera-based automatic detection (both desk-height classification and pose-estimation leg-motion detection) considered and explicitly ruled out — available camera views don't show the legs/under-desk area, a hardware constraint, not just an effort trade-off. See docs/SPEC.md §7.
- **Work-cap ↔ walking interaction:** the daily work cap is not fixed — it's a live "effective cap" = base cap − max(0, 60 − minutes walked today), recalculated continuously. Emergency Mode stacks an additional +1h on top of whatever the effective cap is at the time of activation (confirmed with contributor).
- **Weekday vs weekend base cap:** base cap is 10–11h on weekdays, **3h/day on weekends** (assumed Sat+Sun — flagged for confirmation). All other mechanics (effective-cap formula, Emergency Mode, its 3h/week shared budget) apply identically on both.
- **Timer as a pure state machine:** `pomodoro_guardian/timer.py` takes its clock as an argument and returns events instead of firing callbacks — no threads, no sleeping, no UI. Everything with a real duration (a 25-minute interval, a 4th-cycle long break, an hour-long idle gap) is therefore testable in milliseconds without a display or input hooks, which matters because every other part of Phase 1 needs a live Windows session to exercise.
- **Work is credited against input timestamps, not tick deltas:** the engine advances a watermark up to the last real keystroke. The 30s `input_gap` grace still bridges a pause in typing — it's paid retroactively when you resume — but silence is never credited on its own, and the first keystroke after the machine wakes can't buy back the hours it spent asleep. Two Phase 1 tests failed on exactly these before the fix.
- **Lock safety hatch:** `Config.safety_unlock` (on by default) releases the lock if you hold Escape for 3s. Enforcement is deliberately weakened while the lock is young — a bug in a 25-minute unattended lock strands you on your own machine. Turn it off via `--no-safety-unlock` once it's proven itself. Ctrl+Alt+Del is never blocked: that needs a kernel driver, which is the wrong trade for enforcing coffee breaks.
- **Dev environment:** Python 3.12.10 (winget) with a repo-local, gitignored `.venv`. Tests: `.venv\Scripts\python.exe -m pytest tests`.
- **Dev/documentation location:** this repo (`C:\Users\tetiana.ronska\repos\pomodoro-app`) is now the canonical, git-backed Claude Code/Cowork project — see `CLAUDE.md`. Work happens directly in this repo (via the Cowork device bridge or a native Claude Code session), not in a separate Claude-session workspace mirrored out afterward.

## Open items

- Exact daily work cap: 10h vs 11h (default 10.5h pending a decision). Needed by Phase 4.
- App name/branding.
- First-run setup flow details (Google OAuth consent screen, etc.).
- Confirm "weekend" = Saturday + Sunday, and that Emergency Mode's 3h/week budget is one pool across all 7 days. Both assumed in docs/SPEC.md §5; needed by Phase 4.
- Whether to keep `Config.safety_unlock` (hold Escape to release the lock) on. Currently on — see Architectural decisions.

### Resolved

- ~~Long-break-every-4th-cycle reset rule~~ — **resolved 2026-08-09: resets after an idle gap** (`Config.idle_reset_after`, default 60 min), not at a fixed daily time. Chosen to match the app's auto-detect premise: a genuine spell away from the desk starts a fresh set, whereas a midnight reset would carry a count across a long lunch and reset one mid-evening.

## Session Worklog

### Session 3 — 2026-08-09

First session in native Claude Code on the contributor's machine rather than through the Cowork device bridge. **Phase 1 built, tested, and committed** (`8648860`).

Pushed the backlog first: `git push -u origin main` succeeded immediately, so the six earlier commits are now on GitHub. The bridge's lack of network access was the only thing blocking it.

**Built** — `pomodoro_guardian/`, ~830 lines across six modules:
- `timer.py` — the rhythm as a pure state machine (see Architectural decisions).
- `activity.py` — `GetLastInputInfo` backend, which reads system-wide idle time with no hooks at all and is the right default; `pynput` event-listener fallback; scriptable fake for tests.
- `overlay.py` — the lock: borderless, always-on-top, sized to the virtual-screen bounding box so it covers every monitor, with global input suppression underneath. Re-asserts topmost every tick so nothing can climb above it mid-break.
- `app.py` — ticks the engine off the tkinter loop; `--dry-run` and `--demo FACTOR` make a full cycle observable without waiting half an hour.
- `config.py` — every duration in one frozen dataclass, since docs/SPEC.md §10 still has open values.

**Two real bugs, caught by tests, not by reading.** Both traced to one root cause: the 30-second `input_gap` grace was being counted as work. It let the start threshold be satisfied by silence (a 50s typing run plus 30s of grace tripped a 60s threshold), and it accrued 30s of every idle stretch into the work interval. Fixed by crediting work against a watermark of the last real keystroke. This also closed a bug no test had targeted yet: after a machine wakes from sleep, the first keystroke would have retroactively bought the entire time it spent asleep. **16/16 tests pass.**

**Verified on real hardware** — the part previous sessions could not do:
- `GetLastInputInfo` returns sane live idle times.
- Full cycle through work → warning → lock → unlock over four cycles, with the long break landing correctly on the 4th.
- The lock overlay actually locked: covered both monitors at exactly `-1920,0 → 5120x1080` (the contributor runs a dual-monitor setup with the second display to the *left* of the primary, so the geometry string carries a negative offset — verified Tk applies it rather than clamping to zero, which would have left that monitor exposed), stayed topmost, suppressed input, released cleanly. Whether keystrokes were genuinely swallowed from other apps was observed by the contributor, not measured here.

**Environment:** the machine had no Python at all — only the Microsoft Store alias stubs on `PATH`. Installed 3.12.10 via winget with approval, plus a gitignored `.venv` with `requirements.txt` + pytest. Logged in `ISSUES.md`.

**Decisions:**
- Long-break reset: **after an idle gap**, not a fixed daily time (closes a docs/SPEC.md §10 open item).
- Lock keeps a hold-Escape safety release while the code is young; Ctrl+Alt+Del stays available permanently by design.

**Workflow issues logged to `ISSUES.md`:** `laivly-global-session` is declared a hard gate in `CLAUDE.md` but isn't installed in native Claude Code — the gate is unsatisfiable exactly where the app code gets written; proceeded with contributor approval from the practices already documented in-repo. Also confirmed the device-bridge git-lock caveat is **bridge-only** (a dozen native git commands left no locks), but a stale `.git/HEAD.lock` from the previous bridge session survived into this one and would have failed the first commit — read-only commands and even `push` ignored it, so it stayed invisible until then.

**Next:** Phase 2 — video call / screen share detection (docs/SPEC.md §3). Worth doing before Phase 2 starts: run the app for a real 25-minute cycle to confirm the lock behaves unattended, and decide whether `safety_unlock` stays on.

### Session 2 — 2026-08-09

Ran PSB (Design - Project, MVP depth) to turn this repo into an actual Claude Code/Cowork project, per contributor request. Decisions:
- Project abbreviation: `pomo`.
- No standard tool connectors — repo-backed only (git + device bridge/native Claude Code).
- Issues log: local `ISSUES.md` in the repo, Claude-writable directly (not a governed Jira/Confluence log).
- No project-specific `qa-standards.md` — generic PSB Improve-mode standards are sufficient.

Created: `.gitattributes` (first commit, per repo-setup standard), `CLAUDE.md` (instruction layer — identity, task routing, `laivly-global-session` invocation, behavioral rules, environment caveat), `pomo-task-build-phase.md` (the one task file: how to implement/continue a build phase), `ISSUES.md`, `.gitignore`. Initialized git (`main` branch), set commit identity (Tetiana Ronska / GitHub noreply email, confirmed with contributor), committed in three commits (`.gitattributes` alone first, then the scaffold, then `.gitignore`).

**Environment finding — device-bridge git lock friction:** every git command run through the Cowork device bridge (`device_bash`) leaves a stale `.git/index.lock` (sometimes `.git/HEAD.lock`) behind, because the bridge can't delete files, only move them — the next git command then fails with `Unable to create '.git/index.lock': File exists` until the stale lock is moved aside. Confirmed the repo itself stays intact throughout (`git fsck` clean after each commit). Contributor chose to keep working through the bridge with this workaround rather than pause or switch to a native session. Documented in `CLAUDE.md`, `pomo-task-build-phase.md`, and `ISSUES.md` (open item) for future sessions; worth confirming whether it's a bridge-only issue the first time a native Claude Code session runs git in this repo.

Superseded Session 1's dev-location decision (mirror-out-after-each-change via device bridge) — replaced with working directly in this repo as noted in Architectural decisions above.

Added `README.md` and `requirements.txt`; wired the local repo to remote `git@github.com:TetianaRon/teti-pomodoro-app.git` (`git remote add origin`). The Cowork device bridge (`device_bash`) has no network access and no SSH credentials for the contributor's GitHub account, so the actual `git push` must be run by the contributor (or a native Claude Code session on their machine) — not something Claude can complete through this bridge. Also flagged the git-lock workaround itself as a `laivly-global-session` skill candidate directly in `CLAUDE.md`'s "Skill candidate" section, for a future PSB Improve pass to evaluate.

**Next:** begin Phase 1 (core activity-detection + Pomodoro timer + lock overlay) using `pomo-task-build-phase.md`.

### Session 1 — 2026-08-09

Gathered full requirements via structured Q&A:
- Detection: sustained keyboard/mouse activity auto-starts a work session.
- Rhythm: classic Pomodoro, 25 min work / 5 min break, long break every 4th cycle.
- Break enforcement: full-screen hard lock, 2-minute warning first.
- Exclusions (never interrupt): video calls, screen sharing — auto-detected.
- Break-skip: unlimited Google Calendar meeting-skip; capped custom skip (5/10/20 min, 60 min/day accumulated).
- Daily work cap: 10–11h tracked work/day; **Emergency Mode** overrides it by +1h per activation, capped at 3h/week — confirmed this extends the daily cap only and does **not** suspend break enforcement.
- Focus Mode: separate deep-work override, capped at 1/day, max 2h/use.
- Walking/standing-desk goal: 50 min/day. Initially explored Tapo-camera-based automatic detection (desk-high/low classifier); contributor determined this needs real movement/recognition work against the live feed and is more effort than it's worth right now — **descoped to a manual start/stop toggle for v1**, camera automation parked as a possible future phase.
- Platform: Windows desktop app. Tech stack: Python + PyInstaller (contributor had no preference; chosen for best fit against the system-level requirements).

Drafted spec v1, delivered to contributor for review; contributor confirmed Emergency Mode scope and requested the walking-desk section be simplified (done in v2, this session).

Contributor set the project's documentation/code home to `C:\Users\tetiana.ronska\repos\pomodoro-app` and asked that the `laivly-global-session` skill be applied to this project going forward (treating it as universal session discipline, not Laivly-specific). Adopting: living PLAN.md with worklog + phase status, out-of-scope declaration, file-write verification adapted to the device-bridge toolset (no direct mount access, so verification is via `device_list_dir` after each `device_commit_files` call rather than sandbox-side `tail`/`wc -c`).

Out-of-scope declaration passed: nothing excluded, full spec stands.

**Walking-goal refinement (still Session 1):** contributor proposed training a custom video classifier (their own walking vs. sitting footage) to auto-detect treadmill use. Evaluated two automated options — custom-trained classifier vs. pretrained pose-estimation (MediaPipe) — and recommended the latter as lower-effort/more-robust. Contributor then clarified both are moot: the available cameras (built-in laptop cam, and the Tapo) only see the upper body/desk-top area, not the legs or under-desk region needed for either approach. **Decision: camera automation ruled out on hardware grounds, not deferred.** Replaced with: manual start/stop toggle, target raised from 50 to 60 min/day, and a live proportional consequence tying the walking goal to the daily work cap — confirmed formula: `effective_cap = base_cap − max(0, 60 − minutes_walked_today)`, recalculated continuously, with Emergency Mode's +1h stacking on top of the result. Spec v2 and this plan updated accordingly.

Contributor added a weekend rule: **3h/day base cap on weekends**, all other mechanics unchanged. Assumed "weekend" = Saturday/Sunday and that Emergency Mode's 3h/week budget stays one shared pool across the full week rather than splitting weekday/weekend allowances — flagged in docs/SPEC.md §5/§10 for confirmation.

**Next:** begin Phase 1 (core activity-detection + Pomodoro timer + lock overlay).

**Session close:** contributor requested a handoff to a fresh session before implementation begins. Full requirements-gathering is complete; no code has been written yet. Handoff message produced per MODEL-HANDOFF.md format (not stored here — delivered directly to contributor to paste into the new session). Session 2 should add its own worklog entry below this one when it starts.
