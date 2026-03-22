"""
Main MITTEN window — sidebar nav, dashboard, clips, settings.
Minimizes to tray on close.
"""
from __future__ import annotations

import random
import subprocess
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QThread,
    QTimer,
    Qt,
    QUrl,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .resources import C, CAT, CAT_FONT, CATS, paw_icon, _accent_rgba, _accent_hover, _hex_rgba
from ..daemon_utils import get_daemon_pid, toggle_daemon, toggle_pause
from ..config import PAUSE_FILE, RECORDER_DEAD_FILE
from ..utils import format_duration, get_vram_usage


class _NavButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setMinimumWidth(100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style(False)
        self.toggled.connect(self._update_style)

    def _update_style(self, checked: bool) -> None:
        if checked:
            self.setStyleSheet(
                f"QPushButton {{ background-color: {_accent_rgba(0.10)};"
                f"color: {C.LAVENDER}; border: none;"
                f"border-left: 2px solid {C.LAVENDER};"
                f"text-align: left; padding-left: 18px; font-weight: 600;"
                f"font-size: 13px; border-radius: 0; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {C.SUBTEXT};"
                f"border: none; text-align: left; padding-left: 20px;"
                f"font-size: 13px; border-radius: 0; }}"
                f"QPushButton:hover {{ color: {C.TEXT};"
                f"background-color: rgba(255,255,255,0.03); }}"
            )


class _UpdateCheckerThread(QThread):
    """Runs check_for_update() off the main thread (git fetch is slow)."""
    update_found = pyqtSignal(str, str, str)  # (old_hash, new_hash, new_version)

    def run(self) -> None:
        try:
            from ..updater import check_for_update
            result = check_for_update()
            if result:
                old_hash, new_hash, new_ver = result
                self.update_found.emit(old_hash, new_hash, new_ver)
        except Exception:
            pass


class _StatusBanner(QFrame):
    _STATES = {
        "idle": (
            "~( ^.x.^)>",
            "idle",
            "press Start to begin recording",
            C.GRAY,
        ),
        "recording": (
            "ฅ(=^.\u03c9.^=)ฅ",
            "recording",
            "mitten is watching\u2026",
            C.GREEN,
        ),
        "game": (
            "(=\u0186\u03c9\u0186=)\u2728",
            "game mode",
            "game detected \u2014 enhanced capture active",
            C.ORANGE,
        ),
        "saving": (
            "~( ^.x.^)> \u266a",
            "saving clip\u2026",
            "writing your clip to disk",
            C.BLUE,
        ),
        "paused": (
            "~( ^.-.)>",
            "paused",
            "buffer paused — press Resume to continue",
            C.SUBTEXT,
        ),
        "recorder_dead": (
            "~( x.x.^)>",
            "recorder crashed",
            "gpu-screen-recorder gave up — restart to recover",
            C.PINK,
        ),
        "error": (
            "~( x.x.^)>",
            "error",
            "something went wrong",
            C.PINK,
        ),
        "no_deps": (
            "~( x.x.^)>",
            "gpu-screen-recorder not found",
            "install it: yay -S gpu-screen-recorder",
            C.PINK,
        ),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(56)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 0, 16, 0)
        outer.setSpacing(0)

        left = QVBoxLayout()
        left.setSpacing(2)
        left.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.setContentsMargins(0, 0, 0, 0)

        self._ascii_label = QLabel("~( ^.x.^)>")
        self._ascii_label.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 14px; font-weight: 700;"
            f"background: transparent; border: none; {CAT_FONT}"
        )
        row1.addWidget(self._ascii_label)

        self._state_label = QLabel("idle")
        self._state_label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {C.TEXT};"
            f"background: transparent; border: none;"
        )
        row1.addWidget(self._state_label)
        row1.addStretch()

        left.addLayout(row1)

        self._detail_label = QLabel("press Start to begin recording")
        self._detail_label.setWordWrap(True)
        self._detail_label.setStyleSheet(
            f"font-size: 11px; color: {C.SUBTEXT}; background: transparent; border: none;"
        )
        left.addWidget(self._detail_label)

        outer.addLayout(left, 1)

        self._btn_update = QPushButton("Update")
        self._btn_update.setFixedSize(84, 32)
        self._btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_update.setStyleSheet(
            f"QPushButton {{ background-color: {_hex_rgba(C.GREEN, 0.85)}; color: {C.BG};"
            f"border: none; border-radius: 6px; font-weight: 700; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: {C.GREEN}; }}"
        )
        self._btn_update.hide()

        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setFixedSize(90, 32)  # wide enough for "Resume"
        self._btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_pause.setStyleSheet(
            f"QPushButton {{ background-color: {_hex_rgba(C.BLUE, 0.85)}; color: {C.BG};"
            f"border: none; border-radius: 6px; font-weight: 700; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: {C.BLUE}; }}"
        )
        self._btn_pause.hide()

        self._btn_toggle = QPushButton("Start")
        self._btn_toggle.setFixedSize(84, 32)
        self._btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)

        _btn_group = QWidget()
        _btn_group.setStyleSheet("background: transparent;")
        _btn_row = QHBoxLayout(_btn_group)
        _btn_row.setContentsMargins(0, 0, 0, 0)
        _btn_row.setSpacing(6)
        _btn_row.addWidget(self._btn_update)
        _btn_row.addWidget(self._btn_pause)
        _btn_row.addWidget(self._btn_toggle)
        outer.addWidget(_btn_group)

        self.set_state("idle")

    def set_state(self, state: str, detail: str = "") -> None:
        ascii_art, text, default_detail, color = self._STATES.get(
            state, self._STATES["idle"]
        )

        self.setStyleSheet(
            f"QFrame {{ background-color: transparent;"
            f"border-radius: 10px; border: 1px solid {_hex_rgba(C.BORDER, 0.4)}; }}"
        )

        # Use theme-aware contextual cat for this state
        try:
            from . import themes as _themes_mod
            ascii_art = _themes_mod.get_state_cat(state)
        except Exception:
            pass

        self._ascii_label.setText(ascii_art)
        self._ascii_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: 700;"
            f"background: transparent; border: none; {CAT_FONT}"
        )

        self._state_label.setText(text)
        self._state_label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {color};"
            f"background: transparent; border: none;"
        )

        # Light mode: replace idle detail with rotating abuse.
        # Import the themes MODULE (not just the variable) so we always read the live value,
        # not a snapshot from when the function was first called.
        if not detail and state == "idle":
            try:
                from . import themes as _themes_mod
                if _themes_mod.LIGHT_MODE_ACTIVE:
                    default_detail = _themes_mod.get_abuse()
            except Exception:
                pass
        # Light mode: also append abuse to recording detail ~40% of the time
        if state == "recording" and detail:
            try:
                from . import themes as _themes_mod
                import random as _rnd
                if _themes_mod.LIGHT_MODE_ACTIVE and _rnd.random() < 0.40:
                    detail = detail + " · " + _themes_mod.get_abuse()
            except Exception:
                pass
        _detail_text = detail or default_detail
        self._detail_label.setText(_detail_text)
        self._detail_label.setStyleSheet(
            f"font-size: 11px; color: {C.SUBTEXT}; background: transparent; border: none;"
        )

        dead = state == "no_deps" or state == "recorder_dead"
        self._btn_toggle.setEnabled(not dead)

        running = state in ("recording", "game", "saving")
        paused = state == "paused"

        # Pause button: visible when recording or paused; label flips
        if running:
            self._btn_pause.setText("Pause")
            self._btn_pause.show()
        elif paused:
            self._btn_pause.setText("Resume")
            self._btn_pause.show()
        else:
            self._btn_pause.hide()

        self._btn_toggle.setText("Stop" if (running or paused) else "Start")
        if running or paused:
            self._btn_toggle.setStyleSheet(
                f"QPushButton {{ background-color: {_hex_rgba(C.PINK, 0.85)}; color: {C.BG};"
                f"border: none; border-radius: 6px; font-weight: 700; font-size: 12px; }}"
                f"QPushButton:hover {{ background-color: {C.PINK}; }}"
            )
        else:
            self._btn_toggle.setStyleSheet(
                f"QPushButton {{ background-color: {_hex_rgba(C.GREEN, 0.85)}; color: {C.BG};"
                f"border: none; border-radius: 6px; font-weight: 700; font-size: 12px; }}"
                f"QPushButton:hover {{ background-color: {C.GREEN}; }}"
            )

    def show_update_available(self) -> None:
        """Make the Update button visible in the banner."""
        self._btn_update.show()


class _StatCard(QFrame):
    """Glassy stat card — label + large accent-colored value."""

    def __init__(
        self, label: str, accent: str = C.LAVENDER,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._accent = accent
        self._bg_alpha: float = 0.32
        self._hover_anim: QPropertyAnimation | None = None
        self._apply_style(0.32)
        self.setMinimumHeight(82)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(5)

        _lbl_text = label
        try:
            from . import themes as _themes_mod
            import random as _rnd
            if _themes_mod.LIGHT_MODE_ACTIVE and _rnd.random() < 0.20:
                _lbl_text = _themes_mod.get_abuse().upper()
        except Exception:
            pass
        lbl = QLabel(_lbl_text)
        lbl.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 10px; font-weight: 700;"
            f"letter-spacing: 1.2px; border: none; background: transparent;"
        )
        layout.addWidget(lbl)

        self._value = QLabel("\u2014")
        self._value.setStyleSheet(
            f"color: {accent}; font-size: 20px; font-weight: 400; border: none; background: transparent;"
        )
        layout.addWidget(self._value)
        layout.addStretch()

    def _apply_style(self, alpha: float) -> None:
        # Border swaps from gray → lavender midway through hover
        border = _accent_rgba(0.28) if alpha > 0.43 else _hex_rgba(C.BORDER, 0.28)
        self.setStyleSheet(
            f"QFrame {{ background-color: {_hex_rgba(C.SURFACE, alpha)};"
            f"border-radius: 10px; border: 1px solid {border}; }}"
        )

    @pyqtProperty(float)
    def bg_alpha(self) -> float:
        return self._bg_alpha

    @bg_alpha.setter
    def bg_alpha(self, value: float) -> None:
        self._bg_alpha = value
        self._apply_style(value)

    def _animate_to(self, target: float) -> None:
        if self._hover_anim:
            self._hover_anim.stop()
        anim = QPropertyAnimation(self, b"bg_alpha", self)
        anim.setDuration(120)
        anim.setStartValue(self._bg_alpha)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._hover_anim = anim

    def enterEvent(self, event) -> None:
        self._animate_to(0.55)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_to(0.32)
        super().leaveEvent(event)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


