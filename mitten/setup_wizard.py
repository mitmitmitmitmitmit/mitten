"""
Terminal system installer — runs on first launch when no config exists.

Steps:
  1. Welcome + auto-install runtime deps
  2. Input group check / fix
  3. Write default config, install .desktop, systemd service
  4. Autostart prompt
  5. Start-now prompt
  6. Done → GUI launches
"""
from __future__ import annotations

import subprocess
import sys


_BANNER = r"""
 /\_____/\
 ( ^.x.^ )   MITTEN
  )     (    replay buffer recorder
 (  ===  )
  `-----'
"""


def run_wizard() -> None:
    """Run the terminal installer and return when done."""
    try:
        _run_installer()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.")
        sys.exit(0)


def _run_installer() -> None:
    print(_BANNER)
    print("=" * 52)
    print("  First-run system setup")
    print("=" * 52)

    # ── Step 1: Dependencies ──────────────────────────────
    print("\n[1/4] Checking runtime dependencies...\n")
    from .gui.system_setup import (
        check_dependencies,
        install_dependencies,
        is_arch_based,
    )

    deps = check_dependencies()
    missing = [name for name, ok in deps.items() if not ok]

    if not missing:
        print("  All runtime dependencies found.\n")
    else:
        print(f"  Missing: {', '.join(missing)}\n")
        if is_arch_based():
            print("  Auto-installing missing packages...\n")
            install_dependencies()
        else:
            print(
                "  MITTEN requires an Arch-based distro for auto-install.\n"
                "  Install manually:\n"
                "    gpu-screen-recorder → yay -S gpu-screen-recorder\n"
                "    ffmpeg              → sudo pacman -S ffmpeg\n"
                "    notify-send         → sudo pacman -S libnotify\n"
            )

        import shutil as _sh
        if not _sh.which("gpu-screen-recorder"):
            print(
                "\n  WARNING: gpu-screen-recorder not found.\n"
                "  Recording will not work until it is installed.\n"
                "  MITTEN will launch but the dashboard will show a warning.\n"
            )

    # ── Step 2: Input group ───────────────────────────────
    print("[2/4] Checking input group membership...")
    from .gui.system_setup import check_input_group

    if check_input_group():
        print("  Already in 'input' group.\n")
    else:
        print("  Not in 'input' group — mouse button detection requires it.")
        ans = _ask("  Add current user to 'input' group? [Y/n] ", default="y")
        if ans == "y":
            import os
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
            rc = subprocess.run(
                ["sudo", "usermod", "-aG", "input", user],
                capture_output=False,
            ).returncode
            if rc == 0:
                print(
                    "  Done. You must log out and back in for this to take effect.\n"
                )
            else:
                print(
                    "  Command failed — run manually:\n"
                    f"    sudo usermod -aG input {user}\n"
                )
        else:
            print("  Skipped. Mouse button detection may not work.\n")

    # ── Step 3: Config + desktop integration ─────────────
    print("[3/4] Installing MITTEN...")

    from .config import CONFIG_FILE, create_default_config
    from .gui.system_setup import install_desktop_file, install_service

    if not CONFIG_FILE.exists():
        create_default_config()
        print(f"  ✓ Default config written: {CONFIG_FILE}")
    else:
        print(f"  Config already exists: {CONFIG_FILE}")

    install_desktop_file()
    print("  ✓ App launcher entry installed (~/.local/share/applications/)")

    ok = install_service()
    if ok:
        print("  ✓ Systemd service installed and enabled")
    else:
        print("  ✗ Service install failed (systemctl missing?)")

    print()

    # ── Step 4: Autostart ─────────────────────────────────
    print("[4/4] Autostart on login")
    ans = _ask("  Start MITTEN recording daemon on login? [Y/n] ", default="y")
    if ans == "y":
        from .gui.system_setup import install_autostart
        install_autostart()
        print("  ✓ Autostart enabled (~/.config/autostart/)\n")
    else:
        print("  Skipped.\n")

    # ── Start now? ────────────────────────────────────────
    ans = _ask("Start recording now? [Y/n] ", default="y")
    if ans == "y":
        import shutil as _sh
        if _sh.which("systemctl"):
            subprocess.run(
                ["systemctl", "--user", "start", "mitten.service"],
                capture_output=True,
            )
            print("  Recording daemon started.\n")
        else:
            print("  systemctl not found — start manually: mitten run\n")

    print("Setup complete! Launching MITTEN...\n")


def _ask(prompt: str, default: str = "y") -> str:
    """Prompt for y/n, return 'y' or 'n'. Returns default on empty input."""
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return default if ans == "" else ("y" if ans.startswith("y") else "n")
