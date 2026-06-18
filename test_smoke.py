"""Offscreen smoke test for the core rendering logic (no hotkey/tray)."""
import os
import sys
import tempfile

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from overlay import ScreenshotOverlay
from annotations import ArrowAnnotation, RectAnnotation, TextAnnotation

app = QApplication(sys.argv)

DPR = 1.25
# Simulate a grabbed screenshot: 200x100 device px at 1.25 scale -> 160x80 logical.
pm = QPixmap(200, 100)
pm.fill(QColor("#3366cc"))
painter = QPainter(pm)
painter.fillRect(QRect(0, 0, 100, 100), QColor("#cc6633"))
painter.end()
pm.setDevicePixelRatio(DPR)

geo = QRect(0, 0, 160, 80)  # logical
overlay = ScreenshotOverlay(pm, geo, dict(DEFAULTS))

assert abs(overlay.dpr - DPR) < 1e-6, f"dpr mismatch: {overlay.dpr}"
assert overlay.screenshot.width() == 200, "screenshot should be raw device px"

# Make a selection and add annotations.
overlay.selection = QRect(20, 10, 80, 40)  # logical
overlay.has_selection = True
overlay.annotations.append(RectAnnotation(QColor("#ff0000"), 3, QPoint(25, 15)))
overlay.annotations[-1].update_end(QPoint(90, 45))
overlay.annotations.append(ArrowAnnotation(QColor("#00ff00"), 3, QPoint(30, 20)))
overlay.annotations[-1].update_end(QPoint(80, 40))
overlay.annotations.append(TextAnnotation(QColor("#ffffff"), 16, QPoint(30, 22), "Hello 你好"))

# Export and verify device-resolution output.
result = overlay.render_result()
exp_w = round(80 * DPR)
exp_h = round(40 * DPR)
assert result.width() == exp_w, f"width {result.width()} != {exp_w}"
assert result.height() == exp_h, f"height {result.height()} != {exp_h}"

tmp = os.path.join(tempfile.gettempdir(), "screenshot_smoke.png")
assert result.save(tmp), "save failed"
assert os.path.getsize(tmp) > 0, "empty file"

# Exercise the paint path in both states (no selection -> magnifier; with selection).
overlay.mouse_pos = QPoint(40, 30)
saved_sel = overlay.has_selection
overlay.has_selection = False
overlay.grab()                # triggers paintEvent + magnifier
overlay.has_selection = saved_sel
overlay.reposition_toolbar()
overlay.toolbar.show()
overlay.grab()                # triggers paintEvent + annotations + handles

# Undo should drop the last annotation.
n = len(overlay.annotations)
overlay.undo()
assert len(overlay.annotations) == n - 1

print("SMOKE OK:",
      f"result={result.width()}x{result.height()}",
      f"saved={tmp}",
      f"annotations_left={len(overlay.annotations)}")
