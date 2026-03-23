"""
Editor model — pure Python data classes for clip overlay editing.
No Qt imports. Serializes to/from .edits.json sidecar.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger(__name__)

# ── Built-in SFX ──────────────────────────────────────────────────────────────

_ASSETS_DIR = Path(__file__).parent.parent / "assets"

BUILTIN_SFX: dict[str, Path] = {
    "vine_boom":  _ASSETS_DIR / "sfx_vine_boom.mp3",
    "bruh":       _ASSETS_DIR / "sfx_bruh.mp3",
    "airhorn":    _ASSETS_DIR / "sfx_airhorn.mp3",
    "rizz":       _ASSETS_DIR / "sfx_rizz.mp3",
}

SFX_DISPLAY_NAMES: dict[str, str] = {
    "vine_boom":  "Vine Boom",
    "bruh":       "Bruh",
    "airhorn":    "Airhorn",
    "rizz":       "Rizz",
}


# ── Overlay item ──────────────────────────────────────────────────────────────

@dataclass
class OverlayItem:
    kind: str           # "text" | "sfx" | "image"
    timestamp_s: float
    duration_s: float   # ignored for sfx (instantaneous)

    # text fields
    text: str = ""
    font_size: int = 28
    color: str = "white"
    x_pct: float = 0.5
    y_pct: float = 0.9

    # sfx fields
    sfx_name: str = "vine_boom"
    volume: float = 1.0

    # image fields
    image_path: str = ""
    image_scale: float = 0.25
    img_x_pct: float = 0.5
    img_y_pct: float = 0.5

    def describe(self) -> str:
        """Short human-readable description for the overlay list."""
        t = self.timestamp_s
        if self.kind == "text":
            preview = self.text[:30] + ("…" if len(self.text) > 30 else "")
            return f"[{t:.1f}s] text: {preview!r}"
        elif self.kind == "sfx":
            name = SFX_DISPLAY_NAMES.get(self.sfx_name, self.sfx_name)
            return f"[{t:.1f}s] sfx: {name}  ×{self.volume:.1f}"
        elif self.kind == "image":
            fname = Path(self.image_path).name if self.image_path else "(no file)"
            return f"[{t:.1f}s] image: {fname}  {int(self.image_scale*100)}%"
        return f"[{t:.1f}s] {self.kind}"


# ── Editor model ──────────────────────────────────────────────────────────────

class EditorModel:
    """Holds all overlay data for a clip editing session."""

    def __init__(self, clip_path: Path, duration_s: float) -> None:
        self.clip_path = clip_path
        self.duration_s = duration_s
        self.overlays: list[OverlayItem] = []

    @property
    def _sidecar_path(self) -> Path:
        return self.clip_path.with_suffix(".edits.json")

    def add(self, item: OverlayItem) -> None:
        self.overlays.append(item)
        # Keep sorted by timestamp
        self.overlays.sort(key=lambda o: o.timestamp_s)

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.overlays):
            del self.overlays[index]

    def clear(self) -> None:
        self.overlays.clear()

    def save(self) -> None:
        """Persist overlays to .edits.json sidecar."""
        try:
            data = {
                "clip": str(self.clip_path),
                "duration_s": self.duration_s,
                "overlays": [asdict(o) for o in self.overlays],
            }
            self._sidecar_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("EditorModel.save failed: %s", e)

    def load(self) -> None:
        """Load overlays from .edits.json sidecar if it exists."""
        sp = self._sidecar_path
        if not sp.exists():
            return
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            self.overlays = []
            for od in data.get("overlays", []):
                # Only pull known fields — ignore extras gracefully
                item = OverlayItem(
                    kind=od.get("kind", "text"),
                    timestamp_s=float(od.get("timestamp_s", 0.0)),
                    duration_s=float(od.get("duration_s", 3.0)),
                    text=od.get("text", ""),
                    font_size=int(od.get("font_size", 28)),
                    color=od.get("color", "white"),
                    x_pct=float(od.get("x_pct", 0.5)),
                    y_pct=float(od.get("y_pct", 0.9)),
                    sfx_name=od.get("sfx_name", "vine_boom"),
                    volume=float(od.get("volume", 1.0)),
                    image_path=od.get("image_path", ""),
                    image_scale=float(od.get("image_scale", 0.25)),
                    img_x_pct=float(od.get("img_x_pct", 0.5)),
                    img_y_pct=float(od.get("img_y_pct", 0.5)),
                )
                self.overlays.append(item)
            log.debug("EditorModel.load: %d overlays from sidecar", len(self.overlays))
        except Exception as e:
            log.warning("EditorModel.load failed: %s", e)
