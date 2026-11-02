"""주석 목록의 필터 창: 지금 문서에 있는 작성자 · 종류 · 색을 체크 목록으로 보이고 고른 것만 남긴다.

「적용」은 체크한 것만 보이고, 「모두 지우기」는 필터를 풀어 전부 보인다. 다 체크하면 필터 없음과 같다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from .dialogs import from_rgb01


def color_key(c) -> str:
    """색 묶음 열쇠(#RRGGBB). 색이 없으면 빈 글자."""
    return from_rgb01(c).name() if c else ""


class AnnotFilterDialog(QDialog):
    def __init__(self, parent, authors: list[str], kinds: list[str], colors: list[str], current: dict | None):
        super().__init__(parent)
        self.setWindowTitle("주석 필터")
        cur = current or {}
        lay = QVBoxLayout(self)
        self.lists: dict[str, QListWidget] = {}
        for key, title, values in (("authors", "작성자", authors), ("kinds", "종류", kinds), ("colors", "색", colors)):
            lay.addWidget(QLabel(title))
            lw = QListWidget(); lw.setMaximumHeight(min(150, 22 * max(1, len(values)) + 6))
            chosen = cur.get(key)
            for v in values:
                it = QListWidgetItem(v or ("작성자 없음" if key == "authors" else "색 없음"))
                it.setData(Qt.ItemDataRole.UserRole, v)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if chosen is None or v in chosen else Qt.CheckState.Unchecked)
                if key == "colors" and v:
                    pm = QPixmap(14, 14); pm.fill(QColor(v)); it.setIcon(QIcon(pm)); it.setText(v.upper())
                lw.addItem(it)
            lay.addWidget(lw)
            self.lists[key] = lw
        row = QHBoxLayout(); row.addStretch()
        clear = QPushButton("모두 지우기"); clear.clicked.connect(self._clear); row.addWidget(clear)
        ok = QPushButton("적용"); ok.setObjectName("accent"); ok.setDefault(True); ok.clicked.connect(self.accept); row.addWidget(ok)
        lay.addLayout(row)
        self.cleared = False

    def _clear(self):
        self.cleared = True
        self.accept()

    def values(self) -> dict | None:
        """{칸: 고른 값 집합}. 필터가 없으면(모두 지우기·다 체크) None."""
        if self.cleared:
            return None
        out = {}
        for key, lw in self.lists.items():
            items = [lw.item(i) for i in range(lw.count())]
            on = {it.data(Qt.ItemDataRole.UserRole) for it in items if it.checkState() == Qt.CheckState.Checked}
            if len(on) < len(items):
                out[key] = on
        return out or None
