"""
Programmatic icon generation and color palette for MITTEN's GUI.

Paw-print tray icons are drawn via QPainter — no external image files needed.
The palette is a warm Catppuccin-inspired dark theme that feels cozy and playful.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)


# ------------------------------------------------------------------ #
# Color palette
# ------------------------------------------------------------------ #

class C:
    """MITTEN color constants."""
    BG          = "#1a1826"
    SURFACE     = "#252336"
    OVERLAY     = "#313244"
    BORDER      = "#3a3650"
    TEXT        = "#e8e0f0"
    SUBTEXT     = "#9890a8"
    LAVENDER    = "#c4a7e7"
    GREEN       = "#a6e3a1"
    ORANGE      = "#fab387"
    BLUE        = "#89b4fa"
    GRAY        = "#585b70"
    PINK        = "#f38ba8"
    DARK_ACCENT = "#b497d7"


# Cat emoticons — some KDE fonts render ~ as ¬; force a known-good family.
# CAT is the primary brand logo. CATS is for variety in non-branding contexts.
CAT = "~( ^.x.^)>"
CAT_FONT = "font-family: 'Noto Sans', 'DejaVu Sans', 'Liberation Sans', sans-serif;"

CATS = [
    "~( ^.x.^)>",
    "=^._.^= \u222b",
    "/\u1d20. \u02d5.\u1d20\\",
    "(\u00b4\u2022\u03c9\u2022`)",
    "\u0f3c=\u00b4\u03c9`=\u0f3d",
]


# State → paw color mapping
STATE_COLORS: dict[str, str] = {
    "idle":      C.GRAY,
    "recording": C.GREEN,
    "game":      C.ORANGE,
    "saving":    C.BLUE,
}


# ------------------------------------------------------------------ #
# Paw-print icon generator
# ------------------------------------------------------------------ #

def make_paw_icon(color: str | QColor, size: int = 64) -> QIcon:
    """Draw a paw-print icon filled with `color`. Returns a QIcon."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    c = QColor(color) if isinstance(color, str) else color

    # Subtle radial glow behind the paw for depth
    glow = QRadialGradient(QPointF(size / 2, size * 0.58), size * 0.45)
    glow.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 40))
    glow.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setBrush(QBrush(glow))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(0, size * 0.15, size, size * 0.85))

    # Draw the paw
    _draw_paw(p, c, size)

    p.end()
    return QIcon(px)


def _draw_paw(p: QPainter, color: QColor, s: int) -> None:
    """Draw a stylized paw on painter `p` within an s×s canvas."""
    hi = QColor(color)
    hi.setAlpha(255)
    lo = QColor(color)
    lo = lo.darker(130)

    p.setPen(Qt.PenStyle.NoPen)

    # Main pad — large rounded shape, slightly below center
    cx, cy = s * 0.50, s * 0.62
    rx, ry = s * 0.22, s * 0.18
    _oval_with_highlight(p, cx, cy, rx, ry, hi, lo)

    # Four toe pads — arc arrangement above the main pad
    toes = [
        (0.26, 0.32, 0.105),
        (0.40, 0.22, 0.100),
        (0.60, 0.22, 0.100),
        (0.74, 0.32, 0.105),
    ]
    for (tx, ty, tr) in toes:
        _oval_with_highlight(p, s * tx, s * ty, s * tr, s * tr, hi, lo)


def _oval_with_highlight(
    p: QPainter,
    cx: float, cy: float,
    rx: float, ry: float,
    hi: QColor, lo: QColor,
) -> None:
    grad = QRadialGradient(QPointF(cx, cy - ry * 0.3), max(rx, ry) * 1.4)
    grad.setColorAt(0.0, hi)
    grad.setColorAt(1.0, lo)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QRectF(cx - rx, cy - ry, rx * 2, ry * 2))


# ------------------------------------------------------------------ #
# Pre-built icon cache
# ------------------------------------------------------------------ #

_icon_cache: dict[str, QIcon] = {}


