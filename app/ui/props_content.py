"""속성 패널의 편집 개체 칸: 고른 글자 덩어리(글꼴·크기·색·굵게·기울임·밑줄·취소선·정렬·줄 간격·문자 간격)와 그림(돌리기·뒤집기·불투명도·크기·바꾸기).

바꾸는 즉시 쪽에 적용하고 한 번 바꿀 때마다 실행 취소 한 걸음이 된다. 「쪽 가운데」 정렬 셋은 둘 다 쓴다.
"""
from __future__ import annotations

import fitz
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSlider, QToolButton, QVBoxLayout, QWidget)

from ..engine import annots as A
from ..engine import fonts as F
from .dialogs import ColorButton
from .dlg_pagebox import FINE, UNITS, UnitBox
from .sidebar import group_card

ALIGNS = ("왼쪽", "가운데", "오른쪽", "양쪽")   # insert_textbox 의 align 0~3


def _row(*ws) -> QWidget:
    """단추 여럿을 한 줄에 같은 폭으로(패널 폭을 넘지 않게)."""
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
    for x in ws:
        x.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed); x.setMinimumWidth(0)
        h.addWidget(x)
    return w


def _toggle(text: str) -> QToolButton:
    b = QToolButton(); b.setText(text); b.setCheckable(True)
    return b


