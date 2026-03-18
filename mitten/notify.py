"""
Desktop notification transport via notify-send.
Single fire-and-forget notify() function — callers supply the message content.
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

_APP_NAME = "Mitten"


def notify(
    summary: str,
    body: str = "",
    urgency: str = "normal",
    icon: str = "video-display",
    timeout_ms: int = 5000,
) -> None:
    """
    Send a desktop notification via notify-send (non-blocking).

    Args:
        summary:    Notification title.
        body:       Optional detail text.
        urgency:    "low", "normal", or "critical".
        icon:       Icon name (freedesktop icon spec) or file path.
        timeout_ms: Display duration in milliseconds.
    """
    cmd = [
        "notify-send",
        "-a", _APP_NAME,
        "-u", urgency,
        "-t", str(timeout_ms),
        "-i", icon,
        summary,
    ]
    if body:
        cmd.append(body)
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.debug("notify-send not found, skipping notification: %s — %s", summary, body)
    except Exception as e:
        log.debug("notify-send error: %s", e)