class _DurProber(QThread):
    """One-shot ffprobe thread — emits formatted duration string."""
    done = pyqtSignal(str)

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self._path = path

    def run(self) -> None:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(self._path)],
                capture_output=True, text=True, timeout=10,
            )
            secs = max(0, int(float(result.stdout.strip())))
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            self.done.emit(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        except Exception:
            self.done.emit("")


class _ClipPreview(QFrame):
    """Auto-looping muted preview of the last clip. Hover plays audio."""

    hovered = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {C.BG}; border-radius: 8px; }}"
        )
        self._clip_path: Path | None = None
        self._media_player = None
        self._audio_proc: subprocess.Popen | None = None
        self._dur_prober: _DurProber | None = None
        # Delayed audio stop for soft fade-out effect on mouse leave
        self._audio_stop_timer = QTimer(self)
        self._audio_stop_timer.setSingleShot(True)
        self._audio_stop_timer.timeout.connect(self._stop_audio)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            self._player_widget = QVideoWidget()
            self._player_widget.setAspectRatioMode(
                Qt.AspectRatioMode.KeepAspectRatioByExpanding
            )
            self._player_widget.setMinimumHeight(160)
            self._player_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self._media_player = QMediaPlayer()
            audio = QAudioOutput()
            audio.setVolume(0)   # muted — just a living thumbnail
            self._media_player.setAudioOutput(audio)
            self._media_player.setVideoOutput(self._player_widget)
            self._media_player.mediaStatusChanged.connect(self._on_status)
            layout.addWidget(self._player_widget, 1)
        except ImportError:
            self._player_widget = None
            cat = random.choice(CATS)
            placeholder = QLabel(f"{cat}\nno video backend")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: 16px; {CAT_FONT}"
            )
            layout.addWidget(placeholder, 1)

        self._name_label = QLabel("no clips yet")
        self._name_label.setWordWrap(False)
        self._name_label.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 11px;"
            f"padding: 6px 12px;"
            f"background: {_hex_rgba(C.BG, 0.7)};"
            f"border-radius: 0 0 8px 8px;"
        )
        layout.addWidget(self._name_label)

        self._dur_badge = QLabel(self)
        self._dur_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dur_badge.setStyleSheet(
            "background: rgba(0,0,0,0.55); color: white; font-size: 10px;"
            "font-weight: 600; border-radius: 10px; padding: 2px 8px;"
        )
        self._dur_badge.hide()

        self._play_icon = QLabel("▶", self)
        self._play_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._play_icon.setFixedSize(56, 56)
        self._play_icon.setStyleSheet(
            "background: rgba(0,0,0,0.5); color: rgba(255,255,255,0.9);"
            "font-size: 22px; border-radius: 28px;"
        )
        self._play_icon.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        name_h = 32  # approximate name strip height
        w, h = self.width(), self.height()
        # Duration badge: bottom-right, just above name strip
        bw = max(self._dur_badge.sizeHint().width(), 50)
        self._dur_badge.setGeometry(w - bw - 10, h - name_h - 26, bw, 22)
        self._dur_badge.raise_()
        # Play icon: centered in video area above name strip
        self._play_icon.move((w - 56) // 2, (h - name_h - 56) // 2)
        self._play_icon.raise_()

    def _start_audio(self, path: Path) -> None:
        self._stop_audio()
        try:
            self._audio_proc = subprocess.Popen(
                [
                    "ffplay", "-nodisp", "-autoexit",
                    "-af", "afade=t=in:st=0:d=3.0,volume=0.35",
                    str(path),
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _stop_audio(self) -> None:
        if self._audio_proc and self._audio_proc.poll() is None:
            self._audio_proc.terminate()
        self._audio_proc = None

    def enterEvent(self, event) -> None:
        self._audio_stop_timer.stop()
        if self._clip_path and self._clip_path.exists():
            if self._media_player:
                self._media_player.setPosition(0)
                self._media_player.play()
            self._start_audio(self._clip_path)
            self._play_icon.show()
            self._reposition_overlays()
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._audio_stop_timer.start(400)
        self._play_icon.hide()
        self.hovered.emit(False)
        super().leaveEvent(event)

    def set_clip(self, path: Path | None) -> None:
        if path == self._clip_path:
            return
        self._audio_stop_timer.stop()
        self._stop_audio()
        self._clip_path = path
        self._dur_badge.hide()
        self._play_icon.hide()
        if self._dur_prober and self._dur_prober.isRunning():
            self._dur_prober.quit()
            self._dur_prober.wait(2000)
        self._dur_prober = None
        if path and path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            short = path.stem.replace("mitten_", "").replace("_", " ")
            self._name_label.setText(f"{short}  \u00b7  {size_mb:.1f} MB")
            if self._media_player:
                self._media_player.setSource(QUrl.fromLocalFile(str(path)))
                self._media_player.play()
            self._dur_prober = _DurProber(path, self)
            self._dur_prober.done.connect(self._on_dur_ready)
            self._dur_prober.start()
        else:
            self._name_label.setText("no clips yet")
            if self._media_player:
                self._media_player.stop()

    def set_saving(self) -> None:
        """Show a placeholder while a clip is being processed."""
        self._name_label.setText("saving your clip\u2026")
        self._dur_badge.hide()
        if self._media_player:
            self._media_player.stop()

    def _on_dur_ready(self, dur_str: str) -> None:
        if dur_str:
            self._dur_badge.setText(dur_str)
            self._dur_badge.show()
            self._reposition_overlays()

    def _on_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._media_player.setPosition(0)
            self._media_player.play()


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {C.SUBTEXT}; font-size: 11px; font-weight: 700;"
        f"letter-spacing: 1.5px;"
        f"border-left: 2px solid {C.LAVENDER};"
        f"padding-left: 8px;"
        f"background: transparent;"
    )
    return lbl


class _PillTabBar(QWidget):
    """Horizontal row of pill-shaped toggle buttons backed by a QButtonGroup."""

    tab_changed = pyqtSignal(int)

    def __init__(self, labels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for i, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            self._group.addButton(btn, i)
            layout.addWidget(btn)
            self._style_btn(btn, False)

        layout.addStretch()

        self._group.idClicked.connect(self._on_click)
        first = self._group.button(0)
        if first:
            first.setChecked(True)
            self._style_btn(first, True)

    def _style_btn(self, btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                f"QPushButton {{ background: {_accent_rgba(0.15)}; color: {C.LAVENDER};"
                f"border: 1px solid {_accent_rgba(0.3)}; border-radius: 12px;"
                f"font-size: 11px; font-weight: 600; padding: 0 14px; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {C.SUBTEXT};"
                f"border: 1px solid {C.BORDER}; border-radius: 12px;"
                f"font-size: 11px; font-weight: 600; padding: 0 14px; }}"
                f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.LAVENDER}; }}"
            )

    def _on_click(self, idx: int) -> None:
        for bid in self._group.buttons():
            self._style_btn(bid, self._group.id(bid) == idx)
        self.tab_changed.emit(idx)


class _DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._anims: dict[str, QPropertyAnimation] = {}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._content = QWidget()
        self._content.setMaximumWidth(1040)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )

        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        self.banner = _StatusBanner()
        layout.addWidget(self.banner)

        # ── Performance container — fades + collapses on clip hover ──
        self._perf_container = QWidget()
        self._perf_container.setStyleSheet("background: transparent;")
        self._perf_natural_h: int = 0
        perf_layout = QVBoxLayout(self._perf_container)
        perf_layout.setContentsMargins(0, 0, 0, 0)
        perf_layout.setSpacing(14)

        perf_layout.addWidget(_section_header("PERFORMANCE"))

        self._pill_bar = _PillTabBar(["RAM", "GPU · CPU", "CLIPS"])
        perf_layout.addWidget(self._pill_bar)

        self._stat_stack = QStackedWidget()

        ram_page = QWidget()
        ram_layout = QHBoxLayout(ram_page)
        ram_layout.setContentsMargins(0, 4, 0, 0)
        ram_layout.setSpacing(10)
        self.card_ram_total  = _StatCard("TOTAL RAM",  C.SUBTEXT)
        self.card_ram_used   = _StatCard("USED RAM",   C.ORANGE)
        self.card_ram_mitten = _StatCard("MITTEN",     C.LAVENDER)
        ram_layout.addWidget(self.card_ram_total)
        ram_layout.addWidget(self.card_ram_used)
        ram_layout.addWidget(self.card_ram_mitten)
        self._stat_stack.addWidget(ram_page)  # index 0

        gpu_page = QWidget()
        gpu_layout = QHBoxLayout(gpu_page)
        gpu_layout.setContentsMargins(0, 4, 0, 0)
        gpu_layout.setSpacing(10)
        self.card_vram       = _StatCard("GPU VRAM",   C.ORANGE)
        self.card_cpu        = _StatCard("CPU TOTAL",  C.BLUE)
        self.card_cpu_mitten = _StatCard("MITTEN CPU", C.GREEN)
        gpu_layout.addWidget(self.card_vram)
        gpu_layout.addWidget(self.card_cpu)
        gpu_layout.addWidget(self.card_cpu_mitten)
        self._stat_stack.addWidget(gpu_page)  # index 1

        clips_page = QWidget()
        clips_layout = QHBoxLayout(clips_page)
        clips_layout.setContentsMargins(0, 4, 0, 0)
        clips_layout.setSpacing(10)
        self.card_week     = _StatCard("THIS WEEK",  C.LAVENDER)
        self.card_avg_save = _StatCard("AVG SAVE",   C.GREEN)
        self.card_compress = _StatCard("COMPRESS %", C.ORANGE)
        clips_layout.addWidget(self.card_week)
        clips_layout.addWidget(self.card_avg_save)
        clips_layout.addWidget(self.card_compress)
        self._stat_stack.addWidget(clips_page)  # index 2

        perf_layout.addWidget(self._stat_stack)
        self._pill_bar.tab_changed.connect(self._on_tab_changed)

        est_lbl = QLabel("* RSS / cpu_percent \u2014 approximations, not exact process accounting")
        est_lbl.setStyleSheet(
            f"color: {_hex_rgba(C.SUBTEXT, 0.38)}; font-size: 9px; padding-top: 1px;"
        )
        perf_layout.addWidget(est_lbl)

        layout.addWidget(self._perf_container)

        self._last_clip_header = _section_header("LAST CLIP")
        layout.addWidget(self._last_clip_header)

        self.clip_preview = _ClipPreview()
        self.clip_preview.setMinimumHeight(180)
        layout.addWidget(self.clip_preview, 1)

        self.clip_preview.hovered.connect(self._on_preview_hover)

        outer.addStretch(1)
        outer.addWidget(self._content, 6)
        outer.addStretch(1)

    def _start_anim(self, key: str, anim: QPropertyAnimation) -> None:
        old = self._anims.get(key)
        if old and old.state() == QPropertyAnimation.State.Running:
            old.stop()
        self._anims[key] = anim
        if anim.state() != QPropertyAnimation.State.Running:
            anim.start()

    def _on_tab_changed(self, idx: int) -> None:
        from .anim import cross_fade
        old = self._stat_stack.currentWidget()
        self._stat_stack.setCurrentIndex(idx)
        new = self._stat_stack.currentWidget()
        if old and new and old is not new:
            out_anim, in_anim = cross_fade(old, new, duration_ms=180)
            self._start_anim("tab_out", out_anim)
            self._start_anim("tab_in", in_anim)

    def _on_preview_hover(self, is_hovered: bool) -> None:
        from .anim import fade_in, fade_out
        c = self._perf_container
        if is_hovered:
            if self._perf_natural_h == 0:
                self._perf_natural_h = c.sizeHint().height()
            fade_out(c, duration_ms=200, on_done=self._collapse_perf)
        else:
            self._expand_perf()

    def _collapse_perf(self) -> None:
        c = self._perf_container
        if self._perf_natural_h == 0:
            self._perf_natural_h = max(c.height(), c.sizeHint().height())
        anim = QPropertyAnimation(c, b"maximumHeight", c)
        anim.setDuration(260)
        anim.setStartValue(c.height())
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._start_anim("perf_collapse", anim)

    def _expand_perf(self) -> None:
        from .anim import fade_in
        c = self._perf_container
        target = self._perf_natural_h or 200
        anim = QPropertyAnimation(c, b"maximumHeight", c)
        anim.setDuration(280)
        anim.setStartValue(c.maximumHeight())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: c.setMaximumHeight(16777215) if c else None)
        self._start_anim("perf_expand", anim)
        fade_in(c, duration_ms=280)


