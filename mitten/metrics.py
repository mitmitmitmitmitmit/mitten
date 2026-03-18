"""
Clip save metrics logger — tracks save duration, size, and compression per clip.
Provides aggregate stats for the CLIPS pill tab.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

METRICS_FILE = Path.home() / ".local" / "share" / "mitten" / "clip_metrics.json"

_lock = threading.Lock()


@dataclass
class ClipMetric:
    timestamp: float           # unix time
    save_duration_sec: float   # how long the save took end-to-end
    compressed: bool           # True if size-reduction re-encode fired
    original_size_mb: float    # file size before any compression pass
    final_size_mb: float       # file size written to disk


def log_clip_metric(metric: ClipMetric) -> None:
    """Append a metric record to the JSON file. Thread-safe. Never raises."""
    try:
        with _lock:
            METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            records: list[dict] = []
            if METRICS_FILE.exists():
                try:
                    records = json.loads(METRICS_FILE.read_text())
                except Exception:
                    records = []
            records.append(asdict(metric))
            METRICS_FILE.write_text(json.dumps(records))
    except Exception:
        pass


def load_metrics() -> list[ClipMetric]:
    """Parse metrics JSON. Returns empty list on any error."""
    try:
        if not METRICS_FILE.exists():
            return []
        raw = json.loads(METRICS_FILE.read_text())
        return [ClipMetric(**r) for r in raw]
    except Exception:
        return []


def clips_this_week() -> int:
    """Count clips saved in the last 7 days."""
    cutoff = time.time() - 7 * 86400
    return sum(1 for m in load_metrics() if m.timestamp >= cutoff)


def avg_save_time() -> float | None:
    """Average save duration of the last 30 clips in seconds. None if no data."""
    metrics = load_metrics()[-30:]
    if not metrics:
        return None
    return sum(m.save_duration_sec for m in metrics) / len(metrics)


def compression_rate() -> float | None:
    """Fraction of last 30 clips that triggered compression (0.0–1.0). None if no data."""
    metrics = load_metrics()[-30:]
    if not metrics:
        return None
    return sum(1 for m in metrics if m.compressed) / len(metrics)
