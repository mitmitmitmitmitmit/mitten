"""
Stats & Performance panel — compact floating window showing live resource usage.

Shows: daemon state, RAM (daemon + recorder), GPU VRAM, uptime, today's clips.
Updates every 2s via QTimer.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .resources import C, CAT, CAT_FONT
from ..daemon_utils import get_daemon_pid
from ..utils import format_duration, get_vram_usage


# ------------------------------------------------------------------ #
# Stat card helper
# ------------------------------------------------------------------ #

class _StatCard(QFrame):
    """A rounded card with a label on top and a large value below."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "stat-card")
        self.setStyleSheet(
            f"background-color: {C.SURFACE}; border-radius: 8px; padding: 0px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        self._label = QLabel(label)
        self._label.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        layout.addWidget(self._label)

        self._value = QLabel("—")
        self._value.setStyleSheet(f"color: {C.TEXT}; font-size: 20px; font-weight: bold;")
        layout.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def set_label(self, text: str) -> None:
        self._label.setText(text)


# ------------------------------------------------------------------ #
# Status banner
# ------------------------------------------------------------------ #

class _StatusBanner(QFrame):
    """Colored banner showing current daemon state."""

    _STATE_STYLES = {
        "idle":      (C.GRAY,   "idle"),
        "recording": (C.GREEN,  "recording"),
        "game":      (C.ORANGE, "game mode active"),
        "saving":    (C.BLUE,   "saving clip..."),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "status-banner")
        self.setMinimumHeight(56)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self._state_label = QLabel(f"{CAT}  idle")
        self._state_label.setStyleSheet(f"font-size: 13px; font-weight: bold; {CAT_FONT}")
        layout.addWidget(self._state_label)

        self._detail_label = QLabel("uptime: —")
        self._detail_label.setStyleSheet(f"font-size: 11px; color: {C.SUBTEXT};")
        layout.addWidget(self._detail_label)

        self.set_state("idle")

    def set_state(self, state: str, detail: str = "") -> None:
        color_hex, text = self._STATE_STYLES.get(state, self._STATE_STYLES["idle"])
        color = QColor(color_hex)

        # Tinted background
        bg = QColor(color)
        bg.setAlpha(25)
        self.setStyleSheet(
            f"background-color: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
            f"border-radius: 8px; border-left: 3px solid {color_hex};"
        )
        self._state_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color_hex};"
        )
        self._state_label.setText(f"{CAT}  {text}")
        if detail:
            self._detail_label.setText(detail)


# ------------------------------------------------------------------ #
# Main stats dialog
# ------------------------------------------------------------------ #

