"""영역 OCR: 문서 위에 사각형을 여러 개(여러 쪽) 그리고 [인식] 하면 그 자리에 보이지 않는 글자층을 넣는다.

- 도구 「ocrarea」 가 켜지면 보기 오른쪽에 패널(언어 인식 체크 목록 · 고른 언어 이름 줄 · [인식])이 뜬다.
- 사각형: 누르면 고름(Ctrl 로 더 고름), 오른쪽 클릭 「삭제」·「모두 선택」, Ctrl+A · Delete.
- 인식은 사본에서 하고 바꿔 끼워 Ctrl+Z 한 번에 되돌린다. 끝나면 도구를 끈다.
보기(PageView.hooks)에 얹혀 그리기·누르기·키를 먼저 받는다.
"""
from __future__ import annotations

import fitz
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout

from . import dlg_ocr as DO

EDGE = QColor(113, 128, 230)
PICKED = QColor(240, 126, 35)
PANEL_W = 240
TITLE = "영역 문자인식(OCR)"


def run_areas(tab, areas: list[tuple[int, fitz.Rect]], langs: list[str]) -> int | None:
    """사각형들을 인식해 그 쪽에 글자층으로 넣는다. 넣은 낱말 수(실패·없음이면 None)."""
    from . import dialogs as D
    from ..engine import ocr as O
    win = tab.win
    if not areas:
        return None
    if not langs:
        QMessageBox.warning(win, "영역 OCR", DO.NO_LANG_TEXT); return None
    win.prefs["ocr_langs"] = list(langs)
    try:
        from .window import save_settings
        save_settings(win.settings)
    except Exception:  # noqa: BLE001
        pass
    work = tab.doc.copy()
    n = D.ProgressDialog(win, TITLE, O.ocr_area_into, work, [(p, fitz.Rect(r)) for p, r in areas], lang=list(langs)).run()
    if n is None:
        work.close(); return None
    tab.doc.adopt(work)   # 되돌리기 사본을 뜬 뒤 바꿔 끼운다(Ctrl+Z 한 번)
    tab.changed(sorted({p for p, _ in areas}))
    win.say(f"글자 {n}개를 인식해 넣었습니다. 이제 찾기·복사가 됩니다." if n else "인식한 글자가 없습니다")
    return n


class Panel(QFrame):
    def __init__(self, owner: "OcrArea"):
        super().__init__(owner.view)
        self.owner = owner
        self.setObjectName("ocrAreaPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setFixedWidth(PANEL_W)
        v = QVBoxLayout(self); v.setContentsMargins(10, 8, 10, 10); v.setSpacing(6)
        t = QLabel("영역 OCR"); f = t.font(); f.setBold(True); t.setFont(f); v.addWidget(t)
        v.addWidget(QLabel("언어 인식"))
        avail = DO.languages()
        self.langs = DO.LangList(avail, DO.current_langs(owner.tab.win.prefs, avail), names_line=True)
        v.addWidget(self.langs, 1)
        self.run = QPushButton("인식"); self.run.setDefault(True)
        self.run.clicked.connect(owner.recognize)
        v.addWidget(self.run)
        self.setMinimumHeight(260)

    def sync(self):
        self.run.setEnabled(bool(self.owner.rects))


class OcrArea(QObject):
    """문서 탭 하나의 영역 OCR 사각형들."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.view = tab.viewer
        self.rects: list[tuple[int, fitz.Rect]] = []
        self.picked: set[int] = set()
        self.active = False
        self.panel: Panel | None = None
        self.view.hooks.append(self)
        self.view.installEventFilter(self)

    # ── 상태 ─────────────────────────────────────────────
    def tool_changed(self, tool: str):
        on = tool == "ocrarea"
        if on == self.active:
            return
        self.active = on
        if on:
            if self.panel is None:
                self.panel = Panel(self)
            self.panel.sync(); self._place(); self.panel.show(); self.panel.raise_()
        else:
            self.rects.clear(); self.picked.clear()
            if self.panel is not None:
                self.panel.hide()
        self.view.viewport().update()

    def eventFilter(self, obj, ev):
        if obj is self.view and ev.type() == QEvent.Type.Resize and self.panel is not None:
            self._place()
        return False

    def _place(self):
        vp = self.view.viewport().geometry()
        h = min(max(260, vp.height() // 2), vp.height() - 16)
        self.panel.setGeometry(vp.right() - PANEL_W - 10, vp.top() + 10, PANEL_W, max(120, h))

    def add(self, page: int, rect):
        r = fitz.Rect(rect).normalize()
        if r.width < 2 or r.height < 2:
            return
        self.rects.append((page, r))
        self.picked = {len(self.rects) - 1}
        if self.panel is not None:
            self.panel.sync()
        self.view.viewport().update()

    def delete_picked(self):
        if not self.picked:
            return
        self.rects = [x for k, x in enumerate(self.rects) if k not in self.picked]
        self.picked.clear()
        if self.panel is not None:
            self.panel.sync()
        self.view.viewport().update()

    def select_all(self):
        self.picked = set(range(len(self.rects)))
        self.view.viewport().update()

    def recognize(self):
        if not self.rects:
            return
        langs = self.panel.langs.checked() if self.panel is not None else DO.current_langs(self.tab.win.prefs)
        if not langs:
            QMessageBox.warning(self.tab.win, "영역 OCR", DO.NO_LANG_TEXT); return
        if run_areas(self.tab, list(self.rects), langs) is not None:
            self.tab.win.on_tool("select")   # 끝나면 도구를 끈다(사각형도 걷힌다)

    # ── 보기 도우미 ───────────────────────────────────────
    def _hit(self, i: int, sp: QPointF) -> int:
        for k in range(len(self.rects) - 1, -1, -1):
            pg, r = self.rects[k]
            if pg == i and self.view.from_page_rect(pg, r).contains(sp):
                return k
        return -1

    def press(self, e: QMouseEvent, i: int, sp: QPointF) -> bool:
        if not self.active:
            return False
        k = self._hit(i, sp) if i >= 0 else -1
        if e.button() == Qt.MouseButton.RightButton:
            if k >= 0 and k not in self.picked:
                self.picked = {k}
            self.view.viewport().update()
            m = QMenu(self.view)
            m.addAction("삭제", self.delete_picked).setEnabled(bool(self.picked))
            m.addAction("모두 선택", self.select_all).setEnabled(bool(self.rects))
            m.exec(e.globalPosition().toPoint())
            return True
        if e.button() == Qt.MouseButton.LeftButton and k >= 0:
            if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.picked ^= {k}
            else:
                self.picked = {k}
            self.view.viewport().update()
            return True
        if e.button() == Qt.MouseButton.LeftButton:
            self.picked.clear()
        return False

    def key(self, e: QKeyEvent) -> bool:
        if not self.active:
            return False
        if e.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all(); return True
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.picked:
            self.delete_picked(); return True
        return False

    def paint(self, p: QPainter, i: int):
        if not self.active:
            return
        for k, (pg, r) in enumerate(self.rects):
            if pg != i:
                continue
            c = PICKED if k in self.picked else EDGE
            fill = QColor(c); fill.setAlpha(30)
            p.setPen(QPen(c, 2)); p.setBrush(fill)
            p.drawRect(self.view.from_page_rect(pg, r))
