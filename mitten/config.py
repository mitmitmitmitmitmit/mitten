"""
Config loading, validation, and the MittenConfig dataclass.
"""
from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "mitten"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_CONFIG_SRC = Path(__file__).parent.parent / "default_config.toml"

TMP_DIR = Path("/tmp/mitten")
PID_FILE = TMP_DIR / "mitten.pid"
GUI_SOCKET = TMP_DIR / "gui.sock"
GUI_PRESENCE_FILE = TMP_DIR / "gui_presence.json"  # written by GUI when focused, read by daemon
PAUSE_FILE = TMP_DIR / "paused"          # exists ↔ recording is paused
RECORDER_DEAD_FILE = TMP_DIR / "recorder_dead"  # exists ↔ recorder gave up

VALID_QUALITIES = {"very_high", "high", "medium", "low"}
VALID_POSITIONS = {"bottom_right", "bottom_left", "top_right", "top_left"}
VALID_MODES = {"desktop", "window", "game"}

BUTTON_NAMES: dict[str, int] = {
    "BTN_LEFT":    272,
    "BTN_RIGHT":   273,
    "BTN_MIDDLE":  274,
    "BTN_SIDE":    275,
    "BTN_EXTRA":   276,
    "BTN_FORWARD": 277,
    "BTN_BACK":    278,
}


@dataclass(frozen=True)
class GeneralConfig:
    mode: str = "desktop"
    buffer_seconds: int = 30
    framerate: int = 30
    save_dir: Path = Path.home() / "Videos" / "mitten"
    monitor: str = "auto"
    theme: str = "Default"
    developer_mode: bool = False


@dataclass(frozen=True)
class RecorderConfig:
    container: str = "mp4"
    quality: str = "very_high"
    capture_codec: str = "hevc"   # codec for the replay buffer: hevc = better compression in RAM
    output_codec: str = "h264"    # codec for saved clips: h264 = Discord/browser compatible
    watermark_cq: int = 26        # NVENC constant quality: lower = better/larger, higher = smaller/worse
    audio_device: str = ""        # empty = no audio; use gpu-screen-recorder --list-audio-devices
    auto_compress: bool = True    # re-encode clip to fit within compression_target_mb
    compression_target_mb: int = 10  # target file size for auto_compress


@dataclass(frozen=True)
class TriggerConfig:
    button: str = "BTN_EXTRA"
    cooldown: float = 3.0


@dataclass(frozen=True)
class WatermarkConfig:
    enabled: bool = True
    text: str = "~( ^.x.^)> caught by mitten"
    subtext: str = "programmed by mit"
    font_family: str = "Sans"
    fontsize: int = 20
    fontcolor: str = "white@0.6"
    position: str = "bottom_right"
    padding: int = 20


@dataclass(frozen=True)
class GameDetectionConfig:
    enabled: bool = True
    poll_interval: int = 5
    auto_switch: bool = True
    custom_processes: tuple[str, ...] = field(default_factory=tuple)
    custom_window_titles: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NotificationsConfig:
    enabled: bool = True
    on_start: bool = True
    on_save: bool = True
    on_error: bool = True


@dataclass(frozen=True)
class DiscordConfig:
    enabled: bool = True
    show_ascii: bool = True
    animated_ascii: bool = True
    show_game_name: bool = True    # False = "clipping with mitten" instead of actual game name
    show_mode_label: bool = True   # False = activity name is just "mitten"
    show_name: bool = True         # False = no name override (uses app name "MITTEN")


@dataclass(frozen=True)
class MittenConfig:
    general: GeneralConfig
    recorder: RecorderConfig
    trigger: TriggerConfig
    watermark: WatermarkConfig
    game_detection: GameDetectionConfig
    notifications: NotificationsConfig
    discord: DiscordConfig = field(default_factory=DiscordConfig)


def _resolve_path(p: str) -> Path:
    return Path(os.path.expanduser(p)).expanduser()


def _parse_button(val: str | int) -> str:
    if isinstance(val, int):
        reverse = {v: k for k, v in BUTTON_NAMES.items()}
        return reverse.get(val, f"BTN_{val}")
    val = str(val).upper()
    if not val.startswith("BTN_"):
        val = "BTN_" + val
    return val


def _validate(cfg: MittenConfig) -> None:
    g = cfg.general
    if g.mode not in VALID_MODES:
        raise ValueError(f"general.mode must be one of {VALID_MODES}")
    if not (15 <= g.buffer_seconds <= 120):
        raise ValueError("general.buffer_seconds must be between 15 and 120")
    if g.framerate not in (24, 30, 60):
        raise ValueError("general.framerate must be 24, 30, or 60")

    r = cfg.recorder
    if r.quality not in VALID_QUALITIES:
        raise ValueError(f"recorder.quality must be one of {VALID_QUALITIES}")

    w = cfg.watermark
    if w.position not in VALID_POSITIONS:
        raise ValueError(f"watermark.position must be one of {VALID_POSITIONS}")

    t = cfg.trigger
    if t.cooldown < 1.0:
        raise ValueError("trigger.cooldown must be >= 1.0 seconds")