class _ReviewSlider(QFrame):
    """Medal complaint / mitten response carousel."""

    _SLIDES = [
        (
            '"when idling at desktop medal is using around 600mb of memory. '
            'when gaming it jumps to 1000mb and around 25% of my gpu. '
            'just for a 720p 60fps replay buffer."',
            "r/MedalTV",
            "yeah, that's not happening here. mitten hits nvenc directly. "
            "idles during gameplay, barely registers on any gpu meter. "
            "your 1080p60 buffer sits under 300mb. medal chews through your ram doing nothing useful.",
        ),
        (
            '"medal is windows only. no linux support, no plans for linux support."',
            "medal.tv faq",
            "you're on linux. they don't care. mitten was built for this. "
            "wayland native, systemd service, nvenc, no compatibility layers. "
            "medal's faq says 'no plans.' ok.",
        ),
        (
            '"they paywalled clip length. used to be unlimited, '
            'now you need medal pro for clips over 60 seconds."',
            "r/MedalTV",
            "your buffer is however long you set it. right now. no account, no tier, "
            "no email asking you to upgrade. you set a number in config and that's your buffer. "
            "medal took something free and charged you for it back. mitten never will.",
        ),
        (
            '"ads started showing up in the overlay during gameplay. '
            'no way to disable them without paying for premium."',
            "r/MedalTV",
            "there is no overlay. there are no ads. there is no account to serve them to. "
            "mitten doesn't know who you are and doesn't want to. "
            "it records your screen, saves your clip, and shuts up.",
        ),
        (
            '"medal crashed and i lost my entire session. '
            'hours of gameplay just gone with no warning."',
            "r/MedalTV",
            "every clip is its own ffmpeg job. one crashes, the others are fine. "
            "everything is on your local disk. no cloud sync to fail. "
            "you own your clips. medal's servers going down isn't your problem.",
        ),
    ]

    @staticmethod
    def _get_slides():
        """Return slides, injecting light mode insults if active."""
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE:
                import random as _r
                abused = []
                for quote, source, response in _ReviewSlider._SLIDES:
                    extra = f" also you're using light mode. {get_abuse(include_name=False)}"
                    abused.append((quote, source, response + extra))
                return abused
        except Exception:
            pass
        return _ReviewSlider._SLIDES

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("QFrame { background: transparent; }")
        self._idx = 0
        self._anim: QPropertyAnimation | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._stack = QStackedWidget()
        _slides = self._get_slides()
        for quote, source, response in _slides:
            self._stack.addWidget(self._make_slide(quote, source, response))
        layout.addWidget(self._stack)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)

        btn_style = (
            f"QPushButton {{ background: {_accent_rgba(0.08)}; color: {C.LAVENDER};"
            f"border: 1px solid {_accent_rgba(0.2)}; border-radius: 11px;"
            f"font-size: 12px; min-width: 22px; max-width: 22px;"
            f"min-height: 22px; max-height: 22px; }}"
            f"QPushButton:hover {{ background: {_accent_rgba(0.18)}; }}"
        )
        self._btn_prev = QPushButton("\u2190")
        self._btn_prev.setStyleSheet(btn_style)
        self._btn_next = QPushButton("\u2192")
        self._btn_next.setStyleSheet(btn_style)

        nav.addWidget(self._btn_prev)
        nav.addStretch()
        self._dot_labels: list[QLabel] = []
        for i in range(len(_slides)):
            dot = QLabel("\u2022" if i == 0 else "\u25e6")
            dot.setStyleSheet(
                f"color: {C.LAVENDER if i == 0 else C.SUBTEXT}; font-size: 14px; background: transparent;"
            )
            self._dot_labels.append(dot)
            nav.addWidget(dot)
        nav.addStretch()
        nav.addWidget(self._btn_next)
        layout.addLayout(nav)

        self._btn_prev.clicked.connect(self._prev)
        self._btn_next.clicked.connect(self._next)

    def _make_slide(self, quote: str, source: str, response: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)

        block = QFrame()
        block.setFrameShape(QFrame.Shape.NoFrame)
        block.setStyleSheet(
            f"QFrame {{ background-color: {_hex_rgba(C.SURFACE, 0.5)};"
            f"border: none; border-left: 3px solid {C.PINK};"
            f"border-radius: 0 6px 6px 0; }}"
        )
        bl = QVBoxLayout(block)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(6)
        q_lbl = QLabel(quote)
        q_lbl.setWordWrap(True)
        q_lbl.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; background: transparent;")
        bl.addWidget(q_lbl)
        src_lbl = QLabel(f"\u2014 {source}")
        src_lbl.setStyleSheet(f"color: {C.GRAY}; font-size: 10px; background: transparent;")
        bl.addWidget(src_lbl)
        wl.addWidget(block)

        resp = QLabel(f"mitten: {response}")
        resp.setWordWrap(True)
        resp.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 12px; background: transparent;")
        wl.addWidget(resp)
        return w

    def _animate_to(self, new_idx: int) -> None:
        if self._anim and self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()
        self._stack.setCurrentIndex(new_idx)
        new_w = self._stack.currentWidget()
        if new_w:
            eff = QGraphicsOpacityEffect(new_w)
            eff.setOpacity(0.0)
            new_w.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", new_w)
            anim.setDuration(180)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: new_w.setGraphicsEffect(None))
            anim.start()
            self._anim = anim

    def _prev(self) -> None:
        self._idx = (self._idx - 1) % len(self._SLIDES)
        self._animate_to(self._idx)
        self._update_dots()

    def _next(self) -> None:
        self._idx = (self._idx + 1) % len(self._SLIDES)
        self._animate_to(self._idx)
        self._update_dots()

    def _update_dots(self) -> None:
        for i, dot in enumerate(self._dot_labels):
            dot.setText("\u2022" if i == self._idx else "\u25e6")
            dot.setStyleSheet(
                f"color: {C.LAVENDER if i == self._idx else C.SUBTEXT}; "
                f"font-size: 14px; background: transparent;"
            )


