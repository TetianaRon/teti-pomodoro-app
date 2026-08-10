"""Tests for a lock that cannot block input (docs/MAC-PORT.md).

Input suppression needs a permission the OS can refuse — Accessibility on
macOS. When it is refused the overlay still covers every screen, which is a
useful nudge on its own, but three things have to hold or it becomes a trap
rather than a reminder:

* the failure must not escape into the tick, whose last statement schedules
  the next tick — an exception there leaves a borderless always-on-top
  window with nothing alive to remove it;
* the gestures must still work, through tkinter's own key events rather
  than a global hook;
* the window must stop fighting for the z-order, or leaving it is impossible
  even though nothing is blocked.
"""

from __future__ import annotations

from pomodoro_guardian.config import MINUTE, Config
from pomodoro_guardian.overlay import (
    InputSuppressor,
    LockOverlay,
    SkipOffer,
    SkipOption,
)


class Event:
    """The two attributes _translate reads off a tkinter key event."""

    def __init__(self, char: str = "", keysym: str = "") -> None:
        self.char = char
        self.keysym = keysym


class Root:
    """Enough of tk.Tk to run the hold timer without a display."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[int, callable]] = {}
        self.cancelled: list[str] = []
        self._next = 0

    def after(self, ms, func):
        self._next += 1
        job = f"job{self._next}"
        self.jobs[job] = (ms, func)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)
        self.jobs.pop(job, None)

    def fire(self, job):
        _ms, func = self.jobs.pop(job)
        func()


class Overlay(LockOverlay):
    """LockOverlay with the tkinter parts stubbed out."""

    def __init__(self, config: Config | None = None, on_skip=None):
        self.taken = []
        self.root = Root()
        super().__init__(
            root=self.root,
            config=config or Config(),
            skip_offer=self._default_offer,
            on_skip=on_skip or self.taken.append,
        )
        self._windows = [object()]
        self._bodies = [object()]

    @staticmethod
    def _default_offer():
        return SkipOffer(
            (SkipOption(5 * MINUTE, "5 min", True),), remaining=30 * MINUTE
        )

    def _build_menu(self, body):
        return object()

    def _close_menu(self):
        self._menus = []
        self._menu_open = False


# -- the failure must never reach the tick -----------------------------


def test_a_refused_hook_reports_failure_rather_than_raising():
    """The macOS case: pynput imports fine, then the OS refuses the tap."""
    suppressor = InputSuppressor()

    def refuse():
        raise OSError("not trusted for accessibility")

    suppressor._keyboard_listener = refuse

    assert suppressor.start() is False
    assert not suppressor.active


def test_a_missing_pynput_reports_failure_rather_than_raising():
    suppressor = InputSuppressor()
    suppressor._keyboard_listener = lambda: (_ for _ in ()).throw(ImportError)

    assert suppressor.start() is False


def test_a_failed_start_leaves_no_half_registered_listeners():
    """Or stop() and with_keyboard_lifted() would work on a dead listener."""
    started = []

    class Dying:
        def __init__(self, ok):
            self.ok = ok

        def start(self):
            if not self.ok:
                raise OSError("refused")
            started.append(self)

    suppressor = InputSuppressor()
    suppressor._keyboard_listener = lambda: Dying(True)
    suppressor._mouse_listener = lambda: Dying(False)

    assert suppressor.start() is False
    assert suppressor._listeners == []


# -- the gestures still work, without a global hook ---------------------


def test_a_local_escape_hold_opens_the_skip_menu():
    """Same downstream path as the global hook, so no behaviour diverges."""
    overlay = Overlay()

    overlay._local_key_press(Event(char="\x1b", keysym="Escape"))
    assert overlay._local_hold is not None
    overlay.root.fire(overlay._local_hold)
    overlay._drain()

    assert overlay._menu_open


def test_a_local_digit_takes_the_skip():
    overlay = Overlay()
    overlay._local_key_press(Event(char="\x1b", keysym="Escape"))
    overlay.root.fire(overlay._local_hold)
    overlay._drain()

    overlay._local_key_release(Event(char="\x1b", keysym="Escape"))
    overlay._local_key_press(Event(char="1", keysym="1"))
    overlay._drain()

    assert overlay.taken == [5 * MINUTE]


def test_auto_repeat_does_not_push_the_hold_deadline_back():
    """606 repeats were measured across one 60s lock; the timer starts once."""
    overlay = Overlay()

    overlay._local_key_press(Event(char="\x1b", keysym="Escape"))
    first = overlay._local_hold
    for _ in range(50):
        overlay._local_key_press(Event(char="\x1b", keysym="Escape"))

    assert overlay._local_hold == first
    assert len(overlay.root.jobs) == 1


def test_releasing_escape_early_cancels_the_hold():
    overlay = Overlay()
    overlay._local_key_press(Event(char="\x1b", keysym="Escape"))
    job = overlay._local_hold

    overlay._local_key_release(Event(char="\x1b", keysym="Escape"))

    assert overlay._local_hold is None
    assert job in overlay.root.cancelled


def test_no_hold_is_timed_when_the_safety_release_is_off():
    overlay = Overlay(config=Config(safety_unlock=False))
    overlay._local_key_press(Event(char="\x1b", keysym="Escape"))

    assert overlay._local_hold is None
    assert overlay.root.jobs == {}


def test_unprintable_characters_are_not_passed_on_as_text():
    """Escape's char is \\x1b, which must not read as a menu keystroke."""
    char, name = LockOverlay._translate(Event(char="\x1b", keysym="Escape"))
    assert char is None
    assert name == "esc"


def test_an_ordinary_letter_survives_translation():
    assert LockOverlay._translate(Event(char="m", keysym="m")) == ("m", None)


def test_an_unmapped_key_translates_to_no_name():
    assert LockOverlay._translate(Event(char="", keysym="F5")) == (None, None)


# -- a reminder has to be leaveable ------------------------------------


def test_the_z_order_is_not_reclaimed_when_input_is_not_blocked():
    """Reasserting topmost once a second would make a leaveable window a trap."""
    overlay = Overlay()
    overlay._suppressing = False
    overlay._windows = [_ExplodingWindow()]

    overlay.tick(60.0)      # would raise if it touched the window


def test_the_z_order_is_reclaimed_while_input_is_blocked():
    overlay = Overlay()
    overlay._suppressing = True
    window = _RecordingWindow()
    overlay._windows = [window]
    overlay._countdowns = []
    overlay._hints = []

    overlay.tick(60.0)

    assert window.lifted, "a real lock must take the z-order back"


class _ExplodingWindow:
    def attributes(self, *_args):
        raise AssertionError("touched the window while only reminding")

    def lift(self):
        raise AssertionError("touched the window while only reminding")


class _RecordingWindow:
    def __init__(self) -> None:
        self.lifted = False

    def attributes(self, *_args):
        pass

    def lift(self):
        self.lifted = True
