"""Probe the exact hotkey-registration path used by main.py and log the result."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget

out = sys.argv[1] if len(sys.argv) > 1 else r"D:\code\screenshot_tool\_diag.txt"

app = QApplication(sys.argv)
win = QWidget()
win.setWindowFlags(Qt.Tool)
win.setAttribute(Qt.WA_DontShowOnScreen, True)
win.resize(1, 1)
win.show()
hwnd = int(win.winId())

u = ctypes.windll.user32
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
VK_A = 0x41
handle = wintypes.HWND(hwnd) if hwnd else None
ok = u.RegisterHotKey(handle, 1, MOD_ALT | MOD_NOREPEAT, VK_A)
err = ctypes.windll.kernel32.GetLastError()
with open(out, "w", encoding="utf-8") as f:
    f.write(f"frozen={getattr(sys,'frozen',False)} hwnd={hwnd} "
            f"register_ok={bool(ok)} GetLastError={err}\n")
if ok:
    u.UnregisterHotKey(handle, 1)
QTimer.singleShot(100, app.quit)
app.exec()
