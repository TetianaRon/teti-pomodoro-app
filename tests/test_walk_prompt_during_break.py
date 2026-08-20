"""Tests for the walk prompt and the break lock not colliding.

Two live-use failures, reported together: the walk prompt flashed through a
break, and W on the lock screen would not stop the walk. Both trace to the
same cause — the walk prompt is a focusable always-on-top window, and it
was left up during a break. It and the lock then reassert always-on-top on
every tick (the flashing), and whenever the prompt won that race it took
the focus the lock's local W key needs (the walk could not be stopped).

The fix takes the prompt down for the duration of a break and brings it
back afterwards. These drive the real `_update_walking` with the tkinter
pieces stubbed, so the wiring is checked without a display — the same
`Application.__new__` approach the enforcement-mode tests use.

Separately, clearing stuck modifiers when suppression ends: a suppressing
hook can swallow a modifier's key-up, leaving Ctrl "down" so the browser
zooms on scroll until a re-login. `stop()` now releases the modifiers, but
only when it was really suppressing.
"""

from __future__ import annotations

from datetime import datetime

from pomodoro_guardian.app import Application
from pomodoro_guardian.overlay import InputSuppressor


class FakePrompt:
    """The walk prompt surface `_update_walking` touches."""

    def __init__(self, visible: bool = False) -> None:
        self.visible = visible
        self.hidden = 0
        self.shown: list[str] = []
        self.updates = 0

    def hide(self) -> None:
        self.visible = False
        self.hidden += 1

    def show(self, headline: str) -> None:
        self.visible = True
        self.shown.append(headline)

    def update(self, **_kwargs) -> None:
        self.updates += 1


class FakeOverlay:
    def __init__(self, visible: bool) -> None:
        self.visible = visible


class FakeSettings:
    def __init__(self, reminder_times=(), target_minutes=60.0) -> None:
        #: Empty by default, so `due_prompt` never fires and a test can
        #: isolate the hide/restore behaviour from reminder scheduling.
        self.walking_reminder_times = reminder_times
        self.walking_target_minutes = target_minutes


class FakeState:
    def __init__(self, walked_seconds=0.0, walking=False) -> None:
        self.walk_prompts_done: tuple[str, ...] = ()
        self.walking = walking
        self._walked = walked_seconds
        self.shown_labels: list[str] = []

    def walked_including_current(self) -> float:
        return self._walked

    def with_prompt_shown(self, label: str) -> "FakeState":
        self.shown_labels.append(label)
        self.walk_prompts_done = self.walk_prompts_done + (label,)
        return self


def app(
    *,
    break_locked: bool,
    prompt_up: bool,
    hidden_flag: bool,
    reminder_times=(),
    target_minutes=60.0,
    walked_seconds=0.0,
) -> Application:
    instance = Application.__new__(Application)
    instance.dry_run = False
    instance.overlay = FakeOverlay(break_locked)
    instance.walk_prompt = FakePrompt(prompt_up)
    instance._walk_prompt_hidden_for_break = hidden_flag
    instance.settings = FakeSettings(reminder_times, target_minutes)
    instance._state = FakeState(walked_seconds)
    instance._save_state = lambda: None
    instance._log = lambda _message: None
    return instance


def due_now() -> str:
    """A reminder time that `due_prompt` will treat as due this tick.

    The current minute is always within `due_prompt`'s five-minute
    look-back window, and time only moves forward between here and the
    method's own `datetime.now()`, so this never races the window shut.
    """
    return datetime.now().strftime("%H:%M")


# -- the prompt gets out of the lock's way -----------------------------


def test_a_visible_prompt_is_taken_down_when_the_lock_goes_up():
    instance = app(break_locked=True, prompt_up=True, hidden_flag=False)

    instance._update_walking()

    assert not instance.walk_prompt.visible
    assert instance.walk_prompt.hidden == 1
    assert instance._walk_prompt_hidden_for_break is True


