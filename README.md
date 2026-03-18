# mitten

a Linux replay buffer recorder — like Medal, but it actually works and doesn't eat your GPU.

keeps a rolling buffer in RAM using `gpu-screen-recorder`. when you press your configured mouse button (or middle-click the tray icon), it saves the last N seconds as a watermarked clip. that's it.

---

## why

Medal is Windows-only. and even on Windows, it's using 25% of your GPU at idle on a 3090 while sitting in the background — that's CUDA cores doing software work instead of the dedicated NVENC block. the company is subscription-gated and doesn't really care about Linux.

mitten uses `gpu-screen-recorder` which hits the NVENC encoder directly. ~300MB RAM for a 30s 1080p60 HEVC buffer. your GPU doesn't notice it's running.

---

## features

- replay buffer (configurable seconds, default 30s)
- saves on mouse button press (default side button) or tray middle-click
- ffmpeg watermark burned in on save
- game detection — auto-switches to game mode via `/proc` polling
- PyQt6 tray GUI with clip browser, settings, and stats
- systemd user service with auto-update on startup
- desktop notifications

---

## requirements

**OS:** Arch-based (Arch, CachyOS, Manjaro, EndeavourOS). Wayland only.

**runtime binaries** (installed automatically on first run):

| binary | package |
|--------|---------|
| `gpu-screen-recorder` | `yay -S gpu-screen-recorder` (AUR) |
| `ffmpeg` / `ffplay` | `sudo pacman -S ffmpeg` |
| `notify-send` | `sudo pacman -S libnotify` |

**Python:** 3.11+

---

## install

```bash
git clone git@github.com:mitmitmitmitmitmit/mitten.git
cd mitten
pip install -e . --break-system-packages
mitten
```

> `--break-system-packages` is required on Arch-based distros (PEP 668). it's safe —
> mitten's dependencies (`PyQt6`, `evdev`) don't conflict with any Arch system packages.

first run with no config file triggers the terminal setup wizard automatically:
- installs missing runtime deps via pacman/yay
- adds you to the `input` group if needed
- writes default config, installs the `.desktop` entry and systemd service
- asks if you want autostart on login

---

## usage

```bash
mitten          # open the GUI (runs setup wizard on first launch)
```

the daemon is managed via systemd — the GUI start/stop button and tray icon handle this.
you don't normally need to interact with it directly, but:

```bash
systemctl --user start mitten.service    # start recording
systemctl --user stop mitten.service     # stop recording
systemctl --user status mitten.service   # check status
```

---

## auto-update

on every daemon startup (`mitten run`, which is what the systemd service runs), mitten checks
for new commits on `origin/main`. if an update is available, it opens a konsole terminal
showing the update UI and updates automatically — no user interaction needed.

a backup tarball is saved to `~/.local/share/mitten/backup/` before every update. if the
update fails, mitten automatically rolls back and restarts with the previous version.

---

## config

stored at `~/.config/mitten/config.toml`. the setup wizard writes sensible defaults.

```toml
[general]
buffer_seconds = 30
framerate = 30
save_dir = "~/Videos/mitten"
monitor = "auto"          # or "DP-1", "HDMI-1", etc.

[trigger]
button = "BTN_EXTRA"      # side mouse button — use the "Detect" button in settings
cooldown = 3.0

[recorder]
quality = "very_high"
output_codec = "h264"     # h264 = Discord/browser compatible
```

---

## Wayland note

built for Wayland. no X11. Qt audio is broken on Wayland — clip playback uses `ffplay -nodisp` subprocess instead.

---

## license

MIT
