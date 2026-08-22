# Pomodoro Guardian — Project Plan

## North Star

A Windows desktop app that auto-detects active work and enforces Pomodoro-style breaks with a real full-screen lock, with a small number of deliberately capped escape hatches (custom skip, Emergency Mode, Focus Mode) so the app — not in-the-moment willpower — is what actually holds the line. Also tracks a daily standing-desk/walking goal, independent of the break cycle.

## Phases

1. ✅ Core loop: activity detection → Pomodoro timer → full-screen lock overlay — complete 2026-08-09
2. ✅ Exclusions: video call / screen share detection — complete 2026-08-09, confirmed against a live Google Meet call
3. ✅ Break-skip system (calendar meeting skip + capped custom skip) — complete 2026-08-09
4. ✅ Daily work cap + Emergency Mode — complete 2026-08-09
5. ✅ Focus Mode — complete 2026-08-09
6. ✅ Walking/standing-desk manual tracking (toggle + 60 min/day tally + live effective work-cap formula) — complete 2026-08-09
7. ✅ Local history log + tray summary view — complete 2026-08-09. Tray icon and menu built earlier; SQLite log records both the user's actions and **the app's own decisions** (day classification, computed cap, start/stop), so the accounting bugs this app is prone to — the ones that take days to surface — are auditable rather than gone at midnight. Read with `--history [DAYS]` and `--events [N]`.
8. ✅ Packaging / daily launch — complete 2026-08-09. **Resolved as a Startup shortcut, not an `.exe`:** an unsigned PyInstaller build was made successfully and then blocked outright by the machine's security tooling, a well-known false positive. On a single-machine personal app, clearing that needs IT involvement for no benefit, while a Startup-folder shortcut to the venv's `pythonw.exe` starts at login with no console, no unsigned binary, and no rebuild step. `pomodoro-guardian.spec` is kept for the day it might need to run without Python. First-run setup was built early (see below).

## Handoff — start here in a fresh session

**State: all 8 phases complete and in daily use.** 428 tests, pyflakes clean,
`main` clean and pushed. The app runs from a Startup shortcut at login; there is
no build step. No skill is required to work on this project — see `CLAUDE.md`'s
session practices.

### Run it
```
.venv\Scripts\python.exe -m pomodoro_guardian            # the app
.venv\Scripts\python.exe -m pytest tests                 # 428 tests
.venv\Scripts\python.exe -m pyflakes pomodoro_guardian tests
```
Diagnostics, all safe while the app is running: `--doctor`, `--history [DAYS]`,
`--events [N]`, `--exclusions`, `--test-sounds`, `--shortcuts`, `--setup`.
Run flags: `--dry-run`, `--demo FACTOR`, `--remind-only`, `--no-exclusions`.

### A macOS port is being handed to someone else
A colleague with the same overfocusing problem is porting this to a Mac on her
own machine, with her own Claude account — she is not a developer, and there is
no Mac here to test on. **`docs/MAC-PORT.md` is the whole handover**; read it
before touching anything platform-specific.

What that changed on this side: `platform.py` names all 14 platform
capabilities and `--doctor` reports them; `activity.py` gained an idle-time
seam whose macOS half needs no permission; and the lock's degraded mode is
now safe rather than a trap. **The app is expected to run with pieces
missing** — that is deliberate, and `--doctor` is what keeps it honest.

**Keep `docs/MAC-PORT.md` current with the Windows app.** It went stale
within a day: it told her to build the `break_ignored` accounting that had
just been built, and quoted a test count two features out of date. Anything
that changes what she would do — a new run flag, a capability, a feature that
removes one of her tasks — belongs there in the same sitting.

### The work now is feedback-driven, not phase-driven
Every phase is built. What changes the app from here is **using it and
reporting what happens** — which is how the last dozen commits were found. The
numbers are all still guesses: 12h cap, 50 min walking, 25/5, 11:45/15:20
reminders, 90s input gap, 1.4s banner hold.

### Unverified on hardware
The lock screen's **`M` (media) and `W` (stop treadmill timer)** keys share one
key-routing path and neither has run for real. `local/checkmedia.py` exercises
`M`; start a walk from the tray first and one lock covers both. `M` also lifts
keyboard suppression for a few milliseconds — the script reports whether it
came back intact, which is the failure that would matter.

**Also unverified (2026-08-22):** the meeting/skip redesign — a break
bleeding into a real meeting reads as free-to-skip, and a break already due
waits `post_meeting_break_delay` after the call ends rather than firing
instantly. Both are engine-level tested but have not been watched against a
real calendar feed and a real lock screen yet.

