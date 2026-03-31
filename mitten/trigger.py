"""
evdev mouse button listener (Linux) / pynput listener (Windows).
Runs in a daemon thread, fires a callback when the configured button is pressed.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

from .config import MittenConfig, button_name_to_code

log = logging.getLogger(__name__)

if sys.platform != "win32":
    import select
    # Module-level import so the absence of evdev is detected once at startup,
    # not silently swallowed on every call.
    try:
        from evdev import InputDevice, ecodes, list_devices
        _HAS_EVDEV = True
    except ImportError:
        _HAS_EVDEV = False
else:
    _HAS_EVDEV = False

# pynput button code mapping (evdev codes → pynput Button enum)
# BTN_LEFT=272, BTN_RIGHT=273, BTN_MIDDLE=274, BTN_SIDE=275, BTN_EXTRA=276
_PYNPUT_BUTTON_MAP: dict[int, object] = {}

def _init_pynput_map() -> None:
    global _PYNPUT_BUTTON_MAP
    try:
        from pynput.mouse import Button
        _PYNPUT_BUTTON_MAP = {
            272: Button.left,
            273: Button.right,
            274: Button.middle,
            275: Button.x1,
            276: Button.x2,
        }
    except ImportError:
        _PYNPUT_BUTTON_MAP = {}

if sys.platform == "win32":
    _init_pynput_map()


class TriggerListener:
    """
    Opens all mouse-capable evdev devices and listens for the configured
    button press. Calls `on_trigger()` when detected, subject to cooldown.
    """

    def __init__(
        self,
        config: MittenConfig,
        on_trigger: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
        on_triple_trigger: Callable[[], None] | None = None,
    ) -> None:
        self._button_code: int = button_name_to_code(config.trigger.button)
        self._cooldown: float = config.trigger.cooldown
        self._trigger_type: str = config.trigger.trigger_type
        self._trigger_key: str = config.trigger.trigger_key
        self._on_trigger = on_trigger
        self._on_error = on_error
        self._on_triple_trigger = on_triple_trigger
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._last_trigger: float = 0.0
        self._click_times: list[float] = []  # for triple-click detection
        self._pending_timer: threading.Timer | None = None
        self._pynput_listener = None  # Windows only

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> bool:
        """
        Start the listener thread.
        Returns True if at least one mouse device was found (Linux) or pynput
        is available (Windows), False otherwise.
        """
        if sys.platform == "win32":
            if self._trigger_type == "keyboard":
                return self._start_pynput_keyboard()
            return self._start_pynput()

        if not _HAS_EVDEV:
            msg = "python-evdev not installed. Run: pip install evdev"
            log.error(msg)
            if self._on_error:
                self._on_error(msg)
            return False

        devices = self._open_devices()
        if not devices:
            log.warning(
                "No mouse evdev devices found. "
                "Is the user in the 'input' group? "
                "Falling back to SIGUSR1 / tray icon save."
            )
            return False

        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._listen_loop,
            args=(devices,),
            name="trigger-listener",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "Trigger listener started on %d device(s), button code=%d",
            len(devices),
            self._button_code,
        )
        return True

    def stop(self) -> None:
        self._shutdown.set()
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None
        if sys.platform == "win32" and self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _open_devices(self) -> list:
        devices = []
        for fn in list_devices():
            try:
                dev = InputDevice(fn)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    devices.append(dev)
                    log.debug("Monitoring input device: %s (%s)", fn, dev.name)
            except (PermissionError, OSError) as e:
                log.debug("Cannot open %s: %s", fn, e)
        return devices

    def _listen_loop(self, devices: list) -> None:
        fd_map = {d.fd: d for d in devices}

        try:
            while not self._shutdown.is_set():
                try:
                    r, _, _ = select.select(fd_map.keys(), [], [], 1.0)
                except (ValueError, OSError):
                    fd_map = {fd: d for fd, d in fd_map.items() if _fd_ok(fd)}
                    if not fd_map:
                        msg = "All input devices closed — trigger disabled"
                        log.error(msg)
                        if self._on_error:
                            self._on_error(msg)
                        return
                    continue

                for fd in r:
                    dev = fd_map.get(fd)
                    if dev is None:
                        continue
                    try:
                        for event in dev.read():
                            if (
                                event.type == ecodes.EV_KEY
                                and event.code == self._button_code
                                and event.value == 1  # key-down
                            ):
                                self._fire()
                    except OSError as e:
                        log.warning("Device read error: %s", e)
                        fd_map.pop(fd, None)
        finally:
            for d in devices:
                try:
                    d.close()
                except Exception:
                    pass

    def _fire(self) -> None:
        now = time.monotonic()

        # Record every press for triple-click detection (bypass cooldown for counting)
        self._click_times.append(now)
        self._click_times = [t for t in self._click_times if now - t <= 0.6]

        # Triple-click: 3 presses within 600ms — cancel any pending single-click save
        if len(self._click_times) >= 3 and self._on_triple_trigger:
            log.info("Triple-click detected — toggling session recording")
            self._click_times.clear()
            self._last_trigger = now  # reset cooldown
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None
            try:
                self._on_triple_trigger()
            except Exception as e:
                log.error("on_triple_trigger callback raised: %s", e)
            return

        # Normal single-click with cooldown — debounced 400ms so rapid re-clicks
        # don't double-fire before the triple-click window closes
        if now - self._last_trigger < self._cooldown:
            log.debug("Trigger ignored (cooldown)")
            return
        self._last_trigger = now
        if self._pending_timer is not None:
            self._pending_timer.cancel()
        t = threading.Timer(0.4, self._dispatch_single)
        t.daemon = True
        self._pending_timer = t
        t.start()

    def _dispatch_single(self) -> None:
        self._pending_timer = None
        log.info("Trigger fired!")
        try:
            self._on_trigger()
        except Exception as e:
            log.error("on_trigger callback raised: %s", e)

    # ------------------------------------------------------------------ #
    # Windows-specific: pynput
    # ------------------------------------------------------------------ #

    def _pynput_button_for_code(self, code: int):
        """Map an evdev-style integer button code to a pynput Button enum value."""
        return _PYNPUT_BUTTON_MAP.get(code)

    def _start_pynput(self) -> bool:
        try:
            from pynput.mouse import Listener
        except ImportError:
            msg = "pynput not installed. Run: pip install pynput"
            log.error(msg)
            if self._on_error:
                self._on_error(msg)
            return False

        target_button = self._pynput_button_for_code(self._button_code)
        if target_button is None:
            msg = f"No pynput mapping for button code {self._button_code}"
            log.error(msg)
            if self._on_error:
                self._on_error(msg)
            return False

        def _on_click(x, y, button, pressed):
            if pressed and button == target_button:
                self._fire()

        self._shutdown.clear()
        self._pynput_listener = Listener(on_click=_on_click)
        self._pynput_listener.start()
        log.info(
            "Trigger listener started (pynput), button code=%d",
            self._button_code,
        )
        return True

    def _on_pynput_click(self, x, y, button, pressed) -> None:
        """Called by pynput on every mouse click event."""
        target_button = self._pynput_button_for_code(self._button_code)
        if pressed and button == target_button:
            self._fire()

    def _start_pynput_keyboard(self) -> bool:
        """Windows: listen for a configured keyboard key via pynput GlobalHotKeys."""
        try:
            from pynput import keyboard
        except ImportError:
            msg = "pynput not installed. Run: pip install pynput"
            log.error(msg)
            if self._on_error:
                self._on_error(msg)
            return False

        key_str = self._trigger_key.strip()
        if not key_str:
            msg = "No keyboard key configured. Set a key in Settings → Trigger."
            log.error(msg)
            if self._on_error:
                self._on_error(msg)
            return False

        self._shutdown.clear()

        def _on_activate():
            self._fire()

        try:
            self._pynput_listener = keyboard.GlobalHotKeys({key_str: _on_activate})
            self._pynput_listener.start()
        except Exception as e:
            msg = f"Failed to start keyboard listener for key '{key_str}': {e}"
            log.error(msg)
            if self._on_error:
                self._on_error(msg)
            return False

        log.info("Trigger listener started (keyboard), key=%s", key_str)
        return True


def _fd_ok(fd: int) -> bool:
    if sys.platform == "win32":
        return False
    try:
        select.select([fd], [], [], 0)
        return True
    except (ValueError, OSError):
        return False
