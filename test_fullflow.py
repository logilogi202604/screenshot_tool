"""Diagnose the full hotkey -> capture -> overlay chain in the real app."""
import ctypes
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from config import load_config
from hotkey import describe_hotkey
import main as M

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

cfg = load_config()
print("loaded hotkey:", describe_hotkey(cfg["hotkey"]), "->", cfg["hotkey"])

tray = M.TrayApp(app, cfg)
print("hotkey registered:", tray.hotkey.registered)

VK_MENU = 0x12  # Alt
VK_A = 0x41
KEYUP = 0x0002
user32 = ctypes.windll.user32


def tap_alt_a():
    print("simulating Alt+A ...")
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_A, 0, 0, 0)
    user32.keybd_event(VK_A, 0, KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)


def check():
    ov = tray.overlay
    print("overlay created:", ov is not None)
    if ov is not None:
        print("  visible:", ov.isVisible())
        print("  geometry:", ov.geometry())
        print("  active:", ov.isActiveWindow())
        ov.close()
    app.quit()


QTimer.singleShot(600, tap_alt_a)
QTimer.singleShot(1600, check)
app.exec()
print("done")
