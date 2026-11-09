"""왼쪽·오른쪽 막대 틀: 내용 칸 폭 끌기(QSplitter) · 막대 통째 접기(16px 띠) · 접힘과 폭 기억.

폭은 settings 최상위 left_w · right_w(논리 px), 접힘은 left_collapsed · right_collapsed. 모든 탭·창이 같은 값을 쓴다.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QPushButton, QSplitter, QToolButton, QVBoxLayout, QWidget

from . import icons

LEFT_W, RIGHT_W = 252, 260   # 처음 폭
MIN_W = 120                  # 내용 칸 최소 폭
STRIP_W = 16                 # 접었을 때 남는 띠 폭
MAX_W = 16777215             # QWIDGETSIZE_MAX


class _ShowFit(QObject):
    """탭이 보일 때마다 기억한 폭으로 다시 나눈다(다른 탭에서 바꾼 폭을 따라오게)."""
    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Show:
            fit(obj.property("doctab"))
        return False


_show_fit = _ShowFit()


def build(tab) -> QSplitter:
    """탭의 왼쪽 패널 묶음 · 보기 · 속성 패널을 끌어 폭을 바꾸는 한 줄로 묶는다."""
    sp = QSplitter(Qt.Orientation.Horizontal)
    sp.setObjectName("docsplit"); sp.setChildrenCollapsible(False); sp.setHandleWidth(4)
    for w in (tab.side, tab.props):
        w.setMinimumWidth(MIN_W); w.setMaximumWidth(MAX_W)
    sp.addWidget(tab.side); sp.addWidget(tab.view_stack); sp.addWidget(tab.props)
    sp.setStretchFactor(0, 0); sp.setStretchFactor(1, 1); sp.setStretchFactor(2, 0)
    sp.splitterMoved.connect(lambda *_: remember(tab))
    sp.setProperty("doctab", tab)
    sp.installEventFilter(_show_fit)
    # 오른쪽 속성 패널의 닫기는 오른쪽을 가리킨다
    for b in tab.props.findChildren(QPushButton, "panelclose"):
        b.setIcon(icons.icon("next", 14))
    tab.split = sp
    return sp


def _settings(tab) -> dict:
    return tab.win.settings


def widths(settings: dict) -> tuple[int, int]:
    def ok(v, d):
        return int(v) if isinstance(v, (int, float)) and v >= MIN_W else d
    return ok(settings.get("left_w"), LEFT_W), ok(settings.get("right_w"), RIGHT_W)


def fit(tab):
    """보이는 옆 칸을 기억한 폭으로 맞춘다. 패널을 열고 닫을 때·탭이 보일 때 부른다."""
    sp = getattr(tab, "split", None)
    if sp is None:
        return
    lw, rw = widths(_settings(tab))
    total = max(sp.width(), lw + rw + 200)
    left = lw if not tab.side.isHidden() else 0
    right = rw if not tab.props.isHidden() else 0
    sp.setSizes([left, max(total - left - right, 1), right])


def remember(tab):
    """사람이 끈 폭을 기억한다(파일에는 창을 닫을 때 같이 쓴다)."""
    s = _settings(tab)
    sizes = tab.split.sizes()
    if not tab.side.isHidden() and sizes[0] >= MIN_W:
        s["left_w"] = sizes[0]
    if not tab.props.isHidden() and sizes[2] >= MIN_W:
        s["right_w"] = sizes[2]


def _strip(name: str, icon: str, tip: str, fn) -> QWidget:
    """접었을 때 남는 16px 띠. 아래쪽 16x32 단추로 다시 편다."""
    w = QWidget(); w.setObjectName(name); w.setFixedWidth(STRIP_W)
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 6); lay.setSpacing(0); lay.addStretch(1)
    b = QToolButton(); b.setObjectName("stripbtn"); b.setFixedSize(STRIP_W, 32); b.setIcon(icons.icon(icon, 12)); b.setToolTip(tip); b.setAccessibleName(tip)
    b.clicked.connect(fn); lay.addWidget(b)
    w.button = b
    w.hide()
    return w


class Bars:
    """창 하나의 양쪽 막대 접기. navstrip·rightstrip 은 창이 만든 세로 단추 줄."""

    def __init__(self, win, navstrip: QWidget, rightstrip: QWidget, save):
        self.win = win
        self.navstrip, self.rightstrip = navstrip, rightstrip
        self._save = save
        self._read = False   # 읽기 모드로 막대를 숨겼다
        self.left_strip = _strip("leftfold", "right", "왼쪽 막대 펴기", lambda: self.set_left(False))
        self.right_strip = _strip("rightfold", "left", "오른쪽 막대 펴기", lambda: self.set_right(False))
        self.left_btn = self._fold_btn("left", "왼쪽 막대 접기", lambda: self.set_left(True))
        self.right_btn = self._fold_btn("right", "오른쪽 막대 접기", lambda: self.set_right(True))
        self._open_left: set = set()    # 접을 때 패널이 열려 있던 탭(펴면 다시 연다)
        self._open_right: set = set()

    @staticmethod
    def _fold_btn(icon: str, tip: str, fn) -> QToolButton:
        b = QToolButton(); b.setObjectName("nav"); b.setIcon(icons.icon(icon, 14)); b.setToolTip(tip); b.setAccessibleName(tip)
        b.clicked.connect(fn)
        return b

    @property
    def left_collapsed(self) -> bool:
        return bool(self.win.settings.get("left_collapsed"))

    @property
    def right_collapsed(self) -> bool:
        return bool(self.win.settings.get("right_collapsed"))

    def set_left(self, on: bool):
        if on == self.left_collapsed and self.left_strip.isVisible() == on:
            return
        self.win.settings["left_collapsed"] = on
        tabs = self.win.tabs
        if on:
            self._open_left = {t for t in tabs if not t.side.isHidden()}
            for t in tabs:
                t.side.hide()
        else:
            for t in tabs:
                if t in self._open_left and t.side_key:
                    t.side.show(); fit(t)
            self._open_left = set()
        self.sync()
        self._save()

    def set_right(self, on: bool):
        if on == self.right_collapsed and self.right_strip.isVisible() == on:
            return
        self.win.settings["right_collapsed"] = on
        tabs = self.win.tabs
        if on:
            self._open_right = {t for t in tabs if not t.props.isHidden()}
            for t in tabs:
                t.props.hide()
        else:
            for t in tabs:
                if t in self._open_right:
                    t.props.show(); fit(t)
            self._open_right = set()
        self.sync()
        self._save()

    def set_read_mode(self, on: bool):
        """읽기 모드: 양쪽 막대(접힌 띠까지) 숨김. 끝나면 접힘 값대로."""
        self._read = on
        self.sync()

    def sync(self):
        """막대·띠 보이기를 접힘 값과 읽기 모드에 맞춘다."""
        lc, rc = self.left_collapsed, self.right_collapsed
        self.navstrip.setVisible(not self._read and not lc)
        self.left_strip.setVisible(not self._read and lc)
        self.rightstrip.setVisible(not self._read and not rc)
        self.right_strip.setVisible(not self._read and rc)

    def apply_tab(self, t):
        """새로 연 탭·옮겨 온 탭: 접힌 쪽 패널은 숨긴다."""
        if self.left_collapsed:
            t.side.hide()
        if self.right_collapsed:
            t.props.hide()
