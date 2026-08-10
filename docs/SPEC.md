# Pomodoro Guardian — App Design Spec (v2)

*Windows desktop app. Name confirmed 2026-08-09 — no longer a placeholder.*

## 1. Problem this solves

You work long, unbroken stretches — including late evenings and nights — at the cost of exercise, walking the dog, and downtime. A phone-based Pomodoro app requires you to notice it, open it, and obey it, which fails exactly when discipline is already low. This app removes the "remember to use it" step: it watches for you actively working and takes over from there, enforcing breaks with a real screen lock rather than a dismissible notification.

## 2. Core loop

1. **Detection.** Sustained keyboard/mouse activity (past a short threshold, e.g. continuous input for ~1–2 minutes) starts a tracked work session automatically. No manual "start" needed.
2. **Rhythm.** Classic Pomodoro: 25 min work / 5 min break, with a **15 min** long break every 4th cycle — confirmed 2026-08-09, settling the earlier 15–20 min range. A config value, not hardcoded. In practice a long break lands roughly every 2 hours of tracked work.
3. **Break enforcement.** When a break is due:
   - A 2-minute warning appears first (so you can save/wrap up a thought).
   - At zero, a full-screen, always-on-top, input-blocking overlay locks the screen for the break duration. This is a real block, not a reminder — closing/alt-tabbing away is not an escape hatch (see §6 for the one exception).
4. **Idle handling.** If you stop producing input (step away without triggering a break), the work session pauses; it does not silently keep counting toward your daily cap while you're not there.

**Thresholds as built (Phase 1, all in `pomodoro_guardian/config.py`):**

| Setting | Default | What it governs |
| --- | --- | --- |
| `input_gap` | 90 s | How long a pause still counts as "at the desk" — keyboard *and* mouse movement. Raised from 30 s on 2026-08-10: reading a long message is work, and at 30 s the clock stopped partway through, so reading-heavy days under-counted and breaks arrived late. |
| `start_threshold` | 1 min | Span of sustained input needed to auto-start a session. Measured between first and last keystroke, so the grace period above can't satisfy it on its own. |
| `warning_lead` | 2 min | How far ahead of the lock the warning appears. |
| `idle_pause_after` | 2 min | When the session is shown as paused. Accrual has already stopped at the last keystroke; this is the user-visible signal. |
| `idle_reset_after` | 60 min | When the part-finished interval is discarded and the long-break cycle count resets. |

**Media is paused when the lock appears (added 2026-08-09).** A video would
otherwise carry on behind the overlay — audible, invisible, and impossible to
stop while input is suppressed. Four mechanisms were measured on the real
machine; three failed:

| Mechanism | Result |
| --- | --- |
| `PostMessage` broadcast, `APPCOMMAND_MEDIA_PAUSE` | never arrived |
| `SendMessageTimeout` broadcast | pauses, then something answers with a play and it resumes ~1s later |
| `WM_APPCOMMAND` to the foreground window | the foreground at break time is whatever you are *working* in — measured as VS Code every time, never the media app |
| **A real media keystroke** | **works** — Windows routes it to whichever app holds the media session, regardless of focus |

The key is sent *before* input suppression starts, or the lock's own hook
would swallow it. **It is only sent when audio is actually playing**, checked
via per-application peak levels: `VK_MEDIA_PLAY_PAUSE` is a toggle, so firing
it into silence would *start* playback on every quiet break — worse than the
problem being solved. If the audio check is unavailable the key is not sent at
all, failing towards doing nothing.

Media is **not** resumed afterwards. The break exists to get you away from the
screen, and un-pausing on your behalf would be a surprise rather than a
courtesy.

