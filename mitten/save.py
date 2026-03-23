"""
Watermark post-processing: burns the watermark into a raw clip saved by
gpu-screen-recorder, then moves the result to the save directory.

When auto_compress is enabled, watermarking and compression happen in a single
ffmpeg pass (no generation loss). Primary encoder is NVENC VBR; libx264 two-pass
is the fallback.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import MittenConfig, TMP_DIR
from . import notify as _notify
from .errors import (fmt as _efmt, E_SAVE_SEMAPHORE, E_SAVE_MISSING, E_SAVE_FFMPEG_WM,
                     E_SAVE_FFMPEG_ENCODE, E_SAVE_MOVE, E_SAVE_FFMPEG_DUAL_HEVC,
                     E_SAVE_FFMPEG_DUAL_H264)

log = logging.getLogger(__name__)

# Only one watermark job runs at a time to avoid GPU/CPU contention
_save_semaphore = threading.Semaphore(1)


def process_clip(
    raw_path: Path,
    config: MittenConfig,
    on_success: Callable[[Path, int], None] | None = None,
    on_failure: Callable[[str], None] | None = None,
    meta: dict | None = None,
) -> threading.Thread:
    """
    Spawn a background thread to watermark `raw_path` and move it to save_dir.
    Returns the thread (already started).
    """
    t = threading.Thread(
        target=_worker,
        args=(raw_path, config, on_success, on_failure, meta or {}),
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
    meta: dict,
) -> None:
    acquired = _save_semaphore.acquire(timeout=60.0)
    if not acquired:
        msg = _efmt(E_SAVE_SEMAPHORE, "Save job timed out waiting for semaphore")
        log.warning(msg)
        if on_failure:
            on_failure(msg)
        return
    try:
        _do_process(raw_path, config, on_success, on_failure, meta)
    finally:
        _save_semaphore.release()


def _write_meta(output_path: Path, base_meta: dict, duration_s: int, config: MittenConfig,
                watermarked: bool, compressed: bool) -> None:
    """Write a JSON sidecar next to the clip. Never raises."""
    try:
        from importlib.metadata import version as _pkg_version
        mitten_ver = _pkg_version("mitten")
    except Exception:
        mitten_ver = "unknown"
    try:
        full = {
            **base_meta,
            "duration_s": duration_s,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "mitten_version": mitten_ver,
            "mode": getattr(config.general, "mode", "desktop"),
            "watermarked": watermarked,
            "compressed": compressed,
            "codec": getattr(config.recorder, "output_codec", "h264"),
            "size_mb": round(output_path.stat().st_size / (1024 * 1024), 2),
        }
        output_path.with_suffix(".json").write_text(json.dumps(full, indent=2))
    except Exception:
        pass


def _do_process(
    raw_path: Path,
    config: MittenConfig,
    on_success: Callable | None,
    on_failure: Callable | None,
    meta: dict,
) -> None:
    start_time = time.monotonic()
    if not raw_path.exists() or raw_path.stat().st_size == 0:
        msg = _efmt(E_SAVE_MISSING, f"Raw clip missing or empty: {raw_path.name}")
        log.warning(msg)
        if on_failure:
            on_failure(msg)
        return

    save_dir: Path = config.general.save_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    filename = f"mitten_{timestamp}.mp4"
    output_path = save_dir / filename
    # Collision guard — microseconds make this nearly impossible, but guard anyway
    counter = 1
    while output_path.exists():
        output_path = save_dir / f"mitten_{timestamp}_{counter:03d}.mp4"
        counter += 1

    # Probe clip duration for notifications and bitrate math
    actual_seconds = _probe_duration(raw_path)

    wm = config.watermark
    auto_compress = config.recorder.auto_compress
    target_mb = config.recorder.compression_target_mb
    light_mode = _is_light_mode(config)

    # ── Intro animation path (always takes priority when enabled) ────────────
    if wm.anim_enabled:
        log.info("Encoding clip with intro animation (%s): %s (%ds)",
                 getattr(wm, "anim_style", "Snap"), filename, actual_seconds)
        target = target_mb if auto_compress and actual_seconds > 0 else None
        success = _encode_with_intro(raw_path, output_path, wm, target,
                                     actual_seconds, light_mode=light_mode)
        if not success:
            output_path.unlink(missing_ok=True)
            if on_failure:
                on_failure(_efmt(E_SAVE_FFMPEG_ENCODE, "Intro encode failed — check logs"))
            return
        raw_path.unlink(missing_ok=True)
        if auto_compress and target_mb is not None:
            post_mb = output_path.stat().st_size / (1024 * 1024)
            if post_mb > target_mb:
                _notify.notify(
                    "~( ^.x.^)>  Mitten",
                    f"clip saved but still {post_mb:.1f}MB (target was {target_mb}MB)",
                    urgency="normal", icon="dialog-information", timeout_ms=5000,
                )
        log.info("Clip saved (intro): %s", output_path.name)
        _record_metric(start_time, output_path, auto_compress and actual_seconds > 0, None)
        _write_meta(output_path, meta, actual_seconds, config,
                    watermarked=wm.enabled, compressed=auto_compress and actual_seconds > 0)
        if on_success:
            on_success(output_path, actual_seconds)
        return

    # ── No watermark path ────────────────────────────────────────────
    if not wm.enabled:
        if auto_compress and actual_seconds > 0:
            log.info("Encoding clip (no watermark, targeted): %s (%ds)", filename, actual_seconds)
            success = _encode_targeted(raw_path, output_path, None, target_mb, actual_seconds, light_mode=light_mode)
        else:
            try:
                shutil.move(str(raw_path), str(output_path))
                success = True
            except OSError as e:
                success = False
                msg = _efmt(E_SAVE_MOVE, f"Failed to move clip: {e}")
                log.error(msg)
                if on_failure:
                    on_failure(msg)
                return

        if success:
            log.info("Clip saved (no watermark): %s", filename)
            _record_metric(start_time, output_path, auto_compress, raw_path)
            _write_meta(output_path, meta, actual_seconds, config, watermarked=False, compressed=auto_compress)
            if on_success:
                on_success(output_path, actual_seconds)
        else:
            output_path.unlink(missing_ok=True)
            if on_failure:
                on_failure(_efmt(E_SAVE_FFMPEG_ENCODE, "Encode failed — check journal for details"))
        return

    # ── Watermark path ────────────────────────────────────────────────
    if auto_compress and actual_seconds > 0:
        # Single pass: watermark + bitrate-targeted encode — no generation loss
        log.info("Encoding clip (watermark + compress): %s (%ds, target %dMB)",
                 filename, actual_seconds, target_mb)
        success = _encode_targeted(raw_path, output_path, wm, target_mb, actual_seconds, light_mode=light_mode)

        if not success:
            output_path.unlink(missing_ok=True)
            if on_failure:
                on_failure(_efmt(E_SAVE_FFMPEG_ENCODE, "Encode failed — check journal for details"))
            return

        raw_path.unlink(missing_ok=True)

        # Notify if still over target (shouldn't normally happen)
        post_mb = output_path.stat().st_size / (1024 * 1024)
        if post_mb > target_mb:
            _notify.notify(
                "~( ^.x.^)>  Mitten",
                f"clip saved but still {post_mb:.1f}MB (target was {target_mb}MB)",
                urgency="normal", icon="dialog-information", timeout_ms=5000,
            )

        log.info("Clip saved: %s (%.1fMB)", output_path.name, post_mb)
        _record_metric(start_time, output_path, True, None)
        _write_meta(output_path, meta, actual_seconds, config, watermarked=True, compressed=True)
        if on_success:
            on_success(output_path, actual_seconds)
        return

    # ── CQ watermark only (auto_compress disabled) ────────────────────
    codec = config.recorder.output_codec
    cq = config.recorder.watermark_cq

    # Dual encode: HEVC watermark pass → H.264 transcode (smaller output, Discord compatible)
    if codec == "h264+hevc":
        hevc_tmp = output_path.with_suffix(".hevc_tmp.mp4")
        log.info("Dual-encode pass 1/2 (HEVC watermark): %s", filename)
        cmd_hevc = _build_watermark_cmd(raw_path, hevc_tmp, wm, codec="hevc", wm_config_cq=cq, light_mode=light_mode)
        try:
            r1 = subprocess.run(cmd_hevc, capture_output=True, timeout=180)
            if r1.returncode != 0:
                log.warning("HEVC pass failed, retrying software: %s", r1.stderr.decode(errors="replace")[-200:])
                cmd_hevc_sw = _build_watermark_cmd(raw_path, hevc_tmp, wm, software=True, codec="hevc", wm_config_cq=cq, light_mode=light_mode)
                r1 = subprocess.run(cmd_hevc_sw, capture_output=True, timeout=240)
            if r1.returncode != 0:
                stderr = r1.stderr.decode(errors="replace").strip()
                log.error("HEVC watermark pass failed: %s", stderr[-300:])
                hevc_tmp.unlink(missing_ok=True)
                if on_failure:
                    on_failure(_efmt(E_SAVE_FFMPEG_DUAL_HEVC, f"ffmpeg HEVC pass failed: {stderr[-200:]}"))
                return

            log.info("Dual-encode pass 2/2 (H.264 transcode): %s", filename)
            cmd_h264 = [
                "ffmpeg", "-y", "-i", str(hevc_tmp),
                "-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(cq),
                "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
                str(output_path),
            ]
            r2 = subprocess.run(cmd_h264, capture_output=True, timeout=180)
            if r2.returncode != 0:
                log.warning("H.264 NVENC transcode failed, retrying libx264: %s", r2.stderr.decode(errors="replace")[-200:])
                cmd_h264_sw = [
                    "ffmpeg", "-y", "-i", str(hevc_tmp),
                    "-c:v", "libx264", "-preset", "fast", "-crf", str(min(51, cq + 2)),
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
                    str(output_path),
                ]
                r2 = subprocess.run(cmd_h264_sw, capture_output=True, timeout=240)
            hevc_tmp.unlink(missing_ok=True)
            if r2.returncode != 0:
                stderr = r2.stderr.decode(errors="replace").strip()
                log.error("H.264 transcode pass failed: %s", stderr[-300:])
                output_path.unlink(missing_ok=True)
                if on_failure:
                    on_failure(_efmt(E_SAVE_FFMPEG_DUAL_H264, f"ffmpeg H.264 transcode failed: {stderr[-200:]}"))
                return

            raw_path.unlink(missing_ok=True)
            log.info("Clip saved (h264+hevc dual): %s", output_path)
            _record_metric(start_time, output_path, False, None)
            _write_meta(output_path, meta, actual_seconds, config, watermarked=True, compressed=False)
            if on_success:
                on_success(output_path, actual_seconds)
        except subprocess.TimeoutExpired:
            hevc_tmp.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            log.error("ffmpeg dual-encode timed out")
            if on_failure:
                on_failure(_efmt(E_SAVE_FFMPEG_DUAL_H264, "ffmpeg dual-encode timed out"))
        return

    cmd = _build_watermark_cmd(raw_path, output_path, wm, codec=codec, wm_config_cq=cq, light_mode=light_mode)
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
            cmd_sw = _build_watermark_cmd(raw_path, output_path, wm, software=True, codec=codec, wm_config_cq=cq, light_mode=light_mode)
            result = subprocess.run(cmd_sw, capture_output=True, timeout=180)

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            msg = f"ffmpeg watermark failed (code {result.returncode}): {stderr[-300:]}"
            log.error(msg)
            output_path.unlink(missing_ok=True)
            if on_failure:
                on_failure(_efmt(E_SAVE_FFMPEG_WM, f"ffmpeg error: {stderr[-200:]}"))
            return

        raw_path.unlink(missing_ok=True)

        log.info("Clip saved: %s", output_path)
        _record_metric(start_time, output_path, False, None)
        _write_meta(output_path, meta, actual_seconds, config, watermarked=True, compressed=False)
        if on_success:
            on_success(output_path, actual_seconds)

    except subprocess.TimeoutExpired:
        msg = "ffmpeg timed out during watermarking"
        log.error(msg)
        output_path.unlink(missing_ok=True)
        if on_failure:
            on_failure(msg)
    except OSError as e:
        msg = f"Unexpected OS error during watermarking: {e}"
        log.error(msg)
        output_path.unlink(missing_ok=True)
        if on_failure:
            on_failure(str(e))


# ── Targeted encode (watermark + compress in one pass) ────────────────────────


def _encode_targeted(
    input_path: Path,
    output_path: Path,
    wm,
    target_mb: int,
    duration_sec: int,
    light_mode: bool = False,
) -> bool:
    """
    Encode to a bitrate-targeted output. Optionally burns watermark in the same pass.
    Primary: NVENC VBR (GPU, fast). Fallback: libx264 two-pass (accurate).
    Auto-downscales to 720p for clips longer than 90 seconds.
    """
    video_kbps = max(200, int((target_mb * 8 * 1024 * 0.95) / max(1, duration_sec)) - 96)

    vf_parts: list[str] = []
    if duration_sec > 90:
        vf_parts.append("scale=-2:720")
    if wm is not None:
        vf_parts.append(_drawtext_filter(
            wm.text, wm.subtext, wm.fontsize, wm.fontcolor, wm.position, wm.padding
        ))
    if light_mode:
        vf_parts.append(_light_mode_shame_filter())
    vf_parts.append("hqdn3d=1.5:1.5:6:6")
    vf = ",".join(vf_parts)

    log.debug("Targeted encode: %dkbps, vf=%s", video_kbps, vf[:80])

    success = _run_nvenc_vbr(input_path, output_path, video_kbps, vf)
    if not success:
        log.info("NVENC VBR failed, falling back to libx264 two-pass")
        success = _run_x264_twopass(input_path, output_path, video_kbps, vf)
    return success


def _run_nvenc_vbr(
    input_path: Path, output_path: Path, video_kbps: int, vf: str
) -> bool:
    """NVENC VBR encode — hardware accelerated, H.264 output."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "h264_nvenc",
        "-rc", "vbr",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps * 2}k",
        "-bufsize", f"{video_kbps * 4}k",
        "-preset", "p5",
        "-tune", "hq",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return True
        stderr = result.stderr.decode(errors="replace").strip()
        log.debug("NVENC VBR failed (code %d): %s", result.returncode, stderr[-200:])
        output_path.unlink(missing_ok=True)
        return False
    except subprocess.TimeoutExpired:
        log.warning("NVENC VBR timed out")
        output_path.unlink(missing_ok=True)
        return False
    except OSError as e:
        log.warning("NVENC VBR OS error: %s", e)
        output_path.unlink(missing_ok=True)
        return False