class _SysInfoSection(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toggle = QPushButton("\u25b8  your setup")
        self._toggle.setCheckable(True)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.SUBTEXT};"
            f"border: none; text-align: left; font-size: 12px; padding: 4px 0; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; }}"
            f"QPushButton:checked {{ color: {C.LAVENDER}; }}"
        )
        self._toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self._toggle)

        self._body = QWidget()
        self._body.setStyleSheet(
            f"background: {_hex_rgba(C.SURFACE, 0.35)}; border-radius: 6px;"
        )
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(4)
        self._lines: list[QLabel] = []
        for _ in range(6):
            lbl = QLabel()
            lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px; background: transparent;")
            bl.addWidget(lbl)
            self._lines.append(lbl)
        self._body.hide()
        layout.addWidget(self._body)

    def _on_toggle(self, checked: bool) -> None:
        self._toggle.setText(("\u25be" if checked else "\u25b8") + "  your setup")
        if checked:
            self._populate()
        self._body.setVisible(checked)

    def _populate(self) -> None:
        rows: list[str] = []
        try:
            from ..config import load_config
            cfg = load_config()
            rows.append(f"buffer    {cfg.general.buffer_seconds}s")
            rows.append(f"monitor   {cfg.general.monitor}")
            rows.append(f"trigger   {cfg.trigger.button}")
            rows.append(f"mode      {cfg.general.mode}")
        except Exception:
            rows.append("config    unavailable")
        try:
            from ..daemon_utils import get_daemon_pid
            pid = get_daemon_pid()
            rows.append(f"daemon    {'running (pid ' + str(pid) + ')' if pid else 'stopped'}")
        except Exception:
            rows.append("daemon    unknown")
        try:
            import subprocess as _sp
            r = _sp.run(
                ["gpu-screen-recorder", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            ver = (r.stdout.strip() or r.stderr.strip()).split("\n")[0][:40]
            rows.append(f"gsr       {ver}")
        except Exception:
            rows.append("gsr       not found")
        for i, lbl in enumerate(self._lines):
            lbl.setText(rows[i] if i < len(rows) else "")


class _AboutPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        inner = QWidget()
        inner.setMaximumWidth(920)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(40, 36, 40, 40)
        layout.setSpacing(0)

        def _h(text: str, size: int = 22, color: str = C.LAVENDER) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {color}; font-size: {size}px; font-weight: 700;"
                f"background: transparent; {CAT_FONT}"
            )
            return lbl

        def _p(text: str, color: str = C.TEXT, size: int = 13) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {color}; font-size: {size}px; background: transparent;"
                f"line-height: 160%;"
            )
            return lbl

        def _gap(px: int = 18) -> QWidget:
            w = QWidget()
            w.setFixedHeight(px)
            w.setStyleSheet("background: transparent;")
            return w

        def _divider() -> QWidget:
            line = QWidget()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background-color: {_hex_rgba(C.BORDER, 0.4)};")
            return line

        def _pill(text: str, color: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {color}; background: transparent;"
                f"border: 1px solid {color}; border-radius: 11px;"
                f"padding: 3px 10px; font-size: 11px;"
            )
            return lbl

        ver_row = QHBoxLayout()
        ver_row.setContentsMargins(0, 0, 0, 0)
        ver_row.setSpacing(10)
        try:
            import importlib.metadata
            ver_str = importlib.metadata.version("mitten")
        except Exception:
            ver_str = "0.2.x"
        ver_badge = QLabel(f"v{ver_str}")
        ver_badge.setStyleSheet(
            f"color: {C.LAVENDER}; background: {_accent_rgba(0.12)};"
            f"border: 1px solid {_accent_rgba(0.3)}; border-radius: 10px;"
            f"padding: 2px 10px; font-size: 11px; font-weight: 600;"
        )
        ver_row.addWidget(ver_badge)
        ver_row.addStretch()
        layout.addLayout(ver_row)
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "a replay buffer for linux. press a button, save the last n seconds. that's it.",
            C.SUBTEXT, 14,
        ))

        try:
            from . import themes as _themes_mod
            if _themes_mod.LIGHT_MODE_ACTIVE and random.random() < 0.5:
                _abuse_lbl = QLabel(_themes_mod.get_abuse())
                _abuse_lbl.setWordWrap(True)
                _abuse_lbl.setStyleSheet(
                    f"color: {C.SUBTEXT}; font-size: 11px; font-style: italic;"
                    f"background: transparent;"
                )
                layout.addWidget(_gap(8))
                layout.addWidget(_abuse_lbl)
        except Exception:
            pass

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_h("why i made this", 15, C.LAVENDER))
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "medal doesn't run on linux. not 'limited support', "
            "straight up doesn't exist. so that was already a no."
        ))
        layout.addWidget(_gap(8))
        layout.addWidget(_p(
            "and even on windows it sucks. idles at 20%+ gpu on a 3090. "
            "ads in the overlay during gameplay, no way to disable without paying. "
            "paywalled clip length that used to be free. uploads your clips publicly by default. "
            "one user called it malware for reinstalling itself after uninstall. they weren't wrong."
        ))
        layout.addWidget(_gap(8))
        layout.addWidget(_p(
            "so i made mitten. your clips stay on your machine. "
            "no account, no cloud, no bullshit."
        ))

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_h("what it does", 15, C.GREEN))
        layout.addWidget(_gap(10))
        features = [
            ("replay buffer",     "last n seconds always rolling in ram. something happened? save it."),
            ("one button save",   "any mouse button you want. press it, clip saved."),
            ("game detection",    "sees when a game opens and switches capture automatically"),
            ("watermarking",      "burns your tag into every clip. fully customizable — text, size, position, opacity. "
                                  "one small 'mitten' credit stays in the corner. that's the whole business model."),
            ("basically no overhead", "gpu-screen-recorder uses your hardware encoder so cpu barely moves"),
            ("wayland native",    "built for wayland, not ported to it"),
        ]
        for title, desc in features:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            dot = QLabel("\u25b8")
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color: {C.LAVENDER}; font-size: 13px; background: transparent;")
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            txt = _p(f"<b>{title}</b>  {desc}")
            row.addWidget(txt, 1)
            layout.addLayout(row)
            layout.addWidget(_gap(4))

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_h("vs medal", 15, C.PINK))
        layout.addWidget(_gap(10))
        layout.addWidget(_ReviewSlider())

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_h("built with", 15, C.ORANGE))
        layout.addWidget(_gap(12))
        pills_row = QHBoxLayout()
        pills_row.setContentsMargins(0, 0, 0, 0)
        pills_row.setSpacing(8)
        pills_data = [
            ("gpu-screen-recorder", C.GREEN,
             "powers the replay buffer, uses your gpu's hardware encoder (nvenc) so cpu barely moves"),
            ("ffmpeg", C.ORANGE,
             "does all post-processing, watermarking, compression, and clip trimming"),
            ("python", C.BLUE,
             "the whole app runs here, daemon, gui, trigger listener, save pipeline, everything"),
            ("pyqt6", C.LAVENDER,
             "the gui framework, draws every window, button, animation, and overlay"),
            ("evdev", C.PINK,
             "reads raw mouse input from the kernel so mitten can detect your trigger button"),
            ("pipewire", C.BLUE,
             "audio capture, records system audio alongside the video in the buffer"),
        ]
        for label, color, tip in pills_data:
            p = _pill(label, color)
            p.setToolTip(tip)
            pills_row.addWidget(p)
        pills_row.addStretch()
        layout.addLayout(pills_row)

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        _GITHUB = "https://github.com/mitmitmitmitmitmit/mitten"
        _COMMIT = _GITHUB + "/commit/{}"

        ch_row = QHBoxLayout()
        ch_row.setContentsMargins(0, 0, 0, 0)
        ch_row.setSpacing(8)
        ch_row.addWidget(_h("changelog", 15, C.SUBTEXT))
        hist_lbl = QLabel(f'<a href="{_GITHUB}/commits/main">full history →</a>')
        hist_lbl.setOpenExternalLinks(True)
        hist_lbl.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 11px; background: transparent;"
        )
        hist_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ch_row.addWidget(hist_lbl, 1)
        layout.addLayout(ch_row)
        layout.addWidget(_gap(10))

        _changelog_entries = [
            ("0.2.25",   "0000000", "about page polish, README cleanup, copy pass", False),
            ("0.2.24",   "fac86b6", "discord rich presence, dead code cleanup", False),
            ("0.2.23.1", "15fe242", "README rewrite, dialog title fix, ui text cleanup", True),
            ("0.2.23",   "01a3ef5", "adaptive cat system, nav look-around, clips vibe cycle", False),
            ("0.2.22",   "4169948", "triple-click session recording", False),
            ("0.2.21",   "dd26855", "restart dialog fix", True),
            ("0.2.20.9", "f9217ab", "adaptive cats, wink system, light mode crash fixes", True),
            ("0.2.20.8", "8335970", "shame watermark, audio device dropdown, review slider", True),
            ("0.2.19.1", "d11ea99", "light mode abuse sounds, hint label fixes", True),
            ("0.2.19",   "00e622c", "dev mode, theme overhaul, sounds, CQ slider, dual codec", False),
        ]
        try:
            from . import themes as _themes_mod
            if _themes_mod.LIGHT_MODE_ACTIVE:
                _hotfix_idxs = [i for i, e in enumerate(_changelog_entries) if e[3]]
                if _hotfix_idxs:
                    _target = random.choice(_hotfix_idxs)
                    _v, _h, _n, _hf = _changelog_entries[_target]
                    _changelog_entries[_target] = (_v, _h, _themes_mod.get_abuse(include_name=False), _hf)
        except Exception:
            pass
        for ver, commit_hash, notes, is_hotfix in _changelog_entries:
            row = QHBoxLayout()
            indent = 18 if is_hotfix else 0
            row.setContentsMargins(indent, 0, 0, 0)
            row.setSpacing(14)
            ver_color = _accent_rgba(0.55) if is_hotfix else C.LAVENDER
            ver_size = 10 if is_hotfix else 11
            v_lbl = QLabel(
                f'<a href="{_COMMIT.format(commit_hash)}" '
                f'style="color:{ver_color}; font-size:{ver_size}px; font-weight:600; '
                f'text-decoration:none;">{ver}</a>'
            )
            v_lbl.setOpenExternalLinks(True)
            v_lbl.setFixedWidth(70)
            v_lbl.setStyleSheet("background: transparent;")
            n_lbl = QLabel(notes)
            n_lbl.setWordWrap(True)
            note_size = 10 if is_hotfix else 11
            n_lbl.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: {note_size}px; background: transparent;"
            )
            row.addWidget(v_lbl)
            row.addWidget(n_lbl, 1)
            layout.addLayout(row)
            layout.addWidget(_gap(3 if is_hotfix else 5))

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        gh_lbl = QLabel(f'<a href="{_GITHUB}">view source on github</a>')
        gh_lbl.setOpenExternalLinks(True)
        gh_lbl.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 12px; background: transparent;"
            f"text-decoration: none;"
        )
        layout.addWidget(gh_lbl)

        layout.addWidget(_gap(16))
        layout.addWidget(_SysInfoSection())

        layout.addWidget(_gap(20))
        layout.addWidget(_p("made with \u2665 by mit", C.SUBTEXT, 11))
        layout.addStretch()

        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch()
        wl.addWidget(inner)
        wl.addStretch()
        scroll.setWidget(wrapper)

        outer.addWidget(scroll)



