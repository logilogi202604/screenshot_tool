"""Verify the virtual-desktop grab against what the OS itself reports.

The dimension checks need a real display (not offscreen) because they grab
actual screens. Run it on each machine you deploy to — the bug it guards against
only appears with more than one monitor, so a single-screen pass proves nothing
about a multi-screen box.

The _blit check at the top is synthetic and runs anywhere. It exists because the
first version of this test only compared sizes, and that let a compositing bug
through: every screen was painted at size/devicePixelRatio, so the canvas had
the right dimensions while each screen sat shrunk in a corner of its slot with
white seams between them. Dimensions alone do not prove the pixels are right.
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
app = QApplication(sys.argv)

from capture import (  # noqa: E402  (needs the app)
    _blit,
    _grab_screens,
    grab_virtual_desktop,
    native_screen_rects,
)


def is_red(c):
    return c.red() > 200 and c.green() < 100 and c.blue() < 100


# --- synthetic: a tagged pixmap must land at its raw pixel size --------------
for dpr in (1.0, 1.25, 2.0):
    src = QPixmap(400, 200)
    src.fill(QColor("red"))
    src.setDevicePixelRatio(dpr)
    canvas = QPixmap(900, 500)
    canvas.fill(QColor("white"))
    painter = QPainter(canvas)
    _blit(painter, src, 0, 0)
    painter.end()
    img = canvas.toImage()
    w = max((x for x in range(900) if is_red(img.pixelColor(x, 0))), default=-1) + 1
    h = max((y for y in range(500) if is_red(img.pixelColor(0, y))), default=-1) + 1
    assert (w, h) == (400, 200), f"dpr={dpr}: blit covered {w}x{h}, expected 400x200"
    # The source must not be mutated — it is the caller's screen grab.
    assert src.devicePixelRatio() == dpr, "blit changed the source pixmap's ratio"
print("BLIT OK: raw pixel size preserved at dpr 1.0 / 1.25 / 2.0")

# --- real screens ------------------------------------------------------------
screens = QGuiApplication.screens()
primary = QGuiApplication.primaryScreen()
print(f"\nscreens={len(screens)}  primary dpr={primary.devicePixelRatio()}")
for s in screens:
    g = s.geometry()
    print(f"  {s.name()!r:24s} qt_geo={g.x()},{g.y()} {g.width()}x{g.height()}  dpr={s.devicePixelRatio()}")

pm, logical = grab_virtual_desktop()
assert pm is not None and logical is not None, "grab_virtual_desktop returned nothing"
dpr = pm.devicePixelRatio()
print(f"\ncomposite = {pm.width()}x{pm.height()} px, dpr={dpr}")
print(f"logical   = {logical.x()},{logical.y()} {logical.width()}x{logical.height()}")

# The window rect must cover the whole image once scaled back up, or the overlay
# would clip the desktop it is supposed to cover.
assert round(logical.width() * dpr) >= pm.width() - 1, \
    f"logical width {logical.width()}x{dpr} < canvas {pm.width()}"
assert round(logical.height() * dpr) >= pm.height() - 1, \
    f"logical height {logical.height()}x{dpr} < canvas {pm.height()}"

if len(screens) > 1:
    # One fresh grab, reused — re-grabbing per assertion both wastes captures and
    # risks comparing two different moments of a live desktop.
    grabs = _grab_screens(screens)
    rects = native_screen_rects(grabs)
    for screen, rect, screen_pm in rects:
        assert (screen_pm.width(), screen_pm.height()) == (rect.width(), rect.height()), \
            f"{screen.name()}: grab {screen_pm.size()} != rect {rect.size()}"
    covered = sum(r.width() * r.height() for _s, r, _p in rects)
    assert pm.width() * pm.height() >= covered * 0.9, \
        f"canvas {pm.width()}x{pm.height()} too small for {len(screens)} screens"
    print(f"LAYOUT OK: {len(screens)} screens, {covered} px covered by a {pm.width()*pm.height()} px canvas")

if sys.platform == "win32":
    import ctypes

    u = ctypes.windll.user32
    want = (u.GetSystemMetrics(78), u.GetSystemMetrics(79))
    print(f"win32 virtual desktop = {want[0]}x{want[1]}")
    # This is the assertion that would have failed before the fix: Qt's
    # virtualGeometry reported 4608x2614 (x1.25 -> 5760x3268) where Windows
    # reports 5120x2902.
    assert (pm.width(), pm.height()) == want, \
        f"composite {pm.width()}x{pm.height()} != win32 {want[0]}x{want[1]}"
    print("WIN32 MATCH OK: composite equals the real virtual desktop")

print("\nCAPTURE OK")
