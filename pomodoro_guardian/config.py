"""Tunable timings for the core loop.

Everything is seconds, so the state machine never has to do unit maths.
Values live here rather than in the engine because docs/SPEC.md §10 flags
several of them as still-open decisions — changing one should be a
one-line edit, not a hunt through the logic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

MINUTE = 60


@dataclass(frozen=True)
class Config:
    """Timing knobs for one Pomodoro rhythm."""

    # --- Rhythm (SPEC §2.2) ---
    work_duration: float = 25 * MINUTE
    short_break_duration: float = 5 * MINUTE
    long_break_duration: float = 15 * MINUTE
    long_break_every: int = 4

    # --- Break enforcement (SPEC §2.3) ---
    # How long before the lock the warning appears, so you can wrap up.
    warning_lead: float = 2 * MINUTE

    # The warning banner is click-through, so it can never block anything.
    # It stays clearly readable by default — it has two minutes to be
    # noticed, and a warning nobody sees is useless — then fades as the
    # cursor approaches, so you can see whatever it is sitting over.
    # Hover is detected by polling the cursor, since a click-through window
    # receives no mouse events of its own.
    banner_alpha: float = 0.90
    banner_alpha_hover: float = 0.15
    banner_hover_poll: float = 0.12

    # --- Past the daily cap (SPEC §5) ---
    # Going over the cap does not switch the app off — that would mean no
    # breaks exactly when you are most tired. The work interval shortens
    # sharply instead, so breaks become a standing nudge to stop while
    # leaving room to wrap up or start Emergency Mode.
    overtime_work_duration: float = 5 * MINUTE
    # 2 minutes against a 5-minute interval would leave the banner up
    # almost half the time.
    overtime_warning_lead: float = 1 * MINUTE
    emergency_grant_hours: float = 1.0

    # --- Custom break skip (SPEC §4B) ---
    # Fixed choices, offered by the hold-Escape menu against the lock. All
    # skips draw on one accumulated daily budget; once it is spent the menu
    # still opens, with every option greyed out, so an exhausted budget
    # reads as a limit rather than a broken key.
    custom_skip_options: tuple[float, ...] = (5 * MINUTE, 10 * MINUTE, 20 * MINUTE)
    custom_skip_daily_budget: float = 60 * MINUTE

    # --- Never-interrupt exclusions (SPEC §3) ---
    # Which signals hold a break off. Camera and microphone are read from
    # the registry keys behind Windows' own privacy indicator; presenting
    # comes from SHQueryUserNotificationState.
    exclude_on_camera: bool = True
    exclude_on_microphone: bool = True
    exclude_on_presenting: bool = True
    # A break held off this long without a break is worth saying out loud —
    # usually an app holding the mic open rather than a real six-hour call.
    # Only warns; it does not override the exclusion.
    exclusion_warn_after: float = 2 * 60 * MINUTE

    # --- Detection (SPEC §2.1) ---
    # Input is "active" if the last keystroke/click was this recent. Bridges
    # the ordinary gaps in real typing so reading a paragraph mid-sentence
    # doesn't read as stepping away.
    input_gap: float = 30.0
    # Sustained activity needed to auto-start a tracked work session.
    start_threshold: float = 1 * MINUTE

    # --- Idle handling (SPEC §2.4) ---
    # Past this, a work session stops accruing time — you're not there.
    idle_pause_after: float = 2 * MINUTE
    # Past this, you've genuinely left: the part-finished work interval is
    # discarded and the long-break cycle count resets. This is the
    # "reset after an idle gap" answer to SPEC §10's open question,
    # chosen over a fixed daily reset time (Session 3).
    idle_reset_after: float = 60 * MINUTE

    # --- Robustness ---
    # A tick longer than this means the machine slept, the process was
    # suspended, or the clock jumped. Treated as an idle gap rather than
    # silently credited as work time.
    max_tick: float = 30.0

    # --- Safety (Phase 1 only; see overlay.py) ---
    # Holding Escape for this long releases the lock. Enforcement is
    # deliberately weakened while the lock is still unproven code — set
    # safety_unlock to False once you trust it not to strand you.
    safety_unlock: bool = True
    safety_unlock_hold: float = 3.0

    # Pause whatever is playing as the lock goes up. Sends a real media
    # key, but only when audio is genuinely playing — the key is a toggle,
    # so firing it into silence would start playback. Media is not resumed
    # afterwards: the break exists to get you away from the screen, and
    # un-pausing on your behalf would be a surprise, not a courtesy.
    pause_media_on_lock: bool = True

    # Hard ceiling on input suppression, enforced by a plain thread rather
    # than the UI loop. If the lock ever outlives its break by this much —
    # a hung tkinter loop, a crashed tick, a bug — suppression releases
    # itself. This is the safety property that survives closing the
    # Escape route in Phase 3 (docs/SPEC.md §4B): it guards against the
    # app failing, not against the user.
    lock_max_overrun: float = 60.0

    def scaled(self, factor: float) -> "Config":
        """Return a copy with every duration divided by `factor`.

        Used by the `--demo` smoke test so a full 25/5 cycle plus a long
        break can be watched end to end in a couple of minutes.
        """
        if factor <= 0:
            raise ValueError("scale factor must be positive")
        return replace(
            self,
            work_duration=self.work_duration / factor,
            short_break_duration=self.short_break_duration / factor,
            long_break_duration=self.long_break_duration / factor,
            warning_lead=self.warning_lead / factor,
            input_gap=self.input_gap / factor,
            start_threshold=self.start_threshold / factor,
            idle_pause_after=self.idle_pause_after / factor,
            idle_reset_after=self.idle_reset_after / factor,
        )


DEFAULT = Config()
