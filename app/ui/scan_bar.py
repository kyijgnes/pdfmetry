"""스캔 문서 안내 막대: 앞 3쪽이 모두 스캔 쪽(글자 없이 그림)이면 보기 위에 한 줄.

[OCR 수행] 은 OCR 창으로, [다시 표시 안 함] 은 그 파일을 `settings["scan_bar_off"]`(소문자 절대 경로 목록)에 적는다.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

TEXT = "스캔한 PDF입니다. OCR을 실행하면 편집 및 검색 가능합니다."
CHECK_PAGES = 3


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).lower()


def looks_scanned(doc) -> bool:
    n = min(CHECK_PAGES, doc.page_count)
    if n <= 0:
        return False
    try:
        return all(doc.is_scanned(i) for i in range(n))
    except Exception:  # noqa: BLE001
        return False


def is_off(settings: dict, path: str | None) -> bool:
    return bool(path) and _key(path) in (settings.get("scan_bar_off") or [])


def turn_off(settings: dict, path: str | None) -> None:
    if not path:
        return
    lst = settings.setdefault("scan_bar_off", [])
    k = _key(path)
    if k not in lst:
        lst.append(k)


class ScanBar(QFrame):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setObjectName("scanBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout(self); h.setContentsMargins(12, 4, 8, 4); h.setSpacing(8)
        self.label = QLabel(TEXT); h.addWidget(self.label, 1)
        self.run = QPushButton("OCR 수행"); self.run.setDefault(True); h.addWidget(self.run)
        self.never = QPushButton("다시 표시 안 함"); h.addWidget(self.never)
        self.close_btn = QPushButton("✕"); self.close_btn.setFlat(True); self.close_btn.setFixedWidth(28); h.addWidget(self.close_btn)
        self.run.clicked.connect(self._run)
        self.never.clicked.connect(self._never)
        self.close_btn.clicked.connect(self.hide)
        self.restyle()
        self.hide()

    def restyle(self):
        """경고 줄(QLabel#warn)과 같은 바탕색. 테마를 바꾼 뒤 다시 부르면 맞춰진다."""
        from . import theme
        c = theme.CUR or theme.LIGHT
        self.setStyleSheet(f"QFrame#scanBar {{ background: {c.get('WARN_BG', '#FDEBEC')}; border: none;"
                           f" border-bottom: 1px solid {c.get('WARN_LINE', '#F5C9CB')}; }}")

    def showEvent(self, e):
        self.restyle()
        super().showEvent(e)

    def check(self):
        """열 때 한 번: 스캔 문서이고 꺼 둔 파일이 아니면 보인다."""
        d = self.tab.doc
        self.setVisible(not is_off(self.tab.win.settings, d.path) and looks_scanned(d))

    def _run(self):
        self.hide()
        self.tab.win.ocr(self.tab)

    def _never(self):
        win = self.tab.win
        turn_off(win.settings, self.tab.doc.path)
        try:
            from .window import save_settings
            save_settings(win.settings)
        except Exception:  # noqa: BLE001
            pass
        self.hide()


def attach(tab, layout) -> ScanBar:
    """DocTab 의 세로 틀 맨 위에 막대를 넣고 검사한다."""
    bar = ScanBar(tab)
    layout.insertWidget(0, bar)
    bar.check()
    return bar
