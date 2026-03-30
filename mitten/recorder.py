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
import sys
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
    Detect the primary display output name.
    On Windows, uses screeninfo to get the primary monitor.
    On Linux, queries kscreen-doctor or wlr-randr.
    Returns a specific output name like 'DP-1' or '\\\\.\\DISPLAY1', or 'screen' as fallback.
    """
    if sys.platform == "win32":
        try:
            from screeninfo import get_monitors
            for m in get_monitors():
                if getattr(m, "is_primary", False):
                    return m.name or "screen"
            monitors = get_monitors()
            if monitors:
                return monitors[0].name or "screen"
        except Exception:
            pass
        return "screen"

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
            audio = "default_output"
        if audio:
            cmd += ["-a", audio.split("|")[0]]
        mic = cfg.recorder.mic_device
        if mic:
            cmd += ["-a", mic.split("|")[0]]
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
                cmd += ["-a", audio.split("|")[0]]
            mic = cfg.recorder.mic_device
            if mic:
                cmd += ["-a", mic.split("|")[0]]

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


# ── Windows ffmpeg gdigrab recorder ──────────────────────────────────────────
# Segment-based rolling buffer. ffmpeg captures the desktop via GDI and writes
# short .ts segments to TMP_DIR. On trigger, recent segments are concatenated
# into a .mp4 that ClipWatcher picks up for post-processing. No OBS required.

_WIN_SEG_SECS = 5        # each rolling segment is ~5 seconds
_WIN_CODECS = [          # tried in order; first that survives >5s is kept
    "h264_nvenc",
    "libx264 -preset ultrafast",
]

def _win_capture_geometry(config: MittenConfig) -> tuple[int, int, int, int] | None:
    """
    Return (x, y, width, height) for the monitor to capture on Windows.
    'auto' → primary monitor. A specific monitor name (e.g. '\\\\.\\DISPLAY1') → that monitor.
    Returns None if screeninfo is unavailable (caller falls back to full desktop).
    """
    try:
        from screeninfo import get_monitors
        monitors = get_monitors()
    except Exception:
        return None

    monitor_cfg = config.general.monitor
    if monitor_cfg == "auto":
        target = next((m for m in monitors if m.is_primary), monitors[0] if monitors else None)
    else:
        target = next((m for m in monitors if m.name == monitor_cfg), None)
        if target is None:
            # Name not matched — fall back to primary
            target = next((m for m in monitors if m.is_primary), monitors[0] if monitors else None)

    if target is None:
        return None
    return (target.x, target.y, target.width, target.height)


# NVENC max resolution: conservative limit for older GPUs (Maxwell/Pascal = 4096).
# Turing+ supports 8192, but we don't know the GPU generation at startup.
_NVENC_MAX_DIM = 4096


if sys.platform == "win32":
    class FfmpegWindowsRecorder:
        """
        Windows recorder backend: ffmpeg gdigrab → rolling .ts segments.
        Mirrors GpuRecorder's interface so the daemon works unchanged.
        """

        def __init__(
            self,
            config: MittenConfig,
            on_crash: Callable[[str], None] | None = None,
        ) -> None:
            self._config = config
            self._on_crash = on_crash
            self._proc: subprocess.Popen | None = None
            self._running = False
            self._lock = threading.Lock()
            self._shutdown = threading.Event()
            self._codec_idx = 0
            self._start_time = 0.0

        def _build_command(self, codec: str, use_audio: bool = True, use_loopback: bool = True) -> list[str]:
            cfg = self._config
            r = cfg.recorder
            num_segs = max(4, cfg.general.buffer_seconds // _WIN_SEG_SECS + 3)
            seg_pattern = str(TMP_DIR / "winseg_%03d.ts")

            geom = _win_capture_geometry(cfg)
            gdigrab_args: list[str] = []
            if geom:
                x, y, w, h = geom
                gdigrab_args += [
                    "-offset_x", str(x),
                    "-offset_y", str(y),
                    "-video_size", f"{w}x{h}",
                ]

            cmd = [
                "ffmpeg", "-y",
                "-f", "gdigrab",
                "-framerate", str(cfg.general.framerate),
                *gdigrab_args,
                "-i", "desktop",
            ]

            audio_raw = r.audio_device.split("|")[0].strip() if r.audio_device else ""
            if use_audio and audio_raw:
                device = "default" if audio_raw == "default" else audio_raw
                if use_loopback:
                    # WASAPI loopback: captures desktop audio output
                    cmd += ["-f", "wasapi", "-loopback", "1", "-i", device]
                else:
                    # WASAPI without loopback flag — works on some builds that
                    # don't recognise -loopback but still support render capture
                    cmd += ["-f", "wasapi", "-i", device]

            cmd += ["-c:v"] + codec.split()

            if use_audio and audio_raw:
                cmd += ["-c:a", "aac", "-b:a", "128k"]
            else:
                cmd += ["-an"]

            cmd += [
                "-f", "segment",
                "-segment_time", str(_WIN_SEG_SECS),
                "-segment_wrap", str(num_segs),
                "-reset_timestamps", "1",
                seg_pattern,
            ]
            return cmd

        def start(self, target: str = "auto") -> None:
            with self._lock:
                if self._proc and self._proc.poll() is None:
                    log.debug("Windows recorder already running")
                    return
                self._shutdown.clear()
                self._codec_idx = 0
                for f in TMP_DIR.glob("winseg_*.ts"):
                    try:
                        f.unlink()
                    except OSError:
                        pass

            # Check capture resolution — NVENC has a max dimension on older GPUs
            geom = _win_capture_geometry(self._config)
            start_codec = 0
            if geom:
                _, _, w, h = geom
                if w > _NVENC_MAX_DIM or h > _NVENC_MAX_DIM:
                    log.warning(
                        "Capture resolution %dx%d exceeds NVENC safe limit (%dpx) — "
                        "skipping NVENC, using libx264 (expect higher CPU usage)",
                        w, h, _NVENC_MAX_DIM,
                    )
                    start_codec = next(
                        (i for i, c in enumerate(_WIN_CODECS) if "nvenc" not in c),
                        0,
                    )
            else:
                log.warning(
                    "Could not detect monitor resolution — capturing full virtual desktop. "
                    "If CPU/RAM usage is high, set a specific monitor in Settings."
                )
            self._launch(start_codec)

        def _launch(self, codec_idx: int, use_audio: bool = True, use_loopback: bool = True) -> None:
            codec = _WIN_CODECS[codec_idx]
            cmd = self._build_command(codec, use_audio, use_loopback)
            log.info(
                "Starting Windows recorder (codec=%s, audio=%s, loopback=%s)",
                codec.split()[0], use_audio, use_loopback,
            )
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                )
                self._proc = proc
                self._running = True
                self._start_time = time.time()
                threading.Thread(
                    target=self._watch, args=(proc, codec_idx, use_audio, use_loopback),
                    name="win-recorder-watcher", daemon=True,
                ).start()
            except Exception as e:
                log.error("Failed to start ffmpeg recorder: %s", e)
                self._running = False
                if self._on_crash:
                    self._on_crash(str(e))

        def _watch(self, proc: subprocess.Popen, codec_idx: int, use_audio: bool = True, use_loopback: bool = True) -> None:
            # Drain stderr continuously — if we block-read it only on exit, the pipe
            # fills up, ffmpeg stalls on stderr writes, and gdigrab buffers raw frames
            # in RAM (250 MB/s at 1080p30 → GBs in seconds).
            from collections import deque
            stderr_lines: deque[bytes] = deque(maxlen=200)

            def _drain() -> None:
                if proc.stderr:
                    try:
                        for line in proc.stderr:
                            stderr_lines.append(line)
                    except Exception:
                        pass

            drain_t = threading.Thread(target=_drain, name="win-stderr-drain", daemon=True)
            drain_t.start()
            proc.wait()
            drain_t.join(timeout=2)

            if self._shutdown.is_set():
                return
            elapsed = time.time() - self._start_time
            stderr_str = b"".join(stderr_lines).decode(errors="replace")
            if stderr_str:
                tail = stderr_str[-600:].strip()
                log.error("ffmpeg stderr: %s", tail)
            if elapsed < 5.0:
                # WASAPI loopback flag not supported — retry without -loopback flag
                # (some builds support WASAPI render capture without it)
                if use_audio and use_loopback and "loopback" in stderr_str.lower():
                    log.warning("WASAPI -loopback unsupported, retrying WASAPI without loopback flag")
                    self._launch(0, use_audio=True, use_loopback=False)
                    return
                # WASAPI without loopback also failed — drop audio entirely
                if use_audio and not use_loopback and (
                    "wasapi" in stderr_str.lower() or elapsed < 1.0
                ):
                    log.warning("WASAPI audio failed, retrying without audio")
                    self._launch(0, use_audio=False, use_loopback=False)
                    return
                # Codec not supported — try next codec
                if codec_idx + 1 < len(_WIN_CODECS):
                    log.warning(
                        "Codec %s failed (%.1fs), trying %s",
                        _WIN_CODECS[codec_idx].split()[0], elapsed,
                        _WIN_CODECS[codec_idx + 1].split()[0],
                    )
                    self._launch(codec_idx + 1, use_audio, use_loopback)
                    return
            self._running = False
            log.error("ffmpeg recorder exited after %.1fs", elapsed)
            if self._on_crash:
                self._on_crash("ffmpeg recorder exited unexpectedly")

        def stop(self) -> None:
            self._shutdown.set()
            with self._lock:
                if self._proc:
                    try:
                        self._proc.terminate()
                        self._proc.wait(timeout=5)
                    except Exception:
                        try:
                            self._proc.kill()
                        except Exception:
                            pass
                    self._proc = None
            self._running = False

        def restart(self, target: str | None = None) -> None:
            self.stop()
            time.sleep(0.5)
            self.start()

        def save_replay(self) -> bool:
            """Kick off segment concat in background; returns True if started."""
            segs = sorted(TMP_DIR.glob("winseg_*.ts"), key=lambda f: f.stat().st_mtime)
            if not segs:
                log.warning("No segments for replay save")
                return False
            threading.Thread(
                target=self._do_concat, args=(list(segs),),
                name="win-concat", daemon=True,
            ).start()
            return True

        def _do_concat(self, segs: list) -> None:
            complete = segs[:-1] if len(segs) > 1 else segs
            needed = max(1, -(-self._config.general.buffer_seconds // _WIN_SEG_SECS))
            to_use = complete[-needed:]
            if not to_use:
                log.warning("No complete segments to concat")
                return
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_path = TMP_DIR / f"replay_{ts}.mp4"
            list_path = TMP_DIR / "_winseg_list.txt"
            try:
                list_path.write_text(
                    "\n".join(f"file '{p.as_posix()}'" for p in to_use),
                    encoding="utf-8",
                )
                result = subprocess.run(
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                     "-i", str(list_path), "-c", "copy", str(out_path)],
                    capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                )
                if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                    log.info("Replay saved: %s", out_path)
                else:
                    log.error(
                        "ffmpeg concat failed (rc=%d): %s",
                        result.returncode,
                        result.stderr.decode(errors="replace")[-300:],
                    )
            except Exception as e:
                log.error("save_replay concat error: %s", e)

        def is_running(self) -> bool:
            return self._running and self._proc is not None and self._proc.poll() is None

        def build_command(self, target: str | None = None) -> list[str]:
            return self._build_command(_WIN_CODECS[self._codec_idx])

        @property
        def pid(self) -> int | None:
            return self._proc.pid if self._proc else None


def make_recorder(
    config: MittenConfig,
    on_crash: Callable[[str], None] | None = None,
):
    """Factory: returns FfmpegWindowsRecorder on Windows, GpuRecorder on Linux."""
    if sys.platform == "win32":
        return FfmpegWindowsRecorder(config, on_crash)
    return GpuRecorder(config, on_crash)
