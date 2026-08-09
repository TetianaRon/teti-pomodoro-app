# Pomodoro Guardian — Project Plan

## North Star

A Windows desktop app that auto-detects active work and enforces Pomodoro-style breaks with a real full-screen lock, with a small number of deliberately capped escape hatches (custom skip, Emergency Mode, Focus Mode) so the app — not in-the-moment willpower — is what actually holds the line. Also tracks a daily standing-desk/walking goal, independent of the break cycle.

## Phases

1. ⏳ Core loop: activity detection → Pomodoro timer → full-screen lock overlay
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
- **Dev/documentation location:** developed and version-controlled in this Claude session's workspace; current files mirrored out to `C:\Users\tetiana.ronska\repos\pomodoro-app` on the contributor's machine via the device bridge after each meaningful change.

## Open items

- Exact daily work cap: 10h vs 11h (default 10.5h pending a decision).
- Long-break-every-4th-cycle reset rule: fixed daily reset time vs. reset after any idle gap.
- App name/branding.
- First-run setup flow details (Google OAuth consent screen, etc.).

## Session Worklog

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
