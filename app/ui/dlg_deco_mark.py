"""워터마크·배경 창(공용 틀 dlg_deco.DecoDialog 위). 값만 모으고 일은 dlg_deco.run_decorate 가 엔진에 시킨다."""
from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QCheckBox, QHBoxLayout, QPlainTextEdit, QRadioButton, QStackedWidget, QVBoxLayout, QWidget

from ..engine import deco as E
from .dialogs import ColorButton
from .dlg_deco import DecoDialog, FileRow, FontRow, LookRow, PositionGrid, ScaleRow


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


class WatermarkDialog(DecoDialog):
    KIND = "watermark"

    def build(self):
        lay = self.group("내용")
        self.is_text, self.is_file, stack = _switch(self, lay, ("텍스트", "파일"))
        tl = _page(stack)
        self.text = QPlainTextEdit("대외비"); self.text.setPlaceholderText("내용"); self.text.setFixedHeight(64)
        self.font = FontRow(48, (0.5, 0.5, 0.5))
        tl.addWidget(self.text); tl.addWidget(self.font)
        fl = _page(stack)
        self.file = FileRow(); self.scale = ScaleRow("max", 50, 50)
        fl.addWidget(self.file); fl.addWidget(self.scale)
        pl = self.group("포지션")
        self.pos = PositionGrid(9)
        self.tile = QCheckBox("타일")
        pl.addLayout(self.row("", self.pos)); pl.addWidget(self.tile)
        self.tile.toggled.connect(lambda on: self.pos.setEnabled(not on))
        sl = self.group("설정")
        self.look = LookRow(45, 0.3); sl.addWidget(self.look)
        self.watch(self.is_text, self.text, self.font, self.file, self.scale, self.pos, self.tile, self.look)

    def settings(self) -> dict:
        path, fpage = self.file.value()
        mode, pct = self.scale.value()
        rotate, opacity = self.look.value()
        text = self.is_text.isChecked()
        return {"v": E.SETTINGS_VERSION, "text": self.text.toPlainText() if text else None,
                "file": None if text else path, "file_page": fpage, "font": self.font.value(),
                "position": self.pos.value(), "offset": [0.0, 0.0], "tile": self.tile.isChecked(), "tile_gap": [2, 2],
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
        self.scale.set_value(s.get("scale_mode", "max"), s.get("scale_pct", 50))
        self.pos.set_value(s.get("position", "center")); self.tile.setChecked(bool(s.get("tile")))
        self.look.set_value(s.get("rotate", 45), s.get("opacity", 0.3))
        super().load(s, pages)


class BackgroundDialog(DecoDialog):
    KIND = "background"

    def build(self):
        lay = self.group("내용")
        self.is_color, self.is_file, stack = _switch(self, lay, ("색상", "파일"))
        cl = _page(stack)
        self.color = ColorButton((1, 0.98, 0.9))
        cl.addLayout(self.row("색", self.color)); cl.addStretch(1)
        fl = _page(stack)
        self.file = FileRow(); self.scale = ScaleRow("page", 100, 100)
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
        return self.is_color.isChecked() or bool(self.file.value()[0])

    def check(self) -> str | None:
        return None if self.is_color.isChecked() else self.file.check()

    def load(self, s: dict, pages: list[int]):
        (self.is_file if s.get("file") else self.is_color).setChecked(True)
        if s.get("color"):
            self.color.color = tuple(s["color"]); self.color._paint()
        self.file.set_value(s.get("file"), s.get("file_page", 0))
        self.scale.set_value(s.get("scale_mode", "page"), s.get("scale_pct", 100))
        self.look.set_value(s.get("rotate", 0), s.get("opacity", 1.0))
        super().load(s, pages)
