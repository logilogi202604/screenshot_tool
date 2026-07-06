"""Verify a THREAD-bound hotkey (hwnd=NULL) both registers persistently and
delivers WM_HOTKEY to Qt's native event filter."""
import sys

if sys.platform != "win32":
    print("SKIP: test_null_hotkey.py exercises Win32 RegisterHotKey (macOS is "
          "covered by test_mac.py)")
    raise SystemExit(0)

import ctypes

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hotkey import GlobalHotkey, MOD_ALT, MOD_CONTROL, MOD_SHIFT

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_F12 = 0x7B
KEYUP = 0x0002
user32 = ctypes.windll.user32

app = QApplication(sys.argv)

hk = GlobalHotkey(MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_F12)
app.installNativeEventFilter(hk)
ok = hk.register(None)  # thread-bound, no window
print("thread register:", ok, "err:", hk.last_error)

state = {"fired": False}
hk.activated.connect(lambda: state.update(fired=True))


def tap():
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_F12):
        user32.keybd_event(vk, 0, 0, 0)
    for vk in (VK_F12, VK_SHIFT, VK_MENU, VK_CONTROL):
        user32.keybd_event(vk, 0, KEYUP, 0)


def finish():
    hk.unregister()
    print("FIRED" if state["fired"] else "NOT FIRED")
    app.quit()


# Fire after 4s — also proves the registration survived several seconds.
QTimer.singleShot(4000, tap)
QTimer.singleShot(5000, finish)
app.exec()
sys.exit(0 if state["fired"] else 1)