### Two habits that account for most bugs found
1. **Assert on the rendered result, not the requested one.** Geometry checks
   passed through three separate layout bugs.
2. **Check the data before writing the rule**, and prefer running a thing to
   reviewing it. Roughly a dozen bugs were invisible to review and to unit
   tests: they needed a live call, a real keyboard, a playing video, an evening
   clock.

### Interactive checks live in `local/` (gitignored)
`checklock.py`, `checkmedia.py`, `checkwalk.py`. Run **by the contributor**, not
by the agent: timed on-screen tests failed three times because their prompts
print to a console sitting behind the lock. The script prints what it saw
afterwards.

### Landmines
- **`git add -A` is dangerous here.** It once committed five stock mp3s to a
  public repo; they were purged from history with `git-filter-repo` and
  force-pushed. `assets/sound-effects/` is gitignored. Check `git status`
  before staging wholesale.
- **`VK_MEDIA_PLAY_PAUSE` is a toggle**, and the only mechanism that reaches
  Chrome. Three bugs trace to that. Auto-pausing is now off by default and the
  lock offers `M` instead — do not reintroduce automatic firing without
  re-reading docs/SPEC.md §2.3.
- **A dev `Application` will write to the live `history.db` and `state.json`
  unless you point it elsewhere.** Pass *both* `state_file` and `settings_file`
  at a scratch directory and assert `app.history.path` is inside it before
  ticking. A test run once put a zeroed snapshot into a real working day, which
  made the day's walking total read 0 min. See `ISSUES.md`.
- **The lock screen's own key bindings look redundant. They are not.**
  `LockOverlay._bind_local_keys` is installed on every break, including ones
  that suppress input properly, and looks like it should be conditional. It
  must stay unconditional: while suppression works the keystroke never reaches
  a window, so the bindings are inert, and that self-selection is the only
  cover for a hook that starts cleanly and then receives nothing — which no
  return value can detect. Making it conditional restores a full-screen window
  with no way out.
- **Adding a test will fail the suite until you update two documents. That is
  deliberate.** `tests/test_docs_current.py` checks that the count quoted in
  `docs/MAC-PORT.md` and this file matches what pytest actually collects. The
  figure is load-bearing: it appears at step 5 of the macOS setup as "you
  should see N passing", so a stale number tells a non-developer her machine
  is broken. It went stale three times in two days before this existed. The
  failure message names the new number; put it in both files. Running a single
  test file skips the check, so it never gets in the way while working.
- **Never call tkinter from a non-UI thread.** pynput listeners and pystray
  both run on their own threads and post to a queue the tick drains.
  `root.after()` from a listener raises "main thread is not in main loop".
- `local/calendar.ics` and the secret iCal URL in `%APPDATA%` are credentials.
- Moving or renaming the repo silently breaks all three shortcuts; re-run
  `--shortcuts` and re-toggle Start with Windows.

