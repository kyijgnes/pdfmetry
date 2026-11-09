"""페이지 내보내기(추출) 대화상자. 쪽 범위와 「추출 후 페이지 삭제」 만 모아 돌려주고, 일은 window.py 가 한다."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QGroupBox, QMessageBox, QToolButton, QVBoxLayout

from ..engine import pages as P
from .dialogs import PageRangeEdit, _buttons

DELETE_ALL_WARNING = "모든 페이지를 삭제할 수는 없습니다. 페이지 범위를 다시 고르십시오."


class ExtractDialog(QDialog):
    def __init__(self, parent, total: int, current: int, pages: list[int], can_modify: bool = True):
        super().__init__(parent)
        self.setWindowTitle("페이지 내보내기")
        self.setMinimumWidth(380)
        self.total = total
        lay = QVBoxLayout(self)
        box = QGroupBox("페이지 범위"); bl = QVBoxLayout(box)
        self.range = PageRangeEdit(total, current)
        self.range.set_pages(pages)
        bl.addWidget(self.range)
        lay.addWidget(box)

        self.more = QToolButton(); self.more.setText("설정 더 보기"); self.more.setCheckable(True)
        self.more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more.setArrowType(Qt.ArrowType.RightArrow); self.more.setAutoRaise(True)
        self.more.setStyleSheet("QToolButton, QToolButton:checked { border: none; background: transparent; }")   # 펼침 머리라 눌린 단추처럼 칠하지 않는다
        self.delete_after = QCheckBox("추출 후 페이지 삭제")
        self.delete_after.setEnabled(can_modify)   # 수정 권한이 없는 문서는 막는다
        self.delete_after.setVisible(False)
        self.more.toggled.connect(self._more)
        lay.addWidget(self.more); lay.addWidget(self.delete_after)
        lay.addStretch(1)
        lay.addWidget(_buttons(self, "내보내기"))
        self._pages: list[int] = []

    def _more(self, on: bool):
        self.more.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.delete_after.setVisible(on)
        self.adjustSize()

    def accept(self):
        try:
            pages = self.range.pages()
        except ValueError:
            QMessageBox.warning(self, self.windowTitle(), P.RANGE_ERROR)
            self.range.focus_error()
            return
        if self.delete_after.isChecked() and len(pages) >= self.total:
            QMessageBox.warning(self, self.windowTitle(), DELETE_ALL_WARNING)
            return
        self._pages = pages
        super().accept()

    def values(self) -> dict:
        return {"pages": self._pages, "delete": self.delete_after.isChecked()}
