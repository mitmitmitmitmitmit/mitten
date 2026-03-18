"""
System integration utilities — dependency checks, .desktop install,
systemd service, autostart.  No GUI widgets; importable by both the
setup wizard (terminal) and the GUI (dashboard no_deps check).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


# ------------------------------------------------------------------ #
# Arch / package-manager detection
# ------------------------------------------------------------------ #

def is_arch_based() -> bool:
    """True if pacman is available on PATH."""
    return shutil.which("pacman") is not None


def _aur_helper() -> str | None:
    """Return the first available AUR helper (yay > paru), or None."""
    for helper in ("yay", "paru"):
        if shutil.which(helper):
            return helper
    return None


# ------------------------------------------------------------------ #
# Dependency checks
# ------------------------------------------------------------------ #

#: Binaries that must be present for MITTEN to run.
_RUNTIME_DEPS: dict[str, str] = {
    "gpu-screen-recorder": "gpu-screen-recorder",  # name → AUR/pacman pkg
    "ffmpeg": "ffmpeg",
    "ffplay": "ffmpeg",         # same package as ffmpeg
    "notify-send": "libnotify",
}


def check_dependencies() -> dict[str, bool]:
    """
    Return a dict mapping binary name → True if found on PATH.
    Read-only — does not attempt to install anything.
    """
    return {name: (shutil.which(name) is not None) for name in _RUNTIME_DEPS}


# ------------------------------------------------------------------ #
# Dependency installation
# ------------------------------------------------------------------ #

def install_dependencies() -> dict[str, bool]:
    """
    Attempt to install any missing runtime binaries.
    Returns dict of name → True if now present (already was or just installed).
    Aborts with a printed message if not on an Arch-based system.
    """
    if not is_arch_based():
        print(
            "\n  MITTEN currently requires an Arch-based distro (pacman not found).\n"
            "  Install the runtime deps manually:\n"
            "    gpu-screen-recorder  ffmpeg  libnotify\n"
        )
        return {name: (shutil.which(name) is not None) for name in _RUNTIME_DEPS}

    aur = _aur_helper()
    results: dict[str, bool] = {}

    # Packages to install: AUR vs pacman
    aur_pkgs = {"gpu-screen-recorder"}
    pacman_pkgs = {"ffmpeg", "libnotify"}
    python_pkgs = {"python-evdev": "evdev", "python-pyqt6": "PyQt6"}

    # Collect what's missing
    missing_aur: set[str] = set()
    missing_pacman: set[str] = set()

    for binary, pkg in _RUNTIME_DEPS.items():
        if shutil.which(binary) is None:
            if pkg in aur_pkgs:
                missing_aur.add(pkg)
            else:
                missing_pacman.add(pkg)

    # Install AUR packages
    for pkg in missing_aur:
        if aur:
            print(f"  Installing {pkg} via {aur}...")
            rc = subprocess.run(
                [aur, "-S", "--noconfirm", pkg],
                capture_output=False,
            ).returncode
            if rc != 0:
                print(f"  ✗ {aur} failed for {pkg} — install manually: {aur} -S {pkg}")
        else:
            print(
                f"  ✗ No AUR helper found (tried yay, paru).\n"
                f"    Install manually: yay -S {pkg}  or  paru -S {pkg}"
            )

    # Install pacman packages (may need distinct packages)
    # Deduplicate: ffmpeg covers both ffmpeg+ffplay
    deduped_pacman = set()
    for binary, pkg in _RUNTIME_DEPS.items():
        if shutil.which(binary) is None and pkg in pacman_pkgs:
            deduped_pacman.add(pkg)

    for pkg in deduped_pacman:
        print(f"  Installing {pkg} via pacman...")
        rc = subprocess.run(
            ["sudo", "pacman", "-S", "--noconfirm", pkg],
            capture_output=False,
        ).returncode
        if rc != 0:
            print(f"  ✗ pacman failed for {pkg} — install manually: sudo pacman -S {pkg}")

    # Install Python packages via pacman
    for pacman_pkg, py_module in python_pkgs.items():
        try:
            __import__(py_module)
        except ImportError:
            print(f"  Installing {pacman_pkg} via pacman...")
            rc = subprocess.run(
                ["sudo", "pacman", "-S", "--noconfirm", pacman_pkg],
                capture_output=False,
            ).returncode
            if rc != 0:
                print(f"  ✗ pacman failed for {pacman_pkg} — install manually: sudo pacman -S {pacman_pkg}")

    # Re-check after installs
    for binary in _RUNTIME_DEPS:
        found = shutil.which(binary) is not None
        results[binary] = found
        sym = "✓" if found else "✗"
        print(f"  {sym} {binary}")

    return results


# ------------------------------------------------------------------ #
# Input group
# ------------------------------------------------------------------ #

def check_input_group() -> bool:
    """True if the current user is in the 'input' group."""
    import grp
    import os
    try:
        input_gid = grp.getgrnam("input").gr_gid
        return input_gid in os.getgroups()
    except KeyError:
        return False


# ------------------------------------------------------------------ #
# .desktop + icon install
# ------------------------------------------------------------------ #

def install_desktop_file() -> None:
    """
    Install mitten.desktop to ~/.local/share/applications/ and
    mitten.png to ~/.local/share/icons/hicolor/128x128/apps/.
    """
    apps_dir = Path.home() / ".local" / "share" / "applications"
    icons_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    # Desktop file — look in project root first, then installed package location
    desktop_src = Path(__file__).parent.parent.parent / "mitten.desktop"
    if not desktop_src.exists():
        # Installed via pip — write it inline
        desktop_src = None

    desktop_dest = apps_dir / "mitten.desktop"
    if desktop_src and desktop_src.exists():
        shutil.copy(desktop_src, desktop_dest)
    else:
        desktop_dest.write_text(
            "[Desktop Entry]\n"
            "Name=MITTEN\n"
            "Comment=Replay buffer screen recorder\n"
            "Exec=mitten\n"
            "Icon=mitten\n"
            "Type=Application\n"
            "Categories=AudioVideo;Recorder;\n"
            "Keywords=screen;record;clip;replay;\n"
            "StartupWMClass=MITTEN\n"
        )

    # Icon
    icon_src = Path(__file__).parent.parent / "assets" / "mitten.png"
    if icon_src.exists():
        shutil.copy(icon_src, icons_dir / "mitten.png")

    # Refresh desktop DB if available
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(apps_dir)],
            capture_output=True,
        )


# ------------------------------------------------------------------ #
# systemd service
# ------------------------------------------------------------------ #

def install_service() -> bool:
    """
    Copy mitten.service to ~/.config/systemd/user/, daemon-reload, enable.
    Returns True on success.
    """
    if not shutil.which("systemctl"):
        print("  systemctl not found — skipping service install.")
        return False

    service_src = Path(__file__).parent.parent.parent / "mitten.service"
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_dest = systemd_dir / "mitten.service"

    if not service_src.exists():
        # Generate service file inline
        mitten_bin = shutil.which("mitten") or "mitten"
        service_src = None
        service_dest.write_text(
            "[Unit]\n"
            "Description=MITTEN replay buffer recorder\n"
            "After=graphical-session.target\n\n"
            "[Service]\n"
            f"ExecStart={mitten_bin} run\n"
            "Restart=on-failure\n"
            "RestartSec=5\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
    else:
        shutil.copy(service_src, service_dest)

    rc1 = subprocess.run(
        ["systemctl", "--user", "daemon-reload"], capture_output=True
    ).returncode
    rc2 = subprocess.run(
        ["systemctl", "--user", "enable", "mitten.service"], capture_output=True
    ).returncode
    return rc1 == 0 and rc2 == 0


# ------------------------------------------------------------------ #
# Autostart
# ------------------------------------------------------------------ #

def install_autostart() -> None:
    """Write ~/.config/autostart/mitten-autostart.desktop."""
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    (autostart_dir / "mitten-autostart.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MITTEN Background\n"
        "Exec=systemctl --user start mitten.service\n"
        "Hidden=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def remove_autostart() -> None:
    """Remove the autostart .desktop file if present."""
    f = Path.home() / ".config" / "autostart" / "mitten-autostart.desktop"
    if f.exists():
        f.unlink()
