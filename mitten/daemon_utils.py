"""
Shared daemon helpers used by the GUI, tray, and stats panel.
Centralises the PID-file read pattern, daemon toggle, and save-signal
that were previously duplicated across main_window.py, tray.py, and stats.py.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

from .config import PID_FILE

IPC_PORT = 47821


def _send_ipc_command(cmd: str) -> bool:
    """Send a JSON IPC command to the daemon over TCP (Windows only)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", IPC_PORT))
        s.sendall(json.dumps({"cmd": cmd}).encode())
        s.close()
        return True
    except OSError:
        return False


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
        if sys.platform == "win32":
            import psutil
            comm = psutil.Process(pid).name()
        else:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        if "python" not in comm and "mitten" not in comm:
            try:
                PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            return None
    except OSError:
        pass  # /proc unavailable; skip name check
    except Exception:
        pass  # psutil error; skip name check

    return pid


def toggle_daemon(pid: int | None = None) -> bool:
    """
    Start or stop the MITTEN recording daemon.
    If `pid` is given (daemon is running) → stop.
    If `pid` is None (daemon is not running) → start.
    Returns True on success, False on failure.
    """
    if sys.platform == "win32":
        if pid is not None:
            try:
                import psutil
                psutil.Process(pid).terminate()
                return True
            except Exception:
                return False
        else:
            try:
                subprocess.Popen(
                    [sys.executable, "run"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                return False

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
    if sys.platform == "win32":
        return _send_ipc_command("pause")
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
    if sys.platform == "win32":
        return _send_ipc_command("save")
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
    if sys.platform == "win32":
        return _send_ipc_command("reload")
    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except ProcessLookupError:
        return False
