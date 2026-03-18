"""
Main MITTEN window — sidebar nav, dashboard, clips, settings.
Minimizes to tray on close.
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .resources import C, CAT, CAT_FONT, CATS, paw_icon
from ..daemon_utils import get_daemon_pid, toggle_daemon
from ..utils import format_duration, get_vram_usage


# ------------------------------------------------------------------ #
# Sidebar nav button
# ------------------------------------------------------------------ #

class _NavButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style(False)
        self.toggled.connect(self._update_style)

    def _update_style(self, checked: bool) -> None:
        if checked:
            self.setStyleSheet(
                f"QPushButton {{ background-color: rgba(196,167,231,0.10);"
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


# ------------------------------------------------------------------ #
# Status banner
# ------------------------------------------------------------------ #

class _StatusBanner(QFrame):
    # (ascii, label, detail, color)
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

        # Left text block — cat+state on row 1, detail on row 2
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
        self._detail_label.setStyleSheet(
            f"font-size: 11px; color: {C.SUBTEXT}; background: transparent; border: none;"
        )
        left.addWidget(self._detail_label)

        outer.addLayout(left, 1)

        self._btn_toggle = QPushButton("Start")
        self._btn_toggle.setFixedSize(84, 32)
        self._btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        outer.addWidget(self._btn_toggle)

        self.set_state("idle")

    def set_state(self, state: str, detail: str = "") -> None:
        ascii_art, text, default_detail, color = self._STATES.get(
            state, self._STATES["idle"]
        )

        self.setStyleSheet(
            f"QFrame {{ background-color: transparent;"
            f"border-radius: 10px; border: 1px solid rgba(58,54,80,0.4); }}"
        )

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

        self._detail_label.setText(detail or default_detail)
        self._detail_label.setStyleSheet(
            f"font-size: 11px; color: {C.SUBTEXT}; background: transparent; border: none;"
        )

        no_deps = state == "no_deps"
        self._btn_toggle.setEnabled(not no_deps)

        running = state in ("recording", "game", "saving")
        self._btn_toggle.setText("Stop" if running else "Start")
        if running:
            self._btn_toggle.setStyleSheet(
                f"QPushButton {{ background-color: rgba(243,139,168,0.85); color: {C.BG};"
                f"border: none; border-radius: 6px; font-weight: 700; font-size: 12px; }}"
                f"QPushButton:hover {{ background-color: {C.PINK}; }}"
            )
        else:
            self._btn_toggle.setStyleSheet(
                f"QPushButton {{ background-color: rgba(166,227,161,0.85); color: {C.BG};"
                f"border: none; border-radius: 6px; font-weight: 700; font-size: 12px; }}"
                f"QPushButton:hover {{ background-color: {C.GREEN}; }}"
            )


# ------------------------------------------------------------------ #
# Stat card
# ------------------------------------------------------------------ #

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(5)

        lbl = QLabel(label)
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
        border = "rgba(196,167,231,0.28)" if alpha > 0.43 else "rgba(58,54,80,0.28)"
        self.setStyleSheet(
            f"QFrame {{ background-color: rgba(37,35,54,{alpha:.2f});"
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


# ------------------------------------------------------------------ #
# Clip preview (auto-play muted loop)
# ------------------------------------------------------------------ #

class _ClipPreview(QFrame):
    """Auto-looping muted preview of the last clip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: #0d0b14; border-radius: 8px; }}"
        )
        self._clip_path: Path | None = None
        self._media_player = None

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

        # Info strip at bottom
        self._name_label = QLabel("no clips yet")
        self._name_label.setWordWrap(False)
        self._name_label.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 11px;"
            f"padding: 6px 12px;"
            f"background: rgba(13,11,20,0.7);"
            f"border-radius: 0 0 8px 8px;"
        )
        layout.addWidget(self._name_label)

    def set_clip(self, path: Path | None) -> None:
        if path == self._clip_path:
            return
        self._clip_path = path
        if path and path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            short = path.stem.replace("mitten_", "").replace("_", " ")
            self._name_label.setText(f"{short}  \u00b7  {size_mb:.1f} MB")
            if self._media_player:
                self._media_player.setSource(QUrl.fromLocalFile(str(path)))
                self._media_player.play()
        else:
            self._name_label.setText("no clips yet")
            if self._media_player:
                self._media_player.stop()

    def _on_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._media_player.setPosition(0)
            self._media_player.play()