def load_config(config_path: Path | None = None) -> MittenConfig:
    """Load config from file, creating defaults if missing."""
    path = config_path or CONFIG_FILE

    if not path.exists():
        create_default_config()

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except Exception as e:
        raise SystemExit(
            f"\nError: config file is invalid or unreadable: {path}\n"
            f"  {e}\n\n"
            f"To reset to defaults, delete it and run mitten again:\n"
            f"  rm {path}\n"
        ) from e

    g  = raw.get("general", {})
    r  = raw.get("recorder", {})
    t  = raw.get("trigger", {})
    wm = raw.get("watermark", {})
    gd = raw.get("game_detection", {})
    n  = raw.get("notifications", {})
    d  = raw.get("discord", {})

    cfg = MittenConfig(
        general=GeneralConfig(
            mode=g.get("mode", "desktop"),
            buffer_seconds=int(g.get("buffer_seconds", 30)),
            framerate=int(g.get("framerate", 30)),
            save_dir=_resolve_path(g.get("save_dir", "~/Videos/mitten")),
            monitor=str(g.get("monitor", "auto")),
            theme=str(g.get("theme", "Default")),
            developer_mode=bool(g.get("developer_mode", False)),
        ),
        recorder=RecorderConfig(
            container=str(r.get("container", "mp4")),
            quality=str(r.get("quality", "very_high")),
            capture_codec=str(r.get("capture_codec", "hevc")),
            output_codec=str(r.get("output_codec", "h264")),
            watermark_cq=int(r.get("watermark_cq", 26)),
            audio_device=str(r.get("audio_device", "")),
            auto_compress=bool(r.get("auto_compress", True)),
            compression_target_mb=int(r.get("compression_target_mb", 10)),
        ),
        trigger=TriggerConfig(
            button=_parse_button(t.get("button", "BTN_EXTRA")),
            cooldown=float(t.get("cooldown", 3.0)),
        ),
        watermark=WatermarkConfig(
            enabled=bool(wm.get("enabled", True)),
            text=str(wm.get("text", "~( ^.x.^)> caught by mitten")),
            subtext=str(wm.get("subtext", "programmed by mit")),
            font_family=str(wm.get("font_family", "Sans")),
            fontsize=int(wm.get("fontsize", 20)),
            fontcolor=str(wm.get("fontcolor", "white@0.6")),
            position=str(wm.get("position", "bottom_right")),
            padding=int(wm.get("padding", 20)),
        ),
        game_detection=GameDetectionConfig(
            enabled=bool(gd.get("enabled", True)),
            poll_interval=int(gd.get("poll_interval", 5)),
            auto_switch=bool(gd.get("auto_switch", True)),
            custom_processes=tuple(gd.get("custom_processes", [])),
            custom_window_titles=tuple(gd.get("custom_window_titles", [])),
        ),
        notifications=NotificationsConfig(
            enabled=bool(n.get("enabled", True)),
            on_start=bool(n.get("on_start", True)),
            on_save=bool(n.get("on_save", True)),
            on_error=bool(n.get("on_error", True)),
        ),
        discord=DiscordConfig(
            enabled=bool(d.get("enabled", True)),
            show_ascii=bool(d.get("show_ascii", True)),
            animated_ascii=bool(d.get("animated_ascii", True)),
            show_game_name=bool(d.get("show_game_name", True)),
            show_mode_label=bool(d.get("show_mode_label", True)),
            show_name=bool(d.get("show_name", True)),
        ),
    )

    _validate(cfg)
    return cfg


def create_default_config(dest: Path | None = None) -> Path:
    dest = dest or CONFIG_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    if DEFAULT_CONFIG_SRC.exists():
        shutil.copy(DEFAULT_CONFIG_SRC, dest)
    else:
        dest.write_text(_INLINE_DEFAULT)
    return dest


def button_name_to_code(name: str) -> int:
    return BUTTON_NAMES.get(name.upper(), 276)


_INLINE_DEFAULT = """\
[general]
mode = "desktop"
buffer_seconds = 30
framerate = 30
save_dir = "~/Videos/mitten"
monitor = "auto"

[recorder]
container = "mp4"
quality = "very_high"

[trigger]
button = "BTN_EXTRA"
cooldown = 3.0

[watermark]
enabled = true
text = "~( ^.x.^)> caught by mitten"
subtext = "programmed by mit"
fontsize = 20
fontcolor = "white@0.6"
position = "bottom_right"
padding = 20

[game_detection]
enabled = true
poll_interval = 5
auto_switch = true
custom_processes = []
custom_window_titles = []

[notifications]
enabled = true
on_start = true
on_save = true
on_error = true

[discord]
enabled = true
show_ascii = true
animated_ascii = true
show_game_name = true
show_mode_label = true
show_name = true
"""
