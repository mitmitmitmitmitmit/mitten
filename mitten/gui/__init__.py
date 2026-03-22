"""MITTEN GUI — system tray application."""
from __future__ import annotations


def launch_gui(abuse_reveal: bool = False) -> None:
    """Entry point called by `mitten gui`."""
    from .app import run_app
    run_app(abuse_reveal=abuse_reveal)
