"""머리글/바닥글 · 페이지 번호 · 베이츠 번호 창(공용 틀 dlg_deco.DecoDialog 위)."""
from __future__ import annotations

import datetime as _dt
import os

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator, QTextCursor
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox

from ..engine import deco as E
from ..engine.deco_text import DATE_FORMATS, HF_MARGINS, MAX_SLOT_CHARS, PAGE_NUMBER_STYLES, insert_macro
from .dlg_deco import DecoDialog, FontRow, LookRow, MarginGrid, PositionGrid

INT_MAX = 2 ** 31 - 1
SLOT_NAMES = {"top_left": "왼쪽 머리글", "top": "가운데 머리글", "top_right": "오른쪽 머리글",
              "bottom_left": "왼쪽 바닥글", "bottom": "가운데 바닥글", "bottom_right": "오른쪽 바닥글"}


class _Slot:
    """자리 하나의 글(창이 자리마다 따로 기억). 고른 자리면 글 칸과 같이 움직인다."""

    def __init__(self, dlg: "HeaderFooterDialog", cell: str):
        self.dlg, self.cell = dlg, cell

    def text(self) -> str:
        return self.dlg._texts.get(self.cell, "")

    def setText(self, t: str):   # noqa: N802  Qt 글 칸과 같은 이름
        t = (t or "")[:MAX_SLOT_CHARS]
        self.dlg._texts[self.cell] = t
        if self.dlg.pos.value() == self.cell:
            self.dlg.edit.setPlainText(t)
        else:
            self.dlg.changed()


def _start_number(lo: int = 1) -> QSpinBox:
    sb = QSpinBox(); sb.setRange(lo, INT_MAX); sb.setValue(1)
    return sb


class HeaderFooterDialog(DecoDialog):
    KIND = "header_footer"

    def build(self):
        self._texts: dict[str, str] = {}
        self._date = _dt.date.today().isoformat()
        lay = self.group("내용")
        self.pos = PositionGrid(6, "top_left")
        self.edit = QPlainTextEdit(); self.edit.setFixedHeight(64)
        lay.addLayout(self.row("", self.pos, self.edit))
        self.slots = {cell: _Slot(self, cell) for cell in E.CELLS6}
        self.font = FontRow(11); lay.addWidget(self.font)
        self.style = QComboBox(); self.style.addItems([n for n, _ in PAGE_NUMBER_STYLES])
        add_no = QPushButton("페이지 번호 추가")
        add_no.clicked.connect(lambda: self.insert(PAGE_NUMBER_STYLES[self.style.currentIndex()][1]))
        self.date_style = QComboBox(); self.date_style.addItems(DATE_FORMATS)
        add_date = QPushButton("날짜 추가")
        add_date.clicked.connect(lambda: self.insert(f"<<{self.date_style.currentText()}>>"))
        g = QGridLayout(); g.setHorizontalSpacing(6); g.setColumnStretch(1, 1)
        for r, (label, combo, btn) in enumerate((("페이지 번호 스타일", self.style, add_no),
                                                 ("데이터 스타일", self.date_style, add_date))):
            g.addWidget(QLabel(label), r, 0); g.addWidget(combo, r, 1); g.addWidget(btn, r, 2)
        lay.addLayout(g)
        ml = self.group("설정 더 보기")
        self.start = _start_number()
        ml.addLayout(self.row("시작 페이지 번호", self.start))
        self.margins = MarginGrid(self.unit, HF_MARGINS); ml.addWidget(self.margins)
        self.pos.changed.connect(self._show_slot)
        self.edit.textChanged.connect(self._typed)
        self._show_slot()
        self.watch(self.font, self.start, self.margins)

    def _show_slot(self):
        cell = self.pos.value()
        self.edit.setPlaceholderText(f"{SLOT_NAMES[cell]} 텍스트 편집")
        self.edit.blockSignals(True); self.edit.setPlainText(self._texts.get(cell, "")); self.edit.blockSignals(False)
        self.edit.moveCursor(QTextCursor.MoveOperation.End)

    def _typed(self):
        t = self.edit.toPlainText()
        if len(t) > MAX_SLOT_CHARS:   # 한 자리 512자까지
            cur = self.edit.textCursor().position()
            t = t[:MAX_SLOT_CHARS]
            self.edit.blockSignals(True); self.edit.setPlainText(t); self.edit.blockSignals(False)
            c = self.edit.textCursor(); c.setPosition(min(cur, len(t))); self.edit.setTextCursor(c)
        self._texts[self.pos.value()] = t
        self.changed()

    def insert(self, macro: str):
        """글 칸 커서 자리에 매크로를 끼운다."""
        t, at = insert_macro(self.edit.toPlainText(), self.edit.textCursor().position(), macro)
        if len(t) > MAX_SLOT_CHARS:
            return
        self.edit.setPlainText(t)
        c = self.edit.textCursor(); c.setPosition(at); self.edit.setTextCursor(c)
        self.edit.setFocus()

    def settings(self) -> dict:
        stem = os.path.splitext(self.doc.name)[0]
        slots = {c: E.old_placeholders(t, stem) for c, t in self._texts.items() if t}
        return {"v": E.SETTINGS_VERSION, "slots": slots, "start_number": self.start.value(),
                "font": self.font.value(), "margins": self.margins.value(), "date": self._date}

    def can_apply(self) -> bool:
        return any(t.strip() for t in self._texts.values())

    def load(self, s: dict, pages: list[int]):
        slots = s.get("slots") or {}
        self._texts = {c: str(slots.get(c, ""))[:MAX_SLOT_CHARS] for c in E.CELLS6 if slots.get(c)}
        self._date = s.get("date") or self._date
        # 처음 차 있는 자리, 다 비었으면 왼쪽 머리글
        self.pos.set_value(next((c for c in E.CELLS6 if self._texts.get(c)), "top_left"))
        self._show_slot()
        self.font.set_value(s.get("font") or {"size": 11})
        self.start.setValue(max(int(s.get("start_number", 1)), 1))
        self.margins.set_value(s.get("margins") or HF_MARGINS)
        super().load(s, pages)


