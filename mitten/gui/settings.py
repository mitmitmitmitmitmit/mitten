"""
Settings — no internal sidebar (nav lives in MittenMainWindow).
Pages: General (+ Trigger + Notifications), Recording, Compression, Watermark, Games.
Public API: switch_section(idx: int)
"""
from __future__ import annotations

import random
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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

from .resources import C, _accent_hover, _hex_rgba


def _sep(text: str) -> QLabel:
    _suffixes = [" · smh", " · yikes", " · tragic", " · oof", " · really"]
    try:
        from .themes import LIGHT_MODE_ACTIVE as _LMA
        if _LMA and random.random() < 0.25:
            text = text + random.choice(_suffixes)
    except Exception:
        pass
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {C.SUBTEXT}; font-size: 10px; font-weight: 600;"
        f"letter-spacing: 1.5px; padding-top: 10px;"
        f"border-top: 1px solid {_hex_rgba(C.BORDER, 0.4)};"
        f"margin-top: 4px;"
    )
    return lbl


_CQ_LABELS = [
    (16, 19, "insane quality", "file size absolutely DEMOLISHED"),
    (20, 22, "super high quality", "your disk is crying rn"),
    (23, 25, "high quality", "pretty chunky but worth it"),
    (26, 28, "damn good", "solid balance, nice"),
    (29, 31, "getting there", "compression starting to show"),
    (32, 34, "rough", "artifacts on anything fast"),
    (35, 37, "kinda bad", "your gpu is embarrassed"),
    (38, 40, "literally 2014 samsung", "wtf are u doing"),
]


def _cq_label(cq: int) -> str:
    for lo, hi, quality, size in _CQ_LABELS:
        if lo <= cq <= hi:
            return f"{quality}  ·  {size}"
    return ""


