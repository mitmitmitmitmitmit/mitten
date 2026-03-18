"""
System tray icon: state machine, context menu, daemon communication.

Secondary to the main window — provides quick actions and minimize-to-tray.
Left-click shows/hides the main window. Middle-click triggers a save.
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QSystemTrayIcon,
)

from ..daemon_utils import get_daemon_pid, toggle_daemon, send_save_signal
from .resources import paw_icon


class MittenTray(QSystemTrayIcon):
    """Paw-print tray icon — quick access when main window is hidden."""

    IDLE      = "idle"
    RECORDING = "recording"
    GAME      = "game"
    SAVING    = "saving"

    def __init__(self, app: QApplication, main_window=None) -> None:
        super().__init__(paw_icon(self.IDLE), app)
        self._app = app
        self._main_window = main_window
        self._state = self.IDLE

        self._menu = QMenu()
        self._build_menu()
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)

        # Status poll (2s)
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(2000)

        # Save flash (one-shot 2s)
        self._save_flash_timer = QTimer()
        self._save_flash_timer.setSingleShot(True)
        self._save_flash_timer.timeout.connect(self._end_save_flash)

        self._update_tooltip()
        self._poll_status()

    # ------------------------------------------------------------------ #
    # Menu
    # ------------------------------------------------------------------ #

    def _build_menu(self) -> None:
        m = self._menu

        self._act_status = QAction("~( ^.x.^)>  idle")
        self._act_status.setEnabled(False)
        m.addAction(self._act_status)
        m.addSeparator()

        act_show = QAction("Open MITTEN")
        act_show.triggered.connect(self._show_main_window)
        m.addAction(act_show)

        m.addSeparator()

        self._act_toggle = QAction("Start Recording")
        self._act_toggle.triggered.connect(self._toggle_recording)
        m.addAction(self._act_toggle)

        self._act_save = QAction("Save Clip Now")
        self._act_save.triggered.connect(self._manual_save)
        self._act_save.setEnabled(False)
        m.addAction(self._act_save)

        m.addSeparator()

        act_quit = QAction("Quit")
        act_quit.triggered.connect(self._quit)
        m.addAction(act_quit)

    def _refresh_menu(self) -> None:
        running = self._state in (self.RECORDING, self.GAME, self.SAVING)
        self._act_toggle.setText("Stop Recording" if running else "Start Recording")
        self._act_save.setEnabled(running)

        labels = {
            self.IDLE:      "~( ^.x.^)>  idle",
            self.RECORDING: "~( ^.x.^)>  recording",
            self.GAME:      "~( ^.x.^)>  game mode active",
            self.SAVING:    "~( ^.x.^)>  saving clip...",
        }
        self._act_status.setText(labels.get(self._state, labels[self.IDLE]))

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.setIcon(paw_icon(state))
        self._update_tooltip()
        self._refresh_menu()

    def _update_tooltip(self) -> None:
        tips = {
            self.IDLE:      "~( ^.x.^)>  MITTEN — idle",
            self.RECORDING: "~( ^.x.^)>  MITTEN — recording",
            self.GAME:      "~( ^.x.^)>  MITTEN — game mode",
            self.SAVING:    "~( ^.x.^)>  MITTEN — saving clip...",
        }
        self.setToolTip(tips.get(self._state, tips[self.IDLE]))

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    def _poll_status(self) -> None:
        if self._state == self.SAVING:
            return
        pid = get_daemon_pid()
        if pid is None:
            self._set_state(self.IDLE)
        else:
            self._set_state(self.RECORDING)

    # ------------------------------------------------------------------ #
    # Click handling
    # ------------------------------------------------------------------ #

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_main_window()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._manual_save()

    def _show_main_window(self) -> None:
        if self._main_window:
            if self._main_window.isVisible():
                self._main_window.raise_()
                self._main_window.activateWindow()
            else:
                self._main_window.show()
                self._main_window.raise_()
                self._main_window.activateWindow()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _toggle_recording(self) -> None:
        pid = get_daemon_pid()
        toggle_daemon(pid)
        QTimer.singleShot(1500, self._poll_status)

    def _manual_save(self) -> None:
        pid = get_daemon_pid()
        if pid is None:
            return
        if send_save_signal(pid):
            self._set_state(self.SAVING)
            self._save_flash_timer.start(2000)

    def _end_save_flash(self) -> None:
        self._poll_status()

    def _quit(self) -> None:
        self._poll_timer.stop()
        self.hide()
        self._app.quit()
