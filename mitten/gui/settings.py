"""
Settings — no internal sidebar (nav lives in MittenMainWindow).
Pages: General (+ Trigger + Notifications), Recording, Compression, Watermark, Games.
Public API: switch_section(idx: int)
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .resources import C


# ------------------------------------------------------------------ #
# Section separator label
# ------------------------------------------------------------------ #

def _sep(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {C.SUBTEXT}; font-size: 10px; font-weight: 600;"
        f"letter-spacing: 1.5px; padding-top: 10px;"
        f"border-top: 1px solid rgba(58,54,80,0.4);"
        f"margin-top: 4px;"
    )
    return lbl


# ------------------------------------------------------------------ #
# Settings widget
# ------------------------------------------------------------------ #

class SettingsDialog(QWidget):
    """MITTEN settings — headless QStackedWidget, nav controlled by main window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C.BG};")
        self._build_ui()
        self._load_config()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._make_general_tab())       # 0
        self._pages.addWidget(self._make_recording_tab())     # 1
        self._pages.addWidget(self._make_compression_tab())   # 2
        self._pages.addWidget(self._make_watermark_tab())     # 3
        self._pages.addWidget(self._make_games_tab())         # 4
        root.addWidget(self._pages, 1)

        # Save bar pinned at bottom
        save_bar = QWidget()
        save_bar.setStyleSheet(
            f"background-color: rgba(22,20,34,0.9);"
            f"border-top: 1px solid rgba(58,54,80,0.3);"
        )
        save_bar.setFixedHeight(52)
        sb_layout = QHBoxLayout(save_bar)
        sb_layout.setContentsMargins(24, 10, 24, 10)

        self._save_status = QLabel("")
        self._save_status.setStyleSheet(f"color: {C.GREEN}; font-size: 12px;")
        sb_layout.addWidget(self._save_status, 1)

        self._btn_save = QPushButton("Save settings")
        self._btn_save.setFixedSize(130, 32)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setStyleSheet(
            f"QPushButton {{ background-color: {C.LAVENDER}; color: {C.BG};"
            f"border: none; border-radius: 6px; font-weight: bold; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: #d4bff7; }}"
            f"QPushButton:pressed {{ background-color: {C.DARK_ACCENT}; }}"
        )
        self._btn_save.clicked.connect(self._on_save)
        sb_layout.addWidget(self._btn_save)

        root.addWidget(save_bar)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def switch_section(self, idx: int) -> None:
        self._pages.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    # Page builders
    # ------------------------------------------------------------------ #

    def _page_wrapper(self) -> tuple[QWidget, QFormLayout]:
        """Scrollable page with centred max-width form."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        inner = QWidget()
        inner.setMaximumWidth(500)
        form = QFormLayout(inner)
        form.setContentsMargins(28, 24, 28, 24)
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch()
        wl.addWidget(inner)
        wl.addStretch()

        scroll.setWidget(wrapper)
        return scroll, form

    # ── General (+ Trigger + Notifications) ──────────────────────────

    def _make_general_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["desktop", "window", "game"])
        form.addRow("Mode", self._mode_combo)

        buf_row = QHBoxLayout()
        self._buffer_slider = QSlider(Qt.Orientation.Horizontal)
        self._buffer_slider.setRange(15, 120)
        self._buffer_slider.setValue(30)
        self._buffer_spin = QSpinBox()
        self._buffer_spin.setRange(15, 120)
        self._buffer_spin.setSuffix("s")
        self._buffer_spin.setValue(30)
        self._buffer_slider.valueChanged.connect(self._buffer_spin.setValue)
        self._buffer_spin.valueChanged.connect(self._buffer_slider.setValue)
        buf_row.addWidget(self._buffer_slider, 1)
        buf_row.addWidget(self._buffer_spin)
        form.addRow("Buffer", buf_row)

        self._fps_combo = QComboBox()
        self._fps_combo.addItems(["24", "30", "60"])
        self._fps_combo.setCurrentText("30")
        form.addRow("Framerate", self._fps_combo)

        self._monitor_combo = QComboBox()
        self._monitor_combo.addItems(["auto"])
        self._monitor_combo.setEditable(True)
        form.addRow("Monitor", self._monitor_combo)

        dir_row = QHBoxLayout()
        self._save_dir_edit = QLineEdit("~/Videos/mitten")
        self._save_dir_browse = QPushButton("…")
        self._save_dir_browse.setProperty("class", "secondary")
        self._save_dir_browse.setFixedWidth(36)
        self._save_dir_browse.clicked.connect(self._browse_save_dir)
        dir_row.addWidget(self._save_dir_edit, 1)
        dir_row.addWidget(self._save_dir_browse)
        form.addRow("Save dir", dir_row)

        # ── Trigger section ──
        form.addRow(_sep("TRIGGER"))

        btn_row = QHBoxLayout()
        self._trigger_btn_label = QLabel("BTN_EXTRA (276)")
        self._trigger_btn_label.setStyleSheet(
            f"color: {C.TEXT}; font-size: 13px; font-weight: bold;"
            f"background-color: rgba(37,35,54,0.6); padding: 6px 12px;"
            f"border-radius: 4px; border: 1px solid {C.BORDER};"
        )
        self._detect_btn = QPushButton("Detect…")
        self._detect_btn.setProperty("class", "secondary")
        self._detect_btn.setMinimumWidth(72)
        self._detect_btn.clicked.connect(self._on_detect_button)
        btn_row.addWidget(self._trigger_btn_label, 1)
        btn_row.addWidget(self._detect_btn)
        form.addRow("Button", btn_row)

        self._cooldown_spin = QDoubleSpinBox()
        self._cooldown_spin.setRange(1.0, 10.0)
        self._cooldown_spin.setSingleStep(0.5)
        self._cooldown_spin.setSuffix("s")
        self._cooldown_spin.setValue(3.0)
        form.addRow("Cooldown", self._cooldown_spin)

        # ── Notifications section ──
        form.addRow(_sep("NOTIFICATIONS"))

        self._notif_enabled = QCheckBox("Enable desktop notifications")
        self._notif_enabled.setChecked(True)
        self._notif_enabled.toggled.connect(self._toggle_notify_fields)
        form.addRow("", self._notif_enabled)

        self._notif_start = QCheckBox("On recording start")
        self._notif_start.setChecked(True)
        form.addRow("", self._notif_start)

        self._notif_save = QCheckBox("On clip saved")
        self._notif_save.setChecked(True)
        form.addRow("", self._notif_save)

        self._notif_error = QCheckBox("On errors")
        self._notif_error.setChecked(True)
        form.addRow("", self._notif_error)

        return page

    # ── Recording (+ Audio) ──────────────────────────────────────────

    def _make_recording_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._quality_combo = QComboBox()
        self._quality_combo.addItems(["very_high", "high", "medium", "low"])
        form.addRow("Quality", self._quality_combo)

        self._cap_codec_combo = QComboBox()
        self._cap_codec_combo.addItems(["hevc", "h264"])
        cap_hint = QLabel("hevc = better compression in RAM buffer")
        cap_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        cap_col = QVBoxLayout()
        cap_col.setSpacing(2)
        cap_col.addWidget(self._cap_codec_combo)
        cap_col.addWidget(cap_hint)
        form.addRow("Capture codec", cap_col)

        # ── Audio section ──
        form.addRow(_sep("AUDIO"))

        self._audio_combo = QComboBox()
        self._audio_combo.addItem("System default", "default")
        self._audio_combo.addItem("(no audio)", "")
        self._audio_combo.setEditable(True)
        audio_hint = QLabel("type a device name, or run: gpu-screen-recorder --list-audio-devices")
        audio_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        audio_col = QVBoxLayout()
        audio_col.setSpacing(2)
        audio_col.addWidget(self._audio_combo)
        audio_col.addWidget(audio_hint)
        form.addRow("Desktop audio", audio_col)

        self._mic_enabled = QCheckBox("Capture microphone")
        self._mic_enabled.toggled.connect(self._toggle_mic)
        form.addRow("", self._mic_enabled)

        self._mic_combo = QComboBox()
        self._mic_combo.addItem("System default", "default")
        self._mic_combo.addItem("(select mic)", "")
        self._mic_combo.setEditable(True)
        self._mic_combo.setEnabled(False)
        mic_hint = QLabel("type a device name, or run: gpu-screen-recorder --list-audio-devices")
        mic_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        mic_col = QVBoxLayout()
        mic_col.setSpacing(2)
        mic_col.addWidget(self._mic_combo)
        mic_col.addWidget(mic_hint)
        form.addRow("Mic input", mic_col)

        return page

    # ── Compression ──────────────────────────────────────────────────

    def _make_compression_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._out_codec_combo = QComboBox()
        self._out_codec_combo.addItems(["h264", "hevc", "av1"])
        out_hint = QLabel("h264 = Discord / browser compatible")
        out_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        out_col = QVBoxLayout()
        out_col.setSpacing(2)
        out_col.addWidget(self._out_codec_combo)
        out_col.addWidget(out_hint)
        form.addRow("Output codec", out_col)

        cq_row = QHBoxLayout()
        self._cq_slider = QSlider(Qt.Orientation.Horizontal)
        self._cq_slider.setRange(16, 40)
        self._cq_slider.setValue(26)
        self._cq_spin = QSpinBox()
        self._cq_spin.setRange(16, 40)
        self._cq_spin.setValue(26)
        self._cq_slider.valueChanged.connect(self._cq_spin.setValue)
        self._cq_spin.valueChanged.connect(self._cq_slider.setValue)
        cq_row.addWidget(self._cq_slider, 1)
        cq_row.addWidget(self._cq_spin)
        cq_hint = QLabel("lower = better quality, larger file")
        cq_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        cq_col = QVBoxLayout()
        cq_col.setSpacing(2)
        cq_col.addLayout(cq_row)
        cq_col.addWidget(cq_hint)
        form.addRow("Quality (CQ)", cq_col)

        self._container_combo = QComboBox()
        self._container_combo.addItems(["mp4", "mkv", "mov"])
        form.addRow("Container", self._container_combo)

        self._auto_compress = QCheckBox("Re-compress after saving (slower, smaller file)")
        form.addRow("", self._auto_compress)

        return page

    # ── Watermark ────────────────────────────────────────────────────

    def _make_watermark_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._wm_enabled = QCheckBox("Enable watermark on saved clips")
        self._wm_enabled.setChecked(True)
        self._wm_enabled.toggled.connect(self._toggle_watermark_fields)
        form.addRow("", self._wm_enabled)

        self._wm_text = QLineEdit("~( ^.x.^)> caught by mitten")
        form.addRow("Text", self._wm_text)

        self._wm_subtext = QLineEdit("programmed by mit")
        form.addRow("Subtext", self._wm_subtext)

        self._wm_fontfamily = QComboBox()
        self._wm_fontfamily.addItems([
            "Sans", "Monospace", "Noto Sans", "DejaVu Sans",
            "Liberation Sans", "Ubuntu", "Roboto",
        ])
        form.addRow("Font family", self._wm_fontfamily)

        self._wm_fontsize = QSpinBox()
        self._wm_fontsize.setRange(10, 48)
        self._wm_fontsize.setValue(20)
        form.addRow("Font size", self._wm_fontsize)

        self._wm_fontcolor = QLineEdit("white@0.6")
        fc_hint = QLabel("ffmpeg format: color@opacity  (e.g. white@0.6)")
        fc_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        fc_col = QVBoxLayout()
        fc_col.setSpacing(2)
        fc_col.addWidget(self._wm_fontcolor)
        fc_col.addWidget(fc_hint)
        form.addRow("Font color", fc_col)

        self._wm_position = QComboBox()
        self._wm_position.addItems([
            "bottom_right", "bottom_left", "top_right", "top_left",
        ])
        form.addRow("Position", self._wm_position)

        self._wm_padding = QSpinBox()
        self._wm_padding.setRange(0, 100)
        self._wm_padding.setSuffix("px")
        self._wm_padding.setValue(20)
        form.addRow("Padding", self._wm_padding)

        # ── Animation section ──
        anim_sep_row = QHBoxLayout()
        anim_sep_row.setSpacing(8)
        anim_sep_row.addWidget(_sep("ANIMATION"))
        anim_coming = QLabel("(coming soon)")
        anim_coming.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 10px; opacity: 0.6; padding-top: 10px;"
        )
        anim_sep_row.addWidget(anim_coming)
        anim_sep_row.addStretch()
        form.addRow(anim_sep_row)

        self._wm_anim_preset = QComboBox()
        self._wm_anim_preset.addItems(["None", "Slide In", "Fade", "Bounce", "Pop"])
        self._wm_anim_preset.setEnabled(False)
        form.addRow("Animation", self._wm_anim_preset)

        # Paw icon intro
        self._wm_paw_intro = QCheckBox("Show animated paw intro")
        self._wm_paw_intro.setChecked(True)
        self._wm_paw_intro.setEnabled(False)
        form.addRow("", self._wm_paw_intro)

        # Custom icon path
        icon_row = QHBoxLayout()
        self._wm_icon_path = QLineEdit()
        self._wm_icon_path.setPlaceholderText("(default paw icon)")
        self._wm_icon_path.setEnabled(False)
        self._wm_icon_browse = QPushButton("…")
        self._wm_icon_browse.setProperty("class", "secondary")
        self._wm_icon_browse.setFixedWidth(36)
        self._wm_icon_browse.clicked.connect(self._browse_icon)
        self._wm_icon_browse.setEnabled(False)
        icon_row.addWidget(self._wm_icon_path, 1)
        icon_row.addWidget(self._wm_icon_browse)
        form.addRow("Custom icon", icon_row)

        self._wm_fields = [
            self._wm_text, self._wm_subtext, self._wm_fontfamily,
            self._wm_fontsize, self._wm_fontcolor, self._wm_position,
            self._wm_padding, self._wm_anim_preset, self._wm_paw_intro,
            self._wm_icon_path, self._wm_icon_browse,
        ]

        return page

    # ── Games ────────────────────────────────────────────────────────

    def _make_games_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._gd_enabled = QCheckBox("Enable game detection")
        self._gd_enabled.setChecked(True)
        self._gd_enabled.toggled.connect(self._toggle_game_fields)
        form.addRow("", self._gd_enabled)

        self._gd_poll = QSpinBox()
        self._gd_poll.setRange(1, 30)
        self._gd_poll.setSuffix("s")
        self._gd_poll.setValue(5)
        form.addRow("Poll interval", self._gd_poll)

        self._gd_auto_switch = QCheckBox("Auto-switch to game mode on detect")
        self._gd_auto_switch.setChecked(True)
        form.addRow("", self._gd_auto_switch)

        # ── Custom processes ──
        form.addRow(_sep("CUSTOM PROCESSES"))

        hint1 = QLabel("Add process names that should trigger game mode")
        hint1.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        form.addRow("", hint1)

        self._proc_list = QListWidget()
        self._proc_list.setMinimumHeight(90)
        self._proc_list.setMaximumHeight(130)
        form.addRow(self._proc_list)

        proc_row = QHBoxLayout()
        self._proc_input = QLineEdit()
        self._proc_input.setPlaceholderText("e.g. my_game.exe")
        self._proc_add = QPushButton("Add")
        self._proc_add.setProperty("class", "secondary")
        self._proc_add.setMinimumWidth(70)
        self._proc_add.clicked.connect(self._add_process)
        self._proc_remove = QPushButton("Remove")
        self._proc_remove.setProperty("class", "secondary")
        self._proc_remove.setMinimumWidth(80)
        self._proc_remove.clicked.connect(self._remove_process)
        proc_row.addWidget(self._proc_input, 1)
        proc_row.addWidget(self._proc_add)
        proc_row.addWidget(self._proc_remove)
        form.addRow(proc_row)

        # ── Custom window titles ──
        form.addRow(_sep("CUSTOM WINDOW TITLES"))

        hint2 = QLabel("Add window titles (substring match) that trigger game mode")
        hint2.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        hint2.setWordWrap(True)
        form.addRow("", hint2)

        self._title_list = QListWidget()
        self._title_list.setMinimumHeight(90)
        self._title_list.setMaximumHeight(130)
        form.addRow(self._title_list)

        title_row = QHBoxLayout()
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("e.g. Deep Rock Galactic")
        self._title_add = QPushButton("Add")
        self._title_add.setProperty("class", "secondary")
        self._title_add.setMinimumWidth(70)
        self._title_add.clicked.connect(self._add_title)
        self._title_remove = QPushButton("Remove")
        self._title_remove.setProperty("class", "secondary")
        self._title_remove.setMinimumWidth(80)
        self._title_remove.clicked.connect(self._remove_title)
        title_row.addWidget(self._title_input, 1)
        title_row.addWidget(self._title_add)
        title_row.addWidget(self._title_remove)
        form.addRow(title_row)

        self._game_fields = [
            self._gd_poll, self._gd_auto_switch,
            self._proc_list, self._proc_input, self._proc_add, self._proc_remove,
            self._title_list, self._title_input, self._title_add, self._title_remove,
        ]

        return page

    # ------------------------------------------------------------------ #
    # Toggles
    # ------------------------------------------------------------------ #

    def _toggle_mic(self, checked: bool) -> None:
        self._mic_combo.setEnabled(checked)

    def _toggle_watermark_fields(self, checked: bool) -> None:
        for w in self._wm_fields:
            w.setEnabled(checked)

    def _toggle_game_fields(self, checked: bool) -> None:
        for w in self._game_fields:
            w.setEnabled(checked)

    def _toggle_notify_fields(self, checked: bool) -> None:
        for w in (self._notif_start, self._notif_save, self._notif_error):
            w.setEnabled(checked)

    # ------------------------------------------------------------------ #
    # Interactions
    # ------------------------------------------------------------------ #

    def _browse_save_dir(self) -> None:
        current = self._save_dir_edit.text().replace("~", str(Path.home()))
        path = QFileDialog.getExistingDirectory(self, "Choose save directory", current)
        if path:
            home = str(Path.home())
            self._save_dir_edit.setText(
                path.replace(home, "~") if path.startswith(home) else path
            )

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose icon image", "", "Images (*.png *.jpg *.svg)"
        )
        if path:
            self._wm_icon_path.setText(path)

    def _add_process(self) -> None:
        text = self._proc_input.text().strip()
        if text:
            self._proc_list.addItem(text)
            self._proc_input.clear()

    def _remove_process(self) -> None:
        row = self._proc_list.currentRow()
        if row >= 0:
            self._proc_list.takeItem(row)

    def _add_title(self) -> None:
        text = self._title_input.text().strip()
        if text:
            self._title_list.addItem(text)
            self._title_input.clear()

    def _remove_title(self) -> None:
        row = self._title_list.currentRow()
        if row >= 0:
            self._title_list.takeItem(row)

    def _on_detect_button(self) -> None:
        from .button_detect import ButtonDetectDialog
        from PyQt6.QtWidgets import QDialog
        dlg = ButtonDetectDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.result()
            if result:
                _code, name = result
                self._trigger_btn_label.setText(name)

    # ------------------------------------------------------------------ #
    # Config I/O
    # ------------------------------------------------------------------ #

    def _load_config(self) -> None:
        try:
            from ..config import load_config
            cfg = load_config()
        except Exception:
            return

        g = cfg.general
        self._mode_combo.setCurrentText(g.mode)
        self._buffer_slider.setValue(g.buffer_seconds)
        self._buffer_spin.setValue(g.buffer_seconds)
        self._fps_combo.setCurrentText(str(g.framerate))
        self._monitor_combo.setCurrentText(str(g.monitor))
        home = str(Path.home())
        sd = str(g.save_dir)
        self._save_dir_edit.setText(sd.replace(home, "~") if sd.startswith(home) else sd)

        r = cfg.recorder
        self._quality_combo.setCurrentText(r.quality)
        self._cap_codec_combo.setCurrentText(r.capture_codec)
        self._out_codec_combo.setCurrentText(r.output_codec)
        self._cq_slider.setValue(r.watermark_cq)
        self._cq_spin.setValue(r.watermark_cq)
        self._container_combo.setCurrentText(r.container)
        # Default audio to "System default" unless config has a specific device
        if r.audio_device and r.audio_device != "default":
            self._audio_combo.addItem(r.audio_device, r.audio_device)
            self._audio_combo.setCurrentText(r.audio_device)
        elif r.audio_device == "default" or not r.audio_device:
            self._audio_combo.setCurrentIndex(0)  # System default

        try:
            from ..config import BUTTON_NAMES
            t = cfg.trigger
            code = BUTTON_NAMES.get(t.button, "?")
            self._trigger_btn_label.setText(f"{t.button}  ({code})")
            self._cooldown_spin.setValue(t.cooldown)
        except Exception:
            pass

        wm = cfg.watermark
        self._wm_enabled.setChecked(wm.enabled)
        self._wm_text.setText(wm.text)
        self._wm_subtext.setText(wm.subtext)
        self._wm_fontsize.setValue(wm.fontsize)
        self._wm_fontcolor.setText(wm.fontcolor)
        self._wm_position.setCurrentText(wm.position)
        self._wm_padding.setValue(wm.padding)
        self._toggle_watermark_fields(wm.enabled)

        gd = cfg.game_detection
        self._gd_enabled.setChecked(gd.enabled)
        self._gd_poll.setValue(gd.poll_interval)
        self._gd_auto_switch.setChecked(gd.auto_switch)
        self._proc_list.clear()
        for proc in gd.custom_processes:
            self._proc_list.addItem(proc)
        self._title_list.clear()
        for title in gd.custom_window_titles:
            self._title_list.addItem(title)
        self._toggle_game_fields(gd.enabled)

        n = cfg.notifications
        self._notif_enabled.setChecked(n.enabled)
        self._notif_start.setChecked(n.on_start)
        self._notif_save.setChecked(n.on_save)
        self._notif_error.setChecked(n.on_error)
        self._toggle_notify_fields(n.enabled)

    def _on_save(self) -> None:
        try:
            self._do_save()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def _do_save(self) -> None:
        from pathlib import Path as _P
        from ..config import (
            MittenConfig, GeneralConfig, RecorderConfig,
            TriggerConfig, WatermarkConfig, GameDetectionConfig,
            NotificationsConfig, CONFIG_FILE, _validate,
        )
        from .config_io import config_to_toml

        # Read trigger button name from label text
        btn_label_text = self._trigger_btn_label.text()
        btn_name = btn_label_text.split("  (")[0].strip() if "  (" in btn_label_text else "BTN_EXTRA"

        # Audio device value
        audio_val = self._audio_combo.currentData() or self._audio_combo.currentText()
        if audio_val == "System default":
            audio_val = "default"

        # Custom processes list
        procs = [
            self._proc_list.item(i).text()
            for i in range(self._proc_list.count())
        ]
        titles = [
            self._title_list.item(i).text()
            for i in range(self._title_list.count())
        ]

        save_dir_str = self._save_dir_edit.text()
        save_dir = _P(save_dir_str.replace("~", str(_P.home()))).expanduser()

        cfg = MittenConfig(
            general=GeneralConfig(
                mode=self._mode_combo.currentText(),
                buffer_seconds=self._buffer_spin.value(),
                framerate=int(self._fps_combo.currentText()),
                save_dir=save_dir,
                monitor=self._monitor_combo.currentText(),
            ),
            recorder=RecorderConfig(
                container=self._container_combo.currentText(),
                quality=self._quality_combo.currentText(),
                capture_codec=self._cap_codec_combo.currentText(),
                output_codec=self._out_codec_combo.currentText(),
                watermark_cq=self._cq_spin.value(),
                audio_device=audio_val,
            ),
            trigger=TriggerConfig(
                button=btn_name,
                cooldown=self._cooldown_spin.value(),
            ),
            watermark=WatermarkConfig(
                enabled=self._wm_enabled.isChecked(),
                text=self._wm_text.text(),
                subtext=self._wm_subtext.text(),
                fontsize=self._wm_fontsize.value(),
                fontcolor=self._wm_fontcolor.text(),
                position=self._wm_position.currentText(),
                padding=self._wm_padding.value(),
            ),
            game_detection=GameDetectionConfig(
                enabled=self._gd_enabled.isChecked(),
                poll_interval=self._gd_poll.value(),
                auto_switch=self._gd_auto_switch.isChecked(),
                custom_processes=tuple(procs),
                custom_window_titles=tuple(titles),
            ),
            notifications=NotificationsConfig(
                enabled=self._notif_enabled.isChecked(),
                on_start=self._notif_start.isChecked(),
                on_save=self._notif_save.isChecked(),
                on_error=self._notif_error.isChecked(),
            ),
        )

        _validate(cfg)

        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(config_to_toml(cfg))

        self._save_status.setText("✓  saved")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self._save_status.setText(""))
