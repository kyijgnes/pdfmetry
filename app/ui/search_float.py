"""검색 및 바꾸기(Ctrl+T): 문서 위에 떠 있는 판.

검색 패널과 결과를 나눠 쓰지 않고 따로 찾는다(서로 결과를 지우지 않게). 다음/이전은 지금 쪽부터 한 바퀴,
위쪽 띠를 끌어 옮기고 Esc 로 숨는다(숨을 때 강조를 지움).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget)

from ..engine import search_multi as SM
from . import icons
from .search_panel import NO_RESULT, REPLACED, index_after

WIDTH = 360
MARGIN = 16   # 문서 화면 오른쪽 위에서 띄우는 거리


class SearchFloat(QFrame):
    def __init__(self, tab):
        super().__init__(tab.view_stack)
        self.tab = tab
        self.doc = tab.doc
        self.results: list | None = None   # 찾아 둔 결과(문서가 바뀌거나 조건이 바뀌면 버림)
        self._key = None                   # 결과를 만든 (찾을 글자, 대소문자, 단어 단위)
        self.current = -1
        self._quiet = False
        self._drag: QPoint | None = None
        self._placed = False
        self.setObjectName("searchfloat")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setFixedWidth(WIDTH)
        sh = QGraphicsDropShadowEffect(self); sh.setBlurRadius(18); sh.setOffset(0, 2); sh.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(sh)
        v = QVBoxLayout(self); v.setContentsMargins(8, 4, 8, 8); v.setSpacing(6)
        # 머리 띠: 모드 · 고급 검색 · 닫기 (띠를 끌어 옮긴다)
        self.head = QWidget(); self.head.setCursor(Qt.CursorShape.SizeAllCursor)
        hl = QHBoxLayout(self.head); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)
        self.mode = QComboBox(); self.mode.addItems(["검색", "바꾸기"]); self.mode.currentIndexChanged.connect(self._mode_changed)
        hl.addWidget(self.mode); hl.addStretch()
        self.adv = QToolButton(); self.adv.setText("고급 검색"); self.adv.setAutoRaise(True)
        self.adv.clicked.connect(lambda: self.tab.win.on_action("search_advanced"))
        hl.addWidget(self.adv)
        x = QToolButton(); x.setObjectName("quick"); x.setIcon(icons.icon("close", 14)); x.setAutoRaise(True); x.clicked.connect(self.dismiss)
        hl.addWidget(x)
        self.head.installEventFilter(self)
        v.addWidget(self.head)
        # 찾기 줄
        row = QHBoxLayout(); row.setSpacing(4)
        self.edit = QLineEdit(); self.edit.setPlaceholderText("찾을 글자"); self.edit.returnPressed.connect(lambda: self.find(1))
        self.edit.textChanged.connect(self._cond_changed)
        row.addWidget(self.edit, 1)
        self.where = QLabel(""); self.where.setObjectName("dim"); self.where.setMinimumWidth(44)
        self.where.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.where)
        self.prev = QPushButton("이전"); self.prev.clicked.connect(lambda: self.find(-1)); row.addWidget(self.prev)
        self.next = QPushButton("다음"); self.next.clicked.connect(lambda: self.find(1)); row.addWidget(self.next)
        v.addLayout(row)
        opts = QHBoxLayout()
        self.whole = QCheckBox("단어 단위로"); self.case = QCheckBox("대/소문자 구분")
        for c in (self.whole, self.case):
            c.toggled.connect(self._cond_changed); opts.addWidget(c)
        opts.addStretch()
        v.addLayout(opts)
        # 바꾸기 줄(바꾸기 모드에서만)
        self.repl_box = QWidget()
        rl = QHBoxLayout(self.repl_box); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(4)
        self.repl = QLineEdit(); self.repl.setPlaceholderText("바꿀 글자"); self.repl.returnPressed.connect(self.replace_one)
        self.repl.textChanged.connect(self._sync_buttons)
        rl.addWidget(self.repl, 1)
        self.rep1 = QPushButton("바꾸기"); self.rep1.clicked.connect(self.replace_one); rl.addWidget(self.rep1)
        self.repall = QPushButton("모두 바꾸기"); self.repall.clicked.connect(self.replace_all); rl.addWidget(self.repall)
        v.addWidget(self.repl_box)
        self.repl_box.hide()
        tab.view_stack.installEventFilter(self)
        self._sync_buttons()
        self.hide()

    # ── 열고 닫기 ──────────────────────────────────────────────
    def open(self, replace: bool = False):
        if replace:
            self.mode.setCurrentIndex(1)
        if not self.edit.text():
            self.edit.setText(self.tab.search.edit.text())
        self.show(); self.raise_(); self.adjustSize()
        if not self._placed:
            self._placed = True
            self.move(self.parentWidget().width() - self.width() - MARGIN, MARGIN)
        self._clamp()
        self.edit.setFocus(); self.edit.selectAll()

    def dismiss(self):
        """숨기고 강조를 지운다."""
        self.hide()
        self.current = -1
        self._show_pos()
        self.tab.show_hits([], -1, jump=False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.dismiss(); return
        super().keyPressEvent(e)

    def eventFilter(self, obj, ev):
        if obj is self.head:
            t = ev.type()
            if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._drag = ev.globalPosition().toPoint() - self.pos(); return True
            if t == QEvent.Type.MouseMove and self._drag is not None:
                self.move(ev.globalPosition().toPoint() - self._drag); self._clamp(); return True
            if t == QEvent.Type.MouseButtonRelease:
                self._drag = None; return True
        elif obj is self.parentWidget() and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._clamp()
        return super().eventFilter(obj, ev)

    def _clamp(self):
        p = self.parentWidget()
        x = max(0, min(self.x(), p.width() - self.width()))
        y = max(0, min(self.y(), p.height() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ── 상태 ──────────────────────────────────────────────────
    def _args(self):
        return self.edit.text().strip(), self.case.isChecked(), self.whole.isChecked()

    def _mode_changed(self, i: int):
        self.repl_box.setVisible(i == 1)
        self.adjustSize(); self._clamp(); self._sync_buttons(); self._show_pos()

    def _cond_changed(self, *_):
        """조건이 바뀌면 다음 찾기는 처음부터."""
        self.results = None; self.current = -1
        self._show_pos(); self._sync_buttons()

    def can_modify(self) -> bool:
        return SM.can_modify(self.doc.fitz)

    def _sync_buttons(self, *_):
        q = bool(self._args()[0])
        self.prev.setEnabled(q); self.next.setEnabled(q)
        ok = q and bool(self.repl.text()) and self.can_modify()
        self.rep1.setEnabled(ok); self.repall.setEnabled(ok)

    def _show_pos(self):
        """검색 모드에서 「지금/전체」."""
        n = len(self.results or [])
        self.where.setText(f"{self.current + 1}/{n}" if self.mode.currentIndex() == 0 and n and self.current >= 0 else "")

    def doc_changed(self, *_):
        """문서가 바뀌었다: 세어 둔 결과를 버린다(다음 찾기 때 다시 셈)."""
        if self._quiet:
            return
        self.results = None; self.current = -1
        self._show_pos()

    def _ensure(self) -> bool:
        """결과를 찾아 둔다. 새로 찾았으면 True."""
        key = self._args()
        if self.results is not None and key == self._key:
            return False
        q, case, whole = key
        self.results = SM.panel_search(self.doc, q, case=case, whole=whole)
        self._key = key; self.current = -1
        return True

    def _emit(self):
        hits = [(h.page, h.all_rects()) for h in self.results or []]
        self.tab.show_hits(hits, self.current, jump=True)
        self._show_pos()

    # ── 찾기 · 바꾸기 ─────────────────────────────────────────
    def find(self, d: int = 1) -> bool:
        """다음(d=1)·이전(d=-1). 처음이면 지금 쪽부터. 하나도 없으면 알리고 False."""
        if not self._args()[0]:
            return False
        fresh = self._ensure() or self.current < 0
        if not self.results:
            self.tab.show_hits([], -1, jump=False); self._show_pos()
            QMessageBox.information(self, "검색", NO_RESULT)
            return False
        n = len(self.results)
        if fresh:
            k = index_after(self.results, (self.tab.viewer.current, -1e9, -1e9))
            self.current = k if d > 0 else (k - 1) % n
        else:
            self.current = (self.current + d) % n
        self._emit()
        return True

    def _notify(self, pages):
        self._quiet = True
        try:
            self.tab.changed(pages)
        finally:
            self._quiet = False

    def replace_one(self):
        """지금 결과를 바꾸고 바로 다음으로. 지금 결과가 없으면 이번에는 찾기만."""
        if not self.rep1.isEnabled():
            return
        self._ensure()
        if not (self.results and 0 <= self.current < len(self.results)):
            self.find(1); return
        h = self.results[self.current]
        self.doc.replace_text([h], self.repl.text())
        self._notify([h.page])
        # 바꾼 글자 뒤부터 다시 센다
        self.results = SM.panel_search(self.doc, self._key[0], case=self._key[1], whole=self._key[2])
        if not self.results:
            self.current = -1; self.tab.show_hits([], -1, jump=False); self._show_pos(); return
        self.current = index_after(self.results, (h.page, h.rect.y0, h.rect.x1 - 1))
        self._emit()

    def replace_all(self) -> int:
        if not self.repall.isEnabled():
            return 0
        self.results = None
        self._ensure()
        if not self.results:
            QMessageBox.information(self, "검색", NO_RESULT); return 0
        todo = self.results
        n = self.doc.replace_text(todo, self.repl.text())
        self._notify(sorted({h.page for h in todo}))
        self.results = None; self.current = -1
        self.tab.show_hits([], -1, jump=False); self._show_pos()
        QMessageBox.information(self, "바꾸기", REPLACED.format(n))
        return n


def open_for(tab, replace: bool = False) -> SearchFloat:
    """그 탭의 떠 있는 판을 연다(처음이면 만든다)."""
    f = getattr(tab, "find_float", None)
    if f is None:
        f = tab.find_float = SearchFloat(tab)
    f.open(replace)
    return f
