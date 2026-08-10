# Pomodoro Guardian — Project Plan

## North Star

A Windows desktop app that auto-detects active work and enforces Pomodoro-style breaks with a real full-screen lock, with a small number of deliberately capped escape hatches (custom skip, Emergency Mode, Focus Mode) so the app — not in-the-moment willpower — is what actually holds the line. Also tracks a daily standing-desk/walking goal, independent of the break cycle.

## Phases

1. ✅ Core loop: activity detection → Pomodoro timer → full-screen lock overlay — complete 2026-08-09
2. ✅ Exclusions: video call / screen share detection — complete 2026-08-09, confirmed against a live Google Meet call
3. ✅ Break-skip system (calendar meeting skip + capped custom skip) — complete 2026-08-09
4. ✅ Daily work cap + Emergency Mode — complete 2026-08-09
5. ⏳ Focus Mode
6. ✅ Walking/standing-desk manual tracking (toggle + 60 min/day tally + live effective work-cap formula) — complete 2026-08-09
7. 🔄 Local history log + tray summary view — tray icon and menu built 2026-08-09; SQLite history log still to do
8. ⏳ Packaging as Windows `.exe`, first-run setup flow

## Architectural decisions

- **Tech stack:** Python, packaged via PyInstaller to a standalone `.exe`.
- **Key libraries:** `pynput`/`pywin32` (activity detection), `tkinter`/`PyQt` (lock overlay), `google-api-python-client` (Calendar), `pystray` (tray icon/controls), `sqlite3` (local history).
- **Data:** local-only, no cloud sync.
- **Calendar provider:** Google Calendar.
- **Walking/standing-desk detection:** manual toggle only. Tapo-camera-based automatic detection (both desk-height classification and pose-estimation leg-motion detection) considered and explicitly ruled out — available camera views don't show the legs/under-desk area, a hardware constraint, not just an effort trade-off. See docs/SPEC.md §7.
- **Work-cap ↔ walking interaction:** the daily work cap is not fixed — it's a live "effective cap" = base cap − max(0, 60 − minutes walked today), recalculated continuously. Emergency Mode stacks an additional +1h on top of whatever the effective cap is at the time of activation (confirmed with contributor).
- **Working vs non-working base cap (confirmed 2026-08-09):** **11h** on working days, **3h/day** on non-working days. A day is non-working if it's a Saturday or Sunday, *or* the work calendar shows it busy for its full length — which is how both company holidays and self-added vacation days appear. Emergency Mode's 3h/week is **one shared pool** across all 7 days. All other mechanics (effective-cap formula, Emergency Mode) apply identically on both.
- **Calendar access is free/busy only — confirmed against a real export (2026-08-09).** The work calendar is reachable as a secret iCal URL whose every event is literally `SUMMARY:Busy`: no titles, no event types, no all-day flags, all timed, all UTC. Every calendar-driven rule must therefore be expressible in busy intervals alone.
  - **Day-off rule, measured not guessed:** a day is non-working if its longest contiguous busy block (merged, in the calendar's own timezone) is **≥ 6 h**. From 706 events across 14 months: ordinary weekdays never exceed **1.5 h**, company holidays run **8.5–12.5 h** as a single ~09:00–18:00 block, and vacations are a **24 h** midnight-aligned block. Near-sixfold separation, zero false positives.
  - **Holidays and vacations look nothing alike.** An earlier draft of this rule looked for full-day coverage only; it would have caught vacations and silently missed every holiday. Caught by checking real data rather than reasoning about it.
  - **The same ≥6 h blocks must be excluded from §4A's meeting skip**, or a holiday would suppress breaks for 9 hours and a vacation day for 24 — the opposite of the reduced cap's intent. The two rules partition the same data and must never both fire on one block.
  - **No OAuth needed:** a plain HTTPS fetch plus an `.ics` parse. Removes the consent screen, `google-api-python-client`, and most of first-run setup. The secret URL is a credential, so it lives in gitignored local config, never the repo.
  - **Manual day-type override, asymmetrically budgeted (confirmed 2026-08-09, docs/SPEC.md §5a).** The ≥6 h rule can't tell a genuine all-day offsite from a holiday, so the day's classification is correctable by hand from the tray — but the two directions carry very different risk. *Lowering* (11h → 3h, for a sick day or an unbooked holiday) is self-restricting and unlimited. *Raising* (3h → 11h) is a bigger jump than Emergency Mode's +1h and, left free, would turn any weekend into an 11h day on demand — hollowing out the rule the app exists to enforce. Capped at **2 per calendar month**, today-only, expiring at local midnight, not refunded if cleared. Phase 4 work.
  - **`tzdata` is required on Windows** — no system IANA database, so `zoneinfo` can't resolve `America/Toronto` without it. Added to `requirements.txt`; PyInstaller must bundle it in Phase 8.
- **Timer as a pure state machine:** `pomodoro_guardian/timer.py` takes its clock as an argument and returns events instead of firing callbacks — no threads, no sleeping, no UI. Everything with a real duration (a 25-minute interval, a 4th-cycle long break, an hour-long idle gap) is therefore testable in milliseconds without a display or input hooks, which matters because every other part of Phase 1 needs a live Windows session to exercise.
- **Work is credited against input timestamps, not tick deltas:** the engine advances a watermark up to the last real keystroke. The 30s `input_gap` grace still bridges a pause in typing — it's paid retroactively when you resume — but silence is never credited on its own, and the first keystroke after the machine wakes can't buy back the hours it spent asleep. Two Phase 1 tests failed on exactly these before the fix.
- **Lock safety hatch, becoming the skip gesture:** `Config.safety_unlock` (on, confirmed 2026-08-09) releases the lock if you hold Escape for 3s. Originally a temporary weakening while the lock was young code — a bug in a 25-minute unattended lock strands you on your own machine — it is now **kept permanently** and repurposed: in Phase 3 the same gesture opens the 5/10/20-minute custom-skip menu instead of plainly unlocking (docs/SPEC.md §4B). Ctrl+Alt+Del is never blocked: that needs a kernel driver, which is the wrong trade for enforcing coffee breaks.
- **Exclusions read Windows' own signals, not process names (2026-08-09).** Camera/microphone use comes from CapabilityAccessManager's ConsentStore registry keys — the ones behind the tray privacy indicator, where a live device has `LastUsedTimeStop = 0` — and presenting comes from `SHQueryUserNotificationState`. Chasing conferencing executables would break on every new or renamed tool, and a running app says nothing about whether a call is happening. **`chrome.exe` is the case that settles it:** browser-based Meet and Teams calls are invisible to process matching since Chrome is always running, but "Chrome is holding the microphone" identifies them exactly. Only `QUNS_PRESENTATION_MODE`/`QUNS_BUSY` count as presenting — full-screen games and video players deliberately don't, or a maximised player could hold breaks off all evening.
- **Input suppression carries a UI-independent watchdog (2026-08-09).** A plain daemon thread holding no reference to tkinter force-releases suppression if a lock ever outlives its break by more than `Config.lock_max_overrun` (60s). Built during Phase 1 rather than deferred, because the shipped lock had no failsafe at all — a hung tick would have left input suppressed with no way out, the one failure a user cannot talk their way out of. The key distinction: it guards against **the app failing**, not against the user. It cannot be invoked, has no UI, and grants no discretionary escape — which is precisely what lets Phase 3 close the Escape route without losing any safety.
- **Settings live outside the repo (2026-08-09).** `%APPDATA%\PomodoroGuardian\config.json`, with a `--config` override for development. Correct for a packaged `.exe` — config beside the binary breaks on upgrade and is unwritable under Program Files — and it keeps the secret calendar URL, a credential granting read access to the whole work calendar, structurally unable to reach a GitHub repo. The file is written in **minutes and hours** rather than seconds because it is meant to be hand-edited; conversion happens in `settings.py` so the engine never handles units. `Settings` wraps `Config` rather than extending it, so `timer.py` still only ever sees the rhythm values and stays pure. Two deliberate robustness choices, both tested: a corrupt file falls back to defaults rather than stopping the app (losing break enforcement to a stray comma is the wrong trade), and one malformed value costs only that value, not its neighbours.
- **Dev environment:** Python 3.12.10 (winget) with a repo-local, gitignored `.venv`. Tests: `.venv\Scripts\python.exe -m pytest tests`.
- **Dev/documentation location:** this repo (`C:\Users\tetiana.ronska\repos\pomodoro-app`) is now the canonical, git-backed Claude Code/Cowork project — see `CLAUDE.md`. Work happens directly in this repo (via the Cowork device bridge or a native Claude Code session), not in a separate Claude-session workspace mirrored out afterward.

## Open items

- **Whether muting in native Zoom or Slack releases the microphone.** Not testable when Phase 2 was built; deferred by the contributor. The *detection mechanism* is already known to cover both — `Zoom.exe` and `91750D7E.Slack` appear in this machine's ConsentStore history, in the same two key formats the code was verified against — so nothing app-specific is at stake. The only question is whether their mute button releases the device or merely stops transmitting. Meet keeps it (measured), and that is the standard pattern. **If one of them releases it, the symptom is the screen locking during a muted call.** Mitigations already exist: Phase 3's calendar meeting-skip covers any *scheduled* call regardless of devices, the hold-Escape skip covers the moment, and `--no-exclusions` disables the mechanism outright. Check with `--exclusions` next time either is in use.

### Resolved

- ~~What holding Escape does once the skip budget is spent~~ — **resolved 2026-08-09: nothing, the break holds**, with durations greyed out so the exhausted budget is visible rather than the gesture appearing broken. The safety property moved to a watchdog instead — see below.

- ~~Whether to keep `Config.safety_unlock` on~~ — **resolved 2026-08-09: stays on**, and gains a second life. Rather than being a temporary hatch to switch off once the lock is trusted, the hold-Escape gesture becomes the *invocation* for Phase 3's capped custom skip: hold 3s → choose 5/10/20 min → the break is skipped and the daily budget debited. See docs/SPEC.md §4B, including the tension this creates.

- ~~First-run setup flow~~ — **built 2026-08-09**, ahead of its Phase 8 slot. A ttk window shown on first run or via `--setup`, persisting to `%APPDATA%\PomodoroGuardian\config.json`. See the settings decision below.

- ~~App name/branding~~ — **resolved 2026-08-09: Pomodoro Guardian.** Briefly renamed to "Pomo" and reverted; the contributor preferred the original. `pomo` remains the project/file prefix, as a shorthand rather than the app's name.
- ~~Exact daily work cap~~ — **resolved 2026-08-09: 11h** on working days, 3h on non-working days. Config values, not hardcoded.
- ~~"Weekend" = Saturday + Sunday?~~ — **confirmed 2026-08-09**, and extended: non-working days now also cover company holidays and vacations, read from the work calendar. See the calendar-access decision below.
- ~~Emergency Mode's 3h/week budget split?~~ — **resolved 2026-08-09: one shared pool** across all 7 days.
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

**Late-session decisions (contributor, after Phase 1 was committed):** weekday cap **11h**; weekend = **Sat + Sun**, extended to cover **holidays and vacations** from the work calendar; Emergency Mode budget is **one shared pool**. Then a correction: the work calendar is reachable only as **free/busy**, so no titles or event types are available.

**Calendar rule derived from real data, not reasoning.** The contributor first shared a `calendar.google.com/...?cid=` link; decoding it showed the `cid` was just the calendar ID (their email, base64) with no token, and fetching it returned a 302 to `accounts.google.com/ServiceLogin` — it is an "add this calendar" link for a signed-in human, carrying no data an app could read. The workable path is the **secret iCal address**, which they exported to `local/calendar.ics` (gitignored; `*.ics` and `local/` both covered).

Analysing that export — 706 events, 2026-06-10 → 2028-08-07 — produced the day-off rule now in docs/SPEC.md §5, and **corrected a rule that would have shipped broken**. The earlier draft looked for a busy block covering the whole day. That catches vacations (24 h, midnight-aligned) but **misses every company holiday**, which appear as ordinary ~09:00–18:00 blocks of 8.5–12.5 h. The replacement — longest contiguous merged block **≥ 6 h**, computed in the feed's own timezone — separates cleanly: ordinary weekdays never exceed 1.5 h across 384 samples, giving near-sixfold headroom and zero false positives. Two apparent false positives (2026-09-30, 2026-11-11) turned out to be National Day for Truth and Reconciliation and Remembrance Day.

Also found: **`tzdata` is required on Windows** (no system IANA database, so `zoneinfo` can't resolve `America/Toronto` — the first analysis attempt crashed on exactly this), and computing day boundaries in UTC misattributes evening events to the next day. Both now recorded; `tzdata` added to `requirements.txt` and flagged for PyInstaller bundling in Phase 8. The Google OAuth client libraries are commented out of `requirements.txt` — kept as a documented fallback rather than deleted.

Also confirmed the **long break is 15 min**, settling docs/SPEC.md §2.2's 15–20 min range — Phase 1 had implemented the bottom of that range as a default rather than a decision.

Closed the last calendar-driven open item by adding the **manual day-type override** (docs/SPEC.md §5a) — the resolution to the one limitation the ≥6h rule couldn't engineer away.

### Phase 1 verified end-to-end on real hardware (2026-08-09)

A full unattended 25-minute run, then targeted tests for the parts it couldn't cover. **Everything passed.**

**The timing was exact to the second.** Launched 04:09:12 → work started 04:10:13 (61s, the 60s threshold) → warning 04:32:13 (*exactly* 22:00 later) → lock 04:34:13 (*exactly* 2:00 after the warning) → Escape hold released it at 04:34:33. No `paused` events across 24 minutes of continuous typing, and zero drift — the watermark crediting tracks real input second-for-second.

Confirmed by the contributor:
- **Keyboard suppression genuinely reaches other apps.** Tested differentially: typing into Notepad while only the banner was up worked; typing during the lock produced nothing. This mattered because pynput uses *independent* keyboard and mouse listeners, so the already-confirmed mouse block did not imply it.
- **All three monitors show a correctly centred countdown.**
- **The Escape safety release works under live input suppression** — the riskiest untested claim in Phase 1, since a failure there means being stranded on your own machine.

**Two overlay bugs found, both by measuring rather than reading the code.** The 5-second smoke test had checked window *geometry* and never where the content landed:
1. Children were packed without `expand`, stacking against the top edge instead of centring; and a single window spanning the virtual screen centres on the bounding box, not on any real monitor. Fixed with one window per monitor.
2. The warning banner was **48 pixels wide** — its label was created empty and measured for placement before receiving text, so it anchored off bare padding, then grew past the primary monitor's edge onto the laptop panel. Invisible in practice. Fixing that surfaced a third: `show()` assigned `self._window` after calling `_reposition()`, which returns early on `None`, so the banner silently never moved from (0,0).

**Lesson worth carrying into later phases:** a UI smoke test that asserts on geometry while never inspecting where content actually renders will pass while the screen looks wrong. Assert on the rendered rect.

**Banner design settled:** click-through (`WS_EX_TRANSPARENT` + `WS_EX_NOACTIVATE`), readable at 90% by default and fading to 15% as the cursor approaches — a warning has two minutes to be noticed, so it should be visible, and hover is exactly when it should get out of the way. Hover is found by polling the cursor position, because a click-through window receives no mouse events and so never fires `<Enter>`/`<Leave>`.

### First-run setup built out of order (2026-08-09)

Contributor picked this up next, ahead of its Phase 8 slot. Defensible: the settings layer is foundational, nothing depends on it, and it gives `safety_unlock` a home other than a CLI flag. The cost is that the calendar URL it collects sits unused until Phase 3.

Added `settings.py` (persistence), `calendar_feed.py` (fetch and parse the free/busy feed) and `setup_dialog.py` (the ttk window), plus `--setup` and `--config PATH`. Test count went 16 → 41.

`calendar_feed.py` deliberately stops at *validation*: fetch, parse busy blocks, describe what came back. The day-off and meeting-skip rules built on it stay Phase 3/4 work. Its parser was checked against the real 706-event export and matched the throwaway probe script exactly — same event count, same date range, same 24h longest block on 2026-07-17.

The Test button matters more than it looks: a wrong URL fails immediately with a specific message instead of silently in Phase 3. Pasting the `?cid=` link — the exact mistake already made once this session — is detected and answered with a pointer to the secret address rather than a generic failure.

Verified end to end: the window opened, saved, and reloaded correctly, with the stored URL confirmed as a genuine `basic.ics` secret address.

### Session close — 2026-08-09

**Phase 1 complete and verified on real hardware; first-run setup and the settings layer built ahead of their Phase 8 slot. Every open item raised across all three sessions is now resolved.** 16 commits, 45 tests, working tree clean and pushed.

Decisions closed this session: weekday cap 11h · weekend = Sat+Sun plus calendar-driven holidays and vacations · Emergency Mode as one shared 3h/week pool · long break 15 min · app name kept as Pomodoro Guardian · manual day-type override, asymmetrically budgeted · calendar access is free/busy only, no OAuth · the ≥6h day-off rule derived from real data · `safety_unlock` kept permanently and repurposed as the Phase 3 skip gesture · what happens when the skip budget is spent.

**Two things worth carrying forward as habits, both of which caught real bugs today:**
1. **Assert on the rendered result, not the requested one.** The 5-second lock smoke test checked window geometry and passed through three separate layout bugs — content stacked at the top edge, centring computed against the virtual-screen bounding box, and a 48px banner spilling onto a second monitor.
2. **Check the data before writing the rule.** The day-off rule was drafted from reasoning about how all-day events "should" appear and would have shipped catching vacations while silently missing every company holiday. Ten minutes with the real export corrected it.

**Not done, and the reason to prioritise it:** Phase 2 (video call / screen share exclusions). Until it exists the app will lock mid-meeting, so it's the one thing standing between this and daily use.

**Next session:** start Phase 2 per `pomo-task-build-phase.md`. `docs/SPEC.md` §3 has the design; nothing blocks it.

### Session 4 — 2026-08-09

**Phase 2 built and unit-tested; one live check outstanding.** `exclusions.py` plus engine support for freezing, 62 tests (up from 45), and two new flags: `--exclusions` reports what is currently holding breaks off, `--no-exclusions` disables the mechanism.

The detection approach is the substance here — see Architectural decisions above for why it reads Windows' own signals rather than matching process names.

Three engine subtleties, each with a test:
- **A long call must not abandon the session.** Two hours on a call with no typing is well past `idle_reset_after`; without special handling the interval would be discarded the instant the call ended. Idle is therefore measured from when the exclusion lifted, not from the last keystroke.
- **Call time is never retroactively credited.** The work watermark is pinned during an exclusion, so the first keystroke after a meeting doesn't buy back its duration — the same class of bug as the sleep-wake one found in Phase 1.
- **A break already locked runs its full course.** You cannot join a call through a lock, and cutting a break short would be worse than letting it finish.

**Verified live against a real Google Meet call.** Five minutes of observation with the camera toggled and the mic muted: `chrome.exe` correctly identified holding both devices, the camera released within about a second of being switched off, and **the microphone stayed acquired for the whole call including while muted**. Phase 2 marked complete.

That settles a limitation this plan had speculated about — a listen-only group call with camera off and mic muted **is** detected, because software mute sets `track.enabled = false` without handing the device back. The contributor predicted this correctly; the worry was unfounded. Measured for Chrome/Meet only; native Zoom and Teams are expected to behave the same but haven't been checked.

**A NameError that would have crashed the app on the first real call.** `_join` was referenced in `WindowsDetector.check()` but never defined, so the detector raised the moment any device was actually in use. Nothing caught it: every unit test used `FakeDetector`, the earlier `--exclusions` run had nothing holding a device, and `py_compile` can't see a runtime name error. The failing branch needed the live path, which by definition had never run. Found only because the contributor asked to test with a real camera and call rather than trusting the review.

Fixed, plus the six tests that were missing (`WindowsDetector` exercised with devices in use via monkeypatched readers), and `pyflakes` added to the toolchain to hunt the same bug class — an undefined name in a rarely-executed branch. One other finding, an unused import, removed. **This is the third instance today of code that read correctly, passed its tests, and was wrong**; it is worth treating "has this line ever actually executed?" as a standing question for anything that only fires on live hardware.

**Accepted limitation:** an app that holds the microphone open silently disables break enforcement. After 2h of continuous exclusion the app now shows a standing corner banner naming what is holding the device, but does not override it — overriding would mean locking the screen during what might be a genuine call, the exact failure exclusions exist to prevent. Contributor confirmed calls should never be interrupted, so a stuck device is reported rather than overruled.

**Banner handling is now state-driven, not event-driven** — and that fixed a bug introduced earlier the same session. Hiding the banner on `EXCLUSION_STARTED` meant that if a call began during the 2-minute warning and ended while the warning was still running, the banner never came back. `_update_banner()` now decides from the current state each tick, so there is no transition to miss. The banner also grew a general `set_text()`/`notice()` API; verified it re-anchors correctly as the message widens, from 164px up to 733px, staying inside the primary monitor.

**Worth stating plainly, since it is easy to misread:** the 2h warning measures one *continuous* stretch, not a daily total — ordinary back-to-back meetings each start fresh and will never trigger it. And on a heavy meeting day the app does almost nothing by design: breaks do not accumulate, and none is owed when the calls end.

### Phase 3 complete — break-skip system (2026-08-09)

Two paths, deliberately different mechanisms. The **calendar meeting skip** is modelled as another exclusion source rather than new machinery — §3 and §4A both mean "do not lock right now" — so it reuses Phase 2's freezing through `CombinedDetector`. The **custom skip** *defers* a break instead: work resumes with exactly the bought time left, which is what makes the 60-minute daily budget mean anything. A skipped break doesn't count toward the long-break cycle, because skipping is not taking.

New modules: `calendar_watch.py` (background refresh, cached, stale data ignored), `state.py` (daily budgets, kept apart from settings so a corrupt tally can't cost you your configuration), `media.py`.

**Missing or stale calendar data enforces breaks normally.** A network problem must not silently switch enforcement off; the cost of failing this way is an unwanted lock, which the skip answers.

**Media is paused when the lock appears.** Four mechanisms were measured and three failed — see docs/SPEC.md §2.3. Only a real media keystroke works, and it is guarded by an audio check because the key is a *toggle*: fired into silence it would start playback on every quiet break.

**Four bugs, none visible to code review or unit tests.** All four needed a real lock and a real keyboard:
1. `NameError` in `WindowsDetector.check()` — only ran when a device was actually in use; would have crashed the app on the first call.
2. `root.after()` called from the pynput listener thread — worked while `mainloop()` ran, raised "main thread is not in main loop" otherwise. Input now posts to a queue drained on the UI thread.
3. Escape-hold detection depended on key **auto-repeat**; a single press could never fire it.
4. The hold that opened the skip menu immediately closed it again — Escape auto-repeats at roughly **10 events per second** (606 across one 60-second lock), so the next repeat read as "dismiss" inside the same drain loop.

**The diagnostic method mattered more than the diagnostics.** Three timed on-screen tests produced no usable data at all, because the prompts print to a console that is behind the lock. Only when the contributor ran `local/checklock.py` themselves — seeing prompts live, and the script reporting afterwards — did each stage become separable. Worth reusing for anything that only runs behind a lock.

**Calendar meeting skip verified live (2026-08-09)** against a placeholder event, in two passes. `--exclusions` reported the meeting through the real `CalendarWatcher` → `MeetingDetector` → `CombinedDetector` chain, and a second pass drove `Application._tick` directly: the detector resolved to `CombinedDetector`, the engine reported `excluded=True` on the first tick, and the log printed `holding off — meeting in progress (starting 20:20)` once rather than every tick. The engine correctly stayed in `IDLE` — while excluded it will not start a work session from activity.

The **10-minute lead window** was confirmed in the same run: the hold began before the meeting started, and the message distinguished the states ("starting 20:20" rather than "until 21:20"), which would otherwise have misdescribed a pre-meeting hold as the meeting itself.

Google's `basic.ics` published both the original and the rescheduled event within a couple of minutes each time, so feed lag is not the obstacle it was expected to be. **Phase 3 now has no unverified paths.**

### Phases 4, 6 and the tray — 2026-08-09

**Phase 4 (cap + Emergency Mode).** The literal spec would have switched the app off past the cap, meaning no breaks exactly when most tired. Instead the work interval drops to 5 minutes past the cap, with a persistent overtime marker — escalating pressure rather than an off-switch, decided with the contributor. `caps.py` keeps the rules pure like `timer.py`. Emergency Mode rides the existing hold-Escape menu and appears only once the cap is reached, so it can't be mistaken for a fourth skip length. Its 3h pool is a true rolling 7 days, not a calendar week, so it can't be gamed across a Sunday.

**Phase 6 (walking).** Manual toggle as SPEC §7 always specified, plus scheduled prompts (default 11:40 and 15:20) in a small non-blocking window. §7's shortfall formula is now live — it had been deliberately dormant, since subtracting an hour for an untracked goal would have cut every day short for nothing. A walk in progress counts before it is banked, and survives both a restart and midnight.

**Tray icon, brought forward from Phase 7.** Prompted by a good question: scheduled prompts are a *reminder*, not a tracker — a walk you decide on at 2pm needs a control you can reach. The tray also unblocked Phase 4's day-type override, which had working accounting and no way to be invoked. Menu clicks arrive on pystray's thread and are queued for the app's tick, the same arrangement the lock overlay uses. Left-click opens the menu via a small subclass remapping `WM_LBUTTONUP`; pinning the icon out of the overflow is a Windows user setting an app cannot control.

**Two bugs found by end-to-end runs, not review:**
- `emergency_hours_today()` compared a **UTC** grant timestamp against a **local** date. At 20:36 in Toronto it is already tomorrow in UTC, so a grant just made did not raise today's cap. It would have worked every morning and failed every evening.
- `pady=(18, 4)` is valid for `pack()` but not a widget constructor, which takes a single value.

**Still open:** Phase 5 (Focus Mode) is unbuilt; Phase 7's SQLite history log is unbuilt; Phase 8 packaging untouched. Settings apply unevenly by design — caps, walking and calendar values are re-read each tick, while rhythm and lock values are baked in at construction and need a restart, which the app says rather than silently ignoring half an edit.

**Next:** Phase 5 (Focus Mode, docs/SPEC.md §6) or Phase 7's history log. Both are smaller than what has just been built.

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
