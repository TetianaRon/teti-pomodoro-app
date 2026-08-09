# Pomodoro Guardian — App Design Spec (v2)

*Working title only — rename anytime. Windows desktop app.*

## 1. Problem this solves

You work long, unbroken stretches — including late evenings and nights — at the cost of exercise, walking the dog, and downtime. A phone-based Pomodoro app requires you to notice it, open it, and obey it, which fails exactly when discipline is already low. This app removes the "remember to use it" step: it watches for you actively working and takes over from there, enforcing breaks with a real screen lock rather than a dismissible notification.

## 2. Core loop

1. **Detection.** Sustained keyboard/mouse activity (past a short threshold, e.g. continuous input for ~1–2 minutes) starts a tracked work session automatically. No manual "start" needed.
2. **Rhythm.** Classic Pomodoro: 25 min work / 5 min break, with a longer break (15–20 min) every 4th cycle.
3. **Break enforcement.** When a break is due:
   - A 2-minute warning appears first (so you can save/wrap up a thought).
   - At zero, a full-screen, always-on-top, input-blocking overlay locks the screen for the break duration. This is a real block, not a reminder — closing/alt-tabbing away is not an escape hatch (see §6 for the one exception).
4. **Idle handling.** If you stop producing input (step away without triggering a break), the work session pauses; it does not silently keep counting toward your daily cap while you're not there.

**Thresholds as built (Phase 1, all in `pomodoro_guardian/config.py`):**

| Setting | Default | What it governs |
| --- | --- | --- |
| `input_gap` | 30 s | How long a pause in typing still counts as "at the desk". Bridged gaps are credited as work once you resume; silence beyond this never is. |
| `start_threshold` | 1 min | Span of sustained input needed to auto-start a session. Measured between first and last keystroke, so the grace period above can't satisfy it on its own. |
| `warning_lead` | 2 min | How far ahead of the lock the warning appears. |
| `idle_pause_after` | 2 min | When the session is shown as paused. Accrual has already stopped at the last keystroke; this is the user-visible signal. |
| `idle_reset_after` | 60 min | When the part-finished interval is discarded and the long-break cycle count resets. |

