"""서명된 문서 위 한 줄: 검증 결과 · 서명으로 이동 · 하이라이트 필드 · 서명 속성 · 유효성 검사."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from ..engine import dsign as G


class SignBar(QFrame):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.states: list[G.SigState] = []
        self.setObjectName("signBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout(self); h.setContentsMargins(12, 4, 8, 4); h.setSpacing(8)
        self.label = QLabel(""); h.addWidget(self.label, 1)
        self.b_goto = QPushButton("서명으로 이동"); self.b_mark = QPushButton("하이라이트 필드")
        self.b_props = QPushButton("서명 속성"); self.b_check = QPushButton("유효성 검사")
        for b in (self.b_goto, self.b_mark, self.b_props, self.b_check):
            h.addWidget(b)
        self.b_goto.clicked.connect(self.goto)
        self.b_mark.clicked.connect(self.mark)
        self.b_props.clicked.connect(self.props)
        self.b_check.clicked.connect(lambda: self._dg().validate_all(self.tab.win, self.tab))
        self.restyle()
        self.hide()

    @staticmethod
    def _dg():
        from . import dlg_dsign as DG
        return DG

    def restyle(self):
        from . import theme
        c = theme.CUR or theme.LIGHT
        ok = bool(self.states) and all(s.valid for s in self.states)
        bg = c.get("OK_BG", "#E8F4EA") if ok else c.get("WARN_BG", "#FDEBEC")
        line = c.get("OK_LINE", "#BFDCC5") if ok else c.get("WARN_LINE", "#F5C9CB")
        self.setStyleSheet(f"QFrame#signBar {{ background: {bg}; border: none; border-bottom: 1px solid {line}; }}")

    def check(self):
        """문서를 검증해 띠를 보이거나 숨긴다."""
        if not G.has_signatures(self.tab.doc.fitz):
            self.states = []; self.hide(); return
        self.show_states(self._dg().validate_tab(self.tab.win, self.tab))

    def show_states(self, states: list[G.SigState]):
        self.states = states
        if not states:
            self.hide(); return
        self.label.setText(G.summary(states))
        self.restyle()
        self.show()

    def _first(self):
        return self.states[0] if self.states else None

    def goto(self):
        s = self._first()
        if s is None:
            return
        v = self.tab.viewer
        v.goto(s.page)
        x = next((w["xref"] for w in G.sig_widgets(self.tab.doc.fitz) if w["name"] == s.field), None)
        if x is not None:
            v.select_fields([(s.page, x)])

    def mark(self):
        items = [(w["page"], w["xref"]) for w in G.sig_widgets(self.tab.doc.fitz)]
        if items:
            self.tab.viewer.select_fields(items)

    def props(self):
        s = self._first()
        if s is not None:
            self._dg().show_props(self.tab.win, self.tab, s.field)


def attach(tab, layout) -> SignBar:
    """보안 띠(ProtectBar) 바로 아래에 넣고, 창이 다 뜬 뒤 한 번 검증한다."""
    bar = SignBar(tab)
    pb = getattr(tab, "protect_bar", None)
    k = layout.indexOf(pb) + 1 if pb is not None and layout.indexOf(pb) >= 0 else 0
    layout.insertWidget(k, bar)
    if G.has_signatures(tab.doc.fitz):
        QTimer.singleShot(0, lambda: (not getattr(tab.viewer, "closed", False)) and bar.check())
    return bar