# ------------------------------------------------------------------ #
# Section header (bold, accent bar)
# ------------------------------------------------------------------ #

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


# ------------------------------------------------------------------ #
# Pill tab bar
# ------------------------------------------------------------------ #

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
        # Select first by default
        first = self._group.button(0)
        if first:
            first.setChecked(True)
            self._style_btn(first, True)

    def _style_btn(self, btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                f"QPushButton {{ background: rgba(196,167,231,0.15); color: {C.LAVENDER};"
                f"border: 1px solid rgba(196,167,231,0.3); border-radius: 12px;"
                f"font-size: 11px; font-weight: 600; padding: 0 14px; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {C.SUBTEXT};"
                f"border: 1px solid rgba(58,54,80,0.3); border-radius: 12px;"
                f"font-size: 11px; font-weight: 600; padding: 0 14px; }}"
                f"QPushButton:hover {{ color: {C.TEXT}; border-color: rgba(58,54,80,0.6); }}"
            )

    def _on_click(self, idx: int) -> None:
        for bid in self._group.buttons():
            self._style_btn(bid, self._group.id(bid) == idx)
        self.tab_changed.emit(idx)


# ------------------------------------------------------------------ #
# Dashboard page
# ------------------------------------------------------------------ #

class _DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

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

        layout.addWidget(_section_header("PERFORMANCE"))

        # Pill tab bar
        self._pill_bar = _PillTabBar(["RAM", "GPU · CPU", "CLIPS"])
        layout.addWidget(self._pill_bar)

        # Stacked content pages
        self._stat_stack = QStackedWidget()

        # ── RAM page ──
        ram_page = QWidget()
        ram_layout = QHBoxLayout(ram_page)
        ram_layout.setContentsMargins(0, 4, 0, 0)
        ram_layout.setSpacing(10)
        self.card_ram = _StatCard("MITTEN RAM", C.LAVENDER)
        ram_layout.addWidget(self.card_ram)
        ram_layout.addStretch()
        self._stat_stack.addWidget(ram_page)  # index 0

        # ── GPU · CPU page ──
        gpu_page = QWidget()
        gpu_layout = QHBoxLayout(gpu_page)
        gpu_layout.setContentsMargins(0, 4, 0, 0)
        gpu_layout.setSpacing(10)
        self.card_vram = _StatCard("GPU VRAM", C.ORANGE)
        self.card_cpu  = _StatCard("CPU", C.BLUE)
        gpu_layout.addWidget(self.card_vram)
        gpu_layout.addWidget(self.card_cpu)
        gpu_layout.addStretch()
        self._stat_stack.addWidget(gpu_page)  # index 1

        # ── CLIPS page ──
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
        clips_layout.addStretch()
        self._stat_stack.addWidget(clips_page)  # index 2

        layout.addWidget(self._stat_stack)

        self._pill_bar.tab_changed.connect(self._on_tab_changed)

        layout.addWidget(_section_header("LAST CLIP"))

        self.clip_preview = _ClipPreview()
        self.clip_preview.setMinimumHeight(180)
        layout.addWidget(self.clip_preview, 1)

        outer.addStretch(1)
        outer.addWidget(self._content, 6)
        outer.addStretch(1)

    def _on_tab_changed(self, idx: int) -> None:
        from .anim import cross_fade
        old = self._stat_stack.currentWidget()
        self._stat_stack.setCurrentIndex(idx)
        new = self._stat_stack.currentWidget()
        if old and new and old is not new:
            cross_fade(old, new, duration_ms=180)


# ------------------------------------------------------------------ #
# About page
# ------------------------------------------------------------------ #

