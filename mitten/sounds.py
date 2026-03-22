"""
Sound feedback for clip events.

Discovery order (first match wins):
  1. ~/.local/share/sounds/**
  2. XDG_DATA_DIRS/sounds/** (usually /usr/share/sounds/**)
  3. /usr/share/sounds/** (explicit fallback)

Tries event names in priority order per action, any extension (.oga/.ogg/.wav).
Player is auto-detected: paplay → pw-play → ffplay → winsound (Windows).
If no sound file is found, falls back to a short ffplay-generated sine tone (Linux)
or winsound.Beep (Windows). Errors are silently swallowed — sound is best-effort.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Bundled assets (shipped with the package — always available)
_ASSETS_DIR = Path(__file__).parent / "assets"
_BUNDLED: dict[str, Path] = {
    "trigger": _ASSETS_DIR / "snd_trigger.oga",
    "success": _ASSETS_DIR / "snd_saved.oga",
    "error":   _ASSETS_DIR / "snd_error.oga",
}

# ── Sound event names to search for, in priority order ────────────────────────
# Names follow freedesktop Sound Naming Spec where possible.

_EVENTS: dict[str, list[str]] = {
    "trigger": [
        "audio-volume-change",
        "camera-shutter",
        "button-pressed",
        "dialog-information",
        "message",
        "bell",
    ],
    "success": [
        "complete",
        "bell",
        "message-new-instant",
        "dialog-information",
        "message",
    ],
    "error": [
        "dialog-error",
        "dialog-warning",
        "bell",
        "message",
    ],
}

# Fallback sine tones via ffplay (frequency, duration_s)
_FALLBACK_TONES: dict[str, tuple[int, float]] = {
    "trigger": (880, 0.08),   # short high tick
    "success": (1046, 0.18),  # C6 ding
    "error":   (220, 0.25),   # low buzz
}

_EXTENSIONS = (".oga", ".ogg", ".wav", ".mp3")


# ── Discovery ─────────────────────────────────────────────────────────────────

def _sound_dirs() -> list[Path]:
    dirs: list[Path] = []

    # User sounds first
    user_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    dirs.append(user_data / "sounds")

    # XDG_DATA_DIRS (colon-separated on Linux)
    xdg_data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for d in xdg_data_dirs.split(":"):
        p = Path(d) / "sounds"
        if p not in dirs:
            dirs.append(p)

    # Explicit fallback always last
    dirs.append(Path("/usr/share/sounds"))

    return [d for d in dirs if d.exists()]


def _find_sound(event: str) -> Path | None:
    names = _EVENTS.get(event, [])
    dirs = _sound_dirs()

    for name in names:
        for d in dirs:
            # Search recursively — sounds may be nested under theme/stereo/ etc.
            for ext in _EXTENSIONS:
                matches = list(d.rglob(f"{name}{ext}"))
                if matches:
                    return matches[0]
    return None


# ── Player detection ──────────────────────────────────────────────────────────

def _get_player() -> str | None:
    """Return the name of the first available audio CLI player."""
    for player in ("paplay", "pw-play", "ffplay"):
        if shutil.which(player):
            return player
    return None


def _play_file(path: Path) -> None:
    player = _get_player()
    if player is None:
        return

    try:
        if player == "ffplay":
            subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            # paplay / pw-play accept the file directly
            subprocess.Popen(
                [player, str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        log.debug("sound play failed (%s): %s", player, e)


def _play_fallback(event: str) -> None:
    freq, dur = _FALLBACK_TONES.get(event, (660, 0.1))

    if sys.platform == "win32":
        try:
            import winsound
            winsound.Beep(freq, int(dur * 1000))
        except Exception as e:
            log.debug("winsound.Beep failed: %s", e)
        return

    if shutil.which("ffplay"):
        try:
            subprocess.Popen(
                [
                    "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                    "-f", "lavfi",
                    f"sine=frequency={freq}:duration={dur}",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.debug("ffplay tone failed: %s", e)


# ── Cache discovered paths so we only glob once per session ───────────────────

_cache: dict[str, Path | None] = {}


def _play(event: str) -> None:
    if event not in _cache:
        # 1. Bundled asset (always ships with mitten)
        bundled = _BUNDLED.get(event)
        if bundled and bundled.exists():
            _cache[event] = bundled
        else:
            # 2. System sound theme discovery
            _cache[event] = _find_sound(event)

    path = _cache[event]
    if path and path.exists():
        _play_file(path)
    else:
        _play_fallback(event)


# ── Public API ────────────────────────────────────────────────────────────────

def save_triggered() -> None:
    """Short sound when the user presses the save button."""
    _play("trigger")


def save_done() -> None:
    """Satisfying chime when the clip is confirmed saved."""
    _play("success")


def save_error() -> None:
    """Error tone when saving fails."""
    _play("error")


def session_start() -> None:
    """Triple trigger tick — plays the trigger sound 3 times to signal session recording start."""
    import threading as _t
    def _triple():
        for i in range(3):
            _play("trigger")
            if i < 2:
                time.sleep(0.18)
    _t.Thread(target=_triple, daemon=True).start()


def session_stop() -> None:
    """Double success chime — signals session recording has been saved."""
    import threading as _t
    def _double():
        _play("success")
        time.sleep(0.25)
        _play("success")
    _t.Thread(target=_double, daemon=True).start()
