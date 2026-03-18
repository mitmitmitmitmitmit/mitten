"""
Main orchestrator: manages gpu-screen-recorder, watches for saved clips,
fires watermark post-processing, and coordinates game detection + trigger.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from .config import MittenConfig, TMP_DIR, PID_FILE
from .detect import GameDetector, GameInfo
from .recorder import GpuRecorder
from .trigger import TriggerListener
from . import notify, save

log = logging.getLogger(__name__)


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

        self._recorder = GpuRecorder(config, on_crash=self._on_recorder_crash)
        self._watcher = ClipWatcher(on_clip_ready=self._on_clip_ready)
        self._trigger = TriggerListener(config, on_trigger=self._on_trigger)
        self._detector: GameDetector | None = None

        if config.general.mode == "game" and config.game_detection.enabled:
            self._detector = GameDetector(
                config,
                on_game_start=self._on_game_start,
                on_game_stop=self._on_game_stop,
            )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        self._setup_signal_handlers()
        self._ensure_dirs()
        self._write_pid()

        log.info(
            "MITTEN starting (mode=%s, buffer=%ds, monitor=%s)",
            self._config.general.mode,
            self._config.general.buffer_seconds,
            self._config.general.monitor,
        )

        # Start clip watcher first so it's ready before any clips land
        self._watcher.start()

        # Start recording (skip in game mode — detector will start it)
        if self._config.general.mode != "game":
            try:
                self._recorder.start()
            except RuntimeError as e:
                log.error("%s", e)
                print(f"\nError: {e}\n")
                return

        # Start trigger listener
        has_input = self._trigger.start()
        if not has_input and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  MITTEN — No Input Found",
                "No input devices detected. Trigger via SIGUSR1 or the tray icon.",
                urgency="normal", icon="input-mouse", timeout_ms=5000,
            )

        # Start game detector
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

    # ------------------------------------------------------------------ #
    # Callbacks
    # ------------------------------------------------------------------ #

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

        self._recorder.save_replay()

    def _on_clip_ready(self, raw_path: Path) -> None:
        """Called by ClipWatcher when gpu-screen-recorder writes a new clip."""
        def on_success(path: Path, seconds: int) -> None:
            if self._config.notifications.on_save and self._config.notifications.enabled:
                notify.notify(
                    "~( ^.x.^)>  Mitten caught one!",
                    f"{path.name} ({seconds}s)",
                    urgency="normal", icon="emblem-videos", timeout_ms=6000,
                )

        def on_failure(reason: str) -> None:
            if self._config.notifications.on_error and self._config.notifications.enabled:
                notify.notify(
                    "~( ^.x.^)>  Mitten — Save Failed",
                    reason,
                    urgency="normal", icon="dialog-error", timeout_ms=5000,
                )

        save.process_clip(
            raw_path=raw_path,
            config=self._config,
            on_success=on_success,
            on_failure=on_failure,
        )

    def _on_recorder_crash(self, reason: str) -> None:
        log.error("Recorder crash: %s", reason)
        if self._config.notifications.on_error and self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten — Capture Error",
                reason,
                urgency="normal", icon="dialog-warning", timeout_ms=5000,
            )

    def _on_game_start(self, game: GameInfo) -> None:
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

    def _on_game_stop(self, game: GameInfo) -> None:
        log.info("Game stopped: %s", game.name)
        if self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten paused",
                f"{game.name} closed",
                urgency="low", icon="media-playback-pause", timeout_ms=3000,
            )
        self._recorder.stop()
        log.info("Game mode: capture paused. Waiting for next game...")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _ensure_dirs(self) -> None:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._config.general.save_dir.mkdir(parents=True, exist_ok=True)

    def _write_pid(self) -> None:
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(os.getpid()))
        except Exception as e:
            log.debug("Could not write PID file: %s", e)

    def _teardown(self) -> None:
        log.info("Shutting down MITTEN...")
        if self._config.notifications.enabled:
            notify.notify(
                "~( ^.x.^)>  Mitten stopped",
                "Recording has ended",
                urgency="low", icon="media-playback-stop", timeout_ms=3000,
            )
        self._trigger.stop()
        if self._detector:
            self._detector.stop()
        self._watcher.stop()
        self._recorder.stop()
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        log.info("MITTEN stopped.")

    def _setup_signal_handlers(self) -> None:
        def _handle_shutdown(signum, frame):
            log.info("Received signal %d, shutting down...", signum)
            self._shutdown.set()

        def _handle_save(signum, frame):
            log.info("Received SIGUSR1 — triggering save")
            self.trigger_save()

        signal.signal(signal.SIGINT, _handle_shutdown)
        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGUSR1, _handle_save)