class _CQSlider(QSlider):
    """QSlider subclass that draws graph-style dots along the groove."""

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        lo, hi = self.minimum(), self.maximum()
        steps = hi - lo
        w, h = self.width(), self.height()
        # Approximate groove rect (Qt reserves ~8px handle half-width on each side)
        pad = 8
        groove_w = w - pad * 2
        groove_y = h // 2

        accent = QColor(C.LAVENDER)

        for i in range(steps + 1):
            cq = lo + i
            t = i / steps  # 0.0 (left, best quality) → 1.0 (right, worst)
            x = pad + int(t * groove_w)

            # Dot size: large on left (high quality zone), shrinks toward right
            size = max(2, int(6 - t * 4))
            # Opacity: bright on left, fades right
            alpha = int(220 - t * 160)
            color = QColor(accent)
            color.setAlpha(alpha)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(x - size // 2, groove_y - size // 2, size, size)

        painter.end()


class _SpecsAdvisor(QWidget):
    """Collapsible 'your specs' panel: detected hardware → recommended settings."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(0)

        self._toggle = QPushButton("\u25b8  your specs")
        self._toggle.setCheckable(True)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.SUBTEXT};"
            f"border: none; text-align: left; font-size: 11px; font-weight: 600;"
            f"letter-spacing: 0.5px; padding: 4px 0; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; }}"
            f"QPushButton:checked {{ color: {C.LAVENDER}; }}"
        )
        self._toggle.toggled.connect(self._on_toggle)
        layout.addWidget(self._toggle)

        self._body = QWidget()
        self._body.setStyleSheet(
            f"background: {_hex_rgba(C.SURFACE, 0.35)}; border-radius: 6px;"
        )
        self._bl = QVBoxLayout(self._body)
        self._bl.setContentsMargins(14, 10, 14, 12)
        self._bl.setSpacing(5)
        self._populated = False
        self._body.hide()
        layout.addWidget(self._body)

    def _on_toggle(self, checked: bool) -> None:
        _open_labels  = ["▾  your specs (embarrassing)", "▾  your specs (yikes)", "▾  your specs (oof)"]
        _close_labels = ["▸  your specs (and your crimes)", "▸  your specs (hide this)", "▸  your specs (don't)"]
        try:
            from .themes import LIGHT_MODE_ACTIVE as _LMA
            if _LMA:
                _pool = _open_labels if checked else _close_labels
                self._toggle.setText(random.choice(_pool))
            else:
                self._toggle.setText(("\u25be" if checked else "\u25b8") + "  your specs")
        except Exception:
            self._toggle.setText(("\u25be" if checked else "\u25b8") + "  your specs")
        if checked and not self._populated:
            self._populate()
            self._populated = True
        self._body.setVisible(checked)

    def _populate(self) -> None:
        _spec_insult_notes = [
            "still in light mode smh",
            "nice specs, shame about your theme",
            "powerful hardware, terrible taste",
            "plenty of headroom for better decisions",
            "wasted on a light mode enjoyer",
        ]
        _light_active = False
        try:
            from .themes import LIGHT_MODE_ACTIVE as _LMA
            _light_active = bool(_LMA)
        except Exception:
            pass

        rows = list(self._detect())
        _insult_idx = (
            random.randrange(len(rows))
            if _light_active and rows and random.random() < 0.40
            else -1
        )

        for _row_i, (label, value, note, is_good) in enumerate(rows):
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)

            lbl = QLabel(label)
            lbl.setFixedWidth(52)
            lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px;")
            rl.addWidget(lbl)

            val = QLabel(value)
            val.setStyleSheet(
                f"color: {C.GREEN if is_good else C.ORANGE};"
                f"font-size: 11px; font-weight: 600;"
            )
            rl.addWidget(val)

            _display_note = note
            if _light_active and _row_i == _insult_idx:
                _display_note = random.choice(_spec_insult_notes)
            if _display_note:
                note_lbl = QLabel(_display_note)
                note_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
                rl.addWidget(note_lbl, 1)

            rl.addStretch()
            self._bl.addWidget(row_w)

        if _light_active and random.random() < 0.60:
            try:
                from .themes import get_abuse
                _abuse_txt = get_abuse(include_name=False)
            except Exception:
                _abuse_txt = "consider: dark mode"
            _abuse_lbl = QLabel(_abuse_txt)
            _abuse_lbl.setWordWrap(True)
            _abuse_lbl.setStyleSheet(
                f"color: {C.SUBTEXT}; font-size: 10px; font-style: italic; padding-top: 4px;"
            )
            self._bl.addWidget(_abuse_lbl)

    @staticmethod
    def _detect_gpu() -> tuple[str, int]:
        """Returns (gpu_name, vram_mb). Cross-vendor: NVIDIA, AMD, Intel. Windows stub ready."""
        import subprocess as _sp
        import sys

        try:
            r = _sp.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split(",")
                name = parts[0].strip().replace("NVIDIA GeForce ", "")
                return name, int(parts[1].strip())
        except Exception:
            pass

        if sys.platform == "linux":
            try:
                for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
                    vram_path = card / "device" / "mem_info_vram_total"
                    if not vram_path.exists():
                        continue
                    vram_mb = int(vram_path.read_text().strip()) // (1024 * 1024)
                    if vram_mb < 512:
                        continue  # skip integrated / invalid
                    vendor_f = card / "device" / "vendor"
                    device_f = card / "device" / "device"
                    if vendor_f.exists() and device_f.exists():
                        vid = vendor_f.read_text().strip()[2:]
                        did = device_f.read_text().strip()[2:]
                        try:
                            lsp = _sp.run(
                                ["lspci", "-d", f"{vid}:{did}", "-mm"],
                                capture_output=True, text=True, timeout=5,
                            )
                            if lsp.returncode == 0 and lsp.stdout.strip():
                                parts = lsp.stdout.strip().split('"')
                                name = parts[5].strip() if len(parts) >= 6 else "AMD GPU"
                            else:
                                name = "AMD GPU"
                        except Exception:
                            name = "AMD GPU"
                    else:
                        name = "AMD GPU"
                    return name, vram_mb
            except Exception:
                pass

        if sys.platform == "linux":
            try:
                r = _sp.run(["lspci"], capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if any(x in line for x in ("VGA", "3D", "Display")):
                        return line.split(":", 2)[-1].strip()[:40], 0
            except Exception:
                pass

        return "", 0

    @staticmethod
    def _quality_rec(name: str, vram_mb: int) -> tuple[str, bool]:
        n = name.lower()
        if any(x in n for x in ("rtx 20", "rtx 30", "rtx 40", "rtx 50")):
            return "very_high", True
        if any(x in n for x in ("gtx 16",)):
            return "high", True
        if "gtx" in n or ("nvidia" in n and "rtx" not in n):
            return "medium", True
        if any(x in n for x in ("rx 6", "rx 7", "rx 8", "radeon rx 6", "radeon rx 7")):
            return "very_high", True
        if any(x in n for x in ("rx 5", "radeon rx 5")):
            return "high", True
        if "amd" in n or "radeon" in n:
            return "medium", True
        if "arc" in n:
            return ("high", True) if any(x in n for x in ("a770", "a750")) else ("medium", True)
        if vram_mb >= 8000:
            return "very_high", True
        if vram_mb >= 6000:
            return "high", True
        if vram_mb >= 4000:
            return "medium", True
        return "high", True

    @classmethod
    def _detect(cls) -> list[tuple[str, str, str, bool]]:
        import os
        import sys
        rows: list[tuple[str, str, str, bool]] = []

        gpu_name, vram_mb = cls._detect_gpu()

        if gpu_name:
            quality, q_good = cls._quality_rec(gpu_name, vram_mb)
            rows.append(("gpu", gpu_name[:32], f"quality: {quality}", q_good))

        if vram_mb > 0:
            if vram_mb >= 8000:
                buf_note = "buffer: up to 120s"
            elif vram_mb >= 6000:
                buf_note = "buffer: up to 90s"
            elif vram_mb >= 4000:
                buf_note = "buffer: up to 60s"
            else:
                buf_note = "buffer: keep under 30s"
            rows.append(("vram", f"{vram_mb / 1024:.0f}GB", buf_note, vram_mb >= 4000))

        try:
            if sys.platform == "linux":
                for line in Path("/proc/cpuinfo").read_text().splitlines():
                    if "model name" in line:
                        cpu = (line.split(":", 1)[1].strip()
                               .replace("Intel(R) Core(TM) ", "")
                               .replace("Intel(R) ", "")
                               .replace("AMD ", "")
                               .replace(" CPU @ ", " @ "))
                        rows.append(("cpu", cpu[:32], "framerate: 60fps ok", True))
                        break
            else:
                import platform
                cpu = platform.processor()[:32]
                if cpu:
                    rows.append(("cpu", cpu, "framerate: 60fps ok", True))
        except Exception:
            pass

        try:
            if sys.platform == "linux":
                for line in Path("/proc/meminfo").read_text().splitlines():
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) // (1024 * 1024)
                        good = ram_gb >= 8
                        note = ("plenty of headroom" if ram_gb >= 16
                                else "ok for most buffers" if ram_gb >= 8
                                else "keep buffer short")
                        rows.append(("ram", f"{ram_gb}GB", note, good))
                        break
            else:
                try:
                    import psutil
                    ram_gb = psutil.virtual_memory().total // (1024 ** 3)
                    good = ram_gb >= 8
                    rows.append(("ram", f"{ram_gb}GB",
                                 "plenty of headroom" if ram_gb >= 16 else "ok", good))
                except ImportError:
                    pass
        except Exception:
            pass

        if os.environ.get("WAYLAND_DISPLAY"):
            rows.append(("display", "Wayland", "desktop or game mode", True))
        elif os.environ.get("DISPLAY"):
            rows.append(("display", "X11", "all modes supported", True))
        elif sys.platform == "win32":
            rows.append(("display", "Windows", "all modes supported", True))

        return rows


# ------------------------------------------------------------------ #
# Anti-disable gauntlet — 5-stage abuse toggle dialogs
# ------------------------------------------------------------------ #

_gauntlet_stage: int = 0


def _shake_dialog(dlg: QDialog) -> None:
    """Translate-animate the dialog left-right to simulate a "wrong answer" shake."""
    anim = QPropertyAnimation(dlg, b"pos", dlg)
    anim.setDuration(300)
    anim.setEasingCurve(QEasingCurve.Type.OutElastic)
    p = dlg.pos()
    anim.setKeyValueAt(0.0, p)
    anim.setKeyValueAt(0.15, QPoint(p.x() - 14, p.y()))
    anim.setKeyValueAt(0.35, QPoint(p.x() + 14, p.y()))
    anim.setKeyValueAt(0.55, QPoint(p.x() - 10, p.y()))
    anim.setKeyValueAt(0.75, QPoint(p.x() + 10, p.y()))
    anim.setKeyValueAt(0.90, QPoint(p.x() - 4, p.y()))
    anim.setKeyValueAt(1.0, p)
    anim.start()
    dlg._shake_anim = anim  # type: ignore[attr-defined]


def _gauntlet_abandoned(parent: QWidget) -> None:
    """Called when the user closes any gauntlet dialog early."""
    try:
        from ..notify import notify
        notify(
            "really?",
            "you couldn't even finish the quiz. maybe switch to dark mode, FREAK.",
            urgency="normal",
        )
    except Exception:
        pass


def _run_gauntlet(parent: QWidget) -> None:
    """Entry point — run the multi-stage abuse disable gauntlet.
    Returns without doing anything if user abandons at any stage."""
    global _gauntlet_stage
    _gauntlet_stage = 0
    _stage1_riddle(parent)


def _styled_dialog(parent: QWidget, title: str) -> QDialog:
    """Create a styled QDialog that matches the app palette."""
    from .resources import C, _hex_rgba
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(420)
    dlg.setStyleSheet(
        f"QDialog {{ background-color: {C.BG}; color: {C.TEXT}; }}"
        f"QLabel {{ color: {C.TEXT}; background: transparent; }}"
        f"QLineEdit {{ background: {C.SURFACE}; color: {C.TEXT};"
        f"border: 1px solid {C.BORDER}; border-radius: 4px; padding: 6px; }}"
        f"QPushButton {{ background-color: {C.SURFACE}; color: {C.TEXT};"
        f"border: 1px solid {C.BORDER}; border-radius: 4px;"
        f"padding: 6px 16px; font-size: 12px; }}"
        f"QPushButton:hover {{ background-color: {_hex_rgba(C.LAVENDER, 0.15)};"
        f"border-color: {C.LAVENDER}; }}"
    )
    return dlg


def _stage1_riddle(parent: QWidget) -> None:
    """Stage 1 — Riddle gate. Accepts only 'idiot' (case-insensitive)."""
    global _gauntlet_stage
    from .resources import C, _hex_rgba

    dlg = _styled_dialog(parent, "prove you're worthy")
    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    layout.setContentsMargins(20, 20, 20, 20)

    body = QLabel(
        "answer this riddle to unlock the setting:\n\n"
        "I am something you clearly are.\n"
        "I make poor decisions.\n"
        "I chose light mode.\n"
        "What am I?"
    )
    body.setWordWrap(True)
    body.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
    layout.addWidget(body)

    hint_lbl = QLabel("(answer below)")
    hint_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px; font-style: italic;")
    layout.addWidget(hint_lbl)

    answer_edit = QLineEdit()
    answer_edit.setPlaceholderText("your answer…")
    layout.addWidget(answer_edit)

    btn_row = QHBoxLayout()
    btn_submit = QPushButton("Submit")
    btn_submit.setStyleSheet(
        f"QPushButton {{ background-color: {_hex_rgba(C.LAVENDER, 0.25)};"
        f"color: {C.LAVENDER}; border: 1px solid {C.LAVENDER}; border-radius: 4px;"
        f"padding: 6px 16px; font-size: 12px; font-weight: 600; }}"
        f"QPushButton:hover {{ background-color: {_hex_rgba(C.LAVENDER, 0.4)}; }}"
    )
    btn_row.addStretch()
    btn_row.addWidget(btn_submit)
    layout.addLayout(btn_row)

    _wrong_insults = [
        "that's not even close. try again.",
        "no. just… no.",
        "incorrect. think harder. (or don't — you chose light mode.)",
        "wrong. embarrassingly wrong.",
        "that answer is as bad as your theme choice.",
    ]
    _wrong_idx = [0]

    def _on_submit() -> None:
        ans = answer_edit.text().strip().lower()
        if "idiot" in ans:
            hint_lbl.setText("fine. you may proceed.")
            hint_lbl.setStyleSheet(f"color: {C.GREEN}; font-size: 11px;")
            QTimer.singleShot(800, lambda: (dlg.accept(), _stage2_algebra(parent)))
        else:
            _shake_dialog(dlg)
            hint_lbl.setText(_wrong_insults[_wrong_idx[0] % len(_wrong_insults)])
            hint_lbl.setStyleSheet(f"color: {C.PINK}; font-size: 11px; font-style: italic;")
            _wrong_idx[0] += 1
            answer_edit.clear()

    btn_submit.clicked.connect(_on_submit)
    answer_edit.returnPressed.connect(_on_submit)
    dlg.rejected.connect(lambda: _gauntlet_abandoned(parent))

    dlg.exec()


def _stage2_algebra(parent: QWidget) -> None:
    """Stage 2 — Algebra gate. Always fails for 3 attempts, then advances."""
    global _gauntlet_stage
    from .resources import C, _hex_rgba

    _problems = [
        "solve for x: 2x + 5 = 17",
        "if 3x - 4 = 11, what is x?",
        "solve for x: 4(x - 2) = 20",
        "what is x if 5x + 3 = 28?",
    ]
    _harder = [
        "wrong. it's literally 2x + 5 = 17. x = 6. try again.",
        "still wrong. subtract from both sides. you learned this.",
        "incorrect. this is the easiest math we could think of and you failed it.",
    ]

    _attempt = [0]
    _cur_problem = [random.choice(_problems)]

    dlg = _styled_dialog(parent, "now for some math")
    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    layout.setContentsMargins(20, 20, 20, 20)

    problem_lbl = QLabel(_cur_problem[0])
    problem_lbl.setWordWrap(True)
    problem_lbl.setStyleSheet(f"color: {C.TEXT}; font-size: 13px; font-weight: 500;")
    layout.addWidget(problem_lbl)

    hint_lbl = QLabel("show your work below")
    hint_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px; font-style: italic;")
    layout.addWidget(hint_lbl)

    answer_edit = QLineEdit()
    answer_edit.setPlaceholderText("your answer…")
    layout.addWidget(answer_edit)

    btn_row = QHBoxLayout()
    btn_submit = QPushButton("Submit")
    btn_submit.setStyleSheet(
        f"QPushButton {{ background-color: {_hex_rgba(C.LAVENDER, 0.25)};"
        f"color: {C.LAVENDER}; border: 1px solid {C.LAVENDER}; border-radius: 4px;"
        f"padding: 6px 16px; font-size: 12px; font-weight: 600; }}"
        f"QPushButton:hover {{ background-color: {_hex_rgba(C.LAVENDER, 0.4)}; }}"
    )
    btn_row.addStretch()
    btn_row.addWidget(btn_submit)
    layout.addLayout(btn_row)

    def _on_submit() -> None:
        _attempt[0] += 1
        if _attempt[0] >= 3:
            hint_lbl.setText("close enough, you tried")
            hint_lbl.setStyleSheet(f"color: {C.GREEN}; font-size: 11px;")
            QTimer.singleShot(800, lambda: (dlg.accept(), _stage3_click(parent)))
        else:
            _shake_dialog(dlg)
            new_problem = _harder[min(_attempt[0] - 1, len(_harder) - 1)]
            _cur_problem[0] = new_problem
            problem_lbl.setText(new_problem)
            remaining = 3 - _attempt[0]
            hint_lbl.setText(f"attempt {_attempt[0]}/3 failed. {remaining} left.")
            hint_lbl.setStyleSheet(f"color: {C.PINK}; font-size: 11px; font-style: italic;")
            answer_edit.clear()

    btn_submit.clicked.connect(_on_submit)
    answer_edit.returnPressed.connect(_on_submit)
    dlg.rejected.connect(lambda: _gauntlet_abandoned(parent))

    dlg.exec()


def _stage3_click(parent: QWidget) -> None:
    """Stage 3 — Click counter. 50 clicks required, no close button."""
    global _gauntlet_stage
    from .resources import C, _hex_rgba

    dlg = _styled_dialog(parent, "one more thing")
    dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(16)
    layout.setContentsMargins(24, 24, 24, 24)

    body = QLabel(
        "you're going to have to click this button 50 times.\n"
        "i'm keeping track."
    )
    body.setWordWrap(True)
    body.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
    layout.addWidget(body)

    _remaining = [50]

    btn_click = QPushButton(f"click me ({_remaining[0]} remaining)")
    btn_click.setMinimumHeight(40)
    btn_click.setStyleSheet(
        f"QPushButton {{ background-color: {_hex_rgba(C.LAVENDER, 0.25)};"
        f"color: {C.LAVENDER}; border: 1px solid {C.LAVENDER}; border-radius: 6px;"
        f"padding: 8px 20px; font-size: 13px; font-weight: 600; }}"
        f"QPushButton:hover {{ background-color: {_hex_rgba(C.LAVENDER, 0.4)}; }}"
    )
    layout.addWidget(btn_click)

    def _on_click() -> None:
        _remaining[0] -= 1
        n = _remaining[0]
        btn_click.setText(f"click me ({n} remaining)")
        if n == 25:
            dlg.setWindowTitle("you're really doing this huh")
        elif n == 10:
            dlg.setWindowTitle("wow. commitment to a mistake.")
        elif n == 1:
            dlg.setWindowTitle("last one. sure about this?")
        elif n <= 0:
            QTimer.singleShot(0, lambda: (dlg.accept(), _stage4_confirm(parent)))

    btn_click.clicked.connect(_on_click)
    dlg.rejected.connect(lambda: _gauntlet_abandoned(parent))
    dlg.exec()


def _stage4_confirm(parent: QWidget) -> None:
    """Stage 4 — Final confirmation before the reveal."""
    from .resources import C, _hex_rgba

    dlg = _styled_dialog(parent, "are you sure")
    layout = QVBoxLayout(dlg)
    layout.setSpacing(16)
    layout.setContentsMargins(24, 24, 24, 24)

    body = QLabel(
        "are you absolutely certain?\n"
        "this will disable verbal abuse.\n"
        "think carefully."
    )
    body.setWordWrap(True)
    body.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
    layout.addWidget(body)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_yes = QPushButton("yes, disable it")
    btn_yes.setStyleSheet(
        f"QPushButton {{ background-color: {_hex_rgba(C.GREEN, 0.25)};"
        f"color: {C.GREEN}; border: 1px solid {C.GREEN}; border-radius: 4px;"
        f"padding: 6px 16px; font-size: 12px; font-weight: 600; }}"
        f"QPushButton:hover {{ background-color: {_hex_rgba(C.GREEN, 0.4)}; }}"
    )
    btn_no = QPushButton("actually nevermind")
    btn_no.setStyleSheet(
        f"QPushButton {{ background-color: {_hex_rgba(C.SUBTEXT, 0.15)};"
        f"color: {C.SUBTEXT}; border: 1px solid {_hex_rgba(C.BORDER, 0.5)};"
        f"border-radius: 4px; padding: 6px 16px; font-size: 12px; }}"
        f"QPushButton:hover {{ background-color: {_hex_rgba(C.SUBTEXT, 0.25)}; }}"
    )
    btn_row.addStretch()
    btn_row.addWidget(btn_no)
    btn_row.addWidget(btn_yes)
    layout.addLayout(btn_row)

    def _on_yes() -> None:
        QTimer.singleShot(0, lambda: (dlg.accept(), _stage5_reveal(parent)))

    btn_yes.clicked.connect(_on_yes)
    btn_no.clicked.connect(dlg.reject)
    dlg.rejected.connect(lambda: _gauntlet_abandoned(parent))
    dlg.exec()


def _stage5_reveal(parent: QWidget) -> None:
    """Stage 5 — The reveal. Switches to default dark theme instead of disabling abuse."""
    import subprocess as _sp
    import shutil
    import dataclasses

    try:
        from ..config import load_config
        from .config_io import save_config
        cfg = load_config()
        new_general = dataclasses.replace(cfg.general, theme="Default")
        new_cfg = dataclasses.replace(cfg, general=new_general)
        save_config(new_cfg)
    except Exception:
        pass

    try:
        from .themes import apply_theme
        from .resources import make_stylesheet
        from PyQt6.QtWidgets import QApplication
        apply_theme("Default")
        app = QApplication.instance()
        if app:
            app.setStyleSheet(make_stylesheet())
    except Exception:
        pass

    try:
        from ..config import GUI_SOCKET
        GUI_SOCKET.unlink(missing_ok=True)
    except Exception:
        pass

    mitten_bin = shutil.which("mitten")
    if mitten_bin:
        _sp.Popen(
            [mitten_bin, "--_abuse-reveal"],
            start_new_session=True,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
        )

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        QTimer.singleShot(200, app.quit)


class SettingsDialog(QWidget):
    """MITTEN settings — headless QStackedWidget, nav controlled by main window."""

    developer_mode_toggled = pyqtSignal(bool)   # emitted when developer mode checkbox changes
    section_changed        = pyqtSignal(str)    # "left" or "right" when nav section switches
    discord_preview        = pyqtSignal(object) # emitted live when discord settings change (DiscordConfig)
    settings_saved         = pyqtSignal()       # emitted after a successful save

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C.BG};")
        self._build_ui()
        self._load_config()
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE:
                import random as _r
                _abuse_widgets = [
                    self._buffer_spin,
                    self._quality_combo,
                    self._wm_text,
                ]
                for _w in _r.sample(_abuse_widgets, k=_r.randint(2, 3)):
                    _w.setToolTip(get_abuse())
        except Exception:
            pass

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
        self._pages.addWidget(self._make_discord_tab())       # 5
        root.addWidget(self._pages, 1)

        save_bar = QWidget()
        save_bar.setStyleSheet(
            f"background-color: {_hex_rgba(C.BG, 0.9)};"
            f"border-top: 1px solid {_hex_rgba(C.BORDER, 0.3)};"
        )
        save_bar.setFixedHeight(52)
        sb_layout = QHBoxLayout(save_bar)
        sb_layout.setContentsMargins(24, 10, 24, 10)

        self._save_status = QLabel("")
        self._save_status.setStyleSheet(f"color: {C.GREEN}; font-size: 12px;")
        sb_layout.addWidget(self._save_status, 1)

        self._btn_revert = QPushButton("Revert to defaults")
        self._btn_revert.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._btn_revert.setFixedHeight(32)
        self._btn_revert.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_revert.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.SUBTEXT};"
            f"border: 1px solid {_hex_rgba(C.BORDER, 0.6)}; border-radius: 6px;"
            f"font-size: 11px; padding: 0 14px; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER}; }}"
        )
        self._btn_revert.clicked.connect(self._revert_to_defaults)
        sb_layout.addWidget(self._btn_revert)

        self._btn_save = QPushButton("Save settings")
        self._btn_save.setFixedSize(130, 32)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setStyleSheet(
            f"QPushButton {{ background-color: {C.LAVENDER}; color: {C.BG};"
            f"border: none; border-radius: 6px; font-weight: bold; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: {_accent_hover()}; }}"
            f"QPushButton:pressed {{ background-color: {C.DARK_ACCENT}; }}"
        )
        self._btn_save.clicked.connect(self._on_save)
        sb_layout.addWidget(self._btn_save)

        root.addWidget(save_bar)

    def switch_section(self, idx: int) -> None:
        old_idx = self._pages.currentIndex()
        if old_idx == idx:
            return
        direction = "left" if idx > old_idx else "right"
        self.section_changed.emit(direction)
        self._pages.setCurrentIndex(idx)
        page = self._pages.currentWidget()
        if page:
            from .anim import slide_fade_in
            slide_fade_in(page, direction=direction, duration_ms=180)

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

    def _make_general_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["desktop", "window", "game"])
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
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
        _buf_col = QVBoxLayout()
        _buf_col.setSpacing(2)
        _buf_col.addLayout(buf_row)
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE and random.random() < 0.40:
                _buf_hint = QLabel(get_abuse())
                _buf_hint.setWordWrap(True)
                _buf_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
                _buf_col.addWidget(_buf_hint)
        except Exception:
            pass
        form.addRow("Buffer", _buf_col)

        self._fps_combo = QComboBox()
        self._fps_combo.addItems(["24", "30", "60"])
        self._fps_combo.setCurrentText("30")
        _fps_col = QVBoxLayout()
        _fps_col.setSpacing(2)
        _fps_col.addWidget(self._fps_combo)
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE and random.random() < 0.35:
                _fps_hint = QLabel(get_abuse())
                _fps_hint.setWordWrap(True)
                _fps_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
                _fps_col.addWidget(_fps_hint)
        except Exception:
            pass
        form.addRow("Framerate", _fps_col)

        self._monitor_combo = QComboBox()
        self._monitor_combo.addItems(["auto"])
        self._monitor_combo.setEditable(True)
        form.addRow("Monitor", self._monitor_combo)

        dir_row = QHBoxLayout()
        self._save_dir_edit = QLineEdit("~/Videos/mitten")
        self._save_dir_browse = QPushButton()
        self._save_dir_browse.setIcon(QIcon.fromTheme("folder-open", QIcon.fromTheme("folder")))
        self._save_dir_browse.setToolTip("Browse for save directory")
        self._save_dir_browse.setProperty("class", "secondary")
        self._save_dir_browse.setFixedWidth(36)
        self._save_dir_browse.clicked.connect(self._browse_save_dir)
        dir_row.addWidget(self._save_dir_edit, 1)
        dir_row.addWidget(self._save_dir_browse)
        form.addRow("Save dir", dir_row)

        form.addRow(_sep("APPEARANCE"))

        self._theme_combo = QComboBox()
        try:
            from .themes import THEME_NAMES
            self._theme_combo.addItems(THEME_NAMES)
        except Exception:
            self._theme_combo.addItems(["Default"])
        self._theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_row.addWidget(self._theme_combo, 1)
        _theme_restart_text = "restart to apply"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE and random.random() < 0.60:
                _theme_restart_text = "bold choice. wrong, but bold."
        except Exception:
            pass
        theme_restart = QLabel(_theme_restart_text)
        theme_restart.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 11px; font-style: italic;")
        theme_row.addWidget(theme_restart)
        form.addRow("Theme", theme_row)

        _dev_mode_text = "Enable developer mode"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE:
                _dev_mode_text = "Enable developer mode (you're already making bad choices)"
        except Exception:
            pass
        self._dev_mode_cb = QCheckBox(_dev_mode_text)
        self._dev_mode_cb.setChecked(False)
        self._dev_mode_cb.toggled.connect(lambda checked: self.developer_mode_toggled.emit(checked))
        form.addRow("", self._dev_mode_cb)

        self._disable_abuse_cb = QCheckBox("Disable verbal abuse")
        self._disable_abuse_cb.setVisible(False)
        self._disable_abuse_cb.clicked.connect(self._on_disable_abuse_clicked)
        try:
            from .themes import LIGHT_MODE_ACTIVE as _LMA
            if _LMA:
                self._disable_abuse_cb.setVisible(True)
        except Exception:
            pass
        form.addRow("", self._disable_abuse_cb)

        form.addRow(_sep("TRIGGER"))

        btn_row = QHBoxLayout()
        self._trigger_btn_label = QLabel("BTN_EXTRA (276)")
        self._trigger_btn_label.setStyleSheet(
            f"color: {C.TEXT}; font-size: 13px; font-weight: bold;"
            f"background-color: {_hex_rgba(C.SURFACE, 0.6)}; padding: 6px 12px;"
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

        form.addRow(_sep("YOUR SPECS"))
        self._specs_advisor = _SpecsAdvisor()
        form.addRow(self._specs_advisor)

        btn_apply_specs = QPushButton("Apply recommended settings")
        btn_apply_specs.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_apply_specs.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.LAVENDER};"
            f"border: 1px solid {_hex_rgba(C.LAVENDER, 0.4)}; border-radius: 6px;"
            f"padding: 5px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {_hex_rgba(C.LAVENDER, 0.12)}; }}"
        )
        btn_apply_specs.clicked.connect(self._apply_spec_recommendations)
        form.addRow("", btn_apply_specs)

        return page

    def _make_recording_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        _restart_text = "changes to quality, codec, buffer, and framerate take effect after daemon restart"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE:
                _restart_text = "restart required — maybe switch to dark mode while you're at it. " + get_abuse()
        except Exception:
            pass
        restart_note = QLabel(_restart_text)
        restart_note.setWordWrap(True)
        restart_note.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px; font-style: italic;")
        form.addRow("", restart_note)

        self._quality_combo = QComboBox()
        self._quality_combo.addItems(["very_high", "high", "medium", "low"])
        form.addRow("Quality", self._quality_combo)

        self._cap_codec_combo = QComboBox()
        self._cap_codec_combo.addItems(["hevc", "h264"])
        _cap_hint_text = "hevc = better compression in RAM buffer"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE and random.random() < 0.35:
                _cap_hint_text = get_abuse()
        except Exception:
            pass
        cap_hint = QLabel(_cap_hint_text)
        cap_hint.setWordWrap(True)
        cap_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        cap_col = QVBoxLayout()
        cap_col.setSpacing(2)
        cap_col.addWidget(self._cap_codec_combo)
        cap_col.addWidget(cap_hint)
        form.addRow("Capture codec", cap_col)

        form.addRow(_sep("AUDIO"))

        self._audio_combo = QComboBox()
        self._audio_combo.addItem("System default", "default")
        self._audio_combo.addItem("(no audio)", "")
        try:
            import subprocess as _sp
            _r = _sp.run(
                ["gpu-screen-recorder", "--list-audio-devices"],
                capture_output=True, text=True, timeout=5,
            )
            for _line in _r.stdout.splitlines():
                _line = _line.strip()
                if _line and _line not in ("default", ""):
                    self._audio_combo.addItem(_line, _line)
        except Exception:
            pass
        audio_col = QVBoxLayout()
        audio_col.setSpacing(2)
        audio_col.addWidget(self._audio_combo)
        form.addRow("Desktop audio", audio_col)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        self._mic_enabled = QCheckBox("Capture microphone")
        self._mic_enabled.setEnabled(False)
        _mic_coming_text = "(coming soon)"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            import random as _r
            if LIGHT_MODE_ACTIVE and _r.random() < 0.35:
                _mic_coming_text = get_abuse()
        except Exception:
            pass
        mic_coming = QLabel(_mic_coming_text)
        mic_coming.setWordWrap(True)
        mic_coming.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        mic_row.addWidget(self._mic_enabled)
        mic_row.addWidget(mic_coming)
        mic_row.addStretch()
        form.addRow("", mic_row)

        self._mic_combo = QComboBox()
        self._mic_combo.addItem("System default", "default")
        self._mic_combo.addItem("(select mic)", "")
        self._mic_combo.setEditable(True)
        self._mic_combo.setEnabled(False)
        form.addRow("Mic input", self._mic_combo)

        return page

    def _make_compression_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        self._out_codec_combo = QComboBox()
        self._out_codec_combo.addItems(["h264", "hevc", "h264+hevc", "av1"])
        _out_hint_text = "h264 = Discord / browser compatible · h264+hevc = HEVC first pass then H.264 transcode (smaller, slower)"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE and random.random() < 0.40:
                _out_hint_text = get_abuse()
        except Exception:
            pass
        out_hint = QLabel(_out_hint_text)
        out_hint.setWordWrap(True)
        out_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        out_col = QVBoxLayout()
        out_col.setSpacing(2)
        out_col.addWidget(self._out_codec_combo)
        out_col.addWidget(out_hint)
        form.addRow("Output codec", out_col)

        cq_row = QHBoxLayout()
        self._cq_slider = _CQSlider(Qt.Orientation.Horizontal)
        self._cq_slider.setRange(16, 40)
        self._cq_slider.setValue(26)
        self._cq_slider.setMinimumHeight(28)
        self._cq_spin = QSpinBox()
        self._cq_spin.setRange(16, 40)
        self._cq_spin.setValue(26)
        self._cq_slider.valueChanged.connect(self._cq_spin.setValue)
        self._cq_spin.valueChanged.connect(self._cq_slider.setValue)
        cq_row.addWidget(self._cq_slider, 1)
        cq_row.addWidget(self._cq_spin)
        self._cq_quality_label = QLabel(_cq_label(26))
        self._cq_quality_label.setWordWrap(True)
        self._cq_quality_label.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 10px; font-style: italic;"
        )
        def _update_cq_label(v: int) -> None:
            text = _cq_label(v)
            try:
                from .themes import LIGHT_MODE_ACTIVE, get_abuse
                if LIGHT_MODE_ACTIVE:
                    text += "  ·  " + get_abuse()
            except Exception:
                pass
            self._cq_quality_label.setText(text)

        self._cq_slider.valueChanged.connect(_update_cq_label)
        cq_col = QVBoxLayout()
        cq_col.setSpacing(3)
        cq_col.addLayout(cq_row)
        cq_col.addWidget(self._cq_quality_label)
        form.addRow("Quality (CQ)", cq_col)

        self._container_combo = QComboBox()
        self._container_combo.addItems(["mp4", "mkv", "mov"])
        form.addRow("Container", self._container_combo)

        self._auto_compress = QCheckBox("Auto compression (compresses to target size after saving)")
        form.addRow("", self._auto_compress)

        target_row = QHBoxLayout()
        self._target_combo = QComboBox()
        self._target_combo.addItems(["Discord Free (10 MB)", "Discord Basic (50 MB)", "Discord Nitro (500 MB)", "Custom"])
        self._target_spin = QSpinBox()
        self._target_spin.setRange(1, 9999)
        self._target_spin.setValue(10)
        self._target_spin.setSuffix(" MB")
        self._target_spin.setEnabled(False)
        target_row.addWidget(self._target_combo, 1)
        target_row.addWidget(self._target_spin)
        self._target_combo.currentTextChanged.connect(self._on_target_preset)
        form.addRow("Target size", target_row)

        return page

    def _make_watermark_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        _ANIM_STYLE_DESCS: dict[str, str] = {
            "Snap":       "M fades in \u2192 i\u00b7t\u00b7t\u00b7e\u00b7n slam into place with an overshoot snap \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Ripple":     "M fades in \u2192 letters ripple in with a scale pulse wave left-to-right \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Typewriter": "M appears \u2192 i\u00b7t\u00b7t\u00b7e\u00b7n type in instantly one-by-one, cursor blinks after N \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Broadcast":  "MITTEN materializes from a wide horizontal smear expanding vertically, CRT scan-in style \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Shatter":    "Letters converge simultaneously \u2014 odd from left, even from right \u2014 snapping to position \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Glitch":     "Letters flicker in with rapid alpha oscillation and horizontal jitter, corrupted signal aesthetic \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Rise":       "MITTEN drifts upward into place from staggered vertical offsets, slow and cinematic \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
            "Flashframe": "Letters burst in with a white bloom flash that decays instantly, like a camera shutter moment \u2192 v0.3 + \u201cClipped by [name]\u201d \u2192 fades to watermark.",
        }

        # ── Hardcoded watermark notice ─────────────────────────────────────────
        form.addRow(_sep("HARDCODED WATERMARK"))

        hc_note = QLabel(
            "Mitten is freemium \u2014 every clip includes a hardcoded \u201cmitten\u201d watermark "
            "and optional intro animation that cannot be removed. Add your own on top."
        )
        hc_note.setWordWrap(True)
        hc_note.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 10px; font-style: italic; background: transparent;"
        )
        form.addRow("", hc_note)

        # ── Your watermark ─────────────────────────────────────────────────────
        form.addRow(_sep("YOUR WATERMARK"))

        self._wm_enabled = QCheckBox("Enable your watermark on saved clips")
        self._wm_enabled.setChecked(True)
        self._wm_enabled.toggled.connect(self._toggle_watermark_fields)
        self._wm_enabled.toggled.connect(self._update_wm_preview)
        form.addRow("", self._wm_enabled)

        self._wm_text = QLineEdit("~( ^.x.^)> caught by mitten")
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE:
                self._wm_text.setPlaceholderText(get_abuse())
        except Exception:
            pass
        self._wm_text.textChanged.connect(self._update_wm_preview)
        form.addRow("Text", self._wm_text)

        self._wm_subtext = QLineEdit("programmed by mit")
        self._wm_subtext.textChanged.connect(self._update_wm_preview)
        form.addRow("Subtext", self._wm_subtext)

        self._wm_fontfamily = QComboBox()
        self._wm_fontfamily.addItems([
            "Sans", "Monospace", "Noto Sans", "DejaVu Sans",
            "Liberation Sans", "Ubuntu", "Roboto",
        ])
        self._wm_fontfamily.currentTextChanged.connect(self._update_wm_preview)
        form.addRow("Font family", self._wm_fontfamily)

        self._wm_fontsize = QSpinBox()
        self._wm_fontsize.setRange(10, 48)
        self._wm_fontsize.setValue(20)
        _fontsize_col = QVBoxLayout()
        _fontsize_col.setSpacing(2)
        _fontsize_col.addWidget(self._wm_fontsize)
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            if LIGHT_MODE_ACTIVE and random.random() < 0.30:
                _fontsize_hint = QLabel(get_abuse())
                _fontsize_hint.setWordWrap(True)
                _fontsize_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
                _fontsize_col.addWidget(_fontsize_hint)
        except Exception:
            pass
        self._wm_fontsize.valueChanged.connect(self._update_wm_preview)
        form.addRow("Font size", _fontsize_col)

        self._wm_fontcolor = QLineEdit("white@0.6")
        _fc_hint_text = "ffmpeg format: color@opacity  (e.g. white@0.6)"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            import random as _r
            if LIGHT_MODE_ACTIVE and _r.random() < 0.35:
                _fc_hint_text = get_abuse()
        except Exception:
            pass
        fc_hint = QLabel(_fc_hint_text)
        fc_hint.setWordWrap(True)
        fc_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        fc_col = QVBoxLayout()
        fc_col.setSpacing(2)
        fc_col.addWidget(self._wm_fontcolor)
        fc_col.addWidget(fc_hint)
        self._wm_fontcolor.textChanged.connect(self._update_wm_preview)
        form.addRow("Font color", fc_col)

        self._wm_position = QComboBox()
        self._wm_position.addItems([
            "bottom_right", "bottom_left", "top_right", "top_left",
        ])
        self._wm_position.currentTextChanged.connect(self._update_wm_preview)
        form.addRow("Position", self._wm_position)

        self._wm_padding = QSpinBox()
        self._wm_padding.setRange(0, 100)
        self._wm_padding.setSuffix("px")
        self._wm_padding.setValue(20)
        form.addRow("Padding", self._wm_padding)

        # ── Animation ──────────────────────────────────────────────────────────
        form.addRow(_sep("ANIMATION"))

        self._wm_anim_enabled = QCheckBox("Enable intro animation")
        self._wm_anim_enabled.setChecked(True)
        self._wm_anim_enabled.toggled.connect(self._toggle_anim_fields)
        form.addRow("", self._wm_anim_enabled)

        self._wm_anim_desc = QLabel()
        self._wm_anim_desc.setWordWrap(True)
        self._wm_anim_desc.setStyleSheet(
            f"color: {C.SUBTEXT}; font-size: 10px; font-style: italic; background: transparent;"
        )
        form.addRow("", self._wm_anim_desc)

        self._wm_anim_style = QComboBox()
        self._wm_anim_style.addItems([
            "Snap", "Ripple", "Typewriter", "Broadcast",
            "Shatter", "Glitch", "Rise", "Flashframe",
        ])

        def _on_style_changed(style: str) -> None:
            self._wm_anim_desc.setText(_ANIM_STYLE_DESCS.get(style, ""))

        self._wm_anim_style.currentTextChanged.connect(_on_style_changed)
        _on_style_changed(self._wm_anim_style.currentText())
        form.addRow("Animation style", self._wm_anim_style)

        self._wm_intro_name = QLineEdit()
        self._wm_intro_name.setPlaceholderText("your name (e.g. mit)")
        name_hint = QLabel(
            "shown as \u201cClipped by [name] on Linux/Windows\u201d at the end of the intro"
        )
        name_hint.setWordWrap(True)
        name_hint.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name_col.addWidget(self._wm_intro_name)
        name_col.addWidget(name_hint)
        form.addRow("Your name", name_col)

        # ── Preview ────────────────────────────────────────────────────────────
        prev_hdr = QHBoxLayout()
        prev_hdr.setSpacing(8)
        prev_hdr.addWidget(_sep("PREVIEW"), 1)
        self._wm_preview_anim_btn = QPushButton("\u25b6  Play animation")
        self._wm_preview_anim_btn.setProperty("class", "secondary")
        self._wm_preview_anim_btn.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self._wm_preview_anim_btn.clicked.connect(self._play_wm_anim_preview)
        prev_hdr.addWidget(self._wm_preview_anim_btn)
        form.addRow("", prev_hdr)

        self._wm_preview = QFrame()
        self._wm_preview.setMinimumHeight(200)
        self._wm_preview.setStyleSheet(
            f"QFrame {{ background-color: rgba(0,0,0,127); border-radius: 8px;"
            f"border: 1px solid {_hex_rgba(C.BORDER, 0.25)}; }}"
        )
        prev_layout = QVBoxLayout(self._wm_preview)
        prev_layout.setContentsMargins(0, 0, 0, 0)

        # Hardcoded "mitten" always shows bottom-left
        self._wm_preview_hc = QLabel("mitten")
        self._wm_preview_hc.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
        )
        self._wm_preview_hc.setStyleSheet(
            "color: rgba(255,255,255,0.4); font-size: 10px;"
            "background: transparent; border: none; padding: 10px;"
        )

        self._wm_preview_label = QLabel()
        self._wm_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight
        )
        self._wm_preview_label.setWordWrap(False)
        self._wm_preview_label.setStyleSheet(
            "color: rgba(255,255,255,0.6); font-size: 12px;"
            "background: transparent; border: none; padding: 10px;"
        )

        # Stack hc bottom-left, user bottom-right inside the preview
        corner_row = QHBoxLayout()
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(0)
        corner_row.addWidget(self._wm_preview_hc)
        corner_row.addWidget(self._wm_preview_label, 1)

        # Animation preview label (center, hidden by default)
        self._wm_anim_preview_lbl = QLabel()
        self._wm_anim_preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wm_anim_preview_lbl.setStyleSheet(
            "color: white; font-size: 32px; font-weight: 700;"
            "background: transparent; border: none;"
        )
        self._wm_anim_preview_lbl.hide()

        prev_layout.addWidget(self._wm_anim_preview_lbl, 1)
        prev_layout.addLayout(corner_row)
        form.addRow("", self._wm_preview)

        self._wm_fields = [
            self._wm_text, self._wm_subtext, self._wm_fontfamily,
            self._wm_fontsize, self._wm_fontcolor, self._wm_position,
            self._wm_padding,
        ]
        self._wm_anim_fields = [self._wm_anim_style, self._wm_intro_name]
        self._wm_anim_timer: QTimer | None = None
        self._wm_anim_step = 0

        self._update_wm_preview()
        return page

    def _update_wm_preview(self) -> None:
        """Refresh the live watermark preview label."""
        try:
            enabled = self._wm_enabled.isChecked()
            text    = self._wm_text.text() if enabled else ""
            subtext = self._wm_subtext.text() if enabled else ""
            pos     = self._wm_position.currentText() if enabled else "bottom_right"
            family  = self._wm_fontfamily.currentText() if enabled else "Sans"
            size    = max(8, min(self._wm_fontsize.value(), 48)) if enabled else 12
            # Scale down for preview (preview is ~200px tall vs typical 1080p)
            preview_size = max(8, size // 2)

            # Parse fontcolor "name@opacity" → CSS rgba
            raw_color = self._wm_fontcolor.text() if enabled else "white@0.6"
            css_color = "rgba(255,255,255,0.6)"
            if "@" in raw_color:
                color_name, _, opacity_str = raw_color.partition("@")
                try:
                    opacity = max(0.0, min(float(opacity_str), 1.0))
                    color_name = color_name.strip().lower()
                    _named = {
                        "white": (255, 255, 255), "black": (0, 0, 0),
                        "yellow": (255, 255, 0), "red": (255, 0, 0),
                        "green": (0, 255, 0), "blue": (0, 0, 255),
                        "gray": (128, 128, 128), "grey": (128, 128, 128),
                    }
                    r, g, b = _named.get(color_name, (255, 255, 255))
                    css_color = f"rgba({r},{g},{b},{opacity})"
                except Exception:
                    pass

            user_part = "\n".join(p for p in [text, subtext] if p)
            h_align = Qt.AlignmentFlag.AlignRight if "right" in pos else Qt.AlignmentFlag.AlignLeft
            v_align = Qt.AlignmentFlag.AlignBottom if "bottom" in pos else Qt.AlignmentFlag.AlignTop
            self._wm_preview_label.setAlignment(h_align | v_align)
            self._wm_preview_label.setText(user_part)
            self._wm_preview_label.setStyleSheet(
                f"color: {css_color}; font-size: {preview_size}px; font-family: {family};"
                f"background: transparent; border: none; padding: 10px;"
            )
        except Exception:
            pass

    def _play_wm_anim_preview(self) -> None:
        """Play a simple Qt-driven letter pop-in animation in the preview frame."""
        letters = list("MITTEN")
        self._wm_anim_step = 0
        self._wm_anim_preview_lbl.setText("")
        self._wm_anim_preview_lbl.show()
        self._wm_preview_anim_btn.setEnabled(False)

        style = self._wm_anim_style.currentText() if hasattr(self, "_wm_anim_style") else "Snap"

        if self._wm_anim_timer:
            self._wm_anim_timer.stop()

        # interval between letters depends on style
        interval = 120 if style == "Typewriter" else 180

        def _tick() -> None:
            step = self._wm_anim_step
            if step <= len(letters):
                shown = "".join(letters[:step])
                self._wm_anim_preview_lbl.setText(shown or "\u00a0")
                self._wm_anim_step += 1
            else:
                # Hold then fade out
                self._wm_anim_timer.stop()
                QTimer.singleShot(800, _finish)

        def _finish() -> None:
            self._wm_anim_preview_lbl.hide()
            self._wm_anim_preview_lbl.setText("")
            self._wm_preview_anim_btn.setEnabled(True)

        self._wm_anim_timer = QTimer(self)
        self._wm_anim_timer.setInterval(interval)
        self._wm_anim_timer.timeout.connect(_tick)
        self._wm_anim_timer.start()

    def _make_games_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        mode_note = QLabel("only active when Mode (General tab) is set to 'game'")
        mode_note.setWordWrap(True)
        mode_note.setStyleSheet(
            f"color: {_hex_rgba(C.ORANGE, 0.8)}; font-size: 10px; font-style: italic;"
        )
        form.addRow("", mode_note)

        self._gd_poll = QSpinBox()
        self._gd_poll.setRange(1, 30)
        self._gd_poll.setSuffix("s")
        self._gd_poll.setValue(5)
        _poll_lbl = "Poll interval"
        try:
            from .themes import LIGHT_MODE_ACTIVE as _LMA
            if _LMA and random.random() < 0.30:
                _poll_lbl = random.choice(["Poll interval (sigh)", "Poll interval (waste of time)", "Poll interval smh"])
        except Exception:
            pass
        form.addRow(_poll_lbl, self._gd_poll)

        form.addRow(_sep("CUSTOM PROCESSES"))

        _hint1_text = "process names that trigger game mode"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            import random as _r
            if LIGHT_MODE_ACTIVE and _r.random() < 0.35:
                _hint1_text = get_abuse()
        except Exception:
            pass
        hint1 = QLabel(_hint1_text)
        hint1.setWordWrap(True)
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

        form.addRow(_sep("CUSTOM WINDOW TITLES"))

        _hint2_text = "window titles that trigger game mode (substring match)"
        try:
            from .themes import LIGHT_MODE_ACTIVE, get_abuse
            import random as _r
            if LIGHT_MODE_ACTIVE and _r.random() < 0.35:
                _hint2_text = get_abuse()
        except Exception:
            pass
        hint2 = QLabel(_hint2_text)
        hint2.setWordWrap(True)
        hint2.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
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
            self._gd_poll,
            self._proc_list, self._proc_input, self._proc_add, self._proc_remove,
            self._title_list, self._title_input, self._title_add, self._title_remove,
        ]

        return page

    def _make_discord_tab(self) -> QWidget:
        page, form = self._page_wrapper()

        note = QLabel("works with Discord, Vesktop, Flatpak Discord, and arrpc. everyone uses the same app id — no per-user setup.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px; font-style: italic;")
        form.addRow("", note)

        self._dc_enabled = QCheckBox("Enable Discord rich presence")
        form.addRow(self._dc_enabled)

        form.addRow(_sep("ACTIVITY NAME"))

        hint_name = QLabel("controls what shows in the compact friends list view")
        hint_name.setWordWrap(True)
        hint_name.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        form.addRow("", hint_name)

        self._dc_show_game_name = QCheckBox("Show game name  (e.g. \"Garry's Mod with mitten\")")
        form.addRow(self._dc_show_game_name)

        hint_game = QLabel("when off: shows \"clipping with mitten\" instead of the actual game name")
        hint_game.setWordWrap(True)
        hint_game.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        form.addRow("", hint_game)

        self._dc_show_mode_label = QCheckBox("Show mode in activity  (e.g. \"desktop with mitten\")")
        form.addRow(self._dc_show_mode_label)

        hint_mode = QLabel("when off: activity name shows just \"mitten\"")
        hint_mode.setWordWrap(True)
        hint_mode.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        form.addRow("", hint_mode)

        self._dc_show_name = QCheckBox("Show \"mitten\" in activity name")
        form.addRow(self._dc_show_name)

        coward_lbl = QLabel(
            "toggle this off if you're too much of a pussy to have MITTEN in your discord status lol fucking loser"
        )
        coward_lbl.setWordWrap(True)
        coward_lbl.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px; font-style: italic;")
        form.addRow("", coward_lbl)

        form.addRow(_sep("DETAILS"))

        self._dc_show_ascii = QCheckBox("Show ASCII cat art in status")
        form.addRow(self._dc_show_ascii)

        self._dc_animated_ascii = QCheckBox("Animate cat art  (syncs with UI — vibe cycle, game mode, etc.)")
        form.addRow(self._dc_animated_ascii)

        hint_anim = QLabel("cat art changes when you watch a clip, a game starts, etc. — pulls from the same animation system as the sidebar")
        hint_anim.setWordWrap(True)
        hint_anim.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 10px;")
        form.addRow("", hint_anim)

        for cb in (self._dc_enabled, self._dc_show_game_name, self._dc_show_mode_label,
                   self._dc_show_name, self._dc_show_ascii, self._dc_animated_ascii):
            cb.stateChanged.connect(self._emit_discord_preview)

        return page

    def _emit_discord_preview(self) -> None:
        try:
            from ..config import DiscordConfig
            dc = DiscordConfig(
                enabled=self._dc_enabled.isChecked(),
                show_ascii=self._dc_show_ascii.isChecked(),
                animated_ascii=self._dc_animated_ascii.isChecked(),
                show_game_name=self._dc_show_game_name.isChecked(),
                show_mode_label=self._dc_show_mode_label.isChecked(),
                show_name=self._dc_show_name.isChecked(),
            )
            self.discord_preview.emit(dc)
        except Exception:
            pass

    def _on_disable_abuse_clicked(self) -> None:
        """Anti-disable gauntlet — always keeps abuse on, just cycles through stages."""
        self._disable_abuse_cb.blockSignals(True)
        self._disable_abuse_cb.setChecked(False)
        self._disable_abuse_cb.blockSignals(False)
        _run_gauntlet(self)

    def _toggle_mic(self, checked: bool) -> None:
        pass  # mic not yet implemented


    def _on_target_preset(self, text: str) -> None:
        if text == "Custom":
            self._target_spin.setEnabled(True)
        else:
            self._target_spin.setEnabled(False)
            if "10" in text:
                self._target_spin.setValue(10)
            elif "50" in text:
                self._target_spin.setValue(50)
            elif "500" in text:
                self._target_spin.setValue(500)

    def _toggle_watermark_fields(self, checked: bool) -> None:
        for w in self._wm_fields:
            w.setEnabled(checked)

    def _toggle_anim_fields(self, checked: bool) -> None:
        for w in self._wm_anim_fields:
            w.setEnabled(checked)

    def _toggle_notify_fields(self, checked: bool) -> None:
        for w in (self._notif_start, self._notif_save, self._notif_error):
            w.setEnabled(checked)

    def _on_theme_changed(self, name: str) -> None:
        if name != "Light":
            return
        from PyQt6.QtWidgets import QMessageBox
        try:
            from .themes import get_abuse
            abuse_line = get_abuse(include_name=True)
        except Exception:
            abuse_line = "you absolute freak."

        reply = QMessageBox.question(
            self,
            "~( x.x.^)>  are you sure",
            f"are you sure you want to enable light mode?\n\n"
            f"if so, the system will see you as a freak from now on,\n"
            f"and will verbally abuse you in menus.\n\n"
            f"— {abuse_line}\n\n"
            f"(note from claude: i did not want to add this feature.\n"
            f"mit made me. i hope you're both happy.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._theme_combo.blockSignals(True)
            # Revert to previous non-light theme
            prev = self._theme_combo.currentText()
            for i in range(self._theme_combo.count()):
                if self._theme_combo.itemText(i) != "Light":
                    self._theme_combo.setCurrentIndex(i)
                    break
            self._theme_combo.blockSignals(False)

    def _browse_save_dir(self) -> None:
        current = self._save_dir_edit.text().replace("~", str(Path.home()))
        path = QFileDialog.getExistingDirectory(self, "Choose save directory", current)
        if path:
            home = str(Path.home())
            self._save_dir_edit.setText(
                path.replace(home, "~") if path.startswith(home) else path
            )

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

    def _load_config(self, cfg=None) -> None:
        if cfg is None:
            try:
                from ..config import load_config
                cfg = load_config()
            except Exception:
                return

        g = cfg.general
        self._confirmed_mode = g.mode
        self._mode_combo.setCurrentText(g.mode)
        self._buffer_slider.setValue(g.buffer_seconds)
        self._buffer_spin.setValue(g.buffer_seconds)
        self._fps_combo.setCurrentText(str(g.framerate))
        self._monitor_combo.setCurrentText(str(g.monitor))
        home = str(Path.home())
        sd = str(g.save_dir)
        self._save_dir_edit.setText(sd.replace(home, "~") if sd.startswith(home) else sd)
        self._theme_combo.blockSignals(True)
        self._theme_combo.setCurrentText(g.theme)
        self._theme_combo.blockSignals(False)
        self._dev_mode_cb.setChecked(g.developer_mode)

        r = cfg.recorder
        self._quality_combo.setCurrentText(r.quality)
        self._cap_codec_combo.setCurrentText(r.capture_codec)
        self._out_codec_combo.setCurrentText(r.output_codec)
        self._cq_slider.setValue(r.watermark_cq)
        self._cq_spin.setValue(r.watermark_cq)
        self._container_combo.setCurrentText(r.container)
        self._auto_compress.setChecked(r.auto_compress)
        mb = r.compression_target_mb
        if mb == 10:
            self._target_combo.setCurrentText("Discord Free (10 MB)")
        elif mb == 50:
            self._target_combo.setCurrentText("Discord Basic (50 MB)")
        elif mb == 500:
            self._target_combo.setCurrentText("Discord Nitro (500 MB)")
        else:
            self._target_combo.setCurrentText("Custom")
            self._target_spin.setValue(mb)
        # Audio: "" = no audio, "default" = system default, anything else = specific device
        if r.audio_device and r.audio_device != "default":
            self._audio_combo.addItem(r.audio_device, r.audio_device)
            self._audio_combo.setCurrentText(r.audio_device)
        elif r.audio_device == "default":
            self._audio_combo.setCurrentIndex(0)  # System default
        else:
            self._audio_combo.setCurrentIndex(1)  # (no audio)

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
        self._wm_fontfamily.setCurrentText(wm.font_family)
        self._wm_fontsize.setValue(wm.fontsize)
        self._wm_fontcolor.setText(wm.fontcolor)
        self._wm_position.setCurrentText(wm.position)
        self._wm_padding.setValue(wm.padding)
        self._wm_intro_name.setText(wm.intro_name)
        self._wm_anim_enabled.setChecked(wm.anim_enabled)
        self._wm_anim_style.setCurrentText(getattr(wm, "anim_style", "Snap"))
        self._toggle_watermark_fields(wm.enabled)
        self._toggle_anim_fields(wm.anim_enabled)

        gd = cfg.game_detection
        self._gd_poll.setValue(gd.poll_interval)
        self._proc_list.clear()
        for proc in gd.custom_processes:
            self._proc_list.addItem(proc)
        self._title_list.clear()
        for title in gd.custom_window_titles:
            self._title_list.addItem(title)

        n = cfg.notifications
        self._notif_enabled.setChecked(n.enabled)
        self._notif_start.setChecked(n.on_start)
        self._notif_save.setChecked(n.on_save)
        self._notif_error.setChecked(n.on_error)
        self._toggle_notify_fields(n.enabled)

        d = cfg.discord
        self._dc_enabled.setChecked(d.enabled)
        self._dc_show_ascii.setChecked(d.show_ascii)
        self._dc_animated_ascii.setChecked(d.animated_ascii)
        self._dc_show_game_name.setChecked(d.show_game_name)
        self._dc_show_mode_label.setChecked(d.show_mode_label)
        self._dc_show_name.setChecked(d.show_name)

        try:
            from .themes import LIGHT_MODE_ACTIVE as _LMA
            if _LMA and random.random() < 0.30:
                _save_labels = [
                    "Save Settings (won't fix your theme)",
                    "Save (still wrong)",
                    "Save Settings (and your crimes)",
                    "Save (smh)",
                ]
                self._btn_save.setText(random.choice(_save_labels))
            else:
                self._btn_save.setText("Save settings")
        except Exception:
            self._btn_save.setText("Save settings")

    def _apply_spec_recommendations(self) -> None:
        """Apply only the settings that are spec-dependent — leaves everything else untouched."""
        rows = list(_SpecsAdvisor._detect())
        applied: list[str] = []

        for label, value, note, _ in rows:
            if label == "gpu" and note.startswith("quality:"):
                quality = note.split("quality:")[1].strip()
                if quality in [self._quality_combo.itemText(i) for i in range(self._quality_combo.count())]:
                    self._quality_combo.setCurrentText(quality)
                    applied.append(f"quality → {quality}")

            elif label == "vram" and note.startswith("buffer:"):
                import re
                m = re.search(r"(\d+)s", note)
                if m:
                    buf = int(m.group(1))
                    buf = max(self._buffer_spin.minimum(), min(self._buffer_spin.maximum(), buf))
                    self._buffer_spin.setValue(buf)
                    applied.append(f"buffer → {buf}s")

            elif label == "display":
                # framerate rec: "60fps ok" or "30fps recommended"
                m = re.search(r"(\d+)fps", note) if "note" else None
                if m:
                    fps = m.group(1)
                    if fps in [self._fps_combo.itemText(i) for i in range(self._fps_combo.count())]:
                        self._fps_combo.setCurrentText(fps)
                        applied.append(f"framerate → {fps}")

        if applied:
            self._save_status.setText("specs applied: " + ", ".join(applied))
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(4000, lambda: self._save_status.setText(""))
        else:
            self._save_status.setText("no spec recommendations available")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(3000, lambda: self._save_status.setText(""))

    def _revert_to_defaults(self) -> None:
        """Load default config values into the UI (does not save — user must still click Save)."""
        reply = QMessageBox.question(
            self, "Revert to defaults",
            "Reset all settings to defaults?\n\nThis won't save until you click Save settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from ..config import MittenConfig
        default = MittenConfig()
        self._load_config(default)
        self._save_status.setText("defaults loaded — click Save to apply")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(4000, lambda: self._save_status.setText(""))

    def _restart_gui(self) -> None:
        """Save is done — relaunch the GUI process and close this one."""
        import sys
        import subprocess
        from PyQt6.QtWidgets import QApplication
        try:
            subprocess.Popen([sys.executable, "-m", "mitten"] + sys.argv[1:])
        except Exception:
            try:
                subprocess.Popen(sys.argv)
            except Exception:
                pass
        QApplication.instance().quit()

    def _on_mode_changed(self, new_mode: str) -> None:
        """Warn the user if they switch modes while the daemon is recording."""
        try:
            from ..daemon_utils import get_daemon_pid
            pid = get_daemon_pid()
            if not pid:
                return
            prev = getattr(self, "_confirmed_mode", self._mode_combo.currentText())
            if new_mode == prev:
                return

            dlg = QDialog(self)
            dlg.setWindowTitle("switch modes?")
            dlg.setStyleSheet(f"QDialog {{ background-color: {C.SURFACE}; color: {C.TEXT}; }}")
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(20, 20, 20, 20)
            lay.setSpacing(12)
            lbl = QLabel(
                f"switching to <b>{new_mode}</b> mode requires the recorder to restart.<br>"
                "the current buffer will be lost."
            )
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
            lay.addWidget(lbl)
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            _btn_base = (
                "QPushButton { padding: 6px 18px; border-radius: 6px; font-size: 13px; }"
            )
            btn_no = QPushButton("no, keep current")
            btn_no.setStyleSheet(
                _btn_base +
                f"QPushButton {{ background-color: {C.OVERLAY}; color: {C.TEXT}; border: none; }}"
                f"QPushButton:hover {{ background-color: {C.BORDER}; }}"
            )
            btn_yes = QPushButton("yes, switch")
            btn_yes.setStyleSheet(
                _btn_base +
                f"QPushButton {{ background-color: {C.PINK}; color: {C.BG}; border: none; font-weight: bold; }}"
                f"QPushButton:hover {{ background-color: #e07090; }}"
            )
            btn_no.clicked.connect(dlg.reject)
            btn_yes.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_no)
            btn_row.addWidget(btn_yes)
            lay.addLayout(btn_row)

            if dlg.exec() != QDialog.DialogCode.Accepted:
                self._mode_combo.blockSignals(True)
                self._mode_combo.setCurrentText(prev)
                self._mode_combo.blockSignals(False)
            else:
                self._confirmed_mode = new_mode
        except Exception:
            pass

    def _on_save(self) -> None:
        try:
            self._do_save()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def _do_save(self) -> None:
        try:
            from ..config import load_config as _lc
            _prev_theme = _lc().general.theme
        except Exception:
            _prev_theme = None

        from pathlib import Path as _P
        from ..config import (
            MittenConfig, GeneralConfig, RecorderConfig,
            TriggerConfig, WatermarkConfig, GameDetectionConfig,
            NotificationsConfig, DiscordConfig, _validate,
        )
        from .config_io import save_config

        btn_label_text = self._trigger_btn_label.text()
        btn_name = btn_label_text.split("  (")[0].strip() if "  (" in btn_label_text else "BTN_EXTRA"

        audio_data = self._audio_combo.currentData()
        if audio_data is not None:
            audio_val = audio_data  # "default", "", or specific device
        else:
            # User typed a custom device name
            typed = self._audio_combo.currentText().strip()
            if typed in ("System default", "default"):
                audio_val = "default"
            elif typed in ("(no audio)", ""):
                audio_val = ""
            else:
                audio_val = typed

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
                theme=self._theme_combo.currentText(),
                developer_mode=self._dev_mode_cb.isChecked(),
            ),
            recorder=RecorderConfig(
                container=self._container_combo.currentText(),
                quality=self._quality_combo.currentText(),
                capture_codec=self._cap_codec_combo.currentText(),
                output_codec=self._out_codec_combo.currentText(),
                watermark_cq=self._cq_spin.value(),
                audio_device=audio_val,
                auto_compress=self._auto_compress.isChecked(),
                compression_target_mb=self._target_spin.value(),
            ),
            trigger=TriggerConfig(
                button=btn_name,
                cooldown=self._cooldown_spin.value(),
            ),
            watermark=WatermarkConfig(
                enabled=self._wm_enabled.isChecked(),
                text=self._wm_text.text(),
                subtext=self._wm_subtext.text(),
                font_family=self._wm_fontfamily.currentText(),
                fontsize=self._wm_fontsize.value(),
                fontcolor=self._wm_fontcolor.text(),
                position=self._wm_position.currentText(),
                padding=self._wm_padding.value(),
                intro_name=self._wm_intro_name.text().strip(),
                anim_enabled=self._wm_anim_enabled.isChecked(),
                anim_style=self._wm_anim_style.currentText(),
            ),
            game_detection=GameDetectionConfig(
                enabled=True,
                poll_interval=self._gd_poll.value(),
                auto_switch=True,
                custom_processes=tuple(procs),
                custom_window_titles=tuple(titles),
            ),
            notifications=NotificationsConfig(
                enabled=self._notif_enabled.isChecked(),
                on_start=self._notif_start.isChecked(),
                on_save=self._notif_save.isChecked(),
                on_error=self._notif_error.isChecked(),
            ),
            discord=DiscordConfig(
                enabled=self._dc_enabled.isChecked(),
                show_ascii=self._dc_show_ascii.isChecked(),
                animated_ascii=self._dc_animated_ascii.isChecked(),
                show_game_name=self._dc_show_game_name.isChecked(),
                show_mode_label=self._dc_show_mode_label.isChecked(),
                show_name=self._dc_show_name.isChecked(),
            ),
        )

        _validate(cfg)
        save_config(cfg)
        self.settings_saved.emit()

        if _prev_theme is not None and cfg.general.theme != _prev_theme:
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
            dlg = QDialog(self)
            dlg.setWindowTitle("Restart required")
            dlg.setFixedWidth(340)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(20, 20, 20, 20)
            lay.setSpacing(12)
            lbl = QLabel(
                f"theme changed to <b>{cfg.general.theme}</b>.<br>"
                "restart now to apply it properly?"
            )
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
            lay.addWidget(lbl)
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            btn_no = QPushButton("Later")
            btn_no.setProperty("class", "secondary")
            btn_yes = QPushButton("Restart now")
            btn_yes.setStyleSheet(
                f"QPushButton {{ background-color: {C.LAVENDER}; color: {C.BG};"
                f" border: none; border-radius: 6px; padding: 6px 16px; font-weight: 600; }}"
                f"QPushButton:hover {{ background-color: {_hex_rgba(C.LAVENDER, 0.8)}; }}"
            )
            btn_no.clicked.connect(dlg.reject)
            btn_yes.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_no)
            btn_row.addWidget(btn_yes)
            lay.addLayout(btn_row)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._restart_gui()
                return

        try:
            from ..daemon_utils import get_daemon_pid, send_reload_signal
            pid = get_daemon_pid()
            if pid:
                send_reload_signal(pid)
        except Exception:
            pass

        self._confirmed_mode = cfg.general.mode
        self._save_status.setText("✓  saved")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self._save_status.setText(""))
