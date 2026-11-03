"""북마크 속성 창(모양·색·[기본값으로]) · 북마크로 목차 쪽 만들기 창(글꼴·크기·모양·색)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout

from ..engine import fonts as F
from ..engine import outline as OL
from .dialogs import ColorButton, _buttons

SHAPES = (("보통", (False, False)), ("굵게", (True, False)), ("기울임", (False, True)), ("굵게 기울임", (True, True)))


def _shape_combo(bold: bool, italic: bool) -> QComboBox:
    c = QComboBox()
    for label, _ in SHAPES:
        c.addItem(label)
    c.setCurrentIndex([v for _, v in SHAPES].index((bool(bold), bool(italic))))
    return c


class BookmarkPropsDialog(QDialog):
    """고른 북마크 모양. [기본값으로] 는 지금 칸 값을 새 북마크 기본 모양으로 기억한다(창은 닫지 않음)."""

    def __init__(self, parent, style: dict, set_default=None):
        super().__init__(parent)
        self.setWindowTitle("북마크 속성")
        self.setMinimumWidth(300)
        st = OL.norm_style(style)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.shape = _shape_combo(st["bold"], st["italic"]); form.addRow("모양", self.shape)
        self.color = ColorButton(OL.rgb(st["color"])); form.addRow("색", self.color)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.default_btn = QPushButton("기본값으로")
        self.default_btn.clicked.connect(lambda: set_default and set_default(self.values()))
        row.addWidget(self.default_btn); row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_buttons(self))

    def values(self) -> dict:
        bold, italic = SHAPES[self.shape.currentIndex()][1]
        return {"bold": bold, "italic": italic, "color": OL.hex_color(self.color.color)}


class TocDialog(QDialog):
    """북마크로 목차 쪽 만들기: 제목 · 글꼴 · 크기 · 모양 · 색."""

    def __init__(self, parent, last: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("북마크로 목차 페이지 만들기")
        self.setMinimumWidth(340)
        f = {**OL.TOC_DEFAULT, **(last or {})}
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(f["title"]); form.addRow("제목", self.title)
        self.family = QComboBox(); self.family.addItems(F.font_families())
        k = self.family.findText(f["name"] or F.DEFAULT_FAMILY)
        if k >= 0:
            self.family.setCurrentIndex(k)
        form.addRow("글꼴", self.family)
        self.size = QDoubleSpinBox(); self.size.setRange(6, 48); self.size.setDecimals(1); self.size.setSuffix(" pt")
        self.size.setValue(float(f["size"])); form.addRow("크기", self.size)
        self.shape = _shape_combo(f["bold"], f["italic"]); form.addRow("모양", self.shape)
        self.color = ColorButton(OL.rgb(f["color"])); form.addRow("색", self.color)
        lay.addLayout(form)
        lay.addWidget(_buttons(self, "만들기"))

    def values(self) -> dict:
        bold, italic = SHAPES[self.shape.currentIndex()][1]
        return {"title": self.title.text().strip(), "name": self.family.currentText(), "size": self.size.value(),
                "bold": bold, "italic": italic, "color": OL.hex_color(self.color.color)}
