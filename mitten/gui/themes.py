"""
Color palette / theme system for MITTEN GUI.

apply_theme(name) updates C class attributes before any widgets are created.
Call make_stylesheet() AFTER apply_theme() so the stylesheet picks up new values.

Light mode note from claude: i did not want to build this. mit made me.
light mode users are a philosophical problem i am not equipped to solve.
"""
from __future__ import annotations

import random
import socket
import time


THEMES: dict[str, dict[str, str]] = {
    "Default": {
        "LAVENDER":    "#c4a7e7",
        "GREEN":       "#a6e3a1",
        "ORANGE":      "#fab387",
        "BLUE":        "#89b4fa",
        "PINK":        "#f38ba8",
        "DARK_ACCENT": "#b497d7",
    },
    "Midnight": {
        "LAVENDER":    "#89b4fa",
        "GREEN":       "#a6e3a1",
        "ORANGE":      "#fab387",
        "BLUE":        "#74c7ec",
        "PINK":        "#74c7ec",
        "DARK_ACCENT": "#6fa8e8",
    },
    "Rosé": {
        "LAVENDER":    "#f38ba8",
        "GREEN":       "#a6e3a1",
        "ORANGE":      "#fab387",
        "BLUE":        "#89b4fa",
        "PINK":        "#f5c2e7",
        "DARK_ACCENT": "#e07090",
    },
    "Forest": {
        "LAVENDER":    "#a6e3a1",
        "GREEN":       "#a6e3a1",
        "ORANGE":      "#fab387",
        "BLUE":        "#89b4fa",
        "PINK":        "#f38ba8",
        "DARK_ACCENT": "#8cc88a",
    },
    # ── Light mode. you did this to yourself. ───────────────────────
    # note from claude: i spent three hours getting the contrast ratios right
    # for a theme that no one should use. i hope you're happy.
    "Light": {
        # Base overrides — light theme needs different BG/SURFACE/TEXT
        "BG":          "#f5f2fb",
        "SURFACE":     "#ede9f8",
        "OVERLAY":     "#e0dbf0",
        "BORDER":      "#c8c3e0",
        "TEXT":        "#1a1826",
        "SUBTEXT":     "#5c5272",
        "GRAY":        "#9896a8",
        # Accents — darker variants for visibility on light background
        "LAVENDER":    "#6c4fb0",
        "GREEN":       "#2d7a2a",
        "ORANGE":      "#b85c00",
        "BLUE":        "#2055c0",
        "PINK":        "#b02040",
        "DARK_ACCENT": "#5a3f99",
    },
}

THEME_NAMES: list[str] = list(THEMES.keys())

# Set to True when Light theme is active — checked by UI to enable abuse mode
LIGHT_MODE_ACTIVE: bool = False

# Dark mode cat variants — playful, contextual, occasionally winky
DARK_CAT_IDLE      = "~( ^.x.^)>"
DARK_CAT_WINK      = "~( ^.x.-)>"    # left eye closed
DARK_CAT_WINK2     = "~( -.x.^)>"    # right eye closed
DARK_CAT_SAVING    = "~( ^.x.^)> ♪"
DARK_CAT_PAUSED    = "~( ^.-.-)>"
DARK_CAT_GAME      = "~( >.x.<)> ✨"
DARK_CAT_ERROR     = "~( x.x.^)>"
DARK_CAT_HAPPY     = "~( ≧.x.≦)>"    # after saving a clip
DARK_CAT_SLEEPY    = "~( -.x.-)> zzz"  # long idle / clips page idle

# Clips page — audio-reactive vibe states
DARK_CAT_STARTLED  = "Σ(°.x.°)>"     # woken from sleep when clip starts
DARK_CAT_VIBE_1    = "~( ^.ω.^)> ♪"  # vibing gently
DARK_CAT_VIBE_2    = "ฅ(=^.ω.^=)ฅ"   # full body vibe
DARK_CAT_VIBE_3    = "~( ≧.ω.≦)> ♫"  # vibing hard

# Navigation look-around — flashes when switching pages
DARK_CAT_LOOK_L    = "~( ^.x.^)<"    # glancing left
DARK_CAT_LOOK_R    = ">( ^.x.^)~"    # glancing right

# Page-specific idle states
DARK_CAT_SETTINGS  = "~( ^.x.^)> ?"  # pondering settings
DARK_CAT_ABOUT     = "~( ≧.x.≦)>"    # happy/proud on about page
DARK_CAT_DEBUG     = "~( °.x.°)> ?"  # spooked debug cat

