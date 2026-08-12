# Running Pomodoro Guardian on a Mac

This app was built and used on Windows. Most of it is plain Python that runs
anywhere; a handful of pieces talk to Windows directly and need a macOS
equivalent written. This document is the plan for doing that.

**Who this is for:** whoever is setting it up on a Mac, working with Claude
Code on that machine. You do not need to be able to write Python. You do need
to be able to open Terminal, paste commands, and read what comes back — and
to stop when something looks wrong rather than pressing on.

**What Claude needs from you:** point it at this file first. Everything it
should know about the port is here or linked from here, including the things
that have already been tried and failed, so it doesn't spend a session
rediscovering them.

---

## 0. Do this before anything else (10 minutes)

The app's whole point is a break you cannot click away from. Blocking the
keyboard and mouse on macOS needs **Accessibility permission**, granted by
hand — and on a company-managed Mac, IT can forbid it.

**Ask IT: "can I grant Accessibility permission to a Python program I run
myself?"**

- **Yes** → the full app is possible, lock included.
- **No, or nobody knows** → you can still have a genuinely useful version.
  See "If the lock is not allowed" below. Find out now rather than after a
  week of work.

This is the same wall a packaged `.exe` build hit on the Windows machine, so
it's a normal thing to run into, not a sign anything is wrong.

---

## 1. What you're getting

The app has two halves.

**The brain — works on a Mac already, unchanged.** Detecting that you're
working, the 25/5 rhythm, the long break every 4th cycle, the daily work cap,
the walking tally, the calendar meeting skip, the history log, the settings
window — plus reminder mode, and the record of which breaks were actually
taken rather than worked through, and time on calls counting towards the daily
cap. Roughly 3,000 lines and **all 416 automated tests**. Nothing here needs
porting.

**The hands — these touch the operating system.** Blocking input, covering
every screen, the menu bar icon, playing chimes, detecting the camera and
microphone, starting at login. This is the work, and it's about 600 lines.

**One module is now much less Windows-bound: `pill.py`.** It draws the corner
countdown and the two-minute warning, and it is Tk plus Pillow apart from one
call — `-transparentcolor`, which keys the pill's silhouette out. On macOS use
a transparent Toplevel instead, which gives real per-pixel alpha and *better*
edges than the keying trick. This replaced a 342-line module that faked a chip
inside the Windows taskbar; that whole approach is gone, so there is nothing
left here to port around.

Run `--doctor` (step 3) and the app will tell you exactly which of these
your machine currently has.

---

## 2. Setting it up

### Install Python

