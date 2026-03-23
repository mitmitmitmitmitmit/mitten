"""
Clip editor panel — timeline, overlays, export.
Integrated into the clips tab via QStackedWidget.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QUrl, pyqtSignal, QPointF, QRectF,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QPolygonF,
)
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .resources import C, _accent_hover
from .editor_model import EditorModel, OverlayItem, BUILTIN_SFX, SFX_DISPLAY_NAMES
from .editor_export import _ExportWorker, _make_output_path

log = logging.getLogger(__name__)

# ── Position preset helpers ───────────────────────────────────────────────────

_POS_PRESETS: list[tuple[str, float, float]] = [
    ("Center",       0.5,  0.5),
    ("Top-Left",     0.02, 0.02),
    ("Top-Right",    0.78, 0.02),
    ("Bottom-Left",  0.02, 0.88),
    ("Bottom-Right", 0.78, 0.88),
    ("Top-Center",   0.35, 0.02),
    ("Bottom-Center",0.35, 0.88),
]


def _pos_from_preset(label: str) -> tuple[float, float]:
    for name, x, y in _POS_PRESETS:
        if name == label:
            return x, y
    return 0.5, 0.5


# ── Timeline widget ───────────────────────────────────────────────────────────

class _TimelineWidget(QWidget):
    """Custom-painted scrubber timeline with overlay markers."""

    seek_requested = pyqtSignal(float)  # time in seconds

    _TRACK_H    = 8
    _MARKER_H   = 14
    _PLAYHEAD_W = 2
    _DIAMOND_SZ = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._duration_s: float = 0.0
        self._position_s: float = 0.0
        self._overlays: list[OverlayItem] = []

    def set_duration(self, d: float) -> None:
        self._duration_s = max(0.0, d)
        self.update()

    def set_position(self, t: float) -> None:
        self._position_s = max(0.0, t)
        self.update()

    def set_overlays(self, overlays: list[OverlayItem]) -> None:
        self._overlays = list(overlays)
        self.update()

    def _t_to_x(self, t: float) -> float:
        w = self.width() - 20
        if self._duration_s <= 0:
            return 10.0
        frac = max(0.0, min(1.0, t / self._duration_s))
        return 10.0 + frac * w

    def _x_to_t(self, x: float) -> float:
        w = self.width() - 20
        if w <= 0 or self._duration_s <= 0:
            return 0.0
        frac = max(0.0, min(1.0, (x - 10.0) / w))
        return frac * self._duration_s

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W = self.width()
        track_y = 20.0
        track_x1 = 10.0
        track_x2 = W - 10.0
        track_w  = track_x2 - track_x1

        # Background track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(C.OVERLAY)))
        p.drawRoundedRect(
            QRectF(track_x1, track_y - self._TRACK_H / 2, track_w, self._TRACK_H),
            4, 4,
        )

        # Progress fill (lavender up to playhead)
        if self._duration_s > 0 and self._position_s > 0:
            fill_w = max(0.0, self._t_to_x(self._position_s) - track_x1)
            p.setBrush(QBrush(QColor(C.LAVENDER)))
            p.drawRoundedRect(
                QRectF(track_x1, track_y - self._TRACK_H / 2, fill_w, self._TRACK_H),
                4, 4,
            )

        # Overlay markers
        for o in self._overlays:
            if o.kind == "text":
                color = QColor(C.BLUE)
            elif o.kind == "sfx":
                color = QColor(C.GREEN)
            else:
                color = QColor(C.ORANGE)
            mx = self._t_to_x(o.timestamp_s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawRect(QRectF(mx - 2, track_y - self._MARKER_H / 2, 4, self._MARKER_H))

        # Playhead
        ph_x = self._t_to_x(self._position_s)
        p.setPen(QPen(QColor(C.TEXT), self._PLAYHEAD_W))
        p.drawLine(QPointF(ph_x, 4), QPointF(ph_x, 36))

        # Playhead diamond
        half = self._DIAMOND_SZ / 2
        diamond = QPolygonF([
            QPointF(ph_x,        track_y - half - 2),
            QPointF(ph_x + half, track_y),
            QPointF(ph_x,        track_y + half + 2),
            QPointF(ph_x - half, track_y),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(C.LAVENDER)))
        p.drawPolygon(diamond)

        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            t = self._x_to_t(event.position().x())
            self.seek_requested.emit(t)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            t = self._x_to_t(event.position().x())
            self.seek_requested.emit(t)


# ── Overlay list row widget ───────────────────────────────────────────────────

class _OverlayRowWidget(QWidget):
    remove_clicked = pyqtSignal(int)

    def __init__(self, index: int, overlay: OverlayItem, parent=None) -> None:
        super().__init__(parent)
        self._index = index
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(6)

        # Color dot
        dot = QLabel()
        dot.setFixedSize(10, 10)
        if overlay.kind == "text":
            color = C.BLUE
        elif overlay.kind == "sfx":
            color = C.GREEN
        else:
            color = C.ORANGE
        dot.setStyleSheet(
            f"background-color: {color}; border-radius: 5px;"
        )
        layout.addWidget(dot)

        # Description label
        desc = QLabel(overlay.describe())
        desc.setStyleSheet(f"color: {C.TEXT}; font-size: 11px;")
        desc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(desc)

        # Remove button
        btn_rm = QPushButton("✕")
        btn_rm.setFixedSize(22, 22)
        btn_rm.setStyleSheet(
            f"QPushButton {{ background: {C.OVERLAY}; color: {C.SUBTEXT};"
            f"border: none; border-radius: 4px; font-size: 10px; padding: 0; }}"
            f"QPushButton:hover {{ background: {C.PINK}; color: {C.BG}; }}"
        )
        btn_rm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rm.clicked.connect(lambda: self.remove_clicked.emit(self._index))
        layout.addWidget(btn_rm)


# ── Main editor panel ─────────────────────────────────────────────────────────

class _EditorPanel(QWidget):
    """Full overlay editor with timeline, add/remove overlays, and export."""

    back_requested = pyqtSignal()
    export_done    = pyqtSignal(object)   # Path on success

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C.BG};")
        self._model: EditorModel | None = None
        self._export_worker: _ExportWorker | None = None
        self._sfx_preview_proc: subprocess.Popen | None = None
        self._media_player = None
        self._video_widget = None
        self._selected_color = "#ffffff"

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── Video preview ──────────────────────────────────────────────────
        self._video_area = QWidget()
        self._video_area.setMinimumHeight(200)
        self._video_area.setStyleSheet(
            f"background-color: {C.BG}; border-radius: 6px 6px 0 0;"
        )
        from PyQt6.QtWidgets import QGridLayout
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
            # No QAudioOutput — audio via ffplay
            self._media_player = QMediaPlayer(self)
            self._media_player.setVideoOutput(self._video_widget)
            self._media_player.positionChanged.connect(self._on_player_position)
            vid_grid.addWidget(self._video_widget, 0, 0)
        except ImportError:
            placeholder = QLabel("video preview unavailable")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 13px;")
            vid_grid.addWidget(placeholder, 0, 0)

        layout.addWidget(self._video_area, 1)

        # ── Controls bar container ─────────────────────────────────────────
        ctrl_container = QWidget()
        ctrl_container.setStyleSheet(
            f"background-color: {C.SURFACE}; border-radius: 0 0 6px 6px;"
        )
        ctrl_layout = QVBoxLayout(ctrl_container)
        ctrl_layout.setContentsMargins(10, 6, 10, 8)
        ctrl_layout.setSpacing(4)

        # ── Timeline ──────────────────────────────────────────────────────
        self._timeline = _TimelineWidget()
        self._timeline.seek_requested.connect(self._on_seek)
        ctrl_layout.addWidget(self._timeline)

        # ── Play/pause row ────────────────────────────────────────────────
        play_row = QHBoxLayout()
        play_row.setSpacing(8)
        self._btn_play_pause = QPushButton("Play")
        self._btn_play_pause.setProperty("class", "secondary")
        self._btn_play_pause.setFixedWidth(70)
        self._btn_play_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_play_pause.clicked.connect(self._toggle_play)
        play_row.addWidget(self._btn_play_pause)
        self._time_lbl = QLabel("0:00 / 0:00")
        self._time_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        play_row.addWidget(self._time_lbl)
        play_row.addStretch()
        ctrl_layout.addLayout(play_row)

        layout.addWidget(ctrl_container)

        # ── Overlay list ──────────────────────────────────────────────────
        self._overlay_list = QListWidget()
        self._overlay_list.setFixedHeight(110)
        self._overlay_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._overlay_list.setStyleSheet(
            f"QListWidget {{ background: {C.SURFACE}; border: 1px solid {C.BORDER};"
            f"border-radius: 4px; }}"
        )
        layout.addWidget(self._overlay_list)

        # ── Add overlay row ───────────────────────────────────────────────
        add_row = QHBoxLayout()
        add_row.setSpacing(6)

        self._type_combo = QComboBox()
        self._type_combo.addItems(["Text", "SFX", "Image"])
        self._type_combo.setFixedWidth(80)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        add_row.addWidget(self._type_combo)

        add_row.addWidget(QLabel("at"))

        self._ts_spin = QDoubleSpinBox()
        self._ts_spin.setRange(0.0, 9999.0)
        self._ts_spin.setSingleStep(0.5)
        self._ts_spin.setDecimals(1)
        self._ts_spin.setFixedWidth(75)
        self._ts_spin.setSuffix("s")
        add_row.addWidget(self._ts_spin)

        btn_add = QPushButton("+ Add")
        btn_add.setFixedWidth(70)
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._on_add_overlay)
        add_row.addWidget(btn_add)
        add_row.addStretch()

        layout.addLayout(add_row)

        # ── Config stack ──────────────────────────────────────────────────
        self._config_stack = QStackedWidget()
        self._config_stack.setFixedHeight(56)

        # Page 0: text config
        text_page = QWidget()
        tp_row = QHBoxLayout(text_page)
        tp_row.setContentsMargins(0, 0, 0, 0)
        tp_row.setSpacing(6)
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("Overlay text…")
        tp_row.addWidget(self._text_edit, 2)
        self._font_spin = QSpinBox()
        self._font_spin.setRange(10, 120)
        self._font_spin.setValue(28)
        self._font_spin.setFixedWidth(60)
        self._font_spin.setSuffix("px")
        tp_row.addWidget(self._font_spin)
        self._btn_color = QPushButton("Color")
        self._btn_color.setFixedWidth(60)
        self._btn_color.setProperty("class", "secondary")
        self._btn_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_color.clicked.connect(self._pick_color)
        tp_row.addWidget(self._btn_color)
        self._text_pos_combo = QComboBox()
        for name, _, _ in _POS_PRESETS:
            self._text_pos_combo.addItem(name)
        self._text_pos_combo.setFixedWidth(120)
        tp_row.addWidget(self._text_pos_combo)
        self._text_dur_spin = QDoubleSpinBox()
        self._text_dur_spin.setRange(0.1, 9999.0)
        self._text_dur_spin.setValue(3.0)
        self._text_dur_spin.setDecimals(1)
        self._text_dur_spin.setFixedWidth(70)
        self._text_dur_spin.setSuffix("s dur")
        tp_row.addWidget(self._text_dur_spin)
        self._config_stack.addWidget(text_page)

        # Page 1: sfx config
        sfx_page = QWidget()
        sp_row = QHBoxLayout(sfx_page)
        sp_row.setContentsMargins(0, 0, 0, 0)
        sp_row.setSpacing(6)
        self._sfx_combo = QComboBox()
        for key, display in SFX_DISPLAY_NAMES.items():
            self._sfx_combo.addItem(display, userData=key)
        self._sfx_combo.setFixedWidth(120)
        sp_row.addWidget(self._sfx_combo)
        self._vol_spin = QDoubleSpinBox()
        self._vol_spin.setRange(0.0, 2.0)
        self._vol_spin.setValue(1.0)
        self._vol_spin.setSingleStep(0.1)
        self._vol_spin.setDecimals(1)
        self._vol_spin.setFixedWidth(70)
        self._vol_spin.setPrefix("vol ")
        sp_row.addWidget(self._vol_spin)
        btn_sfx_preview = QPushButton("▶ Preview")
        btn_sfx_preview.setProperty("class", "secondary")
        btn_sfx_preview.setFixedWidth(80)
        btn_sfx_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sfx_preview.clicked.connect(self._preview_sfx)
        sp_row.addWidget(btn_sfx_preview)
        sp_row.addStretch()
        self._config_stack.addWidget(sfx_page)

        # Page 2: image config
        img_page = QWidget()
        ip_row = QHBoxLayout(img_page)
        ip_row.setContentsMargins(0, 0, 0, 0)
        ip_row.setSpacing(6)
        btn_browse = QPushButton("Browse PNG…")
        btn_browse.setProperty("class", "secondary")
        btn_browse.setFixedWidth(100)
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._browse_image)
        ip_row.addWidget(btn_browse)
        self._img_name_lbl = QLabel("(no file)")
        self._img_name_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
        self._img_name_lbl.setMaximumWidth(150)
        ip_row.addWidget(self._img_name_lbl)
        self._img_scale_spin = QDoubleSpinBox()
        self._img_scale_spin.setRange(0.05, 1.0)
        self._img_scale_spin.setValue(0.25)
        self._img_scale_spin.setSingleStep(0.05)
        self._img_scale_spin.setDecimals(2)
        self._img_scale_spin.setFixedWidth(70)
        self._img_scale_spin.setPrefix("scale ")
        ip_row.addWidget(self._img_scale_spin)
        self._img_pos_combo = QComboBox()
        for name, _, _ in _POS_PRESETS:
            self._img_pos_combo.addItem(name)
        self._img_pos_combo.setFixedWidth(120)
        ip_row.addWidget(self._img_pos_combo)
        self._img_dur_spin = QDoubleSpinBox()
        self._img_dur_spin.setRange(0.1, 9999.0)
        self._img_dur_spin.setValue(3.0)
        self._img_dur_spin.setDecimals(1)
        self._img_dur_spin.setFixedWidth(70)
        self._img_dur_spin.setSuffix("s dur")
        ip_row.addWidget(self._img_dur_spin)
        self._img_path: str = ""
        self._config_stack.addWidget(img_page)

        layout.addWidget(self._config_stack)

        # ── Export row ────────────────────────────────────────────────────
        export_row = QHBoxLayout()
        export_row.setSpacing(8)

        btn_back = QPushButton("← Back")
        btn_back.setProperty("class", "secondary")
        btn_back.setMinimumWidth(70)
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.clicked.connect(self.back_requested.emit)
        export_row.addWidget(btn_back)

        export_row.addStretch()

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setFixedWidth(120)
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)
        export_row.addWidget(self._progress)

        self._btn_export = QPushButton("Export Clip")
        self._btn_export.setMinimumWidth(100)
        self._btn_export.setStyleSheet(
            f"QPushButton {{ background-color: {C.LAVENDER}; color: {C.BG};"
            f"border: none; border-radius: 6px; padding: 6px 14px;"
            f"font-weight: 700; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: {_accent_hover()}; }}"
            f"QPushButton:disabled {{ background-color: {C.OVERLAY}; color: {C.GRAY}; }}"
        )
        self._btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_export.clicked.connect(self._on_export)
        export_row.addWidget(self._btn_export)

        layout.addLayout(export_row)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_clip(self, path: Path, duration_ms: int) -> None:
        """Load a clip into the editor. Restores .edits.json if present."""
        duration_s = max(0.0, duration_ms / 1000.0)
        self._model = EditorModel(path, duration_s)
        self._model.load()

        # Update timestamp max
        self._ts_spin.setMaximum(max(1.0, duration_s))

        # Timeline
        self._timeline.set_duration(duration_s)
        self._timeline.set_position(0.0)
        self._timeline.set_overlays(self._model.overlays)

        # Media player
        if self._media_player:
            self._media_player.setSource(QUrl.fromLocalFile(str(path)))
            self._media_player.play()
            self._btn_play_pause.setText("Pause")

        self._time_lbl.setText(f"0:00 / {self._fmt_ms(duration_ms)}")
        self._refresh_overlay_list()

    def _stop_preview(self) -> None:
        """Stop the media player and any SFX preview subprocess."""
        if self._media_player:
            self._media_player.stop()
        self._btn_play_pause.setText("Play")
        self._stop_sfx_preview()

    def _stop_sfx_preview(self) -> None:
        if self._sfx_preview_proc and self._sfx_preview_proc.poll() is None:
            self._sfx_preview_proc.terminate()
        self._sfx_preview_proc = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _refresh_overlay_list(self) -> None:
        self._overlay_list.clear()
        if self._model is None:
            return
        for i, overlay in enumerate(self._model.overlays):
            item = QListWidgetItem(self._overlay_list)
            row_widget = _OverlayRowWidget(i, overlay)
            row_widget.remove_clicked.connect(self._on_remove_overlay)
            item.setSizeHint(row_widget.sizeHint())
            self._overlay_list.addItem(item)
            self._overlay_list.setItemWidget(item, row_widget)

        if self._model is not None:
            self._timeline.set_overlays(self._model.overlays)

    def _on_player_position(self, pos_ms: int) -> None:
        if self._model is None:
            return
        t = pos_ms / 1000.0
        self._timeline.set_position(t)
        if self._model.duration_s > 0:
            dur_ms = int(self._model.duration_s * 1000)
        else:
            dur_ms = 0
        self._time_lbl.setText(f"{self._fmt_ms(pos_ms)} / {self._fmt_ms(dur_ms)}")

    def _toggle_play(self) -> None:
        if self._media_player is None:
            return
        try:
            from PyQt6.QtMultimedia import QMediaPlayer as _QMP
            if self._media_player.playbackState() == _QMP.PlaybackState.PlayingState:
                self._media_player.pause()
                self._btn_play_pause.setText("Play")
            else:
                self._media_player.play()
                self._btn_play_pause.setText("Pause")
        except Exception:
            pass

    def _on_seek(self, t: float) -> None:
        if self._media_player:
            self._media_player.setPosition(int(t * 1000))
        self._timeline.set_position(t)
        # Also update timestamp spinbox to match seek position
        self._ts_spin.setValue(round(t, 1))

    def _on_type_changed(self, index: int) -> None:
        self._config_stack.setCurrentIndex(index)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._selected_color), self, "Pick text color")
        if color.isValid():
            self._selected_color = color.name()
            self._btn_color.setStyleSheet(
                f"QPushButton {{ background-color: {self._selected_color};"
                f"color: {'#000000' if color.lightness() > 128 else '#ffffff'};"
                f"border: none; border-radius: 4px; }}"
            )

    def _preview_sfx(self) -> None:
        """Play the selected SFX file via ffplay for a quick preview."""
        self._stop_sfx_preview()
        key = self._sfx_combo.currentData()
        sfx_path = BUILTIN_SFX.get(key)
        if sfx_path is None or not sfx_path.exists():
            QMessageBox.warning(self, "SFX Preview",
                                f"SFX file not found for '{key}'.\n"
                                "Place sfx_*.mp3 files in mitten/assets/ to enable SFX.")
            return
        try:
            self._sfx_preview_proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", str(sfx_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg)",
        )
        if path:
            self._img_path = path
            self._img_name_lbl.setText(Path(path).name[:24])

    def _on_add_overlay(self) -> None:
        if self._model is None:
            return

        kind_idx = self._type_combo.currentIndex()
        t = self._ts_spin.value()

        if kind_idx == 0:  # text
            text = self._text_edit.text().strip()
            if not text:
                QMessageBox.warning(self, "Add Text Overlay", "Please enter some text.")
                return
            x, y = _pos_from_preset(self._text_pos_combo.currentText())
            item = OverlayItem(
                kind="text",
                timestamp_s=t,
                duration_s=self._text_dur_spin.value(),
                text=text,
                font_size=self._font_spin.value(),
                color=self._selected_color,
                x_pct=x,
                y_pct=y,
            )

        elif kind_idx == 1:  # sfx
            key = self._sfx_combo.currentData()
            item = OverlayItem(
                kind="sfx",
                timestamp_s=t,
                duration_s=0.0,
                sfx_name=key,
                volume=self._vol_spin.value(),
            )

        else:  # image
            if not self._img_path:
                QMessageBox.warning(self, "Add Image Overlay", "Please browse for an image file.")
                return
            x, y = _pos_from_preset(self._img_pos_combo.currentText())
            item = OverlayItem(
                kind="image",
                timestamp_s=t,
                duration_s=self._img_dur_spin.value(),
                image_path=self._img_path,
                image_scale=self._img_scale_spin.value(),
                img_x_pct=x,
                img_y_pct=y,
            )

        self._model.add(item)
        self._model.save()
        self._refresh_overlay_list()

    def _on_remove_overlay(self, index: int) -> None:
        if self._model is None:
            return
        self._model.remove(index)
        self._model.save()
        self._refresh_overlay_list()

    def _on_export(self) -> None:
        if self._model is None:
            return
        if self._export_worker and self._export_worker.isRunning():
            return

        out_path = _make_output_path(self._model.clip_path)

        self._btn_export.setEnabled(False)
        self._btn_export.setText("Exporting…")
        self._progress.setVisible(True)

        self._export_worker = _ExportWorker(self._model, out_path, self)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.start()

    def _on_export_done(self, success: bool, result: object) -> None:
        self._btn_export.setEnabled(True)
        self._btn_export.setText("Export Clip")
        self._progress.setVisible(False)

        if success:
            self.export_done.emit(result)
        else:
            QMessageBox.critical(self, "Export Failed", str(result))

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"