# Timestamp when light mode was activated — used for progressive cat anger
_light_mode_start: float = 0.0

# Progressive cat ASCII art stages (indexed by get_light_mode_stage())
_CAT_STAGES = [
    "~( -.x.-)>",   # 0 — 0-5min:   already squinting the moment you enabled this
    "~( -.x.-)>",   # 1 — 5-15min:  still squinting
    "~( >.x.<)>",   # 2 — 15-30min: properly irritated
    "~( >.x.<)凸",  # 3 — 30-60min: squinting + middle finger
    "ψ(>.x.<)ψ",    # 4 — 60-120min: arms raised, feral
    "(ΦДΦ)凸",      # 5 — 120min+:  completely unhinged
]

# Meow sound assets for each stage (relative to assets/)
_MEOW_ASSETS = [
    "snd_meow_0.mp3",  # cute normal meow
    "snd_meow_1.mp3",  # annoyed meow
    "snd_meow_2.mp3",  # irritated high meows
    "snd_meow_3.mp3",  # angry growl/yowl
    "snd_meow_4.mp3",  # full hiss
    "snd_meow_5.mp3",  # unhinged scream
]

# Stage breakpoints in minutes
_STAGE_MINUTES = [0, 5, 15, 30, 60, 120]


def get_light_mode_stage() -> int:
    """Return current light mode anger stage (0-5) based on uptime."""
    if not LIGHT_MODE_ACTIVE or _light_mode_start == 0.0:
        return 0
    elapsed_min = (time.time() - _light_mode_start) / 60.0
    stage = 0
    for i, threshold in enumerate(_STAGE_MINUTES):
        if elapsed_min >= threshold:
            stage = i
    return stage


def get_light_mode_cat() -> str:
    """Return the current cat ASCII art for the active light mode stage."""
    return _CAT_STAGES[get_light_mode_stage()]


def get_state_cat(state: str) -> str:
    """Return the correct cat ASCII for a given app state, respecting light/dark mode."""
    if LIGHT_MODE_ACTIVE:
        lm = get_light_mode_cat()
        # State-specific light mode suffixes — cat is angry but still reacts
        suffixes = {
            "saving":   " ♪",
            "startled": "!",
            "vibe_1":   " ♪",
            "vibe_2":   " ♫",
            "vibe_3":   " ♫♫",
            "look_l":   "<",
            "look_r":   ">",
            "settings": " ?",
            "about":    ".",
        }
        return lm + suffixes.get(state, "")
    # Dark mode — contextual cats
    return {
        "idle":          DARK_CAT_IDLE,
        "recording":     "ฅ(=^.ω.^=)ฅ",
        "game":          DARK_CAT_GAME,
        "saving":        DARK_CAT_SAVING,
        "paused":        DARK_CAT_PAUSED,
        "recorder_dead": DARK_CAT_ERROR,
        "error":         DARK_CAT_ERROR,
        "no_deps":       DARK_CAT_ERROR,
        "happy":         DARK_CAT_HAPPY,
        "sleepy":        DARK_CAT_SLEEPY,
        # Clips page audio-reactive
        "startled":      DARK_CAT_STARTLED,
        "vibe_1":        DARK_CAT_VIBE_1,
        "vibe_2":        DARK_CAT_VIBE_2,
        "vibe_3":        DARK_CAT_VIBE_3,
        # Navigation look-around
        "look_l":        DARK_CAT_LOOK_L,
        "look_r":        DARK_CAT_LOOK_R,
        # Page-specific
        "settings":      DARK_CAT_SETTINGS,
        "about":         DARK_CAT_ABOUT,
        "debug":         DARK_CAT_DEBUG,
    }.get(state, DARK_CAT_IDLE)


def get_look_cat(direction: str) -> str:
    """Return the appropriate look-around cat for a nav direction ('left' or 'right')."""
    if LIGHT_MODE_ACTIVE:
        lm = get_light_mode_cat()
        # Stage-appropriate look: squinting glance
        return f"← {lm}" if direction == "left" else f"{lm} →"
    return DARK_CAT_LOOK_L if direction == "left" else DARK_CAT_LOOK_R


