"""
MITTEN CLI — GUI-first entry point.

  mitten          → first-run install if needed, then launch GUI
  mitten restart  → reinstall, restart daemon, relaunch GUI
  mitten run      → start daemon (hidden; used by systemd ExecStart only)
  mitten _update  → auto-update UI (hidden; spawned by updater in konsole)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Suppress console windows for all subprocess calls on Windows (windowed exe)
if sys.platform == "win32":
    import subprocess as _subprocess
    _orig_Popen_init = _subprocess.Popen.__init__
    def _patched_Popen_init(self, args, **kwargs):
        kwargs.setdefault("creationflags", 0)
        kwargs["creationflags"] |= _subprocess.CREATE_NO_WINDOW
        _orig_Popen_init(self, args, **kwargs)
    _subprocess.Popen.__init__ = _patched_Popen_init

from .config import DATA_DIR as _DATA_DIR
_LOG_DIR = _DATA_DIR / "logs"
_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATEFMT = "%H:%M:%S"


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format=_FMT,
        datefmt=_DATEFMT,
    )


def _setup_file_logging(current_log: Path, crash_log: Path, verbose: bool,
                         show_dialog: bool = False) -> None:
    """Add a file handler for current session and install crashhook to write crash log."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    fh = logging.FileHandler(current_log, mode="w", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(fh)

    def _crashhook(exc_type, exc_value, exc_tb):
        import traceback
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            crash_log.write_text(tb_text, encoding="utf-8")
        except Exception:
            pass

        if show_dialog and not issubclass(exc_type, KeyboardInterrupt):
            _show_crash_dialog(exc_type, exc_value, tb_text)

        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _crashhook