The lock blocks keyboard and mouse but deliberately **not Ctrl+Alt+Del** — blocking the Secure Attention Sequence needs a kernel driver. It stays as the last-resort exit, alongside closing the app (SPEC §5's intentional escape hatch). A `safety_unlock` setting additionally releases the lock on a 3-second Escape hold; it is on by default while the lock is new code and is expected to be turned off once proven.

## 3. Never-interrupt exclusions

The lock will not trigger — and an in-progress countdown pauses — while:
- A video call is active (auto-detected: Zoom/Teams/Meet etc. running with camera/mic in use, or the app in focus).
- Screen sharing / presenting is active.
- A calendar meeting is currently in progress (see §4 — this is the "meeting skip").

These are automatic; you don't have to invoke anything for them.

## 4. Break-skip system

Two distinct skip paths, both self-serve, no "prove it" friction:

**A. Meeting skip (calendar-based) — unlimited.**
If Google Calendar shows a meeting in progress at the moment a break would trigger, the break is suppressed for the meeting's duration automatically. No cap — trust-based and unlimited.

**B. Custom skip — capped.**
A manual "skip this break" action with a fixed choice of duration: 5 / 10 / 20 minutes. All custom skips share one **60-minutes-per-day accumulated cap**. Once the daily 60 minutes are used, no more custom skips are available that day — the lock enforces normally.

## 5. Daily work cap + Emergency Mode

- **Base cap: 11 hours** of tracked *work* time per working day (break time doesn't count toward this). Confirmed 2026-08-09.
- **Non-working-day base cap: 3 hours/day.** Confirmed 2026-08-09. A day is a non-working day if **either** holds:
  1. It is a **Saturday or Sunday**.
  2. The work calendar shows the day as **busy for its full length** — a single busy interval running midnight to midnight. Both company holidays (from the subscribed holiday calendar) and vacations (full-day events the contributor adds) present this way.

  Every other mechanic in this section and §7 applies identically on non-working days: same effective-cap formula, same Emergency Mode, same shared weekly budget.
- **Emergency Mode budget is one shared pool** of 3h across all 7 days — not split weekday/weekend. Confirmed 2026-08-09.

**Calendar access is free/busy only — this constrains the whole design.**
The work Google account exposes a shareable link that reports *busy intervals
and nothing else*: no titles, no event types, no `outOfOffice` flag,
no attendee lists. Every calendar-driven rule in this spec must therefore be
expressible in terms of busy intervals alone. In particular:

- **Detecting a day off** means recognising a busy interval that covers the
  entire day, since that is how an all-day event surfaces in free/busy. A day
  merely *packed* with meetings appears as several shorter intervals with gaps
  between them, so the two remain distinguishable. Worth validating against
  real data early in Phase 3 — especially how the shared link renders a
  multi-day vacation (one long interval, or one per day?) and which timezone
  the day boundaries land in.
- **Full-day busy intervals must be excluded from §4A's meeting skip.** §4A
  suppresses breaks whenever the calendar is busy; a midnight-to-midnight
  interval would match all day, so a vacation day would suppress break
  enforcement entirely — the opposite of what the reduced cap intends.
  Meeting-skip should consider only intervals materially shorter than a full
  day; the cap rule above considers exactly the ones it ignores.
- **The cap must degrade safely when the calendar is unreachable** (offline,
  stale link, first run before setup). Assumption unless told otherwise: fall
  back to the day-of-week rule alone — Sat/Sun reduced, everything else 11h —
  and surface that it is running without calendar data, rather than failing
  closed or silently granting a full cap on a holiday.
- **Effective cap:** the base cap, adjusted live by the walking shortfall described in §7 — see the formula there. This is the actual number of work-minutes the app will allow to start on a given day.
- Once the effective cap is hit, the app stops starting new work sessions from activity — you're done for the day as far as the app is concerned (unless Emergency Mode is used).
- **Emergency Mode:** the only way past the effective cap. Each activation grants **+1 hour**, stacking on top of whatever the effective cap is at that moment — forced breaks continue as normal during that extra hour; Emergency Mode is not a break-skip mechanism. Capped at **3 hours total per week** (so at most three activations, or any split adding up to 3h, per rolling week). No calendar or advance-scheduling requirement — a direct override, kept rare by the weekly cap rather than gated by a delay. If a genuinely extreme situation exceeds this budget, the intentional escape hatch is closing the app entirely — a deliberate act, not a casual one.

## 6. Focus Mode (deep-work override)

A separate, low-frequency override for real flow states that aren't calls/meetings:
- Suppresses forced breaks entirely while active.
- **Capped at 1 use per day, max 2 hours per use.**
- Distinct from Emergency Mode (§5) and the custom break-skip (§4B) — this exists specifically so a genuine flow state isn't forced to use up your break-skip budget.

## 7. Walking / standing-desk goal

Separate from break enforcement entirely (not a break variant): a **60 minutes/day** target for active use of your standing desk + treadmill setup, tracked as its own session type, not tied to the work/break cycle.

**Detection: manual toggle only.** A simple start/stop control (tray menu and/or a hotkey) starts and stops a walking session; elapsed time counts toward the 60 min/day target.

**Camera automation — explored and ruled out (not just deferred).** Two automated approaches were considered against the Tapo camera feed: a calibrated desk-height classifier, and pose-estimation-based leg-motion detection (e.g. MediaPipe). Both were rejected for a concrete hardware reason, not just effort: the available camera views only show the upper body / desk-top area, not the legs or the desk's actual under-desk position, so neither approach has the visual signal it needs to work reliably. This isn't a "maybe later, if effort allows" item — it's blocked on camera placement/coverage. It could be revisited only if a camera with a genuine full-body view became available.

**Consequence for missing the target — dynamic, proportional daily cap reduction.** This is the mechanism that gives the goal real teeth, and it's the one place walking tracking connects back to §5's work cap:

```
effective_daily_cap = base_cap − max(0, 60 − minutes_walked_today)
```

This recalculates live all day, off two running numbers (minutes worked today, minutes walked today):
- If you haven't walked at all yet today, your usable cap for the day sits a full hour below the base cap.
- Every minute you walk raises that ceiling back up, minute-for-minute.
- Once you've hit the full 60 minutes walked, the entire base cap is available, no reduction.
- If you're already locked out of new work sessions because you hit a reduced ceiling, going and walking immediately raises the ceiling and un-blocks you, same day — there's no need to wait until tomorrow.

There is deliberately no separate "checkpoint time" setting — the reduction is just always live, computed from the same two numbers already being tracked.

## 8. Data tracked (local only)

A simple local log (e.g. SQLite or a JSON/CSV log file), used both to drive the caps above and to give you visibility into your own patterns over time:
- Work sessions (start/end, duration)
- Breaks taken / skipped (which path, duration)
- Emergency Mode activations (date, duration) — running weekly total
- Focus Mode activations (date, duration) — running daily count
- Walking sessions (manual toggle, duration) vs the 60 min/day target
- Daily total work time vs the base cap, and the live effective cap (base cap adjusted by walking shortfall, per §7)

No cloud sync planned — this stays on your machine.

## 9. Proposed tech stack

**Python**, packaged as a standalone Windows `.exe` (PyInstaller). Rationale: best fit for the mix of system-level needs here — global input activity monitoring, a genuine full-screen input-blocking overlay, and Google Calendar API access all have mature, well-supported Python libraries, and iteration speed matters since this spec will likely evolve after real use.

Likely libraries:
- `pynput` / `pywin32` — global keyboard/mouse activity detection
- `tkinter` or `PyQt` — the full-screen lock overlay window
- Google Calendar reads — **approach revised 2026-08-09.** Since only free/busy
  data is available (see §5), the likely implementation is a plain HTTPS fetch
  of the calendar's shared iCal URL plus an `.ics` parser (`icalendar`), which
  needs **no OAuth, no consent screen, and no `google-api-python-client`** —
  just the URL pasted in once at setup. That would remove the single most
  fiddly piece of the original plan. To be confirmed at the start of Phase 3
  against the actual sharing link; `google-api-python-client` with the
  `freeBusy` endpoint remains the fallback if the link turns out not to work.
- `pystray` — system tray icon (status, manual controls: custom skip, Focus Mode, Emergency Mode, manual walking toggle)
- `sqlite3` (stdlib) — local history log
- `PyInstaller` — packaging to `.exe`

## 10. Open items / assumptions to confirm before or during build

- ~~Exact weekday cap value: 10h vs 11h.~~ **Resolved 2026-08-09: 11h.** Still a config value, not hardcoded.
- ~~Assuming "weekend" = Saturday + Sunday, and that Emergency Mode's 3h/week budget is one shared pool.~~ **Both confirmed 2026-08-09**, and non-working days extended to cover holidays and vacations via the work calendar — see §5.
- **New (2026-08-09):** how a full-day busy block actually renders in the shared free/busy feed — one interval per vacation day or one spanning the whole stretch, and which timezone the day boundaries fall in. Needs checking against real data at the start of Phase 3; §5's day-off rule depends on it.
- ~~Whether "long break every 4th cycle" resets at a fixed time (e.g. midnight) or after any idle gap.~~ **Resolved 2026-08-09 — resets after an idle gap** (`Config.idle_reset_after`, default 60 min). Chosen over a fixed daily reset because it matches the auto-detect premise: a genuine spell away from the desk starts a fresh set, whereas a midnight reset carries a count across a long lunch and resets one mid-evening. Implemented in Phase 1.
- App name/branding (currently just a placeholder).
- First-run setup flow. Likely much smaller than first assumed: if the free/busy iCal URL works (see §9), setup is pasting one URL rather than an OAuth consent flow.

## 11. Suggested build order

1. Core loop: activity detection → Pomodoro timer → full-screen lock overlay (no exclusions/skips yet) — get the fundamental "it actually locks the screen" mechanism solid first.
2. Exclusions: video call / screen share detection.
3. Break-skip system (meeting skip via Calendar, custom capped skip).
4. Daily cap + Emergency Mode.
5. Focus Mode.
6. Walking/standing-desk manual tracking (toggle + tally against 60 min/day + live effective-cap formula).
7. Local history log + simple tray-accessible summary view.
8. Packaging as a Windows `.exe`, first-run setup flow.
