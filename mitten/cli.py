"""
MITTEN CLI — GUI-first entry point.

  mitten          → first-run install if needed, then launch GUI
  mitten run      → start daemon (hidden; used by systemd ExecStart only)
  mitten _update  → auto-update UI (hidden; spawned by updater in konsole)
"""
from __future__ import annotations

import argparse
import logging
import sys


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> None:
    _setup_logging(args.verbose)

    from .config import load_config
    cfg = load_config()

    if args.mode:
        import dataclasses
        cfg = dataclasses.replace(cfg, general=dataclasses.replace(cfg.general, mode=args.mode))
    if args.buffer:
        import dataclasses
        cfg = dataclasses.replace(cfg, general=dataclasses.replace(cfg.general, buffer_seconds=args.buffer))

    if args.dry_run:
        from .recorder import GpuRecorder
        cmd = GpuRecorder(cfg).build_command()
        print("gpu-screen-recorder command:")
        print("  " + " ".join(cmd))
        return

    from .updater import check_for_update, get_repo_dir, spawn_update_terminal

    # Dev mode indicator — printed when running from MittenDev instead of production Mitten
    _repo = get_repo_dir()
    if _repo and _repo.name != "Mitten":
        print(f"\n  [ DEV ] running from {_repo}\n")

    # Check for updates before starting the daemon.
    # Any failure is silently ignored so the daemon always starts.
    update = check_for_update()
    if update is not None:
        old_hash, new_hash = update
        spawn_update_terminal(old_hash, new_hash)
        # Exit cleanly — systemd Restart=on-failure won't trigger (exit code 0),
        # and the _update command will restart the service when it's done.
        sys.exit(0)

    from .daemon import MittenDaemon
    MittenDaemon(cfg, verbose=args.verbose).run()


def cmd_update(args: argparse.Namespace) -> None:
    """Hidden subcommand: runs the update UI inside a konsole window."""
    from .updater import run_update_ui
    run_update_ui(args.from_hash, args.to_hash)


def _launch_gui() -> None:
    try:
        from .gui import launch_gui
    except ImportError:
        print(
            "PyQt6 is required for the GUI.\n"
            "Install it:  sudo pacman -S python-pyqt6\n"
            "         or: pip install PyQt6>=6.5"
        )
        sys.exit(1)
    launch_gui()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mitten",
        description="MITTEN — replay buffer screen recorder",
    )
    sub = parser.add_subparsers(dest="command")

    # Hidden — only used by systemd service ExecStart
    p_run = sub.add_parser("run", help=argparse.SUPPRESS)
    p_run.add_argument("--verbose", "-v", action="store_true")
    p_run.add_argument("--mode", choices=["desktop", "window", "game"])
    p_run.add_argument("--buffer", type=int)
    p_run.add_argument("--dry-run", action="store_true")

    # Hidden — spawned by updater.spawn_update_terminal() in a konsole window
    p_update = sub.add_parser("_update", help=argparse.SUPPRESS)
    p_update.add_argument("--from", dest="from_hash", required=True)
    p_update.add_argument("--to", dest="to_hash", required=True)

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
        return

    if args.command == "_update":
        cmd_update(args)
        return

    # Default: first-run check, then GUI
    from .config import CONFIG_FILE
    if not CONFIG_FILE.exists():
        from .setup_wizard import run_wizard
        run_wizard()

    _launch_gui()
