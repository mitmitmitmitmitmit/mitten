"""
Mitten error codes — used in notifications, dialogs, and crash logs.

Format: M-XXXX
  1xxx  Recorder
  2xxx  Save / encode
  3xxx  Config / lifecycle
  4xxx  Discord
  5xxx  Input / devices
  6xxx  Trigger / general
  7xxx  GUI
"""
from __future__ import annotations

# ── Recorder ──────────────────────────────────────────────────────────────────
E_RECORDER_CRASH_LIMIT  = "M-1001"  # Too many crashes in window — gave up
E_RECORDER_UNEXPECTED   = "M-1002"  # Recorder exited unexpectedly (single crash)
E_RECORDER_DEAD         = "M-1003"  # Recorder confirmed dead, not restarting
E_RECORDER_AUDIO        = "M-1004"  # Audio device not found / gsr exit 50
E_RECORDER_MONITOR      = "M-1005"  # Monitor/display not found
E_RECORDER_START        = "M-1006"  # Recorder failed to start (launch or post-reload)
E_SESSION_EMPTY         = "M-1007"  # Session recording output missing or empty after stop
E_SESSION_POST          = "M-1008"  # Session post-processing (save worker) failed

# ── Save / encode ──────────────────────────────────────────────────────────────
E_SAVE_SEMAPHORE        = "M-2001"  # Save job timed out waiting for semaphore
E_SAVE_MISSING          = "M-2002"  # Raw clip missing or empty before encode
E_SAVE_FFMPEG_WM        = "M-2003"  # ffmpeg watermark pass failed (single codec)
E_SAVE_FFMPEG_ENCODE    = "M-2004"  # ffmpeg targeted encode failed (no watermark path)
E_SAVE_TIMEOUT          = "M-2005"  # No clip appeared within watchdog window (30s)
E_SAVE_MOVE             = "M-2006"  # Failed to move raw clip to save dir (OSError)
E_SAVE_FFMPEG_DUAL_HEVC = "M-2007"  # ffmpeg HEVC pass 1/2 of dual-encode failed
E_SAVE_FFMPEG_DUAL_H264 = "M-2008"  # ffmpeg H.264 pass 2/2 or dual-encode timeout
E_SAVE_FFMPEG_TIMEOUT   = "M-2009"  # ffmpeg single-codec watermark encode timed out
E_SAVE_OS_ERROR         = "M-2010"  # OSError raised during watermarking subprocess
E_SAVE_OVER_TARGET      = "M-2011"  # Clip saved but still exceeds compression target MB
E_TRIM_INVALID          = "M-2012"  # Trim end ≤ trim start
E_TRIM_FAILED           = "M-2013"  # ffmpeg trim operation returned an error

# ── Config / lifecycle ─────────────────────────────────────────────────────────
E_CONFIG_LOAD           = "M-3001"  # Config file could not be loaded / parsed
E_CONFIG_SAVE           = "M-3002"  # Config file could not be written
E_CONFIG_RELOAD         = "M-3003"  # SIGHUP config reload failed (daemon kept old config)
E_DAEMON_ALREADY        = "M-3004"  # Daemon already running — PID file locked
E_DAEMON_NO_RECORDER    = "M-3005"  # Trigger fired but recorder is not active

# ── Discord ────────────────────────────────────────────────────────────────────
E_DISCORD_CONNECT       = "M-4001"  # Could not connect to Discord IPC socket
E_DISCORD_SEND          = "M-4002"  # Failed to send presence update
E_DISCORD_AUTH          = "M-4003"  # Discord IPC handshake / auth failed

# ── Input / devices ────────────────────────────────────────────────────────────
E_INPUT_NO_DEVICES      = "M-5001"  # No accessible input devices found
E_INPUT_EVDEV_MISSING   = "M-5002"  # python-evdev not installed
E_INPUT_PERMISSION      = "M-5003"  # Permission denied on input device (not in 'input' group)
E_INPUT_SELECT          = "M-5004"  # select() failed on input device fds
E_INPUT_ALL_CLOSED      = "M-5005"  # All input device fds closed — trigger disabled
E_INPUT_DETECT_TIMEOUT  = "M-5006"  # Button detect: no press detected within 30s
E_INPUT_LIST_FAILED     = "M-5007"  # Could not enumerate input devices (list_devices failed)
E_INPUT_WAYLAND_MISSING = "M-5008"  # WAYLAND_DISPLAY not set — recording may not work

# ── Trigger / general ──────────────────────────────────────────────────────────
E_TRIGGER               = "M-6001"  # Hotkey / button trigger error
E_TRIGGER_NO_GAME       = "M-6002"  # Trigger fired in game mode with no game running
E_TRIGGER_NO_INPUT      = "M-6003"  # No input devices — trigger via SIGUSR1 or tray only

# ── GUI ────────────────────────────────────────────────────────────────────────
E_GUI_CRASH             = "M-7001"  # Unhandled exception in GUI process
E_GUI_ALREADY_RUNNING   = "M-7002"  # Another GUI instance already running
E_GUI_SAVE_SETTINGS     = "M-7003"  # Unhandled exception in settings _do_save()


def fmt(code: str, msg: str) -> str:
    """Return '[M-XXXX] message' for use in notifications and log lines."""
    return f"[{code}] {msg}"
