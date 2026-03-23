# mitten

a clipping tool that doesn't suck.

keeps a rolling replay buffer on your gpu. press a button, the last N seconds are saved. no cloud, no account, nothing calling home. that's it.

**linux only for now. specifically wayland, dont try to use this on x11 you retard** windows port is in progress. watch the repo.

---

## why

medal idles at 20%+ gpu on a 3090. uploads your clips publicly by default. adds itself to startup without asking. one guy called it malware because it reinstalled itself after uninstall. there's a black screen recording bug that's been open for years. they know. they don't care.

also medal is windows only, so if you're on linux it literally doesn't exist. this does. you're welcome.

mitten hits nvenc/vaapi directly. idles under 300mb ram for a 30s 1080p60 buffer. your gpu doesn't notice it's running.

tdlr medal is shit brah

---

## what it does

- **replay buffer**: last N seconds, always rolling. press the button, it saves. you know how this works
- **game detection**: sees a game launch, starts capture, stops when you close it
- **session recording**: triple click to start, triple click to stop and save. same button, different gesture
- **discord compression**: hits the 10mb free limit. two-pass compression locally, your clip never leaves your machine. online tools fumble this. mitten doesn't
- **vocal trigger** *(coming)*: auto clips laughs, jumpscares, hype moments. no button needed
- **watermark**: burned in on save. fully customizable. one tiny "mitten" credit stays. it's a solo project, that's literally all it asks. fork it if you want, i'm not your dad
- **auto update**: checks for updates on startup, backs up first, rolls back if something breaks
- **gui**: tray app. clip browser, trim, settings, stats. not electron. i said what i said

---

## actually local

zero telemetry. no account. no uploads. no "share with the community" checkbox that's pre ticked and buried in settings.

clips live on your drive. nothing goes anywhere.

---

## how it's built

i'm an avid stimulant abuser who games daily and breaks his own software doing it. i've bricked my os 5 times messing around with shit i shouldn't. if that doesn't make you trust this software i cooked up in a week or two with my homebot claude, i don't know what will.

i also give claude bad instructions on purpose sometimes just to see what he does. he usually figures it out. occasionally he does not. the commits speak for themselves.

---

## requirements

**OS:** arch based (arch, cachyos, manjaro, endeavouros). wayland only. (developed on cachy os specifically)

you need `gpu-screen-recorder`, `ffmpeg`, and `notify-send`. the setup wizard installs everything, don't worry about it.

python 3.11+

---

## install
```bash
git clone https://github.com/mitmitmitmitmitmit/mitten
cd mitten
pip install -e . --break-system-packages
mitten
```

`--break-system-packages` is required on arch. i know how it looks. it's fine.

if `mitten` isn't found after install, your shell doesn't know about `~/.local/bin`:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
# zsh:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

first run launches the setup wizard and handles everything. you don't need a terminal again after that. probably.

---

## usage
```bash
mitten
```

that's it. the gui handles everything else. if you need to poke the daemon directly:
```bash
systemctl --user start mitten.service
systemctl --user stop mitten.service
systemctl --user status mitten.service
```

---

## status

actively used. i clip games with it daily. it hasn't bricked anything for now...

something breaks, open an issue. you fix it, open a pr. there are better odds of me finding love that isn't a femboy twink than me actually reviewing it, which if you knew me, you'd understand is not a high bar. if anything ill just tell the robot to go through the issue list and mark down which issues are critical and which are not, non critical ones get put in the to do when i feel like it list.
---

## coming

- vocal trigger: auto-clips laughs, screams, hype moments without pressing anything
- discord rich presence + direct clip posting
- windows port (i know, i know)
- mitten.clips. soon. i mean it this time.

---

## license

MIT. do whatever you want with it, just don't make it worse. if you do ill fucking kill you. im being so serious (not really)
