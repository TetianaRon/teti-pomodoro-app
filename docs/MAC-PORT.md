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
window. Roughly 3,000 lines and **all 308 automated tests**. Nothing here
needs porting.

**The hands — these touch the operating system.** Blocking input, covering
every screen, the menu bar icon, playing chimes, detecting the camera and
microphone, starting at login. This is the work, and it's about 600 lines.

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

**You should see 308 passing.** This is the moment worth having early: it
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
  Then drop a couple of short sound files into `assets/sound-effects/` —
  they aren't included, deliberately (see `ATTRIBUTION.md`).
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

**The app already handles being refused.** If it can't block input, the break
screen still appears on every monitor with its countdown, says *"reminder
only — input is not blocked"*, and lets you dismiss it or click away. That is
a deliberate mode, not a broken one.

Test with short intervals so you're never waiting 25 minutes:

```sh
.venv/bin/python -m pomodoro_guardian --demo 60
```

That turns 25 minutes of work into 25 seconds and a 5-minute break into 5
seconds. Watch a full cycle, then four in a row to see the long break.

### Stage 4 — The menu bar, and calls

**The menu bar is a real design decision, not a translation.** The Windows
version uses `pystray` on a background thread. On macOS that library needs
the *main* thread, and the window code already owns it — two libraries, one
main thread. Options: `rumps` instead, or a different way to reach the
controls. Until it's solved you have no menu, which means **no way to start a
walk and no way to quit except Ctrl+C** in the Terminal you started it from.

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

1. **Leave the escape hatch on.** Holding **Escape for 3 seconds** releases
   any break. It's on by default. **Never pass `--no-safety-unlock`** while
   this is new code on your machine.
2. **Know how to kill it before you need to.** From a second Terminal window:
   ```sh
   pkill -f pomodoro_guardian
   ```
   Activity Monitor works too. There's also a built-in failsafe: a watchdog
   releases input suppression if a lock ever outlives its break by 60
   seconds, and it's deliberately independent of the rest of the app, so it
   survives the app freezing.
3. **Test with `--demo 60`, never with real 25-minute intervals.** A bug in a
   5-second break costs you 5 seconds.
4. **Don't test the lock right before a meeting.** Obvious, easy to forget.
5. **If Claude isn't sure whether something is safe, stop and ask.** You are
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

**One thing worth building if you land here:** in this mode the history would
record every break as "taken" even if you worked straight through it, which
would quietly make your data useless. Since idle time is available without
permission, the app can watch whether you actually stopped and record
`break_taken` versus `break_ignored`. That turns the weakness into the most
useful thing it could tell you.

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
