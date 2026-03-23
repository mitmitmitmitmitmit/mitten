"""
Clip Browser — split view: table on left, video player + trimmer on right.
"""
from __future__ import annotations

import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt, QUrl, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .resources import C, CAT, CAT_FONT, CATS, _accent_hover, _hex_rgba


# ------------------------------------------------------------------ #
# Duration prober (background thread)
# ------------------------------------------------------------------ #

class _DurationProber(QThread):
    duration_ready = pyqtSignal(int, int)

    def __init__(self, clips: list[tuple[int, Path]], parent=None) -> None:
        super().__init__(parent)
        self._clips = clips

    def run(self) -> None:
        for row, path in self._clips:
            dur = self._probe(path)
            self.duration_ready.emit(row, dur)

    @staticmethod
    def _probe(path: Path) -> int:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            return max(0, int(float(result.stdout.strip())))
        except Exception:
            return 0


# ------------------------------------------------------------------ #
# Trim worker (background thread)
# ------------------------------------------------------------------ #

class _TrimWorker(QThread):
    """Run ffmpeg trim in a background thread."""
    done = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, cmd: list[str], out_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._cmd = cmd
        self._out_path = out_path

    def run(self) -> None:
        try:
            result = subprocess.run(self._cmd, capture_output=True, timeout=60)
            if result.returncode == 0 and self._out_path.exists():
                size = self._out_path.stat().st_size / (1024 * 1024)
                self.done.emit(True, f"{self._out_path.name}  ({size:.1f} MB)")
            else:
                self.done.emit(False, "ffmpeg returned an error.")
        except Exception as e:
            self.done.emit(False, str(e))


# ------------------------------------------------------------------ #
# Video player panel
# ------------------------------------------------------------------ #

