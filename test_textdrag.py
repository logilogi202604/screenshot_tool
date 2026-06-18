"""Offscreen test: text hit-testing, dragging, edit, and auto-save on copy."""
import os
import sys
import tempfile

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from overlay import ScreenshotOverlay
from annotations import TextAnnotation

app = QApplication(sys.argv)

pm = QPixmap(400, 300)
pm.fill(QColor("#444444"))
pm.setDevicePixelRatio(1.0)

cfg = dict(DEFAULTS)
save_dir = os.path.join(tempfile.gettempdir(), "shot_test_autosave")
cfg["save_dir"] = save_dir

ov = ScreenshotOverlay(pm, QRect(0, 0, 400, 300), cfg)
ov.selection = QRect(20, 20, 360, 260)
ov.has_selection = True

# place a text annotation
ann = TextAnnotation(QColor("#ffcc00"), 18, QPoint(100, 100), "拖我 drag me")
ov.annotations.append(ann)

# 1) hit-testing
inside = ann.bounding_rect().center()
assert ov.text_at(inside) is ann, "text_at should hit the label center"
assert ov.text_at(QPoint(350, 250)) is None, "text_at should miss empty area"

# 2) simulate a drag from inside to a new spot
ov.dragging_text = ann
ov.text_drag_offset = inside - ann.pos
new_cursor = QPoint(220, 180)
ann.pos = ov._clamp_text_pos(new_cursor - ov.text_drag_offset, ann)
ov.dragging_text = None
assert ann.pos != QPoint(100, 100), "text should have moved"
# stays inside selection
assert ov.sel_rect().contains(ann.bounding_rect().topLeft()), "text anchor inside selection"

# 3) clamp keeps it inside even when dragged far out
ann.pos = ov._clamp_text_pos(QPoint(99999, 99999), ann)
assert ov.sel_rect().contains(QPoint(ann.pos.x(), ann.pos.y())), "clamped inside"

# 4) edit preserves style
ov.edit_text(ann)
assert ov.text_editor is not None
assert ov.text_editor.ann_color.name() == QColor("#ffcc00").name()
assert ov.text_editor.ann_font_size == 18
ov.cancel_text_editor()

# 5) auto-save writes a real PNG
ann2 = TextAnnotation(QColor("#00ccff"), 18, QPoint(120, 120), "hi")
ov.annotations.append(ann2)
result = ov.render_result()
path = ov._autosave(result)
assert path and os.path.exists(path) and os.path.getsize(path) > 0, "autosave failed"

print("TEXTDRAG OK:", f"moved_to={ann.pos.x()},{ann.pos.y()}", f"saved={path}")
