"""
Desktop notification transport via notify-send (Linux) or winotify (Windows).
Single fire-and-forget notify() function — callers supply the message content.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

_APP_NAME = "Mitten"


def notify(
    summary: str,
    body: str = "",
    urgency: str = "normal",
    icon: str = "video-display",
    timeout_ms: int = 5000,
) -> None:
    """Send a desktop notification (non-blocking)."""
    if sys.platform == "win32":
        def _win_notify() -> None:
            try:
                from winotify import Notification, audio  # type: ignore
                toast = Notification(
                    app_id=_APP_NAME,
                    title=summary,
                    msg=body,
                    duration="short",
                )
                toast.show()
            except Exception as e:
                log.debug("winotify error: %s", e)
        threading.Thread(target=_win_notify, daemon=True).start()
        return

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


def notify_with_actions(
    summary: str,
    body: str = "",
    urgency: str = "normal",
    icon: str = "video-display",
    timeout_ms: int = 6000,
    actions: dict[str, tuple[str, Callable[[], None]]] | None = None,
) -> None:
    """
    Send a desktop notification with optional action buttons.

    actions: {action_id: (label, callback)} — callback is called when the user
    clicks that action. Runs notify-send --wait in a background thread.
    Falls back to plain notify() if actions is None or empty.
    """
    if not actions:
        notify(summary, body, urgency, icon, timeout_ms)
        return

    cmd = [
        "notify-send",
        "--wait",
        "-a", _APP_NAME,
        "-u", urgency,
        "-t", str(timeout_ms),
        "-i", icon,
    ]
    for action_id, (label, _) in actions.items():
        cmd.append(f"--action={action_id}:{label}")
    cmd.append(summary)
    if body:
        cmd.append(body)

    def _run() -> None:
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout_ms / 1000 + 60,
            )
            action_id = result.stdout.decode().strip()
            if action_id and action_id in actions:
                try:
                    actions[action_id][1]()
                except Exception as e:
                    log.debug("notification action callback error: %s", e)
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            log.debug("notify-send not found")
        except Exception as e:
            log.debug("notify_with_actions error: %s", e)

    threading.Thread(target=_run, daemon=True).start()