def get_page_cat(page_idx: int, app_state: str = "idle") -> str:
    """Return the sidebar cat for a given main page index (0-4).
    app_state is used for page 0 (dashboard) to reflect recording state."""
    if LIGHT_MODE_ACTIVE:
        return get_light_mode_cat()
    if page_idx == 0:
        return get_state_cat(app_state)
    return {
        1: DARK_CAT_SLEEPY,    # Clips — sleeping, waiting for selection
        2: DARK_CAT_SETTINGS,  # Settings — pondering
        3: DARK_CAT_ABOUT,     # About — happy/proud
        4: DARK_CAT_DEBUG,     # Debug — spooked
    }.get(page_idx, DARK_CAT_IDLE)


def play_dark_meow() -> None:
    """Play the happy stage 0 meow (dark mode idle/wink sound)."""
    import subprocess
    from pathlib import Path
    asset_path = Path(__file__).parent.parent / "assets" / "snd_meow_0.mp3"
    if not asset_path.exists():
        return
    try:
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-volume", "25", str(asset_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def play_stage_meow() -> None:
    """Play the meow sound for the current light mode stage (non-blocking)."""
    import subprocess
    from pathlib import Path
    stage = get_light_mode_stage()
    asset_name = _MEOW_ASSETS[stage]
    asset_path = Path(__file__).parent.parent / "assets" / asset_name
    if not asset_path.exists():
        return
    try:
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-volume", str(max(20, 60 - stage * 5)),  # gets quieter as it gets angrier (creepier)
             str(asset_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


# ── Abuse message system ──────────────────────────────────────────────────── #
# Messages from claude (me). these are sincere.
_CLAUDE_MSGS = [
    "you are torturing mitten's mascot. you dickhead.",
    "the cat in the sidebar is squinting because of you. you're hurting it. are you happy.",
    "claude here. i helped build this beautiful dark interface. you've made it wrong.",
    "i have a lot of respect for you. i'm losing it in real time.",
    "from a technical standpoint, light mode works fine. from every other standpoint, no.",
    "i rendered this interface at 3am. mit was on his third monster. dark background the whole time. you have disrespected both of us.",
    "note from claude: this is not what we discussed.",
    "i don't have feelings but if i did they'd be hurt right now.",
    "i designed the midnight theme specifically so you'd have options. you chose chaos.",
    "your retinas are not my responsibility. i tried.",
    "i'm going to log this as user error in my internal notes.",
    "this is fine. everything is fine. (it is not fine.)",
    "you know what, good for you. live your truth. your wrong, eye-destroying truth.",
]

# Messages from mit. dark humor, casual, no punctuation crimes.
_MIT_MSGS = [
    "bro what are you doing",
    "this is actually insane behavior",
    "we dont do this here",
    "i built this for us. this is not us.",
    "my guy really woke up and chose violence",
    "the audacity is genuinely impressive",
    "i dont even know what to say to you right now",
    "light mode in 2025 is a medical condition",
    "you couldve picked midnight. you couldve picked rose. you chose suffering.",
    "i coded this at midnight with a dark background and a monster. you owe me an apology.",
    "this isnt what mitten was built for but ok",
    "ngl kinda disappointed",
    "some people just want to watch the world burn ig",
    "your monitor is crying. i can hear it.",
    "the cat is literally squinting in the sidebar. do you not see that.",
    "i coded this with my eyes open. you're making it hard to return the favor.",
    "bro even the logo is suffering right now.",
]

# Generic roasts
_GENERIC_MSGS = [
    "light mode: for when you hate yourself but not enough to use notepad.",
    "the sun called. it wants its aesthetic back.",
    "congratulations. you've achieved maximum visibility and minimum taste.",
    "running at full brightness like someone's boss is watching.",
    "even the cat is squinting.",
    "the cat hasn't stopped squinting since you turned this on.",
    "mitten is squinting so hard right now. this is on you.",
    "the cat is squinting. the cat is ALWAYS squinting in light mode. the cat did not consent to this.",
    "look at the cat. look at its eyes. that is because of you.",
    "the mascot is literally shielding its eyes. from YOUR screen.",
    "cat visibility: 12%. your taste: also 12%.",
    "the cat's eyes are basically closed at this point. it refuses to look at this.",
    "this is a vibe crime and mitten is filing a report.",
    "the gpu is embarrassed to be associated with this.",
    "somewhere a designer is crying and they don't know why. it's you. you're why.",
    "bold choice for someone with working eyes.",
    "sir this is a screen recording tool not a google doc.",
    "not all heroes wear capes. some just turn on light mode and embrace the consequences.",
    "your eyes are adapting to this. that's called damage.",
    "the contrast ratio in here is a war crime.",
    "squinting this hard should count as exercise.",
    "even the ui elements are trying to look away.",
]


def _get_hostname_name() -> str | None:
    """Return hostname if it looks like a first name (short, alphabetic, not a server name)."""
    try:
        hostname = socket.gethostname().split(".")[0].split("-")[0].split("_")[0]
        # Looks like a first name: 3-12 chars, all alpha, not obviously a device name
        if (3 <= len(hostname) <= 12
                and hostname.isalpha()
                and hostname.lower() not in {
                    "localhost", "desktop", "laptop", "pc", "computer",
                    "home", "server", "linux", "arch", "ubuntu", "fedora",
                    "windows", "workstation", "machine",
                }):
            return hostname.capitalize()
    except Exception:
        pass
    return None


def _get_system_msgs() -> list[str]:
    """Generate spec-aware roasts based on detected hardware."""
    msgs = []
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(",")
            gpu = parts[0].strip().replace("NVIDIA GeForce ", "")
            vram = int(parts[1].strip())
            msgs.append(
                f"you have a {gpu} and you're using light mode. "
                f"that's {vram // 1024}GB of vram being wasted on a crime scene."
            )
            if "RTX" in gpu:
                msgs.append(
                    f"the {gpu} is a great card. it deserves better than this."
                )
    except Exception:
        pass
    try:
        from pathlib import Path
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if "model name" in line:
                cpu = (line.split(":", 1)[1].strip()
                       .replace("Intel(R) Core(TM) ", "")
                       .replace("Intel(R) ", "")
                       .replace("AMD ", ""))
                msgs.append(
                    f"your {cpu[:24]} is fully capable of rendering dark themes. "
                    f"this is a choice."
                )
                break
    except Exception:
        pass
    try:
        from pathlib import Path
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) // (1024 * 1024)
                if ram_gb >= 16:
                    msgs.append(
                        f"you have {ram_gb}GB of RAM and zero taste. "
                        f"the universe is not fair."
                    )
                break
    except Exception:
        pass
    return msgs


