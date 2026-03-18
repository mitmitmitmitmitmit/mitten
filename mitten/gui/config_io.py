"""
TOML serializer for MittenConfig.
config_to_toml(cfg) -> str produces a valid config.toml string.
"""
from __future__ import annotations

from ..config import MittenConfig


def config_to_toml(cfg: MittenConfig) -> str:
    g  = cfg.general
    r  = cfg.recorder
    t  = cfg.trigger
    wm = cfg.watermark
    gd = cfg.game_detection
    n  = cfg.notifications

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

[recorder]
container = {_q(r.container)}
quality = {_q(r.quality)}
capture_codec = {_q(r.capture_codec)}
output_codec = {_q(r.output_codec)}
watermark_cq = {r.watermark_cq}
audio_device = {_q(r.audio_device)}

[trigger]
button = {_q(t.button)}
cooldown = {t.cooldown}

[watermark]
enabled = {_bool(wm.enabled)}
text = {_q(wm.text)}
subtext = {_q(wm.subtext)}
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
"""


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