def _run_x264_twopass(
    input_path: Path, output_path: Path, video_kbps: int, vf: str
) -> bool:
    """libx264 two-pass encode — accurate bitrate targeting, Discord/browser H.264 output."""
    TMP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    passlog = str(TMP_DIR / "ffmpeg2pass")

    cmd1 = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-b:v", f"{video_kbps}k",
        "-pass", "1",
        "-passlogfile", passlog,
        "-an", "-f", "null", "/dev/null",
    ]
    cmd2 = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-b:v", f"{video_kbps}k",
        "-pass", "2",
        "-passlogfile", passlog,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        r1 = subprocess.run(cmd1, capture_output=True, timeout=300)
        if r1.returncode != 0:
            log.warning("x264 pass 1 failed (code %d): %s",
                        r1.returncode, r1.stderr.decode(errors="replace")[-200:])
            return False
        r2 = subprocess.run(cmd2, capture_output=True, timeout=300)
        if r2.returncode != 0:
            log.warning("x264 pass 2 failed (code %d): %s",
                        r2.returncode, r2.stderr.decode(errors="replace")[-200:])
            output_path.unlink(missing_ok=True)
            return False
        return output_path.exists() and output_path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        log.warning("x264 two-pass timed out")
        output_path.unlink(missing_ok=True)
        return False
    except OSError as e:
        log.warning("x264 two-pass OS error: %s", e)
        output_path.unlink(missing_ok=True)
        return False
    finally:
        for suffix in ("-0.log", "-0.log.mbtree"):
            Path(passlog + suffix).unlink(missing_ok=True)


