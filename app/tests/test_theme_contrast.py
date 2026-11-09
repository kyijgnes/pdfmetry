"""테마 색 대비(WCAG 2.1 AA)와 키보드 초점 테두리 시험.

글자 4.5:1, 큰 글자·칸 경계·상태 표시 3:1. 꺼진 단추·칸(비활성)은 기준 밖이라 따로 「더 흐린가」만 본다.
초점은 위젯 종류마다 초점 전후를 그려 보고, 초점 뒤에만 진한 파랑(FOCUS) 테두리가 생기는지 센다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app.ui import theme  # noqa: E402


def _lum(hexcol: str) -> float:
    h = hexcol.lstrip("#")
    out = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = out
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


TEXT, NONTEXT = 4.5, 3.0
WHITE = "#FFFFFF"

# (어디, 앞, 뒤, 기준). 앞·뒤는 테마 열쇠나 #색
PAIRS = [
    ("본문 글자", "TEXT", "BG", TEXT),
    ("본문 글자(패널)", "TEXT", "PANEL", TEXT),
    ("본문 글자(카드)", "TEXT", "CARD", TEXT),
    ("본문 글자(입력칸)", "TEXT", "FIELD", TEXT),
    ("흐린 글자(바탕)", "TEXT_DIM", "BG", TEXT),
    ("흐린 글자(패널 섹션 제목)", "TEXT_DIM", "PANEL", TEXT),
    ("흐린 글자(상태줄)", "TEXT_DIM", "BAR", TEXT),
    ("문서 탭(안 고른 탭)", "TEXT_DIM", "TITLEBAR", TEXT),
    ("문서 탭 마우스 올림", "TEXT_DIM", "HOVER", TEXT),
    ("자리표시 글자", "TEXT_DIM", "FIELD", TEXT),
    ("고른 목록 항목 글자", "TEXT", "SELECT_BG", TEXT),
    ("리본 탭 고름·마우스 올림", "ACCENT_TEXT", "BG", TEXT),
    ("리본 고른 단추", "ACCENT_TEXT", "SELECT_BG", TEXT),
    ("서식 단추 켜짐(카드)", "ACCENT_TEXT", "SELECT_BG", TEXT),
    ("서식 단추 꺼짐(카드)", "TEXT", "FIELD", TEXT),
    ("인쇄 링크", "ACCENT_TEXT", "PANEL", TEXT),
    ("인쇄 탭 고름", "ACCENT_TEXT", "PANEL", TEXT),
    ("대화상자 탭 고름", "ACCENT_TEXT", "BG", TEXT),
    ("기본 단추 글자", WHITE, "PRIMARY", TEXT),
    ("기본 단추 마우스 올림·누름", WHITE, "PRIMARY_HOVER", TEXT),
    ("입력칸 고른 글자", WHITE, "PRIMARY", TEXT),
    ("경고줄 글자", "WARN_TEXT", "WARN_BG", TEXT),
    ("툴팁 글자", "TEXT", "BG", TEXT),
    ("입력칸·체크칸 경계", "FIELD_LINE", "FIELD", NONTEXT),
    ("입력칸 경계(패널 위)", "FIELD_LINE", "PANEL", NONTEXT),
    ("입력칸 경계(카드 위)", "FIELD_LINE", "CARD", NONTEXT),
    ("스크롤 손잡이", "SCROLL", "BG", NONTEXT),
    ("스크롤 손잡이(패널)", "SCROLL", "PANEL", NONTEXT),
    ("탭 밑줄·고른 막대", "ACCENT", "BAR", NONTEXT),
    ("리본 탭 밑줄", "ACCENT", "BG", NONTEXT),
    ("고른 목록 항목 왼쪽 막대", "ACCENT", "SELECT_BG", NONTEXT),
    ("체크 고름 표시", WHITE, "ACCENT", NONTEXT),
    ("서식 단추 켜짐 테두리", "ACCENT", "CARD", NONTEXT),
    ("아이콘", "TEXT", "BAR", NONTEXT),
    ("초점 테두리(바탕)", "FOCUS", "BG", NONTEXT),
    ("초점 테두리(상태줄·리본 줄)", "FOCUS", "BAR", NONTEXT),
    ("초점 테두리(패널)", "FOCUS", "PANEL", NONTEXT),
    ("초점 테두리(제목줄)", "FOCUS", "TITLEBAR", NONTEXT),
    ("초점 테두리(입력칸 안)", "FOCUS", "FIELD", NONTEXT),
    ("초점 테두리(고른 단추)", "FOCUS", "SELECT_BG", NONTEXT),
    ("초점 테두리(마우스 올림)", "FOCUS", "HOVER", NONTEXT),
    ("초점 테두리 vs 평소 테두리", "FOCUS", "FIELD_LINE", 1.5),   # 2.2 참고: 바뀐 것이 눈에 띄게
]


def _col(c: dict, k: str) -> str:
    return k if k.startswith("#") else c[k]


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_contrast_aa(dark):
    c = theme.colors(dark)
    bad = []
    for name, fg, bg, need in PAIRS:
        r = ratio(_col(c, fg), _col(c, bg))
        if r < need:
            bad.append(f"{name}: {_col(c, fg)} on {_col(c, bg)} = {r:.2f} < {need}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_disabled_dimmer_than_dim(dark):
    """꺼진 단추 글자는 누를 수 있는 흐린 글자보다 더 흐리다(바탕과의 대비가 더 작다)."""
    c = theme.colors(dark)
    for bg in ("BG", "BAR", "PANEL"):
        assert ratio(c["TEXT_OFF"], c[bg]) < ratio(c["TEXT_DIM"], c[bg]) - 1.0


def test_red_rules():
    """11-09 결정: 빨강 글자 자리는 진한 빨강(다크는 밝힌 빨강), 색 면은 #E8383D, 초점은 빨강이 아님."""
    lt, dk = theme.colors(False), theme.colors(True)
    assert lt["ACCENT_TEXT"] == "#C9272C" and lt["PRIMARY"] == "#C9272C" and dk["PRIMARY"] == "#C9272C"
    assert dk["ACCENT_TEXT"] == "#FF6B6F"
    assert theme.ACCENT == "#E8383D"
    for c in (lt, dk):
        h = c["FOCUS"].lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        assert b > r + 60 and b > g, c["FOCUS"]     # 파랑 계열
    css = theme.qss(False)
    assert "QLineEdit:focus" in css and "border-color: #E8383D; }}" not in css


