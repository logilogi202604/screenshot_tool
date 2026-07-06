"""Full-screen overlay: region selection + annotation, WeChat-style."""
import os
from datetime import datetime

from PySide6.QtCore import (
    QMimeData,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QStandardPaths,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QRegion,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QTextEdit,
    QWidget,
)

from annotations import (
    ArrowAnnotation,
    EllipseAnnotation,
    MosaicAnnotation,
    NumberAnnotation,
    PenAnnotation,
    RectAnnotation,
    TextAnnotation,
)
from toolbar import Toolbar

HANDLE = 8           # half-size of resize handles (logical px)
DIM_ALPHA = 130      # darkness of the area outside the selection
MIN_SELECTION = 5    # ignore accidental tiny drags


class TextEditor(QTextEdit):
    """Borderless inline editor used when placing text annotations."""

    committed = Signal()
    cancelled = Signal()

    def __init__(self, color, font_size, parent=None):
        super().__init__(parent)
        # Remember the styling this text was created with, so the committed
        # annotation keeps it (important when re-editing an existing label).
        self.ann_color = QColor(color)
        self.ann_font_size = font_size
        self.setFrameStyle(0)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(TextAnnotation.MARGIN)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        font = QFont()
        font.setPixelSize(font_size)
        self.setFont(font)
        self.setStyleSheet(
            f"QTextEdit {{ background: transparent; color: {color.name()};"
            f" border: 1px dashed rgba(255,255,255,160); }}"
        )
        self.textChanged.connect(self._autosize)
        self._autosize()

    def _autosize(self):
        doc = self.document()
        doc.setTextWidth(-1)
        w = int(doc.idealWidth()) + 16
        h = int(doc.size().height()) + 4
        self.setFixedSize(max(w, 30), max(h, self.fontMetrics().height() + 8))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        # Enter commits; Shift+Enter inserts a newline.
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.committed.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.committed.emit()