Download the **macOS installer from [python.org](https://www.python.org/downloads/)**
and run it. Version 3.12 or newer.

> Use the python.org installer rather than Homebrew's. The app's windows are
> drawn with Tk, which the python.org build includes and Homebrew's does not —
> with Homebrew you also need `brew install python-tk`, and the error when you
> forget is baffling.

Check it worked. Open Terminal and paste:

```sh
python3 --version
```

### Get the app

Unzip the folder wherever you like — Documents is fine. **The location and
folder name genuinely do not matter**; the app works out where it lives.

Then `cd` into it. The easiest way: type `cd ` (with the space), drag the
folder from Finder onto the Terminal window, and press Return.

### Install what it needs

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

This creates a private folder for the app's libraries, so nothing is
installed system-wide. Some Windows-only packages are skipped
automatically — that's expected, not an error.

### Prove the brain works

```sh
.venv/bin/python -m pytest tests
```

**You should see 416 passing.** This is the moment worth having early: it
means every rule about breaks, caps, walking and history is intact on your
machine, and anything that goes wrong from here is in the OS-facing parts
only.

---

## 3. Find out what your Mac can do

```sh
.venv/bin/python -m pomodoro_guardian --doctor
```

You'll get a list of capabilities, each `ok` or `MISSING`, and for anything
missing: what the app loses without it, where the Windows version lives, and
how macOS should provide it. **Paste this whole output to Claude** — it's the
most useful thing you can give it.

The list is in priority order. Work down it.

---

## 4. The port, in stages

Each stage ends with something that works. Don't skip ahead — stage 3 is the
one that can lock you out, and it's much safer once 1 and 2 are solid.

### Stage 1 — Make it see you working

**Implement `macos_idle_seconds()` in `pomodoro_guardian/activity.py`.**

There's already a version written, but nobody has run it on a Mac. It asks
macOS how many seconds since you last touched the keyboard or mouse. Check
the format it expects is right:

```sh
ioreg -c IOHIDSystem | grep HIDIdleTime
```

Compare that to the comment in the function and tell Claude what you saw.

**Why this is first:** it needs **no permission at all**. It asks how long
you've been idle, never what you typed, so there's nothing for macOS to gate.
Everything else in the app is built on this one number. Get it right and the
timer, the work tracking, the daily cap, the history and the warnings all
start working — with nothing granted.

Then run the app for real:

```sh
.venv/bin/python -m pomodoro_guardian --dry-run
```

`--dry-run` never covers your screen. It just prints what it's doing. Type
for a minute and you should see `work session started`.

### Stage 2 — Chimes and starting at login

Both small and both safe.

- **Chimes**: `afplay <file>` in place of the Windows call in `sounds.py`.

  Then you need the sound files. **They are deliberately not in the
  package** — they're free Pixabay clips whose licence allows any use but
  not redistributing the files themselves, so everyone downloads their own
  (`ATTRIBUTION.md` has the reasoning). It takes two minutes: open any of
  these, press the download button, and drop the file into
  `assets/sound-effects/`.

  - [Bell Notification](https://pixabay.com/sound-effects/bell-notification-337658/)
  - [Notification Bell Sound 1](https://pixabay.com/sound-effects/notification-bell-sound-1-376885/)
  - [Bell chord1](https://pixabay.com/sound-effects/bell-chord1-83260/)
  - [bell sound](https://pixabay.com/sound-effects/bell-sound-370341/)
  - [Bell Ring](https://pixabay.com/sound-effects/bell-ring-123742/)

  Two is enough — one for a break starting, one for it ending. Anything
  short works; these are just the ones already in use on the Windows side.
  Pick them in Settings, or run `--test-sounds` to hear what you have. The
  app treats an empty folder as normal and stays silent, so this is never
  a blocker.
- **Start at login**: a LaunchAgent file, replacing the Windows shortcut in
  `runtime.py`. Worth doing early: an app you have to remember to start is
  the habit this thing exists to replace.

### Stage 3 — The break screen

Read the safety rules below **before** starting this stage.

Two parts, and they're independent:

1. **Covering the screen.** Should mostly work already — it's Tk. On several
   monitors it may only cover one at first; that's the `monitors` capability.
2. **Blocking input.** Needs the Accessibility permission from step 0. macOS
   will prompt you the first time; if it doesn't, add the app by hand under
   **System Settings → Privacy & Security → Accessibility** (and check
   **Input Monitoring** too).

**Do part 1 first and stop there for a while.** Add `--remind-only` and you
have the whole break screen with no permission involved and nothing that can
take your keyboard — a full-screen countdown you can click past. That is a
genuinely useful app, it is the safest possible way to test the screen
covering, and it means part 2 becomes a decision rather than a prerequisite.

**The app already handles being refused, too.** If blocking is wanted but the
system won't allow it, the break screen still appears on every monitor and
says so — and it distinguishes *"enforcement is switched off in settings"*
from *"this system will not let input be blocked"*, so you always know which
you are looking at. Either way you can dismiss it or click away.

Test with short intervals so you're never waiting 25 minutes:

```sh
.venv/bin/python -m pomodoro_guardian --demo 60 --remind-only
```

That turns 25 minutes of work into 25 seconds and a 5-minute break into 5
seconds. Watch a full cycle, then four in a row to see the long break. Drop
`--remind-only` only once you have watched it behave and are ready to let it
hold the keyboard.

### Stage 4 — The menu bar, and calls

**The app refuses to start a menu bar icon on macOS, on purpose.** It would
otherwise try to run pystray's Cocoa backend on a background thread, and AppKit
refuses that at a level below Python — it can kill the process outright rather
than raise an error anything could catch. That would have hit you at the worst
moment: the first launch that is not `--dry-run`, which is exactly when you are
testing the break screen for the first time.

So instead you will see this at startup, and the app carries on:

```
no tray icon: the macOS menu bar has to run on the main thread, which
tkinter already owns — see docs/MAC-PORT.md, stage 4
  so no walk toggle, no settings and no Quit — stop the app with Ctrl+C
```

That is expected until this stage is done. The guard is one line —
`MAIN_THREAD_ONLY` at the top of `tray.py` — so when you have a menu bar that
works, remove `"darwin"` from it.

**The menu bar is a real design decision, not a translation.** The Windows
version uses `pystray` on a background thread. On macOS that library needs
the *main* thread, and the window code already owns it — two libraries, one
main thread. Options: `rumps` instead, or a different way to reach the
controls. Until it's solved you have no menu, which means **no way to start a
walk and no way to quit except Ctrl+C** in the Terminal you started it from.

**The countdown no longer depends on it.** It used to live in the tray, so it
was blocked behind the menu bar problem; it is now a pill in the bottom-right
corner drawn by `pill.py`, which works without a menu bar at all. If you would
rather have it as a menu bar title once that is solved, `app._badge` returns
the number and a colour name and is fully portable.

**Camera and microphone detection** — so a break never lands in the middle of
a call — has no macOS equivalent to what Windows uses, and is genuine
research. **You don't need it to start.** The calendar meeting skip already
covers anything booked on your work calendar, and it's fully portable. Until
then, run with:

```sh
.venv/bin/python -m pomodoro_guardian --no-exclusions
```

That turns the mechanism off honestly rather than half-working.

---

## 5. Safety rules

The lock blocks your keyboard and mouse. On a work machine, on a working day,
that deserves respect.

1. **Start in reminder mode and stay there until you want the lock.**
   `--remind-only` gives you every break, on every screen, with nothing able
   to touch your keyboard. There is no rush to enable blocking on a work
   machine, and nothing you learn from it is lost when you do.
2. **Leave the escape hatch on.** Holding **Escape for 3 seconds** releases
   any break. It's on by default. **Never pass `--no-safety-unlock`** while
   this is new code on your machine.
3. **Know how to kill it before you need to.** From a second Terminal window:
   ```sh
   pkill -f pomodoro_guardian
   ```
   Activity Monitor works too. There's also a built-in failsafe: a watchdog
   releases input suppression if a lock ever outlives its break by 60
   seconds, and it's deliberately independent of the rest of the app, so it
   survives the app freezing.
4. **Test with `--demo 60`, never with real 25-minute intervals.** A bug in a
   5-second break costs you 5 seconds.
5. **Don't test the lock right before a meeting.** Obvious, easy to forget.
6. **If Claude isn't sure whether something is safe, stop and ask.** You are
   approving code you can't read. That's fine for a chime; it is not fine for
   code that takes over your keyboard. "I'm not certain" is a reason to wait.

---

## 6. If the lock is not allowed

If IT says no to Accessibility permission, you don't lose the app — you lose
enforcement. What still works:

- Automatic detection of when you're actually working, with no permissions
- The 25/5 rhythm and the long break every 4th cycle
- The 2-minute warning before each break, on every screen
- **The break screen, covering every monitor**, with its countdown — you can
  click away from it, but you cannot miss it
- The daily work cap, the walking tally, and the full history log

For overfocus, an unmissable full-screen reminder plus honest data about your
own patterns is a real intervention. It is not the same as a lock, and the
app will say so on screen rather than pretending.

**You don't have to wait for IT's answer to use this.** Reminder mode is a
setting, not just what happens when permission is refused. Turn it on in the
settings window ("Block the keyboard and mouse during a break"), or for a
single run:

```sh
.venv/bin/python -m pomodoro_guardian --remind-only
```

That is a good way to start regardless of what IT says — the same way the
3-second Escape release was kept on here while the lock was still new code.
Live with the reminders for a week, then decide whether you want the lock.
The break screen names which of the two it is, so you are never left guessing
whether enforcement is off because you chose it or because the system refused
it.

**The honesty problem this creates is already solved.** A break you can walk
away from is a break you can work straight through, and recording those as
"taken" would quietly make your history useless. The app counts real input
during a break — idle time needs no permission, so this works in reminder
mode — and past a quarter of that break's length records it as
`break_ignored`, reported by `--history` as "worked through". For overfocus
that may be the single most useful number the app produces, and you get it
whether or not the lock ever works.

---

## 7. Don't rediscover these

Hard-won on the Windows side. `docs/SPEC.md` has the full accounts.

- **Media keys are toggles.** Three separate mechanisms were measured and
  failed before a real keystroke worked, and firing one into silence *starts*
  playback — which un-paused a deliberately paused video. Automatic pausing
  is off by default and the lock offers a key instead. Don't make it
  automatic. (`docs/SPEC.md` §2.3)
- **Detecting calls by looking for Zoom or Teams doesn't work.** Browser
  calls are invisible that way, because Chrome is always running. Windows
  answers "who is holding the microphone" instead. Whatever macOS offers,
  aim for that question. (`docs/SPEC.md` §3)
- **Never call the window code from a background thread.** Input listeners
  and the menu bar both run on their own threads; they post messages that the
  main loop picks up. This is not optional tidiness — it raises outright.
- **The calendar is free/busy only.** Every event says `Busy`, nothing more.
  Every calendar rule has to work from time ranges alone. (`docs/SPEC.md` §9)
- **A day off is detected by a busy block of 6 hours or more.** Measured
  against 706 real events, not guessed. Don't "improve" it without data.
- **Test what the app actually renders, not what it was asked to render.**
  Three separate layout bugs passed their tests because the tests checked the
  request.

---

## 8. What to send back

If you hit something this document doesn't cover, the useful things to share:

1. The full `--doctor` output
2. What you ran and what it printed, in full — including the error
3. `.venv/bin/python -m pomodoro_guardian --events 20` if it's about breaks,
   caps or walking, which prints the app's own record of what it decided

The Windows version is still being used and fixed daily, so if something
looks wrong in the shared logic rather than the Mac parts, it's worth
reporting back — it may be a real bug in both.
