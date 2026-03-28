"""
Shared formatting and hardware-query utilities.
Centralises helpers that were previously duplicated across main_window.py and stats.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def format_duration(seconds: int) -> str:
    """Return a human-readable duration string: '30s', '5m', '1h 30m'."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def get_vram_usage() -> tuple[float, float] | None:
    """
    Return (used_gb, total_gb) for the primary GPU, or None if unavailable.
    Tries NVIDIA nvidia-smi first, then AMD sysfs as a fallback.
    """
    # NVIDIA
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                used = float(parts[0].strip())
                total = float(parts[1].strip())
                return used / 1024, total / 1024
    except Exception:
        pass

    # AMD — read from sysfs (best-effort, Linux only)
    if sys.platform != "win32":
        try:
            used_path = Path("/sys/class/drm/card0/device/mem_info_vram_used")
            total_path = Path("/sys/class/drm/card0/device/mem_info_vram_total")
            if used_path.exists() and total_path.exists():
                used_b = int(used_path.read_text().strip())
                total_b = int(total_path.read_text().strip())
                return used_b / (1024 ** 3), total_b / (1024 ** 3)
        except Exception:
            pass

    return None