class ScreenshotOverlay(QWidget):
    finished = Signal()
    saved = Signal(str)  # emitted with the file path when a capture is auto-saved

    def __init__(self, pixmap, geometry, config):
        super().__init__()
        self.config = config
        self.dpr = pixmap.devicePixelRatio() or 1.0
        # Treat the grabbed pixmap as raw device pixels so all DPI math is explicit.
        pixmap.setDevicePixelRatio(1.0)
        self.screenshot = pixmap
        self.image = pixmap.toImage()  # cached for the magnifier's colour readout
        self.mosaic_pix = self._build_mosaic_pix()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setGeometry(geometry)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        # selection state (widget-local logical coords)
        self.selection = QRect()
        self.has_selection = False
        self.selecting = False
        self.sel_origin = QPoint()
        self.moving = False
        self.resizing = False
        self.resize_handle = None
        self.move_offset = QPoint()
        self.mouse_pos = QPoint()

        # annotation state
        self.annotations = []
        self.current = None
        self.tool = None
        self.color = QColor(config.get("default_color", "#ff3b30"))
        self.pen_width = config.get("default_width", 4)
        self.font_size = config.get("default_font_size", 18)
        self.text_editor = None
        self._editing_original = None  # label being re-edited; restored on cancel
        self.dragging_text = None
        self.text_drag_offset = QPoint()

        self.toolbar = Toolbar(self)
        self._wire_toolbar()
        self.toolbar.hide()

        # macOS hides Qt.Tool windows when the app deactivates (e.g. Cmd+Tab
        # away). That hide bypasses closeEvent, so `finished` would never fire
        # and the tray app would stay "busy" forever — hideEvent treats such a
        # hide as cancel. `dialog_open` marks the save dialog's intentional hide.
        self.dialog_open = False
        self._closed = False
        self.native_check_error = None  # last native_on_screen() failure, if any

    def _build_mosaic_pix(self):
        """Whole screenshot pixelated once (device px) so mosaic strokes are cheap."""
        w = self.screenshot.width()
        h = self.screenshot.height()
        block = 12
        sw = max(1, w // block)
        sh = max(1, h // block)
        small = self.screenshot.scaled(
            sw, sh, Qt.IgnoreAspectRatio, Qt.FastTransformation
        )
        return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)

    # ------------------------------------------------------------------ setup
    def _wire_toolbar(self):
        tb = self.toolbar
        tb.tool_changed.connect(self._set_tool)
        tb.color_changed.connect(self._set_color)
        tb.width_changed.connect(self._set_width)
        tb.undo_requested.connect(self.undo)
        tb.save_requested.connect(self.save_to_file)
        tb.copy_requested.connect(self.copy_to_clipboard_and_close)
        tb.confirm_requested.connect(self.copy_to_clipboard_and_close)
        tb.cancel_requested.connect(self.cancel)

    def _set_tool(self, tool):
        self.tool = tool
        self.commit_text_editor()
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)
        self.update()

    def _set_color(self, color):
        self.color = QColor(color)
        if self.text_editor:
            self.text_editor.ann_color = QColor(color)
            self.text_editor.setStyleSheet(
                f"QTextEdit {{ background: transparent; color: {self.color.name()};"
                f" border: 1px dashed rgba(255,255,255,160); }}"
            )

    def _set_width(self, width):
        self.pen_width = width

    # -------------------------------------------------------------- geometry
    def sel_rect(self):
        return self.selection.normalized()

    def handle_points(self, rect):
        cx = rect.center().x()
        cy = rect.center().y()
        return {
            "tl": rect.topLeft(),
            "tr": rect.topRight(),
            "bl": rect.bottomLeft(),
            "br": rect.bottomRight(),
            "t": QPoint(cx, rect.top()),
            "b": QPoint(cx, rect.bottom()),
            "l": QPoint(rect.left(), cy),
            "r": QPoint(rect.right(), cy),
        }

    def handle_at(self, pos):
        if not self.has_selection:
            return None
        for name, pt in self.handle_points(self.sel_rect()).items():
            if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                return name
        return None

    def cursor_for_handle(self, name):
        return {
            "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
            "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        }.get(name, Qt.ArrowCursor)

    def clamp(self, pos):
        x = min(max(pos.x(), 0), self.width() - 1)
        y = min(max(pos.y(), 0), self.height() - 1)
        return QPoint(x, y)

    def text_at(self, pos):
        """Topmost text annotation under pos, or None."""
        for a in reversed(self.annotations):
            if isinstance(a, TextAnnotation) and a.bounding_rect().contains(pos):
                return a
        return None

    def _clamp_text_pos(self, pos, ann):
        """Keep a dragged text label inside the selection."""
        r = self.sel_rect()
        br = ann.bounding_rect()
        x = min(max(pos.x(), r.left()), max(r.left(), r.right() - br.width()))
        y = min(max(pos.y(), r.top()), max(r.top(), r.bottom() - br.height()))
        return QPoint(x, y)

    # ----------------------------------------------------------- mouse input
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        self.mouse_pos = pos

        if not self.has_selection:
            self.selecting = True
            self.sel_origin = pos
            self.selection = QRect(pos, pos)
            self.update()
            return

        # Clicking existing text (in idle or text mode) starts dragging it.
        if self.tool in (None, "text"):
            hit = self.text_at(pos)
            if hit is not None:
                self.commit_text_editor()
                self.dragging_text = hit
                self.text_drag_offset = pos - hit.pos
                self.setCursor(Qt.ClosedHandCursor)
                return

        if self.tool:
            self.commit_text_editor()
            self.start_annotation(pos)
            return

        # No active tool: move or resize the existing selection.
        handle = self.handle_at(pos)
        if handle:
            self.resizing = True
            self.resize_handle = handle
        elif self.sel_rect().contains(pos):
            self.moving = True
            self.move_offset = pos - self.sel_rect().topLeft()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        self.mouse_pos = pos

        if self.dragging_text is not None:
            self.dragging_text.pos = self._clamp_text_pos(
                pos - self.text_drag_offset, self.dragging_text
            )
            self.update()
            return
        if self.selecting:
            self.selection = QRect(self.sel_origin, self.clamp(pos))
            self.update()
            return
        if self.resizing:
            self._resize_selection(self.clamp(pos))
            self.reposition_toolbar()
            self.update()
            return
        if self.moving:
            self._move_selection(pos)
            self.reposition_toolbar()
            self.update()
            return
        if self.current is not None:
            self._update_annotation(self.clamp(pos))
            self.update()
            return

        # Hover feedback: hand over text, handle/move cursors over the selection.
        if self.has_selection and self.tool in (None, "text"):
            if self.text_at(pos):
                self.setCursor(Qt.OpenHandCursor)
            elif self.tool == "text":
                self.setCursor(Qt.CrossCursor)
            else:
                handle = self.handle_at(pos)
                if handle:
                    self.setCursor(self.cursor_for_handle(handle))
                elif self.sel_rect().contains(pos):
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
        elif not self.has_selection:
            self.update()  # keep the magnifier following the cursor

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.dragging_text is not None:
            self.dragging_text = None
            self.setCursor(Qt.OpenHandCursor)
            self.update()
            return
        if self.selecting:
            self.selecting = False
            rect = self.sel_rect()
            if rect.width() >= MIN_SELECTION and rect.height() >= MIN_SELECTION:
                self.has_selection = True
                self.selection = rect
                self.toolbar.show()
                self.reposition_toolbar()
                self.setCursor(Qt.ArrowCursor)
            else:
                self.selection = QRect()
            self.update()
            return

        if self.current is not None:
            self.finish_annotation()
        self.moving = False
        self.resizing = False
        self.resize_handle = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint()
        if not self.has_selection:
            return
        # Double-click on a text label re-opens it for editing.
        if self.tool in (None, "text"):
            hit = self.text_at(pos)
            if hit is not None:
                self.edit_text(hit)
                return
        # Double-click on empty area (idle) confirms & copies.
        if not self.tool and self.sel_rect().contains(pos):
            self.copy_to_clipboard_and_close()

    def _resize_selection(self, pos):
        r = QRect(self.selection.normalized())
        h = self.resize_handle
        if "t" in h:
            r.setTop(pos.y())
        if "b" in h:
            r.setBottom(pos.y())
        if "l" in h:
            r.setLeft(pos.x())
        if "r" in h:
            r.setRight(pos.x())
        self.selection = r

    def _move_selection(self, pos):
        rect = self.sel_rect()
        new_tl = pos - self.move_offset
        x = min(max(new_tl.x(), 0), self.width() - rect.width())
        y = min(max(new_tl.y(), 0), self.height() - rect.height())
        self.selection = QRect(QPoint(x, y), rect.size())

    # ------------------------------------------------------------ annotation
    def start_annotation(self, pos):
        pos = self._clamp_to_selection(pos)
        if self.tool == "rect":
            self.current = RectAnnotation(self.color, self.pen_width, pos)
        elif self.tool == "ellipse":
            self.current = EllipseAnnotation(self.color, self.pen_width, pos)
        elif self.tool == "arrow":
            self.current = ArrowAnnotation(self.color, self.pen_width, pos)
        elif self.tool == "pen":
            self.current = PenAnnotation(self.color, self.pen_width, pos)
        elif self.tool == "mosaic":
            self.current = MosaicAnnotation(self.mosaic_pix, self.dpr, pos)
        elif self.tool == "number":
            self.place_number(pos)
        elif self.tool == "text":
            self.open_text_editor(pos)

    def _update_annotation(self, pos):
        pos = self._clamp_to_selection(pos)
        if isinstance(self.current, PenAnnotation):
            self.current.add_point(pos)
        else:
            self.current.update_end(pos)

    def finish_annotation(self):
        if self.current is None:
            return
        # Drop degenerate zero-size shapes (a click without a drag).
        keep = True
        if isinstance(
            self.current,
            (RectAnnotation, EllipseAnnotation, ArrowAnnotation, MosaicAnnotation),
        ):
            if (self.current.start - self.current.end).manhattanLength() < 3:
                keep = False
        if keep:
            self.annotations.append(self.current)
        self.current = None

    def place_number(self, pos):
        """Drop an auto-incrementing numbered badge at the click point."""
        pos = self._clamp_to_selection(pos)
        n = sum(1 for a in self.annotations if isinstance(a, NumberAnnotation)) + 1
        radius = int(10 + self.pen_width * 1.5)
        self.annotations.append(NumberAnnotation(self.color, n, pos, radius))
        self.current = None
        self.update()

    def _clamp_to_selection(self, pos):
        r = self.sel_rect()
        x = min(max(pos.x(), r.left()), r.right())
        y = min(max(pos.y(), r.top()), r.bottom())
        return QPoint(x, y)

    def undo(self):
        if self.annotations:
            self.annotations.pop()
            self.update()

    # --------------------------------------------------------------- text io
    def open_text_editor(self, pos, text="", color=None, font_size=None):
        editor = TextEditor(color or self.color, font_size or self.font_size, self)
        editor.move(pos)
        if text:
            editor.setPlainText(text)
            editor.moveCursor(QTextCursor.End)
        editor.committed.connect(self.commit_text_editor)
        editor.cancelled.connect(self.cancel_text_editor)
        editor.show()
        editor.setFocus()
        self.text_editor = editor

    def edit_text(self, ann):
        """Re-open an existing text label for editing, preserving its style."""
        self.commit_text_editor()
        idx = self.annotations.index(ann)
        self.annotations.remove(ann)
        # Stash the original (with its z-position) so Esc restores it — otherwise
        # cancelling an edit silently deletes the label (commit replaces it, so
        # commit clears this).
        self._editing_original = (idx, ann)
        self.open_text_editor(ann.pos, text=ann.text, color=ann.color,
                              font_size=ann.font_size)
        self.update()

    def commit_text_editor(self):
        editor = self.text_editor
        if editor is None:
            return
        self.text_editor = None
        self._editing_original = None
        text = editor.toPlainText().strip()
        pos = editor.pos()
        color = editor.ann_color
        font_size = editor.ann_font_size
        editor.deleteLater()
        if text:
            self.annotations.append(TextAnnotation(color, font_size, pos, text))
        self.update()

    def cancel_text_editor(self):
        if self.text_editor is not None:
            editor = self.text_editor
            self.text_editor = None
            editor.deleteLater()
            if self._editing_original is not None:
                idx, ann = self._editing_original
                self.annotations.insert(min(idx, len(self.annotations)), ann)
                self._editing_original = None
            self.update()

    # ----------------------------------------------------------------- paint
    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.screenshot)

        if self.has_selection or self.selecting:
            sel = self.sel_rect()
            outside = QRegion(self.rect()).subtracted(QRegion(sel))
            p.setClipRegion(outside)
            p.fillRect(self.rect(), QColor(0, 0, 0, DIM_ALPHA))
            p.setClipping(False)

            # selection border
            p.setPen(QPen(QColor("#1e90ff"), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))

            # annotations live strictly inside the selection
            p.save()
            p.setClipRect(sel)
            p.setRenderHint(QPainter.Antialiasing, True)
            for a in self.annotations:
                a.draw(p)
            if self.current is not None:
                self.current.draw(p)
            p.restore()

            if self.has_selection:
                self._draw_handles(p, sel)
            self._draw_size_label(p, sel)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, DIM_ALPHA))

        # magnifier + crosshair while picking the region
        if not self.has_selection:
            self._draw_magnifier(p, self.mouse_pos)

    def _draw_handles(self, p, sel):
        p.setPen(QPen(QColor("#1e90ff"), 1))
        p.setBrush(QColor("white"))
        for pt in self.handle_points(sel).values():
            p.drawRect(QRect(pt.x() - 3, pt.y() - 3, 6, 6))

    def _draw_size_label(self, p, sel):
        text = f"{sel.width()} × {sel.height()}"
        font = QFont("Microsoft YaHei", 9)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 12
        th = fm.height() + 6
        x = sel.left()
        y = sel.top() - th - 4
        if y < 0:
            y = sel.top() + 4
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRect(x, y, tw, th)
        p.setPen(QColor("white"))
        p.drawText(QRect(x, y, tw, th), Qt.AlignCenter, text)

    def _draw_magnifier(self, p, pos):
        if pos.isNull():
            return
        # crosshair across the whole screen
        p.setPen(QPen(QColor(0, 174, 255, 160), 1))
        p.drawLine(0, pos.y(), self.width(), pos.y())
        p.drawLine(pos.x(), 0, pos.x(), self.height())

        # zoomed loupe sampled from the raw screenshot (device pixels)
        sample = 14  # half-width of the sampled square in device px
        zoom = 6
        box = sample * 2 * zoom
        cx = int(pos.x() * self.dpr)
        cy = int(pos.y() * self.dpr)
        src = QRect(cx - sample, cy - sample, sample * 2, sample * 2)

        bx = pos.x() + 18
        by = pos.y() + 18
        if bx + box > self.width():
            bx = pos.x() - box - 18
        if by + box + 34 > self.height():
            by = pos.y() - box - 34 - 18

        target = QRect(bx, by, box, box)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawPixmap(target, self.screenshot, src)
        p.setPen(QPen(QColor("white"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(target)
        # centre marker
        p.setPen(QPen(QColor(0, 174, 255), 1))
        p.drawLine(bx, by + box // 2, bx + box, by + box // 2)
        p.drawLine(bx + box // 2, by, bx + box // 2, by + box)

        # coordinate + colour readout
        color = self.image.pixelColor(min(max(cx, 0), self.image.width() - 1),
                                      min(max(cy, 0), self.image.height() - 1))
        info = f"({pos.x()}, {pos.y()})\n#{color.red():02X}{color.green():02X}{color.blue():02X}"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 200))
        p.drawRect(bx, by + box, box, 34)
        p.setPen(QColor("white"))
        p.setFont(QFont("Consolas", 8))
        p.drawText(QRect(bx + 4, by + box, box - 8, 34), Qt.AlignVCenter | Qt.AlignLeft, info)

    # --------------------------------------------------------------- toolbar
    def reposition_toolbar(self):
        if not self.has_selection:
            return
        sel = self.sel_rect()
        self.toolbar.adjustSize()
        tw = self.toolbar.width()
        th = self.toolbar.height()
        x = sel.right() - tw
        x = max(0, min(x, self.width() - tw))
        y = sel.bottom() + 8
        if y + th > self.height():
            y = sel.top() - th - 8
        if y < 0:
            y = sel.top() + 8  # selection fills the screen: float inside
        self.toolbar.move(x, y)

    # ----------------------------------------------------------- export/exit
    def render_result(self):
        """Composite the selected region + annotations at full device resolution."""
        sel = self.sel_rect()
        dpr = self.dpr
        src = QRect(
            round(sel.left() * dpr),
            round(sel.top() * dpr),
            max(1, round(sel.width() * dpr)),
            max(1, round(sel.height() * dpr)),
        )
        out = self.screenshot.copy(src)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(dpr, dpr)
        painter.translate(-sel.topLeft())
        for a in self.annotations:
            a.draw(painter)
        painter.end()
        return out

    def copy_to_clipboard_and_close(self):
        if not self.has_selection:
            return
        self.commit_text_editor()
        result = self.render_result()
        clipboard = QApplication.clipboard()

        path = self._autosave(result) if self.config.get("autosave_on_copy", True) else None
        if path:
            # Image (for pasting into chats/editors) + file URL (so the terminal
            # can paste the path, and you can drag the file into Claude Code).
            mime = QMimeData()
            mime.setImageData(result.toImage())
            mime.setUrls([QUrl.fromLocalFile(path)])
            mime.setText(path)
            clipboard.setMimeData(mime)
            self.saved.emit(path)
        else:
            clipboard.setPixmap(result)
        self.close()

    def _autosave(self, pixmap):
        try:
            save_dir = self.config.get(
                "save_dir",
                QStandardPaths.writableLocation(QStandardPaths.PicturesLocation),
            )
            os.makedirs(save_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            path = os.path.join(save_dir, f"screenshot_{stamp}.png")
            return path if pixmap.save(path, "PNG") else None
        except (OSError, ValueError):  # ValueError: NUL byte in a corrupt path
            return None

    def save_to_file(self):
        if not self.has_selection:
            return
        self.commit_text_editor()
        result = self.render_result()
        pictures = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        save_dir = self.config.get("save_dir", pictures)
        try:
            os.makedirs(save_dir, exist_ok=True)
        except (OSError, ValueError):
            save_dir = pictures  # fall back to a writable location
            try:
                os.makedirs(save_dir, exist_ok=True)
            except (OSError, ValueError):
                save_dir = ""

        # The native dialog needs the overlay out of the way to avoid focus fights.
        self.dialog_open = True
        self.hide()
        try:
            default = os.path.join(save_dir, "screenshot.png") if save_dir else "screenshot.png"
            path, _ = QFileDialog.getSaveFileName(
                None, "保存截图", default, "PNG 图片 (*.png);;JPEG 图片 (*.jpg)"
            )
        finally:
            self.dialog_open = False
        if not path:
            self._restore_overlay()
            return
        if result.save(path):
            self.close()
        else:
            # Don't lose the capture: report and keep the overlay open to retry.
            self._restore_overlay()
            QMessageBox.warning(self, "保存失败", f"无法保存到：\n{path}\n请换一个位置重试。")

    def _restore_overlay(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def cancel(self):
        self.cancel_text_editor()
        self.close()

    # ------------------------------------------------------------- key input
    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape:
            self.cancel()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.copy_to_clipboard_and_close()
        elif key == Qt.Key_Z and (mods & Qt.ControlModifier):
            self.undo()
        elif key == Qt.Key_S and (mods & Qt.ControlModifier):
            self.save_to_file()
        elif key == Qt.Key_C and (mods & Qt.ControlModifier):
            self.copy_to_clipboard_and_close()
        else:
            super().keyPressEvent(event)

    def native_on_screen(self):
        """Whether the native window is genuinely ordered onto the screen.

        Qt's isVisible() only mirrors Qt's own state flag. When macOS pulls
        the window at the AppKit level without telling Qt — locking the screen
        mid-capture does exactly that — no hideEvent is delivered and
        isVisible() keeps answering True, so every Qt-side check is blind to
        it. Ask AppKit directly on macOS; on other platforms (or if the query
        fails) fall back to Qt's answer.
        """
        if QGuiApplication.platformName() != "cocoa":
            return self.isVisible()
        try:
            import ctypes

            import objc  # PyObjC — already present on macOS via pynput

            view = objc.objc_object(c_void_p=ctypes.c_void_p(int(self.winId())))
            win = view.window()
            return win is not None and bool(win.isVisible())
        except Exception as e:
            # Surfaced into app.log by TrayApp: a silent fallback here would
            # let the lock-screen zombie reappear with nothing to debug from.
            self.native_check_error = e
            return self.isVisible()

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._closed and not self.dialog_open:
            # Deferred so the hide that is part of close(), or one immediately
            # followed by a re-show, doesn't double-cancel.
            QTimer.singleShot(0, self._close_if_still_hidden)

    def _close_if_still_hidden(self):
        if not self._closed and not self.dialog_open and not self.isVisible():
            self.cancel()

    def closeEvent(self, event):
        self._closed = True
        self.finished.emit()
        super().closeEvent(event)
