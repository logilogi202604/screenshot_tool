"""Annotation shapes drawn on top of the captured screenshot.

Every shape is an object so the overlay can keep an ordered list and support
undo by simply popping the last one. Coordinates are in the overlay's local
(logical) coordinate system; the overlay scales them when exporting.
"""
import math

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon


class Annotation:
    def __init__(self, color, width):
        self.color = QColor(color)
        self.width = width

    def draw(self, painter):  # pragma: no cover - overridden
        raise NotImplementedError


class RectAnnotation(Annotation):
    def __init__(self, color, width, start):
        super().__init__(color, width)
        self.start = QPoint(start)
        self.end = QPoint(start)

    def update_end(self, point):
        self.end = QPoint(point)

    def draw(self, painter):
        pen = QPen(self.color, self.width, Qt.SolidLine, Qt.SquareCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRect(self.start, self.end).normalized())


class EllipseAnnotation(Annotation):
    def __init__(self, color, width, start):
        super().__init__(color, width)
        self.start = QPoint(start)
        self.end = QPoint(start)

    def update_end(self, point):
        self.end = QPoint(point)

    def draw(self, painter):
        pen = QPen(self.color, self.width, Qt.SolidLine, Qt.SquareCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRect(self.start, self.end).normalized())


class ArrowAnnotation(Annotation):
    def __init__(self, color, width, start):
        super().__init__(color, width)
        self.start = QPoint(start)
        self.end = QPoint(start)

    def update_end(self, point):
        self.end = QPoint(point)

    def draw(self, painter):
        pen = QPen(self.color, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(self.start, self.end)

        # Arrow head sized relative to the line width.
        dx = self.start.x() - self.end.x()
        dy = self.start.y() - self.end.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        angle = math.atan2(dy, dx)
        head = max(12, self.width * 4)
        spread = math.pi / 7
        p1 = QPoint(
            round(self.end.x() + head * math.cos(angle + spread)),
            round(self.end.y() + head * math.sin(angle + spread)),
        )
        p2 = QPoint(
            round(self.end.x() + head * math.cos(angle - spread)),
            round(self.end.y() + head * math.sin(angle - spread)),
        )
        painter.setBrush(self.color)
        painter.setPen(QPen(self.color, 1))
        painter.drawPolygon(QPolygon([self.end, p1, p2]))


class PenAnnotation(Annotation):
    """Freehand stroke captured as a polyline of points."""

    def __init__(self, color, width, start):
        super().__init__(color, width)
        self.points = [QPoint(start)]

    def add_point(self, point):
        self.points.append(QPoint(point))

    def draw(self, painter):
        pen = QPen(self.color, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if len(self.points) == 1:
            painter.drawPoint(self.points[0])
        else:
            painter.drawPolyline(QPolygon(self.points))


class MosaicAnnotation:
    """Pixelated rectangle. Samples from a pre-blurred copy of the screenshot.

    `mosaic_pix` is the whole screenshot already pixelated at device resolution
    (dpr=1); `dpr` maps the logical rect to that pixmap's device pixels.
    """

    def __init__(self, mosaic_pix, dpr, start):
        self.mosaic_pix = mosaic_pix
        self.dpr = dpr
        self.start = QPoint(start)
        self.end = QPoint(start)

    def update_end(self, point):
        self.end = QPoint(point)

    def draw(self, painter):
        r = QRect(self.start, self.end).normalized()
        if r.width() < 1 or r.height() < 1:
            return
        src = QRectF(
            r.left() * self.dpr, r.top() * self.dpr,
            r.width() * self.dpr, r.height() * self.dpr,
        )
        smooth = painter.testRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(QRectF(r), self.mosaic_pix, src)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, smooth)


class NumberAnnotation:
    """A numbered badge (filled circle with a number), placed by a single click."""

    def __init__(self, color, number, pos, radius):
        self.color = QColor(color)
        self.number = number
        self.pos = QPoint(pos)
        self.radius = radius

    def draw(self, painter):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(self.color)
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawEllipse(self.pos, self.radius, self.radius)
        font = QFont()
        font.setPixelSize(max(10, int(self.radius * 1.1)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("white")))
        rect = QRectF(
            self.pos.x() - self.radius, self.pos.y() - self.radius,
            self.radius * 2, self.radius * 2,
        )
        painter.drawText(rect, int(Qt.AlignCenter), str(self.number))


class TextAnnotation(Annotation):
    MARGIN = 3  # keep in sync with the inline editor's document margin

    def __init__(self, color, font_size, pos, text):
        super().__init__(color, 1)
        self.font_size = font_size
        self.pos = QPoint(pos)
        self.text = text

    def bounding_rect(self):
        """Clickable rect of the rendered text (logical coords), with padding."""
        font = QFont()
        font.setPixelSize(self.font_size)
        fm = QFontMetrics(font)
        r = fm.boundingRect(
            QRect(0, 0, 10000, 10000),
            int(Qt.AlignLeft | Qt.AlignTop),
            self.text or " ",
        )
        r.translate(self.pos.x() + self.MARGIN, self.pos.y() + self.MARGIN)
        return r.adjusted(-4, -3, 4, 3)

    def draw(self, painter):
        font = QFont()
        font.setPixelSize(self.font_size)
        painter.setFont(font)
        painter.setPen(QPen(self.color))
        painter.setBrush(Qt.NoBrush)
        rect = QRectF(
            self.pos.x() + self.MARGIN,
            self.pos.y() + self.MARGIN,
            10000,
            10000,
        )
        painter.drawText(rect, int(Qt.AlignLeft | Qt.AlignTop), self.text)
