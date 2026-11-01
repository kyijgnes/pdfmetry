"""머리글/바닥글 · 페이지 번호 · 베이츠 번호 창(공용 틀 dlg_deco.DecoDialog 위)."""
from __future__ import annotations

import os

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLabel, QLineEdit, QSpinBox

from ..engine import deco as E
from ..engine.deco_text import PAGE_NUMBER_STYLES
from .dlg_deco import DecoDialog, FontRow, MarginGrid, PositionGrid

MM = 72 / 25.4


def _margins(mm: float) -> dict:
    return {side: mm * MM for side in ("top", "bottom", "left", "right")}


class HeaderFooterDialog(DecoDialog):
    KIND = "header_footer"
    SLOTS = (("top_left", "머리글 왼쪽"), ("top", "머리글 가운데"), ("top_right", "머리글 오른쪽"),
             ("bottom_left", "바닥글 왼쪽"), ("bottom", "바닥글 가운데"), ("bottom_right", "바닥글 오른쪽"))

    def build(self):
        lay = self.group("내용")
        lay.addWidget(QLabel("쓸 수 있는 자리표: {page} 쪽 번호 · {total} 마지막 번호 · {date} 날짜 · {file} 파일 이름"))
        g = QGridLayout(); g.setHorizontalSpacing(6)
        self.slots: dict[str, QLineEdit] = {}
        for k, (cell, label) in enumerate(self.SLOTS):
            e = QLineEdit(); e.setMaxLength(512); e.setPlaceholderText(label)
            g.addWidget(e, k // 3, k % 3); self.slots[cell] = e
        lay.addLayout(g)
        self.font = FontRow(10)
        lay.addWidget(self.font)
        ml = self.group("설정 더 보기")
        self.start = QSpinBox(); self.start.setRange(0, 99999); self.start.setValue(1)
        ml.addLayout(self.row("시작 페이지 번호", self.start))
        self.margins = MarginGrid(self.unit, _margins(12)); ml.addWidget(self.margins)
        self.watch(*self.slots.values(), self.font, self.start, self.margins)

    def settings(self) -> dict:
        stem = os.path.splitext(self.doc.name)[0]
        slots = {c: E.old_placeholders(e.text(), stem) for c, e in self.slots.items() if e.text()}
        return {"v": E.SETTINGS_VERSION, "slots": slots, "start_number": self.start.value(),
                "font": self.font.value(), "margins": self.margins.value()}

    def can_apply(self) -> bool:
        return any(e.text().strip() for e in self.slots.values())

    def load(self, s: dict, pages: list[int]):
        slots = s.get("slots") or {}
        for cell, e in self.slots.items():
            e.setText(slots.get(cell, ""))
        self.font.set_value(s.get("font") or {}); self.start.setValue(int(s.get("start_number", 1)))
        self.margins.set_value(s.get("margins") or _margins(12))
        super().load(s, pages)


class PageNumberDialog(DecoDialog):
    KIND = "page_number"

    def build(self):
        lay = self.group("내용")
        self.style = QComboBox(); self.style.addItems([n for n, _ in PAGE_NUMBER_STYLES])
        self.start = QSpinBox(); self.start.setRange(0, 99999); self.start.setValue(1)
        lay.addLayout(self.row("페이지 번호 스타일", self.style)); lay.addLayout(self.row("시작 페이지 번호", self.start))
        self.font = FontRow(11); lay.addWidget(self.font)
        pl = self.group("포지션")
        self.pos = PositionGrid(6, "bottom"); pl.addLayout(self.row("", self.pos))
        self.margins = MarginGrid(self.unit, _margins(12.7)); pl.addWidget(self.margins)
        self.watch(self.style, self.start, self.font, self.pos, self.margins)

    def settings(self) -> dict:
        return {"v": E.SETTINGS_VERSION, "style": PAGE_NUMBER_STYLES[self.style.currentIndex()][1],
                "position": self.pos.value(), "start": self.start.value(), "font": self.font.value(),
                "margins": self.margins.value()}

    def load(self, s: dict, pages: list[int]):
        k = next((k for k, (_, f) in enumerate(PAGE_NUMBER_STYLES) if f == s.get("style")), 0)
        self.style.setCurrentIndex(k); self.start.setValue(int(s.get("start", 1)))
        self.font.set_value(s.get("font") or {}); self.pos.set_value(s.get("position", "bottom"))
        self.margins.set_value(s.get("margins") or _margins(12.7))
        super().load(s, pages)


class BatesDialog(DecoDialog):
    KIND = "bates"

    def build(self):
        lay = self.group("내용")
        self.prefix = QLineEdit("DOC-"); self.prefix.setMaxLength(16)
        # 자릿수는 시작 번호를 쓴 글자 수로 정한다(000001 → 여섯 자리)
        self.start = QLineEdit("000001"); self.start.setMaxLength(12)
        self.start.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,12}"), self.start))
        self.suffix = QLineEdit(); self.suffix.setMaxLength(16)
        for label, w in (("접두사", self.prefix), ("시작:", self.start), ("접미사", self.suffix)):
            lay.addLayout(self.row(label, w))
        self.reverse = QCheckBox("베이츠 번호 거꾸로"); lay.addWidget(self.reverse)
        self.font = FontRow(10); lay.addWidget(self.font)
        pl = self.group("포지션")
        self.pos = PositionGrid(9, "bottom_right"); pl.addLayout(self.row("", self.pos))
        self.margins = MarginGrid(self.unit, _margins(12)); pl.addWidget(self.margins)
        self.watch(self.prefix, self.start, self.suffix, self.reverse, self.font, self.pos, self.margins)

    def settings(self) -> dict:
        t = self.start.text() or "1"
        return {"v": E.SETTINGS_VERSION, "prefix": self.prefix.text(), "suffix": self.suffix.text(), "start": int(t),
                "digits": len(t), "reverse": self.reverse.isChecked(), "position": self.pos.value(),
                "font": self.font.value(), "margins": self.margins.value(), "rotate": 0.0, "opacity": 1.0}

    def can_apply(self) -> bool:
        return bool(self.start.text())

    def load(self, s: dict, pages: list[int]):
        self.prefix.setText(s.get("prefix", "")); self.suffix.setText(s.get("suffix", ""))
        self.start.setText(f"{int(s.get('start', 1)):0{int(s.get('digits', 6))}d}")
        self.reverse.setChecked(bool(s.get("reverse")))
        self.font.set_value(s.get("font") or {}); self.pos.set_value(s.get("position", "bottom_right"))
        self.margins.set_value(s.get("margins") or _margins(12))
        super().load(s, pages)
