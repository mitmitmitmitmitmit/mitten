# mitten

a clipping tool that doesn't suck.

keeps a rolling replay buffer on your gpu. press a button, the last N seconds are saved. no cloud, no account, nothing phoning home. that's it.

**linux only for now.** windows port is in progress. watch the repo if you're on windows.

---

## why

medal is windows-only. idles at 20%+ gpu on a 3090. defaults to cpu encoding. uploads your clips publicly by default. adds itself to startup without asking. one user called it malware for reinstalling itself after uninstall. there's a black screen recording bug that's been open for years.

mitten uses `gpu-screen-recorder` which hits nvenc/vaapi directly. idles under 300mb ram for a 30s 1080p60 buffer. your gpu doesn't notice it's running.

if you're on linux, medal doesn't exist. this does.

---

## what it does

- **replay buffer**: last N seconds, always rolling. press the button, it saves
- **game detection**: detects a game launch, starts capture, stops when you close it
- **session recording**: triple-click to start, triple-click to stop and save. same button, different gesture
- **discord compression**: hits the 10MB free limit. two-pass ffmpeg locally, 90+ vmaf at sizes where online tools fall apart. your clip never leaves your machine
- **vocal trigger** *(coming)*: auto-clips laughs, jumpscares, hype moments. no button needed
- **watermark**: burned in on save. fully customizable (text, size, position, opacity). one tiny "mitten" credit stays. that's how a solo project asks for nothing else. MIT license, fork if you must
- **auto-update**: checks github on startup, backs up first, rolls back if anything breaks
- **native gui**: PyQt6 tray app. clip browser, trim, settings, stats. not electron

---

## local-first, actually

zero telemetry. no account. no uploads. no "share with the community" garbage.

the only server mitten has ever contacted is github, and only to check for updates. that can be turned off.

your clips live on your drive. compression runs on your cpu. nothing goes anywhere.

---

## how it's built

i use this daily. features get stress tested by actually gaming with them until something breaks. every line gets read and tested on real hardware before it ships.

---

## requirements

**OS:** Arch-based (Arch, CachyOS, Manjaro, EndeavourOS). Wayland only.

| binary | package |
|--------|---------|
| `gpu-screen-recorder` | `yay -S gpu-screen-recorder` |
| `ffmpeg` / `ffplay` | `sudo pacman -S ffmpeg` |
| `notify-send` | `sudo pacman -S libnotify` |

python 3.11+

---

## install

```bash
git clone https://github.com/mitmitmitmitmitmit/mitten
cd mitten
pip install -e . --break-system-packages
mitten
```

`--break-system-packages` is required on arch. it's safe, mitten's deps don't conflict with system packages.

first run launches the setup wizard automatically: installs missing deps, adds you to the `input` group, writes a default config, sets up the systemd service. you don't need to touch a terminal again after that.

---

## usage

```bash
mitten    # opens GUI, runs setup wizard on first launch
```

the daemon runs as a systemd user service. the GUI handles start/stop. if you need to poke it directly:

```bash
systemctl --user start mitten.service
systemctl --user stop mitten.service
systemctl --user status mitten.service
```

---

## config

`~/.config/mitten/config.toml`. the wizard writes sensible defaults, the settings page in the GUI handles the rest.

```toml
[general]
buffer_seconds = 30
framerate = 30
save_dir = "~/Videos/mitten"
monitor = "auto"          # or "DP-1", "HDMI-1", etc.

[trigger]
button = "BTN_EXTRA"      # use the "Detect" button in settings to find yours
cooldown = 3.0

[recorder]
quality = "very_high"
output_codec = "h264"     # h264 = discord/browser compatible
```

---

## status

actively used. i clip games with it daily.

if something breaks, open an issue. if you fix it, open a pr.

---

## coming

- vocal trigger: auto-clips laughs, screams, and hype moments without pressing anything
- discord rich presence + direct clip posting
- windows port
- mitten.clips, a site. soon.

---

## license

MIT. see LICENSE.
