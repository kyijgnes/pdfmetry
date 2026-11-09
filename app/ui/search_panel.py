"""왼쪽 막대 검색 패널: 찾기 · 결과 목록(찾은 말 주황) · 주석 포함 · 바꾸기 하나/모두."""
from __future__ import annotations

import html
import time

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QStackedWidget, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

from ..engine import search_multi as SM
from ..engine.document import Document, search_pattern
from . import icons

FOUND_COLOR = "#FF8450"   # 결과 줄의 찾은 말
GUIDE = "현재 문서에서 단어나 구문을 검색하고 결과를 나열합니다."
EMPTY = "검색 텍스트는 비워둘 수 없습니다."
NONE = "결과 없음"
NO_RESULT = "검색이 완료되었습니다. 찾고 있는 것과 일치한 항목이 없습니다."
REPLACED = "모두 완료되었습니다. {0}개를 교체했습니다."
PREFIX_ROLE = Qt.ItemDataRole.UserRole + 1   # 결과 줄 앞머리(「3쪽」) 글자 수: 그 뒤만 색칠


def index_after(results: list, near: tuple) -> int:
    """near=(쪽, y, x) 자리이거나 그 뒤에 오는 첫 결과(끝을 넘으면 처음). 결과가 없으면 -1."""
    if not results:
        return -1
    pg, y, x = near
    for k, h in enumerate(results):
        r = h.rect
        if h.page > pg or (h.page == pg and (r.y0 > y + 2 or (abs(r.y0 - y) <= 2 and r.x0 >= x - 1))):
            return k
    return 0


def found_html(text: str, q: str, case: bool, start: int = 0) -> str:
    """결과 줄 한 줄: start 뒤의 찾은 말만 주황 글자(띄어쓰기가 끼어도 맞게 검색과 같은 규칙으로)."""
    spans = []
    if q:
        try:
            spans = [(m.start() + start, m.end() + start) for m in search_pattern(q, case=case).finditer(text[start:])
                     if m.end() > m.start()]
        except Exception:  # noqa: BLE001
            spans = []
    out, i = [], 0
    for a, b in spans:
        out += [html.escape(text[i:a]), f"<span style='color:{FOUND_COLOR}'>", html.escape(text[a:b]), "</span>"]
        i = b
    out.append(html.escape(text[i:]))
    return "".join(out)


class FoundDelegate(QStyledItemDelegate):
    """결과 목록 칸: 찾은 말만 #FF8450 으로 그린다(나머지는 Qt 기본 그대로). src._shown_q = (찾을 글자, 대소문자)."""

    def __init__(self, view, src):
        super().__init__(view)
        self.panel = src

    def paint(self, p, opt, idx):
        from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument, QTextOption
        from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
        o = QStyleOptionViewItem(opt); self.initStyleOption(o, idx)
        q, case = self.panel._shown_q
        text = o.text
        if not q:
            return super().paint(p, opt, idx)
        o.text = ""
        st = o.widget.style() if o.widget else QApplication.style()
        st.drawControl(QStyle.ControlElement.CE_ItemViewItem, o, p, o.widget)
        r = st.subElementRect(QStyle.SubElement.SE_ItemViewItemText, o, o.widget).adjusted(3, 0, -3, 0)
        doc = QTextDocument(); doc.setDocumentMargin(0); doc.setDefaultFont(o.font)
        opt_t = QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter); opt_t.setWrapMode(QTextOption.WrapMode.NoWrap)
        doc.setDefaultTextOption(opt_t)
        text = o.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width()))
        doc.setHtml(f"<span style='white-space:pre'>{found_html(text, q, case, idx.data(PREFIX_ROLE) or 0)}</span>")
        ctx = QAbstractTextDocumentLayout.PaintContext()
        role = QPalette.ColorRole.HighlightedText if o.state & QStyle.StateFlag.State_Selected else QPalette.ColorRole.Text
        ctx.palette.setColor(QPalette.ColorRole.Text, o.palette.color(role))
        p.save()
        p.translate(r.left(), r.top() + (r.height() - doc.size().height()) / 2)
        p.setClipRect(QRectF(0, 0, r.width(), r.height()))
        doc.documentLayout().draw(p, ctx)
        p.restore()


