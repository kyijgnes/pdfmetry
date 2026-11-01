"""워터마크·배경 창(공용 틀 dlg_deco.DecoDialog 위). 값만 모으고 일은 dlg_deco.run_decorate 가 엔진에 시킨다."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..engine import deco as E
from ..engine import deco_mark as M
from . import dialogs as D
from .dialogs import ColorButton
from .dlg_deco import DecoDialog, FileRow, FontRow, LookRow, PositionGrid, ScaleRow
from .dlg_pagebox import UnitBox

WHITE = (1.0, 1.0, 1.0)


def _switch(dlg: DecoDialog, lay, labels: tuple[str, str]) -> tuple[QRadioButton, QRadioButton, QStackedWidget]:
    """「텍스트」/「파일」 같은 전환 단추 둘 + 그 아래 바뀌는 칸."""
    row = QHBoxLayout()
    a, b = QRadioButton(labels[0]), QRadioButton(labels[1])
    grp = QButtonGroup(dlg); grp.addButton(a); grp.addButton(b)
    a.setChecked(True)
    row.addWidget(a); row.addWidget(b); row.addStretch(1)
    lay.addLayout(row)
    stack = QStackedWidget(); lay.addWidget(stack)
    a.toggled.connect(lambda on: stack.setCurrentIndex(0 if on else 1))
    return a, b, stack


def _page(stack: QStackedWidget) -> QVBoxLayout:
    w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
    stack.addWidget(w)
    return lay


class MarkFileRow(FileRow):
    """파일 칸 + 암호 걸린 PDF 면 암호를 물어 엔진에 기억시킨다(문서 안 설정에는 적지 않음)."""

    def _path_changed(self):
        path = self.path.text().strip()
        if self.is_pdf() and os.path.isfile(path) and M.pdf_locked(path):
            while True:
                pw = D.ask_password(self, os.path.basename(path))
                if pw is None or M.try_password(path, pw):
                    break
        super()._path_changed()

    def check(self) -> str | None:
        path = self.value()[0]
        if self.is_pdf() and os.path.isfile(path) and M.pdf_locked(path):
            return f"암호가 걸린 파일입니다: {os.path.basename(path)}"
        return super().check()


def _gap_box() -> QComboBox:
    c = QComboBox()
    c.addItems([str(v) for v in M.TILE_GAPS])
    c.setCurrentIndex(M.TILE_GAPS.index(2))
    return c


class WatermarkDialog(DecoDialog):
    KIND = "watermark"

    def build(self):
        lay = self.group("내용")
        self.is_text, self.is_file, stack = _switch(self, lay, ("텍스트", "파일"))
        tl = _page(stack)
        self.text = QPlainTextEdit(); self.text.setPlaceholderText("내용"); self.text.setFixedHeight(64)
        self.font = FontRow(24, (0, 0, 0))
        self.fit = QCheckBox("페이지 비율")
        self.fit_pct = QSpinBox(); self.fit_pct.setRange(1, 1600); self.fit_pct.setSuffix(" %"); self.fit_pct.setValue(50)
        tl.addWidget(self.text); tl.addWidget(self.font); tl.addLayout(self.row("", self.fit, self.fit_pct))
        fl = _page(stack)
        self.file = MarkFileRow(); self.scale = ScaleRow("page", 50, 100)
        fl.addWidget(self.file); fl.addWidget(self.scale)

        pl = self.group("포지션")
        self.pos = PositionGrid(9)
        grid = QGridLayout(); grid.setHorizontalSpacing(6)
        self.off_v, self.off_h = UnitBox(self.unit, 1000), UnitBox(self.unit, 1000)
        for k, (label, sb) in enumerate((("수직 오프셋", self.off_v), ("수평 오프셋", self.off_h))):
            sb.setMinimum(-1000)
            grid.addWidget(QLabel(label), k, 0); grid.addWidget(sb, k, 1)
        top = QHBoxLayout(); top.setSpacing(16); top.addWidget(self.pos); top.addLayout(grid); top.addStretch(1)
        pl.addLayout(top)
        self.tile = QCheckBox("타일")
        self.gap_x, self.gap_y = _gap_box(), _gap_box()
        pl.addLayout(self.row("", self.tile, QLabel("가로 간격"), self.gap_x, QLabel("세로 간격"), self.gap_y))

        sl = self.group("설정")
        self.look = LookRow(45, 1.0); sl.addWidget(self.look)

        self._page_before_tile = True   # 타일을 켜기 전 파일 배율이 「페이지 비율」 이었는지(끄면 되살림)
        self.tile.toggled.connect(self._tile_toggled)
        self.fit.toggled.connect(self._sync)
        self.is_text.toggled.connect(self._sync)
        self.watch(self.is_text, self.text, self.font, self.fit, self.fit_pct, self.file, self.scale, self.pos,
                   self.off_v, self.off_h, self.tile, self.gap_x, self.gap_y, self.look)
        self._sync()

    def _tile_toggled(self, on: bool):
        sr = self.scale
        if on:
            self._page_before_tile = sr.page.isChecked()
            sr.max.setChecked(True)
        elif self._page_before_tile:
            sr.page.setChecked(True)
        self._sync()

    def _sync(self, *_):
        """타일을 켜면 위치·오프셋·페이지 비율이 꺼지고, 글의 페이지 비율을 켜면 글꼴 크기·타일이 꺼진다."""
        tile = self.tile.isChecked()
        fit = self.is_text.isChecked() and self.fit.isChecked()
        for w in (self.pos, self.off_v, self.off_h, self.fit, self.scale.page):
            w.setEnabled(not tile)
        self.fit_pct.setEnabled(not tile and self.fit.isChecked())
        self.font.size.setEnabled(not fit)
        self.tile.setEnabled(not fit)
        self.gap_x.setEnabled(tile); self.gap_y.setEnabled(tile)

    def settings(self) -> dict:
        path, fpage = self.file.value()
        rotate, opacity = self.look.value()
        text = self.is_text.isChecked()
        if text:
            mode, pct = ("page" if self.fit.isChecked() else "max"), float(self.fit_pct.value())
        else:
            mode, pct = self.scale.value()
        return {"v": E.SETTINGS_VERSION, "text": self.text.toPlainText() if text else None,
                "file": None if text else path, "file_page": fpage, "font": self.font.value(),
                "position": self.pos.value(), "offset": [self.off_h.pt(), self.off_v.pt()],
                "tile": self.tile.isChecked(), "tile_gap": [int(self.gap_x.currentText()), int(self.gap_y.currentText())],
                "scale_mode": mode, "scale_pct": pct, "rotate": rotate, "opacity": opacity}

    def can_apply(self) -> bool:
        if self.is_text.isChecked():
            return bool(self.text.toPlainText().strip())
        return bool(self.file.value()[0])

    def check(self) -> str | None:
        return None if self.is_text.isChecked() else self.file.check()

    def load(self, s: dict, pages: list[int]):
        (self.is_file if s.get("file") else self.is_text).setChecked(True)
        self.text.setPlainText(s.get("text") or "")
        self.file.set_value(s.get("file"), s.get("file_page", 0))
        self.font.set_value(s.get("font") or {})
        mode, pct = s.get("scale_mode", "max"), float(s.get("scale_pct", 50))
        if s.get("file"):
            self.scale.set_value(mode, pct)
        else:
            self.fit.setChecked(mode == "page"); self.fit_pct.setValue(int(round(pct)))
        self.pos.set_value(s.get("position", "center"))
        off = list(s.get("offset") or (0, 0))
        self.off_h.set_pt(float(off[0])); self.off_v.set_pt(float(off[1]))
        gap = list(s.get("tile_gap") or (2, 2))
        for box, n in zip((self.gap_x, self.gap_y), gap):
            k = box.findText(str(int(n)))
            box.setCurrentIndex(k if k >= 0 else M.TILE_GAPS.index(2))
        self.tile.setChecked(bool(s.get("tile")))
        self.look.set_value(s.get("rotate", 45), s.get("opacity", 1.0))
        self._sync()
        super().load(s, pages)


class BackgroundDialog(DecoDialog):
    KIND = "background"

    def build(self):
        lay = self.group("내용")
        self.is_color, self.is_file, stack = _switch(self, lay, ("색상", "파일"))
        cl = _page(stack)
        self.color = ColorButton(WHITE)
        cl.addLayout(self.row("색", self.color)); cl.addStretch(1)
        fl = _page(stack)
        self.file = MarkFileRow(); self.scale = ScaleRow("max", 100, 100)
        fl.addWidget(self.file); fl.addWidget(self.scale)
        sl = self.group("설정")
        self.look = LookRow(0, 1.0); sl.addWidget(self.look)
        self.watch(self.is_color, self.color, self.file, self.scale, self.look)

    def settings(self) -> dict:
        path, fpage = self.file.value()
        mode, pct = self.scale.value()
        rotate, opacity = self.look.value()
        color = self.is_color.isChecked()
        return {"v": E.SETTINGS_VERSION, "color": tuple(self.color.color) if color and self.color.color else None,
                "file": None if color else path, "file_page": fpage, "scale_mode": mode, "scale_pct": pct,
                "rotate": rotate, "opacity": opacity}

    def can_apply(self) -> bool:
        if self.is_color.isChecked():
            return bool(self.color.color) and tuple(round(float(c), 3) for c in self.color.color) != WHITE
        return bool(self.file.value()[0])

    def check(self) -> str | None:
        return None if self.is_color.isChecked() else self.file.check()

    def load(self, s: dict, pages: list[int]):
        (self.is_file if s.get("file") else self.is_color).setChecked(True)
        if s.get("color"):
            self.color.color = tuple(s["color"]); self.color._paint()
        self.file.set_value(s.get("file"), s.get("file_page", 0))
        self.scale.set_value(s.get("scale_mode", "max"), s.get("scale_pct", 100))
        self.look.set_value(s.get("rotate", 0), s.get("opacity", 1.0))
        super().load(s, pages)
