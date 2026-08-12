"""Tests for refusing to start a tray that would take the process down.

pystray's Cocoa backend drives AppKit, which refuses to be used off the main
thread — at the Objective-C level, where it can kill the process rather than
raise something Python can catch. tkinter already owns the main thread here,
so on macOS there is nowhere safe to run it, and `_run`'s `except Exception`
is no protection.

Left unguarded, the failure lands at the worst possible moment: the first
launch that is not `--dry-run`, which is exactly when the break screen is
being tested for the first time on a machine nobody has run this on before.
"""

from __future__ import annotations

from pomodoro_guardian import platform as platform_module
from pomodoro_guardian import tray as tray_module
from pomodoro_guardian.tray import TrayIcon, TrayStatus, backend_unavailable


def test_the_tray_is_refused_where_its_backend_needs_the_main_thread(monkeypatch):
    monkeypatch.setattr(tray_module.sys, "platform", "darwin")
    assert backend_unavailable() != ""


def test_it_is_allowed_on_windows(monkeypatch):
    monkeypatch.setattr(tray_module.sys, "platform", "win32")
    assert backend_unavailable() == ""


def test_the_refusal_says_what_is_wrong_and_where_to_read(monkeypatch):
    """The whole point is turning an unexplained death into a sentence."""
    monkeypatch.setattr(tray_module.sys, "platform", "darwin")
    reason = backend_unavailable()

    assert "main thread" in reason
    assert "MAC-PORT" in reason


def test_starting_is_declined_before_pystray_is_even_imported(monkeypatch):
    """Importing it is harmless; starting the backend is what kills the
    process, so the check has to come first."""
    monkeypatch.setattr(tray_module.sys, "platform", "darwin")

    def explode(_name, *_args, **_kwargs):
        raise AssertionError("pystray must not be touched on this platform")

    monkeypatch.setattr(tray_module, "_left_click_icon", explode)
    tray = TrayIcon(TrayStatus())

    assert tray.start() is False
    assert tray.available is False
    assert "main thread" in tray.reason


def test_a_working_tray_reports_no_reason():
    """`reason` is the caller's explanation, so it must stay empty when
    there is nothing to explain."""
    assert TrayIcon(TrayStatus()).reason == ""


def test_the_doctor_and_the_app_agree_about_the_tray(monkeypatch):
    """--doctor promising a menu the app then refuses would be worse than
    either message alone, so both ask the same function."""
    monkeypatch.setattr(tray_module.sys, "platform", "darwin")

    capability = next(
        cap for cap in platform_module.CAPABILITIES if cap.key == "tray"
    )
    available, detail = capability.check()

    assert available is False
    assert detail == backend_unavailable()
