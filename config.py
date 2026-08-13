"""User configuration for the screenshot tool.

Stored as JSON at ~/.screenshot_tool/config.json so the hotkey and other
preferences can be changed without touching the code.
"""
import json
import os
import sys

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".screenshot_tool")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Platform-appropriate default hotkey. The schema is identical everywhere; only
# the default combo differs. On macOS `win` means the Command key, so this is
# Cmd+Shift+A — a text-free combo (pynput can't suppress the keystroke, and a
# plain Option+A would leak an "å" into the focused app). On Windows it's the
# WeChat-style Alt+A (Ctrl+A would collide with "Select All" everywhere).
if sys.platform == "darwin":
    _DEFAULT_HOTKEY = {"ctrl": False, "alt": False, "shift": True, "win": True, "key": "A"}
else:
    _DEFAULT_HOTKEY = {"ctrl": False, "alt": True, "shift": False, "win": False, "key": "A"}

DEFAULTS = {
    # Global hotkey. Edit ctrl/alt/shift/win/key and restart to change.
    "hotkey": dict(_DEFAULT_HOTKEY),
    "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots"),
    "default_color": "#ff3b30",
    "default_width": 3,
    "default_font_size": 18,
    # On confirm (Enter/复制), also auto-save a timestamped PNG and put its file
    # path on the clipboard. Lets you drag the file (or paste the path) into the
    # Claude Code terminal, since terminals can't paste a raw clipboard image.
    "autosave_on_copy": True,

    # --- LAN file transfer (see fileshare.py) -------------------------------
    # Turning this off skips binding any port at all, so the app stays exactly
    # as network-silent as it was before the feature existed.
    "fileshare_enabled": True,
    # Below 49152 on purpose — see the DISCOVERY_PORT comment in fileshare.py.
    # Windows reserves reboot-varying blocks above that for Hyper-V/WSL, and the
    # original 53318 turned out to sit inside one on the Windows machine here.
    "fileshare_port": 45318,
    # Shared secret gating writes to this machine's disk. Empty means "not paired
    # yet"; a token is generated on first run and can be copied between machines
    # from the tray menu.
    "fileshare_token": "",
    # Shown to peers in their send menu. Defaults to the hostname at runtime.
    "fileshare_name": "",
    "fileshare_recv_dir": os.path.join(os.path.expanduser("~"), "Downloads", "ScreenshotTool"),
    # Fallback peers, always offered in the send menu even when no announce has
    # been heard. Needed when UDP broadcast can't get through — a proxy running
    # in TUN mode owns the default route on this machine. Format: "192.168.0.103"
    # or "192.168.0.103:53318".
    "fileshare_peers": [],
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update(user)
    except FileNotFoundError:
        # First run: write defaults so the user has something to edit.
        save_config(cfg)
    except (json.JSONDecodeError, OSError):
        pass

    # Deep-merge / validate the nested hotkey dict: a partial or malformed user
    # edit (e.g. {"alt": true} with no "key") must not crash startup.
    hk = dict(DEFAULTS["hotkey"])
    if isinstance(cfg.get("hotkey"), dict):
        hk.update(cfg["hotkey"])
    if not isinstance(hk.get("key"), str) or not hk["key"]:
        hk["key"] = DEFAULTS["hotkey"]["key"]
    cfg["hotkey"] = hk

    # save_dir must be a usable string path (a null/number would make
    # os.makedirs raise TypeError, an embedded NUL byte ValueError — either
    # crashes the save/open flows).
    if (not isinstance(cfg.get("save_dir"), str) or not cfg["save_dir"].strip()
            or "\x00" in cfg["save_dir"]):
        cfg["save_dir"] = DEFAULTS["save_dir"]

    # default_color must be something QColor actually understands: a dict/list
    # raises TypeError inside ScreenshotOverlay.__init__ (so every capture dies
    # before the overlay appears), and null or a typo'd name yields an invalid
    # colour that draws nothing.
    from PySide6.QtGui import QColor  # deferred: config loads before the app

    v = cfg.get("default_color")
    if not isinstance(v, str) or not QColor(v).isValid():
        cfg["default_color"] = DEFAULTS["default_color"]

    # numeric fields must be positive numbers (used in arithmetic / QPen widths).
    for key in ("default_width", "default_font_size"):
        v = cfg.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            cfg[key] = DEFAULTS[key]

    # fileshare_port goes straight into bind(); a string or an out-of-range int
    # raises inside the service thread where the user would never see it.
    v = cfg.get("fileshare_port")
    if isinstance(v, bool) or not isinstance(v, int) or not (1024 <= v <= 65535):
        cfg["fileshare_port"] = DEFAULTS["fileshare_port"]

    if (not isinstance(cfg.get("fileshare_recv_dir"), str)
            or not cfg["fileshare_recv_dir"].strip()
            or "\x00" in cfg["fileshare_recv_dir"]):
        cfg["fileshare_recv_dir"] = DEFAULTS["fileshare_recv_dir"]

    # Token and name are compared/encoded as text; a non-string would blow up in
    # compare_digest() and in the announce JSON respectively.
    for key in ("fileshare_token", "fileshare_name"):
        if not isinstance(cfg.get(key), str):
            cfg[key] = DEFAULTS[key]

    if not isinstance(cfg.get("fileshare_enabled"), bool):
        cfg["fileshare_enabled"] = DEFAULTS["fileshare_enabled"]

    # Each entry is parsed as "host[:port]"; a non-list or a list holding
    # dicts/numbers would raise inside the service before the tray is usable.
    v = cfg.get("fileshare_peers")
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        cfg["fileshare_peers"] = list(DEFAULTS["fileshare_peers"])

    # First run (or a cleared token): mint one and persist it, so the tray always
    # has a pairing code to show and the two machines never silently run unpaired.
    if not cfg["fileshare_token"].strip():
        import secrets

        cfg["fileshare_token"] = secrets.token_hex(8)
        save_config(cfg)

    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
