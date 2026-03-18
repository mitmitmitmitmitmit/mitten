"""
Watermark post-processing: burns the watermark into a raw clip saved by
gpu-screen-recorder, then moves the result to the save directory.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import MittenConfig

log = logging.getLogger(__name__)

# Only one watermark job runs at a time to avoid GPU/CPU contention
_save_semaphore = threading.Semaphore(1)


def process_clip(
    raw_path: Path,
    config: MittenConfig,
    on_success: Callable[[Path, int], None] | None = None,
    on_failure: Callable[[str], None] | None = None,
) -> threading.Thread:
    """
    Spawn a background thread to watermark `raw_path` and move it to save_dir.
    Returns the thread (already started).
    """
    t = threading.Thread(
        target=_worker,
        args=(raw_path, config, on_success, on_failure),
        name="save-worker",
        daemon=True,
    )
    t.start()
    return t


def _worker(
    raw_path: Path,
    config: MittenConfig,
    on_success: Callable | None,
    on_failure: Callable | None,
) -> None:
    acquired = _save_semaphore.acquire(timeout=60.0)
    if not acquired:
        msg = "Watermark job timed out waiting for semaphore"
        log.warning(msg)
        if on_failure:
            on_failure(msg)
        return
    try:
        _do_process(raw_path, config, on_success, on_failure)
    finally:
        _save_semaphore.release()


def _do_process(
    raw_path: Path,
    config: MittenConfig,
    on_success: Callable | None,
    on_failure: Callable | None,
) -> None:
    start_time = time.monotonic()
    if not raw_path.exists() or raw_path.stat().st_size == 0:
        msg = f"Raw clip missing or empty: {raw_path.name}"
        log.warning(msg)
        if on_failure:
            on_failure(msg)
        return

    save_dir: Path = config.general.save_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"mitten_{timestamp}.mp4"
    output_path = save_dir / filename

    # Probe clip duration for the notification
    actual_seconds = _probe_duration(raw_path)

    wm = config.watermark
    if not wm.enabled:
        try:
            shutil.move(str(raw_path), str(output_path))
            log.info("Clip saved (no watermark): %s", filename)
            try:
                from .metrics import ClipMetric, log_clip_metric
                size_mb = output_path.stat().st_size / (1024 * 1024)
                log_clip_metric(ClipMetric(
                    timestamp=time.time(),
                    save_duration_sec=time.monotonic() - start_time,
                    compressed=False,
                    original_size_mb=size_mb,
                    final_size_mb=size_mb,
                ))
            except Exception:
                pass
            if on_success:
                on_success(output_path, actual_seconds)
        except Exception as e:
            msg = f"Failed to move clip: {e}"
            log.error(msg)
            if on_failure:
                on_failure(msg)
        return

    codec = config.recorder.output_codec
    cq = config.recorder.watermark_cq
    cmd = _build_watermark_cmd(raw_path, output_path, wm, codec=codec, wm_config_cq=cq)
    log.info("Watermarking clip: %s (%ds)", filename, actual_seconds)
    log.debug("ffmpeg cmd: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            log.warning(
                "%s_nvenc failed (code %d), retrying with software encoder: %s",
                codec, result.returncode, stderr[-200:],
            )
            # Fallback to software encoder
            cmd_sw = _build_watermark_cmd(raw_path, output_path, wm, software=True, codec=codec, wm_config_cq=cq)
            result = subprocess.run(cmd_sw, capture_output=True, timeout=180)

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            msg = f"ffmpeg watermark failed (code {result.returncode}): {stderr[-300:]}"
            log.error(msg)
            output_path.unlink(missing_ok=True)
            if on_failure:
                on_failure(f"ffmpeg error: {stderr[-200:]}")
            return

        raw_path.unlink(missing_ok=True)

        # Size check: if over 10MB, re-encode harder until it fits (max 2 attempts)
        size_mb = output_path.stat().st_size / (1024 * 1024)
        original_size_mb = size_mb
        did_compress = False
        if size_mb > 10.0:
            from . import notify
            notify.notify(
                "~( ^.x.^)>  Mitten",
                f"Clip is {size_mb:.1f}MB — compressing to fit under 10MB...",
                urgency="low", icon="media-record", timeout_ms=4000,
            )
            log.info("Clip is %.1fMB, compressing harder...", size_mb)

            did_compress = True
            compressed = _compress_to_target(output_path, codec, cq, attempt=1)
            if compressed:
                size_mb2 = output_path.stat().st_size / (1024 * 1024)
                if size_mb2 > 10.0:
                    log.info("Still %.1fMB after first pass, compressing harder...", size_mb2)
                    _compress_to_target(output_path, codec, cq, attempt=2)

        log.info("Clip saved: %s", output_path)
        try:
            from .metrics import ClipMetric, log_clip_metric
            final_size_mb = output_path.stat().st_size / (1024 * 1024)
            log_clip_metric(ClipMetric(
                timestamp=time.time(),
                save_duration_sec=time.monotonic() - start_time,
                compressed=did_compress,
                original_size_mb=original_size_mb,
                final_size_mb=final_size_mb,
            ))
        except Exception:
            pass
        if on_success:
            on_success(output_path, actual_seconds)

    except subprocess.TimeoutExpired:
        msg = "ffmpeg timed out during watermarking"
        log.error(msg)
        output_path.unlink(missing_ok=True)
        if on_failure:
            on_failure(msg)
    except Exception as e:
        msg = f"Unexpected error during watermarking: {e}"
        log.exception(msg)
        output_path.unlink(missing_ok=True)
        if on_failure:
            on_failure(str(e))


def _build_watermark_cmd(
    input_path: Path,
    output_path: Path,
    wm,
    software: bool = False,
    codec: str = "hevc",
    wm_config_cq: int = 26,
) -> list[str]:
    vf = _drawtext_filter(wm.text, wm.subtext, wm.fontsize, wm.fontcolor, wm.position, wm.padding)

    cq = str(wm_config_cq)
    if software:
        encoder = ["-c:v", "libx265", "-preset", "fast", "-crf", str(min(51, wm_config_cq + 4))]
    elif codec == "hevc":
        encoder = ["-c:v", "hevc_nvenc", "-preset", "p4", "-cq", cq]
    else:
        encoder = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", cq]

    return [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        *encoder,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]


def _drawtext_filter(
    text: str,
    subtext: str,
    fontsize: int,
    fontcolor: str,
    position: str,
    padding: int,
) -> str:
    """Build a two-line ffmpeg drawtext filter string."""
    p = padding
    subsize = max(10, fontsize - 6)
    subcolor = "white@0.35"
    shadow = ":shadowcolor=black@0.7:shadowx=2:shadowy=2"
    subshadow = ":shadowcolor=black@0.5:shadowx=1:shadowy=1"
    line_gap = subsize + 6

    if position == "bottom_right":
        main_x, sub_x = f"W-tw-{p}", f"W-tw-{p}"
        main_y, sub_y = f"H-th-{p + line_gap}", f"H-th-{p}"
    elif position == "bottom_left":
        main_x, sub_x = str(p), str(p)
        main_y, sub_y = f"H-th-{p + line_gap}", f"H-th-{p}"
    elif position == "top_right":
        main_x, sub_x = f"W-tw-{p}", f"W-tw-{p}"
        main_y, sub_y = str(p), f"{p + fontsize + 6}"
    else:  # top_left
        main_x, sub_x = str(p), str(p)
        main_y, sub_y = str(p), f"{p + fontsize + 6}"

    safe_main = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    safe_sub  = subtext.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")

    main_filter = (
        f"drawtext=text='{safe_main}'"
        f":fontsize={fontsize}:fontcolor={fontcolor}"
        f":x={main_x}:y={main_y}" + shadow
    )
    sub_filter = (
        f"drawtext=text='{safe_sub}'"
        f":fontsize={subsize}:fontcolor={subcolor}"
        f":x={sub_x}:y={sub_y}" + subshadow
    )
    return f"{main_filter},{sub_filter}"


def _compress_to_target(path: Path, codec: str, base_cq: int, attempt: int) -> bool:
    """
    Re-encode `path` in-place with increased CQ to reduce file size.
    attempt=1: CQ + 8  (moderate compression boost)
    attempt=2: CQ + 16 (aggressive)
    Returns True on success.
    """
    new_cq = min(51, base_cq + (8 * attempt))
    tmp = path.with_suffix(".tmp.mp4")

    if codec == "hevc":
        encoder = ["-c:v", "hevc_nvenc", "-preset", "p4", "-cq", str(new_cq)]
    else:
        encoder = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(new_cq)]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(path),
        *encoder,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(path)
            log.info("Recompressed to %.1fMB (CQ %d)", path.stat().st_size / (1024*1024), new_cq)
            return True
    except Exception as e:
        log.warning("Compression attempt %d failed: %s", attempt, e)
    finally:
        tmp.unlink(missing_ok=True)
    return False


def _probe_duration(path: Path) -> int:
    """Return clip duration in seconds via ffprobe, or 0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return max(0, int(float(result.stdout.strip())))
    except Exception:
        return 0
