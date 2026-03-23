"""
Editor export — ffmpeg pipeline for baking overlays into a new clip file.
Runs in a QThread worker; never modifies the original file.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .editor_model import EditorModel, OverlayItem, BUILTIN_SFX

log = logging.getLogger(__name__)


# ── Text escaping (same rules as intro.py _safe_text) ────────────────────────

def _safe_text(text: str) -> str:
    """Escape text for use inside ffmpeg drawtext=text='...' expressions."""
    return text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


# ── Video info probe ──────────────────────────────────────────────────────────

def _probe_video_info(path: Path) -> tuple[int, int, str, bool]:
    """Return (width, height, fps_str, has_audio) via ffprobe.
    Returns (0, 0, '30', False) on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams",
                "-print_format", "json",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return 0, 0, "30", False
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        width = height = 0
        fps = "30"
        has_audio = False
        for s in streams:
            if s.get("codec_type") == "video" and width == 0:
                width = int(s.get("width", 0))
                height = int(s.get("height", 0))
                r_fr = s.get("r_frame_rate", "30/1")
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


# ── Collision-guarded output path ─────────────────────────────────────────────

def _make_output_path(clip_path: Path) -> Path:
    """Return <stem>_edited.mp4, adding _2, _3 etc. on collision."""
    base = clip_path.with_name(clip_path.stem + "_edited.mp4")
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = clip_path.with_name(f"{clip_path.stem}_edited_{counter}.mp4")
        if not candidate.exists():
            return candidate
        counter += 1


# ── Position helpers ──────────────────────────────────────────────────────────

def _drawtext_xy(x_pct: float, y_pct: float) -> tuple[str, str]:
    """Convert 0.0-1.0 fractional position to ffmpeg drawtext x/y expressions."""
    x_expr = f"(W-tw)*{x_pct:.4f}"
    y_expr = f"(H-th)*{y_pct:.4f}"
    return x_expr, y_expr


def _overlay_xy(x_pct: float, y_pct: float) -> tuple[str, str]:
    """Convert 0.0-1.0 fractional position to overlay x/y expressions."""
    x_expr = f"(W-overlay_w)*{x_pct:.4f}"
    y_expr = f"(H-overlay_h)*{y_pct:.4f}"
    return x_expr, y_expr


# ── filter_complex builder ────────────────────────────────────────────────────

def _build_filter_complex(
    model: EditorModel,
    width: int,
    height: int,
    has_audio: bool,
) -> tuple[str, list[str], list[str]]:
    """
    Build filter_complex string for all overlays.

    Returns:
        (filter_complex_str, extra_input_args, map_args)
        extra_input_args: extra -i flags for image inputs
        map_args: -map flags to select vout/aout streams
    """
    text_overlays  = [o for o in model.overlays if o.kind == "text"]
    sfx_overlays   = [o for o in model.overlays if o.kind == "sfx"]
    image_overlays = [o for o in model.overlays if o.kind == "image"]

    # Validate and filter SFX: skip missing files, log warning
    valid_sfx: list[OverlayItem] = []
    for o in sfx_overlays:
        sfx_path = BUILTIN_SFX.get(o.sfx_name)
        if sfx_path is None or not sfx_path.exists():
            log.warning("SFX file missing for '%s', skipping overlay at %.1fs", o.sfx_name, o.timestamp_s)
        else:
            valid_sfx.append(o)

    # Validate image overlays: skip missing/non-file paths
    valid_images: list[OverlayItem] = []
    for o in image_overlays:
        ip = Path(o.image_path)
        if not ip.exists() or not ip.is_file():
            log.warning("Image file missing: %s, skipping overlay at %.1fs", o.image_path, o.timestamp_s)
        else:
            valid_images.append(o)

    # extra -i inputs: SFX files first, then image files
    # Input indices: 0 = clip, 1..N = sfx, N+1..M = images
    sfx_input_start = 1
    img_input_start = sfx_input_start + len(valid_sfx)

    extra_input_args: list[str] = []
    for o in valid_sfx:
        extra_input_args += ["-i", str(BUILTIN_SFX[o.sfx_name])]
    for o in valid_images:
        extra_input_args += ["-i", str(o.image_path)]

    filter_parts: list[str] = []
    current_v = "[0:v]"

    # ── Text overlays ─────────────────────────────────────────────────────────
    for i, o in enumerate(text_overlays):
        out_label = f"[tv{i}]"
        t_start = o.timestamp_s
        t_end   = o.timestamp_s + o.duration_s
        x_expr, y_expr = _drawtext_xy(o.x_pct, o.y_pct)
        safe = _safe_text(o.text)
        color = o.color if o.color else "white"
        f = (
            f"{current_v}drawtext="
            f"text='{safe}'"
            f":fontsize={o.font_size}"
            f":fontcolor={color}"
            f":x='{x_expr}'"
            f":y='{y_expr}'"
            f":shadowcolor=black@0.7:shadowx=2:shadowy=2"
            f":enable='between(t,{t_start:.3f},{t_end:.3f})'"
            f":alpha='between(t,{t_start:.3f},{t_end:.3f})'"
            f"{out_label}"
        )
        filter_parts.append(f)
        current_v = out_label

    # ── Image overlays ────────────────────────────────────────────────────────
    for i, o in enumerate(valid_images):
        input_idx = img_input_start + i
        scale_target_w = max(1, int(width * o.image_scale))
        scaled_label = f"[imgscaled{i}]"
        out_label    = f"[iv{i}]"
        t_start = o.timestamp_s
        t_end   = o.timestamp_s + o.duration_s
        x_expr, y_expr = _overlay_xy(o.img_x_pct, o.img_y_pct)

        scale_f = f"[{input_idx}:v]scale={scale_target_w}:-2{scaled_label}"
        filter_parts.append(scale_f)

        overlay_f = (
            f"{current_v}{scaled_label}overlay="
            f"x='{x_expr}':y='{y_expr}'"
            f":enable='between(t,{t_start:.3f},{t_end:.3f})'"
            f"{out_label}"
        )
        filter_parts.append(overlay_f)
        current_v = out_label

    # ── Final video format ────────────────────────────────────────────────────
    filter_parts.append(f"{current_v}format=yuv420p[vout]")

    # ── Audio: SFX mixing ─────────────────────────────────────────────────────
    map_args = ["-map", "[vout]"]

    if valid_sfx:
        sfx_labels: list[str] = []
        for i, o in enumerate(valid_sfx):
            input_idx = sfx_input_start + i
            delay_ms  = int(o.timestamp_s * 1000)
            sfx_label = f"[sfx{i}]"
            # adelay pads all channels with the same delay
            f = (
                f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},"
                f"volume={o.volume:.3f}"
                f"{sfx_label}"
            )
            filter_parts.append(f)
            sfx_labels.append(sfx_label)

        if has_audio:
            base_audio = "[0:a]"
            n_inputs = 1 + len(valid_sfx)
        else:
            # Generate silence as base
            dur = model.duration_s
            filter_parts.append(
                f"aevalsrc=0:duration={dur:.3f}:sample_rate=48000"
                f":channel_layout=stereo[sil]"
            )
            base_audio = "[sil]"
            n_inputs = 1 + len(valid_sfx)

        all_audio = base_audio + "".join(sfx_labels)
        filter_parts.append(
            f"{all_audio}amix=inputs={n_inputs}:normalize=0[aout]"
        )
        map_args += ["-map", "[aout]"]
    elif has_audio:
        map_args += ["-map", "0:a?"]

    return ";".join(filter_parts), extra_input_args, map_args