def test_a_prompt_already_down_is_not_flagged_for_restore():
    """Only a prompt *we* hid should come back; a dismissed one stays gone."""
    instance = app(break_locked=True, prompt_up=False, hidden_flag=False)

    instance._update_walking()

    assert instance._walk_prompt_hidden_for_break is False
    assert instance.walk_prompt.hidden == 0


def test_the_prompt_is_not_refreshed_while_the_lock_is_up():
    """No update() means no per-tick always-on-top fight with the lock."""
    instance = app(break_locked=True, prompt_up=True, hidden_flag=False)

    instance._update_walking()

    assert instance.walk_prompt.updates == 0


# -- and comes back afterwards -----------------------------------------


def test_the_prompt_returns_after_the_break_if_it_was_up_before():
    instance = app(break_locked=False, prompt_up=False, hidden_flag=True)

    instance._update_walking()

    assert instance.walk_prompt.visible
    assert instance.walk_prompt.shown == ["Time to walk"]
    assert instance._walk_prompt_hidden_for_break is False


def test_the_prompt_stays_down_after_a_break_if_it_was_not_up_before():
    instance = app(break_locked=False, prompt_up=False, hidden_flag=False)

    instance._update_walking()

    assert not instance.walk_prompt.visible
    assert instance.walk_prompt.shown == []


# -- the reminder only fires while a walk is still owed ----------------


def test_a_due_reminder_pops_up_when_the_goal_is_not_yet_met():
    instance = app(
        break_locked=False, prompt_up=False, hidden_flag=False,
        reminder_times=(due_now(),), target_minutes=60.0, walked_seconds=10 * 60,
    )

    instance._update_walking()

    assert instance.walk_prompt.visible
    assert instance.walk_prompt.shown == ["Time to walk"]


def test_a_due_reminder_is_suppressed_once_the_goal_is_met():
    instance = app(
        break_locked=False, prompt_up=False, hidden_flag=False,
        reminder_times=(due_now(),), target_minutes=60.0, walked_seconds=60 * 60,
    )

    instance._update_walking()

    assert not instance.walk_prompt.visible
    assert instance.walk_prompt.shown == []


def test_a_suppressed_reminder_is_still_consumed_so_it_asks_once():
    """Marked shown even when skipped, or it would re-evaluate every tick."""
    label = due_now()
    instance = app(
        break_locked=False, prompt_up=False, hidden_flag=False,
        reminder_times=(label,), target_minutes=60.0, walked_seconds=60 * 60,
    )

    instance._update_walking()

    assert instance._state.shown_labels == [label]


def test_walking_past_the_goal_still_suppresses_the_reminder():
    """The check is >= target, so overshooting the goal counts as met."""
    instance = app(
        break_locked=False, prompt_up=False, hidden_flag=False,
        reminder_times=(due_now(),), target_minutes=60.0, walked_seconds=90 * 60,
    )

    instance._update_walking()

    assert not instance.walk_prompt.visible


def test_a_held_prompt_does_not_return_after_a_break_if_the_goal_is_met():
    instance = app(
        break_locked=False, prompt_up=False, hidden_flag=True,
        target_minutes=60.0, walked_seconds=60 * 60,
    )

    instance._update_walking()

    assert not instance.walk_prompt.visible
    assert instance.walk_prompt.shown == []
    assert instance._walk_prompt_hidden_for_break is False


# -- stuck modifiers are cleared, but only after real suppression ------


def test_stop_clears_modifiers_only_when_it_was_suppressing():
    released: list[bool] = []
    suppressor = InputSuppressor()
    suppressor._release_stuck_modifiers = lambda: released.append(True)

    # Never suppressed — no listeners, so nothing to clear.
    suppressor.stop()
    assert released == []

    # Now stand in a live listener: stopping it must clear the modifiers.
    class _Listener:
        def stop(self) -> None:
            pass

    suppressor._listeners = [_Listener()]
    suppressor.stop()
    assert released == [True]
