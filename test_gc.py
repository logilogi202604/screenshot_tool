"""Prove that an unreferenced TrayApp is garbage-collected (the root cause)."""
import gc
import sys
import weakref

from PySide6.QtWidgets import QApplication

from config import load_config
import main as M

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
cfg = load_config()

# Reproduce the BUGGY pattern: create TrayApp, keep no strong reference.
obj = M.TrayApp(app, cfg)
ref = weakref.ref(obj)
hk_ref = weakref.ref(obj.hotkey)
del obj
gc.collect()

print("BUGGY (no strong ref): TrayApp alive after gc?", ref() is not None)
print("BUGGY (no strong ref): GlobalHotkey alive after gc?", hk_ref() is not None)

# Now the FIX: keep a strong reference, GC must NOT collect it.
keep = M.TrayApp(app, cfg)
ref2 = weakref.ref(keep)
gc.collect()
print("FIXED (strong ref):    TrayApp alive after gc?", ref2() is not None)