def _style_name(macro: str) -> str:
    """목록에 없는 옛 번호 모양(<<##1##>> / <<##n##>> 등)의 화면 이름: 「1 / n」."""
    return macro.replace("<<", "").replace(">>", "").replace("##1##", "1").replace("##n##", "n")


class PageNumberDialog(DecoDialog):
    KIND = "page_number"

    def build(self):
        lay = self.group("내용")
        self.style = QComboBox(); self.style.addItems([n for n, _ in PAGE_NUMBER_STYLES])
        self.styles = [m for _, m in PAGE_NUMBER_STYLES]
        self.start = _start_number()
        lay.addLayout(self.row("페이지 번호 스타일", self.style)); lay.addLayout(self.row("시작 페이지 번호", self.start))
        self.font = FontRow(11); lay.addWidget(self.font)
        pl = self.group("포지션")
        self.pos = PositionGrid(6, "bottom"); pl.addLayout(self.row("", self.pos))
        self.margins = MarginGrid(self.unit, HF_MARGINS); pl.addWidget(self.margins)
        self.watch(self.style, self.start, self.font, self.pos, self.margins)

    def settings(self) -> dict:
        # 스타일·자리는 하나씩이라 바꾸면 고른 자리 한 칸에만 그 번호가 남는다
        return {"v": E.SETTINGS_VERSION, "style": self.styles[self.style.currentIndex()],
                "position": self.pos.value(), "start": self.start.value(), "font": self.font.value(),
                "margins": self.margins.value()}

    def load(self, s: dict, pages: list[int]):
        style = s.get("style") or self.styles[0]
        if style not in self.styles:   # 옛 문서의 다른 모양은 목록 끝에 붙여 그대로 둔다
            self.styles.append(style); self.style.addItem(_style_name(style))
        self.style.setCurrentIndex(self.styles.index(style))
        self.start.setValue(max(int(s.get("start", 1)), 1))
        self.font.set_value(s.get("font") or {"size": 11}); self.pos.set_value(s.get("position", "bottom"))
        self.margins.set_value(s.get("margins") or HF_MARGINS)
        super().load(s, pages)


class BatesDialog(DecoDialog):
    KIND = "bates"

    def build(self):
        lay = self.group("내용")
        self.prefix = QLineEdit(); self.prefix.setMaxLength(8)
        # 자릿수는 시작 칸에 쓴 글자 수로 정한다(0001 → 네 자리)
        self.start = QLineEdit("1"); self.start.setMaxLength(8); self.start.setPlaceholderText("번호 입력")
        self.start.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,8}"), self.start))
        self.suffix = QLineEdit(); self.suffix.setMaxLength(8)
        for label, w in (("접두사", self.prefix), ("시작:", self.start), ("접미사", self.suffix)):
            lay.addLayout(self.row(label, w))
        self.reverse = QCheckBox("베이츠 번호 거꾸로"); lay.addWidget(self.reverse)
        self.marked_only = QCheckBox("베이츠 번호 포함 페이지의 번호만 증가"); lay.addWidget(self.marked_only)
        self.font = FontRow(24); lay.addWidget(self.font)
        pl = self.group("포지션")
        self.pos = PositionGrid(9, "center"); pl.addLayout(self.row("", self.pos))
        self.margins = MarginGrid(self.unit, E.ZERO_MARGINS); pl.addWidget(self.margins)
        self.look = LookRow(0, 1.0); self.group("설정").addWidget(self.look)
        self.watch(self.prefix, self.start, self.suffix, self.reverse, self.marked_only, self.font, self.pos,
                   self.margins, self.look)

    def settings(self) -> dict:
        t = self.start.text() or "1"
        rotate, opacity = self.look.value()
        return {"v": E.SETTINGS_VERSION, "prefix": self.prefix.text(), "suffix": self.suffix.text(), "start": int(t),
                "digits": len(t), "reverse": self.reverse.isChecked(), "marked_only": self.marked_only.isChecked(),
                "position": self.pos.value(), "font": self.font.value(), "margins": self.margins.value(),
                "rotate": rotate, "opacity": opacity}

    def can_apply(self) -> bool:
        return bool(self.start.text())

    def load(self, s: dict, pages: list[int]):
        self.prefix.setText(str(s.get("prefix", ""))[:8]); self.suffix.setText(str(s.get("suffix", ""))[:8])
        digits = min(max(int(s.get("digits", 1)), 1), 8)
        self.start.setText(f"{int(s.get('start', 1)):0{digits}d}"[-8:])
        self.reverse.setChecked(bool(s.get("reverse"))); self.marked_only.setChecked(bool(s.get("marked_only")))
        self.font.set_value(s.get("font") or {"size": 24}); self.pos.set_value(s.get("position", "center"))
        self.margins.set_value(s.get("margins") or E.ZERO_MARGINS)
        self.look.set_value(float(s.get("rotate", 0)), float(s.get("opacity", 1)))
        super().load(s, pages)