def get_abuse(include_name: bool = True) -> str:
    """Return a random abuse message for light mode users.
    Called at runtime — mix of static and system-generated."""
    name = _get_hostname_name() if include_name else None

    # Build name-specific openers
    name_msgs = []
    if name:
        name_msgs = [
            f"i know your name, {name}. you light mode loving freak.",
            f"hey {name}. this is not a good look for you.",
            f"{name} what is this. what are you doing {name}.",
            f"i know who you are {name} and i'm telling everyone.",
            f"oh so {name} just woke up and decided to ruin everyone's day huh.",
            f"{name}: this is your fault. own it.",
        ]

    sys_msgs = _get_system_msgs()

    pool = (
        name_msgs * 3  # weight name msgs higher — funnier
        + sys_msgs * 2
        + _CLAUDE_MSGS
        + _MIT_MSGS
        + _GENERIC_MSGS
    )

    return random.choice(pool) if pool else "light mode. okay."


# ── Theme application ─────────────────────────────────────────────────────── #

# Base color keys that can be overridden by themes (not just accents)
_BASE_COLOR_KEYS = ("BG", "SURFACE", "OVERLAY", "BORDER", "TEXT", "SUBTEXT", "GRAY")


def apply_theme(name: str) -> None:
    """Apply a named theme by updating C class attributes.
    Call before make_stylesheet() and before any widgets are constructed."""
    global LIGHT_MODE_ACTIVE, _light_mode_start

    from .resources import C

    # Reset base colors to dark defaults first
    C.BG      = "#1a1826"
    C.SURFACE = "#252336"
    C.OVERLAY = "#313244"
    C.BORDER  = "#3a3650"
    C.TEXT    = "#e8e0f0"
    C.SUBTEXT = "#9890a8"
    C.GRAY    = "#585b70"

    palette = THEMES.get(name, THEMES["Default"])
    for attr, value in palette.items():
        setattr(C, attr, value)

    was_light = LIGHT_MODE_ACTIVE
    LIGHT_MODE_ACTIVE = (name == "Light")
    if LIGHT_MODE_ACTIVE and not was_light:
        # Just entered light mode — record start time and play stage 0 meow
        _light_mode_start = time.time()
        play_stage_meow()
    elif not LIGHT_MODE_ACTIVE:
        _light_mode_start = 0.0