def _show_crash_dialog(exc_type: type, exc_value: BaseException, tb_text: str) -> None:
    """Show a crash report dialog. Safe to call from sys.excepthook."""
    import subprocess as _sp
    try:
        from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout
        from PyQt6.QtWidgets import QLabel, QPushButton, QPlainTextEdit
        from PyQt6.QtCore import Qt

        app = QApplication.instance() or QApplication(sys.argv)

        summary = f"{exc_type.__name__}: {exc_value}"
        if len(summary) > 200:
            summary = summary[:197] + "..."

        dlg = QDialog()
        dlg.setWindowTitle("Mitten crashed")
        dlg.setMinimumWidth(560)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        from .errors import E_GUI_CRASH
        title = QLabel(f"~( >.x.<)>  Mitten crashed  [{E_GUI_CRASH}]")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #f38ba8;")
        layout.addWidget(title)

        err_lbl = QLabel(summary)
        err_lbl.setWordWrap(True)
        err_lbl.setStyleSheet(
            "font-family: monospace; font-size: 11px; color: #cdd6f4;"
            "background: #1e1e2e; border-radius: 6px; padding: 10px;"
        )
        layout.addWidget(err_lbl)

        tb_view = QPlainTextEdit(tb_text)
        tb_view.setReadOnly(True)
        tb_view.setMaximumHeight(180)
        tb_view.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #a6adc8;"
            "background: #181825; border-radius: 4px; padding: 8px;"
        )
        layout.addWidget(tb_view)

        report_lbl = QLabel(
            "Please open a bug report at "
            "<a href='https://github.com/mitmitmitmitmitmit/mitten/issues' "
            "style='color:#cba6f7;'>github.com/mitmitmitmitmitmit/mitten</a>"
            " with your crash log and a screenshot of this message."
        )
        report_lbl.setOpenExternalLinks(True)
        report_lbl.setWordWrap(True)
        report_lbl.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(report_lbl)

        if sys.platform == "win32":
            _fallback_text = (
                "If this crash is caused by a recent update, you can download a previous "
                "release from the releases page on GitHub."
            )
        else:
            _fallback_text = (
                "If you cannot access the GUI, run "
                "<span style='font-family:monospace; color:#a6e3a1;'>mitten restart</span>"
                " in your terminal to reinstall and restart Mitten. "
                "If this crash is caused by a recent update, wait for a fix to be released, "
                "then run that command."
            )
        restart_lbl = QLabel(_fallback_text)
        restart_lbl.setWordWrap(True)
        restart_lbl.setStyleSheet(
            "color: #6c7086; font-size: 10px; font-style: italic;"
            "background: #181825; border-radius: 5px; padding: 8px 10px;"
        )
        layout.addWidget(restart_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        open_btn = QPushButton("Open log folder")
        open_btn.setStyleSheet(
            "QPushButton { background: #45475a; color: #cdd6f4; border: none;"
            "border-radius: 6px; padding: 7px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #585b70; }"
        )
        def _open_log_folder() -> None:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                import os as _os_open
                _os_open.startfile(str(_LOG_DIR))
            else:
                _sp.Popen(["xdg-open", str(_LOG_DIR)],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

        open_btn.clicked.connect(_open_log_folder)

        revert_btn = QPushButton("Revert to previous version")
        revert_btn.setStyleSheet(
            "QPushButton { background: #fab387; color: #1e1e2e; border: none;"
            "border-radius: 6px; padding: 7px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #f9c096; }"
        )

        def _do_revert() -> None:
            revert_btn.setEnabled(False)
            revert_btn.setText("reverting…")
            try:
                from .updater import restore_backup, get_repo_dir
                import subprocess as _sub
                import json as _json
                # Capture the bad version before reverting
                try:
                    from importlib.metadata import version as _iv
                    _bad_ver = _iv("mitten")
                except Exception:
                    _bad_ver = ""
                repo = get_repo_dir()
                ok = repo is not None and restore_backup(repo)
                if ok:
                    # Write revert lock so update notifications stay suppressed
                    # until a version newer than the bad one is published
                    if _bad_ver:
                        try:
                            from .config import REVERT_LOCK_FILE
                            REVERT_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
                            REVERT_LOCK_FILE.write_text(
                                _json.dumps({"blocked_version": _bad_ver})
                            )
                        except Exception:
                            pass
                    _sub.run(
                        [sys.executable, "-m", "pip", "install", "-e", ".",
                         "--break-system-packages", "-q"],
                        cwd=repo, capture_output=True,
                    )
                    _sub.Popen(
                        ["systemctl", "--user", "restart", "mitten.service"],
                        stdout=_sub.DEVNULL, stderr=_sub.DEVNULL,
                    )
                    revert_btn.setText("reverted — relaunch mitten")
                else:
                    revert_btn.setText("no backup found")
                    revert_btn.setEnabled(True)
            except Exception as _e:
                revert_btn.setText(f"failed: {_e}")
                revert_btn.setEnabled(True)

        revert_btn.clicked.connect(_do_revert)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { background: #f38ba8; color: #1e1e2e; border: none;"
            "border-radius: 6px; padding: 7px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #eba0ac; }"
        )
        close_btn.clicked.connect(dlg.accept)

        btn_row.addWidget(open_btn)
        btn_row.addWidget(revert_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()
    except Exception:
        pass


def cmd_run(args: argparse.Namespace) -> None:
    _setup_logging(args.verbose)
    _setup_file_logging(
        _LOG_DIR / "daemon_current.log",
        _LOG_DIR / "daemon_crash.log",
        args.verbose,
    )

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


def cmd_restart(args: argparse.Namespace) -> None:
    """Stop everything, reinstall from repo, restart service, relaunch GUI."""
    import os
    import subprocess

    from .updater import get_repo_dir

    repo_dir = get_repo_dir()
    current_pid = os.getpid()

    print("\n  ~( ^.x.^)>  mitten restart\n")

    # 1. Kill any running GUI / bare daemon processes (not us, not systemd-managed)
    print("  [1] killing mitten processes...")
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.pid == current_pid:
                    continue
                cmdline = " ".join(proc.cmdline())
                if "mitten" in cmdline and "restart" not in cmdline:
                    proc.terminate()
            except Exception:
                pass
    except ImportError:
        import logging as _log
        _log.getLogger(__name__).warning("psutil not available, using pkill fallback")
        subprocess.run(
            ["pkill", "-f", "mitten run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    print("      \u2713")

    # 2. Stop systemd service
    print("  [2] stopping mitten.service...")
    subprocess.run(
        ["systemctl", "--user", "stop", "mitten.service"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("      \u2713")

    # 3. Fetch + reset + reinstall (mirrors auto-updater; skip git steps in dev)
    if repo_dir:
        is_prod = repo_dir.name == "Mitten"
        print(f"  [3] reinstalling from {repo_dir.name}...")
        ok = True
        if is_prod:
            from .updater import GITHUB_URL
            r = subprocess.run(
                ["git", "fetch", GITHUB_URL, "main"],
                cwd=repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                subprocess.run(
                    ["git", "reset", "--hard", "FETCH_HEAD"],
                    cwd=repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                print("      ! git fetch failed — installing from current state")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".",
             "--break-system-packages", "-q"],
            cwd=repo_dir,
        )
        ok = result.returncode == 0
        print(f"      {'✓' if ok else '✗  pip install failed'}")
    else:
        print("  [3] skipping reinstall — not in a git repo")

    # 4. Start service
    print("  [4] starting mitten.service...")
    subprocess.run(
        ["systemctl", "--user", "start", "mitten.service"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("      \u2713")

    # 5. Launch GUI detached
    print("  [5] launching GUI...")
    subprocess.Popen(
        ["mitten"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print("      \u2713\n")
    print("  done.\n")


def cmd_update(args: argparse.Namespace) -> None:
    """Hidden subcommand: runs the update UI inside a konsole window."""
    from .updater import run_update_ui
    run_update_ui(args.from_hash, args.to_hash)


def _launch_gui(abuse_reveal: bool = False) -> None:
    _setup_logging(False)
    _setup_file_logging(
        _LOG_DIR / "gui_current.log",
        _LOG_DIR / "gui_crash.log",
        False,
        show_dialog=True,
    )
    try:
        from .gui import launch_gui
    except ImportError:
        print(
            "PyQt6 is required for the GUI.\n"
            "Install it:  sudo pacman -S python-pyqt6\n"
            "         or: pip install PyQt6>=6.5"
        )
        sys.exit(1)
    launch_gui(abuse_reveal=abuse_reveal)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mitten",
        description="MITTEN — replay buffer screen recorder",
    )
    sub = parser.add_subparsers(dest="command")

    # User-facing
    sub.add_parser("restart", help="reinstall, restart daemon, relaunch GUI")

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

    # Hidden flag used by the anti-disable gauntlet in settings.py
    parser.add_argument("--_abuse-reveal", dest="abuse_reveal", action="store_true",
                        help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "restart":
        cmd_restart(args)
        return

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

    _launch_gui(abuse_reveal=getattr(args, "abuse_reveal", False))
