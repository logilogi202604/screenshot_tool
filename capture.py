"""Grab the whole virtual desktop as one pixmap — correctly, on every platform.

The obvious one-liner, `primaryScreen().grabWindow(0, *virtualGeometry())`, is
wrong on Windows the moment there is more than one monitor and the scale factor
is fractional: Qt then reports screen **positions** in device pixels but screen
**sizes** in logical pixels, so `virtualGeometry()` is a mixed-unit rect. On the
four-monitor 125% machine this project is used with, Qt reported a 4608x2614
desktop where Windows reports 5120x2902 — a 640x366 overshoot — and every
monitor after the first was composited at the wrong offset.

Per-screen `grabWindow(0)` has no such problem: it returns the true native
pixmap for that screen (measured 2560x1440 on all four). So the desktop is
composited from per-screen grabs instead of asking for one big rect.

Window *placement* is not affected and needs no workaround — Qt's widget
coordinates really are logical. Verified by setting a frameless window to
native/dpr and reading the result back through GetWindowRect: it landed exactly
on the real desktop, while feeding it `virtualGeometry()` overshot by the same
640x366.
"""
import math
import sys

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap


def _win32_virtual_size():
    """(width, height) of the virtual desktop in real device pixels, or None.

    Used only to sanity-check the interpretation chosen in native_screen_rects();
    it needs no monitor-to-QScreen matching, just two metrics.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # The process is already DPI-aware by the time Qt is up, so these come
        # back in device pixels.
        return user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    except Exception:
        return None


def _blit(painter, pixmap, x, y):
    """Draw `pixmap` at raw pixel size, ignoring its devicePixelRatio.

    QPainter.drawPixmap() honours the *source* ratio and paints it at
    size/ratio: a 2560x1440 grab tagged 1.25 lands as 2048x1152, which silently
    shrank every screen and left white seams between them. The composite is a
    raw-pixel canvas, so the ratio has to be neutralised before painting.
    """
    scratch = QPixmap(pixmap)          # shallow, refcounted copy
    scratch.setDevicePixelRatio(1.0)
    painter.drawPixmap(int(x), int(y), scratch)


def _grab_screens(screens):
    """[(screen, pixmap)] with the null grabs dropped.

    A screen can fail to grab (permissions, a display going away mid-capture);
    keeping a null pixmap would silently contribute a zero-sized rect and skew
    the union that the Windows heuristic below is judged against.
    """
    out = []
    for s in screens:
        pm = s.grabWindow(0)
        if pm is not None and not pm.isNull():
            out.append((s, pm))
    return out


def native_screen_rects(grabs):
    """[(screen, QRect device px, pixmap)] — takes the grabs, never re-grabs.

    Sizes always come from the screen's own grab, which is authoritative. Only
    the *position* is ambiguous, and only on Windows: Qt hands back device-pixel
    positions there but logical ones elsewhere. Both readings are computed and
    the one matching the OS's own virtual-desktop size wins, so a future Qt that
    fixes the inconsistency keeps working.
    """
    def build(positions_are_native):
        rects = []
        for screen, pm in grabs:
            top_left = screen.geometry().topLeft()
            if not positions_are_native:
                dpr = screen.devicePixelRatio() or 1.0
                top_left = top_left * dpr
            rects.append((screen, QRect(top_left, pm.size()), pm))
        return rects

    if sys.platform != "win32":
        return build(False)

    candidates = [build(True), build(False)]
    expected = _win32_virtual_size()
    if expected is None:
        return candidates[0]

    def error(rects):
        union = QRect()
        for _s, r, _pm in rects:
            union = union.united(r)
        return abs(union.width() - expected[0]) + abs(union.height() - expected[1])

    return min(candidates, key=error)


def _uniform_dpr(grabs):
    """The shared devicePixelRatio, or None when the screens disagree."""
    ratios = {round(s.devicePixelRatio() or 1.0, 4) for s, _pm in grabs}
    return ratios.pop() if len(ratios) == 1 else None


def _composite_uniform(grabs, dpr):
    """All screens share one ratio: lay the raw grabs out in device pixels.

    This is the exact path — no resampling, and the canvas matches what the OS
    reports for the virtual desktop (verified against GetSystemMetrics).
    """
    rects = native_screen_rects(grabs)
    union = QRect()
    for _s, r, _pm in rects:
        union = union.united(r)
    if union.isEmpty():
        return None, None

    canvas = QPixmap(union.size())
    canvas.fill()
    painter = QPainter(canvas)
    for _s, rect, pm in rects:
        _blit(painter, pm, rect.x() - union.x(), rect.y() - union.y())
    painter.end()
    canvas.setDevicePixelRatio(dpr)

    # Round outward so the overlay window never falls short of the real desktop.
    logical = QRect(
        math.floor(union.x() / dpr), math.floor(union.y() / dpr),
        math.ceil(union.width() / dpr), math.ceil(union.height() / dpr),
    )
    return canvas, logical


def _composite_mixed(grabs):
    """Screens disagree on scale: composite in logical space at the finest ratio.

    There is no single ratio that describes a mixed canvas, so the coarser
    screens get upscaled to the finest one. Slightly soft on those screens, but
    geometrically correct — which matters more, because getting this wrong puts
    a MacBook's external monitor *inside* the built-in screen's rectangle.
    """
    scale = max((s.devicePixelRatio() or 1.0) for s, _pm in grabs)

    logical_rects = []
    for screen, pm in grabs:
        geo = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        top_left = geo.topLeft()
        if sys.platform == "win32":
            # Positions come back in device pixels here; see native_screen_rects.
            top_left = top_left / dpr
        # The size from grabWindow is authoritative; geometry()'s may be logical
        # already, so derive logical size from the pixels we actually hold.
        logical_rects.append(
            (QRect(top_left, QSize(round(pm.width() / dpr), round(pm.height() / dpr))), pm)
        )

    union = QRect()
    for r, _pm in logical_rects:
        union = union.united(r)
    if union.isEmpty():
        return None, None

    canvas = QPixmap(QSize(round(union.width() * scale), round(union.height() * scale)))
    canvas.fill()
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    for rect, pm in logical_rects:
        target = QRect(
            round((rect.x() - union.x()) * scale), round((rect.y() - union.y()) * scale),
            round(rect.width() * scale), round(rect.height() * scale),
        )
        scratch = QPixmap(pm)
        scratch.setDevicePixelRatio(1.0)
        painter.drawPixmap(target, scratch)
    painter.end()
    canvas.setDevicePixelRatio(scale)
    return canvas, union


def grab_virtual_desktop():
    """Return (pixmap in device pixels with dpr set, logical QRect to place it at).

    The pair matches what ScreenshotOverlay expects: raw device pixels for the
    image, logical coordinates for the window.
    """
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    if not screens or primary is None:
        return None, None

    # Single screen is the overwhelmingly common case and has no offset maths to
    # get wrong; take the direct path so nothing can regress there.
    if len(screens) == 1:
        pm = primary.grabWindow(0)
        if pm is None or pm.isNull():
            return None, None
        return pm, primary.geometry()

    grabs = _grab_screens(screens)
    if not grabs:
        return None, None
    if len(grabs) == 1:
        screen, pm = grabs[0]
        return pm, screen.geometry()

    dpr = _uniform_dpr(grabs)
    if dpr is not None:
        return _composite_uniform(grabs, dpr)
    return _composite_mixed(grabs)
