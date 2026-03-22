"""
Shared daemon helpers used by the GUI, tray, and stats panel.
Centralises the PID-file read pattern, daemon toggle, and save-signal
that were previously duplicated across main_window.py, tray.py, and stats.py.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .config import PID_FILE


def get_daemon_pid() -> int | None:
    """
    Read the PID file and verify the process is still alive.
    Returns the PID as int, or None if the daemon is not running.
    Deletes a stale PID file if the recorded PID is no longer a valid mitten process.
    """
    try:
        pid = int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None

    try:
        os.kill(pid, 0)  # signal 0 = existence check, no actual signal
    except (ProcessLookupError, PermissionError, OSError):
        # Process is gone — remove the stale PID file
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    # Guard against recycled PIDs — verify it's actually a mitten/python process
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
        if "python" not in comm and "mitten" not in comm:
            try:
                PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            return None
    except OSError:
        pass  # /proc unavailable (non-Linux?); skip name check

    return pid


def toggle_daemon(pid: int | None = None) -> bool:
    """
    Start or stop the MITTEN recording daemon.
    If `pid` is given (daemon is running) → stop via systemctl, fallback to SIGTERM.
    If `pid` is None (daemon is not running) → start via systemctl.
    Returns True on success, False on failure.
    """
    if pid is not None:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "stop", "mitten.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except ProcessLookupError:
                return False
        except subprocess.TimeoutExpired:
            return False
    else:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "start", "mitten.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
        except subprocess.TimeoutExpired:
            return False


def toggle_pause(pid: int) -> bool:
    """
    Send SIGUSR2 to the daemon to pause/resume recording.
    Returns True on success, False if the process no longer exists.
    """
    try:
        os.kill(pid, signal.SIGUSR2)
        return True
    except ProcessLookupError:
        return False


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


def send_reload_signal(pid: int) -> bool:
    """
    Send SIGHUP to the daemon to reload config from disk and apply changes.
    Returns True on success, False if the process no longer exists.
    """
    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except ProcessLookupError:
        return False
