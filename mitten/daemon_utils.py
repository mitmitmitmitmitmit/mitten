"""
Shared daemon helpers used by the GUI, tray, and stats panel.
Centralises the PID-file read pattern, daemon toggle, and save-signal
that were previously duplicated across main_window.py, tray.py, and stats.py.
"""
from __future__ import annotations

import os
import signal
import subprocess

from .config import PID_FILE


def get_daemon_pid() -> int | None:
    """
    Read the PID file and verify the process is still alive.
    Returns the PID as int, or None if the daemon is not running.
    """
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = existence check, no actual signal
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def toggle_daemon(pid: int | None = None) -> None:
    """
    Start or stop the MITTEN recording daemon.
    If `pid` is given (daemon is running) → stop via systemctl, fallback to SIGTERM.
    If `pid` is None (daemon is not running) → start via systemctl.
    """
    if pid is not None:
        try:
            subprocess.Popen(
                ["systemctl", "--user", "stop", "mitten.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    else:
        try:
            subprocess.Popen(
                ["systemctl", "--user", "start", "mitten.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass


def send_save_signal(pid: int) -> bool:
    """
    Send SIGUSR1 to the daemon to trigger a replay save.
    Returns True on success, False if the process no longer exists.
    """
    try:
        os.kill(pid, signal.SIGUSR1)
        return True
    except ProcessLookupError:
        return False