def paw_icon(state: str) -> QIcon:
    """Return the cached paw QIcon for a given state name."""
    if state not in _icon_cache:
        color = STATE_COLORS.get(state, C.GRAY)
        _icon_cache[state] = make_paw_icon(color)
    return _icon_cache[state]


def clear_icon_cache() -> None:
    _icon_cache.clear()


# ------------------------------------------------------------------ #
# Application-wide stylesheet
# ------------------------------------------------------------------ #


def _hex_rgba(hex_color: str, alpha: float) -> str:
    """Compute rgba() string from any hex color string + alpha."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _accent_rgba(alpha: float) -> str:
    """Compute rgba() string from current C.LAVENDER + alpha."""
    return _hex_rgba(C.LAVENDER, alpha)


def _accent_hover() -> str:
    """Slightly lighter version of C.LAVENDER for hover states."""
    h = C.LAVENDER.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{min(255,r+28):02x}{min(255,g+28):02x}{min(255,b+28):02x}"


def _pink_hover() -> str:
    h = C.PINK.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{min(255,r+20):02x}{min(255,g+20):02x}{min(255,b+20):02x}"


def make_stylesheet() -> str:
    """Build the application stylesheet from current C palette values.
    Call AFTER apply_theme() so all C attributes are set correctly."""
    ah      = _accent_hover()
    ph      = _pink_hover()
    a_06    = _accent_rgba(0.06)
    a_08    = _accent_rgba(0.08)
    a_12    = _accent_rgba(0.12)
    a_14    = _accent_rgba(0.14)
    a_15    = _accent_rgba(0.15)
    a_30    = _accent_rgba(0.30)
    a_60    = _accent_rgba(0.60)

    return f"""
/* ── Base ── */
QDialog, QWidget#centralWidget {{
    background-color: {C.BG};
    color: {C.TEXT};
}}

QLabel {{
    color: {C.TEXT};
    background: transparent;
}}
QLabel[class="section"] {{
    color: {C.SUBTEXT};
    font-size: 11px;
    font-weight: 600;
    padding-top: 4px;
}}
QLabel[class="stat-value"] {{
    font-size: 20px;
    font-weight: bold;
    color: {C.TEXT};
}}
QLabel[class="stat-label"] {{
    font-size: 11px;
    color: {C.SUBTEXT};
}}
QLabel[class="cat-header"] {{
    font-size: 14px;
    font-weight: bold;
    color: {C.LAVENDER};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {C.LAVENDER};
    color: {C.BG};
    border: none;
    border-radius: 6px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: {ah};
}}
QPushButton:pressed {{
    background-color: {C.DARK_ACCENT};
}}
QPushButton:disabled {{
    background-color: {C.OVERLAY};
    color: {C.GRAY};
}}
QPushButton[class="secondary"] {{
    background-color: {C.SURFACE};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
}}
QPushButton[class="secondary"]:hover {{
    border-color: {C.LAVENDER};
    background-color: {a_08};
    color: {C.LAVENDER};
}}
QPushButton[class="secondary"]:pressed {{
    background-color: {a_14};
}}
QPushButton[class="danger"] {{
    background-color: {C.PINK};
    color: {C.BG};
    border: none;
}}
QPushButton[class="danger"]:hover {{
    background-color: {ph};
}}

/* ── Inputs ── */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {C.SURFACE};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    border-radius: 4px;
    padding: 5px 10px;
    font-size: 12px;
    min-height: 20px;
    selection-background-color: {C.LAVENDER};
    selection-color: {C.BG};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {a_60};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {C.LAVENDER};
    background-color: {C.OVERLAY};
}}

/* ComboBox dropdown */
QComboBox {{
    padding-right: 24px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {C.SURFACE};
    color: {C.TEXT};
    selection-background-color: {a_15};
    selection-color: {C.LAVENDER};
    border: 1px solid {C.BORDER};
    border-radius: 4px;
    padding: 2px;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 3px;
}}