# ── 초점 테두리 ────────────────────────────────────────────

def _focus_pixels(w, focus: str) -> int:
    from PySide6.QtGui import QColor
    want = QColor(focus)
    im = w.grab().toImage()
    n = 0
    for y in range(im.height()):
        for x in range(im.width()):
            p = im.pixelColor(x, y)
            if abs(p.red() - want.red()) + abs(p.green() - want.green()) + abs(p.blue() - want.blue()) < 60:
                n += 1
    return n


def _widgets():
    """(이름, 만드는 함수 → (초점 줄 위젯, 그려 볼 위젯))."""
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGraphicsView, QHBoxLayout,
                                   QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QRadioButton, QSlider,
                                   QSpinBox, QTabBar, QTabWidget, QTextEdit, QToolButton, QTreeWidget,
                                   QTreeWidgetItem, QVBoxLayout, QWidget)
    from PySide6.QtCore import Qt

    def tool(name, checked=False, text="단추"):
        def make():
            b = QToolButton(); b.setText(text)
            if name:
                b.setObjectName(name)
            b.setCheckable(True); b.setChecked(checked); b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return b, b
        return make

    def push(default=False):
        def make():
            b = QPushButton("확인"); b.setDefault(default); return b, b
        return make

    def card_toggle():
        f = QFrame(); f.setObjectName("group"); h = QHBoxLayout(f)
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        b = QToolButton(); b.setText("굵게"); b.setCheckable(True); b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        hl.addWidget(b); h.addWidget(row)
        return b, f

    def combo(editable=False):
        def make():
            c = QComboBox(); c.addItems(["가", "나"]); c.setEditable(editable); return c, c
        return make

    def doctabs():
        t = QTabBar(); t.setObjectName("doctabs"); t.addTab("문서 하나"); t.addTab("문서 둘"); t.setTabsClosable(True)
        t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return t, t

    def tabwidget():
        tw = QTabWidget(); tw.addTab(QWidget(), "일반"); tw.addTab(QWidget(), "글꼴")
        tw.tabBar().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return tw.tabBar(), tw

    def lst():
        w = QListWidget(); w.addItems(["하나", "둘"]); return w, w

    def tree():
        w = QTreeWidget(); w.setHeaderHidden(True); QTreeWidgetItem(w, ["하나"]); return w, w

    def slider():
        s = QSlider(Qt.Orientation.Horizontal); s.setFixedWidth(120); s.setValue(50)
        s.setFocusPolicy(Qt.FocusPolicy.StrongFocus); return s, s

    def check(cls):
        def make():
            b = cls("고르기"); b.setFocusPolicy(Qt.FocusPolicy.StrongFocus); return b, b
        return make

    def simple(cls):
        def make():
            w = cls(); return w, w
        return make

    def view():
        v = QGraphicsView(); v.setFixedSize(120, 80); return v, v

    return [
        ("QPushButton", push()), ("기본 단추", push(True)),
        ("리본 단추", tool("rb")), ("리본 단추 켜짐", tool("rb", True)), ("빠른 실행", tool("quick")),
        ("세로 막대", tool("nav")), ("세로 막대 켜짐", tool("nav", True)),
        ("리본 탭", tool("ribbontab")), ("리본 탭 고름", tool("ribbontab", True)), ("파일 탭", tool("filetab")),
        ("인쇄 탭", tool("printtab", True)), ("방향 단추", tool("orient")), ("패널 제목 단추", tool("paneltitlebtn")),
        ("서식 단추(카드)", card_toggle),
        ("QLineEdit", simple(QLineEdit)), ("QComboBox", combo()), ("편집 콤보", combo(True)),
        ("QSpinBox", simple(QSpinBox)), ("QDoubleSpinBox", simple(QDoubleSpinBox)),
        ("QTextEdit", simple(QTextEdit)), ("QPlainTextEdit", simple(QPlainTextEdit)),
        ("QCheckBox", check(QCheckBox)), ("QRadioButton", check(QRadioButton)), ("QSlider", slider),
        ("문서 탭", doctabs), ("대화상자 탭", tabwidget),
        ("QListWidget", lst), ("QTreeWidget", tree), ("문서 보기", view),
    ]