# ── CQ watermark-only encode ──────────────────────────────────────────────────


def _build_watermark_cmd(
    input_path: Path,
    output_path: Path,
    wm,
    software: bool = False,
    codec: str = "hevc",
    wm_config_cq: int = 26,
    light_mode: bool = False,
) -> list[str]:
    vf = _drawtext_filter(wm.text, wm.subtext, wm.fontsize, wm.fontcolor, wm.position, wm.padding)
    if light_mode:
        vf = vf + "," + _light_mode_shame_filter()

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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _drawtext_filter(
    text: str,
    subtext: str,
    fontsize: int,
    fontcolor: str,
    position: str,
    padding: int,
) -> str:
    """Build a two-line ffmpeg drawtext filter string."""
    # Security: strip control characters and shell-special chars (Phase 11)
    text    = re.sub(r'[\x00-\x1f\x7f`$]', '', text)
    subtext = re.sub(r'[\x00-\x1f\x7f`$]', '', subtext)

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


def _light_mode_shame_filter() -> str:
    """Barely-visible shame watermark burned into every clip saved in light mode.
    Bottom-left corner, low opacity — it's there. they can't remove it."""
    text = "this user is a freak that uses light mode"
    safe = text.replace("'", "\\'").replace(":", "\\:")
    return (
        f"drawtext=text='{safe}'"
        f":fontsize=11"
        f":fontcolor=white@0.18"
        f":x=8:y=H-th-8"
        f":shadowcolor=black@0.3:shadowx=1:shadowy=1"
    )


