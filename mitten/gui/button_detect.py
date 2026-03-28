"""
evdev-based (Linux) / pynput-based (Windows) mouse button detection — used by
Settings "Detect..." button.

ButtonDetectWorker  — QThread that listens for the first button press
ButtonDetectDialog  — Modal dialog wrapping the worker
"""
from __future__ import annotations

import logging
import sys
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

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Worker thread
# ------------------------------------------------------------------ #

class ButtonDetectWorker(QThread):
    detected = pyqtSignal(int, str)   # (code, name)
    error    = pyqtSignal(str)
    status   = pyqtSignal(str)        # live status updates for the dialog label

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._abort = False

    def cancel(self) -> None:
        self._abort = True

    def run(self) -> None:
        log.debug("ButtonDetectWorker starting")
        if sys.platform == "win32":
            self._run_pynput()
        else:
            self._run_evdev()

    def _run_pynput(self) -> None:
        """Windows path: use pynput to detect the first mouse button press."""
        import threading as _threading

        # Reversed mapping: pynput Button → evdev-style integer code
        _PYNPUT_TO_CODE: dict = {}
        try:
            from pynput.mouse import Button, Listener
        except ImportError:
            msg = "pynput not installed — run: pip install pynput"
            log.error(msg)
            self.error.emit(msg)
            return

        _PYNPUT_TO_CODE = {
            Button.left:   272,
            Button.right:  273,
            Button.middle: 274,
            Button.x1:     275,
            Button.x2:     276,
        }

        try:
            from ..config import BUTTON_NAMES
            reverse = {v: k for k, v in BUTTON_NAMES.items()}
        except Exception as e:
            log.warning("Could not load BUTTON_NAMES: %s", e)
            reverse = {}

        detected_event = _threading.Event()

        def _on_click(x, y, button, pressed):
            if self._abort or not pressed:
                return
            code = _PYNPUT_TO_CODE.get(button)
            if code is None:
                return
            name = reverse.get(code, f"BTN_{code}")
            log.info("Detected button (pynput): code=%d name=%s", code, name)
            self.detected.emit(code, name)
            detected_event.set()
            return False  # stop listener

        self.status.emit("Listening for mouse button press…")
        listener = Listener(on_click=_on_click)
        listener.start()

        deadline = time.monotonic() + 30.0
        while not self._abort and not detected_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.1)

        listener.stop()

        if not self._abort and not detected_event.is_set():
            log.warning("Button detect timed out after 30s")
            self.error.emit("Timed out — no button press detected in 30s.")

        log.debug("ButtonDetectWorker (pynput) done")

    def _run_evdev(self) -> None:
        """Linux path: use evdev + select to detect the first button press."""
        import select

        try:
            from evdev import InputDevice, ecodes, list_devices
        except ImportError:
            msg = "python-evdev not installed — run: sudo pacman -S python-evdev"
            log.error(msg)
            self.error.emit(msg)
            return

        # Open all devices that support key/button events
        devices: list[InputDevice] = []
        all_paths = []
        try:
            all_paths = list(list_devices())
        except Exception as e:
            log.error("Failed to list input devices: %s", e)
            self.error.emit(f"Could not list input devices: {e}")
            return

        log.debug("Found %d input device paths", len(all_paths))
        for path in all_paths:
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    devices.append(dev)
                    log.debug("Opened device: %s (%s)", path, dev.name)
                else:
                    dev.close()
            except PermissionError:
                log.warning("Permission denied on %s — not in 'input' group?", path)
            except Exception as e:
                log.debug("Skipping %s: %s", path, e)

        if not devices:
            msg = (
                "No accessible input devices found.\n"
                "Make sure you are in the 'input' group:\n"
                "  sudo usermod -aG input $USER\n"
                "Then log out and back in."
            )
            log.error("No usable input devices found")
            self.error.emit(msg)
            return

        self.status.emit(f"Listening on {len(devices)} device(s)…")
        log.debug("Listening on %d devices", len(devices))

        fd_map = {d.fd: d for d in devices}

        # Reverse-lookup table: evdev code → BTN_* config name
        try:
            from ..config import BUTTON_NAMES
            reverse = {v: k for k, v in BUTTON_NAMES.items()}
        except Exception as e:
            log.warning("Could not load BUTTON_NAMES: %s", e)
            reverse = {}

        try:
            deadline = time.monotonic() + 30.0
            while not self._abort and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    r, _, _ = select.select(fd_map.keys(), [], [], min(remaining, 0.2))
                except OSError as e:
                    log.error("select() failed: %s", e)
                    self.error.emit(f"Input select error: {e}")
                    return
                except Exception as e:
                    log.error("Unexpected select error: %s", e)
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
                                log.info("Detected button: code=%d name=%s device=%s", code, name, dev.name)
                                self.detected.emit(code, name)
                                return
                    except OSError as e:
                        log.warning("Read error on %s: %s — removing from poll", dev.path, e)
                        fd_map.pop(fd, None)
                        try:
                            dev.close()
                        except Exception:
                            pass
                    except Exception as e:
                        log.error("Unexpected read error on %s: %s", dev.path, e)
            if not self._abort:
                log.warning("Button detect timed out after 30s")
                self.error.emit("Timed out — no button press detected in 30s.")
        finally:
            log.debug("ButtonDetectWorker cleaning up %d devices", len(devices))
            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass
            log.debug("ButtonDetectWorker done")


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
        self._worker.status.connect(self._status.setText)
        self._worker.start()
        log.debug("ButtonDetectDialog opened, worker started")

    def _on_detected(self, code: int, name: str) -> None:
        self._result = (code, name)
        # Wait for the worker thread to fully exit before the dialog is destroyed.
        # Without this, Python GC destroys the dialog (and its child QThread) while
        # the thread is still in its finally-block closing devices — undefined behaviour
        # that crashes the entire process.
        self._worker.wait(2000)
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._cancel_btn.setText("Close")

    def _on_cancel(self) -> None:
        self._worker.cancel()
        self._worker.wait(2000)
        self.reject()

    def result(self) -> tuple[int, str] | None:  # type: ignore[override]
        return self._result

    def closeEvent(self, event) -> None:
        self._worker.cancel()
        self._worker.wait(2000)
        super().closeEvent(event)
