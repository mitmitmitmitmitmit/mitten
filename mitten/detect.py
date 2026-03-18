"""
Game detection: polls /proc every N seconds to find running games.
State machine: IDLE <-> ACTIVE(game_name, pid).
Calls on_game_start / on_game_stop when state changes.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import MittenConfig

log = logging.getLogger(__name__)


@dataclass
class GameInfo:
    name: str
    pid: int
    detection_method: str  # "steam", "minecraft", "roblox", "wine", "custom", "window"


class GameDetector:
    """
    Background thread that polls /proc for known game processes.
    Notifies the orchestrator via callbacks when a game starts or stops.
    """

    def __init__(
        self,
        config: MittenConfig,
        on_game_start: Callable[[GameInfo], None],
        on_game_stop: Callable[[GameInfo], None],
    ) -> None:
        self._config = config
        self._on_start = on_game_start
        self._on_stop = on_game_stop
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._active: GameInfo | None = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="game-detector",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def current_game(self) -> GameInfo | None:
        return self._active

    # ------------------------------------------------------------------ #
    # Poll loop
    # ------------------------------------------------------------------ #

    def _poll_loop(self) -> None:
        interval = self._config.game_detection.poll_interval
        while not self._shutdown.is_set():
            try:
                self._tick()
            except Exception as e:
                log.debug("Game detector tick error: %s", e)
            self._shutdown.wait(interval)

    def _tick(self) -> None:
        if self._active is not None:
            # Check if the known game is still running
            if not Path(f"/proc/{self._active.pid}").exists():
                old = self._active
                self._active = None
                log.info("Game stopped: %s (pid %d)", old.name, old.pid)
                self._on_stop(old)
            return

        # IDLE: scan for games
        game = self._scan_for_game()
        if game:
            self._active = game
            log.info(
                "Game detected: %s (pid %d, method=%s)",
                game.name, game.pid, game.detection_method,
            )
            self._on_start(game)

    def _scan_for_game(self) -> GameInfo | None:
        """Scan /proc for a running game. Returns first match."""
        try:
            pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
        except PermissionError:
            return None

        custom_procs = set(self._config.game_detection.custom_processes)

        for pid in pids:
            game = self._check_pid(pid, custom_procs)
            if game:
                return game

        return None

    def _check_pid(self, pid: int, custom_procs: set[str]) -> GameInfo | None:
        """Check a single PID against all detection strategies."""
        proc_path = Path(f"/proc/{pid}")
        if not proc_path.exists():
            return None

        # 1. Steam (SteamAppId env var)
        game = _detect_steam(pid)
        if game:
            return game

        # 2. Minecraft (Java process with minecraft in cmdline)
        game = _detect_minecraft(pid)
        if game:
            return game

        # 3. Roblox / Sober
        game = _detect_roblox(pid)
        if game:
            return game

        # 4. Wine / Proton
        game = _detect_wine(pid)
        if game:
            return game

        # 5. Custom process list
        comm = _read_comm(pid)
        if comm and comm in custom_procs:
            return GameInfo(name=comm, pid=pid, detection_method="custom")

        return None

# ------------------------------------------------------------------ #
# Detection helpers — read /proc directly
# ------------------------------------------------------------------ #

def _read_file(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return b""


def _read_comm(pid: int) -> str | None:
    data = _read_file(f"/proc/{pid}/comm")
    return data.decode(errors="ignore").strip() or None


def _read_cmdline(pid: int) -> str:
    data = _read_file(f"/proc/{pid}/cmdline")
    return data.replace(b"\x00", b" ").decode(errors="ignore")


def _read_environ_dict(pid: int) -> dict[str, str]:
    data = _read_file(f"/proc/{pid}/environ")
    result = {}
    for part in data.split(b"\x00"):
        if b"=" in part:
            k, _, v = part.partition(b"=")
            result[k.decode(errors="ignore")] = v.decode(errors="ignore")
    return result


def _read_exe(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


_STEAM_LAUNCHER_COMMS = {
    "steam", "steamwebhelper", "steam.exe", "steamservice",
    "steam_osx", "steamerrorreporter",
}


def _detect_steam(pid: int) -> GameInfo | None:
    env = _read_environ_dict(pid)
    app_id = env.get("SteamAppId") or env.get("STEAMAPPID")
    if not app_id or app_id == "0":
        return None

    # Skip the Steam client itself — only match actual games
    comm = (_read_comm(pid) or "").lower()
    if comm in _STEAM_LAUNCHER_COMMS:
        return None
    exe = _read_exe(pid).lower()
    if any(s in exe for s in ("steam/ubuntu12_32/steam", "steam/steamwebhelper", "steam/steam")):
        return None

    game_name = _steam_game_name(app_id, env) or f"Steam App {app_id}"
    return GameInfo(name=game_name, pid=pid, detection_method="steam")


def _steam_game_name(app_id: str, env: dict) -> str | None:
    steam_root = Path.home() / ".local" / "share" / "Steam"
    manifest = steam_root / "steamapps" / f"appmanifest_{app_id}.acf"
    if not manifest.exists():
        return None
    try:
        content = manifest.read_text(errors="ignore")
        m = re.search(r'"name"\s+"([^"]+)"', content)
        return m.group(1) if m else None
    except Exception:
        return None


def _detect_minecraft(pid: int) -> GameInfo | None:
    cmdline = _read_cmdline(pid)
    exe = _read_exe(pid)

    if "java" not in exe.lower() and "java" not in cmdline.lower():
        return None

    minecraft_patterns = [
        "net.minecraft.client.main.Main",
        "net.minecraft.server.Main",
        "cpw.mods.bootstraplauncher",   # Modern Forge
        "org.quiltmc",                   # Quilt
        "net.fabricmc",                  # Fabric
        ".minecraft",
        "minecraft_server.jar",
    ]

    cmdline_lower = cmdline.lower()
    for pat in minecraft_patterns:
        if pat.lower() in cmdline_lower:
            return GameInfo(name="Minecraft", pid=pid, detection_method="minecraft")

    # Prism Launcher / ATLauncher child processes
    comm = _read_comm(pid) or ""
    if comm.lower() in ("prismlauncher", "java") and ".minecraft" in cmdline:
        return GameInfo(name="Minecraft", pid=pid, detection_method="minecraft")

    return None


def _detect_roblox(pid: int) -> GameInfo | None:
    comm = _read_comm(pid) or ""
    exe = _read_exe(pid)

    comm_lower = comm.lower()
    exe_lower = exe.lower()

    if "sober" in comm_lower or "sober" in exe_lower:
        return GameInfo(name="Roblox (Sober)", pid=pid, detection_method="roblox")

    if "robloxplayer" in comm_lower or "robloxplayer" in exe_lower:
        return GameInfo(name="Roblox", pid=pid, detection_method="roblox")

    if "roblox" in comm_lower or "roblox" in exe_lower:
        return GameInfo(name="Roblox", pid=pid, detection_method="roblox")

    return None


def _detect_wine(pid: int) -> GameInfo | None:
    exe = _read_exe(pid)
    if not exe:
        return None

    exe_lower = exe.lower()
    is_wine = "wine" in exe_lower or "proton" in exe_lower

    if not is_wine:
        return None

    env = _read_environ_dict(pid)
    # Confirm it's actually Wine by checking for WINEPREFIX or Steam compat
    if not any(k in env for k in ("WINEPREFIX", "STEAM_COMPAT_DATA_PATH", "WINE_PREFIX")):
        return None

    # Try to find the .exe being run
    cmdline = _read_cmdline(pid)
    exe_match = re.search(r'(\w[\w\s]*\.exe)', cmdline, re.IGNORECASE)
    game_exe = exe_match.group(1).strip() if exe_match else ""

    # Check for Roblox under Wine
    if "roblox" in game_exe.lower():
        return GameInfo(name="Roblox (Wine)", pid=pid, detection_method="wine")

    runtime = "Proton" if "proton" in exe_lower else "Wine"
    name = game_exe or f"{runtime} game"
    return GameInfo(name=name, pid=pid, detection_method="wine")
