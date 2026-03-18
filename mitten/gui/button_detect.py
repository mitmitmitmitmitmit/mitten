"""
evdev-based mouse button detection — used by Settings "Detect..." button.

ButtonDetectWorker  — QThread that listens for the first button press
ButtonDetectDialog  — Modal dialog wrapping the worker
"""
from __future__ import annotations

import select
import time

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .resources import C, CAT_FONT


# ------------------------------------------------------------------ #
# Worker thread
# ------------------------------------------------------------------ #

class ButtonDetectWorker(QThread):
    detected = pyqtSignal(int, str)   # (code, name)
    error    = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._abort = False

    def cancel(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            from evdev import InputDevice, ecodes, list_devices
        except ImportError:
            self.error.emit("python-evdev not installed.\nRun: sudo pacman -S python-evdev")
            return

        # Open all devices that support key/button events
        devices: list[InputDevice] = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    devices.append(dev)
            except Exception:
                pass

        if not devices:
            self.error.emit(
                "No input devices found.\n"
                "Make sure you are in the 'input' group:\n"
                "  sudo usermod -aG input $USER"
            )
            return

        fd_map = {d.fd: d for d in devices}

        # Reverse-lookup table: code → BTN_* name
        try:
            from ..config import BUTTON_NAMES
            reverse = {v: k for k, v in BUTTON_NAMES.items()}
        except Exception:
            reverse = {}

        try:
            deadline = time.monotonic() + 30.0
            while not self._abort and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    r, _, _ = select.select(fd_map.keys(), [], [], min(remaining, 0.2))
                except Exception:
                    break
                for fd in r:
                    if self._abort:
                        return
                    dev = fd_map[fd]
                    try:
                        for event in dev.read():
                            if event.type == ecodes.EV_KEY and event.value == 1:
                                code = event.code
                                name = reverse.get(code, f"BTN_{code}")
                                self.detected.emit(code, name)
                                return
                    except Exception:
                        pass
        finally:
            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass


# ------------------------------------------------------------------ #
# Dialog
# ------------------------------------------------------------------ #

class ButtonDetectDialog(QDialog):
    """
    Modal dialog: "Press a mouse button…" with cancel.
    After accepted, .result() returns (code, name).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detect Button")
        self.setModal(True)
        self.setMinimumWidth(320)
        self.setStyleSheet(
            f"QDialog {{ background-color: {C.BG}; color: {C.TEXT}; }}"
        )
        self._result: tuple[int, str] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        cat = QLabel("~( ^.x.^)>")
        cat.setStyleSheet(
            f"color: {C.LAVENDER}; font-size: 20px; font-weight: 700; {CAT_FONT}"
        )
        layout.addWidget(cat)

        prompt = QLabel("Press any mouse button or key\nyou want to use as your trigger.")
        prompt.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
        layout.addWidget(prompt)

        self._status = QLabel("Listening…")
        self._status.setStyleSheet(f"color: {C.SUBTEXT}; font-size: 12px;")
        layout.addWidget(self._status)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("class", "secondary")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btns.addStretch()
        btns.addWidget(self._cancel_btn)
        layout.addLayout(btns)

        self._worker = ButtonDetectWorker(self)
        self._worker.detected.connect(self._on_detected)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_detected(self, code: int, name: str) -> None:
        self._result = (code, name)
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._cancel_btn.setText("Close")

    def _on_cancel(self) -> None:
        self._worker.cancel()
        self._worker.wait(500)
        self.reject()

    def result(self) -> tuple[int, str] | None:  # type: ignore[override]
        return self._result

    def closeEvent(self, event) -> None:
        self._worker.cancel()
        self._worker.wait(500)
        super().closeEvent(event)