class StatsPanel(QDialog):
    """Compact performance stats window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{CAT}  mitten stats")
        self.setFixedSize(310, 400)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(f"background-color: {C.BG};")

        self._build_ui()

        # Poll every 2 seconds
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)
        self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ── status banner ──
        self._banner = _StatusBanner()
        root.addWidget(self._banner)

        # ── memory section ──
        mem_label = QLabel("memory")
        mem_label.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 11px; font-weight: 600;"
        )
        root.addWidget(mem_label)

        mem_grid = QGridLayout()
        mem_grid.setSpacing(8)

        self._card_daemon = _StatCard("daemon")
        self._card_recorder = _StatCard("recorder")
        mem_grid.addWidget(self._card_daemon, 0, 0)
        mem_grid.addWidget(self._card_recorder, 0, 1)

        root.addLayout(mem_grid)

        # ── GPU VRAM ──
        self._vram_card = QFrame()
        self._vram_card.setStyleSheet(
            f"background-color: {C.SURFACE}; border-radius: 8px;"
        )
        vram_layout = QVBoxLayout(self._vram_card)
        vram_layout.setContentsMargins(12, 10, 12, 10)
        vram_layout.setSpacing(4)

        vram_header = QHBoxLayout()
        vram_lbl = QLabel("gpu vram")
        vram_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        self._vram_value = QLabel("— / — GB")
        self._vram_value.setStyleSheet(
            f"color: {C.TEXT}; font-size: 13px; font-weight: bold;"
        )
        self._vram_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        vram_header.addWidget(vram_lbl)
        vram_header.addWidget(self._vram_value)
        vram_layout.addLayout(vram_header)

        self._vram_bar = QProgressBar()
        self._vram_bar.setRange(0, 100)
        self._vram_bar.setValue(0)
        self._vram_bar.setFixedHeight(6)
        self._vram_bar.setTextVisible(False)
        vram_layout.addWidget(self._vram_bar)

        root.addWidget(self._vram_card)

        # ── caught today ──
        today_label = QLabel("caught today")
        today_label.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 11px; font-weight: 600;"
        )
        root.addWidget(today_label)

        today_card = QFrame()
        today_card.setStyleSheet(
            f"background-color: {C.SURFACE}; border-radius: 8px;"
        )
        today_layout = QVBoxLayout(today_card)
        today_layout.setContentsMargins(12, 10, 12, 10)
        today_layout.setSpacing(4)

        # Row: clips count + storage
        row1 = QHBoxLayout()
        self._clips_lbl = QLabel("clips")
        self._clips_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        self._clips_val = QLabel("—")
        self._clips_val.setStyleSheet(f"color: {C.TEXT}; font-size: 13px; font-weight: bold;")
        self._clips_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        row1.addWidget(self._clips_lbl)
        row1.addWidget(self._clips_val)
        today_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._storage_lbl = QLabel("storage")
        self._storage_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        self._storage_val = QLabel("—")
        self._storage_val.setStyleSheet(f"color: {C.TEXT}; font-size: 13px; font-weight: bold;")
        self._storage_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        row2.addWidget(self._storage_lbl)
        row2.addWidget(self._storage_val)
        today_layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._last_lbl = QLabel("last clip")
        self._last_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        self._last_val = QLabel("—")
        self._last_val.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        self._last_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        row3.addWidget(self._last_lbl)
        row3.addWidget(self._last_val)
        today_layout.addLayout(row3)

        root.addWidget(today_card)
        root.addStretch()

    # ------------------------------------------------------------------ #
    # Refresh logic (called every 2s)
    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        pid = self._read_daemon_pid()
        if pid is None:
            self._banner.set_state("idle", "uptime: —")
            self._card_daemon.set_value("—")
            self._card_recorder.set_value("—")
        else:
            uptime_str = self._get_uptime(pid)
            # TODO (Sonnet): detect game vs recording from config
            self._banner.set_state("recording", f"uptime: {uptime_str}")
            self._refresh_memory(pid)

        self._refresh_vram()
        self._refresh_clips()

    def _read_daemon_pid(self) -> int | None:
        return get_daemon_pid()

    def _get_uptime(self, pid: int) -> str:
        """Return human-readable uptime for a given PID."""
        try:
            import psutil
            elapsed = time.time() - psutil.Process(pid).create_time()
            return format_duration(int(elapsed))
        except Exception:
            return "—"

    def _refresh_memory(self, daemon_pid: int) -> None:
        """Read RSS for daemon + recorder child."""
        try:
            import psutil
        except ImportError:
            self._card_daemon.set_value("n/a")
            self._card_recorder.set_value("n/a")
            return

        try:
            proc = psutil.Process(daemon_pid)
            daemon_mb = proc.memory_info().rss / (1024 * 1024)
            self._card_daemon.set_value(f"{daemon_mb:.0f} MB")

            children = proc.children(recursive=True)
            rec_mb = 0.0
            for child in children:
                try:
                    rec_mb += child.memory_info().rss / (1024 * 1024)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            self._card_recorder.set_value(f"{rec_mb:.0f} MB")
        except Exception:
            self._card_daemon.set_value("—")
            self._card_recorder.set_value("—")

    def _refresh_vram(self) -> None:
        """Read GPU VRAM via shared utility (NVIDIA + AMD fallback)."""
        vram = get_vram_usage()
        if vram is not None:
            used_gb, total_gb = vram
            pct = int((used_gb / total_gb) * 100) if total_gb > 0 else 0
            self._vram_value.setText(f"{used_gb:.1f} / {total_gb:.1f} GB")
            self._vram_bar.setValue(pct)
            self._vram_card.show()
        else:
            self._vram_value.setText("unavailable")
            self._vram_bar.setValue(0)

    def _refresh_clips(self) -> None:
        """Count today's clips and show the last one."""
        try:
            from ..config import load_config
            save_dir = load_config().general.save_dir
        except Exception:
            save_dir = Path.home() / "Videos" / "mitten"

        today = datetime.now().strftime("%Y-%m-%d")
        clips = sorted(save_dir.glob("mitten_*.mp4"), reverse=True)
        todays_clips = [c for c in clips if today in c.name]

        self._clips_val.setText(str(len(todays_clips)))

        total_bytes = sum(c.stat().st_size for c in todays_clips if c.exists())
        total_mb = total_bytes / (1024 * 1024)
        self._storage_val.setText(f"{total_mb:.1f} MB")

        if todays_clips:
            last = todays_clips[0]
            size_mb = last.stat().st_size / (1024 * 1024)
            short_name = last.stem.replace("mitten_", "").replace("_", " ")
            self._last_val.setText(f"{short_name}  ·  {size_mb:.1f} MB")
        else:
            self._last_val.setText("no clips yet")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        self._timer.start(2000)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