class _AboutPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        inner = QWidget()
        inner.setMaximumWidth(580)
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
            line.setStyleSheet(f"background-color: rgba(58,54,80,0.4);")
            return line

        # ── Header ──
        layout.addWidget(_h("~( ^.x.^)>  mitten", 26))
        layout.addWidget(_gap(6))
        layout.addWidget(_p(
            "a replay buffer for linux. press a button, save the last N seconds. that's it.",
            C.SUBTEXT, 13,
        ))

        layout.addWidget(_gap(28))
        layout.addWidget(_divider())
        layout.addWidget(_gap(24))

        # ── Why MITTEN exists ──
        layout.addWidget(_h("why i made this", 16, C.LAVENDER))
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "medal is windows only. not \"works better on windows\", "
            "straight up windows only. so that was already a no."
        ))
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "and even on windows it's genuinely bad. it tanks your performance, "
            "shoves ads in your face, paywalls stuff that used to be free, and the "
            "company behind it is just kind of slop. it feels like a startup that "
            "found out gamers save clips and decided to monetize that as hard as possible."
        ))
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "so i made mitten. it keeps a rolling buffer in ram, watermarks your clips, "
            "detects when you're in a game, and gets out of your way. "
            "no account, no cloud, no nonsense."
        ))

        layout.addWidget(_gap(28))
        layout.addWidget(_divider())
        layout.addWidget(_gap(24))

        # ── What it does ──
        layout.addWidget(_h("what it does", 16, C.GREEN))
        layout.addWidget(_gap(10))

        features = [
            ("replay buffer",     "last N seconds always rolling in ram. something happened? save it."),
            ("one button save",   "any mouse button you want. press it, clip saved."),
            ("game detection",    "sees when a game opens and switches capture automatically"),
            ("watermarking",      "burns your tag into every clip via ffmpeg"),
            ("basically no overhead", "gpu-screen-recorder uses your hardware encoder so cpu barely moves"),
            ("wayland",           "built for wayland, not an afterthought"),
        ]
        for title, desc in features:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            dot = QLabel("▸")
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color: {C.LAVENDER}; font-size: 13px; background: transparent;")
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            txt = _p(f"<b>{title}</b>  {desc}")
            row.addWidget(txt, 1)
            layout.addLayout(row)
            layout.addWidget(_gap(6))

        layout.addWidget(_gap(28))
        layout.addWidget(_divider())
        layout.addWidget(_gap(24))

        # ── Performance comparison ──
        layout.addWidget(_h("vs medal", 16, C.PINK))
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "from a real post on r/MedalTV (RTX 3090, i9-13700F, 32GB DDR5, "
            "720p 60fps replay buffer):"
        ))
        layout.addWidget(_gap(8))

        # Quote block
        quote = QFrame()
        quote.setStyleSheet(
            f"background-color: rgba(37,35,54,0.5);"
            f"border-left: 3px solid {C.PINK};"
            f"border-radius: 0 6px 6px 0;"
        )
        ql = QVBoxLayout(quote)
        ql.setContentsMargins(14, 10, 14, 10)
        ql.setSpacing(4)
        ql.addWidget(_p(
            "\"when idling at desktop, medal is using around 600 MB of memory and 0% gpu. "
            "when gaming, medal is using around 1000 MB of memory and around 25% of my gpu. "
            "i'm only running 720p at 60fps and not doing full session recording.\"",
            C.SUBTEXT, 12,
        ))
        ql.addWidget(_p("— r/MedalTV", C.GRAY, 10))
        layout.addWidget(quote)

        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "25% GPU on a 3090 for a 720p replay buffer is the dedicated CUDA shader cores "
            "doing software work. mitten uses gpu-screen-recorder which hits the NVENC "
            "hardware encoder block directly. that block sits idle during normal gaming "
            "and barely registers on any GPU meter when recording."
        ))
        layout.addWidget(_gap(8))
        layout.addWidget(_p(
            "RAM wise, mitten's buffer scales with your buffer length and resolution. "
            "a 30 second 1080p60 buffer in HEVC typically sits under 300MB. "
            "the daemon itself is a few MB of Python.",
            C.SUBTEXT, 12,
        ))
        layout.addWidget(_gap(6))
        layout.addWidget(_p(
            "note: these are user-reported numbers, not a controlled benchmark. "
            "your mileage will vary.",
            C.GRAY, 11,
        ))

        layout.addWidget(_gap(28))
        layout.addWidget(_divider())
        layout.addWidget(_gap(24))

        # ── Stack ──
        layout.addWidget(_h("built with", 16, C.ORANGE))
        layout.addWidget(_gap(10))
        layout.addWidget(_p(
            "gpu-screen-recorder  ·  ffmpeg  ·  python  ·  pyqt6  ·  evdev  ·  pipewire"
        ))

        layout.addWidget(_gap(28))
        layout.addWidget(_divider())
        layout.addWidget(_gap(20))

        layout.addWidget(_p("made with ♥ by mit", C.SUBTEXT, 11))
        layout.addStretch()

        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch()
        wl.addWidget(inner)
        wl.addStretch()
        scroll.setWidget(wrapper)

        outer.addWidget(scroll)


