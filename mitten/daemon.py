"""
Main orchestrator: manages gpu-screen-recorder, watches for saved clips,
fires watermark post-processing, and coordinates game detection + trigger.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable

if sys.platform != "win32":
    import fcntl

from .config import MittenConfig, TMP_DIR, PID_FILE, PAUSE_FILE, RECORDER_DEAD_FILE, GUI_PRESENCE_FILE
from .detect import GameDetector, GameInfo
from .recorder import GpuRecorder, SessionRecorder, make_recorder
from .trigger import TriggerListener
from . import notify, save, sounds
from .errors import (fmt as _efmt, E_SAVE_TIMEOUT, E_RECORDER_DEAD, E_TRIGGER,
                      E_RECORDER_CRASH_LIMIT)
from .discord_presence import DiscordPresence

log = logging.getLogger(__name__)


def _recorder_settings_changed(old: MittenConfig, new: MittenConfig) -> bool:
    """Return True only if a setting that actually affects the gsr process changed."""
    og, ng = old.general, new.general
    or_, nr = old.recorder, new.recorder
    return (
        og.mode         != ng.mode         or
        og.monitor      != ng.monitor      or
        og.framerate    != ng.framerate    or
        og.buffer_seconds != ng.buffer_seconds or
        or_.quality       != nr.quality       or
        or_.capture_codec != nr.capture_codec or
        or_.container     != nr.container     or
        or_.audio_device  != nr.audio_device  or
        or_.mic_device    != nr.mic_device
    )


def _trigger_settings_changed(old: MittenConfig, new: MittenConfig) -> bool:
    """Return True if any trigger setting changed."""
    return (
        old.trigger.button       != new.trigger.button or
        old.trigger.cooldown     != new.trigger.cooldown or
        old.trigger.trigger_type != new.trigger.trigger_type or
        old.trigger.trigger_key  != new.trigger.trigger_key
    )


class ClipWatcher:
    """
    Polls TMP_DIR for new .mp4 files written by gpu-screen-recorder on SIGUSR1.
    Debounces by checking file size stability, then fires the on_clip_ready callback.
    """

    def __init__(self, on_clip_ready: Callable[[Path], None]) -> None:
        self._on_clip_ready = on_clip_ready
        self._known: set[str] = set()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._shutdown.clear()
        # Pre-populate known files so we don't reprocess clips from a previous run
        for f in TMP_DIR.glob("*.mp4"):
            self._known.add(f.name)

        self._thread = threading.Thread(
            target=self._poll_loop,
            name="clip-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _poll_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                self._scan()
            except Exception as e:
                log.debug("ClipWatcher scan error: %s", e)
            self._shutdown.wait(0.5)

    def _scan(self) -> None:
        for f in TMP_DIR.glob("*.mp4"):
            if f.name in self._known:
                continue
            # Debounce: wait until file size is stable (gpu-screen-recorder finished writing)
            try:
                size1 = f.stat().st_size
            except OSError:
                continue
            self._shutdown.wait(0.5)
            if not f.exists():
                continue
            try:
                size2 = f.stat().st_size
            except OSError:
                continue
            if size1 == size2 and size1 > 0:
                self._known.add(f.name)
                log.info("New clip detected: %s", f.name)
                try:
                    self._on_clip_ready(f)
                except Exception as e:
                    log.error("on_clip_ready raised: %s", e)


class MittenDaemon:
    def __init__(self, config: MittenConfig, verbose: bool = False) -> None:
        self._config = config
        self._verbose = verbose
        self._shutdown = threading.Event()

        self._pid_fd = None  # held open for lifetime of process to maintain flock (Linux)
        self._filelock = None  # Windows filelock replacement
        self._save_timer: threading.Timer | None = None
        self._current_game: "GameInfo | None" = None
        self._presence = DiscordPresence()
        # Track daemon's own current state so we can re-assert it when GUI deactivates
        self._daemon_state: str = "idle"
        self._daemon_state_ov: str | None = None
        self._daemon_detail_ov: str | None = None
        self._daemon_name_ov: str | None = None
        self._recorder = make_recorder(config, on_crash=self._on_recorder_crash)
        self._session_recorder = SessionRecorder(config)
        self._watcher = ClipWatcher(on_clip_ready=self._on_clip_ready)
        self._trigger = TriggerListener(
            config,
            on_trigger=self._on_trigger,
            on_error=self._on_trigger_error,
            on_triple_trigger=self._on_triple_trigger,
        )
        self._detector: GameDetector | None = None

        if config.general.mode == "game" and config.game_detection.enabled:
            self._detector = GameDetector(
                config,
                on_game_start=self._on_game_start,
                on_game_stop=self._on_game_stop,
            )

    def run(self) -> None:
        self._setup_signal_handlers()
        self._ensure_dirs()
        self._lock_pid_file()
        self._cleanup_stale_files()

        log.info(
            "MITTEN starting (mode=%s, buffer=%ds, monitor=%s)",
            self._config.general.mode,
            self._config.general.buffer_seconds,
            self._config.general.monitor,
        )

        self._presence.start()
        self._set_presence("idle")
        threading.Thread(target=self._gui_presence_loop, name="gui-presence", daemon=True).start()

        self._watcher.start()

        if self._config.general.mode != "game":
            try:
                self._recorder.start()
                self._set_presence("recording", name_override=self._recording_name())
            except RuntimeError as e:
                log.error("%s", e)
                print(f"\nError: {e}\n")
                sys.exit(1)

        has_input = self._trigger.start()
        if not has_input and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  MITTEN — No Input Found",
                "No input devices detected. Trigger via SIGUSR1 or the tray icon.",
                urgency="normal", icon="input-mouse", timeout_ms=5000,
            )

        if self._detector:
            self._detector.start()
            log.info("Game mode: waiting for a game to be detected...")

        if self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten is running",
                f"{self._config.general.mode} mode · "
                f"{self._config.general.buffer_seconds}s buffer · "
                "press your button to clip",
                urgency="low", icon="media-record", timeout_ms=4000,
            )

        log.info("MITTEN running. Press configured button to save a clip.")
        log.info("Stop with Ctrl+C or: systemctl --user stop mitten")

        try:
            while not self._shutdown.is_set():
                self._shutdown.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self._teardown()

    def trigger_save(self) -> None:
        """Programmatically trigger a save (called by SIGUSR1 handler)."""
        self._on_trigger()

    def _on_triple_trigger(self) -> None:
        """Toggle full session recording on/off."""
        if self._session_recorder.is_recording():
            # Stop — save the file through the normal post-process pipeline
            log.info("Session recording stopped by triple-click")
            self._set_presence("recording" if self._recorder.is_running() else "idle", name_override=self._recording_name() if self._recorder.is_running() else None)
            sounds.session_stop()
            path = self._session_recorder.stop()
            if path:
                notify.notify(
                    "~( ^.x.^)>  session saved",
                    f"full recording: {path.name}",
                )
                # Run through watermark/compress pipeline like a normal clip
                save.process_clip(
                    raw_path=path,
                    config=self._config,
                    on_success=lambda p, s: log.info("Session processed: %s (%ds)", p, s),
                    on_failure=lambda msg: log.error("Session post-process failed: %s", msg),
                )
            else:
                notify.notify("~( x.x.^)>  session error", "recording was empty or lost")
        else:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_path = TMP_DIR / f"session_{timestamp}.mp4"
            log.info("Session recording started by triple-click → %s", out_path)
            self._set_presence("session")
            sounds.session_start()
            self._session_recorder.start(out_path)
            notify.notify(
                "~( ^.x.^)>  session recording",
                "triple-click again to stop and save",
            )

    def _on_trigger(self) -> None:
        if not self._recorder.is_running():
            log.warning("Trigger fired but recorder not running — nothing to save")
            if self._config.notifications.on_error and self._config.notifications.enabled:
                if self._config.general.mode == "game":
                    notify.notify(
                        "~( ^.x.^)>  Mitten",
                        "No game detected — start a game first",
                        urgency="low", icon="applications-games", timeout_ms=3000,
                    )
                else:
                    notify.notify(
                        "~( ^.x.^)>  Mitten — Save Failed",
                        "Recorder not active",
                        urgency="normal", icon="dialog-error", timeout_ms=5000,
                    )
            return

        if self._config.notifications.on_save and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten",
                f"Saving clip ({self._config.general.buffer_seconds}s)...",
                urgency="low", icon="media-record", timeout_ms=3000,
            )

        if self._recorder.save_replay():
            sounds.save_triggered()
            self._set_presence("saving")
            # 30-second watchdog: notify if no clip appears
            self._save_timer = threading.Timer(30.0, self._on_save_timeout)
            self._save_timer.start()

    def _on_save_timeout(self) -> None:
        log.warning("Save watchdog: no clip appeared within 30s")
        if self._config.notifications.on_error and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten — Save May Have Failed",
                _efmt(E_SAVE_TIMEOUT, "No clip appeared after 30 seconds"),
                urgency="critical", icon="dialog-warning", timeout_ms=8000,
            )

    def _on_trigger_error(self, reason: str) -> None:
        log.error("Trigger error: %s", reason)
        if self._config.notifications.on_error and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten — Trigger Error",
                _efmt(E_TRIGGER, reason),
                urgency="normal", icon="input-mouse", timeout_ms=6000,
            )

    def _on_clip_ready(self, raw_path: Path) -> None:
        """Called by ClipWatcher when gpu-screen-recorder writes a new clip."""
        # Cancel the 30-second save watchdog
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None

        def on_success(path: Path, seconds: int) -> None:
            sounds.save_done()
            if self._config.notifications.on_save and self._config.notifications.enabled:
                import random as _rnd
                _body = f"{path.name} ({seconds}s)"
                try:
                    from .gui.themes import LIGHT_MODE_ACTIVE as _LMA
                    if _LMA and _rnd.random() < 0.30:
                        _taunts = [
                            "nice clip. shame about the theme.",
                            "saved. still in light mode though.",
                            "caught it. fix your theme.",
                            "clip saved. god is watching.",
                        ]
                        _body = f"{path.name} ({seconds}s)  — {_rnd.choice(_taunts)}"
                except Exception:
                    pass
                notify.notify(
                    "~( ^.x.^)>  Mitten caught one!",
                    _body,
                    urgency="normal", icon="emblem-videos", timeout_ms=6000,
                )

        def on_failure(reason: str) -> None:
            sounds.save_error()
            if self._config.notifications.on_error and self._config.notifications.enabled:
                notify.notify(
                    "~( ^.x.^)>  Mitten — Save Failed",
                    reason,
                    urgency="normal", icon="dialog-error", timeout_ms=5000,
                )

        self._set_presence("recording" if self._recorder.is_running() else "idle", name_override=self._recording_name() if self._recorder.is_running() else None)

        save.process_clip(
            raw_path=raw_path,
            config=self._config,
            on_success=on_success,
            on_failure=on_failure,
            meta={
                "saved_manually": True,
                "clip_type": "clip",
                "game": self._current_game.name if self._current_game else None,
            },
        )

    def _on_recorder_crash(self, reason: str) -> None:
        self._set_presence("recorder_dead")
        log.error("Recorder crash: %s", reason)
        # Write state file so GUI can show "recorder dead" in status banner
        try:
            RECORDER_DEAD_FILE.write_text(reason)
        except OSError:
            pass
        if self._config.notifications.on_error and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten — Capture Error",
                _efmt(E_RECORDER_CRASH_LIMIT if "giving up" in reason else E_RECORDER_DEAD, reason),
                urgency="critical", icon="dialog-error", timeout_ms=8000,
            )

    def _set_presence(self, state: str, state_override=None, detail_override=None, name_override=None) -> None:
        """Update tracked daemon state and send to Discord (skips send if GUI is focused)."""
        self._daemon_state = state
        self._daemon_state_ov = state_override
        self._daemon_detail_ov = detail_override
        self._daemon_name_ov = name_override
        if not GUI_PRESENCE_FILE.exists():
            self._presence.set_state(state, state_override, detail_override, name_override)

    def _gui_presence_loop(self) -> None:
        """Background thread: watches for GUI presence file and forwards it to Discord."""
        last_mtime: float | None = None
        gui_was_active = False
        while not self._shutdown.is_set():
            try:
                if GUI_PRESENCE_FILE.exists():
                    mtime = GUI_PRESENCE_FILE.stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        data = json.loads(GUI_PRESENCE_FILE.read_text())
                        self._presence.set_state(
                            "idle",
                            state_override=data.get("state_override"),
                            detail_override=data.get("detail_override"),
                            name_override=data.get("name_override"),
                        )
                        gui_was_active = True
                elif gui_was_active:
                    gui_was_active = False
                    last_mtime = None
                    self._presence.reset_rate_limit()
                    self._presence.set_state(
                        self._daemon_state,
                        self._daemon_state_ov,
                        self._daemon_detail_ov,
                        self._daemon_name_ov,
                    )
            except Exception as e:
                log.debug("GUI presence watcher: %s", e)
            self._shutdown.wait(2.0)

    def _recording_name(self) -> str | None:
        dc = self._config.discord
        if not dc.show_name:
            return None
        if not dc.show_mode_label:
            return "Mitten"
        mode = self._config.general.mode
        if mode == "window":
            return "window with Mitten"
        if mode == "game":
            return "game with Mitten"
        return "desktop with Mitten"

    def _on_game_start(self, game: "GameInfo") -> None:
        self._current_game = game
        dc = self._config.discord
        if dc.show_game_name:
            detail = "~( >.x.<)> \u2728" if dc.show_ascii else None
            state = f"Mitten is watching {game.name}"
            name = f"{game.name} with Mitten" if dc.show_name else None
        else:
            detail = None
            state = None
            name = ("clipping with Mitten" if dc.show_mode_label else "Mitten") if dc.show_name else None
        self._set_presence("game", state_override=state, detail_override=detail, name_override=name)
        log.info("Game started: %s", game.name)
        if self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten is watching",
                f"{game.name} detected",
                urgency="low", icon="applications-games", timeout_ms=4000,
            )

        if not self._config.game_detection.auto_switch:
            return

        monitor = self._config.general.monitor
        target = monitor if monitor != "auto" else "screen"
        try:
            self._recorder.start(target=target)
        except RuntimeError as e:
            log.error("Failed to start recorder for game: %s", e)
            return

        if self._config.notifications.on_start and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten Recording",
                f"window mode · {self._config.general.buffer_seconds}s buffer",
                urgency="low", icon="media-record", timeout_ms=3000,
            )

    def _on_game_stop(self, game: "GameInfo") -> None:
        self._current_game = None
        self._set_presence("idle")
        log.info("Game stopped: %s", game.name)
        if self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten paused",
                f"{game.name} closed",
                urgency="low", icon="media-playback-pause", timeout_ms=3000,
            )
        self._recorder.stop()
        log.info("Game mode: capture paused. Waiting for next game...")

    def _ensure_dirs(self) -> None:
        TMP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._config.general.save_dir.mkdir(parents=True, exist_ok=True)
        # Clear stale state files from previous run
        PAUSE_FILE.unlink(missing_ok=True)
        RECORDER_DEAD_FILE.unlink(missing_ok=True)
        GUI_PRESENCE_FILE.unlink(missing_ok=True)

    def _lock_pid_file(self) -> None:
        if sys.platform == "win32":
            try:
                from filelock import FileLock, Timeout
                lock_path = str(PID_FILE) + ".lock"
                self._filelock = FileLock(lock_path)
                self._filelock.acquire(timeout=0)
            except Exception:
                log.error("daemon already running — exiting")
                sys.exit(1)
            try:
                with open(PID_FILE, 'w') as f:
                    f.write(str(os.getpid()))
            except OSError as e:
                log.warning("Could not write PID file: %s", e)
            return

        try:
            self._pid_fd = open(PID_FILE, 'w')
            fcntl.flock(self._pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("daemon already running — exiting")
            sys.exit(1)
        except OSError as e:
            log.warning("Could not lock PID file: %s", e)
            return
        self._pid_fd.write(str(os.getpid()))
        self._pid_fd.flush()

    def _cleanup_stale_files(self) -> None:
        now = time.time()
        for f in TMP_DIR.glob("*.mp4"):
            try:
                if (now - f.stat().st_mtime) > 3600:
                    f.unlink(missing_ok=True)
            except OSError:
                pass
        for f in TMP_DIR.glob("seg_*.ts"):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    def _teardown(self) -> None:
        log.info("Shutting down MITTEN...")
        if self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten stopped",
                "Recording has ended",
                urgency="low", icon="media-playback-stop", timeout_ms=3000,
            )
        self._presence.clear()
        self._presence.stop()
        self._trigger.stop()
        if self._detector:
            self._detector.stop()
        self._watcher.stop()
        self._recorder.stop()
        if self._pid_fd is not None:
            try:
                self._pid_fd.close()
            except Exception:
                pass
            self._pid_fd = None
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        log.info("MITTEN stopped.")

    def _toggle_pause(self) -> None:
        """SIGUSR2: pause recording if active, resume if paused."""
        if PAUSE_FILE.exists():
            log.info("Resuming recording (SIGUSR2)")
            PAUSE_FILE.unlink(missing_ok=True)
            try:
                self._recorder.start()
                self._set_presence("recording", name_override=self._recording_name())
            except RuntimeError as e:
                log.error("Failed to resume recorder: %s", e)
                return
            if self._config.notifications.enabled:
                notify.notify(
                    "~( ^.x.^)>  Mitten resumed",
                    "Recording buffer restarted",
                    urgency="low", icon="media-record", timeout_ms=3000,
                )
        else:
            log.info("Pausing recording (SIGUSR2)")
            self._set_presence("paused")
            self._recorder.stop()
            try:
                PAUSE_FILE.touch()
            except OSError:
                pass
            if self._config.notifications.enabled:
                notify.notify(
                    "~( ^.x.^)>  Mitten paused",
                    "Recording buffer paused — press Resume to continue",
                    urgency="low", icon="media-playback-pause", timeout_ms=3000,
                )

    def _reload_config(self) -> None:
        """Reload config from disk and apply mode/recorder changes."""
        try:
            from .config import load_config
            new_cfg = load_config()
        except Exception as e:
            log.error("Config reload failed: %s", e)
            return

        old_mode = self._config.general.mode
        new_mode = new_cfg.general.mode
        old_cfg = self._config
        self._config = new_cfg

        if old_mode == "game" and new_mode != "game":
            if self._detector:
                self._detector.stop()
                self._detector = None
            if not self._recorder.is_running():
                try:
                    self._recorder.start()
                    self._set_presence("recording", name_override=self._recording_name())
                    log.info("Config reload: %s mode active, recorder started", new_mode)
                except RuntimeError as e:
                    log.error("Recorder start after reload failed: %s", e)

        elif old_mode != "game" and new_mode == "game":
            self._recorder.stop()
            self._set_presence("idle")
            if new_cfg.game_detection.enabled and not self._detector:
                from .detect import GameDetector
                self._detector = GameDetector(
                    new_cfg,
                    on_game_start=self._on_game_start,
                    on_game_stop=self._on_game_stop,
                )
                self._detector.start()
                log.info("Config reload: game mode active, detector started")

        else:
            if self._recorder.is_running() and _recorder_settings_changed(old_cfg, new_cfg):
                self._recorder.restart()
                log.info("Config reload: recorder settings changed, restarted")
                self._set_presence("recording", name_override=self._recording_name())
            else:
                log.info("Config reload: non-recorder settings updated, recorder kept running")

        # Restart trigger listener if button or cooldown changed
        if _trigger_settings_changed(old_cfg, new_cfg):
            try:
                self._trigger.stop()
                self._trigger = TriggerListener(
                    new_cfg,
                    on_trigger=self._on_trigger,
                    on_error=self._on_trigger_error,
                    on_triple_trigger=self._on_triple_trigger,
                )
                self._trigger.start()
                log.info("Config reload: trigger settings changed, listener restarted (button=%s)",
                         new_cfg.trigger.button)
            except Exception as e:
                log.error("Trigger restart after reload failed: %s", e)

        log.info("Config reloaded (mode: %s → %s)", old_mode, new_mode)

    def _setup_signal_handlers(self) -> None:
        def _handle_shutdown(signum, frame):
            log.info("Received signal %d, shutting down...", signum)
            self._shutdown.set()

        def _handle_save(signum, frame):
            log.info("Received SIGUSR1 — triggering save")
            self.trigger_save()

        def _handle_pause(signum, frame):
            log.info("Received SIGUSR2 — toggling pause")
            threading.Thread(target=self._toggle_pause, daemon=True).start()

        def _handle_reload(signum, frame):
            log.info("Received SIGHUP — reloading config")
            threading.Thread(target=self._reload_config, daemon=True).start()

        signal.signal(signal.SIGINT, _handle_shutdown)
        signal.signal(signal.SIGTERM, _handle_shutdown)

        if sys.platform != "win32":
            signal.signal(signal.SIGUSR1, _handle_save)
            signal.signal(signal.SIGUSR2, _handle_pause)
            signal.signal(signal.SIGHUP, _handle_reload)
        else:
            # Windows: listen for JSON commands over TCP socket
            threading.Thread(
                target=self._ipc_listener,
                name="ipc-listener",
                daemon=True,
            ).start()

    def _ipc_listener(self) -> None:
        """Windows IPC: accept JSON commands on TCP port 47821."""
        import socket as _socket
        from .daemon_utils import IPC_PORT
        srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", IPC_PORT))
            srv.listen(5)
            srv.settimeout(1.0)
            log.info("IPC listener started on port %d", IPC_PORT)
            while not self._shutdown.is_set():
                try:
                    conn, _ = srv.accept()
                except _socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    data = conn.recv(1024)
                    conn.close()
                    if data:
                        msg = json.loads(data.decode())
                        cmd = msg.get("cmd")
                        if cmd == "save":
                            log.info("IPC: save command received")
                            self.trigger_save()
                        elif cmd == "pause":
                            log.info("IPC: pause command received")
                            threading.Thread(target=self._toggle_pause, daemon=True).start()
                        elif cmd == "reload":
                            log.info("IPC: reload command received")
                            threading.Thread(target=self._reload_config, daemon=True).start()
                        else:
                            log.warning("IPC: unknown command %r", cmd)
                except Exception as e:
                    log.debug("IPC listener error: %s", e)
        except OSError as e:
            log.error("IPC listener could not bind: %s", e)
        finally:
            try:
                srv.close()
            except Exception:
                pass