# ── Export worker ─────────────────────────────────────────────────────────────

class _ExportWorker(QThread):
    """Background thread that runs the ffmpeg export."""

    # (success: bool, result: Path on success | str error message on failure)
    done = pyqtSignal(bool, object)

    def __init__(self, model: EditorModel, out_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._out_path = out_path

    def run(self) -> None:
        try:
            result = self._do_export()
            if result is True:
                self.done.emit(True, self._out_path)
            else:
                self.done.emit(False, str(result))
        except Exception as e:
            log.exception("Export worker unhandled exception")
            self.done.emit(False, str(e))

    def _do_export(self) -> bool | str:
        model     = self._model
        out_path  = self._out_path
        clip_path = model.clip_path

        if not clip_path.exists():
            return f"Source clip not found: {clip_path}"

        # No overlays → stream copy only
        if not model.overlays:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(clip_path),
                "-c", "copy",
                str(out_path),
            ]
            log.debug("Export (no overlays): %s", " ".join(cmd))
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=60)
                if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                    return True
                stderr = r.stderr.decode(errors="replace").strip()
                out_path.unlink(missing_ok=True)
                return f"ffmpeg stream copy failed: {stderr[-300:]}"
            except subprocess.TimeoutExpired:
                out_path.unlink(missing_ok=True)
                return "ffmpeg timed out (stream copy)"
            except OSError as e:
                return str(e)

        # Probe video info
        width, height, fps, has_audio = _probe_video_info(clip_path)
        if width == 0 or height == 0:
            log.warning("Export: ffprobe gave no dimensions, using 1920x1080 fallback")
            width, height = 1920, 1080

        # Build filter_complex
        try:
            fc, extra_inputs, map_args = _build_filter_complex(
                model, width, height, has_audio
            )
        except Exception as e:
            return f"filter_complex build failed: {e}"

        log.debug("Export filter_complex: %s", fc[:200])

        # Build ffmpeg command — NVENC primary, libx264 fallback
        common = [
            "ffmpeg", "-y",
            "-i", str(clip_path),
            *extra_inputs,
            "-filter_complex", fc,
            *map_args,
        ]
        tail = ["-movflags", "+faststart", str(out_path)]

        nvenc_cmd = common + ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26",
                              "-pix_fmt", "yuv420p"] + tail
        sw_cmd    = common + ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                              "-pix_fmt", "yuv420p"] + tail

        log.debug("Export NVENC cmd: %s …", " ".join(nvenc_cmd[:18]))

        result = self._run_cmd(nvenc_cmd, timeout=300)
        if result is True:
            return True

        log.info("NVENC export failed (%s), falling back to libx264", result)
        out_path.unlink(missing_ok=True)

        result = self._run_cmd(sw_cmd, timeout=420)
        if result is True:
            return True

        out_path.unlink(missing_ok=True)
        return f"Export failed (both NVENC and libx264): {result}"

    def _run_cmd(self, cmd: list[str], timeout: int) -> bool | str:
        """Run ffmpeg command, return True on success or error string on failure."""
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if r.returncode == 0 and self._out_path.exists() and self._out_path.stat().st_size > 0:
                return True
            stderr = r.stderr.decode(errors="replace").strip()
            log.debug("ffmpeg failed (code %d): %s", r.returncode, stderr[-300:])
            return f"ffmpeg error (code {r.returncode}): {stderr[-200:]}"
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg export timed out after %ds", timeout)
            return f"ffmpeg timed out after {timeout}s"
        except OSError as e:
            return str(e)
