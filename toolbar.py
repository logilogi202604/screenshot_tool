"""Floating annotation toolbar shown next to the selected region."""
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

# Preset palette (WeChat-ish): the first one is the default.
PALETTE = [
    "#ff3b30", "#ff9500", "#ffcc00", "#34c759",
    "#007aff", "#5856d6", "#000000", "#ffffff",
]

# (tool id, button label, tooltip)
TOOLS = [
    ("rect", "▭", "矩形"),
    ("ellipse", "○", "椭圆"),
    ("arrow", "↗", "箭头"),
    ("pen", "✎", "画笔"),
    ("mosaic", "▦", "马赛克"),
    ("number", "①", "序号"),
    ("text", "T", "文字"),
]

WIDTHS = [("细", 2), ("中", 4), ("粗", 7)]

_STYLE = """
QWidget#Toolbar {
    background-color: #2b2b2b;
    border: 1px solid #1a1a1a;
    border-radius: 8px;
}
QPushButton {
    background-color: transparent;
    color: #e6e6e6;
    border: none;
    border-radius: 5px;
    min-width: 28px;
    min-height: 26px;
    font-size: 15px;
    font-family: "Segoe UI Symbol", "Microsoft YaHei";
}
QPushButton:hover { background-color: #3d3d3d; }
QPushButton:checked { background-color: #007aff; color: white; }
QPushButton#action { font-size: 13px; padding: 0 6px; }
QPushButton#confirm { color: #34c759; font-size: 17px; }
QPushButton#cancel  { color: #ff5b50; font-size: 17px; }
QFrame#sep { background-color: #4a4a4a; max-width: 1px; min-width: 1px; }
"""


class Toolbar(QWidget):
    tool_changed = Signal(object)      # tool id (str) or None
    color_changed = Signal(QColor)
    width_changed = Signal(int)
    undo_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()
    pin_requested = Signal()
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Toolbar")
        self.setStyleSheet(_STYLE)
        # Paint the rounded background even though we're a plain QWidget.
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._color_buttons = []
        self._active_tool = None
        self._build()

    def _sep(self):
        f = QFrame()
        f.setObjectName("sep")
        f.setFrameShape(QFrame.VLine)
        return f

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(3)

        # --- drawing tools (exclusive, toggleable) ---
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self._tool_buttons = {}
        for tool_id, label, tip in TOOLS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda checked, t=tool_id: self._on_tool_clicked(t, checked))
            self.tool_group.addButton(btn)
            self._tool_buttons[tool_id] = btn
            layout.addWidget(btn)

        layout.addWidget(self._sep())

        # --- line widths ---
        self.width_group = QButtonGroup(self)
        self.width_group.setExclusive(True)
        for label, value in WIDTHS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(f"线宽 {value}px")
            btn.clicked.connect(lambda checked, v=value: self.width_changed.emit(v))
            self.width_group.addButton(btn)
            layout.addWidget(btn)
            if value == 4:
                btn.setChecked(True)

        layout.addWidget(self._sep())

        # --- color swatches ---
        self.color_group = QButtonGroup(self)
        self.color_group.setExclusive(True)
        for hexcolor in PALETTE:
            btn = self._make_color_button(hexcolor)
            self.color_group.addButton(btn)
            layout.addWidget(btn)
            self._color_buttons.append(btn)
        self._color_buttons[0].setChecked(True)

        # custom color picker
        more = QPushButton("…")
        more.setToolTip("自定义颜色")
        more.clicked.connect(self._pick_custom_color)
        layout.addWidget(more)

        layout.addWidget(self._sep())

        # --- actions ---
        for obj_name, label, tip, signal in [
            ("action", "↶", "撤销 (Ctrl+Z)", self.undo_requested),
            ("action", "💾", "保存 (Ctrl+S)", self.save_requested),
            ("action", "复制", "复制到剪贴板 (Ctrl+C)", self.copy_requested),
            ("cancel", "✕", "取消 (Esc)", self.cancel_requested),
            ("confirm", "✓", "完成并复制 (Enter)", self.confirm_requested),
        ]:
            btn = QPushButton(label)
            btn.setObjectName(obj_name)
            btn.setToolTip(tip)
            btn.clicked.connect(signal.emit)
            layout.addWidget(btn)

    def _make_color_button(self, hexcolor):
        btn = QPushButton()
        btn.setCheckable(True)
        btn.setToolTip(hexcolor)
        btn.setFixedSize(QSize(18, 18))
        border = "#888" if hexcolor.lower() != "#ffffff" else "#bbb"
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {hexcolor}; border: 1px solid {border};"
            f" border-radius: 9px; min-width: 18px; min-height: 18px; }}"
            f"QPushButton:checked {{ border: 2px solid #00aaff; }}"
        )
        btn.clicked.connect(lambda: self.color_changed.emit(QColor(hexcolor)))
        return btn

    def _on_tool_clicked(self, tool_id, checked):
        # An exclusive QButtonGroup never reports checked=False (you can't uncheck
        # the active button by clicking it), so track the active tool ourselves:
        # clicking the already-active tool toggles back to plain selection mode.
        if tool_id == self._active_tool:
            self.tool_group.setExclusive(False)
            self._tool_buttons[tool_id].setChecked(False)
            self.tool_group.setExclusive(True)
            self._active_tool = None
            self.tool_changed.emit(None)
        else:
            self._active_tool = tool_id
            self.tool_changed.emit(tool_id)

    def _pick_custom_color(self):
        color = QColorDialog.getColor(parent=self, title="选择颜色")
        if color.isValid():
            self.color_group.setExclusive(False)
            for b in self._color_buttons:
                b.setChecked(False)
            self.color_group.setExclusive(True)
            self.color_changed.emit(color)

    def clear_tool_selection(self):
        self.tool_group.setExclusive(False)
        for b in self._tool_buttons.values():
            b.setChecked(False)
        self.tool_group.setExclusive(True)
        self._active_tool = None
