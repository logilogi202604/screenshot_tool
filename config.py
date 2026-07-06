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

    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