class _PlayerPanel(QWidget):
    """Video player with play/pause, seek, and trim controls."""

    clip_started   = pyqtSignal()   # clip begins playing (new load or resume)
    clip_paused    = pyqtSignal()   # clip paused or unloaded
    edit_requested = pyqtSignal()   # user pressed Edit button

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clip_path: Path | None = None
        self._duration_ms = 0
        self._media_player = None
        self._slider_dragging = False
        self._audio_proc: subprocess.Popen | None = None
        self._muted = False
        self._lag_timer = QTimer(self)
        self._lag_timer.setSingleShot(True)
        self._lag_timer.timeout.connect(self._do_light_mode_lag)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Video area — QGridLayout so the mute button can float over the video
        self._video_area = QWidget()
        self._video_area.setMinimumHeight(200)
        self._video_area.setStyleSheet(f"background-color: {C.BG}; border-radius: 6px 6px 0 0;")
        vid_grid = QGridLayout(self._video_area)
        vid_grid.setContentsMargins(0, 0, 0, 0)
        vid_grid.setSpacing(0)

        try:
            from PyQt6.QtMultimedia import QMediaPlayer
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            self._video_widget = QVideoWidget()
            self._video_widget.setStyleSheet(f"background: {C.BG};")
            self._video_widget.setAspectRatioMode(
                Qt.AspectRatioMode.KeepAspectRatioByExpanding
            )
            self._media_player = QMediaPlayer(self)
            # No QAudioOutput — audio is handled by ffplay subprocess
            self._media_player.setVideoOutput(self._video_widget)
            self._media_player.positionChanged.connect(self._on_position)
            self._media_player.durationChanged.connect(self._on_duration)
            vid_grid.addWidget(self._video_widget, 0, 0)
        except ImportError:
            cat = random.choice(CATS)
            placeholder = QLabel(f"{cat}\nvideo player unavailable")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 14px; {CAT_FONT}")
            vid_grid.addWidget(placeholder, 0, 0)

        # Empty state — same cell, hidden once a clip loads
        self._empty = QWidget()
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(6)
        try:
            from .themes import DARK_CAT_SLEEPY
            _empty_cat_text = DARK_CAT_SLEEPY
        except Exception:
            _empty_cat_text = "~( -.x.-)> zzz"
        self._empty_cat_lbl = QLabel(_empty_cat_text)
        self._empty_cat_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_cat_lbl.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 28px; font-weight: 700; {CAT_FONT}"
            f"background: transparent; border: none;"
        )
        empty_primary = QLabel("select a clip to preview")
        empty_primary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_primary.setStyleSheet(
            f"color: {C.TEXT}; font-size: 13px; background: transparent; border: none;"
        )
        empty_hint = QLabel("click any row on the left")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 11px; background: transparent; border: none;"
        )
        empty_layout.addWidget(self._empty_cat_lbl)
        empty_layout.addWidget(empty_primary)
        empty_layout.addWidget(empty_hint)
        vid_grid.addWidget(self._empty, 0, 0)

        layout.addWidget(self._video_area, 1)

        # Controls bar
        self._controls = QFrame()
        self._controls.setStyleSheet(
            f"QFrame {{ background-color: {_hex_rgba(C.SURFACE, 0.6)};"
            f"border-radius: 0 0 6px 6px; }}"
        )
        ctrl_layout = QVBoxLayout(self._controls)
        ctrl_layout.setContentsMargins(12, 8, 12, 10)
        ctrl_layout.setSpacing(8)

        # Seek slider
        self._pos_slider = QSlider(Qt.Orientation.Horizontal)
        self._pos_slider.setRange(0, 1000)
        self._pos_slider.sliderPressed.connect(self._on_slider_pressed)
        self._pos_slider.sliderReleased.connect(self._on_slider_released)
        self._pos_slider.sliderMoved.connect(self._seek)
        ctrl_layout.addWidget(self._pos_slider)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_play = QPushButton("Pause")
        self._btn_play.setFixedWidth(76)
        self._btn_play.setProperty("class", "secondary")
        self._btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_play.clicked.connect(self._toggle_play)
        btn_row.addWidget(self._btn_play)

        self._btn_mute = QPushButton("Mute")
        self._btn_mute.setProperty("class", "secondary")
        self._btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mute.setToolTip("Mute / unmute")
        self._btn_mute.clicked.connect(self._toggle_mute)
        btn_row.addWidget(self._btn_mute)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        btn_row.addWidget(self._time_label)

        btn_row.addStretch()

        self._btn_open = QPushButton("Open External")
        self._btn_open.setProperty("class", "secondary")
        self._btn_open.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.clicked.connect(self._open_external)
        btn_row.addWidget(self._btn_open)

        self._btn_edit = QPushButton("Edit")
        self._btn_edit.setProperty("class", "secondary")
        self._btn_edit.setMinimumWidth(60)
        self._btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_edit.setVisible(False)
        self._btn_edit.clicked.connect(self.edit_requested.emit)
        btn_row.addWidget(self._btn_edit)

        ctrl_layout.addLayout(btn_row)

        # Trim row
        trim_row = QHBoxLayout()
        trim_row.setSpacing(8)

        trim_lbl = QLabel("TRIM")
        trim_lbl.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        trim_row.addWidget(trim_lbl)

        self._trim_start = QSlider(Qt.Orientation.Horizontal)
        self._trim_start.setRange(0, 1000)
        self._trim_start.setValue(0)
        self._trim_start.setStyleSheet(
            f"QSlider::sub-page:horizontal {{ background-color: {C.PINK}; }}"
        )
        trim_row.addWidget(self._trim_start, 1)

        self._trim_range_lbl = QLabel("full clip")
        self._trim_range_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        self._trim_range_lbl.setMinimumWidth(80)
        self._trim_range_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trim_row.addWidget(self._trim_range_lbl)

        self._trim_end = QSlider(Qt.Orientation.Horizontal)
        self._trim_end.setRange(0, 1000)
        self._trim_end.setValue(1000)
        trim_row.addWidget(self._trim_end, 1)

        self._btn_trim = QPushButton("Save Trim")
        self._btn_trim.setMinimumWidth(95)
        self._btn_trim.setStyleSheet(
            f"QPushButton {{ background-color: {C.LAVENDER}; color: {C.BG};"
            f"border: none; border-radius: 6px; padding: 6px 14px;"
            f"font-weight: 700; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: {_accent_hover()}; }}"
        )
        self._btn_trim.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_trim.clicked.connect(self._save_trim)
        trim_row.addWidget(self._btn_trim)

        self._trim_start.valueChanged.connect(self._update_trim_label)
        self._trim_end.valueChanged.connect(self._update_trim_label)

        ctrl_layout.addLayout(trim_row)
        layout.addWidget(self._controls)

        # Hide controls until a clip is loaded
        self._controls.setVisible(False)
        self._empty.setVisible(True)

    def load_clip(self, path: Path) -> None:
        self._clip_path = path
        self._duration_ms = 0
        self._empty.setVisible(False)
        self._controls.setVisible(True)
        self._btn_edit.setVisible(True)
        self._trim_start.setValue(0)
        self._trim_end.setValue(1000)
        self._trim_range_lbl.setText("full clip")

        self._start_audio(path, 0.0)
        if self._media_player:
            self._media_player.setSource(QUrl.fromLocalFile(str(path)))
            self._media_player.play()
        self._btn_play.setText("Pause")
        self.clip_started.emit()

    def play(self) -> None:
        if self._clip_path:
            pos = (self._media_player.position() / 1000.0) if self._media_player else 0.0
            self._start_audio(self._clip_path, pos)
        if self._media_player:
            self._media_player.play()
        self._btn_play.setText("Pause")
        self.clip_started.emit()
        # Light mode: randomly schedule fake lag events during playback
        self._schedule_light_mode_lag()

    def _start_audio(self, path: Path, pos_sec: float) -> None:
        self._stop_audio()
        if self._muted:
            return
        try:
            self._audio_proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-ss", f"{pos_sec:.2f}", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass  # ffplay not installed

    def _stop_audio(self) -> None:
        if self._audio_proc and self._audio_proc.poll() is None:
            self._audio_proc.terminate()
        self._audio_proc = None

    def __del__(self) -> None:
        self._stop_audio()

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        self._btn_mute.setText("Unmute" if self._muted else "Mute")
        if self._muted:
            self._stop_audio()
        elif self._clip_path and self._media_player:
            pos = self._media_player.position() / 1000.0
            self._start_audio(self._clip_path, pos)


    def _toggle_play(self) -> None:
        if not self._media_player:
            self._open_external()
            return
        from PyQt6.QtMultimedia import QMediaPlayer
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_audio()
            self._media_player.pause()
            self._btn_play.setText("Play")
            self.clip_paused.emit()
        else:
            pos = self._media_player.position() / 1000.0
            if self._clip_path:
                self._start_audio(self._clip_path, pos)
            self._media_player.play()
            self._btn_play.setText("Pause")
            self.clip_started.emit()

    def _schedule_light_mode_lag(self) -> None:
        """In light mode, randomly schedule a fake freeze during playback."""
        try:
            from . import themes as _t
            if not _t.LIGHT_MODE_ACTIVE:
                return
        except Exception:
            return
        if random.random() > 0.55:  # ~55% chance of lag per playback session
            return
        delay_ms = random.randint(4_000, 18_000)  # freeze somewhere 4-18s in
        self._lag_timer.start(delay_ms)

    def _do_light_mode_lag(self) -> None:
        """Freeze the player for 0.4-1.2s, then resume — simulates buffering."""
        if not self._media_player:
            return
        from PyQt6.QtMultimedia import QMediaPlayer
        if self._media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        self._media_player.pause()
        freeze_ms = random.randint(400, 1200)
        QTimer.singleShot(freeze_ms, self._resume_after_lag)

    def _resume_after_lag(self) -> None:
        if not self._media_player:
            return
        from PyQt6.QtMultimedia import QMediaPlayer
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._media_player.play()
            # Maybe lag again
            self._schedule_light_mode_lag()

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True

    def _on_slider_released(self) -> None:
        self._slider_dragging = False
        value = self._pos_slider.value()
        if self._media_player and self._duration_ms > 0:
            pos_ms = int((value / 1000) * self._duration_ms)
            self._media_player.setPosition(pos_ms)
            # Restart audio in sync with new position
            if self._clip_path:
                self._start_audio(self._clip_path, pos_ms / 1000.0)

    def _seek(self, value: int) -> None:
        # Only updates video while dragging; audio restarts on release
        if self._media_player and self._duration_ms > 0:
            self._media_player.setPosition(int((value / 1000) * self._duration_ms))

    def _on_position(self, pos_ms: int) -> None:
        if not self._slider_dragging and self._duration_ms > 0:
            self._pos_slider.blockSignals(True)
            self._pos_slider.setValue(int((pos_ms / self._duration_ms) * 1000))
            self._pos_slider.blockSignals(False)
        self._time_label.setText(
            f"{self._fmt(pos_ms)} / {self._fmt(self._duration_ms)}"
        )

    def _on_duration(self, dur_ms: int) -> None:
        self._duration_ms = dur_ms
        self._update_trim_label()

    def _update_trim_label(self) -> None:
        if self._duration_ms <= 0:
            return
        s = (self._trim_start.value() / 1000) * (self._duration_ms / 1000)
        e = (self._trim_end.value() / 1000) * (self._duration_ms / 1000)
        self._trim_range_lbl.setText(f"{s:.1f}s \u2014 {e:.1f}s")

    def _save_trim(self) -> None:
        if not self._clip_path or self._duration_ms <= 0:
            return

        start_s = (self._trim_start.value() / 1000) * (self._duration_ms / 1000)
        end_s   = (self._trim_end.value() / 1000) * (self._duration_ms / 1000)

        if end_s <= start_s:
            QMessageBox.warning(self, "Trim", "End must be after start.")
            return

        out = self._clip_path.with_name(
            self._clip_path.stem + "_trimmed" + self._clip_path.suffix
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(self._clip_path),
            "-ss", f"{start_s:.2f}",
            "-to", f"{end_s:.2f}",
            "-c", "copy",
            "-movflags", "+faststart",
            str(out),
        ]

        self._btn_trim.setEnabled(False)
        self._btn_trim.setText("trimming…")
        self._trim_worker = _TrimWorker(cmd, out, self)
        self._trim_worker.done.connect(self._on_trim_done)
        self._trim_worker.start()

    def _on_trim_done(self, success: bool, msg: str) -> None:
        self._btn_trim.setEnabled(True)
        self._btn_trim.setText("Save Trim")
        if success:
            QMessageBox.information(self, CAT, f"Trimmed clip saved!\n{msg}")
        else:
            QMessageBox.warning(self, "Trim Failed", msg)

    def _open_external(self) -> None:
        if self._clip_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._clip_path)))

    def closeEvent(self, event) -> None:
        self._stop_audio()
        worker = getattr(self, "_trim_worker", None)
        if worker is not None and worker.isRunning():
            worker.quit()
            worker.wait(3000)
        super().closeEvent(event)

    @staticmethod
    def _fmt(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ #
# Clip browser
# ------------------------------------------------------------------ #

class ClipBrowser(QWidget):
    """Split view: clip table + player/trimmer."""

    # Emitted whenever the clips page cat state changes — main window syncs logo
    cat_state_changed = pyqtSignal(str)
    # Emitted when a clip is selected: (path, duration_seconds)
    clip_selected = pyqtSignal(object, int)

    # Vibe cycle: cat states to cycle through while a clip is playing
    _VIBE_CYCLE = ["vibe_1", "vibe_2", "vibe_3", "vibe_2", "vibe_1"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C.BG};")
        self._prober: _DurationProber | None = None
        self._clip_paths: list[Path] = []
        self._cat_state = "sleepy"      # current cat state for this page
        self._vibe_idx = 0
        self._is_vibing = False

        # Vibe timer — advances the vibe cycle while a clip plays
        self._vibe_timer = QTimer(self)
        self._vibe_timer.setInterval(750)  # ~80 BPM — close enough to feel musical
        self._vibe_timer.timeout.connect(self._vibe_tick)

        self._build_ui()
        self._refresh()

        # Wire player signals after _build_ui creates self._player
        self._player.clip_started.connect(self._on_clip_started)
        self._player.clip_paused.connect(self._on_clip_paused)
        self._player.edit_requested.connect(self._enter_editor)
        self._editor.back_requested.connect(self._exit_editor)
        self._editor.export_done.connect(self._on_export_done)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 10)
        root.setSpacing(10)

        # Header
        header = QHBoxLayout()
        self._title = QLabel(f"{CAT}  clips")
        self._title.setWordWrap(False)
        self._title.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 14px; font-weight: 700; {CAT_FONT}"
        )
        header.addWidget(self._title)
        header.addStretch()

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setProperty("class", "secondary")
        self._btn_refresh.setMinimumWidth(80)
        self._btn_refresh.clicked.connect(self._refresh)
        header.addWidget(self._btn_refresh)
        root.addLayout(header)

        # Empty state
        self._empty = QWidget()
        el = QVBoxLayout(self._empty)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cat_lbl = QLabel(random.choice(CATS))
        cat_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cat_lbl.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 28px; font-weight: 700; {CAT_FONT}"
        )
        el.addWidget(cat_lbl)
        self._empty_hint_label = QLabel("start recording first, then save a clip to see it here")
        self._empty_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint_label.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 12px;")
        self._empty_hint_label.setWordWrap(True)
        el.addWidget(self._empty_hint_label)
        root.addWidget(self._empty)

        # Splitter: table | player
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(5)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Clip", "Date", "Dur", "Size"])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setMinimumWidth(260)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(1, 120)   # Date
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(2, 45)    # Dur
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(3, 55)    # Size

        self._table.clicked.connect(self._on_select)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        self._splitter.addWidget(self._table)
        self._splitter.setCollapsible(0, False)

        from .editor import _EditorPanel
        self._player = _PlayerPanel()
        self._editor = _EditorPanel()
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(self._player)
        self._right_stack.addWidget(self._editor)
        self._right_stack.setMinimumWidth(400)
        self._splitter.addWidget(self._right_stack)
        self._splitter.setCollapsible(1, False)
        self._splitter.setSizes([380, 400])

        root.addWidget(self._splitter, 1)

        # Status bar
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        root.addWidget(self._status)

    # ── Data ──

    def _refresh(self) -> None:
        try:
            from ..config import load_config
            save_dir = load_config().general.save_dir
        except Exception:
            save_dir = Path.home() / "Videos" / "mitten"

        clips = sorted(
            save_dir.glob("mitten_*.mp4"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        self._clip_paths = clips

        if not clips:
            self._empty.show()
            self._splitter.hide()
            self._status.setText("")
            # Light mode: replace empty state hint text with abuse
            try:
                from .themes import LIGHT_MODE_ACTIVE, get_abuse
                if LIGHT_MODE_ACTIVE:
                    self._empty_hint_label.setText(get_abuse(include_name=False))
                else:
                    self._empty_hint_label.setText("start recording first, then save a clip to see it here")
            except Exception:
                pass
            # Update title with current cat state (don't emit — just visual update)
            self._emit_cat(self._cat_state)
            return

        self._empty.hide()
        self._splitter.show()
        # Update title with current cat state
        self._emit_cat(self._cat_state)

        self._table.setRowCount(len(clips))
        probe_jobs: list[tuple[int, Path]] = []
        total_bytes = 0

        for i, clip in enumerate(clips):
            meta = self._load_meta(clip)

            name_item = QTableWidgetItem(self._clip_display_name(clip, meta))
            tooltip = str(clip)
            if meta:
                tooltip = self._meta_tooltip(meta) + f"\n\n{clip}"
            name_item.setToolTip(tooltip)
            self._table.setItem(i, 0, name_item)

            self._table.setItem(i, 1, QTableWidgetItem(self._parse_date(clip.name, meta)))

            # Use sidecar duration if available — skip ffprobe for this row
            if meta and meta.get("duration_s") is not None:
                dur_s = int(round(meta["duration_s"]))
                dur_item = QTableWidgetItem(f"{dur_s}s" if dur_s > 0 else "?")
            else:
                dur_item = QTableWidgetItem("\u2026")
                probe_jobs.append((i, clip))
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 2, dur_item)

            try:
                size = clip.stat().st_size
                total_bytes += size
                size_str = f"{size / (1024 * 1024):.1f}M"
            except OSError:
                size_str = "?"
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(i, 3, size_item)

        self._status.setText(
            f"{len(clips)} clips  \u00b7  {total_bytes / (1024 * 1024):.1f} MB total"
        )

        if self._prober and self._prober.isRunning():
            self._prober.terminate()
            self._prober.wait(2000)

        self._prober = _DurationProber(probe_jobs, self)
        self._prober.duration_ready.connect(self._on_duration)
        self._prober.start()

    def _on_duration(self, row: int, seconds: int) -> None:
        if row < self._table.rowCount():
            item = self._table.item(row, 2)
            if item:
                item.setText(f"{seconds}s" if seconds > 0 else "?")

    @staticmethod
    def _load_meta(path: Path) -> dict | None:
        try:
            mp = path.with_suffix(".json")
            if mp.exists():
                return json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    @staticmethod
    def _clip_display_name(clip: Path, meta: dict | None = None) -> str:
        """Return a human-readable clip name, with game prefix if known."""
        stem = clip.stem.replace("mitten_", "").replace("session_", "")
        try:
            dt = datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S")
            time_str = dt.strftime("%H:%M:%S")
        except ValueError:
            time_str = stem
        if meta:
            game = meta.get("game")
            if game:
                return f"{game}  ·  {time_str}"
            if meta.get("clip_type") == "session":
                return f"session  ·  {time_str}"
        return time_str

    @staticmethod
    def _meta_tooltip(meta: dict) -> str:
        lines = []
        if meta.get("game"):
            lines.append(f"Game: {meta['game']}")
        ctype = meta.get("clip_type", "clip")
        mode  = meta.get("mode", "")
        lines.append(f"Type: {ctype}" + (f"  ·  Mode: {mode}" if mode else ""))
        wm   = "yes" if meta.get("watermarked") else "no"
        comp = "yes" if meta.get("compressed") else "no"
        lines.append(f"Watermarked: {wm}  ·  Compressed: {comp}")
        if meta.get("codec"):
            lines.append(f"Codec: {meta['codec']}")
        lines.append("Saved: " + ("manually" if meta.get("saved_manually") else "auto"))
        if meta.get("mitten_version"):
            lines.append(f"Version: {meta['mitten_version']}")
        return "\n".join(lines)

    def _parse_date(self, filename: str, meta: dict | None = None) -> str:
        if meta and meta.get("saved_at"):
            try:
                dt = datetime.fromisoformat(
                    meta["saved_at"].replace("Z", "+00:00")
                ).astimezone()
                today = datetime.now().date()
                time_str = dt.strftime("%-I:%M %p")
                if dt.date() == today:
                    return f"Today  {time_str}"
                if (today - dt.date()).days == 1:
                    return f"Yesterday  {time_str}"
                return dt.strftime("%b %-d  ") + time_str
            except Exception:
                pass
        try:
            stem = filename.replace("mitten_", "").replace("session_", "").replace(".mp4", "")
            dt = datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S")
            today = datetime.now().date()
            time_str = dt.strftime("%-I:%M %p")
            if dt.date() == today:
                return f"Today  {time_str}"
            if (today - dt.date()).days == 1:
                return f"Yesterday  {time_str}"
            return dt.strftime("%b %-d  ") + time_str
        except (ValueError, OSError):
            return "?"

    # ── Interactions ──

    def _on_select(self, index) -> None:
        if self._right_stack.currentIndex() == 1:
            self._exit_editor()
        row = index.row()
        if 0 <= row < len(self._clip_paths):
            path = self._clip_paths[row]
            self._player.load_clip(path)
            dur_item = self._table.item(row, 2)
            dur = 0
            if dur_item:
                try:
                    dur = int(dur_item.text().replace("s", "").replace("?", "0") or 0)
                except ValueError:
                    pass
            self.clip_selected.emit(path, dur)

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._clip_paths):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._clip_paths[row])))

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._clip_paths):
            return
        clip = self._clip_paths[row]

        menu = QMenu(self)

        act_play = QAction("\u25b6  Play in App", self)
        act_play.triggered.connect(lambda: self._player.load_clip(clip))
        menu.addAction(act_play)

        act_open = QAction("Open External", self)
        act_open.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(clip)))
        )
        menu.addAction(act_open)

        act_folder = QAction("Open Folder", self)
        act_folder.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(clip.parent)))
        )
        menu.addAction(act_folder)

        menu.addSeparator()

        act_del = QAction("Delete", self)
        act_del.triggered.connect(lambda: self._delete_clip(clip))
        menu.addAction(act_del)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _delete_clip(self, clip: Path) -> None:
        reply = QMessageBox.question(
            self, "Delete clip?",
            f"Delete {clip.name}?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                clip.unlink()
                sidecar = clip.with_suffix(".json")
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                pass
            self._refresh()

    # ── Editor ──

    def _enter_editor(self) -> None:
        if not self._player._clip_path:
            return
        if getattr(self._player, '_trim_worker', None) and self._player._trim_worker.isRunning():
            QMessageBox.warning(self, "Trim in progress",
                                "Please wait for the trim to finish before editing.")
            return
        if hasattr(self._player, '_lag_timer'):
            self._player._lag_timer.stop()
        self._player._stop_audio()
        self._player._media_player.pause()
        dur_ms = int(self._player._media_player.duration()) if self._player._media_player else 0
        self._editor.load_clip(self._player._clip_path, dur_ms)
        self._right_stack.setCurrentIndex(1)

    def _exit_editor(self) -> None:
        self._editor._stop_preview()
        self._right_stack.setCurrentIndex(0)

    def _on_export_done(self, out_path: object) -> None:
        if isinstance(out_path, Path):
            QMessageBox.information(self, "Export complete", f"Saved to:\n{out_path}")
            self._refresh()
        else:
            QMessageBox.critical(self, "Export failed", str(out_path))

    # ── Cat vibe system ──

    def _emit_cat(self, state: str) -> None:
        """Update title label + emit signal so sidebar syncs."""
        self._cat_state = state
        try:
            from .themes import get_state_cat, LIGHT_MODE_ACTIVE, get_light_mode_cat
            cat = get_state_cat(state) if not LIGHT_MODE_ACTIVE else get_light_mode_cat()
        except Exception:
            cat = "~( ^.x.^)>"
        count = len(self._clip_paths)
        if count == 0:
            self._title.setText(f"{cat}  no clips yet")
        else:
            self._title.setText(f"{cat}  {count} clips")
        self.cat_state_changed.emit(state)

    def _on_clip_started(self) -> None:
        """Clip started playing — run startled → settle → vibe sequence."""
        self._vibe_timer.stop()
        self._is_vibing = False
        self._emit_cat("startled")
        # After brief startle, start settling into a vibe
        QTimer.singleShot(350, lambda: self._emit_cat("vibe_1"))
        QTimer.singleShot(700, self._start_vibe)

    def _start_vibe(self) -> None:
        self._vibe_idx = 1  # start at index 1 (vibe_2) since we already showed vibe_1
        self._is_vibing = True
        self._vibe_timer.start()

    def _vibe_tick(self) -> None:
        self._vibe_idx = (self._vibe_idx + 1) % len(self._VIBE_CYCLE)
        self._emit_cat(self._VIBE_CYCLE[self._vibe_idx])

    def _on_clip_paused(self) -> None:
        """Clip paused — settle back to sleepy."""
        self._vibe_timer.stop()
        self._is_vibing = False
        self._emit_cat("sleepy")

    # ── Lifecycle ──

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        # Start sleepy when page opens (if not already vibing)
        if not self._is_vibing:
            self._emit_cat("sleepy")

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._player._stop_audio()
        if hasattr(self, '_editor'):
            self._editor._stop_preview()
        self._vibe_timer.stop()
        self._is_vibing = False
        # Signal idle so sidebar restores to app state
        self.cat_state_changed.emit("idle")

    def closeEvent(self, event) -> None:
        self._player._stop_audio()
        if hasattr(self, '_editor'):
            self._editor._stop_preview()
        self._vibe_timer.stop()
        super().closeEvent(event)
