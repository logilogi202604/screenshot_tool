"""Verify Win32 RegisterHotKey fires through Qt's native event filter.

Registers Ctrl+Alt+Shift+F12 (a deliberately unusual combo), synthesizes the
keypress with SendInput, and checks the `activated` signal fires.
"""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from hotkey import GlobalHotkey, MOD_ALT, MOD_CONTROL, MOD_SHIFT

VK_CONTROL = 0x11
VK_MENU = 0x12      # Alt
VK_SHIFT = 0x10
VK_F12 = 0x7B
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32


def tap(down_keys):
    for vk in down_keys:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(down_keys):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


app = QApplication(sys.argv)
holder = QWidget()
holder.show()
holder.hide()
hwnd = int(holder.winId())

hk = GlobalHotkey(MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_F12)
app.installNativeEventFilter(hk)
ok = hk.register(hwnd)
print("registered:", ok)
assert ok, "RegisterHotKey failed"

state = {"fired": False}
hk.activated.connect(lambda: state.update(fired=True))
hk.activated.connect(app.quit)

# Fire the combo shortly after the loop starts.
QTimer.singleShot(300, lambda: tap([VK_CONTROL, VK_MENU, VK_SHIFT, VK_F12]))
# Fail-safe timeout.
QTimer.singleShot(3000, app.quit)

app.exec()
hk.unregister()
print("FIRED" if state["fired"] else "NOT FIRED")
sys.exit(0 if state["fired"] else 1)
