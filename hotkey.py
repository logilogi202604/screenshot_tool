"""System-wide hotkey on Windows using Win32 RegisterHotKey via ctypes.

Integrates with the Qt event loop through a QAbstractNativeEventFilter so we
don't need a background thread or third-party dependency.
"""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QAbstractNativeEventFilter, Signal

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.windll.user32

# Named virtual-key codes for keys that aren't a single ASCII character.
_VK_NAMES = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "SPACE": 0x20, "INSERT": 0x2D, "PRINTSCREEN": 0x2C, "SNAPSHOT": 0x2C,
}


def vk_from_key(key):
    """Map a config key string ('A', 'F2', '1') to a Win32 virtual-key code."""
    key = str(key).upper().strip()
    if key in _VK_NAMES:
        return _VK_NAMES[key]
    if len(key) == 1:
        return ord(key)  # letters and digits share their ASCII code as VK
    return ord(key[0])


def modifiers_from_config(hk):
    mods = 0
    if hk.get("ctrl"):
        mods |= MOD_CONTROL
    if hk.get("alt"):
        mods |= MOD_ALT
    if hk.get("shift"):
        mods |= MOD_SHIFT
    if hk.get("win"):
        mods |= MOD_WIN
    return mods


def describe_hotkey(hk):
    parts = []
    if hk.get("ctrl"):
        parts.append("Ctrl")
    if hk.get("alt"):
        parts.append("Alt")
    if hk.get("shift"):
        parts.append("Shift")
    if hk.get("win"):
        parts.append("Win")
    parts.append(str(hk.get("key", "A")).upper())
    return "+".join(parts)


class GlobalHotkey(QObject, QAbstractNativeEventFilter):
    activated = Signal()

    def __init__(self, modifiers, vk, hotkey_id=1, parent=None):
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self.hotkey_id = hotkey_id
        self.modifiers = modifiers | MOD_NOREPEAT
        self.vk = vk
        self.registered = False
        self._hwnd = None

    def register(self, hwnd=None):
        """Register the hotkey. hwnd may be a window handle (int) or None."""
        self._hwnd = hwnd
        handle = wintypes.HWND(hwnd) if hwnd else None
        self.registered = bool(
            user32.RegisterHotKey(handle, self.hotkey_id, self.modifiers, self.vk)
        )
        self.last_error = 0 if self.registered else ctypes.windll.kernel32.GetLastError()
        return self.registered

    def unregister(self):
        if self.registered:
            handle = wintypes.HWND(self._hwnd) if self._hwnd else None
            user32.UnregisterHotKey(handle, self.hotkey_id)
            self.registered = False

    def nativeEventFilter(self, eventType, message):
        # A thread-bound hotkey (hwnd=NULL) may be delivered either through a
        # window message ("windows_generic_MSG") or the thread message dispatcher
        # ("windows_dispatcher_MSG") depending on the Qt build — accept both.
        if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                    self.activated.emit()
            except (ValueError, TypeError):
                pass
        return False, 0