@pytest.fixture
def themed_app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    old_css, old_pal = app.styleSheet(), app.palette()
    yield app
    app.setStyleSheet(old_css); app.setPalette(old_pal)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_focus_ring_every_kind(themed_app, dark):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    app = themed_app
    app.setStyleSheet(theme.qss(dark))
    focus = theme.colors(dark)["FOCUS"]
    missing = []
    for name, make in _widgets():
        host = QWidget(); lay = QVBoxLayout(host)
        sink = QWidget(); sink.setFocusPolicy(Qt.FocusPolicy.StrongFocus); sink.setFixedSize(4, 4); lay.addWidget(sink)
        target, shown = make(); lay.addWidget(shown)
        host.show(); app.processEvents()
        sink.setFocus(Qt.FocusReason.TabFocusReason); app.processEvents()
        before = _focus_pixels(shown, focus)
        size_before = shown.size()
        target.setFocus(Qt.FocusReason.TabFocusReason); app.processEvents()
        after = _focus_pixels(shown, focus)
        if not (target.hasFocus() or (target.focusProxy() and target.focusProxy().hasFocus())) \
                and not shown.isAncestorOf(app.focusWidget() or host):
            missing.append(f"{name}: 초점이 안 감")
        elif after - before < 20:
            missing.append(f"{name}: 파랑 {before} → {after}")
        if shown.size() != size_before:
            missing.append(f"{name}: 크기 바뀜 {size_before} → {shown.size()}")
        host.close(); host.deleteLater()
    app.processEvents()
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_format_toggle_readable_dark(themed_app, dark):
    """속성 패널 서식 단추(이름 없는 켜고 끄는 단추) 글자가 바탕과 구분된다(11-09 다크에서 흰 바탕 흰 글자)."""
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton
    app = themed_app
    app.setStyleSheet(theme.qss(dark))
    c = theme.colors(dark)
    f = QFrame(); f.setObjectName("group"); h = QHBoxLayout(f)
    b = QToolButton(); b.setText("■■■■"); b.setCheckable(True); h.addWidget(b)
    f.show(); app.processEvents()
    im = b.grab().toImage()
    bg = im.pixelColor(3, im.height() // 2).name().upper()
    assert ratio(bg, c["FIELD"]) < 1.2, bg          # 바탕이 테마 입력칸 색
    assert ratio(c["TEXT"], bg) >= TEXT
    f.close(); f.deleteLater(); app.processEvents()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_fmt_button_rule(themed_app, dark):
    """서식 단추 objectName 「fmt」 규칙: 카드 밖에 있어도 테마 바탕·글자색, 켜지면 빨강 글자, 초점은 파랑."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget
    app = themed_app
    css = theme.qss(dark)
    assert "QToolButton#fmt," in css and "QToolButton#fmt:checked," in css and "QToolButton#fmt:focus," in css
    app.setStyleSheet(css)
    c = theme.colors(dark)
    host = QWidget(); h = QHBoxLayout(host)
    sink = QWidget(); sink.setFocusPolicy(Qt.FocusPolicy.StrongFocus); h.addWidget(sink)
    b = QToolButton(); b.setObjectName("fmt"); b.setText("굵게"); b.setCheckable(True)
    b.setFocusPolicy(Qt.FocusPolicy.StrongFocus); h.addWidget(b)
    host.show(); app.processEvents()
    sink.setFocus(); app.processEvents()
    im = b.grab().toImage()
    bg = im.pixelColor(3, im.height() // 2).name().upper()
    assert ratio(bg, c["FIELD"]) < 1.2, bg
    before = _focus_pixels(b, c["FOCUS"])
    b.setFocus(Qt.FocusReason.TabFocusReason); app.processEvents()
    assert _focus_pixels(b, c["FOCUS"]) - before >= 20
    b.setChecked(True); sink.setFocus(); app.processEvents()
    im = b.grab().toImage()
    assert ratio(im.pixelColor(3, im.height() // 2).name().upper(), c["SELECT_BG"]) < 1.2
    host.close(); host.deleteLater(); app.processEvents()
