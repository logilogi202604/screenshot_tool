"""Shared, platform-independent helpers for the global hotkey.

The actual hotkey registration lives in the platform backends
(``hotkey_win`` / ``hotkey_mac`` / ``hotkey_null``); only pure formatting that
every backend shares lives here.
"""
import sys

_MAC = sys.platform == "darwin"


def describe_hotkey(hk):
    """Human-readable combo like ``Alt+A`` (Win) or ``Cmd+Shift+A`` (macOS).

    The config schema is the same on every platform; only the *labels* differ:
    on macOS ``win`` means the Command key and ``alt`` means Option.
    """
    parts = []
    if hk.get("ctrl"):
        parts.append("Ctrl")
    if hk.get("alt"):
        parts.append("Option" if _MAC else "Alt")
    if hk.get("shift"):
        parts.append("Shift")
    if hk.get("win"):
        parts.append("Cmd" if _MAC else "Win")
    parts.append(str(hk.get("key", "A")).upper())
    return "+".join(parts)