def _is_light_mode(config) -> bool:
    """Check if the saved theme is Light."""
    try:
        return getattr(config.general, "theme", "") == "Light"
    except Exception:
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


def _probe_video_info(path: Path) -> tuple[int, int, str, bool]:
    """Return (width, height, fps_str, has_audio) via ffprobe. Returns (0,0,'30',False) on failure."""
    import json as _json
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams",
                "-print_format", "json",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return 0, 0, "30", False
        data = _json.loads(result.stdout)
        streams = data.get("streams", [])
        width = height = 0
        fps = "30"
        has_audio = False
        for s in streams:
            if s.get("codec_type") == "video" and width == 0:
                width  = int(s.get("width",  0))
                height = int(s.get("height", 0))
                r_fr   = s.get("r_frame_rate", "30/1")
                if "/" in r_fr:
                    num, den = r_fr.split("/", 1)
                    if int(den) > 0:
                        fps = r_fr
            elif s.get("codec_type") == "audio":
                has_audio = True
        return width, height, fps, has_audio
    except Exception as e:
        log.debug("_probe_video_info failed: %s", e)
        return 0, 0, "30", False


def _encode_with_intro(
    input_path: Path,
    output_path: Path,
    wm,
    target_mb: int | None,
    duration_sec: int,
    light_mode: bool = False,
) -> bool:
    """
    Encode the clip with a prepended MITTEN intro animation via filter_complex.
    target_mb=None → CQ encode (no bitrate targeting).
    Falls back to libx264 if NVENC fails.
    """
    from .intro import build_intro_filter_complex, INTRO_DUR

    width, height, fps, has_audio = _probe_video_info(input_path)
    if width == 0 or height == 0:
        log.warning("_encode_with_intro: ffprobe gave no dimensions, skipping intro")
        if target_mb is not None and duration_sec > 0:
            return _encode_targeted(input_path, output_path, wm, target_mb, duration_sec, light_mode)
        return False

    scale_720 = duration_sec > 90 and target_mb is not None
    style = getattr(wm, "anim_style", "Snap") if wm is not None else "Snap"

    try:
        fc, extra_args = build_intro_filter_complex(
            width, height, fps, wm, light_mode, has_audio, scale_720, style
        )
    except Exception as e:
        log.warning("_encode_with_intro: filter_complex build failed: %s", e)
        if target_mb is not None and duration_sec > 0:
            return _encode_targeted(input_path, output_path, wm, target_mb, duration_sec, light_mode)
        return False

    total_dur = duration_sec + INTRO_DUR

    if target_mb is not None:
        video_kbps = max(200, int((target_mb * 8 * 1024 * 0.95) / max(1, total_dur)) - 96)
        nvenc_args = [
            "-c:v", "h264_nvenc",
            "-rc", "vbr",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{video_kbps * 2}k",
            "-bufsize", f"{video_kbps * 4}k",
            "-preset", "p5", "-tune", "hq",
        ]
        sw_args = [
            "-c:v", "libx264",
            "-b:v", f"{video_kbps}k",
            "-preset", "fast",
        ]
    else:
        nvenc_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"]
        sw_args    = ["-c:v", "libx264", "-crf", "26", "-preset", "fast"]

    common_tail = ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)]

    def _run(encoder_args: list[str], timeout: int) -> bool:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-filter_complex", fc,
            *extra_args,
            *encoder_args,
            *common_tail,
        ]
        log.debug("intro encode cmd: %s", " ".join(cmd[:20]) + " …")
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if r.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                return True
            stderr = r.stderr.decode(errors="replace").strip()
            log.debug("intro encode failed (code %d): %s", r.returncode, stderr[-300:])
            output_path.unlink(missing_ok=True)
            return False
        except subprocess.TimeoutExpired:
            log.warning("intro encode timed out")
            output_path.unlink(missing_ok=True)
            return False
        except OSError as e:
            log.warning("intro encode OS error: %s", e)
            output_path.unlink(missing_ok=True)
            return False

    if _run(nvenc_args, 300):
        return True
    log.info("intro NVENC failed, falling back to libx264")
    return _run(sw_args, 420)


def _record_metric(
    start_time: float,
    output_path: Path,
    compressed: bool,
    raw_path: Path | None,
) -> None:
    """Log clip metric. Never raises."""
    try:
        from .metrics import ClipMetric, log_clip_metric
        size_mb = output_path.stat().st_size / (1024 * 1024)
        original_mb = raw_path.stat().st_size / (1024 * 1024) if raw_path and raw_path.exists() else size_mb
        log_clip_metric(ClipMetric(
            timestamp=time.time(),
            save_duration_sec=time.monotonic() - start_time,
            compressed=compressed,
            original_size_mb=original_mb,
            final_size_mb=size_mb,
        ))
    except Exception:
        pass
