"""
Discord Rich Presence for MITTEN.

Uses raw Unix socket IPC — no pypresence dependency, no asyncio.
Runs in a background thread; main code just calls set_state().

Works with native Discord and Vesktop (same IPC socket location).

To expand:
  - Add large_image / small_image once assets are uploaded to Discord dev portal
  - Add party_size for session recording (clips saved this session)
  - Add buttons: "Get mitten" linking to the repo/website
  - Add elapsed time per clip/session via timestamps
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

# Register at discord.com/developers/applications → New Application → copy Application ID.
_CLIENT_ID = "1484018158300823643"

# Presence states — map daemon state strings to (state, details) tuples.
# "state" is the small lower line; "details" is the upper/main line.
_PRESENCE_STATES: dict[str, tuple[str, str]] = {
    "idle":          ("idle",             "waiting for something good"),
    "recording":     ("recording",        "mitten is watching\u2026"),
    "game":          ("game mode",        "recording gameplay"),
    "saving":        ("saving clip",      "caught one!"),
    "paused":        ("paused",           "buffer paused"),
    "session":       ("session recording","recording full session"),
    "recorder_dead": ("recorder crashed", "something went wrong"),
}


def _find_ipc_socket() -> str | None:
    """Probe known socket locations for a live Discord IPC pipe."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    prefixes = [
        runtime,                                          # native Discord / Vesktop
        f"{runtime}/app/com.discordapp.Discord",          # Flatpak Discord
        f"{runtime}/app/com.discordapp.DiscordPTB",
        f"{runtime}/app/com.discordapp.DiscordCanary",
        "/tmp",
    ]
    for prefix in prefixes:
        for n in range(10):
            p = Path(f"{prefix}/discord-ipc-{n}")
            try:
                if p.exists() and p.stat().st_mode & 0xF000 == 0xC000:
                    return str(p)
            except OSError:
                continue
    return None


class DiscordPresence:
    """
    Manages a persistent Discord IPC connection and updates rich presence.

    Thread-safe. All socket work happens on a dedicated background thread.
    Caller just does:
        presence.set_state("recording")
        presence.set_state("saving")
        presence.clear()
    """

    _RECONNECT_DELAY = 15.0   # seconds between reconnect attempts
    _SEND_TIMEOUT   = 5.0     # socket send/recv timeout

    def __init__(self) -> None:
        self._lock     = threading.Lock()
        self._sock: socket.socket | None = None
        self._connected = False
        self._shutdown  = threading.Event()
        self._pending_state: str | None = None   # buffered while disconnected
        self._start_ts: int = int(time.time())   # session start timestamp

        self._thread = threading.Thread(
            target=self._run,
            name="discord-presence",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        log.debug("discord presence thread started")

    def stop(self) -> None:
        self._shutdown.set()
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def set_state(self, state: str, detail_override: str | None = None) -> None:
        """Update Discord presence to reflect the given daemon state.
        detail_override replaces the default details line (e.g. game name)."""
        with self._lock:
            self._pending_state = state
            self._detail_override = detail_override
            if self._connected:
                try:
                    self._send_presence(state, detail_override)
                except OSError:
                    self._connected = False
                    self._sock = None

    def clear(self) -> None:
        """Clear presence (e.g. when daemon shuts down)."""
        with self._lock:
            self._pending_state = None
            if self._connected and self._sock:
                try:
                    self._send_frame({"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": None}, "nonce": str(uuid.uuid4())})
                except OSError:
                    pass

    # ── Internal ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Background thread: connect, keep alive, reconnect on drop."""
        while not self._shutdown.is_set():
            if not self._connected:
                self._try_connect()
            if self._connected:
                # Flush any state that arrived while disconnected
                with self._lock:
                    pending = self._pending_state
                    override = getattr(self, "_detail_override", None)
                if pending:
                    try:
                        with self._lock:
                            self._send_presence(pending, override)
                    except OSError:
                        with self._lock:
                            self._connected = False
                            self._sock = None
                        continue
            self._shutdown.wait(self._RECONNECT_DELAY)

    def _try_connect(self) -> None:
        pipe = _find_ipc_socket()
        if not pipe:
            log.debug("discord ipc socket not found — discord not running?")
            return
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self._SEND_TIMEOUT)
            sock.connect(pipe)
            # Handshake (opcode 0)
            self._sock = sock
            handshake = {"v": 1, "client_id": _CLIENT_ID}
            data = json.dumps(handshake).encode()
            sock.sendall(struct.pack("<II", 0, len(data)) + data)
            _opcode, resp = self._recv_frame(sock)
            if resp.get("evt") != "READY":
                log.warning("discord handshake unexpected response: %s", resp.get("evt"))
                sock.close()
                self._sock = None
                return
            with self._lock:
                self._connected = True
            log.info("discord presence connected (user: %s)", resp.get("data", {}).get("user", {}).get("username", "?"))
        except OSError as e:
            log.debug("discord presence connect failed: %s", e)
            with self._lock:
                self._sock = None

    def _send_presence(self, state: str, detail_override: str | None = None) -> None:
        """Build and send a SET_ACTIVITY frame. Must be called with lock held."""
        entry = _PRESENCE_STATES.get(state, _PRESENCE_STATES["idle"])
        activity_state, activity_details = entry
        if detail_override:
            activity_details = detail_override

        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {
                "pid": os.getpid(),
                "activity": {
                    "state":   activity_state,
                    "details": activity_details,
                    "timestamps": {"start": self._start_ts},
                    # TODO: add large_image/small_image once assets are in the dev portal
                    # "assets": {"large_image": "mitten_logo", "large_text": "mitten"},
                    # TODO: add buttons when the website is live
                    # "buttons": [{"label": "get mitten", "url": "https://mitten.clips"}],
                },
            },
            "nonce": str(uuid.uuid4()),
        }
        self._send_frame(payload)
        if self._sock:
            self._recv_frame(self._sock)  # consume ACK

    def _send_frame(self, payload: dict) -> None:
        if not self._sock:
            return
        data = json.dumps(payload).encode()
        self._sock.sendall(struct.pack("<II", 1, len(data)) + data)  # opcode 1 = frame

    @staticmethod
    def _recv_frame(sock: socket.socket) -> tuple[int, dict]:
        header = b""
        while len(header) < 8:
            chunk = sock.recv(8 - len(header))
            if not chunk:
                raise OSError("discord ipc disconnected")
            header += chunk
        opcode, length = struct.unpack("<II", header)
        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                raise OSError("discord ipc disconnected")
            data += chunk
        return opcode, json.loads(data)
