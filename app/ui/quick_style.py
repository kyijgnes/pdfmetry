"""빠른 스타일: 켜진 주석 도구 단추를 다시 누르면 단추 아래에 뜨는 작은 창.

견본 · 색 여섯과 사용자 색 · 불투명도 · 굵기(지우개는 반지름 하나). 바꾸면 바로 다음에 그릴 모양에 얹고,
창이 닫힐 때 그 종류의 기본값(annot_styles.json, 지우개는 settings.json)으로 기억한다.
리본 「색」 단추는 같은 창을 색 칸만으로 띄운다.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout,
                               QWidget)

from ..engine import annot_shapes as SH
from ..engine import annot_style as S
from . import theme
from .dialogs import from_rgb01, to_rgb01
from .props import SWATCHES

ERASER_RANGE = (4, 30)
ERASER_DEFAULT = 10
_TEXT_KEYS = ("textbox", "callout", "typewriter")


def style_key(tool: str) -> str | None:
    """도구가 기본값을 기억하는 칸. 모양 더보기 넷은 다각형 칸을 같이 쓴다."""
    if tool in SH.SHAPES:
        return "polygon"
    return tool if tool in S.DEFAULTS else None


def has_quick_style(tool: str) -> bool:
    return tool == "eraser" or style_key(tool) is not None


def eraser_radius(settings: dict) -> int:
    try:
        r = int(settings.get("eraser_radius", ERASER_DEFAULT))
    except (TypeError, ValueError):
        r = ERASER_DEFAULT
    return max(ERASER_RANGE[0], min(ERASER_RANGE[1], r))


def _main_key(style: dict) -> str | None:
    return next((k for k in ("color", "stroke", "text_color") if k in style), None)


class Sample(QWidget):
    """지금 모양으로 그린 견본 한 줄."""

    def __init__(self, tool: str, style: dict, parent=None):
        super().__init__(parent)
        self.tool, self.style = tool, style
        self.setFixedHeight(34)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        p.fillRect(r, QColor("white"))
        s, t = self.style, self.tool
        mk = _main_key(s)
        col = from_rgb01(s.get(mk) if mk else (1, 0, 0))
        col.setAlphaF(max(0.0, min(1.0, float(s.get("opacity", 1.0)))))
        width = max(0.5, float(s.get("width", 1.0) or 0))
        mid = r.center().y()
        if t in ("highlight", "areahl"):
            p.fillRect(QRectF(r.left() + 10, r.top() + 3, r.width() - 20, r.height() - 6), col)
            p.setPen(QColor("black")); p.drawText(r, Qt.AlignmentFlag.AlignCenter, "가나다 Abc")
        elif t in ("underline", "strike", "squiggly", "caret"):
            p.setPen(QColor("black")); p.drawText(r, Qt.AlignmentFlag.AlignCenter, "가나다 Abc")
            p.setPen(QPen(col, 1.5))
            y = mid if t == "strike" else r.bottom() - 4
            if t == "squiggly":
                path = QPainterPath(QPointF(r.left() + 20, y))
                for k, x in enumerate(range(int(r.left()) + 23, int(r.right()) - 20, 3)):
                    path.lineTo(QPointF(x, y - 2 if k % 2 == 0 else y))
                p.drawPath(path)
            else:
                p.drawLine(QPointF(r.left() + 20, y), QPointF(r.right() - 20, y))
        elif t in _TEXT_KEYS:
            p.setPen(col); p.drawText(r, Qt.AlignmentFlag.AlignCenter, "가나다 Abc")
        else:
            p.setPen(QPen(col, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            if s.get("fill"):
                f = from_rgb01(s["fill"]); f.setAlphaF(col.alphaF()); p.setBrush(f)
            if t == "pencil":
                path = QPainterPath(QPointF(r.left() + 12, mid + 4))
                path.cubicTo(QPointF(r.left() + 50, mid - 14), QPointF(r.right() - 60, mid + 16), QPointF(r.right() - 12, mid - 4))
                p.drawPath(path)
            elif t in ("line", "arrow", "polyline", "m_distance"):
                p.drawLine(QPointF(r.left() + 12, mid), QPointF(r.right() - 12, mid))
            elif t == "oval":
                p.drawEllipse(QRectF(r.center().x() - 30, r.top() + 2, 60, r.height() - 4))
            else:
                p.drawRect(QRectF(r.center().x() - 30, r.top() + 2, 60, r.height() - 4))
        p.end()


class QuickStyle(QFrame):
    """도구 하나의 빠른 스타일 창. changed(도구) 는 닫히며 기억한 뒤 보낸다."""
    changed = Signal(str)

    def __init__(self, tool: str, viewer, settings: dict, save: Callable[[], None], only_color: bool = False,
                 store: S.StyleStore | None = None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("quickstyle")
        self.setStyleSheet(f"QFrame#quickstyle{{background:{theme.CUR['CARD']}; border:1px solid {theme.CUR['CARD_LINE']};}}")
        self.tool, self.viewer, self.settings, self.save = tool, viewer, settings, save
        self.store = store or S.store()
        self.key = style_key(tool)
        self.style = self.store.get(self.key) if self.key else {}
        self._dirty = False
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 8, 10, 10); lay.setSpacing(6)
        from .ribbon import LABELS
        title = QLabel(LABELS.get(tool, tool)); title.setObjectName("dim"); lay.addWidget(title)
        if tool == "eraser":
            self._eraser(lay)
            return
        self.sample = Sample(tool, self.style); lay.addWidget(self.sample)
        self._colors(lay)
        if only_color:
            return
        grid = QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(4); lay.addLayout(grid)
        if "opacity" in self.style:
            self.opacity = self._slider(grid, "불투명도", 0, 100, round(float(self.style["opacity"]) * 100), "%",
                                        lambda v: self._set("opacity", v / 100))
        if "width" in self.style and self.key not in _TEXT_KEYS:
            self.width = self._slider(grid, "굵기", 1, 120, round(float(self.style["width"] or 0) * 10), "pt",
                                      lambda v: self._set("width", v / 10), scale=10)

    # ── 칸 ───────────────────────────────────────────────────
    def _colors(self, lay):
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)
        for c in SWATCHES:
            b = QToolButton(); b.setFixedSize(22, 22)
            b.setStyleSheet(f"QToolButton{{border:1px solid rgba(0,0,0,40); border-radius:3px; background:{from_rgb01(c).name()};}}")
            b.clicked.connect(lambda _=False, c=c: self._pick(c)); hl.addWidget(b)
        more = QToolButton(); more.setText("사용자 색"); more.clicked.connect(self._custom); hl.addWidget(more)
        lay.addWidget(row)

    def _slider(self, grid: QGridLayout, name: str, lo: int, hi: int, val: int, unit: str, fn, scale: int = 1) -> QSlider:
        r = grid.rowCount()
        s = QSlider(Qt.Orientation.Horizontal); s.setRange(lo, hi); s.setValue(max(lo, min(hi, val))); s.setMinimumWidth(150)
        lab = QLabel(); lab.setMinimumWidth(44)
        show = (lambda v: lab.setText(f"{v / scale:g}{unit}"))
        show(s.value())
        s.valueChanged.connect(lambda v: (show(v), fn(v)))
        grid.addWidget(QLabel(name), r, 0); grid.addWidget(s, r, 1); grid.addWidget(lab, r, 2)
        return s

    def _eraser(self, lay):
        grid = QGridLayout(); lay.addLayout(grid)
        self.radius = self._slider(grid, "반지름", *ERASER_RANGE, eraser_radius(self.settings), "px", self._set_radius)

    # ── 바꾸기 ───────────────────────────────────────────────
    def _set(self, k: str, v):
        self.style[k] = v
        self._dirty = True
        S.to_viewer_props(self.key, self.style, self.viewer.props)
        self.sample.update()

    def _pick(self, c):
        mk = _main_key(self.style)
        if mk is None:
            return
        self._set(mk, tuple(c))

    def _custom(self):
        mk = _main_key(self.style)
        cur = self.style.get(mk) if mk else None
        self.hide()   # 색 고르기 창이 뜨면 팝업이 닫히므로 먼저 기억해 둔다
        c = QColorDialog.getColor(from_rgb01(cur) if cur else QColor("red"), self.parentWidget(), "사용자 색")
        if c.isValid() and mk:
            self._set(mk, to_rgb01(c))
            self._commit()

    def _set_radius(self, v: int):
        self.settings["eraser_radius"] = int(v)
        self._dirty = True
        if hasattr(self.viewer, "set_eraser_radius"):
            self.viewer.set_eraser_radius(int(v))

    def _commit(self):
        if not self._dirty:
            return
        self._dirty = False
        if self.tool == "eraser":
            self.save()
        elif self.key:
            self.store.remember(self.key, self.style)
        self.changed.emit(self.tool)

    def hideEvent(self, e):
        self._commit()
        super().hideEvent(e)

    def popup_under(self, w: QWidget):
        self.adjustSize()
        self.move(w.mapToGlobal(w.rect().bottomLeft()))
        self.show()