class _DebugPage(QWidget):
    """Developer debug panel — test notifications, system info, log viewer, extra tools."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        inner = QWidget()
        inner.setMaximumWidth(820)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(40, 36, 40, 40)
        layout.setSpacing(0)

        # ─── local builder helpers (same visual language as _AboutPage) ─── #

        def _h(text: str, size: int = 15, color: str = C.LAVENDER) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {color}; font-size: {size}px; font-weight: 700;"
                f"background: transparent; {CAT_FONT}"
            )
            return lbl

        def _sub(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: 12px; background: transparent;"
            )
            return lbl

        def _gap(px: int = 14) -> QWidget:
            w = QWidget()
            w.setFixedHeight(px)
            w.setStyleSheet("background: transparent;")
            return w

        def _divider() -> QWidget:
            line = QWidget()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background-color: {_hex_rgba(C.BORDER, 0.4)};")
            return line

        def _sep(label: str) -> QWidget:
            """Uppercase section header + horizontal rule, matching about page style."""
            w = QWidget()
            w.setStyleSheet("background: transparent;")
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: 10px; font-weight: 700;"
                f"letter-spacing: 1.5px; background: transparent;"
            )
            row.addWidget(lbl)
            rule = QWidget()
            rule.setFixedHeight(1)
            rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            rule.setStyleSheet(f"background-color: {_hex_rgba(C.BORDER, 0.4)};")
            row.addWidget(rule, 1)
            return w

        def _card_frame(danger: bool = False) -> tuple[QFrame, QHBoxLayout]:
            """Surface card — C.SURFACE background, C.BORDER border, 8px radius."""
            border = C.BORDER if not danger else _hex_rgba(C.PINK, 0.35)
            frame = QFrame()
            frame.setStyleSheet(
                f"QFrame {{ background: {C.SURFACE}; border-radius: 8px;"
                f"border: 1px solid {border}; }}"
            )
            fl = QHBoxLayout(frame)
            fl.setContentsMargins(16, 12, 16, 12)
            fl.setSpacing(12)
            return frame, fl

        def _action_row(
            desc: str,
            btn_text: str,
            slot,
            *,
            danger: bool = False,
            warn_text: str = "",
        ) -> QFrame:
            """Card row: description (+ optional warning) on left, button on right."""
            frame, fl = _card_frame(danger=danger)
            col = QVBoxLayout()
            col.setSpacing(2)
            dl = QLabel(desc)
            dl.setStyleSheet(
                f"color: {C.TEXT}; font-size: 12px; background: transparent; border: none;"
            )
            dl.setWordWrap(True)
            col.addWidget(dl)
            if warn_text:
                wl = QLabel(warn_text)
                wl.setStyleSheet(
                    f"color: {C.PINK}; font-size: 10px; background: transparent; border: none;"
                )
                col.addWidget(wl)
            fl.addLayout(col, 1)
            btn = QPushButton(btn_text)
            if danger:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {C.PINK}; color: {C.BG};"
                    f" border: none; border-radius: 6px; padding: 6px 16px; font-weight: 600; }}"
                    f"QPushButton:hover {{ background-color: {_hex_rgba(C.PINK, 0.8)}; }}"
                    f"QPushButton:pressed {{ background-color: {_hex_rgba(C.PINK, 0.6)}; }}"
                )
            else:
                btn.setProperty("class", "secondary")
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            fl.addWidget(btn)
            return frame

        def _stat_cell(title: str) -> tuple[QWidget, QLabel]:
            """Stat cell with bold value + small label — for metrics grid."""
            cell = QWidget()
            cell.setStyleSheet(
                f"background: {C.OVERLAY}; border-radius: 6px; border: 1px solid {C.BORDER};"
            )
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(3)
            val_lbl = QLabel("—")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setStyleSheet(
                f"color: {C.TEXT}; font-size: 18px; font-weight: bold;"
                f"background: transparent; border: none;"
            )
            tl = QLabel(title)
            tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tl.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: 10px; background: transparent; border: none;"
            )
            cl.addWidget(val_lbl)
            cl.addWidget(tl)
            return cell, val_lbl

        def _kv_row(card_layout: QVBoxLayout, key: str) -> QLabel:
            """Monospace key → value row inside a card. Returns the value label."""
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            k = QLabel(key)
            k.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: 11px; font-family: monospace;"
                f"background: transparent; border: none;"
            )
            v = QLabel("—")
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            v.setStyleSheet(
                f"color: {C.TEXT}; font-size: 11px; font-family: monospace;"
                f"background: transparent; border: none;"
            )
            rl.addWidget(k, 1)
            rl.addWidget(v)
            card_layout.addWidget(row_w)
            return v

        layout.addWidget(_h("debug panel", 18, C.LAVENDER))
        layout.addWidget(_gap(4))
        layout.addWidget(_sub("developer tools — only shown when developer_mode is enabled"))
        layout.addWidget(_gap(22))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_sep("DAEMON STATUS"))
        layout.addWidget(_gap(10))

        status_frame = QFrame()
        status_frame.setStyleSheet(
            f"QFrame {{ background: {C.SURFACE}; border-radius: 8px;"
            f"border: 1px solid {C.BORDER}; }}"
        )
        status_body = QVBoxLayout(status_frame)
        status_body.setContentsMargins(16, 12, 16, 12)
        status_body.setSpacing(6)
        self._lbl_recorder_pid   = _kv_row(status_body, "recorder pid")
        self._lbl_socket_status  = _kv_row(status_body, "gui socket")
        layout.addWidget(status_frame)

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_sep("TESTING"))
        layout.addWidget(_gap(10))
        layout.addWidget(_action_row(
            "Send a test desktop notification",
            "Test notification",
            self._test_notification,
        ))
        layout.addWidget(_gap(8))
        layout.addWidget(_action_row(
            "Force-save a clip now — sends SIGUSR1 to the recorder (same as the trigger button)",
            "Force save clip now",
            self._force_save,
        ))

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_sep("CLIP METRICS"))
        layout.addWidget(_gap(10))

        metrics_grid_row = QHBoxLayout()
        metrics_grid_row.setSpacing(8)
        cell_clips, self._metric_clips = _stat_cell("total clips")
        cell_avg,   self._metric_avg   = _stat_cell("avg save time")
        cell_size,  self._metric_size  = _stat_cell("total size saved")
        cell_comp,  self._metric_comp  = _stat_cell("compression rate")
        for cell in (cell_clips, cell_avg, cell_size, cell_comp):
            metrics_grid_row.addWidget(cell)
        layout.addLayout(metrics_grid_row)

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_sep("SYSTEM DETECTION"))
        layout.addWidget(_gap(10))

        self._sys_info = QLabel("loading\u2026")
        self._sys_info.setWordWrap(True)
        self._sys_info.setStyleSheet(
            f"QLabel {{ color: {C.TEXT}; font-size: 11px; font-family: monospace;"
            f"background: {C.SURFACE}; border-radius: 6px;"
            f"border: 1px solid {C.BORDER}; padding: 12px; }}"
        )
        layout.addWidget(self._sys_info)
        self._populate_sys_info()

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_sep("FILE ACTIONS"))
        layout.addWidget(_gap(10))
        layout.addWidget(_action_row(
            "Open config file in default editor",
            "Open config file",
            self._open_config,
        ))
        layout.addWidget(_gap(8))
        layout.addWidget(_action_row(
            "Open clips folder in file manager",
            "Open clips folder",
            self._open_clips,
        ))
        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_sep("DANGER ZONE"))
        layout.addWidget(_gap(10))
        layout.addWidget(_action_row(
            "Clear replay buffer",
            "Clear buffer",
            self._clear_buffer,
            danger=True,
            warn_text="warning: current footage will be lost",
        ))

        layout.addWidget(_gap(24))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        log_hdr_row = QHBoxLayout()
        log_hdr_row.setContentsMargins(0, 0, 0, 0)
        log_hdr_row.setSpacing(10)
        log_hdr_row.addWidget(_sep("DAEMON LOG"), 1)
        btn_refresh_log = QPushButton("Refresh")
        btn_refresh_log.setProperty("class", "secondary")
        btn_refresh_log.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        btn_refresh_log.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh_log.clicked.connect(self._refresh_log)
        log_hdr_row.addWidget(btn_refresh_log)
        layout.addLayout(log_hdr_row)
        layout.addWidget(_gap(10))

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(240)
        self._log_view.setStyleSheet(
            f"QPlainTextEdit {{ background: {C.BG}; color: {C.TEXT};"
            f"font-family: monospace; font-size: 11px;"
            f"border: 1px solid {C.BORDER}; border-radius: 6px; padding: 8px; }}"
        )
        layout.addWidget(self._log_view, 1)

        layout.addStretch()

        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch()
        wl.addWidget(inner)
        wl.addStretch()
        scroll.setWidget(wrapper)

        outer.addWidget(scroll)

        self._log_loaded = False

    def _refresh_status(self) -> None:
        """Update recorder PID and socket status labels."""
        try:
            pid = get_daemon_pid()
            if pid:
                try:
                    comm = Path(f"/proc/{pid}/comm").read_text().strip()
                    self._lbl_recorder_pid.setText(f"{pid}  ({comm})")
                except OSError:
                    self._lbl_recorder_pid.setText(str(pid))
            else:
                self._lbl_recorder_pid.setText("not running")
        except Exception:
            self._lbl_recorder_pid.setText("—")

        try:
            from ..config import GUI_SOCKET
            import socket as _sock
            if GUI_SOCKET.exists():
                try:
                    s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect(str(GUI_SOCKET))
                    s.close()
                    self._lbl_socket_status.setText("alive")
                except Exception:
                    self._lbl_socket_status.setText("stale file")
            else:
                self._lbl_socket_status.setText("not found")
        except Exception:
            self._lbl_socket_status.setText("—")

    def _refresh_metrics(self) -> None:
        """Load and display clip metrics."""
        try:
            from ..metrics import load_metrics, avg_save_time, compression_rate
            clips = load_metrics()
            n = len(clips)
            self._metric_clips.setText(str(n) if n else "0")
            if clips:
                avg = avg_save_time()
                self._metric_avg.setText(f"{avg:.1f}s" if avg is not None else "—")
                total_mb = sum(m.final_size_mb for m in clips)
                self._metric_size.setText(f"{total_mb:.1f} MB")
                comp = compression_rate()
                self._metric_comp.setText(f"{comp * 100:.0f}%" if comp is not None else "—")
            else:
                self._metric_avg.setText("—")
                self._metric_size.setText("—")
                self._metric_comp.setText("—")
        except Exception:
            self._metric_clips.setText("—")
            self._metric_avg.setText("—")
            self._metric_size.setText("—")
            self._metric_comp.setText("—")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._log_loaded:
            self._refresh_log()
            self._log_loaded = True
        self._refresh_status()
        self._refresh_metrics()

    def _test_notification(self) -> None:
        try:
            from ..notify import notify
            notify(
                "mitten debug test",
                "this is a test notification from the debug panel",
            )
        except Exception as exc:
            self._log_view.appendPlainText(f"[notification error] {exc}")

    def _force_save(self) -> None:
        """Send SIGUSR1 to the recorder PID to trigger a clip save."""
        try:
            import os, signal as _signal
            pid = get_daemon_pid()
            if pid is None:
                self._log_view.appendPlainText("[force save] daemon is not running")
                return
            os.kill(pid, _signal.SIGUSR1)
            self._log_view.appendPlainText(f"[force save] SIGUSR1 sent to PID {pid}")
        except Exception as exc:
            self._log_view.appendPlainText(f"[force save error] {exc}")

    def _open_config(self) -> None:
        from ..config import CONFIG_FILE
        subprocess.Popen(["xdg-open", str(CONFIG_FILE)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _open_clips(self) -> None:
        try:
            from ..config import load_config
            clips_dir = load_config().general.save_dir
        except Exception:
            clips_dir = Path.home() / "Videos" / "mitten"
        clips_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(clips_dir)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _reload_theme(self) -> None:
        try:
            from .themes import apply_theme, LIGHT_MODE_ACTIVE
            from ..config import load_config
            from .resources import make_stylesheet
            from PyQt6.QtWidgets import QApplication
            theme = load_config().general.theme
            apply_theme(theme)
            app = QApplication.instance()
            if app:
                app.setStyleSheet(make_stylesheet())
            self._log_view.appendPlainText(f"[reload theme] applied theme '{theme}'")
        except Exception as exc:
            self._log_view.appendPlainText(f"[reload theme error] {exc}")

    def _populate_sys_info(self) -> None:
        import os as _os
        lines: list[str] = []

        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                lines.append(f"gpu       {parts[0]}")
                if len(parts) >= 3:
                    lines.append(f"vram      {parts[2]} / {parts[1]} MiB used/total")
        except Exception:
            lines.append("gpu       (nvidia-smi not available)")

        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if "model name" in line:
                    cpu = line.split(":", 1)[1].strip()
                    lines.append(f"cpu       {cpu}")
                    break
        except Exception:
            lines.append("cpu       (unavailable)")

        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    ram_kb = int(line.split()[1])
                    lines.append(f"ram       {ram_kb // (1024 * 1024)} GB total")
                    break
        except Exception:
            lines.append("ram       (unavailable)")

        display = _os.environ.get("WAYLAND_DISPLAY") or _os.environ.get("DISPLAY") or "unknown"
        lines.append(f"display   {display}")

        try:
            r = subprocess.run(
                ["gpu-screen-recorder", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            ver = (r.stdout.strip() or r.stderr.strip()).split("\n")[0][:60] or "(no output)"
            lines.append(f"gsr       {ver}")
        except FileNotFoundError:
            lines.append("gsr       not found")
        except Exception as exc:
            lines.append(f"gsr       error: {exc}")

        self._sys_info.setText("\n".join(lines))

    def _clear_buffer(self) -> None:
        try:
            import os, signal as _signal
            pid = get_daemon_pid()
            if pid is None:
                self._log_view.appendPlainText("[clear buffer] daemon is not running")
                return
            os.kill(pid, _signal.SIGUSR1)
            self._log_view.appendPlainText("[clear buffer] SIGUSR1 sent to daemon")
        except Exception as exc:
            self._log_view.appendPlainText(f"[clear buffer error] {exc}")

    def _refresh_log(self) -> None:
        try:
            result = subprocess.run(
                ["journalctl", "--user", "-u", "mitten", "-n", "100",
                 "--no-pager", "--output=short"],
                capture_output=True, text=True, timeout=10,
            )
            text = result.stdout.strip() or "(no log output)"
        except Exception as exc:
            text = f"(error reading journal: {exc})"
        self._log_view.setPlainText(text)
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())


class MittenMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{CAT}  MITTEN")
        self.setMinimumSize(700, 520)
        self.resize(900, 620)
        self.setWindowIcon(paw_icon("recording"))
        self.setStyleSheet(f"QMainWindow {{ background-color: {C.BG}; }}")

        self._state = "idle"
        self._last_clip_path: Path | None = None
        self._schizo_tick: int = 0  # counts _refresh() calls; used for light-mode schizo effects

        self._gui_presence = None
        self._gui_presence_last_send: float = 0.0
        self._gui_presence_dirty: bool = False
        self._gui_cat_state: str = "sleepy"
        self._gui_settings_idx: int = 0

        try:
            from .system_setup import check_dependencies
            self._has_gsr = check_dependencies().get("gpu-screen-recorder", False)
        except Exception:
            self._has_gsr = True  # assume present if check fails

        self._settings_nav_active = False
        self._previous_page_idx: int = 0

        self._anims: dict[str, QPropertyAnimation] = {}
        self._stagger_timers: list[QTimer] = []

        self._build_ui()
        self._connect_signals()

        try:
            from ..config import load_config
            if load_config().general.developer_mode:
                self._nav_debug.setVisible(True)
        except Exception:
            pass

        self._gui_presence_timer = QTimer(self)
        self._gui_presence_timer.setInterval(5000)
        self._gui_presence_timer.timeout.connect(self._mark_gui_presence_dirty)
        self._gui_presence_timer.start()
        self._init_gui_presence()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)

        self._save_flash = QTimer(self)
        self._save_flash.setSingleShot(True)
        self._save_flash.timeout.connect(self._refresh)

        self._update_hashes: tuple[str, str] | None = None
        self._update_checker: _UpdateCheckerThread | None = None
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._run_update_check)
        self._update_timer.start(60_000)
        # Also run once 10s after launch (avoids slowing initial startup)
        QTimer.singleShot(10_000, self._run_update_check)

        self._refresh()

        self._wink_timer = QTimer(self)
        self._wink_timer.setSingleShot(True)
        self._wink_timer.timeout.connect(self._do_wink)
        self._wink_timer.start(self._next_wink_delay())

        self._lm_fake_update_timer = QTimer(self)
        self._lm_fake_update_timer.setSingleShot(True)
        self._lm_fake_update_timer.timeout.connect(self._show_fake_update_dialog)
        try:
            from . import themes as _t
            if _t.LIGHT_MODE_ACTIVE:
                self._lm_fake_update_timer.start(30 * 60 * 1000)
        except Exception:
            pass

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(140)
        sidebar.setStyleSheet(
            f"background-color: {_hex_rgba(C.BG, 0.97)};"
            f"border-right: 1px solid {_hex_rgba(C.BORDER, 0.35)};"
        )

        self._sidebar_layout = QVBoxLayout(sidebar)
        self._sidebar_layout.setContentsMargins(0, 14, 0, 14)
        self._sidebar_layout.setSpacing(0)

        try:
            from . import themes as _themes_mod
            _initial_cat = _themes_mod.get_light_mode_cat() if _themes_mod.LIGHT_MODE_ACTIVE else CAT
        except Exception:
            _initial_cat = CAT
        logo = QLabel(_initial_cat)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 20px; font-weight: 700;"
            f"padding: 6px 0 1px 0; {CAT_FONT}"
        )
        try:
            from . import themes as _themes_mod
            if _themes_mod.LIGHT_MODE_ACTIVE:
                logo.setToolTip(_themes_mod.get_abuse())
        except Exception:
            pass
        self._logo_label = logo
        self._logo_stage = 0
        self._sidebar_layout.addWidget(logo)

        self._cat_stage_timer = QTimer(self)
        self._cat_stage_timer.setInterval(60_000)
        self._cat_stage_timer.timeout.connect(self._update_cat_stage)
        try:
            from . import themes as _themes_mod
            if _themes_mod.LIGHT_MODE_ACTIVE:
                self._cat_stage_timer.start()
        except Exception:
            pass

        title = QLabel("MITTEN")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {_hex_rgba(C.TEXT, 0.7)}; font-size: 12px; font-weight: 700;"
            f"letter-spacing: 4px; padding: 0 0 20px 0;"
        )
        self._sidebar_layout.addWidget(title)

        self._nav_dashboard = _NavButton("Dashboard")
        self._nav_clips     = _NavButton("Clips")
        self._nav_settings  = _NavButton("Settings")
        self._nav_about     = _NavButton("About")
        self._nav_debug     = _NavButton("Debug")
        self._nav_debug.setVisible(False)  # hidden unless developer_mode is on
        self._nav_dashboard.setChecked(True)
        self._main_nav_buttons = [
            self._nav_dashboard, self._nav_clips, self._nav_settings, self._nav_about,
        ]
        for btn in self._main_nav_buttons:
            self._sidebar_layout.addWidget(btn)
        self._sidebar_layout.addWidget(self._nav_debug)

        try:
            from . import themes as _themes_mod
            if _themes_mod.LIGHT_MODE_ACTIVE:
                _abuse_targets = random.sample(
                    [self._nav_clips, self._nav_settings, self._nav_about], 2
                )
                for _btn in _abuse_targets:
                    _btn.setToolTip(_themes_mod.get_abuse())
        except Exception:
            pass

        self._nav_back = _NavButton("\u2190  Back")
        self._nav_back.setVisible(False)

        self._settings_nav_buttons: list[_NavButton] = []
        for name in ["General", "Recording", "Compression", "Watermark", "Games", "Discord"]:
            btn = _NavButton(name)
            btn.setVisible(False)
            self._settings_nav_buttons.append(btn)
            self._sidebar_layout.addWidget(btn)

        self._sidebar_layout.addWidget(self._nav_back)

        self._sidebar_layout.addStretch()

        try:
            from .. import __version__
            self._current_ver = __version__
        except Exception:
            self._current_ver = "?"

        # Normal version label — hidden when update is available
        self._ver_label = QLabel(f"mitten  v{self._current_ver}")
        self._ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ver_label.setStyleSheet(
            f"color: {_hex_rgba(C.SUBTEXT, 0.45)}; font-size: 10px; font-weight: 600;"
            f"letter-spacing: 0.5px; padding-bottom: 2px;"
        )

        # Update-available row: old (red) → arrow → new (green)
        self._ver_update_widget = QWidget()
        self._ver_update_widget.setStyleSheet("background: transparent;")
        _vu_layout = QVBoxLayout(self._ver_update_widget)
        _vu_layout.setContentsMargins(8, 0, 8, 2)
        _vu_layout.setSpacing(2)

        self._ver_row_label = QLabel()  # set dynamically: "v{old} → v{new}"
        self._ver_row_label.setStyleSheet("background: transparent;")

        self._ver_warning_label = QLabel("⚠ not meant to be non-updateable")
        self._ver_warning_label.setWordWrap(True)
        self._ver_warning_label.setStyleSheet(
            f"color: {_hex_rgba(C.ORANGE, 0.7)}; font-size: 8px;"
            f"padding-left: 0px; background: transparent;"
        )

        _vu_layout.addWidget(self._ver_row_label)
        _vu_layout.addWidget(self._ver_warning_label)
        self._ver_update_widget.hide()

        made_label = QLabel("made with ♥ by mit")
        made_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        made_label.setWordWrap(True)
        made_label.setStyleSheet(
            f"color: {_hex_rgba(C.SUBTEXT, 0.28)}; font-size: 9px; padding: 0 8px 6px 8px;"
        )
        self._sidebar_layout.addWidget(self._ver_label)
        self._sidebar_layout.addWidget(self._ver_update_widget)
        self._sidebar_layout.addWidget(made_label)

        root.addWidget(sidebar)

        self._pages = QStackedWidget()
        self._pages.setStyleSheet(f"background-color: {C.BG};")

        self._dashboard = _DashboardPage()
        self._pages.addWidget(self._dashboard)  # index 0

        from .clips import ClipBrowser
        self._clips_page = ClipBrowser()
        self._pages.addWidget(self._clips_page)  # index 1

        from .settings import SettingsDialog
        self._settings_page = SettingsDialog()
        self._pages.addWidget(self._settings_page)  # index 2

        self._about_page = _AboutPage()
        self._pages.addWidget(self._about_page)     # index 3

        self._debug_page = _DebugPage()
        self._pages.addWidget(self._debug_page)     # index 4

        root.addWidget(self._pages, 1)

    def _next_wink_delay(self) -> int:
        import random as _r
        return _r.randint(45_000, 120_000)

    def _do_wink(self) -> None:
        """Briefly swap the sidebar cat to a winking variant, then restore. Dark mode only."""
        try:
            from . import themes as _t
            if _t.LIGHT_MODE_ACTIVE:
                self._wink_timer.start(self._next_wink_delay())
                return
            if self._pages.currentIndex() == 1 and self._clips_page._is_vibing:
                self._wink_timer.start(self._next_wink_delay())
                return
            import random as _r
            wink = _r.choice([_t.DARK_CAT_WINK, _t.DARK_CAT_WINK2])
            self._logo_label.setText(wink)
            if _r.random() < 0.30:
                _t.play_dark_meow()
            page = self._pages.currentIndex()
            QTimer.singleShot(400, lambda: self._logo_label.setText(
                _t.get_page_cat(page, app_state=self._state)
            ))
        except Exception:
            pass
        self._wink_timer.start(self._next_wink_delay())

    def _show_fake_update_dialog(self) -> None:
        """30-minute light mode punishment: fake update required dialog."""
        try:
            from . import themes as _t
            if not _t.LIGHT_MODE_ACTIVE:
                return
        except Exception:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Update Required")
        dlg.setFixedWidth(420)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("MITTEN Update Required")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)
        notes = QLabel(
            "v0.2.21 — Critical patch available\n\n"
            "• Dark Mode Performance Improvements (+40% fps)\n"
            "• Light Mode Stability Issues Fixed (there are many)\n"
            "• Memory leak patched (light mode only, obviously)\n"
            "• Addressed user taste regression introduced in v0.2.20\n\n"
            "Update required to continue. switch to dark mode freak."
        )
        notes.setWordWrap(True)
        notes.setStyleSheet("font-size: 12px;")
        layout.addWidget(notes)
        btn_row = QHBoxLayout()
        btn_later = QPushButton("Remind Me Later")
        btn_later.clicked.connect(dlg.accept)
        btn_row.addStretch()
        btn_row.addWidget(btn_later)
        layout.addLayout(btn_row)
        dlg.exec()

    def _update_cat_stage(self) -> None:
        """Check if light mode anger stage has advanced; update logo + play meow."""
        try:
            from . import themes as _t
            if not _t.LIGHT_MODE_ACTIVE:
                self._cat_stage_timer.stop()
                return
            stage = _t.get_light_mode_stage()
            if stage != self._logo_stage:
                self._logo_stage = stage
                self._logo_label.setText(_t.get_light_mode_cat())
                _t.play_stage_meow()
        except Exception:
            pass

    def _on_clip_cat_state(self, state: str) -> None:
        """Clips page emitted a cat state — update sidebar logo if we're on the clips page."""
        if self._pages.currentIndex() != 1:
            return
        try:
            from . import themes as _t
            self._logo_label.setText(_t.get_state_cat(state))
        except Exception:
            pass
        self._gui_cat_state = state
        self._mark_gui_presence_dirty()

    def _on_settings_section_look(self, direction: str) -> None:
        """Cat glances when switching settings sections, then settles back to settings cat."""
        try:
            from . import themes as _t
            self._logo_label.setText(_t.get_look_cat(direction))
            QTimer.singleShot(350, lambda: self._logo_label.setText(
                _t.get_page_cat(2, app_state=self._state)
            ))
        except Exception:
            pass

    def _do_nav_look(self, direction: str, dest_page: int) -> None:
        """Flash a look-left/right cat on the sidebar logo for ~450ms, then settle on
        the destination page cat. Gives the feeling of the cat glancing toward the new page."""
        try:
            from . import themes as _t
            look_cat = _t.get_look_cat(direction)
            self._logo_label.setText(look_cat)

            def _settle() -> None:
                if dest_page == 1 and self._clips_page._is_vibing:
                    return
                page_cat = _t.get_page_cat(dest_page, app_state=self._state)
                self._logo_label.setText(page_cat)

            QTimer.singleShot(450, _settle)
        except Exception:
            pass

    def _init_gui_presence(self) -> None:
        try:
            from ..config import load_config
            from ..discord_presence import DiscordPresence
            cfg = load_config()
            if not cfg.discord.enabled:
                return
            self._gui_presence = DiscordPresence()
            self._gui_presence.update_config(show_ascii=cfg.discord.show_ascii)
            self._gui_presence.start()
            self._mark_gui_presence_dirty()
        except Exception:
            pass

    def _mark_gui_presence_dirty(self) -> None:
        self._gui_presence_dirty = True
        import time
        if time.time() - self._gui_presence_last_send >= 4.0:
            self._flush_gui_presence()

    def _flush_gui_presence(self) -> None:
        if not self._gui_presence_dirty or self._gui_presence is None:
            return
        import time
        self._gui_presence_dirty = False
        self._gui_presence_last_send = time.time()
        try:
            from . import themes as _t
            from ..config import load_config
            dc = load_config().discord
            if not dc.enabled:
                return
            self._gui_presence.update_config(show_ascii=dc.show_ascii)

            # Light mode always hijacks
            if _t.LIGHT_MODE_ACTIVE:
                lm_cat = _t.get_light_mode_cat()
                detail = f"{lm_cat}  why" if dc.show_ascii else "why"
                self._gui_presence.set_state(
                    "idle",
                    state_override="light mode loving FREAK",
                    detail_override=detail,
                    name_override="Mitten (L)",
                )
                return

            page = self._pages.currentIndex()
            state_ov, detail_ov, name_ov = self._gui_presence_strings(page, dc)
            self._gui_presence.set_state(
                "idle",
                state_override=state_ov,
                detail_override=detail_ov,
                name_override=name_ov if dc.show_name else None,
            )
        except Exception:
            pass

    def _gui_presence_strings(self, page: int, dc) -> tuple[str, str, str]:
        """Return (state_override, detail_override, name_override) for current GUI context."""
        from .themes import (
            get_state_cat,
            DARK_CAT_IDLE, DARK_CAT_SLEEPY, DARK_CAT_ABOUT, DARK_CAT_DEBUG,
            DARK_CAT_SETTINGS, DARK_CAT_VIBE_1, DARK_CAT_VIBE_2, DARK_CAT_VIBE_3,
            DARK_CAT_STARTLED,
        )

        def _cat(state: str) -> str:
            return get_state_cat(state) if dc.animated_ascii else DARK_CAT_IDLE

        if page == 0:  # Dashboard
            cat = get_state_cat(self._state) if dc.animated_ascii else DARK_CAT_IDLE
            return "on the dashboard", f"{cat}  on the dashboard", "customizing Mitten"

        elif page == 1:  # Clips
            cat_state = self._gui_cat_state
            if cat_state in ("vibe_1", "vibe_2", "vibe_3"):
                vibe_cat = get_state_cat(cat_state) if dc.animated_ascii else DARK_CAT_VIBE_1
                return "watching a clip", f"{vibe_cat}  watching a clip", "watching a clip with Mitten"
            elif cat_state == "startled":
                return "watching a clip", f"{DARK_CAT_STARTLED}  a clip just dropped", "watching a clip with Mitten"
            else:
                return "browsing clips", f"{DARK_CAT_SLEEPY}  browsing clips", "customizing Mitten"

        elif page == 2:  # Settings
            _sections = ["general", "recording", "compression", "watermark", "games", "discord"]
            section = _sections[self._gui_settings_idx] if self._gui_settings_idx < len(_sections) else "settings"
            cat = _cat("settings")
            return f"in settings \u2014 {section}", f"{cat}  tweaking settings", "customizing Mitten"

        elif page == 3:  # About
            cat = _cat("about")
            return "about", f"{cat}  reading about Mitten", "customizing Mitten"

        elif page == 4:  # Debug
            cat = _cat("debug")
            return "debug mode", f"{cat}  in debug mode", "customizing Mitten"

        return "in Mitten", f"{DARK_CAT_IDLE}  in Mitten", "customizing Mitten"

    def _show_light_mode_discord_block(self) -> None:
        try:
            from . import themes as _t
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
            lm_cat = _t.get_light_mode_cat()
            dlg = QDialog(self)
            dlg.setWindowTitle("blocked")
            dlg.setFixedWidth(380)
            dlg.setStyleSheet("QDialog { background-color: #080808; color: #f0f0f0; }")
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(28, 28, 28, 28)
            lay.setSpacing(18)
            cat_lbl = QLabel(lm_cat)
            cat_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_lbl.setStyleSheet("font-size: 28px; color: #ff5555;")
            lay.addWidget(cat_lbl)
            msg = QLabel("light mode loving freaks are only allowed\nto be publicly humiliated")
            msg.setWordWrap(True)
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet("font-size: 14px; color: #f0f0f0; line-height: 1.4;")
            lay.addWidget(msg)
            sub = QLabel("your discord presence has been set to\n\"light mode loving FREAK\"\nand there is nothing you can do about it")
            sub.setWordWrap(True)
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub.setStyleSheet("font-size: 11px; color: #888; font-style: italic;")
            lay.addWidget(sub)
            ok_btn = QPushButton("ok i deserved that")
            ok_btn.setStyleSheet(
                "QPushButton { background-color: #1a1a1a; color: #aaa; border: 1px solid #333;"
                " border-radius: 5px; padding: 7px 24px; font-size: 12px; }"
                "QPushButton:hover { background-color: #222; }"
            )
            ok_btn.clicked.connect(dlg.accept)
            lay.addWidget(ok_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            dlg.exec()
        except Exception:
            pass

    def _connect_signals(self) -> None:
        self._nav_dashboard.clicked.connect(lambda: self._switch_main_page(0))
        self._nav_clips.clicked.connect(lambda: self._switch_main_page(1))
        self._nav_settings.clicked.connect(self._enter_settings)
        self._nav_about.clicked.connect(lambda: self._switch_main_page(3))
        self._nav_debug.clicked.connect(lambda: self._switch_main_page(4))
        self._nav_back.clicked.connect(self._exit_settings)

        self._clips_page.cat_state_changed.connect(self._on_clip_cat_state)

        for i, btn in enumerate(self._settings_nav_buttons):
            btn.clicked.connect(lambda _, idx=i: self._switch_settings_section(idx))

        btn = self._dashboard.banner._btn_toggle
        btn.clicked.connect(self._toggle_recording)
        btn.pressed.connect(lambda: self._btn_press_dip(btn))

        self._dashboard.banner._btn_update.clicked.connect(self._do_update)

        pause_btn = self._dashboard.banner._btn_pause
        pause_btn.clicked.connect(self._toggle_pause)
        pause_btn.pressed.connect(lambda: self._btn_press_dip(pause_btn))

        # Developer mode toggle from settings (no restart required)
        self._settings_page.developer_mode_toggled.connect(self._on_dev_mode_toggled)
        self._settings_page.section_changed.connect(self._on_settings_section_look)

    def _start_anim(self, key: str, anim: QPropertyAnimation) -> None:
        """Stop any existing animation at this key, register and start the new one."""
        old = self._anims.get(key)
        if old and old.state() == QPropertyAnimation.State.Running:
            old.stop()
        self._anims[key] = anim
        if anim.state() != QPropertyAnimation.State.Running:
            anim.start()

    def _btn_press_dip(self, btn: QPushButton) -> None:
        """Brief opacity dip on button press."""
        from .anim import fade_out, fade_in
        anim = fade_out(btn, duration_ms=50, on_done=lambda: fade_in(btn, duration_ms=100))
        self._start_anim(f"btn_dip_{id(btn)}", anim)

    def _switch_main_page(self, index: int) -> None:
        if self._pages.currentIndex() == index:
            return
        prev_index = self._pages.currentIndex()
        direction = "right" if index > prev_index else "left"
        self._do_nav_look(direction, dest_page=index)
        self._fade_to(index)
        for i, btn in enumerate(self._main_nav_buttons):
            btn.setChecked(i == index)
        # Debug button (index 4) is outside _main_nav_buttons
        self._nav_debug.setChecked(index == 4)
        self._mark_gui_presence_dirty()

    def _enter_settings(self) -> None:
        """Fade sidebar from main nav to settings sub-nav."""
        self._previous_page_idx = self._pages.currentIndex()
        self._settings_nav_active = True
        self._fade_sidebar(show_settings=True)
        self._fade_to(2)
        self._switch_settings_section(0)
        self._mark_gui_presence_dirty()

    def _exit_settings(self) -> None:
        """Return from settings sub-nav to main nav."""
        self._settings_nav_active = False
        self._fade_sidebar(show_settings=False)
        self._fade_to(self._previous_page_idx)
        for i, btn in enumerate(self._main_nav_buttons):
            btn.setChecked(i == self._previous_page_idx)
        try:
            from . import themes as _t
            self._logo_label.setText(_t.get_page_cat(self._previous_page_idx, app_state=self._state))
        except Exception:
            pass

    def _fade_sidebar(self, show_settings: bool) -> None:
        from .anim import staggered_fade
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        if show_settings:
            for btn in self._main_nav_buttons:
                btn.setVisible(False)
            # Debug button must be hidden in settings sub-nav — it belongs only to main nav
            self._nav_debug.setVisible(False)
            incoming = [self._nav_back] + self._settings_nav_buttons
        else:
            self._nav_back.setVisible(False)
            for btn in self._settings_nav_buttons:
                btn.setVisible(False)
            try:
                from ..config import load_config
                if load_config().general.developer_mode:
                    self._nav_debug.setVisible(True)
            except Exception:
                pass
            incoming = list(self._main_nav_buttons)

        # Cancel any pending stagger timers from a previous sidebar transition
        for t in self._stagger_timers:
            t.stop()
        self._stagger_timers.clear()

        # Pre-zero opacity before showing so there's no 1-frame flash
        for btn in incoming:
            eff = QGraphicsOpacityEffect(btn)
            eff.setOpacity(0.0)
            btn.setGraphicsEffect(eff)
            btn.setVisible(True)
        self._stagger_timers = staggered_fade(incoming, duration_ms=120, stagger_ms=25, fade_in_=True)

    def _switch_settings_section(self, idx: int) -> None:
        if idx == 5:  # Discord tab — blocked in light mode
            try:
                from . import themes as _t
                if _t.LIGHT_MODE_ACTIVE:
                    self._show_light_mode_discord_block()
                    return
            except Exception:
                pass
        self._settings_page.switch_section(idx)
        for i, btn in enumerate(self._settings_nav_buttons):
            btn.setChecked(i == idx)
        self._gui_settings_idx = idx
        self._mark_gui_presence_dirty()

    def _fade_to(self, index: int) -> None:
        if self._pages.currentIndex() == index:
            return
        prev_index = self._pages.currentIndex()
        direction = "left" if index > prev_index else "right"
        self._pages.setCurrentIndex(index)
        new_w = self._pages.currentWidget()
        from .anim import slide_fade_in
        self._start_anim("page_slide", slide_fade_in(new_w, direction=direction, distance=16, duration_ms=180))

    def _set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        self._dashboard.banner.set_state(state, detail)
        self.setWindowIcon(paw_icon(state))
        # Keep sidebar logo in sync with app state when on dashboard
        if self._pages.currentIndex() == 0:
            try:
                from . import themes as _t
                self._logo_label.setText(_t.get_state_cat(state))
            except Exception:
                pass

    def _daemon_pid(self) -> int | None:
        return get_daemon_pid()

    def _refresh(self) -> None:
        self._schizo_tick += 1
        pid = self._daemon_pid()

        if not self._has_gsr:
            self._set_state("no_deps")
        elif pid is None:
            self._set_state("idle")
        elif RECORDER_DEAD_FILE.exists():
            try:
                reason = RECORDER_DEAD_FILE.read_text().strip()
            except OSError:
                reason = "recorder gave up after repeated crashes"
            self._set_state("recorder_dead", reason)
        elif PAUSE_FILE.exists():
            self._set_state("paused")
        else:
            uptime_str = self._get_uptime_str(pid)
            detail = "mitten is watching\u2026"
            if uptime_str:
                detail += f" \u00b7 up {uptime_str}"
            self._set_state("recording", detail)

        self._refresh_memory(pid)
        self._refresh_vram()
        self._refresh_cpu(pid)
        self._refresh_clip_metrics()
        self._refresh_clip_preview()

        # ── Schizo light-mode effects ──────────────────────────────────
        try:
            from . import themes as _themes_mod
            if _themes_mod.LIGHT_MODE_ACTIVE:
                _abuse = _themes_mod.get_abuse

                # Window title flicker: every ~10 ticks, show insult for 1 tick then restore
                _base_title = f"{CAT}  MITTEN"
                if self._schizo_tick % 10 == 0:
                    self.setWindowTitle(_abuse())
                    QTimer.singleShot(1800, lambda: self.setWindowTitle(_base_title))

                # Stat card value flicker: ~25% chance, one card per tick
                if random.random() < 0.25:
                    _cards = [
                        self._dashboard.card_ram_mitten,
                        self._dashboard.card_vram,
                        self._dashboard.card_cpu,
                    ]
                    _card = random.choice(_cards)
                    _real = _card._value.text()
                    _card.set_value(_abuse(include_name=False))
                    QTimer.singleShot(1600, lambda c=_card, v=_real: c.set_value(v))
        except Exception:
            pass

    def _refresh_memory(self, pid: int | None) -> None:
        try:
            import psutil
            vm = psutil.virtual_memory()
            self._dashboard.card_ram_total.set_value(f"{vm.total / (1024**3):.1f} GB")
            self._dashboard.card_ram_used.set_value(f"{vm.used / (1024**3):.1f} GB")
        except Exception:
            self._dashboard.card_ram_total.set_value("\u2014")
            self._dashboard.card_ram_used.set_value("\u2014")

        if pid is None:
            self._dashboard.card_ram_mitten.set_value("\u2014")
            return
        try:
            import psutil
            proc = psutil.Process(pid)
            mb = proc.memory_info().rss / (1024 * 1024)
            for child in proc.children(recursive=True):
                try:
                    mb += child.memory_info().rss / (1024 * 1024)
                except Exception:
                    pass
            self._dashboard.card_ram_mitten.set_value(f"{mb:.0f} MB" if mb else "\u2014")
        except Exception:
            self._dashboard.card_ram_mitten.set_value("\u2014")

    def _get_uptime_str(self, pid: int) -> str:
        try:
            import psutil
            elapsed = time.time() - psutil.Process(pid).create_time()
            return format_duration(int(elapsed))
        except Exception:
            return ""

    def _refresh_vram(self) -> None:
        vram = get_vram_usage()
        if vram is not None:
            used_gb, total_gb = vram
            self._dashboard.card_vram.set_value(f"{used_gb:.1f} / {total_gb:.1f} GB")
        else:
            self._dashboard.card_vram.set_value("\u2014")

    def _refresh_cpu(self, pid: int | None = None) -> None:
        try:
            import psutil
            self._dashboard.card_cpu.set_value(f"{psutil.cpu_percent(interval=None):.0f}%")
        except Exception:
            self._dashboard.card_cpu.set_value("\u2014")

        if pid is None:
            self._dashboard.card_cpu_mitten.set_value("\u2014")
            return
        try:
            import psutil
            proc = psutil.Process(pid)
            mitten_cpu = proc.cpu_percent(interval=None)
            for child in proc.children(recursive=True):
                try:
                    mitten_cpu += child.cpu_percent(interval=None)
                except Exception:
                    pass
            self._dashboard.card_cpu_mitten.set_value(f"{mitten_cpu:.1f}%")
        except Exception:
            self._dashboard.card_cpu_mitten.set_value("\u2014")

    def _refresh_clip_metrics(self) -> None:
        try:
            from ..metrics import avg_save_time, clips_this_week, compression_rate
            week = clips_this_week()
            avg = avg_save_time()
            comp = compression_rate()
            self._dashboard.card_week.set_value(str(week))
            self._dashboard.card_avg_save.set_value(f"{avg:.1f}s" if avg is not None else "\u2014")
            self._dashboard.card_compress.set_value(f"{comp * 100:.0f}%" if comp is not None else "\u2014")
        except Exception:
            pass

    def _refresh_clip_preview(self) -> None:
        try:
            from ..config import load_config
            save_dir = load_config().general.save_dir
        except Exception:
            save_dir = Path.home() / "Videos" / "mitten"

        try:
            clips = sorted(save_dir.glob("mitten_*.mp4"), reverse=True)
        except (FileNotFoundError, OSError):
            self._dashboard.clip_preview.set_clip(None)
            return

        if not clips:
            self._dashboard.clip_preview.set_clip(None)
            return

        try:
            if clips[0].stat().st_size == 0:
                self._dashboard.clip_preview.set_saving()
                return
        except OSError:
            self._dashboard.clip_preview.set_clip(None)
            return

        self._dashboard.clip_preview.set_clip(clips[0])

    def _run_update_check(self) -> None:
        """Start a background thread to check for updates. Skips if one is already running."""
        if self._update_checker and self._update_checker.isRunning():
            return
        self._update_checker = _UpdateCheckerThread(self)
        self._update_checker.update_found.connect(self._on_update_found)
        self._update_checker.start()

    def _on_update_found(self, old_hash: str, new_hash: str, new_ver: str) -> None:
        """Called on main thread when checker finds a newer version."""
        if self._update_hashes == (old_hash, new_hash):
            return  # already notified
        self._update_hashes = (old_hash, new_hash)

        display_ver = f"v{new_ver}" if new_ver else new_hash

        from .. import notify as _notify
        _notify.notify(
            f"{CAT}  Mitten update available",
            f"v{self._current_ver} → {display_ver}  — click Update in the app to install",
            urgency="normal", icon="software-update-available", timeout_ms=6000,
        )

        self._dashboard.banner.show_update_available()

        self._ver_label.hide()
        self._ver_row_label.setText(
            f'<span style="color:{C.PINK}; font-size:9px;">v{self._current_ver}</span>'
            f'<span style="color:{_hex_rgba(C.SUBTEXT, 0.6)};"> → </span>'
            f'<span style="color:{C.GREEN}; font-size:9px; font-weight:700;">{display_ver}</span>'
        )
        self._ver_row_label.setTextFormat(Qt.TextFormat.RichText)
        self._ver_update_widget.show()

    def _do_update(self) -> None:
        """Spawn update terminal and quit the GUI."""
        if not self._update_hashes:
            return
        old_hash, new_hash = self._update_hashes
        from ..updater import spawn_update_terminal
        from PyQt6.QtWidgets import QApplication
        spawn_update_terminal(old_hash, new_hash)
        QApplication.instance().quit()

    def _toggle_recording(self) -> None:
        ok = toggle_daemon(self._daemon_pid())
        if not ok:
            self._dashboard.banner.set_state("error", "failed to start/stop daemon — check journal")
            QTimer.singleShot(4000, self._refresh)
        else:
            QTimer.singleShot(1500, self._refresh)

    def _toggle_pause(self) -> None:
        pid = self._daemon_pid()
        if pid is None:
            return
        toggle_pause(pid)
        QTimer.singleShot(800, self._refresh)

    def _on_dev_mode_toggled(self, enabled: bool) -> None:
        """Show or hide the Debug nav button when developer mode is toggled."""
        # Only show the debug button when we're in the main nav, not the settings sub-nav.
        # If we're in the settings sub-nav, the button stays hidden; it will be shown
        # (if enabled) when the user exits settings via _fade_sidebar(show_settings=False).
        if not self._settings_nav_active:
            self._nav_debug.setVisible(enabled)
        # If debug was the active page and we're hiding it, go back to dashboard
        if not enabled and self._pages.currentIndex() == 4:
            self._switch_main_page(0)

    def changeEvent(self, event) -> None:
        from PyQt6.QtCore import QEvent
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowActivate and self._gui_presence is not None:
            self._gui_presence_last_send = 0.0  # bypass rate limiter
            self._gui_presence_dirty = True
            self._flush_gui_presence()

    def closeEvent(self, event) -> None:
        # Cancel any pending stagger timers (Bug 2-1: QTimer on destroyed widgets)
        for t in self._stagger_timers:
            t.stop()
        self._stagger_timers.clear()

        # Stop orphaned update checker thread (Bug 3-3)
        if self._update_checker and self._update_checker.isRunning():
            self._update_checker.quit()
            self._update_checker.wait(2000)

        if self._gui_presence is not None:
            try:
                self._gui_presence.clear()
                self._gui_presence.stop()
            except Exception:
                pass

        self.hide()
        event.ignore()

