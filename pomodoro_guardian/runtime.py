"""Things that only matter once this is a packaged `.exe` (SPEC §11.8).

Running from a terminal hides three problems that a double-clicked
executable exposes immediately: nothing stops you starting it twice,
there is no console for the log to go to, and there is no obvious way to
have it start with Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Pomodoro Guardian"
MUTEX_NAME = "Global\\PomodoroGuardian_SingleInstance"
SHORTCUT_NAME = "Pomodoro Guardian.lnk"


def frozen() -> bool:
    """True when running from a PyInstaller build rather than source."""
    return getattr(sys, "frozen", False)


# -- single instance --------------------------------------------------


class SingleInstance:
    """A named mutex, held for the life of the process.

    Two copies would each install a global input hook and each try to own
    the lock overlay — the second one would fight the first for input
    suppression, which is the worst possible thing to get wrong here.
    """

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle = None

    def acquire(self) -> bool:
        """True if we are the only instance. False means one is running."""
        try:
            import win32api
            import win32event
            import winerror
        except ImportError:
            return True   # can't check; don't block startup over it

        self._handle = win32event.CreateMutex(None, True, self._name)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            self._handle = None
            return False
        return True

    def release(self) -> None:
        if self._handle is not None:
            try:
                import win32api

                win32api.CloseHandle(self._handle)
            except Exception:  # pragma: no cover - teardown must not raise
                pass
            self._handle = None


# -- logging ----------------------------------------------------------


def log_path(settings_dir: Path | None = None) -> Path:
    from .settings import default_path

    base = settings_dir or default_path().parent
    return base / "pomodoro.log"


def redirect_output(path: Path | None = None, max_bytes: int = 512_000) -> Path:
    """Send stdout and stderr to a file, for a build with no console.

    Truncates rather than rotating: this is a diary of one machine's
    breaks, not an audit trail, and an unbounded log on a personal app is
    a worse problem than losing last week's lines.
    """
    target = path or log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > max_bytes:
        target.unlink()

    stream = open(target, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    return target


# -- start with Windows -----------------------------------------------


def startup_dir() -> Path:
    return (
        Path(os.environ.get("APPDATA", Path.home()))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )


def startup_shortcut() -> Path:
    return startup_dir() / SHORTCUT_NAME


def starts_with_windows() -> bool:
    return startup_shortcut().is_file()


def set_start_with_windows(enabled: bool) -> bool:
    """Add or remove the Startup shortcut. Returns the resulting state.

    A shortcut in the Startup folder rather than a Run registry key: it is
    visible in Task Manager's Startup tab, and removable by hand without
    touching the registry — which matters for something that takes over
    your screen.
    """
    shortcut = startup_shortcut()
    if not enabled:
        try:
            shortcut.unlink(missing_ok=True)
        except OSError:
            pass
        return starts_with_windows()

    target, arguments, workdir = _launch_command()
    try:
        import pythoncom
        from win32com.client import Dispatch

        pythoncom.CoInitialize()
        link = Dispatch("WScript.Shell").CreateShortCut(str(shortcut))
        link.Targetpath = target
        link.Arguments = arguments
        link.WorkingDirectory = workdir
        link.Description = APP_NAME
        link.save()
    except Exception:
        return False
    return starts_with_windows()


def _launch_command() -> tuple[str, str, str]:
    """How to start this app again — as a build, or from source.

    Returns (target, arguments, working directory). The working directory
    matters when running from source: `-m pomodoro_guardian` only resolves
    from the repository root, so pointing it at Python's own folder — as
    an earlier version did — produced a shortcut that silently did
    nothing at login.
    """
    if frozen():
        return sys.executable, "", str(Path(sys.executable).parent)

    # pythonw rather than python: a console window flashing up at login
    # would be worse than useless for a tray app. Taken from the venv's
    # own Scripts directory, so the dependencies are the installed ones.
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    python = str(windowless if windowless.exists() else exe)
    repo_root = Path(__file__).resolve().parent.parent
    return python, "-m pomodoro_guardian", str(repo_root)