# ------------------------------------------------------------------ #
# Main window
# ------------------------------------------------------------------ #

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

        # Cache deps check — gpu-screen-recorder presence, checked once on init
        try:
            from .system_setup import check_dependencies
            self._has_gsr = check_dependencies().get("gpu-screen-recorder", False)
        except Exception:
            self._has_gsr = True  # assume present if check fails

        # Whether settings sub-nav is showing in the sidebar
        self._settings_nav_active = False

        self._build_ui()
        self._connect_signals()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)

        self._save_flash = QTimer(self)
        self._save_flash.setSingleShot(True)
        self._save_flash.timeout.connect(self._refresh)

        self._refresh()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──
        sidebar = QWidget()
        sidebar.setFixedWidth(140)
        sidebar.setStyleSheet(
            f"background-color: rgba(22,20,34,0.97);"
            f"border-right: 1px solid rgba(58,54,80,0.35);"
        )

        self._sidebar_layout = QVBoxLayout(sidebar)
        self._sidebar_layout.setContentsMargins(0, 14, 0, 14)
        self._sidebar_layout.setSpacing(0)

        # Logo
        logo = QLabel(f"  {CAT}")
        logo.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 20px; font-weight: 700;"
            f"padding: 6px 0 1px 10px; {CAT_FONT}"
        )
        self._sidebar_layout.addWidget(logo)

        title = QLabel("  MITTEN")
        title.setStyleSheet(
            f"color: rgba(232,224,240,0.7); font-size: 12px; font-weight: 700;"
            f"letter-spacing: 4px; padding: 0 0 20px 10px;"
        )
        self._sidebar_layout.addWidget(title)

        # Main nav buttons
        self._nav_dashboard = _NavButton("Dashboard")
        self._nav_clips     = _NavButton("Clips")
        self._nav_settings  = _NavButton("Settings")
        self._nav_about     = _NavButton("About")
        self._nav_dashboard.setChecked(True)
        self._main_nav_buttons = [
            self._nav_dashboard, self._nav_clips, self._nav_settings, self._nav_about,
        ]
        for btn in self._main_nav_buttons:
            self._sidebar_layout.addWidget(btn)

        # Settings sub-nav (hidden by default)
        self._nav_back = _NavButton("\u2190  Back")
        self._nav_back.setVisible(False)

        self._settings_nav_buttons: list[_NavButton] = []
        for name in ["General", "Recording", "Compression", "Watermark", "Games"]:
            btn = _NavButton(name)
            btn.setVisible(False)
            self._settings_nav_buttons.append(btn)
            self._sidebar_layout.addWidget(btn)

        self._sidebar_layout.addWidget(self._nav_back)

        self._sidebar_layout.addStretch()

        try:
            from .. import __version__
            ver = __version__
        except Exception:
            ver = "?"
        ver_label = QLabel(f"  mitten  v{ver}")
        ver_label.setStyleSheet(
            f"color: rgba(152,144,168,0.45); font-size: 10px; font-weight: 600;"
            f"letter-spacing: 0.5px; padding-left: 10px; padding-bottom: 2px;"
        )
        made_label = QLabel("  made with ♥ by mit")
        made_label.setStyleSheet(
            f"color: rgba(152,144,168,0.28); font-size: 9px; padding-left: 10px; padding-bottom: 4px;"
        )
        self._sidebar_layout.addWidget(ver_label)
        self._sidebar_layout.addWidget(made_label)

        root.addWidget(sidebar)

        # ── Pages ──
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

        root.addWidget(self._pages, 1)

    # ------------------------------------------------------------------ #
    # Signal wiring
    # ------------------------------------------------------------------ #

    def _connect_signals(self) -> None:
        self._nav_dashboard.clicked.connect(lambda: self._switch_main_page(0))
        self._nav_clips.clicked.connect(lambda: self._switch_main_page(1))
        self._nav_settings.clicked.connect(self._enter_settings)
        self._nav_about.clicked.connect(lambda: self._switch_main_page(3))
        self._nav_back.clicked.connect(self._exit_settings)

        for i, btn in enumerate(self._settings_nav_buttons):
            btn.clicked.connect(lambda _, idx=i: self._switch_settings_section(idx))

        btn = self._dashboard.banner._btn_toggle
        btn.clicked.connect(self._toggle_recording)
        btn.pressed.connect(lambda: self._btn_press_dip(btn))

    def _btn_press_dip(self, btn: QPushButton) -> None:
        """Brief opacity dip on button press."""
        from .anim import fade_out, fade_in
        fade_out(btn, duration_ms=50, on_done=lambda: fade_in(btn, duration_ms=100))

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def _switch_main_page(self, index: int) -> None:
        if self._pages.currentIndex() == index:
            return
        self._fade_to(index)
        for i, btn in enumerate(self._main_nav_buttons):
            btn.setChecked(i == index)

    def _enter_settings(self) -> None:
        """Fade sidebar from main nav to settings sub-nav."""
        self._settings_nav_active = True
        self._fade_sidebar(show_settings=True)
        self._fade_to(2)
        # Select General by default
        self._switch_settings_section(0)

    def _exit_settings(self) -> None:
        """Return from settings sub-nav to main nav."""
        self._settings_nav_active = False
        self._fade_sidebar(show_settings=False)
        self._fade_to(0)
        for btn in self._main_nav_buttons:
            btn.setChecked(False)
        self._nav_dashboard.setChecked(True)

    def _fade_sidebar(self, show_settings: bool) -> None:
        from .anim import staggered_fade
        if show_settings:
            for btn in self._main_nav_buttons:
                btn.setVisible(False)
            self._nav_back.setVisible(True)
            for btn in self._settings_nav_buttons:
                btn.setVisible(True)
            staggered_fade(
                [self._nav_back] + self._settings_nav_buttons,
                duration_ms=100, stagger_ms=20, fade_in_=True,
            )
        else:
            self._nav_back.setVisible(False)
            for btn in self._settings_nav_buttons:
                btn.setVisible(False)
            for btn in self._main_nav_buttons:
                btn.setVisible(True)
            staggered_fade(
                self._main_nav_buttons,
                duration_ms=100, stagger_ms=20, fade_in_=True,
            )

    def _switch_settings_section(self, idx: int) -> None:
        self._settings_page.switch_section(idx)
        for i, btn in enumerate(self._settings_nav_buttons):
            btn.setChecked(i == idx)

    def _fade_to(self, index: int) -> None:
        if self._pages.currentIndex() == index:
            return
        prev_index = self._pages.currentIndex()
        direction = "left" if index > prev_index else "right"
        self._pages.setCurrentIndex(index)
        new_w = self._pages.currentWidget()
        from .anim import slide_fade_in
        self._current_anim = slide_fade_in(new_w, direction=direction, distance=16, duration_ms=180)

    # ------------------------------------------------------------------ #
    # State & refresh
    # ------------------------------------------------------------------ #

    def _set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        self._dashboard.banner.set_state(state, detail)
        self.setWindowIcon(paw_icon(state))

    def _daemon_pid(self) -> int | None:
        return get_daemon_pid()

    def _refresh(self) -> None:
        if not self._has_gsr:
            self._set_state("no_deps")
            self._dashboard.card_ram.set_value("\u2014")
            self._refresh_vram()
            self._refresh_cpu()
            self._refresh_clip_metrics()
            self._refresh_clip_preview()
            return

        pid = self._daemon_pid()
        if pid is None:
            self._set_state("idle")
            self._dashboard.card_ram.set_value("\u2014")
        else:
            uptime_str = self._get_uptime_str(pid)
            detail = f"mitten is watching\u2026"
            if uptime_str:
                detail += f" \u00b7 up {uptime_str}"
            self._set_state("recording", detail)
            self._refresh_memory(pid)

        self._refresh_vram()
        self._refresh_cpu()
        self._refresh_clip_metrics()
        self._refresh_clip_preview()

    def _refresh_memory(self, pid: int) -> None:
        total_mb = 0.0
        try:
            import psutil
            proc = psutil.Process(pid)
            total_mb = proc.memory_info().rss / (1024 * 1024)
            for child in proc.children(recursive=True):
                try:
                    total_mb += child.memory_info().rss / (1024 * 1024)
                except Exception:
                    pass
        except Exception:
            pass
        self._dashboard.card_ram.set_value(
            f"{total_mb:.0f} MB" if total_mb else "\u2014"
        )

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

    def _refresh_cpu(self) -> None:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            self._dashboard.card_cpu.set_value(f"{cpu:.0f}%")
        except Exception:
            self._dashboard.card_cpu.set_value("\u2014")

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

        clips = sorted(save_dir.glob("mitten_*.mp4"), reverse=True)
        if clips:
            self._dashboard.clip_preview.set_clip(clips[0])
        else:
            self._dashboard.clip_preview.set_clip(None)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _toggle_recording(self) -> None:
        toggle_daemon(self._daemon_pid())
        QTimer.singleShot(1500, self._refresh)

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()

