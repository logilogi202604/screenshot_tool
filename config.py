"""User configuration for the screenshot tool.

Stored as JSON at ~/.screenshot_tool/config.json so the hotkey and other
preferences can be changed without touching the code.
"""
import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".screenshot_tool")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    # Global hotkey. Defaults to WeChat-style Alt+A (Ctrl+A would collide with
    # "Select All" in every app). Edit ctrl/alt/shift/win/key and restart to change.
    "hotkey": {"ctrl": False, "alt": True, "shift": False, "win": False, "key": "A"},
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
    # os.makedirs raise TypeError and crash the save/open flows).
    if not isinstance(cfg.get("save_dir"), str) or not cfg["save_dir"].strip():
        cfg["save_dir"] = DEFAULTS["save_dir"]

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