### Where state lives
`%APPDATA%\PomodoroGuardian\` — `config.json` (settings, hand-editable),
`state.json` (today's tallies plus rolling budgets), `history.db` (append-only
log), `pomodoro.log` (text log, written when there is no console).

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

### 2026-08-22 — the timer stopped freezing during meetings, and skips stopped restarting the long break

First of a batch of feedback items (this repo's normal mode now — see "The
work now is feedback-driven" above). This sitting covers the three most
tangled ones together, since they share the same code paths: the meeting
timer/skip redesign (checkpoint 1 of 4). The other three — overwork skip
caps, a 10-min pre-cap warning window, and three standalone bug fixes (media
pause, walking-reminder position, warning-pill lag) — are separate sessions.

**The interval no longer freezes during a meeting.** It used to: an
exclusion held `_work_elapsed` exactly where it was for the whole call, on
the reasoning that "hold the break off" and "freeze the countdown" were the
same thing. They never were — `worked_total` was already fixed to advance
during a call on 2026-08-11 for exactly this reason, and the interval simply
never got the same treatment. The symptom: a 40-minute meeting sitting on a
25-minute interval left the countdown reading whatever it had when the call
started, so a break overdue by 15 minutes the moment the call ended looked
freshly begun. Now `_work_elapsed` advances by tick-delta through a call
exactly like `worked_total` does; only actually raising the lock stays held
off, via a new `_check_work_thresholds` helper both the normal tick and the
excluded path call into. See `docs/SPEC.md` §3.

**A break already due when the meeting ends now waits a beat before
firing** — `Config.post_meeting_break_delay` (default 5 min, 0 for
immediate) — rather than locking the instant the call clears, so there's a
moment to write down what just got decided. Only holds off a break that was
*already* waiting; one that becomes due later is unaffected.

**A break that bleeds into a meeting is free to skip.** The existing
10-minute meeting lead only stops a break from *starting* late; it doesn't
stop one that had already started from *running into* the meeting — a
15-minute long break starting 11 minutes early isn't covered by a 10-minute
lead and lands 4 minutes into the call. Rather than auto-cutting the break
(keeping §3's "a break already locked runs its course" as the default), the
skip menu now waives its usual 60-min/day budget whenever a meeting
exclusion is active at the moment of skipping.

**Skipping after resting ≥5 minutes now counts the break as taken, not
deferred.** Reported live: a 15-min break, 10 minutes actually rested, then
skipped for an urgent message — and the *next* break arrived as another full
15-min long break, because `defer_break()` deliberately never advances the
cycle count (a skip isn't a taken break), so the unchanged count made the
4th-cycle arithmetic recompute "long" again. Below
`Config.break_skip_complete_after` (5 min), nothing changes: `defer_break`
still spends budget and buys back the chosen minutes. At or past it, a new
`PomodoroEngine.complete_break()` path advances the cycle and resumes on a
full fresh interval instead — free, since there's nothing left to buy back.
This is also where the meeting free-skip and the rest-complete rule overlap
in practice: a break far enough into a meeting has usually also cleared 5
minutes rested, so `app.py`'s new `skip_terms()` computes both independently
and either alone waives the budget.

**Verified:** 428 tests (12 new — `tests/test_skips.py` gained engine
coverage for `complete_break()` and the pure `skip_terms()` decision
function, `tests/test_meeting_time.py` gained the post-meeting-delay cases),
pyflakes clean. `skip_terms()` was deliberately pulled out of `Application`
into a free function in `app.py` so it's testable without constructing a
live `Application` (tkinter root, calendar watcher, sound resolution) —
nothing in the test suite does that today, and this logic didn't need to be
the first. **Not verified on hardware**: the free/complete skip menu text
and the post-meeting delay haven't been watched against a real calendar
feed and a real lock screen yet — flagged the same way the existing `M`/`W`
key handling already is in the Handoff section above.

### 2026-08-11 — the tray would have killed the app on her first real launch

Found while answering "what will she need to do?", by reading the startup
path rather than trusting that it degrades. Most of it does: the
single-instance guard, the Start Menu shortcut and the log redirect all
no-op correctly off Windows. **The tray does not.**

`pystray` imports fine on macOS and its Cocoa backend then runs on a
*background thread*, which AppKit refuses at the Objective-C level — where it
can take the process down instead of raising anything Python can catch, so
`_run`'s `except Exception` is no protection at all. tkinter already owns the
main thread, so there is nowhere safe to put it; that collision is the design
decision stage 4 of the handover describes.

The timing is what made it worth fixing before sending anything: it lands on
the first launch that is *not* `--dry-run` — precisely when she would be
testing the break screen for the first time, on a machine nobody has ever run
this on. An unexplained death there is the worst possible first impression.

`tray.start()` now refuses on macOS before pystray is even imported (the
import is harmless; starting the backend is what kills it), sets a `reason`,
and the app prints it along with what it costs — no walk toggle, no settings,
no Quit, stop with Ctrl+C. `--doctor` asks the same function, so it cannot
promise a menu the app will then refuse. Building the icon and menu is
guarded too, not just the import: the backend is chosen at import time and
constructs platform objects in the constructor.

The guard is one line — `MAIN_THREAD_ONLY` at the top of `tray.py` — and the
handover says to remove `"darwin"` from it once a real menu bar works.

**Verified:** 416 tests (6 new), pyflakes clean, and the launch simulated with
`sys.platform` set to darwin — refusal confirmed, pystray provably never
touched (it was replaced with something that raises), and the exact log lines
she will see printed out.

### 2026-08-11 — the countdown became a corner pill, and the warning became the same pill

Two requests in one: move the countdown out of the taskbar into the
bottom-right corner just above it, and give the two-minute warning that same
pill so it pops up centred and then **replaces** the countdown in that corner.
Plus a settings toggle for the countdown — the warning stays mandatory, since
that one is the app doing its job.

**The move deleted more than it added.** The in-taskbar version was a floating
window anchored to `TrayNotifyWnd` and composited over a photograph of the
strip behind it. It looked exactly like part of the taskbar, and it had cost
three separate bug fixes to get there: shell windows reporting themselves as
full-screen, the shell taking its z-order, and screen grabs failing on a locked
session. Sitting plainly above the taskbar needs no photograph, no anchoring,
and no guessing which tray slot the icon landed in. `taskbar.py` is gone (342
lines) and `pill.py` (about 470, shared by both users) replaces it.

**Sharing the drawing is the point, not a saving.** The countdown and the
warning occupy the same corner one after the other, so any difference in shape
or weight would read as two unrelated things competing for the spot. Measured
rather than asserted: the settled warning matches the countdown's height, right
edge and bottom edge exactly.

**Four things only looking could have settled:**

- The silhouette is **keyed**, since there is nothing stable to photograph over
  ordinary windows — and keying compares colours exactly, so anti-aliased edges
  survived as a magenta fringe. Thresholding the alpha to binary fixes it; a
  darker ring inside the edge hides the staircase left on the curves.
- **Opaque.** The inherited 0.90 alpha let the window behind show through the
  plate, which read as grime rather than translucency.
- **Dark amber for the warning.** Bright amber was tried first and the tomato,
  being red, vanished into it at the size the warning arrives at.
- The tomato was being rendered supersampled *and* `render_icon` already
  supersamples internally, so a 135px pill cost **52ms** — twice the animation's
  frame budget, on the thread that also runs the tick. Pasting it once at final
  size brought that to 22ms.

**tkinter would not keep the position, which took the longest to find.**
`geometry()` on a mapped `overrideredirect` window reports back correctly and
then reverts to the window's first position once idle processing runs. The
symptom was a warning that shrank to nothing mid-screen instead of settling in
the corner — and it survived four wrong hypotheses (`raise_above` clobbering
the move, `update()` between frames, the geometry cache short-circuiting,
hover-fade hiding it), each disproved by a standalone repro that *worked*. What
found it was instrumenting the real run and printing what tk reported straight
after each call. The rectangle is now asserted through `SetWindowPos` every
tick alongside the z-order, so tk's opinion no longer matters.

**Verified:** 397 tests (34 new), pyflakes clean, and photographed in the real
corner — the countdown in three tones, the warning arriving large and centred,
and the warning settled on the countdown's exact anchor with one pill per
monitor in each screen's own corner.

### 2026-08-11 — meetings were invisible to the daily cap

**The best bug found so far, and it came from the contributor reading her own
tray**: 9am start, no long breaks taken, and only 2.3h tracked by 13:34. Her
guess — "does the tracker omit the time I spend on meetings if it delays the
break?" — was exactly right.

The history log settled it in one query. `11:50 excluded — meeting in progress`,
then **82 minutes at 0% credited**, resuming the moment the call ended. Of 4.7h
at the desk, 2.0h counted: ~1h20m meeting, ~50m of breaks correctly excluded,
~20m of reading under the 90s input gap.

**Why it mattered more than a wrong number.** A day of meetings could be
followed by a *full cap's worth* of tracked work on top. The cap is the whole
point of the app, and it could be walked straight past by having a normal
meeting-heavy day.

**The cause was two needs sharing one mechanism.** An exclusion means "do not
start a break now". It had also come to mean "do not count this time", and
`_apply_exclusion` returned before any crediting while pinning the watermark
each tick. Focus Mode had already reasoned this through and *says so in
timer.py* — "an exclusion freezes the countdown, which would make focus time
invisible to the daily cap and let a 2h session be worked for free" — and the
same argument was simply never carried across to exclusions. **The codebase
had already written down the answer to a bug it was still shipping.**

Now: the interval stays frozen and no break fires, but wall clock accrues
against the cap, credited from the tick delta rather than the input watermark
(the watermark being the thing that deliberately ignores a silent call). A
machine that slept mid-meeting still credits nothing, because `max_tick`
discarded that delta upstream. Call time is tallied separately and shown by
`--history` as "1.4h on calls", and `exclusion_ended` records each call's
duration — previously only the start was logged, which is why the 82 minutes
had to be reconstructed from snapshot gaps.

**A test asserted the bug.** `test_an_exclusion_still_freezes_even_during_focus`
said "a call is a call: no work should accrue for it either way", so the suite
was actively defending the behaviour. Rewritten to assert what an exclusion is
actually for — the interval does not advance — plus the work that now accrues.
Worth remembering next time a test blocks a fix: ask which of the two is wrong.

**The risk changed direction rather than disappearing.** A stuck microphone
used to under-count; now it over-counts, which would shorten every interval to
five minutes. `count_exclusions_as_work` (`exclusions.count_as_work` in the
config) is the way out, and the 2h stuck-device warning is the guard. A meeting
attended from a phone is credited too — accepted knowingly.

**Verified:** 389 tests (11 new), pyflakes clean, and today's real shape
replayed through both engines — 90 min typing, 82 min meeting, 25 min typing:
1.60h counted before, 2.97h after, **1.37h recovered**, with three breaks
firing either way.

**Note:** today's already-recorded 2.3h is not corrected retroactively. The fix
applies from the next restart.

### 2026-08-11 — why the pill kept disappearing

Reported as "it disappears sometimes", which turned out to be **three
independent bugs** wearing one symptom. Worth recording because each was
invisible to the tests that existed, and one of those tests was actively
misleading.

**Shell windows are not full-screen apps.** The hide-when-covered check only
compared rectangles, and two things on every ordinary desktop satisfy it: the
desktop itself (`Progman`, measured at −1920, 0, 3840, 1200 across both
monitors) and `Windows Input Experience` (`Windows.UI.Core.CoreWindow`, at
exactly 0, 0, 1920, 1080). So **clicking the desktop, pressing Win+D, or
switching keyboard language hid the pill.** Excluded by window class now.

My hypothesis going in was that *maximised* windows were the culprit, and
measuring said no: Windows inflates a maximised window's rect by the invisible
resize border to (−8, −8, 1928, 1040), which stops short of the taskbar strip.
The existing test had asserted this with an invented (0, 0, 1920, 1032) — right
answer, wrong reason, and it would have kept passing through the real bug. It
now uses the measured rect.

**The z-order was taken once and never retaken.** The pill only redrew when the
number changed — once a minute — while the shell raises its own windows every
time the taskbar is clicked or Start is opened. So it sat behind the taskbar for
minutes at a time. Now reasserted every tick with `SetWindowPos` and
`SWP_NOACTIVATE`, not `lift()`, since a taskbar chip stealing focus once a
second would be intolerable.

**A single failure was permanent.** `_broken` latched on any exception, and
locking the workstation is exactly such an exception — a screen grab of a
locked session comes back black or fails. So one Win+L could cost the pill for
the rest of the day. Now a 20-second backoff, and a suspiciously flat grab is
refused rather than cached, since caching black would bake a black rectangle
into the taskbar.

**The lesson worth keeping:** "it disappears sometimes" is a symptom, not a
bug. Three causes, and stopping at the first plausible one — or at my confident
maximised-window hypothesis — would have left two live.

**Verified:** 378 tests (11 new, including the real measured rects for every
offending window class), pyflakes clean, both offenders re-run against the
shipped function on this desktop, and the pill re-photographed in the taskbar.

### Session close — 2026-08-11 (the taskbar countdown pill)

Asked for the countdown "directly in the toolbar next to the app icon", with
a mockup. **Windows cannot do that**: the notification area takes an icon and
a tooltip, deskbands were deprecated, and Windows 11 removed third-party
toolbars. What it *can* do is a borderless click-through always-on-top window
parked immediately left of `TrayNotifyWnd`, which looks identical.

Spiked before promising, because the one thing that could not be reasoned
about was whether a window can draw above the taskbar — itself topmost. It
can. The spike also produced the two problems worth recording:

- **Colour-keying the background left a magenta fringe** on the rounded
  corners: an anti-aliased edge pixel is a blend of pill and key colour and
  matches neither. Fixed by photographing the taskbar behind the pill and
  compositing onto that, which gives clean edges for nothing and stays true
  because the strip it covers is empty taskbar.
- **A short countdown sat hard left** against the padding, since the pill is
  a fixed width. Centred in the space beside the tomato instead. Caught by
  looking at "9 min" next to "25 min", not by any test.

Fixed width is deliberate: a pill that resized once a minute would jitter and
re-grab its backdrop every time. Minutes rather than a ticking `5:15` was the
contributor's call — in the corner of the eye a seconds counter reads as
pressure rather than information. Driven from the same `_badge` function as
the icon, so the two can never disagree.

Hidden when a window covers the screen, so it cannot float over a video —
compared by rectangle rather than by asking `SHQueryUserNotificationState`,
which reports busy for any full-screen window including this app's own lock,
a mistake that already cost this project once.

**Verified:** 356 tests (17 new), pyflakes clean, and photographed in the real
taskbar across all four tones. The screenshots also caught the pill correctly
re-anchoring when a new tray icon appeared and shifted the notification area.

**Then the icon's own countdown came back out.** It had been drawn onto the
tray icon the day before, that being the only way Windows shows a number in
the notification area. With the pill an inch to the left, the plate needed to
make two digits legible was disfiguring the tomato to say something already
said better — and **two places showing the same number is one too many**,
since a disagreement between them would make both worthless. The icon is a
tomato and its walking dot again; `taskbar.py` took over the plate colours and
the font, and `app._badge` remains the single source both would have used.

**For the Mac:** added as a capability, and it is the one item on that list
that is *easier* there — a menu bar item takes a text title directly, so the
whole floating-window trick is unnecessary.

### Session close — 2026-08-10 (fourth sitting: reminder mode, honest breaks, tray countdown)

Three requested changes, all built and verified on Windows first — the Mac
gets a tested app, not a porting project.

**Reminder-only mode** (`Config.block_input`, a settings checkbox,
`--remind-only`). The design point worth keeping: **whether blocking is wanted
is a setting; whether it is possible is the OS's answer**, discovered by
asking. A checkbox claiming to control the second would lie about enforcement,
which is the worst failure this app has available — believing you were held to
a break you were not. ANDed, and the lock screen names the actual cause,
because "switched off in settings" and "this system will not allow it" send you
to different places. Useful well beyond the port: trusting the lock gradually,
or a day of presentations.

**`break_taken` vs `break_ignored`.** A break that can be walked away from can
be worked straight through, and filing those as taken would quietly make the
log useless for the one question it is kept to answer. Input during a break is
now counted — from how far the input watermark advanced, not the idle window,
so a mouse nudge buys a moment rather than a second, and capped at a tick so
waking the machine cannot credit the hours it slept. Past a quarter of that
break's length it reads as worked through, a fraction so it scales with a long
break and with `--demo`. The cycle still counts towards the long break either
way: withholding it on a heuristic about mouse movement would make enforcement
depend on a guess.

**A bug that found, three lines from where I was working.** `BREAK_ENDED` read
`is_long_break` off the snapshot, but `_reset_to_idle` clears it as the break
ends, before the event is handled — so **every break ever logged said "short",
long ones included**. All 17 rows in the live database, none ever "long".
Captured at `BREAK_STARTED` now. Exactly the shape of accounting bug the
history log exists to catch, and it took writing a second break statistic to
see it.

**The countdown in the tray icon.** Windows has no text slot beside a tray
icon, so the minutes are drawn *onto* it over a coloured plate, as battery
meters do. Colour distinguishes ordinary / long-break-next / on a break /
frozen by a call; nothing while idle or in Focus Mode. Only redrawn when the
number changes, since `refresh()` runs every second and the number moves once
a minute.

**Two habits earned their keep again.** The badge geometry was set by
rendering at real 16px and *looking* — the first attempt was legible only when
zoomed. Doing that caught the walking dot being buried under the new plate,
**and caught my own test passing anyway**: it asserted the two images differed,
and the plate's rounded corner left a sliver, so it was green while the thing
it checked for was invisible. It counts green pixels now. Assert on what
renders, not on what was requested.

**Verified:** 339 tests (31 new), pyflakes clean, the icon inspected at true
tray size across all four tones, and a scripted `Application` confirming the
badge reaches the tray, reminder mode reaches the lock screen, and a
worked-through long break reaches the log as `long; 0.8 min of input`.

**Next:** restart the app to pick all of this up. Then use it — whether a
quarter of a break is the right threshold, and whether two digits are readable
in your tray, are both questions only a few days of use can answer.

### Session close — 2026-08-10 (third sitting: prepared for a macOS port)

**Why:** a colleague with the same overfocusing problem wants the app, and
works on a Mac. There is no Mac here to test on, and no way to borrow one — the
only Mac available belongs to someone without a Claude account, and this
account is JumpCloud-gated, which is not going onto a personal machine. So
**she ports it herself**, on her machine, with her own Claude session. She is
not a developer. Everything this sitting produced exists to make that safe and
small.

**Measured before planning.** ~2,900 of ~6,000 lines are fully portable,
including the whole brain and every test. ~600 lines touch Windows. Two items
are not translations: the menu bar (pystray's Cocoa backend wants the main
thread and tkinter already owns it) and camera/mic detection (no registry
equivalent; the calendar meeting skip covers scheduled calls in the meantime).
Also confirmed by grep rather than assumed: **no absolute paths anywhere** —
every path derives from `__file__` or an env var, and `settings.py` already
falls back to `~/.config` with no `%APPDATA%`. The folder name is a non-issue.

**A trap, found by tracing the permission failure.** The lock's degraded mode
("the overlay still covers the screen, it just won't block input") only caught
`ImportError`. A refused macOS Accessibility permission is not one, so the
exception would escape `lock()` — called from the tick, whose last statement
schedules the next tick. The loop would stop rescheduling, leaving a
borderless, always-on-top, `WM_DELETE_WINDOW`-refused window on every screen
with nothing alive to remove it. Worse than not locking at all.

Fixed three ways: `start()` reports whether suppression is live and never
raises; each overlay window binds its **own** key events into the same queue,
so every gesture works without a global hook; and `tick()` stops reclaiming
the z-order when nothing is blocked — otherwise it drags a window she is
allowed to leave back over her work once a second. The bindings are installed
**unconditionally because they are self-selecting**: while suppression works
the keystroke never reaches a window, so they are inert. That covers the
failure no return value can detect — a hook that starts cleanly and receives
nothing.

**The seam is a registry, not a relocation** — a deliberate reversal after
reading the code. Every platform call already had a `sys.platform` guard that
degrades quietly, so per-OS backend modules would have moved working code that
only a live Windows session can exercise, for tidiness. That is the exact
shape of most bugs in this project's history. `platform.py` instead names each
capability with what the app loses without it, where the Windows version
lives, and how macOS should provide it; `--doctor` probes all 13. The one real
extraction is idle detection: `activity.py` now asks the OS "how long since
any input", **which needs no permission on either platform**, where the pynput
listener fallback would have needed the same permission as the lock.
`macos_idle_seconds()` is written and marked UNVERIFIED with the command to
check its format.

**Deliberately not done: writing the macOS implementations blind.** Confident
platform code that cannot be run is this project's most reliable bug source,
and she cannot tell wrong code from right code. She gets contracts and
guidance; her Claude implements on hardware that can run it.

**Verified:** 308 tests (55 new), pyflakes clean, `--doctor` reporting all 13
capabilities present here, a real cosmetic lock driven against actual tkinter
on three monitors with suppression forced to fail, a real `Application` ticked
with the rewritten monitor to confirm the monotonic time base, and the
shareable `git archive` inspected — 58 files, no chimes (licence), no calendar
export, no venv.

**Next:** she runs step 0 of `docs/MAC-PORT.md` — asking IT whether
Accessibility permission can be granted at all. That answer decides whether
this is a lock or a nudge, and it is worth having before any effort.

### Session close — 2026-08-10 (second sitting: the rhythm survives a restart)

**Reported from live use:** the app was quit and relaunched repeatedly through
the day to pick up new features, and the timer restarted from scratch every
time — including the count towards the 4th-cycle long break. Over a full
working day of 10 tracked breaks, **the long break never fired once**.

**Why it hid.** Every existing test drives one engine from start to finish, so
nothing exercised the one thing real use does constantly: rebuilding the engine.
And the symptom is silent by construction — a long break that never arrives is
indistinguishable from one that was not due yet. It took a day of using the app
to notice, which is the pattern the last session already flagged.

**What was built.** The cycle count and the part-finished interval now persist
in `state.json` (schema 2 → 3, old files load unchanged) and are reloaded at
launch. `timer.py` gained a `Position` pair with `position()`/`resume()`;
`state.py` gained `with_position()`/`resumable_position()`. Four rules keep a
restored position honest — see docs/SPEC.md §2.2:

- It ages out on `idle_reset_after`, the same 60-minute gap that discards a
  position mid-run, so quitting for the evening doesn't hand the morning three
  free cycles. The stamp records when the position was last *true*, not last
  written — otherwise an idle hour before quitting would refresh it.
- The part-interval is **credit, not a running session**: a saved file can't say
  you're at the desk, so it's paid in only after `start_threshold` sees real
  work, and it expires on the same idle gap.
- A break in progress is not resumed — no lock at launch.
- Only timing is restored; hours worked are never re-credited.

**Two things made visible**, since the count had no trace of its own: the tray
now says "12 min to a **long** break", and `--events` records `cycles_resumed`
and `cycles_reset`.

**A second bug found by testing the first.** Verifying it meant building a real
`Application` against a scratch `state.json` — and it wrote to the **live**
`history.db` anyway, because `Application` hardcoded the default settings path
and ignored `--config`. Three rows landed in a real working day, one of them a
`{"worked": 0, "walked": 0}` snapshot that made today's walking read 0 min under
the last-row-wins rule. Rows deleted by id after review, `--history` confirmed
back to 4.8h / 69 min, and `--config` now moves the whole data directory
(`report_exclusions` had the same split). Logged in `ISSUES.md`, including the
guardrail that isn't automated yet: assert a dev `Application`'s history and
state paths are inside a scratch directory *before* constructing it.

**Housekeeping:** the `laivly-global-session` hard gate is gone. It was never
installed on this machine, so two sessions in a row hit an unsatisfiable gate on
their first move; the practices it governed are now written out in `CLAUDE.md`
and this project requires no skill.

**Verified:** 255 tests (16 new in `tests/test_resume.py`, including the
reported bug end to end — four launch/quit cycles, asserting the 4th break is
the long one), pyflakes clean, the live schema-2 state file confirmed to load
under schema 3, and a scripted `Application` launch showing
`resuming a previous run — cycle 4 of 4, 20 min into the interval` followed by
`2 min to a long break` in the tray.

**Next:** restart the app to pick this up — the first launch after it has no
saved position, so it starts at cycle 1 and the first long break is four
intervals away. After that, `--events` will show `cycles_resumed` on every
relaunch.

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

**Verified on the machine:** the tray icon appears, the menu opens on **left click** (the `WM_LBUTTONUP` remap works), every menu item behaves — both walk sessions tracked, and all three day-type override states applied and reverted, including falling back to `day off (weekend)` when the override was cleared on a Sunday. Dragging the icon out of the overflow pins it, and the pin survives a restart.

### Session close — 2026-08-10 (first day of live use)

The app ran through a real working day, and that produced more real bugs than
any amount of review had. All eight phases were already complete at the start
of the day; everything since has been feedback.

**Found by using it:**
- The 2-minute warning fired correctly and went **unnoticed** — a small static
  toast on one screen while the work was on another. Now shown on every
  monitor, arriving large and centred before shrinking into the corner.
- A **paused browser video started playing** on a break. The break chime played
  first, the audio guard heard it, and the toggle un-paused the video. Fixed by
  excluding our own process from the guard *and* chiming after the lock — then
  superseded by making media pause a lock-screen key rather than automatic.
- **`excluded — presenting` fired three times in eight seconds** with nothing
  presented. `QUNS_BUSY` covers any full-screen window, this app's own lock
  included. The whole presenting check was then removed as redundant.
- Reading-heavy work under-counted, because a 30s input gap stopped the clock
  mid-message. Now 90s.
- CLI output vanished into the log file, and `--exclusions` was refused while
  the app was running. Both regressions from the same logging change.
- Five stock mp3s were committed to a **public** repo by `git add -A`, contrary
  to the Pixabay licence. Purged from history and force-pushed.

**Contributor decisions that improved the design**, each overriding what was
built: media pause as an offered key rather than a guess; the treadmill timer
stoppable from the lock, since over-counting walking *raises* the work cap;
selectable chimes rather than a filename convention; and asking whether the
presenting exception was needed at all, which it was not.

**Next:** nothing is queued. Use it, read `--history`, and change the numbers
that turn out to be wrong. The one outstanding verification is the lock
screen's `M` and `W` keys — see the Handoff section above.

### Session close — 2026-08-09 (second sitting)

**Phases 2, 3, 4 and 6 complete, plus the tray from Phase 7.** Test count went 16 → 202. `main` clean and pushed throughout.

**The through-line of the whole day: code that read correctly, passed its tests, and was wrong.** Nine bugs found this way, none by review — a `NameError` on a path that had never executed, `root.after()` from a listener thread, Escape-hold depending on key auto-repeat, the hold that opened the skip menu dismissing it, three failed media mechanisms, a UTC/local date comparison, and a tkinter padding tuple. Every one needed something real: a live call, a real keyboard, a playing video, an evening clock.

**The method lesson is as valuable as any of the fixes.** Three timed on-screen diagnostics produced no usable data at all, because their prompts print to a console sitting behind a lock. What worked was `local/checklock.py` and `local/checkwalk.py`: scripts the contributor runs, seeing prompts live, with the script reporting afterwards. Reuse that shape for anything that only runs behind a lock or needs a real click.

**Contributor decisions that improved the design**, each overriding what was specced or built:
- Past the cap, shorten work intervals rather than switch the app off — the literal spec would have removed breaks exactly when most needed.
- Reverse the banner's hover: visible by default, fading on approach. A warning has two minutes to be noticed.
- Hold breaks off *before* a meeting, not just during — a lock three minutes before a call is worse than one inside it.
- Simplify the media controls to a single automatic pause after the key-based version proved erratic.
- A tray control for walking, because a prompt you must wait for is not a tracker.

**Next:** Phase 5 (Focus Mode, docs/SPEC.md §6) is small — one more capped override, and the machinery exists. Phase 7's SQLite history log would show whether the caps and walking target are set right. Phase 8 packaging is what decides whether this gets used daily.

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
