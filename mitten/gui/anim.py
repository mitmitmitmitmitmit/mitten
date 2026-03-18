"""
Animation helpers — reusable QPropertyAnimation utilities for MITTEN GUI.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget


def fade_in(
    widget: QWidget,
    duration_ms: int = 160,
    easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
) -> QPropertyAnimation:
    """Fade widget opacity 0 → 1. Removes graphics effect on finish."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(easing)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start()
    return anim


def fade_out(
    widget: QWidget,
    duration_ms: int = 160,
    on_done: object = None,
    easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
) -> QPropertyAnimation:
    """Fade widget opacity 1 → 0. Calls on_done() when finished."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(easing)
    if on_done:
        anim.finished.connect(on_done)
    anim.start()
    return anim


def cross_fade(
    old_widget: QWidget,
    new_widget: QWidget,
    duration_ms: int = 200,
) -> tuple[QPropertyAnimation, QPropertyAnimation]:
    """Concurrent fade out old_widget, fade in new_widget."""
    out_anim = fade_out(old_widget, duration_ms)
    in_anim = fade_in(new_widget, duration_ms)
    return out_anim, in_anim


def slide_fade_in(
    widget: QWidget,
    direction: str = "left",
    distance: int = 20,
    duration_ms: int = 200,
    easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
) -> QPropertyAnimation:
    """Combined slide + fade for page transitions.

    direction='left'  → widget slides in from the right
    direction='right' → widget slides in from the left
    Returns the opacity animation (slide runs concurrently).
    """
    # Opacity animation
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    fade_anim = QPropertyAnimation(effect, b"opacity", widget)
    fade_anim.setDuration(duration_ms)
    fade_anim.setStartValue(0.0)
    fade_anim.setEndValue(1.0)
    fade_anim.setEasingCurve(easing)
    fade_anim.finished.connect(lambda: widget.setGraphicsEffect(None))

    # Position offset
    orig = widget.pos()
    offset = QPoint(distance if direction == "left" else -distance, 0)
    widget.move(orig + offset)

    slide_anim = QPropertyAnimation(widget, b"pos", widget)
    slide_anim.setDuration(duration_ms)
    slide_anim.setStartValue(orig + offset)
    slide_anim.setEndValue(orig)
    slide_anim.setEasingCurve(easing)

    fade_anim.start()
    slide_anim.start()
    # Keep slide_anim alive via parent
    widget._slide_anim = slide_anim
    return fade_anim


def staggered_fade(
    widgets: list[QWidget],
    duration_ms: int = 100,
    stagger_ms: int = 20,
    fade_in_: bool = True,
) -> None:
    """Fade a list of widgets in or out with a stagger delay between each."""
    fn = fade_in if fade_in_ else fade_out
    for i, widget in enumerate(widgets):
        QTimer.singleShot(i * stagger_ms, lambda w=widget: fn(w, duration_ms))
