"""검색 & 교정 왼쪽 패널: 찾은 곳을 목록으로 보고 체크한 것(또는 모두)에 교정 표시를 단다.

정규식·자주 쓰는 것(전화·주민번호·이메일·숫자)·가릴 색·쪽 범위는 「고급」 을 펼쳐서."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget)

from ..engine import tools as T
from .dialogs import ColorButton, PageRangeEdit
from .sidebar import panel_head

PRESETS = (("직접 입력", None),
           # 앞뒤가 숫자면 안 잡는다(주민번호·학번 안의 숫자에 전화번호가 걸리던 것). 구분자는 - . 띄어쓰기, 외국인번호(5~8)도.
           ("전화번호(010-0000-0000)", r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"),
           ("주민등록번호", r"(?<!\d)\d{6}\s*-?\s*[1-8]\d{6}(?!\d)"),
           ("이메일 주소", r"[\w.+-]+@[\w-]+\.[\w.]+"),
           ("숫자 6자리 이상", r"\d{6,}"))
CHANGED_ASK = "문서가 마지막으로 검색된 후 수정되어 검색 결과가 더 이상 유효하지 않습니다. 검색을 반복하시겠습니까?"


class RedactPanel(QWidget):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.hits: list[dict] = []
        self._stamp = None
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(panel_head("검색 & 교정"))
        body = QWidget(); lay = QVBoxLayout(body); lay.setContentsMargins(8, 6, 8, 8); lay.setSpacing(6)
        outer.addWidget(body, 1)
        row = QHBoxLayout()
        self.text = QLineEdit(); self.text.setPlaceholderText("검색할 텍스트"); self.text.returnPressed.connect(self.find)
        self.go = QPushButton("검색"); self.go.clicked.connect(self.find)
        row.addWidget(self.text, 1); row.addWidget(self.go); lay.addLayout(row)
        self.whole = QCheckBox("단어 단위로"); self.case = QCheckBox("대/소문자 구분")
        lay.addWidget(self.whole); lay.addWidget(self.case)
        self.adv_btn = QToolButton(); self.adv_btn.setText("고급 ▸"); self.adv_btn.setCheckable(True)
        self.adv_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly); self.adv_btn.setAutoRaise(True)
        lay.addWidget(self.adv_btn)
        self.adv = QWidget(); f = QFormLayout(self.adv); f.setContentsMargins(0, 0, 0, 0)
        self.regex = QCheckBox("정규식으로"); f.addRow(self.regex)
        self.presets = QComboBox(); self.presets.addItems([n for n, _ in PRESETS]); f.addRow("자주 쓰는 것", self.presets)
        self.fill = ColorButton((0, 0, 0)); f.addRow("가릴 색", self.fill)
        d = tab.doc
        self.pages = PageRangeEdit(d.page_count, 0); f.addRow("쪽", self.pages)
        self.adv.hide(); lay.addWidget(self.adv)
        self.adv_btn.toggled.connect(lambda on: (self.adv.setVisible(on), self.adv_btn.setText("고급 ▾" if on else "고급 ▸")))
        self.presets.currentIndexChanged.connect(self._preset)
        self.count = QLabel("결과 없음"); self.count.setObjectName("dim"); lay.addWidget(self.count)
        self.list = QListWidget(); lay.addWidget(self.list, 1)
        self.list.itemClicked.connect(self._goto)
        self.all_btn = QPushButton("모두 교정 적용"); self.sel_btn = QPushButton("고른 것만 교정")
        self.clear_btn = QPushButton("결과 지우기")
        self.all_btn.clicked.connect(lambda: self.mark(only_checked=False))
        self.sel_btn.clicked.connect(lambda: self.mark(only_checked=True))
        self.clear_btn.clicked.connect(self.clear)
        lay.addWidget(self.all_btn); lay.addWidget(self.sel_btn); lay.addWidget(self.clear_btn)
        self._sync()

    def _preset(self, k: int):
        pat = PRESETS[k][1]
        if pat:
            self.text.setText(pat); self.regex.setChecked(True)

    def _doc_stamp(self):
        d = self.tab.doc
        return id(d.fitz), len(getattr(d, "_undo", ())), d.page_count

    def _sync(self):
        n = len(self.hits)
        self.count.setText(f"{n}개" if n else "결과 없음")
        for b in (self.all_btn, self.sel_btn, self.clear_btn):
            b.setEnabled(bool(n))

    def focus(self):
        self.text.setFocus(); self.text.selectAll()

    def find(self):
        d = self.tab.doc
        try:
            with d.lock:
                self.hits = T.redact_find(d.fitz, self.text.text(), whole_word=self.whole.isChecked(),
                                          case=self.case.isChecked(), regex=self.regex.isChecked(),
                                          pages=self.pages.pages() if self.adv_btn.isChecked() else None)
        except T.EngineError as e:
            QMessageBox.warning(self, "검색 & 교정", str(e)); return
        self._stamp = self._doc_stamp()
        self.list.clear()
        for h in self.hits:
            ctx = " ".join((h.get("ctx") or h["text"]).split())
            it = QListWidgetItem(f"{h['page'] + 1}쪽 · {ctx}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable); it.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self._sync()
        self._show_hits()

    def _show_hits(self):
        try:
            self.tab.show_hits([(h["page"], h["rects"]) for h in self.hits], 0)
        except Exception:  # noqa: BLE001
            pass

    def _goto(self, it: QListWidgetItem):
        h = self.hits[self.list.row(it)]
        r = h["rects"][0] if h["rects"] else None
        self.tab.viewer.goto(h["page"], r.y0 if r is not None else None, rect=r)

    def checked(self) -> list[int]:
        return [i for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def mark(self, only_checked: bool = False) -> int:
        if self._stamp is not None and self._stamp != self._doc_stamp():
            if QMessageBox.question(self, "검색 & 교정", CHANGED_ASK) == QMessageBox.StandardButton.Yes:
                self.find()
            return 0
        rows = self.checked() if only_checked else list(range(len(self.hits)))
        if not rows:
            self.tab.win.say("교정할 결과를 체크하세요"); return 0
        d = self.tab.doc
        from . import dlg_redact as DR
        st = DR.tool_style(self.tab.viewer.props)
        d.snapshot()
        with d.lock:
            n = T.mark_hits(d.fitz, [self.hits[i] for i in rows], self.fill.color, st)
        pages = sorted({self.hits[i]["page"] for i in rows})
        self.tab.changed(pages)
        DR.marked(self.tab)
        self.tab.win.say(f"{n}곳에 교정 표시를 했습니다. 「교정 적용」 을 누르면 지워집니다.")
        self.clear()
        return n

    def clear(self):
        self.hits = []; self._stamp = None
        self.list.clear(); self._sync()
        try:
            self.tab.show_hits([], 0)
        except Exception:  # noqa: BLE001
            pass


def attach(tab) -> RedactPanel:
    """탭의 왼쪽 패널 묶음에 「redact」 로 붙인다."""
    p = RedactPanel(tab)
    tab.panels["redact"] = p
    tab.side.addWidget(p)
    return p