class ContentProps(QWidget):
    """PropsPanel 안에 끼우는 칸. show_sel(sel) 로 고른 것(block·image)을 받는다."""

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.sel = None
        self._loading = False
        self._wh = (0.0, 0.0)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(300)
        self._timer.timeout.connect(self._apply_text)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)

        # 글자
        self.text_box, f = group_card("글자"); lay.addWidget(self.text_box)
        self.family = QComboBox(); self.family.setEditable(True)
        self.family.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.family.setMinimumContentsLength(6)
        self.family.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        f.addRow("글꼴", self.family)
        self.size = QDoubleSpinBox(); self.size.setRange(1, 200); self.size.setDecimals(1); self.size.setSingleStep(1)
        f.addRow("크기", self.size)
        self.color = ColorButton((0, 0, 0)); f.addRow("글자색", self.color)
        self.bold, self.italic = _toggle("굵게"), _toggle("기울임")
        f.addRow("모양", _row(self.bold, self.italic))
        self.underline, self.strike = _toggle("밑줄"), _toggle("취소선")
        for b, attr in ((self.bold, "setBold"), (self.italic, "setItalic"), (self.underline, "setUnderline"),
                        (self.strike, "setStrikeOut")):
            fo = b.font(); getattr(fo, attr)(True); b.setFont(fo)
        f.addRow("", _row(self.underline, self.strike))
        self.align = QButtonGroup(self)
        btns = []
        for n, t in enumerate(ALIGNS):
            b = _toggle(t); self.align.addButton(b, n); btns.append(b)
        f.addRow("정렬", QWidget()); f.addRow(_row(*btns))
        self.line_height = QDoubleSpinBox(); self.line_height.setRange(0, 5); self.line_height.setDecimals(2)
        self.line_height.setSingleStep(0.1); self.line_height.setSpecialValueText("기본"); self.line_height.setSuffix(" 배")
        f.addRow("줄 간격", self.line_height)
        self.spacing = QDoubleSpinBox(); self.spacing.setRange(-5, 50); self.spacing.setDecimals(1)
        self.spacing.setSingleStep(0.5); self.spacing.setSuffix(" pt")
        f.addRow("문자 간격", self.spacing)

        # 그림
        self.image_box, f = group_card("이미지"); lay.addWidget(self.image_box)
        self.rot_l, self.rot_r = QPushButton("왼쪽 90°"), QPushButton("오른쪽 90°")
        f.addRow("회전", _row(self.rot_l, self.rot_r))
        self.flip_h, self.flip_v = QPushButton("좌우"), QPushButton("상하")
        f.addRow("뒤집기", _row(self.flip_h, self.flip_v))
        op = QWidget(); ol = QHBoxLayout(op); ol.setContentsMargins(0, 0, 0, 0); ol.setSpacing(6)
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(5, 100)
        self.op_label = QLabel("100%"); self.op_label.setMinimumWidth(34)
        ol.addWidget(self.opacity, 1); ol.addWidget(self.op_label)
        f.addRow("불투명도", op)
        self.w = UnitBox("cm", 1000); self.h = UnitBox("cm", 1000)
        for box in (self.w, self.h):
            box.setMinimumWidth(0); box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        f.addRow("너비", self.w); f.addRow("높이", self.h)
        self.replace_btn = QPushButton("그림 바꾸기"); f.addRow(self.replace_btn)

        # 쪽 가운데
        self.place_box, f = group_card("쪽 가운데 맞추기"); lay.addWidget(self.place_box)
        self.center_h, self.center_v, self.center_both = QPushButton("가로"), QPushButton("세로"), QPushButton("한가운데")
        f.addRow(_row(self.center_h, self.center_v, self.center_both))

        self.family.activated.connect(self._edited)
        self.family.lineEdit().editingFinished.connect(self._edited)
        self.size.valueChanged.connect(self._edited)
        self.color.clicked.connect(self._edited)
        for w in (self.bold, self.italic, self.underline, self.strike):
            w.toggled.connect(self._edited)
        self.line_height.valueChanged.connect(self._edited); self.spacing.valueChanged.connect(self._edited)
        self.align.idClicked.connect(self._edited)
        self.rot_l.clicked.connect(lambda: self._image(rotate=-90))
        self.rot_r.clicked.connect(lambda: self._image(rotate=90))
        self.flip_h.clicked.connect(lambda: self._image(flip_h=True))
        self.flip_v.clicked.connect(lambda: self._image(flip_v=True))
        self.opacity.valueChanged.connect(self._opacity_moved); self.opacity.sliderReleased.connect(self._opacity_done)
        self.w.editingFinished.connect(self._size_done); self.h.editingFinished.connect(self._size_done)
        self.replace_btn.clicked.connect(lambda: self.viewer.requestDialog.emit("replace_image", self.sel))
        self.center_h.clicked.connect(lambda: self._center(True, False))
        self.center_v.clicked.connect(lambda: self._center(False, True))
        self.center_both.clicked.connect(lambda: self._center(True, True))

    # ── 보이기 ────────────────────────────────────────────────
    def _unit(self) -> str:
        u = getattr(self.window(), "prefs", {}).get("unit", "cm")
        return u if u in UNITS else "cm"

    def show_sel(self, sel):
        """고른 것이 글자 덩어리·그림이면 그 칸을 채워 보이고 True, 아니면 숨기고 False."""
        self._timer.stop()
        kind = getattr(sel, "kind", "")
        if kind not in ("block", "image") or self.viewer is None:
            self.sel = None
            self.hide()
            return False
        self.sel = sel
        self._loading = True
        try:
            self.text_box.setVisible(kind == "block")
            self.image_box.setVisible(kind == "image")
            if kind == "block":
                st = sel.extra.get("style")
                if st is None:
                    st = sel.extra["style"] = A.block_style(self.viewer.doc, sel.page, sel.rect)
                if self.family.count() == 0:
                    self.family.addItems(F.font_families())
                self.family.setCurrentText(st["family"] or F.DEFAULT_FAMILY)
                self.size.setValue(float(st["fontsize"]))
                self.color.color = tuple(st["color"]); self.color._paint()
                self.bold.setChecked(st["bold"]); self.italic.setChecked(st["italic"])
                self.underline.setChecked(bool(st.get("underline"))); self.strike.setChecked(bool(st.get("strike")))
                self.line_height.setValue(float(st.get("line_height") or 0))
                self.spacing.setValue(float(st.get("letter_spacing") or 0))
                self.align.button(st.get("align", 0)).setChecked(True)
            else:
                op = A.image_opacity(self.viewer.doc, sel.page, sel.extra.get("xref", 0), sel.rect)
                self.opacity.setValue(int(round(op * 100))); self.op_label.setText(f"{self.opacity.value()}%")
                unit = self._unit()
                for box in (self.w, self.h):
                    box.k = UNITS[unit]; box.setSuffix(f" {unit}"); box.setDecimals(2 if unit in FINE else 1)
                    box.setSingleStep(0.1 if unit in FINE else 1)
                vis = self._visible(sel.rect)
                self.w.set_pt(vis.width); self.h.set_pt(vis.height)
                self._wh = (self.w.value(), self.h.value())   # 단위로 반올림해 보인 값(안 고친 칸은 원래 pt 그대로)
        finally:
            self._loading = False
        self.show()
        return True

    def _page(self) -> fitz.Page:
        return self.viewer.doc.fitz[self.sel.page]

    def _visible(self, r: fitz.Rect) -> fitz.Rect:
        """회전 전 쪽 좌표 → 보이는 쪽 좌표."""
        v = fitz.Rect(r) * self._page().rotation_matrix
        v.normalize()
        return v

    def _unvisible(self, v: fitz.Rect) -> fitz.Rect:
        r = fitz.Rect(v) * self._page().derotation_matrix
        r.normalize()
        return r

    # ── 글자 ──────────────────────────────────────────────────
    def _edited(self, *_):
        if not self._loading and self.sel is not None and self.sel.kind == "block":
            self._timer.start()

    def _apply_text(self):
        s = self.sel
        if s is None or s.kind != "block":
            return
        fam = self.family.currentText().strip()
        st = {"fontsize": self.size.value(), "color": self.color.color or (0, 0, 0), "bold": self.bold.isChecked(),
              "italic": self.italic.isChecked(), "align": max(0, self.align.checkedId()),
              "family": "" if fam in ("", F.DEFAULT_FAMILY) or fam not in F.font_families() else fam,
              "underline": self.underline.isChecked(), "strike": self.strike.isChecked(),
              "line_height": round(self.line_height.value(), 2), "letter_spacing": round(self.spacing.value(), 1)}
        old = dict(s.extra.get("style") or {})
        if old.get("family") == F.DEFAULT_FAMILY:
            old["family"] = ""
        if all(old.get(k) == v for k, v in st.items()):
            return
        A.edit_block(self.viewer.doc, s.page, s.rect, s.text, st["fontsize"], st["color"], st["bold"], st["align"],
                     italic=st["italic"], family=st["family"], style=st)
        s.extra["style"] = st
        self.viewer.refresh_pages([s.page])

    # ── 그림 ──────────────────────────────────────────────────
    def _image(self, **kw):
        if self.sel is not None and self.sel.kind == "image":
            self.viewer.place_content(self.sel.rect, **kw)

    def _opacity_moved(self, *_):
        self.op_label.setText(f"{self.opacity.value()}%")
        if not self._loading and not self.opacity.isSliderDown():  # 끄는 동안은 놓을 때 한 번만
            self._opacity_done()

    def _opacity_done(self):
        if self._loading or self.sel is None or self.sel.kind != "image":
            return
        op = self.opacity.value() / 100
        if abs(op - A.image_opacity(self.viewer.doc, self.sel.page, self.sel.extra.get("xref", 0), self.sel.rect)) > 0.004:
            self._image(opacity=op)

    def _size_done(self):
        if self._loading or self.sel is None or self.sel.kind != "image":
            return
        v = self._visible(self.sel.rect)
        if (self.w.value(), self.h.value()) == self._wh:
            return
        w = v.width if self.w.value() == self._wh[0] else self.w.pt()
        h = v.height if self.h.value() == self._wh[1] else self.h.pt()
        self.viewer.place_content(self._unvisible(fitz.Rect(v.x0, v.y0, v.x0 + w, v.y0 + h)))

    # ── 쪽 가운데 ─────────────────────────────────────────────
    def _center(self, horiz: bool, vert: bool):
        if self.sel is None:
            return
        v = self._visible(self.sel.rect)
        page = self._page().rect          # 보이는 쪽 크기(회전 반영)
        dx = (page.width - v.width) / 2 - v.x0 if horiz else 0
        dy = (page.height - v.height) / 2 - v.y0 if vert else 0
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return
        self.viewer.place_content(self._unvisible(v + (dx, dy, dx, dy)))
