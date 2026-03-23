# mitten

a clipping tool that doesn't suck.

keeps a rolling replay buffer on your gpu. press a button, the last N seconds are saved. no cloud, no account, nothing calling home. that's it.

**linux only for now.** windows port is in progress. watch the repo.

---

## why

medal is windows-only. idles at 20%+ gpu on a 3090. defaults to cpu encoding. uploads your clips publicly by default. adds itself to startup without asking. one guy called it malware because it reinstalled itself after uninstall. there's a black screen recording bug that's been open for years. they know.

mitten uses `gpu-screen-recorder` which hits nvenc/vaapi directly. idles under 300mb ram for a 30s 1080p60 buffer. your gpu doesn't notice it's running.

also if you're on linux, medal doesn't exist. this does.

---

## what it does

- **replay buffer**: last N seconds, always rolling. press the button, it saves. you know how this works
- **game detection**: sees a game launch, starts capture, stops when you close it
- **session recording**: triple-click to start, triple-click to stop and save. same button, different gesture
- **discord compression**: hits the 10mb free limit. two-pass ffmpeg locally, 90+ vmaf at sizes where online tools just give up. your clip never leaves your machine
- **vocal trigger** *(coming)*: auto-clips laughs, jumpscares, hype moments. no button needed
- **watermark**: burned in on save. fully customizable (text, size, position, opacity). one tiny "mitten" credit stays. it's a solo project, that's all it asks. MIT license, fork if you want
- **auto-update**: checks github on startup, backs up first, rolls back if something breaks
- **native gui**: PyQt6 tray app. clip browser, trim, settings, stats. not electron

---

## actually local

zero telemetry. no account. no uploads. no "share with the community" checkbox that's pre-ticked.

the only server mitten has ever touched is github, and only to check for updates. that can be turned off too.

clips live on your drive. compression runs on your machine. nothing goes anywhere.

---

## how it's built

i'm an avid stimulant abuser who games daily and breaks his own software doing it. features get stress tested until something snaps, then fixed, then stress tested again.

i also build a lot of this with claude actively in the codebase. i give him bad instructions on purpose sometimes just to see what he does. he usually figures it out. occasionally he does not. the commits speak for themselves.

every line gets read and tested on real hardware before it ships.

---

## requirements

**OS:** arch-based (arch, cachyos, manjaro, endeavouros). wayland only.

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

`--break-system-packages` is required on arch. it's fine, mitten's deps don't conflict with system packages.

if `mitten` isn't in your path after install, your shell doesn't know about `~/.local/bin`. fix it:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
# zsh:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

first run launches the setup wizard: installs missing deps, adds you to the `input` group, writes a default config, sets up the systemd service. you don't need to open a terminal again after that.

---

## usage
```bash
mitten    # opens gui, runs setup wizard on first launch
```

the daemon runs as a systemd user service. the gui handles start/stop. if you need to poke it directly:
```bash
systemctl --user start mitten.service
systemctl --user stop mitten.service
systemctl --user status mitten.service
```

---

## status

actively used. i clip games with it daily.

something breaks, open an issue. you fix it, open a pr.

---

## coming

- vocal trigger: auto-clips laughs, screams, hype moments without pressing anything
- discord rich presence + direct clip posting
- windows port
- mitten.clips. soon.

---

## license

MIT. see LICENSE.
