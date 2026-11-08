"""글자가 잘리지 않는 칸 폭: 고정 숫자 대신 지금 글꼴로 잰 글자 폭 + 그 칸의 테두리·여백.

입력칸(QLineEdit)·편집 칸이 있는 목록(QComboBox)·숫자칸(QSpinBox)에 쓴다. 글꼴·스타일시트·DPI 배율이
바뀌어도 fontMetrics 와 style 이 같이 바뀌므로 다시 부르기만 하면 맞는다.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QLineEdit, QStyle, QStyleOptionComboBox, QStyleOptionFrame,
                               QStyleOptionSpinBox, QWidget)

PROBE = 1000      # 테두리·여백을 재려고 대 보는 넉넉한 폭
LINE_MARGIN = 2   # QLineEdit 이 글자 양옆에 두는 안쪽 여백(Qt 고정값)
CURSOR = 1        # 맨 끝 글자 뒤 커서 자리


def _line_chrome(le: QLineEdit) -> int:
    """입력칸 폭 가운데 글자가 못 쓰는 몫(테두리·여백·안쪽 여백)."""
    le.ensurePolished()
    opt = QStyleOptionFrame(); opt.initFrom(le)
    opt.rect = QRect(0, 0, PROBE, max(le.height(), 24))
    opt.lineWidth = le.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, opt, le) if le.hasFrame() else 0
    opt.midLineWidth = 0
    opt.state |= QStyle.StateFlag.State_Sunken
    r = le.style().subElementRect(QStyle.SubElement.SE_LineEditContents, opt, le)
    m = le.textMargins()
    return PROBE - r.width() + m.left() + m.right() + 2 * LINE_MARGIN + CURSOR


def _field_chrome(w: QWidget) -> int:
    """목록·숫자칸 바깥 몫(테두리·여백·펼침 단추·위아래 단추)과 안쪽 입력칸 몫을 더한다."""
    w.ensurePolished()
    h = max(w.height(), 24)
    if isinstance(w, QComboBox):
        opt = QStyleOptionComboBox(); opt.initFrom(w)
        opt.editable = w.isEditable(); opt.frame = w.hasFrame(); opt.rect = QRect(0, 0, PROBE, h)
        opt.subControls = QStyle.SubControl.SC_All
        r = w.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, w)
        inner = _line_chrome(w.lineEdit()) if w.lineEdit() is not None else 2 * LINE_MARGIN
    else:
        opt = QStyleOptionSpinBox(); opt.initFrom(w)
        opt.frame = w.hasFrame(); opt.rect = QRect(0, 0, PROBE, h)
        opt.subControls = QStyle.SubControl.SC_All
        opt.buttonSymbols = w.buttonSymbols()
        r = w.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxEditField, w)
        inner = _line_chrome(w.lineEdit())
    return PROBE - r.width() + inner


def need(w: QWidget, texts) -> int:
    """texts 가운데 가장 긴 글자가 다 보이는 칸 폭."""
    w.ensurePolished()
    fm = w.fontMetrics()
    adv = max((fm.horizontalAdvance(t) for t in texts if t), default=0)
    if isinstance(w, QLineEdit):
        return adv + _line_chrome(w)
    if isinstance(w, (QComboBox, QAbstractSpinBox)):
        return adv + _field_chrome(w)
    return adv


class _Refit(QObject):
    """스타일시트·글꼴이 바뀌면(테마 입히기·밝게/어둡게) 같은 글자로 폭을 다시 잰다."""
    EVENTS = (QEvent.Type.StyleChange, QEvent.Type.FontChange)

    def __init__(self, w: QWidget, measure):
        super().__init__(w)
        self.measure = measure
        self.texts: list[str] = []
        self.floor = 0
        w.installEventFilter(self)

    def apply(self, w: QWidget) -> int:
        px = max(self.floor, self.measure(w, self.texts))
        if w.minimumWidth() != px or w.maximumWidth() != px:
            w.setFixedWidth(px)
        return px

    def eventFilter(self, obj, e):
        if e.type() in self.EVENTS:
            self.apply(obj)
        return False


def _keep(w: QWidget, texts, floor: int, measure) -> int:
    r = next((c for c in w.children() if isinstance(c, _Refit)), None)
    if r is None:
        r = _Refit(w, measure)
    r.texts = [t for t in texts if t]; r.floor = floor
    return r.apply(w)


def fit(w: QWidget, texts, floor: int = 0) -> int:
    """칸 폭을 글자 폭에 맞춰 고정한다(floor 보다 좁게는 안 줄인다). 스타일이 바뀌면 다시 잰다. 정한 폭을 돌려준다."""
    return _keep(w, texts, floor, need)


def combo_texts(c: QComboBox) -> list[str]:
    """목록 항목 글자 전부와 입력칸 안내 글자."""
    out = [c.itemText(i) for i in range(c.count())]
    if c.lineEdit() is not None:
        out.append(c.lineEdit().placeholderText())
    return out


def spin_texts(s: QAbstractSpinBox) -> list[str]:
    """숫자칸에 들어갈 수 있는 가장 긴 글자(맨 아래·맨 위 값에 앞뒤 글자를 붙인 것)."""
    out = []
    for v in (s.minimum(), s.maximum()):
        out.append(f"{s.prefix()}{s.textFromValue(v)}{s.suffix()}")
    if s.specialValueText():
        out.append(s.specialValueText())
    return out


def list_need(lst, lines) -> int:
    """목록(QListWidget) 항목 글줄이 다 보이는 폭: 글자 + 항목 여백 + 테두리 + 세로 막대 자리."""
    lst.ensurePolished()
    st = lst.style()
    adv = max((lst.fontMetrics().horizontalAdvance(t) for t in lines if t), default=0)
    item_pad = 2 * 2                                                      # QListWidget::item 좌우 여백
    text_margin = 2 * (st.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, lst) + 1)
    bar = st.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, lst.verticalScrollBar()) + 4
    return adv + item_pad + text_margin + 2 * lst.frameWidth() + bar


def fit_list(lst, lines, floor: int = 0) -> int:
    """목록 폭을 가장 긴 항목 글줄에 맞춰 고정한다. 스타일이 바뀌면 다시 잰다."""
    return _keep(lst, lines, floor, list_need)