class SearchPanel(QWidget):
    hitsChanged = Signal(list, int)   # [(page, [rect…])], current
    replaced = Signal(object)         # 바꾼 쪽 목록(None 이면 여러 쪽)
    pickAnnot = Signal(int, int)      # 주석 결과를 눌렀다: 쪽, xref

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        from .sidebar import panel_head   # sidebar 가 이 파일을 머리에서 부른다(서로 부르기)
        self.doc = doc
        self.hits = []       # [(쪽, [줄마다 네모])]
        self.results = []    # SearchHit (주석이면 search_multi.AnnotHit)
        self.current = -1
        self._gen = 0                      # 찾기를 새로 하면 옛 일꾼 결과는 버린다
        self._workers: list[QThread] = []
        self._shown_q = ("", False)        # 지금 목록을 만든 찾을 글자·대소문자(색칠용)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(panel_head("검색"))
        lay = QVBoxLayout(); lay.setContentsMargins(8, 8, 8, 6); lay.setSpacing(6); outer.addLayout(lay)
        row = QHBoxLayout()
        self.edit = QLineEdit(); self.edit.setPlaceholderText("찾을 글자"); self.edit.returnPressed.connect(self.run)
        self.edit.textChanged.connect(self._text_changed)
        row.addWidget(self.edit)
        b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon("search", 16)); b.setAccessibleName("찾기"); b.clicked.connect(self.run); row.addWidget(b)
        bp = QToolButton(); bp.setObjectName("quick"); bp.setIcon(icons.icon("up", 16)); bp.setAccessibleName("이전 결과"); bp.clicked.connect(lambda: self.step(-1))
        bn = QToolButton(); bn.setObjectName("quick"); bn.setIcon(icons.icon("down", 16)); bn.setAccessibleName("다음 결과"); bn.clicked.connect(lambda: self.step(1))
        row.setSpacing(0); row.addSpacing(4); row.addWidget(bp); row.addWidget(bn)
        lay.addLayout(row)
        opts = QHBoxLayout()
        self.case = QCheckBox("대/소문자 구분"); self.whole = QCheckBox("단어 단위로")
        opts.addWidget(self.case); opts.addWidget(self.whole); opts.addStretch()
        lay.addLayout(opts)
        opts2 = QHBoxLayout()
        self.comments = QCheckBox("주석 포함")
        self.count = QLabel(""); self.count.setObjectName("dim")
        opts2.addWidget(self.comments); opts2.addStretch(); opts2.addWidget(self.count)
        lay.addLayout(opts2)
        # 바꾸기
        self.repl = QLineEdit(); self.repl.setPlaceholderText("바꿀 글자"); self.repl.returnPressed.connect(self.replace_one)
        lay.addWidget(self.repl)
        rrow = QHBoxLayout()
        rb1 = QPushButton("바꾸기"); rb1.clicked.connect(self.replace_one); rrow.addWidget(rb1)
        rb = QPushButton("모두 바꾸기"); rb.clicked.connect(self.replace_all); rrow.addWidget(rb)
        lay.addLayout(rrow)
        self._quiet = False   # 내가 바꾼 것이라 doc_changed 로 다시 찾지 않음
        self.jump = True      # hitsChanged 를 받는 쪽이 지금 결과로 옮겨 갈지
        self.list = QListWidget(); self.list.itemClicked.connect(self._pick)
        self.list.setUniformItemSizes(True)  # 한 줄 글이라 칸 높이가 같다. 안 두면 결과 300건에 칸 재기만 0.24초
        # 고른 줄로 저절로 굴리면 결과마다 목록 전체 칸을 다시 재어 0.13초(298건이면 1.3초) 멈췄다. 보이게 하는 것은 step 이 한 줄만
        self.list.setAutoScroll(False)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setItemDelegate(FoundDelegate(self.list, self))
        # 찾기 전에는 목록 자리에 안내 한 줄
        self.guide = QLabel(GUIDE); self.guide.setObjectName("dim"); self.guide.setWordWrap(True)
        self.guide.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.guide.setContentsMargins(10, 8, 10, 8)
        f = self.guide.font(); f.setItalic(True); self.guide.setFont(f)
        self.body = QStackedWidget(); self.body.addWidget(self.guide); self.body.addWidget(self.list)
        outer.addWidget(self.body, 1)

    def focus(self):
        self.edit.setFocus(); self.edit.selectAll()

    def focus_replace(self):
        """찾을 글자가 있으면 바꿀 글자 칸으로."""
        (self.repl if self.edit.text() else self.edit).setFocus()

    BG_PAGES = 60  # 이보다 큰 문서는 일꾼에서 찾는다(959쪽은 글자 뽑기만 0.9초)

    def run(self, sync: bool = False):
        """Enter·돋보기: 찾을 글자가 비었으면 알리고 멈춘다(칸을 비우는 것은 결과 지우기)."""
        if not self._args()[0]:
            QMessageBox.warning(self, "검색", EMPTY)
            return
        self._search(sync=sync)

    def _args(self):
        return self.edit.text().strip(), self.case.isChecked(), self.whole.isChecked(), self.comments.isChecked()

    def _text_changed(self, text: str):
        if not text.strip() and (self.results or self.current >= 0):
            self._clear()
        elif not text.strip():
            self.body.setCurrentWidget(self.guide)

    def _clear(self):
        self._gen += 1
        self.list.clear(); self.hits = []; self.results = []; self.current = -1
        self.count.setText(""); self.body.setCurrentWidget(self.guide)
        self.hitsChanged.emit([], -1)

    def _search(self, sync: bool = False, near: tuple | None = None, jump: bool = True):
        """전부 다시 찾는다. near=(쪽, y, x) 를 주면 그 자리 다음 결과를 지금 결과로(문서가 바뀌어 다시 찾을 때 자리를 지킨다)."""
        q, case, whole, comments = self._args()
        self.list.clear()
        self.hits = []; self.results = []
        self._gen += 1
        if not q:
            self._clear(); return
        if sync or self.doc.page_count <= self.BG_PAGES:
            self._show(SM.panel_search(self.doc, q, case=case, whole=whole, comments=comments), near, jump)
            return
        self.count.setText("찾는 중")
        w = _SearchWorker(self.doc, q, case, whole, comments)
        w.found.connect(lambda res, g=self._gen: self._show(res, near, jump) if g == self._gen else None)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _show(self, res, near: tuple | None = None, jump: bool = True):
        """jump=False 면 강조만 고치고 보기는 그 자리에 둔다(문서를 고쳐 다시 찾았을 때)."""
        self.jump = jump
        self.results = res
        self.hits = [(h.page, h.all_rects()) for h in res]
        q, case, _w, _c = self._args()
        self._shown_q = (q, case)
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for h in res:
            head = f"{h.page + 1}쪽 주석   " if isinstance(h, SM.AnnotHit) else f"{h.page + 1}쪽   "
            self.list.addItem(head + h.context[:60])
            self.list.item(self.list.count() - 1).setData(PREFIX_ROLE, len(head))
        self.list.setUpdatesEnabled(True)
        self.body.setCurrentWidget(self.list)
        self.current = index_after(self.results, near) if near else (0 if self.hits else -1)
        if self.hits:
            self.list.setCurrentRow(self.current)
            self.list.scrollToItem(self.list.item(self.current))
        self._count()
        self.hitsChanged.emit(self.hits, self.current)

    def _count(self):
        """「3/12」: 지금 결과 / 모두. 없으면 「결과 없음」."""
        self.count.setText(f"{self.current + 1}/{len(self.hits)}" if self.hits else (NONE if self.edit.text().strip() else ""))

    def _index_after(self, near: tuple) -> int:
        return index_after(self.results, near)

    def step(self, d: int):
        if not self.hits:
            return
        self.current = (self.current + d) % len(self.hits)
        self.list.setCurrentRow(self.current)
        self.list.scrollToItem(self.list.item(self.current))
        self._count()
        self.jump = True
        self.hitsChanged.emit(self.hits, self.current)
        self._pick_annot()

    def _pick(self, it):
        self.current = self.list.row(it)
        self.jump = True
        self._count()
        self.hitsChanged.emit(self.hits, self.current)
        self._pick_annot()

    def _pick_annot(self):
        h = self.results[self.current] if 0 <= self.current < len(self.results) else None
        if isinstance(h, SM.AnnotHit):
            self.pickAnnot.emit(h.page, h.xref)

    def _splice(self, pages: set[int], near: tuple | None = None, jump: bool = True):
        """그 쪽만 다시 찾아 결과에 끼운다(한 쪽 4ms 안팎. 959쪽 전체를 다시 찾으면 1초)."""
        q, case, whole, comments = self._args()
        cur = self.results[self.current] if 0 <= self.current < len(self.results) else None
        new = SM.panel_search(self.doc, q, pages=sorted(pages), case=case, whole=whole, comments=comments) if q else []
        res = sorted([h for h in self.results if h.page not in pages] + new, key=lambda h: h.page)   # 쪽 안 차례는 그대로
        if near is None and cur is not None:
            near = (cur.page, cur.rect.y0, cur.rect.x0)
        self._show(res, near, jump)

    def doc_changed(self, pages: list[int] | None, structural: bool = False):
        """문서가 바뀌었다: 찾아 둔 결과의 자리가 틀릴 수 있어 다시 찾는다(바뀐 쪽만, 쪽 구조가 바뀌면 전부)."""
        if self._quiet or not self.edit.text().strip():
            return
        cur = self.results[self.current] if 0 <= self.current < len(self.results) else None
        near = (cur.page, cur.rect.y0, cur.rect.x0) if cur else None
        if structural or pages is None:
            self._search(near=near, jump=False)
        else:
            self._splice(set(pages), near, jump=False)

    def _text_results(self):
        return [h for h in self.results if not isinstance(h, SM.AnnotHit)]

    def replace_one(self):
        """지금 결과 하나를 바꾸고 다음 결과로(「바꾸기」). 아직 안 찾았으면 이번에는 찾기만. 주석 결과는 건너뛴다."""
        if not self.hits:
            self.run(); return
        if not (0 <= self.current < len(self.results)):
            return
        h = self.results[self.current]
        if isinstance(h, SM.AnnotHit):
            self.step(1); return
        self.doc.replace_text([h], self.repl.text())
        self._quiet = True
        try:
            self.replaced.emit([h.page])
        finally:
            self._quiet = False
        # 바꾼 글자 뒤부터(바꾼 글자에 찾는 말이 또 들어 있어도 같은 자리를 다시 고르지 않게)
        self._splice({h.page}, (h.page, h.rect.y0, h.rect.x1 - 1))

    def replace_all(self):
        if not self.hits:
            if not self._args()[0]:
                QMessageBox.warning(self, "검색", EMPTY); return
            self._search(sync=True)
        todo = self._text_results()
        if not todo:
            QMessageBox.information(self, "검색", NO_RESULT); return
        n = self.doc.replace_text(todo, self.repl.text())
        QMessageBox.information(self, "바꾸기", REPLACED.format(n))
        self._quiet = True
        try:
            self.replaced.emit(sorted({h.page for h in todo}))
        finally:
            self._quiet = False
        self._search()

    def close(self):
        self._gen += 1
        for w in list(self._workers):
            w.wait(3000)


class _SearchWorker(QThread):
    found = Signal(object)

    def __init__(self, doc: Document, q: str, case: bool, whole: bool, comments: bool = False):
        super().__init__()
        self.doc, self.args = doc, (q, case, whole, comments)

    def run(self):
        last = [time.perf_counter()]

        def breathe(*_):
            # 그냥 두면 처음 찾을 때 이 스레드가 GIL 을 끝까지 쥐어 화면이 0.8초 멈췄다(959쪽).
            # 20ms 마다 1ms 씩 놓아 준다(쪽마다 놓으면 찾는 시간이 두 배)
            if time.perf_counter() - last[0] > 0.02:
                time.sleep(0.001)
                last[0] = time.perf_counter()
        q, case, whole, comments = self.args
        try:
            res = SM.panel_search(self.doc, q, case=case, whole=whole, comments=comments, progress=breathe)
        except Exception:  # noqa: BLE001  찾는 사이 문서가 닫혔다
            return
        self.found.emit(res)
