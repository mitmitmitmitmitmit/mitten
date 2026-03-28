"""
QApplication bootstrap: stylesheet, single-instance guard, Unix signal bridge.
Opens the main window on startup; tray icon for minimize-to-tray.
"""
from __future__ import annotations

import os
import signal
import socket
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .resources import make_stylesheet
from ..config import GUI_SOCKET


def _is_already_running() -> bool:
    """Check if another MITTEN GUI instance is running via a socket lock."""
    if not hasattr(socket, "AF_UNIX"):
        return False  # AF_UNIX not available on this platform
    sock_path = str(GUI_SOCKET)
    if os.path.exists(sock_path):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            s.close()
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            try:
                os.unlink(sock_path)
            except OSError:
                pass
    return False


def _create_lock() -> socket.socket | None:
    """Create a socket lock so only one GUI instance runs."""
    if not hasattr(socket, "AF_UNIX"):
        return None  # AF_UNIX not available on this platform
    sock_path = str(GUI_SOCKET)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sock_path)
        s.listen(1)
        return s
    except OSError:
        return None


def _check_wayland() -> None:
    """Show a one-time warning if not running under Wayland."""
    if sys.platform == "win32":
        return

    if (
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    ):
        return

    # Only warn once per install
    flag = os.path.expanduser("~/.config/mitten/.x11_warned")
    if os.path.exists(flag):
        return

    app_tmp = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.warning(
        None,
        "~( ^.x.^)>  MITTEN — Wayland Warning",
        "MITTEN is designed for Wayland.\n\n"
        "WAYLAND_DISPLAY is not set — you may be running under X11.\n"
        "Recording may not work correctly.\n\n"
        "(This warning will not appear again.)",
    )
    try:
        from pathlib import Path
        Path(flag).parent.mkdir(parents=True, exist_ok=True)
        Path(flag).touch()
    except OSError:
        pass


def run_app(abuse_reveal: bool = False) -> None:
    """Launch the MITTEN GUI — main window + system tray."""
    _check_wayland()

    if _is_already_running():
        app = QApplication(sys.argv)
        app.setStyleSheet(make_stylesheet())
        QMessageBox.information(
            None,
            "~( ^.x.^)>  MITTEN",
            "MITTEN GUI is already running!\nCheck your system tray.",
        )
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("MITTEN")
    app.setApplicationDisplayName("mitten")
    app.setQuitOnLastWindowClosed(False)

    # Apply theme before any widgets are created
    try:
        from ..config import load_config
        from .themes import apply_theme
        apply_theme(load_config().general.theme)
    except Exception:
        pass

    app.setStyleSheet(make_stylesheet())

    # Single-instance lock
    lock = _create_lock()

    # Unix signal bridge: let Ctrl+C in terminal close the GUI
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(200)

    def _quit_handler(signum, frame):
        app.quit()

    signal.signal(signal.SIGINT, _quit_handler)
    signal.signal(signal.SIGTERM, _quit_handler)

    # Create main window — starts minimized; tray click or app launcher raises it
    from .main_window import MittenMainWindow
    window = MittenMainWindow()
    window.showMinimized()

    # Stage 5 reveal: show a banner message after the light-mode gauntlet trick
    if abuse_reveal:
        QTimer.singleShot(800, lambda: QMessageBox.information(
            window,
            "theme changed",
            "fuck you buddy. i really hate people who use light mode.", # can confirm - mit
        ))

    # Create tray icon (for minimize-to-tray + quick actions)
    has_tray = QSystemTrayIcon.isSystemTrayAvailable()
    tray = None
    if has_tray:
        from .tray import MittenTray
        tray = MittenTray(app, main_window=window)
        tray.show()

    exit_code = app.exec()

    # Clean up lock
    if lock:
        lock.close()
        try:
            os.unlink(str(GUI_SOCKET))
        except OSError:
            pass

    sys.exit(exit_code)