The lock blocks keyboard and mouse but deliberately **not Ctrl+Alt+Del** — blocking the Secure Attention Sequence needs a kernel driver. It stays as the last-resort exit, alongside closing the app (SPEC §5's intentional escape hatch). A `safety_unlock` setting additionally releases the lock on a 3-second Escape hold; it is on by default while the lock is new code and is expected to be turned off once proven.

## 3. Never-interrupt exclusions

The lock will not trigger — and an in-progress countdown pauses — while:
- A video call is active (auto-detected: any app holding the camera or microphone).
- A calendar meeting is currently in progress (see §4 — this is the "meeting skip").

These are automatic; you don't have to invoke anything for them.

**How it is detected (built 2026-08-09, Phase 2).** Deliberately *not* by
process name. Maintaining a list of conferencing executables breaks whenever a
tool is added or renamed, and a running app says nothing about whether a call
is actually happening. One signal Windows already maintains is used instead:

- **CapabilityAccessManager's ConsentStore** — the registry keys behind the
  camera/microphone privacy indicator in the tray. An app currently holding a
  device has `LastUsedTimeStop = 0`; that zero is the whole signal. It is
  authoritative, cheap to read, and works for tools that don't exist yet.
**A presenting check was tried and removed (2026-08-10.)** It read
`SHQueryUserNotificationState`, whose `QUNS_BUSY` state means "a full-screen
application is running *or* presentation settings are applied" — so any
full-screen window tripped it, this app's own lock overlay included. The
history log caught it firing three times in eight seconds with nothing being
presented, silently holding breaks off. Narrowing it to
`QUNS_PRESENTATION_MODE` alone left it responding only to a Vista-era
Mobility Center setting that had never been switched on.

It is redundant in any case: screen sharing accompanies a call, so the
microphone catches it; a scheduled presentation is caught by the calendar
skip (§4A); and presenting in person is what Focus Mode (§6) is for.

Verified against the real machine's registry, which had usage history for
Slack, Chrome, Zoom, Loom, the Camera app and Premiere — covering both
packaged and desktop key formats. **`chrome.exe` is the case that justifies
the approach**: browser-based Meet and Teams calls are invisible to process
matching, since Chrome is always running, but "Chrome is holding the
microphone" identifies them exactly.

**Confirmed live against a real Google Meet call (2026-08-09).** A five-minute
observation with the camera toggled on and off and the microphone muted:

- `chrome.exe` was correctly identified as holding both devices.
- The **camera was released within about a second** of being switched off, so
  camera state tracks the real toggle rather than lagging behind it.
- The **microphone stayed acquired for the entire call** — through every
  camera toggle, and while muted. Muting in Meet sets `track.enabled = false`
  without handing the device back, so the exclusion held throughout.

That last point settles a limitation this spec had speculated about: **a
listen-only group call, camera off and microphone muted, is still detected.**
The worry that such calls would be invisible was wrong.

The measurement is specific to Chrome and Meet. Software mute keeping a
capture device acquired is standard behaviour, so native Zoom and Teams are
expected to match, but that has not been measured.

**Freezing behaviour.** While excluded, the work countdown freezes rather than
running on, no session starts from activity, and time on the call is never
retroactively credited as work once typing resumes. A break *already* locked
runs its full course — you cannot join a call through a lock, and cutting a
break short would be worse than letting it finish. When an exclusion lifts,
idle time is measured from that moment rather than from the last keystroke:
without that, a two-hour meeting you barely typed in would look like an
absence and discard the session the instant it ended.

**Known limitation, accepted (confirmed 2026-08-09).** Any app holding the
microphone open — a conferencing tool that never releases it, a recording app
left running — silently switches break enforcement off. After
`Config.exclusion_warn_after` (2h) of exclusion the app shows a standing
corner banner naming what is holding the device, and logs the same. It does
**not** override the exclusion: calls are never interrupted, so a stuck device
is reported rather than overruled. Locking the screen during what might be a
genuine call is the one failure these exclusions exist to prevent.

Two things worth being explicit about, because they are easy to misread:

- **The 2h is one *continuous* stretch, not a daily total.** Any gap where
  nothing holds the camera or mic resets it. Ordinary back-to-back meetings,
  where each call releases the device on hang-up, will never trigger it.
- **On a heavy meeting day the app does almost nothing**, by design. Breaks
  do not accumulate, and none is owed when the calls end — the countdown
  simply resumes where it froze.

`--exclusions` reports what is currently holding breaks off; `--no-exclusions`
disables the mechanism entirely.

## 4. Break-skip system

Two distinct skip paths, both self-serve, no "prove it" friction:

**A. Meeting skip (calendar-based) — unlimited.**
If Google Calendar shows a meeting in progress at the moment a break would trigger, the break is suppressed for the meeting's duration automatically. No cap — trust-based and unlimited.

**Breaks are held off for 10 minutes *before* a meeting as well (added 2026-08-09.)** A lock landing three minutes before a call is worse than one during it: there is no time to prepare, and no way to get the time back. `Settings.meeting_lead_minutes`, configurable in the setup window. The lead does not resurrect a day-off block — a vacation is still not a meeting however early you look — and it does not extend past a meeting's end.

**Verified live 2026-08-09** against a placeholder event, through the real
`CalendarWatcher` → `MeetingDetector` → `CombinedDetector` chain. The lead
window fired before the meeting began, and the message distinguishes the two
states ("starting 19:05" versus "until 20:05") rather than misdescribing a
pre-meeting hold as the meeting itself. Google's `basic.ics` published the new
event within a couple of minutes, so feed lag is not the obstacle it was
expected to be.

**B. Custom skip — capped.**
A manual "skip this break" action with a fixed choice of duration: 5 / 10 / 20 minutes. All custom skips share one **60-minutes-per-day accumulated cap**. Once the daily 60 minutes are used, no more custom skips are available that day — the lock enforces normally.

**Invocation — hold Escape for 3 seconds (decided 2026-08-09).** Phase 1's
safety release becomes this gesture rather than being replaced by it: holding
Escape against the lock stops being a plain unlock and instead opens a small
menu offering 5 / 10 / 20 minutes, with any option exceeding the remaining
daily budget shown as unavailable. Choosing one dismisses the lock and debits
the budget; dismissing the menu resumes the break.

The gesture is a good fit — it is already deliberate rather than accidental,
it is discoverable from the lock screen itself, and it needs no tray
interaction at the one moment the tray is behind a full-screen window.

**Built and verified 2026-08-09.** Two details worth recording, both found only
by testing against a real keyboard:

- **Escape auto-repeats at roughly 10 events per second** while held — 606
  events across one 60-second lock. Since the gesture *is* a held Escape, the
  menu has to ignore Escape until an actual key-up arrives, or the repeat that
  follows the hold dismisses the menu the same instant it opens. Pressing
  Escape again after releasing still dismisses it.
- **The digits must look like keys.** A first version rendered "1 · 5 min" and
  read as a label rather than an instruction. Each option is now a filled
  keycap above its duration, under a heading that says to press one;
  unavailable options keep the outline and lose the fill.

**⚠️ This creates a tension that Phase 3 must resolve explicitly.** Today the
Escape hold is an *unlimited, unbudgeted* release whose entire purpose is that
it always works — Phase 1's lock is young code, and a bug in an unattended
25-minute lock strands you on your own machine. A capped skip has the opposite
requirement: past 60 minutes a day it must *stop* working, or the cap is
decorative. The same gesture cannot be both.

**Resolved 2026-08-09: once the budget is spent, holding Escape does nothing
and the break holds.** The menu still opens, with all three durations shown
greyed out — so the budget being gone is *visible* rather than the gesture
failing silently, which would read as a bug. This matches §5's stated
philosophy that the real escape hatch is closing the app entirely: a
deliberate act, not a casual one.

**The safety property is preserved structurally instead.** The lock carries a
**hard maximum duration** enforced by a plain daemon thread that owns no
reference to the UI — if suppression ever outlives its break by more than
`Config.lock_max_overrun` (60s), it releases itself. Implemented in Phase 1
rather than deferred, since the shipped lock had no independent failsafe at
all: a hung tkinter loop would have left input suppressed with no way out.

The distinction that makes this work: the watchdog guards against **the app
failing**, not against the user. It cannot be invoked, has no UI, and grants
no discretionary escape — so closing the Escape route in Phase 3 costs
nothing in safety.

## 5. Daily work cap + Emergency Mode

- **Base cap: 11 hours** of tracked *work* time per working day (break time doesn't count toward this). Confirmed 2026-08-09.
- **Non-working-day base cap: 3 hours/day.** Confirmed 2026-08-09. A day is a non-working day if **either** holds:
  1. It is a **Saturday or Sunday**.
  2. The work calendar shows **one contiguous busy block of ≥ 6 hours**, measured in the calendar's own timezone after merging overlapping intervals. Threshold and rationale come from real data — see "Measured against the real feed" below.

  Every other mechanic in this section and §7 applies identically on non-working days: same effective-cap formula, same Emergency Mode, same shared weekly budget.
- **Emergency Mode budget is one shared pool** of 3h across all 7 days — not split weekday/weekend. Confirmed 2026-08-09.

**Calendar access is free/busy only — this constrains the whole design.**
The work Google account exposes a shareable link that reports *busy intervals
and nothing else*: no titles, no event types, no `outOfOffice` flag,
no attendee lists. Every calendar-driven rule in this spec must therefore be
expressible in terms of busy intervals alone. In particular:

**Measured against the real feed (2026-08-09).** A 706-event export spanning
2026-06-10 → 2028-08-07 was analysed. Findings, which the rule above is built on:

| Day type | Longest contiguous busy block |
| --- | --- |
| Ordinary weekday (384 sampled) | **≤ 1.5 h** |
| Company holiday | **8.5 – 12.5 h**, one block, ~09:00–18:00 |
| Vacation day | **24 h**, midnight-aligned |

- The separation is close to sixfold, so any threshold between ~2 h and ~8 h
  classifies every day in the sample correctly. **6 h** is the midpoint with
  the most headroom in both directions.
- **Holidays and vacations do not look alike.** Vacations are the
  midnight-to-midnight blocks; company holidays are ordinary working-hours
  blocks that merely happen to be long. An earlier draft of this rule looked
  only for full-day coverage and would have caught vacations while silently
  missing every holiday.
- Zero false positives across 14 months. The only two ordinary-looking days
  that tripped the rule — 2026-09-30 and 2026-11-11 — turned out to be
  National Day for Truth and Reconciliation and Remembrance Day.
- **Everything is `SUMMARY:Busy`.** No titles, no event types, no `TRANSP`
  values, no all-day flags — all 706 events are timed and in UTC. The
  free/busy constraint is confirmed exactly as described; there is no richer
  signal available to fall back on.
- The feed carries its own timezone (`X-WR-TIMEZONE: America/Toronto`), so
  day boundaries should be computed from that rather than from the machine's
  locale. Doing this in UTC misattributes evening events to the following day.

**Known limits of the rule, accepted:**

- A genuine all-day offsite or workshop looks exactly like a holiday and would
  cut the cap to 3 h. Free/busy data cannot distinguish them. **Resolved by
  the manual override below** (confirmed 2026-08-09). Worth noting the
  practical impact is smaller than it sounds: during an all-day offsite you
  are mostly in sessions rather than typing, and only keyboard/mouse activity
  is tracked, so a 3 h cap may never bind on the day it misfires.
- Holidays are only detectable once they are actually booked into the
  calendar. In the sample, everything through 2027-01-01 is booked and
  nothing after 2027-02-15 is, so the far future currently reads as ordinary
  working days. This resolves itself as the year gets booked, since the app
  only ever asks about *today*, but it does mean a holiday booked late will
  be missed.
- **Day-off blocks must be excluded from §4A's meeting skip.** §4A suppresses
  breaks whenever the calendar is busy. The same ≥6 h blocks that mark a day
  off would otherwise match for their whole length, suppressing break
  enforcement for 9 hours on a holiday or a full 24 on a vacation day — the
  exact opposite of what the reduced cap intends. **Meeting-skip should
  consider only blocks below the day-off threshold; the two rules partition
  the same data and must never both fire on one block.** Measured against the
  real feed this is clean: genuine meetings never exceed 1.5 h.
- **The cap must degrade safely when the calendar is unreachable** (offline,
  stale link, first run before setup). Assumption unless told otherwise: fall
  back to the day-of-week rule alone — Sat/Sun reduced, everything else 11h —
  and surface that it is running without calendar data, rather than failing
  closed or silently granting a full cap on a holiday.
### 5a. Manual day-type override

Confirmed 2026-08-09. The calendar rule above is good but not perfect, so the
day's classification can be corrected by hand from the tray menu. The two
directions carry very different risk and are budgeted differently.

| Direction | Effect | Limit |
| --- | --- | --- |
| **Treat today as a non-working day** | base cap 11 h → 3 h | **Unlimited.** Self-restricting, so there is nothing to abuse. Covers a sick day, or a holiday the calendar never got. |
| **Treat today as a working day** | base cap 3 h → 11 h | **2 per calendar month.** |

**Why the raise is capped.** It is a 3 h → 11 h jump in one click — far larger
than Emergency Mode's +1 h. Left unlimited it would turn every weekend into an
11 h day on demand, which would hollow out the rule §1 calls the whole point of
the app: that the app, not in-the-moment willpower, holds the line. Two per
month is enough for genuine offsites, which are rare, while being too few to
become a habit.

Rules:

- **Today only.** An override expires at local midnight and cannot be set for
  a future date. This matches the live-recalculation model in §7: you set it
  in the moment you notice the app has the day wrong.
- **Persisted**, so restarting the app does not clear an active override or
  reset the monthly budget.
- **The raise budget resets on the 1st** of each calendar month, and is
  separate from Emergency Mode's 3 h weekly pool. The two stack: an overridden
  11 h day can still be extended by Emergency Mode.
- **A spent raise is not refunded** if the override is cleared later the same
  day. Refunding it would make the budget meaningless — activate in the
  morning, work the long day, clear it at night, repeat.
- **The override wins over the calendar** for the rest of the day, even if the
  feed changes underneath it.
- **The walking formula still applies.** §7's shortfall is subtracted from
  whichever base cap is in force, overridden or not.
- Raising is allowed on any day, weekends included. The monthly budget is what
  keeps that honest, rather than a rule about which days qualify.

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
- Day-type overrides (date, direction, and whether the calendar was overruled) — running monthly count of the capped "treat as working day" direction, per §5a. Also the cheapest way to find out whether the ≥6 h rule is misclassifying days in practice: a run of raises would say so.
- Walking sessions (manual toggle, duration) vs the 60 min/day target
- Daily total work time vs the base cap, and the live effective cap (base cap adjusted by walking shortfall, per §7)

No cloud sync planned — this stays on your machine.

## 9. Proposed tech stack

**Python**, packaged as a standalone Windows `.exe` (PyInstaller). Rationale: best fit for the mix of system-level needs here — global input activity monitoring, a genuine full-screen input-blocking overlay, and Google Calendar API access all have mature, well-supported Python libraries, and iteration speed matters since this spec will likely evolve after real use.

Likely libraries:
- `pynput` / `pywin32` — global keyboard/mouse activity detection
- `tkinter` or `PyQt` — the full-screen lock overlay window
- Google Calendar reads — **approach revised and confirmed 2026-08-09.** Only
  free/busy data is available (see §5), and a real export parsed cleanly, so
  the implementation is a plain HTTPS fetch of the calendar's secret iCal URL
  plus an `.ics` parse. This needs **no OAuth, no consent screen, and no
  `google-api-python-client`** — just the URL pasted in once at setup,
  removing the fiddliest piece of the original plan. The feed is simple
  enough (`BEGIN:VEVENT` / `DTSTART` / `DTEND`, everything `SUMMARY:Busy`)
  that a small dependency-free parser is viable; `icalendar` is the fallback
  if recurrence rules turn out to need real handling.
  - ⚠️ **The secret iCal URL is a credential** — it grants read access to the
    entire work calendar and cannot be scoped down. It belongs in a
    gitignored local config, never in the repo. `.gitignore` already covers
    `.env`, `credentials.json`, `token.json`, `*.ics` and `local/`.
- `tzdata` — **required on Windows.** Windows ships no IANA timezone
  database, so `zoneinfo` raises `ZoneInfoNotFoundError` for
  `America/Toronto` without it. Needed for §5's day-boundary maths, and
  PyInstaller must bundle it for the packaged `.exe` in Phase 8.
- `pystray` — system tray icon (status, manual controls: custom skip, Focus Mode, Emergency Mode, manual walking toggle, day-type override per §5a)
- `sqlite3` (stdlib) — local history log
- `PyInstaller` — packaging to `.exe`

## 10. Open items / assumptions to confirm before or during build

- ~~Exact weekday cap value: 10h vs 11h.~~ **Resolved 2026-08-09: 11h.** Still a config value, not hardcoded.
- ~~Assuming "weekend" = Saturday + Sunday, and that Emergency Mode's 3h/week budget is one shared pool.~~ **Both confirmed 2026-08-09**, and non-working days extended to cover holidays and vacations via the work calendar — see §5.
- **New (2026-08-09):** how a full-day busy block actually renders in the shared free/busy feed — one interval per vacation day or one spanning the whole stretch, and which timezone the day boundaries fall in. Needs checking against real data at the start of Phase 3; §5's day-off rule depends on it.
- ~~Whether "long break every 4th cycle" resets at a fixed time (e.g. midnight) or after any idle gap.~~ **Resolved 2026-08-09 — resets after an idle gap** (`Config.idle_reset_after`, default 60 min). Chosen over a fixed daily reset because it matches the auto-detect premise: a genuine spell away from the desk starts a fresh set, whereas a midnight reset carries a count across a long lunch and resets one mid-evening. Implemented in Phase 1.
- ~~What holding Escape does once the day's 60-minute custom-skip budget is spent.~~ **Resolved 2026-08-09: nothing — the break holds**, with the durations shown greyed out so the exhausted budget is visible. The safety property moves to a UI-independent watchdog instead. See §4B.
- ~~App name/branding.~~ **Resolved 2026-08-09: Pomodoro Guardian**, kept after briefly considering the shorter "Pomo". The `pomo` abbreviation stays as the project/file prefix (`pomo-task-build-phase.md`); it is a shorthand, not the app's name.
- ~~First-run setup flow.~~ **Built 2026-08-09**, and it was indeed much smaller than first assumed — pasting one URL, not an OAuth consent flow. A ttk window (`setup_dialog.py`) shown on first run or via `--setup`, covering the calendar URL (with a Test button that fetches and describes the feed), the rhythm, the safety unlock, the daily caps and the walking target. Settings persist to `%APPDATA%\PomodoroGuardian\config.json` — outside the repo, so the secret URL cannot be committed. Detection thresholds stay hand-editable in the JSON rather than being exposed as choices.

## 11. Suggested build order

1. Core loop: activity detection → Pomodoro timer → full-screen lock overlay (no exclusions/skips yet) — get the fundamental "it actually locks the screen" mechanism solid first.
2. Exclusions: video call / screen share detection.
3. Break-skip system (meeting skip via Calendar, custom capped skip).
4. Daily cap + Emergency Mode.
5. Focus Mode.
6. Walking/standing-desk manual tracking (toggle + tally against 60 min/day + live effective-cap formula).
7. Local history log + simple tray-accessible summary view.
8. Packaging as a Windows `.exe`, first-run setup flow.
