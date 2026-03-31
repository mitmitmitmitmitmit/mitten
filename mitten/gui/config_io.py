"""
TOML serializer for MittenConfig.
config_to_toml(cfg) -> str produces a valid config.toml string.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import MittenConfig, CONFIG_FILE


def config_to_toml(cfg: MittenConfig) -> str:
    g  = cfg.general
    r  = cfg.recorder
    t  = cfg.trigger
    wm = cfg.watermark
    gd = cfg.game_detection
    n  = cfg.notifications
    dc = cfg.discord

    save_dir = str(g.save_dir).replace(str(__import__("pathlib").Path.home()), "~")

    procs  = _toml_str_list(list(gd.custom_processes))
    titles = _toml_str_list(list(gd.custom_window_titles))

    return f"""\
[general]
mode = {_q(g.mode)}
buffer_seconds = {g.buffer_seconds}
framerate = {g.framerate}
save_dir = {_q(save_dir)}
monitor = {_q(g.monitor)}
theme = {_q(g.theme)}
developer_mode = {_bool(g.developer_mode)}

[recorder]
container = {_q(r.container)}
quality = {_q(r.quality)}
capture_codec = {_q(r.capture_codec)}
output_codec = {_q(r.output_codec)}
watermark_cq = {r.watermark_cq}
audio_device = {_q(r.audio_device)}
mic_device = {_q(r.mic_device)}
mic_volume = {r.mic_volume}
mic_noise_reduction = {_bool(r.mic_noise_reduction)}
mic_ducking = {_bool(r.mic_ducking)}
mic_ducking_reduction = {r.mic_ducking_reduction}
auto_compress = {_bool(r.auto_compress)}
compression_target_mb = {r.compression_target_mb}

[trigger]
button = {_q(t.button)}
cooldown = {t.cooldown}
trigger_type = {_q(t.trigger_type)}
trigger_key = {_q(t.trigger_key)}

[watermark]
enabled = {_bool(wm.enabled)}
text = {_q(wm.text)}
subtext = {_q(wm.subtext)}
font_family = {_q(wm.font_family)}
fontsize = {wm.fontsize}
fontcolor = {_q(wm.fontcolor)}
position = {_q(wm.position)}
padding = {wm.padding}

[game_detection]
enabled = {_bool(gd.enabled)}
poll_interval = {gd.poll_interval}
auto_switch = {_bool(gd.auto_switch)}
custom_processes = {procs}
custom_window_titles = {titles}

[notifications]
enabled = {_bool(n.enabled)}
on_start = {_bool(n.on_start)}
on_save = {_bool(n.on_save)}
on_error = {_bool(n.on_error)}

[discord]
enabled = {_bool(dc.enabled)}
show_ascii = {_bool(dc.show_ascii)}
animated_ascii = {_bool(dc.animated_ascii)}
show_game_name = {_bool(dc.show_game_name)}
show_mode_label = {_bool(dc.show_mode_label)}
show_name = {_bool(dc.show_name)}
page_dashboard = {_bool(dc.page_dashboard)}
page_clips = {_bool(dc.page_clips)}
page_settings = {_bool(dc.page_settings)}
page_about = {_bool(dc.page_about)}
page_debug = {_bool(dc.page_debug)}
gui_name_override = {_bool(dc.gui_name_override)}
stealth_recording = {_bool(dc.stealth_recording)}
"""


def save_config(cfg: MittenConfig, path: Path | None = None) -> None:
    """Atomically write config to disk. Creates parent dirs if needed."""
    dest = path or CONFIG_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(config_to_toml(cfg))
    os.replace(tmp, dest)
    os.chmod(dest, 0o600)


def _q(s: str) -> str:
    """Wrap a string value in TOML double-quotes with basic escaping."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _toml_str_list(items: list[str]) -> str:
    if not items:
        return "[]"
    inner = ", ".join(_q(x) for x in items)
    return f"[{inner}]"
