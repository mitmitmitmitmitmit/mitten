"""
Intro animation filter_complex builder for Mitten clips.

Prepends a 2.5s animated "MITTEN" logo intro to every saved clip via a single
ffmpeg filter_complex pass. The intro is non-disableable (hardcoded). The user
can choose the animation style (Snap, Rise, Typewriter, Glitch, etc.).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

INTRO_DUR: float = 2.5   # seconds of black preamble before clip content starts

# ── Letter geometry (1920×1080 reference, fontsize 96 for M, 72 for others) ──
# x_ref: pixel offset from left edge of the "MITTEN" word block at 1920px width
# t_pop: when this letter starts appearing (seconds)
_LETTERS: list[tuple[str, int, int, float]] = [
    ("M", 96,   0,  0.00),
    ("i", 72,  69,  0.50),
    ("t", 72,  95,  0.65),
    ("t", 72, 137,  0.80),
    ("e", 72, 179,  0.95),
    ("n", 72, 223,  1.10),
]
_WORD_HALF_W_REF = 132   # half of estimated total word width at 1920px
_FADE_START = 2.00       # when everything starts fading out toward INTRO_DUR
_POST_V03_T  = 1.30      # when version label appears
_POST_NAME_T = 1.50      # when "Clipped by..." line appears


# ── Expression helpers ────────────────────────────────────────────────────────

def _letter_alpha_expr(idx: int, t_pop: float) -> str:
    """Alpha AVExpr for a letter: fade or snap in, hold, then fade out."""
    fade_dur = INTRO_DUR - _FADE_START   # 0.5s fade-out window
    if idx == 0:
        # M fades in over 0.4s
        return (
            f"if(lt(t,0.4),t/0.4,"
            f"if(lt(t,{_FADE_START}),1.0,"
            f"max(0,1.0-(t-{_FADE_START})/{fade_dur})))"
        )
    # Other letters ramp from 0→1 over 0.1s then hold
    ramp_end = round(t_pop + 0.10, 3)
    return (
        f"if(lt(t,{t_pop}),0,"
        f"if(lt(t,{ramp_end}),(t-{t_pop})/0.1,"
        f"if(lt(t,{_FADE_START}),1.0,"
        f"max(0,1.0-(t-{_FADE_START})/{fade_dur}))))"
    )


def _letter_y_expr(t_pop: float, style: str) -> str:
    """Y-position AVExpr for a letter, depending on animation style."""
    center = "(H-th)/2"
    if style in ("Snap", "Shatter", "Flashframe", "Broadcast", "Ripple"):
        # Drop from 20px above center, snap to center over 0.1s
        drop = 20
        ramp_end = round(t_pop + 0.10, 3)
        return (
            f"if(lt(t,{t_pop}),{center}-{drop},"
            f"if(lt(t,{ramp_end}),"
            f"{center}-{drop}+{drop}*(t-{t_pop})/0.10,"
            f"{center}))"
        )
    elif style == "Rise":
        # Drift upward from 20px below, settle over 0.15s
        rise = 20
        ramp_end = round(t_pop + 0.15, 3)
        return (
            f"if(lt(t,{t_pop}),{center}+{rise},"
            f"if(lt(t,{ramp_end}),"
            f"{center}+{rise}-{rise}*(t-{t_pop})/0.15,"
            f"{center}))"
        )
    else:
        # Typewriter, Glitch: no vertical movement
        return center


def _letter_x_expr(x_offset_ref: int, style: str, idx: int, t_pop: float) -> str:
    """X-position AVExpr for a letter, scaled from 1920 reference."""
    x_const = 960 - _WORD_HALF_W_REF + x_offset_ref
    base = f"(W/1920)*{x_const}"
    # Glitch: add x jitter during the arrival window
    if style == "Glitch" and idx > 0:
        jitter_end = round(t_pop + 0.20, 3)
        return (
            f"if(lt(t,{jitter_end}),"
            f"({base})+8*sin(t*97.3),"
            f"{base})"
        )
    # Shatter: odd letters from left, even from right (beyond initial)
    if style == "Shatter" and idx > 0:
        # Start position offset from center, converge to x_const
        side = 80 if idx % 2 == 0 else -80   # even from right, odd from left
        ramp_end = round(t_pop + 0.12, 3)
        return (
            f"if(lt(t,{t_pop}),(W/1920)*{x_const + side},"
            f"if(lt(t,{ramp_end}),"
            f"(W/1920)*{x_const + side}+(W/1920)*{-side}*(t-{t_pop})/0.12,"
            f"{base}))"
        )
    return base


def _subtitle_alpha_expr(t_appear: float) -> str:
    """Alpha AVExpr for post-sequence subtitle lines."""
    ramp_end = round(t_appear + 0.20, 3)
    fade_dur = INTRO_DUR - _FADE_START
    return (
        f"if(lt(t,{t_appear}),0,"
        f"if(lt(t,{ramp_end}),(t-{t_appear})/0.20,"
        f"if(lt(t,{_FADE_START}),1.0,"
        f"max(0,1.0-(t-{_FADE_START})/{fade_dur}))))"
    )


def _safe_text(s: str) -> str:
    """Escape text for use inside ffmpeg drawtext single-quoted string."""
    s = re.sub(r'[\x00-\x1f\x7f`$]', '', s)
    return s.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


# ── Main builder ──────────────────────────────────────────────────────────────

def build_intro_filter_complex(
    w: int,
    h: int,
    fps: str,
    wm,
    light_mode: bool,
    has_audio: bool,
    scale_720: bool,
    style: str = "Snap",
) -> tuple[str, list[str]]:
    """
    Build an ffmpeg filter_complex that prepends a 2.5s MITTEN intro to the clip.

    Returns (filter_complex_str, extra_output_args) where extra_output_args
    contains -map flags to select [vout] and optionally [aout].
    """
    filters: list[str] = []

    # ── Black preamble ────────────────────────────────────────────────────────
    filters.append(
        f"color=black:size={w}x{h}:rate={fps}:duration={INTRO_DUR}[intro_black]"
    )

    # ── Letter animation chain ────────────────────────────────────────────────
    prev = "intro_black"
    for idx, (char, fontsize, x_offset_ref, t_pop) in enumerate(_LETTERS):
        out_label = f"l{idx}"
        alpha_expr = _letter_alpha_expr(idx, t_pop)
        y_expr     = "(H-th)/2" if idx == 0 else _letter_y_expr(t_pop, style)
        x_expr     = _letter_x_expr(x_offset_ref, style, idx, t_pop)

        # Glitch: alpha flicker added on top of normal ramp
        if style == "Glitch" and idx > 0:
            glitch_end = round(t_pop + 0.20, 3)
            alpha_expr = (
                f"if(lt(t,{glitch_end}),"
                f"({alpha_expr})*abs(sin(t*143.7)),"
                f"{alpha_expr})"
            )

        # Flashframe: brief white flash at t_pop (alpha spikes then settles)
        if style == "Flashframe" and idx > 0:
            flash_end = round(t_pop + 0.08, 3)
            alpha_expr = (
                f"if(lt(t,{flash_end}),"
                f"min(1,({alpha_expr})*3),"
                f"{alpha_expr})"
            )

        filters.append(
            f"[{prev}]drawtext="
            f"text='{_safe_text(char)}':"
            f"fontsize={fontsize}:"
            f"fontcolor=white:"
            f"x={x_expr}:"
            f"y={y_expr}:"
            f"shadowcolor=black@0.6:shadowx=2:shadowy=2:"
            f"alpha='{alpha_expr}':"
            f"enable='between(t,0,{INTRO_DUR})'[{out_label}]"
        )
        prev = out_label

    # ── Post-sequence: version label ──────────────────────────────────────────
    try:
        from importlib.metadata import version as _pkg_ver
        ver_full = _pkg_ver("mitten")
    except Exception:
        ver_full = "0.3.1"
    ver_parts = ver_full.split(".")
    ver_display = ".".join(ver_parts[:3]) if len(ver_parts) >= 3 else ver_full

    v03_alpha = _subtitle_alpha_expr(_POST_V03_T)
    filters.append(
        f"[{prev}]drawtext="
        f"text='v{_safe_text(ver_display)}':"
        f"fontsize=22:"
        f"fontcolor=white@0.85:"
        f"x=(W-tw)/2:"
        f"y=(H-th)/2+72:"
        f"shadowcolor=black@0.5:shadowx=1:shadowy=1:"
        f"alpha='{v03_alpha}':"
        f"enable='between(t,{_POST_V03_T},{INTRO_DUR})'[lv]"
    )
    prev = "lv"

    # ── Post-sequence: "Clipped by [name]" ───────────────────────────────────
    intro_name = getattr(wm, "intro_name", "").strip() if wm is not None else ""
    if intro_name:
        clipped_text = f"Clipped by {intro_name} on Linux"
    else:
        clipped_text = "Clipped on Linux"

    name_alpha = _subtitle_alpha_expr(_POST_NAME_T)
    filters.append(
        f"[{prev}]drawtext="
        f"text='{_safe_text(clipped_text)}':"
        f"fontsize=16:"
        f"fontcolor=white@0.65:"
        f"x=(W-tw)/2:"
        f"y=(H-th)/2+100:"
        f"shadowcolor=black@0.5:shadowx=1:shadowy=1:"
        f"alpha='{name_alpha}':"
        f"enable='between(t,{_POST_NAME_T},{INTRO_DUR})'[intro_done]"
    )

    # ── Clip branch: scale → user watermark → shame → denoise → format ────────
    clip_in = "[0:v]"

    if scale_720:
        filters.append(f"{clip_in}scale=-2:720[scaled]")
        clip_in = "[scaled]"

    wm_enabled = wm is not None and getattr(wm, "enabled", True)
    if wm_enabled:
        text    = re.sub(r'[\x00-\x1f\x7f`$]', '', wm.text)
        subtext = re.sub(r'[\x00-\x1f\x7f`$]', '', wm.subtext)
        p        = wm.padding
        fontsize = wm.fontsize
        fontcolor = wm.fontcolor
        position  = wm.position
        subsize  = max(10, fontsize - 6)
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
        else:
            main_x, sub_x = str(p), str(p)
            main_y, sub_y = str(p), f"{p + fontsize + 6}"

        shadow    = "shadowcolor=black@0.7:shadowx=2:shadowy=2"
        subshadow = "shadowcolor=black@0.5:shadowx=1:shadowy=1"

        filters.append(
            f"{clip_in}drawtext=text='{_safe_text(text)}':"
            f"fontsize={fontsize}:fontcolor={fontcolor}:"
            f"x={main_x}:y={main_y}:{shadow}[uwm1]"
        )
        filters.append(
            f"[uwm1]drawtext=text='{_safe_text(subtext)}':"
            f"fontsize={subsize}:fontcolor=white@0.35:"
            f"x={sub_x}:y={sub_y}:{subshadow}[uwm2]"
        )
        clip_in = "[uwm2]"

    if light_mode:
        shame = "this user is a freak that uses light mode"
        filters.append(
            f"{clip_in}drawtext=text='{_safe_text(shame)}':"
            f"fontsize=11:fontcolor=white@0.18:"
            f"x=8:y=H-th-8:"
            f"shadowcolor=black@0.3:shadowx=1:shadowy=1[lm_shame]"
        )
        clip_in = "[lm_shame]"

    # Denoise only on clip branch (not intro frames — no state contamination at seam)
    filters.append(f"{clip_in}hqdn3d=1.5:1.5:6:6[hq]")
    filters.append("[hq]format=yuv420p[clip_done]")

    # Force same pixel format on intro branch for concat compatibility
    filters.append("[intro_done]format=yuv420p[intro_fmt]")

    # ── Concat intro + clip ───────────────────────────────────────────────────
    filters.append("[intro_fmt][clip_done]concat=n=2:v=1:a=0[concat_v]")

    # ── Persistent "mitten" mark — visible only after intro ends ─────────────
    filters.append(
        f"[concat_v]drawtext="
        f"text='mitten':"
        f"fontsize=14:"
        f"fontcolor=white@0.45:"
        f"x=8:y=H-th-8:"
        f"shadowcolor=black@0.5:shadowx=1:shadowy=1:"
        f"enable='gte(t,{INTRO_DUR})'[vout]"
    )

    # ── Audio ─────────────────────────────────────────────────────────────────
    if has_audio:
        ms = int(INTRO_DUR * 1000)
        filters.append(
            f"aevalsrc=0:channel_layout=stereo:sample_rate=48000"
            f":duration={INTRO_DUR}[silence]"
        )
        filters.append(f"[0:a]adelay={ms}|{ms}[aud_delayed]")
        filters.append("[silence][aud_delayed]concat=n=2:v=0:a=1[aout]")
        extra_args = ["-map", "[vout]", "-map", "[aout]"]
    else:
        extra_args = ["-map", "[vout]", "-an"]

    return ";\n".join(filters), extra_args
