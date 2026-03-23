"""
Auto-update system for MITTEN.

On every `mitten run` startup, checks if a new commit exists on origin/main.
If yes, spawns a konsole window showing a branded update UI with a countdown,
then auto-updates. A backup tarball is saved before updating so a faulty update
can be automatically rolled back.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

_BANNER = r"""
/\     /\
( ^.x.^ )   MITTEN AUTO-UPDATER
(       )
 \_____/
"""

_FATAL_BANNER = r"""
/\     /\
( x.x.x )   FATAL ERROR
(       )
 \_____/
"""

_BACKUP_DIR = Path.home() / ".local" / "share" / "mitten" / "backup"


# ------------------------------------------------------------------ #
# Repository detection
# ------------------------------------------------------------------ #

def get_repo_dir() -> Path | None:
    """Walk up from mitten/__file__ looking for .git/. Return repo root or None."""
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    return None


# GitHub remote URL — used directly so no git remote needs to be configured
GITHUB_URL = "https://github.com/mitmitmitmitmitmit/mitten.git"


# ------------------------------------------------------------------ #
# Update check
# ------------------------------------------------------------------ #

def check_for_update() -> tuple[str, str] | None:
    """
    Fetches from the GitHub URL directly (no remote config needed),
    then compares HEAD vs FETCH_HEAD.
    Returns (local_short_hash, remote_short_hash) or None (no update / any error).
    All failures are silently swallowed — a missing update check must never
    prevent the daemon from starting.
    """
    repo_dir = get_repo_dir()
    if repo_dir is None:
        return None

    try:
        subprocess.run(
            ["git", "fetch", GITHUB_URL, "main"],
            cwd=repo_dir,
            capture_output=True,
            timeout=15,
            check=True,
        )

        local = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, text=True, timeout=5,
        ).strip()

        remote = subprocess.check_output(
            ["git", "rev-parse", "FETCH_HEAD"],
            cwd=repo_dir, text=True, timeout=5,
        ).strip()

        if local == remote:
            return None

        # Try to read version string from remote pyproject.toml
        remote_ver = ""
        try:
            toml_text = subprocess.check_output(
                ["git", "show", "FETCH_HEAD:pyproject.toml"],
                cwd=repo_dir, text=True, timeout=5,
            )
            for line in toml_text.splitlines():
                if line.strip().startswith("version"):
                    remote_ver = line.split("=")[1].strip().strip('"')
                    break
        except Exception:
            pass

        return local[:7], remote[:7], remote_ver

    except Exception:
        return None


# ------------------------------------------------------------------ #
# Backup / restore
# ------------------------------------------------------------------ #

def create_backup(repo_dir: Path) -> Path:
    """
    Create ~/.local/share/mitten/backup/backup.tar.gz + backup_meta.json.
    Keeps only the latest backup (deletes previous one first).
    Returns the backup directory path.
    """
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Remove old backup
    (_BACKUP_DIR / "backup.tar.gz").unlink(missing_ok=True)
    (_BACKUP_DIR / "backup_meta.json").unlink(missing_ok=True)

    # Read current commit hash
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, text=True, timeout=5,
        ).strip()
    except Exception:
        commit = "unknown"

    # Read installed version
    try:
        version = subprocess.check_output(
            [sys.executable, "-c",
             "import importlib.metadata; print(importlib.metadata.version('mitten'))"],
            text=True, timeout=5,
        ).strip()
    except Exception:
        version = "unknown"

    # Create tarball (exclude .git, __pycache__, *.egg-info)
    tar_path = _BACKUP_DIR / "backup.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            parts = Path(info.name).parts
            for part in parts:
                if part in (".git", "__pycache__") or part.endswith(".egg-info"):
                    return None
            return info
        tf.add(repo_dir, arcname=".", filter=_filter)

    # Write metadata
    meta = {
        "commit": commit,
        "date": datetime.now().isoformat(),
        "version": version,
    }
    (_BACKUP_DIR / "backup_meta.json").write_text(json.dumps(meta, indent=2))

    return _BACKUP_DIR


def restore_backup(repo_dir: Path) -> bool:
    """Extract backup.tar.gz over repo_dir. Returns True on success."""
    tar_path = _BACKUP_DIR / "backup.tar.gz"
    if not tar_path.exists():
        return False
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(repo_dir)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ #
# Terminal spawning
# ------------------------------------------------------------------ #

def _display_env() -> dict:
    """
    Return a copy of the environment with WAYLAND_DISPLAY and
    DBUS_SESSION_BUS_ADDRESS filled in if they are missing.
    Needed when spawning GUI processes from a systemd service.
    """
    env = os.environ.copy()
    uid = os.getuid()

    if not env.get("WAYLAND_DISPLAY"):
        for display in ("wayland-0", "wayland-1"):
            if Path(f"/run/user/{uid}/{display}").exists():
                env["WAYLAND_DISPLAY"] = display
                break

    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = Path(f"/run/user/{uid}/bus")
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"

    return env


def spawn_update_terminal(old_hash: str, new_hash: str) -> None:
    """
    Launch: konsole --noclose -e mitten _update --from OLD --to NEW
    Detached process (start_new_session=True).
    Falls back to running the update UI inline if konsole is not found.
    """
    if shutil.which("konsole"):
        try:
            subprocess.Popen(
                [
                    "konsole", "--noclose", "-e",
                    "mitten", "_update", "--from", old_hash, "--to", new_hash,
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_display_env(),
            )
            return
        except Exception:
            pass

    # Fallback: run inline (no separate window)
    run_update_ui(old_hash, new_hash)


# ------------------------------------------------------------------ #
# Update UI (runs inside konsole or inline)
# ------------------------------------------------------------------ #

def run_update_ui(old_hash: str, new_hash: str) -> None:
    """
    Full terminal update procedure:
      1. Print banner + 3-second countdown
      2. Create backup tarball
      3. Stop mitten.service
      4. git pull --ff-only origin main
      5. pip install -e .
      6. Restart mitten.service
    On failure at any step: attempt rollback, then fatal error if that also fails.
    """
    repo_dir = get_repo_dir()

    print(_BANNER)
    print(f"  Update detected!")
    print(f"  {old_hash} \u2192 {new_hash}")
    print()
    print("  Update will begin in 3 seconds.")
    print("  not updating is not an option.")
    print()

    for i in range(3, 0, -1):
        print(f"  {i}...", end="", flush=True)
        time.sleep(1)
    print()
    print()

    if repo_dir is None:
        _fatal_error(None, "Could not locate MITTEN repository directory.")
        return

    # ── Step 1: Backup ──────────────────────────────────────────────
    print("  [1] Creating backup...")
    try:
        backup_dir = create_backup(repo_dir)
        print(f"      \u2713 Backup saved to {backup_dir}/")
    except Exception as e:
        print(f"      \u2717 Backup failed: {e}")
        print("      (continuing without backup \u2014 rollback unavailable if update fails)")
    print()

    # ── Step 2: Stop service + kill GUI/tray ───────────────────────
    print("  [2] Stopping mitten service...")
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", "mitten.service"],
            timeout=15, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("      \u2713 Service stopped")
    except Exception as e:
        print(f"      \u2717 Could not stop service: {e}")
        print("      (continuing anyway)")

    # Kill any lingering GUI / tray processes so the new version launches fresh
    import signal as _signal
    current_pid = os.getpid()
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.pid == current_pid:
                    continue
                cmdline = " ".join(proc.cmdline())
                if "mitten" in cmdline and "_update" not in cmdline:
                    proc.send_signal(_signal.SIGTERM)
            except Exception:
                pass
        print("      \u2713 GUI / tray processes terminated")
    except ImportError:
        subprocess.run(
            ["pkill", "-TERM", "-f", "mitten"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("      \u2713 pkill -TERM mitten (psutil unavailable)")
    print()

    # ── Step 3: fetch + reset ────────────────────────────────────────
    print("  [3] Pulling latest changes...")
    try:
        subprocess.run(
            ["git", "fetch", GITHUB_URL, "main"],
            cwd=repo_dir, timeout=60, check=True,
        )
        subprocess.run(
            ["git", "reset", "--hard", "FETCH_HEAD"],
            cwd=repo_dir, timeout=30, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("      \u2713 fetched from GitHub + reset to FETCH_HEAD")
    except Exception as e:
        print(f"      \u2717 update failed: {e}")
        _handle_update_failure(repo_dir, f"git fetch/reset failed: {e}")
        return
    print()

    # ── Step 4: pip install ─────────────────────────────────────────
    print("  [4] Installing updated package...")
    print("      (--break-system-packages is safe here: mitten's deps don't")
    print("       conflict with any Arch system packages)")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".",
             "--break-system-packages", "-q"],
            cwd=repo_dir, timeout=120, check=True,
        )
        print("      \u2713 pip install -e . --break-system-packages")
    except Exception as e:
        print(f"      \u2717 pip install failed: {e}")
        print("      (code updated from git — service restart may still work)")
    print()

    # ── Step 5: Restart service ─────────────────────────────────────
    print("  Update complete! Restarting mitten...")
    print()
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "mitten.service"],
            timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            print("  \u2713 mitten.service started")
        else:
            print(f"  \u2717 Service start returned code {result.returncode}")
            print("  Run manually: systemctl --user start mitten.service")
    except Exception as e:
        print(f"  \u2717 Could not start service: {e}")
        print("  Run manually: systemctl --user start mitten.service")

    # Re-launch GUI detached so the tray icon comes back without a reboot
    try:
        subprocess.Popen(
            ["mitten"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_display_env(),
            start_new_session=True,
        )
        print("  \u2713 GUI relaunched")
    except Exception as e:
        print(f"  \u2717 Could not relaunch GUI: {e}")
        print("  Run manually: mitten")

    print()
    print("  Done. You can close this window.")


# ------------------------------------------------------------------ #
# Failure / rollback / fatal error
# ------------------------------------------------------------------ #

def _handle_update_failure(repo_dir: Path, error_msg: str) -> None:
    """Called when git pull fails. Attempts rollback via git reset or tarball."""
    print()
    print(f"  \u2717 Update failed: {error_msg}")
    print()
    print("  Restoring from backup...")

    rolled_back = False

    # Try git reset to the backed-up commit hash first
    try:
        meta_path = _BACKUP_DIR / "backup_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            old_commit = meta.get("commit", "")
            if old_commit and old_commit != "unknown":
                subprocess.run(
                    ["git", "reset", "--hard", old_commit],
                    cwd=repo_dir, timeout=30, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                print(f"  \u2713 Rolled back to {old_commit[:7]} via git")
                rolled_back = True
    except Exception:
        pass

    # Fallback: extract tarball
    if not rolled_back:
        if restore_backup(repo_dir):
            print("  \u2713 Rolled back from backup tarball")
            rolled_back = True
        else:
            print("  \u2717 Rollback also failed")

    if rolled_back:
        print()
        print("  Restarting mitten with previous version...")
        try:
            subprocess.run(
                ["systemctl", "--user", "start", "mitten.service"],
                timeout=15,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print("  \u2713 mitten.service started")
        except Exception as e:
            print(f"  \u2717 Could not start service: {e}")
        print()
        print("  Rollback complete. This window will close in 10 seconds.")
        time.sleep(10)
    else:
        _fatal_error(repo_dir, error_msg)


def _fatal_error(repo_dir: Path | None, error_msg: str) -> None:
    """
    Show the fatal error banner with manual reinstall commands.
    Attempt pip uninstall mitten. Show result.
    Loops forever (user must close the window).
    """
    repo_str = str(repo_dir) if repo_dir else "~/Documents/Mitten"

    print()
    print(_FATAL_BANNER)
    print("  Auto-update has had a fatal error.")
    print("  Automatic rollback also failed.")
    print()
    print("  To reinstall MITTEN manually:")
    print()
    print("    pip uninstall mitten")
    print(f"    cd {repo_str}")
    print("    git fetch origin main")
    print("    git reset --hard origin/main")
    print("    pip install -e .")
    print("    systemctl --user restart mitten.service")
    print()
    print("  (attempting automatic uninstall...)")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "mitten", "-y"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print("  \u2713 pip uninstall mitten successful")
        else:
            print("  \u2717 pip uninstall mitten failed")
            print()
            print("  Run manually:  pip uninstall mitten -y")
    except Exception as e:
        print(f"  \u2717 Could not attempt uninstall: {e}")
        print()
        print("  Run manually:  pip uninstall mitten -y")

    print()
    print("  This window will stay open. Press Ctrl+C or close it when done.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
