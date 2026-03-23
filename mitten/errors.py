"""
Mitten error codes — used in notifications, dialogs, and crash logs.

Format: M-XXXX
  1xxx  Recorder
  2xxx  Save / encode
  3xxx  Config
  4xxx  Discord
  5xxx  Input / devices
  6xxx  Trigger / general
"""
from __future__ import annotations

# ── Recorder ──────────────────────────────────────────────────────────────────
E_RECORDER_CRASH_LIMIT  = "M-1001"  # Too many crashes in window — gave up
E_RECORDER_UNEXPECTED   = "M-1002"  # Recorder exited unexpectedly (single crash)
E_RECORDER_DEAD         = "M-1003"  # Recorder confirmed dead, not restarting
E_RECORDER_AUDIO        = "M-1004"  # Audio device not found / gsr exit 50
E_RECORDER_MONITOR      = "M-1005"  # Monitor/display not found

# ── Save / encode ──────────────────────────────────────────────────────────────
E_SAVE_SEMAPHORE        = "M-2001"  # Save job timed out waiting for semaphore
E_SAVE_MISSING          = "M-2002"  # Raw clip missing or empty before encode
E_SAVE_FFMPEG_WM        = "M-2003"  # ffmpeg watermark pass failed
E_SAVE_FFMPEG_ENCODE    = "M-2004"  # ffmpeg targeted encode failed
E_SAVE_TIMEOUT          = "M-2005"  # No clip appeared within watchdog window
E_SAVE_MOVE             = "M-2006"  # Failed to move raw clip to save dir
E_SAVE_FFMPEG_DUAL_HEVC = "M-2007"  # ffmpeg HEVC dual-encode pass failed
E_SAVE_FFMPEG_DUAL_H264 = "M-2008"  # ffmpeg H.264 transcode pass failed

# ── Config ─────────────────────────────────────────────────────────────────────
E_CONFIG_LOAD           = "M-3001"  # Config file could not be loaded / parsed
E_CONFIG_SAVE           = "M-3002"  # Config file could not be written

# ── Discord ────────────────────────────────────────────────────────────────────
E_DISCORD_CONNECT       = "M-4001"  # Could not connect to Discord IPC socket
E_DISCORD_SEND          = "M-4002"  # Failed to send presence update
E_DISCORD_AUTH          = "M-4003"  # Discord IPC handshake / auth failed

# ── Input / devices ────────────────────────────────────────────────────────────
E_INPUT_NO_DEVICES      = "M-5001"  # No accessible input devices found
E_INPUT_EVDEV_MISSING   = "M-5002"  # python-evdev not installed
E_INPUT_PERMISSION      = "M-5003"  # Permission denied on input device (not in 'input' group)
E_INPUT_SELECT          = "M-5004"  # select() failed on input device fds

# ── Trigger / general ──────────────────────────────────────────────────────────
E_TRIGGER               = "M-6001"  # Hotkey / button trigger error
E_GUI_CRASH             = "M-6002"  # Unhandled exception in GUI process
E_DAEMON_CRASH          = "M-6003"  # Unhandled exception in daemon process


def fmt(code: str, msg: str) -> str:
    """Return '[M-XXXX] message' for use in notifications and log lines."""
    return f"[{code}] {msg}"