/* SpinBox buttons */
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}

/* ── Slider ── */
QSlider::groove:horizontal {{
    background-color: {C.OVERLAY};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background-color: {C.LAVENDER};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background-color: {ah};
}}
QSlider::sub-page:horizontal {{
    background-color: {C.LAVENDER};
    border-radius: 2px;
}}

/* ── Checkbox ── */
QCheckBox {{
    color: {C.TEXT};
    spacing: 8px;
    font-size: 12px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {C.BORDER};
    border-radius: 4px;
    background-color: {C.SURFACE};
}}
QCheckBox::indicator:hover {{
    border-color: {C.LAVENDER};
}}
QCheckBox::indicator:checked {{
    background-color: {C.LAVENDER};
    border-color: {C.LAVENDER};
}}

/* ── Table ── */
QTableWidget {{
    background-color: transparent;
    alternate-background-color: {C.SURFACE};
    color: {C.TEXT};
    gridline-color: transparent;
    border: 1px solid {C.BORDER};
    border-radius: 6px;
    selection-background-color: {a_12};
    selection-color: {C.TEXT};
    font-size: 12px;
}}
QTableWidget::item {{
    padding: 5px 8px;
    border: none;
}}
QTableWidget::item:hover {{
    background-color: {a_06};
}}
QTableWidget::item:selected {{
    background-color: {a_14};
    color: {C.LAVENDER};
}}
QHeaderView::section {{
    background-color: {C.SURFACE};
    color: {C.SUBTEXT};
    border: none;
    border-bottom: 1px solid {C.BORDER};
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}

/* ── Progress bar ── */
QProgressBar {{
    background-color: {C.OVERLAY};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    font-size: 0px;
}}
QProgressBar::chunk {{
    background-color: {C.LAVENDER};
    border-radius: 3px;
}}

/* ── Scrollbar ── */
QScrollBar:vertical {{
    background-color: transparent;
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {C.BORDER};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {C.GRAY};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background-color: transparent;
    height: 6px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {C.BORDER};
    border-radius: 3px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {C.GRAY};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ── Tooltips ── */
QToolTip {{
    background-color: {C.SURFACE};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 12px;
    opacity: 220;
}}

/* ── Group box ── */
QGroupBox {{
    background-color: {C.SURFACE};
    border: 1px solid {C.BORDER};
    border-radius: 8px;
    margin-top: 16px;
    padding: 12px;
    padding-top: 24px;
    font-size: 12px;
}}
QGroupBox::title {{
    color: {C.SUBTEXT};
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    font-weight: 600;
    font-size: 11px;
}}

/* ── List widget ── */
QListWidget {{
    background-color: {C.SURFACE};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    border-radius: 6px;
    padding: 2px;
    font-size: 12px;
    outline: none;
}}
QListWidget::item {{
    padding: 5px 8px;
    border-radius: 3px;
}}
QListWidget::item:hover {{
    background-color: {a_06};
}}
QListWidget::item:selected {{
    background-color: {a_14};
    color: {C.LAVENDER};
}}

/* ── Menu (context menus) ── */
QMenu {{
    background-color: {C.SURFACE};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    border-radius: 6px;
    padding: 4px 0;
}}
QMenu::item {{
    padding: 7px 28px 7px 14px;
    border-radius: 3px;
    margin: 1px 4px;
}}
QMenu::item:selected {{
    background-color: {a_12};
    color: {C.LAVENDER};
}}
QMenu::separator {{
    height: 1px;
    background-color: {C.BORDER};
    margin: 4px 10px;
}}
QMenu::item:disabled {{
    color: {C.GRAY};
}}

/* ── Splitter handle ── */
QSplitter::handle {{
    background-color: {C.BORDER};
}}
QSplitter::handle:hover {{
    background-color: {a_30};
}}
"""


# Keep STYLESHEET as a lazy alias for backwards compat (first import before theme)
STYLESHEET = make_stylesheet()
