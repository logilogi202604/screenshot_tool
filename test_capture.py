"""Real screen-grab test: capture the live screen, annotate, export a PNG."""
import os
import sys
import tempfile

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from overlay import ScreenshotOverlay
from annotations import (
    ArrowAnnotation,
    MosaicAnnotation,
    NumberAnnotation,
    RectAnnotation,
    TextAnnotation,
)

QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
app = QApplication(sys.argv)

screen = QGuiApplication.primaryScreen()
geo = screen.virtualGeometry()
print("virtual geometry:", geo, "dpr:", screen.devicePixelRatio())
pm = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
print("grabbed pixmap (device px):", pm.width(), "x", pm.height(),
      "reported dpr:", pm.devicePixelRatio())

overlay = ScreenshotOverlay(pm, geo, dict(DEFAULTS))

# Select a 600x400 region near the top-left (logical coords).
overlay.selection = QRect(40, 40, 600, 400)
overlay.has_selection = True
r = RectAnnotation(QColor("#ff3b30"), 4, QPoint(80, 80))
r.update_end(QPoint(400, 260))
overlay.annotations.append(r)
a = ArrowAnnotation(QColor("#34c759"), 5, QPoint(420, 300))
a.update_end(QPoint(220, 160))
overlay.annotations.append(a)
overlay.annotations.append(
    TextAnnotation(QColor("#007aff"), 28, QPoint(90, 300), "截图测试 OK")
)
# mosaic region
m = MosaicAnnotation(overlay.mosaic_pix, overlay.dpr, QPoint(330, 60))
m.update_end(QPoint(560, 180))
overlay.annotations.append(m)
# numbered badges
overlay.annotations.append(NumberAnnotation(QColor("#ff9500"), 1, QPoint(120, 130), 16))
overlay.annotations.append(NumberAnnotation(QColor("#5856d6"), 2, QPoint(470, 360), 16))

result = overlay.render_result()
out = os.path.join(tempfile.gettempdir(), "_capture_test.png")
result.save(out)
print("saved:", out, result.width(), "x", result.height())
