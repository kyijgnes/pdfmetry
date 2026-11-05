"""주석 「여러 쪽에 적용」 창: 쪽 범위 하나만 고른다(설계 17, 4.4). 고른 쪽마다 보이는 화면 기준 같은 자리에 넣는다."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMessageBox

from .dialogs import FormDialog, PageRangeEdit


class ApplyPagesDialog(FormDialog):
    def __init__(self, parent, total: int, current: int, kind_ko: str = "주석"):
        super().__init__("여러 쪽에 적용", parent, ok_text="적용")
        self.form.addRow(QLabel(f"{kind_ko} · {current + 1}쪽 원본은 건너뜀"))
        self.range = self.add("적용할 쪽", PageRangeEdit(total, current))
        self.result_pages: list[int] = []

    def accept(self):
        try:
            self.result_pages = self.range.pages()
        except ValueError as e:
            QMessageBox.warning(self, "여러 쪽에 적용", str(e))
            self.range.focus_error()
            return
        super().accept()

    def pages(self) -> list[int]:
        return list(self.result_pages)
