"""
Game detection: polls /proc every N seconds to find running games.
State machine: IDLE <-> ACTIVE(game_name, pid).
Calls on_game_start / on_game_stop when state changes.
"""
from __future__ import annotations

import logging
import os
import re
import sys
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
            alive = self._pid_alive(self._active.pid)
            if not alive:
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

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if sys.platform == "win32":
            try:
                import psutil
                return psutil.pid_exists(pid)
            except Exception:
                return False
        return Path(f"/proc/{pid}").exists()

    def _scan_for_game(self) -> GameInfo | None:
        """Scan running processes for a known game. Returns first match."""
        if sys.platform == "win32":
            try:
                import psutil
                pids = psutil.pids()
            except Exception:
                return None
        else:
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
        if sys.platform != "win32":
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
# Detection helpers — read /proc directly (Linux) or psutil (Windows)
# ------------------------------------------------------------------ #

def _read_file(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return b""


def _read_comm(pid: int) -> str | None:
    if sys.platform == "win32":
        try:
            import psutil
            return psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        except Exception:
            return None
    data = _read_file(f"/proc/{pid}/comm")
    return data.decode(errors="ignore").strip() or None


def _read_cmdline(pid: int) -> str:
    if sys.platform == "win32":
        try:
            import psutil
            parts = psutil.Process(pid).cmdline()
            return " ".join(parts)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return ""
        except Exception:
            return ""
    data = _read_file(f"/proc/{pid}/cmdline")
    return data.replace(b"\x00", b" ").decode(errors="ignore")


def _read_environ_dict(pid: int) -> dict[str, str]:
    if sys.platform == "win32":
        try:
            import psutil
            return psutil.Process(pid).environ()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return {}
        except Exception:
            return {}
    data = _read_file(f"/proc/{pid}/environ")
    result = {}
    for part in data.split(b"\x00"):
        if b"=" in part:
            k, _, v = part.partition(b"=")
            result[k.decode(errors="ignore")] = v.decode(errors="ignore")
    return result


def _read_exe(pid: int) -> str:
    if sys.platform == "win32":
        try:
            import psutil
            return psutil.Process(pid).exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return ""
        except Exception:
            return ""
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


def _steam_library_paths() -> list[Path]:
    """Return all Steam library folders, including secondary ones from libraryfolders.vdf."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            winreg.CloseKey(key)
            return [Path(steam_path)]
        except Exception:
            return []

    steam_root = Path.home() / ".local" / "share" / "Steam"
    paths = [steam_root / "steamapps"]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        content = vdf.read_text(errors="ignore")
        for m in re.finditer(r'"path"\s+"([^"]+)"', content):
            extra = Path(m.group(1)) / "steamapps"
            if extra not in paths:
                paths.append(extra)
    except OSError:
        pass
    return paths


def _steam_game_name(app_id: str, env: dict) -> str | None:
    for lib in _steam_library_paths():
        manifest = lib / f"appmanifest_{app_id}.acf"
        if not manifest.exists():
            continue
        try:
            content = manifest.read_text(errors="ignore")
            m = re.search(r'"name"\s+"([^"]+)"', content)
            if m:
                return m.group(1)
        except OSError:
            continue
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
    # Wine/Proton detection is meaningless on Windows itself
    if sys.platform == "win32":
        return None

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
