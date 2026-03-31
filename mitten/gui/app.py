"""
QApplication bootstrap: stylesheet, single-instance guard, Unix signal bridge.
Opens the main window on startup; tray icon for minimize-to-tray.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .resources import make_stylesheet
from ..config import GUI_SOCKET

log = logging.getLogger(__name__)

# Windows single-instance guard uses a TCP server socket on this port.
# AF_UNIX leaves stale socket files on Windows when a process crashes,
# causing the "already running" check to wrongly fire on the next launch.
_GUI_LOCK_PORT = 47822


def _is_already_running() -> bool:
    """Check if another MITTEN GUI instance is running."""
    if sys.platform == "win32":
        # On Windows use a TCP probe — AF_UNIX leaves stale files after crashes
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", _GUI_LOCK_PORT))
            s.close()
            return result == 0
        except OSError:
            return False
    if not hasattr(socket, "AF_UNIX"):
        return False
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
    """Create a lock so only one GUI instance runs."""
    if sys.platform == "win32":
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", _GUI_LOCK_PORT))
            s.listen(1)
            return s
        except OSError:
            return None
    if not hasattr(socket, "AF_UNIX"):
        return None
    sock_path = str(GUI_SOCKET)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sock_path)
        s.listen(1)
        return s
    except OSError:
        return None


def _check_ffmpeg_windows() -> None:
    """On Windows, check ffmpeg is installed. If not, offer to install via winget."""
    if sys.platform != "win32":
        return
    import shutil
    if shutil.which("ffmpeg"):
        return

    import subprocess
    import threading
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit
    from PyQt6.QtCore import Qt

    from .resources import C

    has_winget = bool(shutil.which("winget"))

    dlg = QDialog()
    dlg.setWindowTitle("mitten — ffmpeg required")
    dlg.setMinimumWidth(460)
    dlg.setStyleSheet(
        f"QDialog {{ background-color: {C.BG}; color: {C.TEXT}; }}"
        f"QLabel {{ color: {C.TEXT}; background: transparent; }}"
        f"QPlainTextEdit {{ background: {C.SURFACE}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 4px; }}"
    )
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 20, 24, 20)
    lay.setSpacing(14)

    lbl = QLabel(
        "<b>ffmpeg is not installed.</b><br><br>"
        "mitten needs ffmpeg to record. "
        + (
            "click <b>Install automatically</b> to install it via winget, or install manually:"
            if has_winget else
            "install it manually with winget:"
        )
        + "<br><br><code>winget install Gyan.FFmpeg</code><br><br>"
        "after installing, <b>restart mitten</b>."
    )
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setStyleSheet(f"font-size: 13px; color: {C.TEXT};")
    lay.addWidget(lbl)

    output = QPlainTextEdit()
    output.setReadOnly(True)
    output.setFixedHeight(100)
    output.setVisible(False)
    lay.addWidget(output)

    _btn_base = "QPushButton { padding: 8px 20px; border-radius: 6px; font-size: 13px; border: none; }"

    row = QHBoxLayout()
    row.addStretch()

    btn_close = QPushButton("close")
    btn_close.setStyleSheet(
        _btn_base +
        f"QPushButton {{ background: {C.OVERLAY}; color: {C.TEXT}; }}"
        f"QPushButton:hover {{ background: {C.BORDER}; }}"
    )
    btn_close.clicked.connect(dlg.accept)
    row.addWidget(btn_close)

    if has_winget:
        btn_install = QPushButton("install automatically")
        btn_install.setStyleSheet(
            _btn_base +
            f"QPushButton {{ background: {C.GREEN}; color: {C.BG}; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #40a060; }}"
        )

        def _run_install():
            btn_install.setEnabled(False)
            btn_install.setText("installing…")
            output.setVisible(True)
            output.setPlainText("running: winget install Gyan.FFmpeg\n")

            def _worker():
                try:
                    proc = subprocess.Popen(
                        ["winget", "install", "Gyan.FFmpeg",
                         "--accept-source-agreements", "--accept-package-agreements"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True,
                    )
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        output.appendPlainText(line.rstrip())
                    proc.wait()
                    if proc.returncode == 0:
                        output.appendPlainText("\ninstalled. restart mitten to start recording.")
                        btn_install.setText("done — restart mitten")
                    else:
                        output.appendPlainText(f"\nwinget exited with code {proc.returncode}. try manually: winget install Gyan.FFmpeg")
                        btn_install.setText("install failed")
                        btn_install.setEnabled(True)
                except Exception as exc:
                    output.appendPlainText(f"\nerror: {exc}")
                    btn_install.setText("install failed")
                    btn_install.setEnabled(True)

            threading.Thread(target=_worker, daemon=True).start()

        btn_install.clicked.connect(_run_install)
        row.addWidget(btn_install)

    lay.addLayout(row)
    dlg.exec()


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
    log.info("Starting MITTEN GUI")
    _check_wayland()

    if _is_already_running():
        log.info("Another GUI instance detected — exiting")
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

    _check_ffmpeg_windows()

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
        if sys.platform != "win32":
            try:
                os.unlink(str(GUI_SOCKET))
            except OSError:
                pass

    sys.exit(exit_code)
