"""Generate app.ico for the packaged executable (same look as the tray icon)."""
import sys

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)


def draw(size):
    s = size
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    u = s / 64.0
    p.setBrush(QColor("#007aff"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(6 * u, 14 * u, 52 * u, 40 * u), 8 * u, 8 * u)
    p.setBrush(QColor("#cfe8ff"))
    p.drawRoundedRect(QRectF(24 * u, 8 * u, 16 * u, 10 * u), 3 * u, 3 * u)
    p.setBrush(QColor("white"))
    p.drawEllipse(QRectF(22 * u, 26 * u, 20 * u, 20 * u))
    p.setBrush(QColor("#007aff"))
    p.drawEllipse(QRectF(28 * u, 32 * u, 8 * u, 8 * u))
    p.end()
    return pm


icon = draw(256)
ok = icon.save(r"D:\code\screenshot_tool\app.ico", "ICO")
print("ico saved:", ok)
sys.exit(0 if ok else 1)
