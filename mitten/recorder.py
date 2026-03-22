"""
gpu-screen-recorder subprocess management.
Replaces capture.py + buffer.py — the replay buffer lives in gpu-screen-recorder.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .config import MittenConfig, TMP_DIR

log = logging.getLogger(__name__)

_MAX_CRASHES = 3
_CRASH_WINDOW = 30.0  # seconds


def detect_monitor() -> str:
    """
    Detect the primary Wayland output name.
    Returns a specific output name like 'DP-1', or 'screen' as fallback.
    """
    # Try kscreen-doctor (KDE Plasma, most reliable here)
    try:
        out = subprocess.check_output(
            ["kscreen-doctor", "--outputs"],
            text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
        # Lines look like: "Output: 1 DP-1 enabled connected priority 1"
        for line in out.splitlines():
            parts = line.split()
            if "Output:" in parts and "enabled" in parts and "priority" in parts:
                try:
                    prio_idx = parts.index("priority")
                    if parts[prio_idx + 1] == "1":
                        name_idx = parts.index("Output:") + 2
                        return parts[name_idx]
                except (IndexError, ValueError):
                    continue
    except Exception:
        pass

    # Fallback: wlr-randr
    try:
        out = subprocess.check_output(
            ["wlr-randr"], text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
        current = None
        for line in out.splitlines():
            if not line.startswith((" ", "\t")):
                current = line.split()[0]
            if "Enabled: yes" in line and current:
                return current
    except Exception:
        pass

    log.warning("Could not detect primary monitor — using 'screen' (all monitors)")
    return "screen"


class GpuRecorder:
    """
    Manages a gpu-screen-recorder subprocess.
    On trigger, sends SIGUSR1 which makes gpu-screen-recorder write a replay
    file to tmp_dir. ClipWatcher in daemon.py picks that up for post-processing.
    """

    def __init__(
        self,
        config: MittenConfig,
        on_crash: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._on_crash = on_crash
        self._proc: subprocess.Popen | None = None
        self._target: str = "auto"
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._watcher: threading.Thread | None = None
        self._crash_times: list[float] = []

    def start(self, target: str = "auto") -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                log.debug("Recorder already running, ignoring start()")
                return
            self._target = target
            self._shutdown.clear()
            self._launch()

    def stop(self) -> None:
        self._shutdown.set()
        with self._lock:
            self._terminate()

    def restart(self, target: str | None = None) -> None:
        with self._lock:
            if target is not None:
                self._target = target
            self._terminate()
            if not self._shutdown.is_set():
                self._launch()

    def save_replay(self) -> bool:
        """Send SIGUSR1 to gpu-screen-recorder to trigger a replay save."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                log.warning("Cannot save replay — recorder not running")
                return False
            try:
                os.kill(proc.pid, signal.SIGUSR1)
                log.info("Replay save triggered (SIGUSR1 → pid %d)", proc.pid)
                return True
            except ProcessLookupError:
                log.warning("Recorder process gone before SIGUSR1 could be sent")
                return False

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def build_command(self, target: str | None = None) -> list[str]:
        """Return the gpu-screen-recorder command list (for --dry-run)."""
        t = self._resolve_target(target or self._target)
        return self._make_cmd(t)

    def _resolve_target(self, target: str) -> str:
        if target != "auto":
            return target
        mode = self._config.general.mode
        monitor = self._config.general.monitor
        if mode == "window":
            return "focused"
        if monitor == "auto":
            return detect_monitor()
        return monitor

    def _make_cmd(self, resolved_target: str) -> list[str]:
        cfg = self._config
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [
            "gpu-screen-recorder",
            "-w", resolved_target,
            "-f", str(cfg.general.framerate),
            "-r", str(cfg.general.buffer_seconds),
            "-q", cfg.recorder.quality,
            "-k", cfg.recorder.capture_codec,
            "-c", cfg.recorder.container,
            "-o", str(TMP_DIR),
        ]
        audio = cfg.recorder.audio_device
        if audio == "default":
            audio = "default_output"  # gpu-screen-recorder token for system audio
        if audio:
            cmd += ["-a", audio]
        return cmd

    def _launch(self) -> None:
        """Start gpu-screen-recorder. Must be called with self._lock held."""
        if not shutil.which("gpu-screen-recorder"):
            aur = shutil.which("yay") and "yay" or shutil.which("paru") and "paru" or "yay"
            raise RuntimeError(
                "gpu-screen-recorder not found. Install it:\n"
                f"  {aur} -S gpu-screen-recorder"
            )

        resolved = self._resolve_target(self._target)
        cmd = self._make_cmd(resolved)
        log.info("Starting recorder: %s", " ".join(cmd))

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        self._watcher = threading.Thread(
            target=self._watch_proc,
            name="recorder-watcher",
            daemon=True,
        )
        self._watcher.start()

    def _terminate(self) -> None:
        """Stop the current process. Must be called with self._lock held."""
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception as e:
            log.debug("Error stopping recorder: %s", e)

    def _watch_proc(self) -> None:
        """Wait for the process to exit; auto-restart unless shutting down."""
        proc = self._proc
        if proc is None:
            return

        stderr_bytes = b""
        try:
            _, stderr_bytes = proc.communicate()
        except Exception:
            pass

        rc = proc.returncode
        if self._shutdown.is_set():
            return

        stderr_tail = (stderr_bytes or b"").decode(errors="replace").strip()[-300:]
        log.error("Recorder exited (code %d): %s", rc, stderr_tail)

        now = time.monotonic()
        self._crash_times = [t for t in self._crash_times if now - t < _CRASH_WINDOW]
        self._crash_times.append(now)

        if len(self._crash_times) > _MAX_CRASHES:
            reason = f"Recorder crashed {_MAX_CRASHES + 1} times in {_CRASH_WINDOW}s — giving up"
            log.error(reason)
            if self._on_crash:
                self._on_crash(reason)
            return

        log.info("Restarting recorder in 2s...")
        time.sleep(2)
        if not self._shutdown.is_set():
            with self._lock:
                if not self._shutdown.is_set():
                    self._launch()


class SessionRecorder:
    """
    Full session recorder — no replay buffer, records directly to an output file.
    Started by triple-click; stopped by triple-click again.
    Uses gpu-screen-recorder without -r flag.
    """

    def __init__(self, config: MittenConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._lock = threading.Lock()

    def is_recording(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self, output_path: Path) -> None:
        """Start full session recording to output_path."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                log.warning("Session recorder already running")
                return
            cfg = self._config
            r = cfg.recorder
            g = cfg.general

            monitor = cfg.general.monitor
            if monitor == "auto":
                monitor = detect_monitor()

            audio = cfg.recorder.audio_device
            if audio == "default":
                audio = "default_output"

            cmd = [
                "gpu-screen-recorder",
                "-w", monitor,
                "-f", str(g.framerate),
                "-q", r.quality,
                "-k", r.capture_codec,
                "-c", "mp4",
                "-o", str(output_path),
            ]
            if audio:
                cmd += ["-a", audio]

            self._output_path = output_path
            log.info("Starting session recorder: %s", " ".join(cmd))
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

    def stop(self) -> Path | None:
        """Stop recording. Returns the output path if successful, None otherwise."""
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                log.warning("Session recorder not running")
                return None
            try:
                self._proc.send_signal(signal.SIGINT)
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception as e:
                log.error("Session stop error: %s", e)
            path = self._output_path
            self._proc = None
            self._output_path = None
            if path and path.exists() and path.stat().st_size > 0:
                log.info("Session recording saved: %s", path)
                return path
            log.warning("Session output missing or empty: %s", path)
            return None
