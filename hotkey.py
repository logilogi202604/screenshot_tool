"""Global-hotkey facade: picks the right backend for the current platform.

Every backend exposes the same interface used by ``main.py``:

* ``GlobalHotkey.from_config(hk)``  -> instance
* ``instance.activated``           -> Qt Signal emitted on the main thread
* ``instance.start(app)``          -> bool, register + wire into the event loop
* ``instance.unregister()``
* ``instance.last_error``          -> diagnostic for logging

``describe_hotkey`` is shared and re-exported here for convenience.
"""
import sys

from hotkey_common import describe_hotkey  # noqa: F401  (re-exported)

if sys.platform == "darwin":
    from hotkey_mac import GlobalHotkey
elif sys.platform == "win32":
    # Re-export the full Win32 surface the original single-file `hotkey` module
    # exposed, so existing Windows code and tests keep working unchanged
    # (e.g. test_hotkey.py does `from hotkey import GlobalHotkey, MOD_ALT, ...`).
    from hotkey_win import (  # noqa: F401
        GlobalHotkey,
        MOD_ALT,
        MOD_CONTROL,
        MOD_SHIFT,
        MOD_WIN,
        MOD_NOREPEAT,
        WM_HOTKEY,
        vk_from_key,
        modifiers_from_config,
    )
else:
    from hotkey_null import GlobalHotkey

__all__ = ["GlobalHotkey", "describe_hotkey"]
