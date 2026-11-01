# -*- coding: utf-8 -*-
"""텍스트 추가: 쪽을 누른 자리에서 바로 치는 글 상자와 그 위에 뜨는 글자 막대.

글 상자는 쪽 그림의 자식 항목이라 넘기기·확대에 같이 움직인다. 입력이 끝나면(밖을 누름·Esc·창 밖으로
포커스가 나감) 글과 서식을 on_done 으로 넘기고 사라진다. 쪽에 굽는 일은 부른 쪽(창)이 한다.
"""
from __future__ import annotations

from typing import Callable

import fitz
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextOption
from PySide6.QtWidgets import (QColorDialog, QFrame, QGraphicsItem, QGraphicsTextItem, QGraphicsView, QHBoxLayout,
                               QLabel, QStyleOptionGraphicsItem, QToolButton, QWidget)

from ..engine import annots as A

FAMILY = "Malgun Gothic"
BASE_PX = 100          # 글꼴은 이 크기로 그리고 항목 배율로 실제 pt 크기를 맞춘다(소수 pt 도 그대로)
MIN_WIDTH = 100.0      # 처음 글 상자 너비(pt)
LINE = 1.33            # 맑은 고딕 한 줄 높이(글자 크기 배)
SIZES = (6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72)
DEFAULT_STYLE = {"fontsize": 10.0, "color": (0, 0, 0), "bold": False, "italic": False, "align": 0}


def clean_style(s: dict | None) -> dict:
    """settings.json 에서 읽은 서식을 믿을 수 있는 값으로."""
    out = dict(DEFAULT_STYLE)
    for k, v in (s or {}).items():
        if k == "fontsize" and isinstance(v, (int, float)) and 1 <= v <= 400:
            out[k] = float(v)
        elif k == "color" and isinstance(v, (list, tuple)) and len(v) == 3:
            out[k] = tuple(min(1.0, max(0.0, float(c))) for c in v)
        elif k in ("bold", "italic"):
            out[k] = bool(v)
        elif k == "align" and v in (0, 1, 2):
            out[k] = v
    return out


class _Box(QGraphicsTextItem):
    """점선 테두리를 두른 입력 항목."""

    def paint(self, p: QPainter, opt: QStyleOptionGraphicsItem, w: QWidget | None = None):
        super().paint(p, opt, w)
        pen = QPen(QColor(0, 120, 215), 0, Qt.PenStyle.DashLine)   # 0 = 확대와 상관없이 한 화소
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.boundingRect())


class _Bar(QFrame):
    """글 상자 위에 뜨는 글자 막대. 단추는 포커스를 가져가지 않아 누르는 동안에도 입력이 이어진다."""

    def __init__(self, parent: QWidget, ed: "InlineText"):
        super().__init__(parent)
        self.ed = ed
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)

        def btn(text: str, fn, check: bool = False) -> QToolButton:
            b = QToolButton(self)
            b.setText(text)
            b.setCheckable(check)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            lay.addWidget(b)
            return b

        self.smaller = btn("가-", lambda: ed.step_size(-1))
        self.size = QLabel(self)
        lay.addWidget(self.size)
        self.bigger = btn("가+", lambda: ed.step_size(1))
        self.bold = btn("굵게", lambda on: ed.set_style(bold=on), True)
        f = self.bold.font(); f.setBold(True); self.bold.setFont(f)
        self.italic = btn("기울임", lambda on: ed.set_style(italic=on), True)
        f = self.italic.font(); f.setItalic(True); self.italic.setFont(f)
        self.color = btn("색", ed.pick_color)
        self.aligns = [btn(t, lambda _=False, a=a: ed.set_style(align=a), True)
                       for a, t in enumerate(("왼쪽", "가운데", "오른쪽"))]
        self.sync()

    def sync(self):
        s = self.ed.style
        self.size.setText(f"{s['fontsize']:g}pt")
        self.bold.setChecked(s["bold"])
        self.italic.setChecked(s["italic"])
        c = QColor.fromRgbF(*s["color"])
        self.color.setStyleSheet(f"QToolButton {{ color: {c.name()}; font-weight: bold; }}")
        for a, b in enumerate(self.aligns):
            b.setChecked(s["align"] == a)


class InlineText(QObject):
    """쪽 위에서 바로 치는 글 상자 하나. finish() 뒤에는 다시 쓰지 않는다.

    scene_pt: 누른 자리(장면 좌표). drag_width: 끌어 그렸으면 그 너비(pt), 이때는 누른 자리가 상자 왼쪽 위.
    on_done(text, page_rect, style): 끝날 때 한 번. page_rect 는 회전 전 쪽 좌표.
    """

    def __init__(self, viewer, page: int, scene_pt: QPointF, style: dict | None,
                 on_done: Callable[[str, fitz.Rect, dict], None], drag_width: float | None = None):
        super().__init__(viewer)
        self.v = viewer
        self.page = page
        self.style = clean_style(style)
        self.on_done = on_done
        self.done = False
        self._busy = False   # 색 고르기 창이 떠 있는 동안은 포커스가 나가도 끝내지 않는다
        it = viewer.items[page]
        z = viewer.zoom
        fs = self.style["fontsize"]
        local = scene_pt - it.pos()
        # 화면에 보이는 쪽(회전 뒤) 기준 pt. 클릭이면 그 자리가 첫 줄 가운데에 오게 줄 높이 절반을 올린다
        self.pos_pt = QPointF(local.x() / z, local.y() / z - (0 if drag_width else fs * LINE / 2))
        self.pos_pt.setY(max(0.0, self.pos_pt.y()))
        page_w = it.w / z
        self.max_width = max(10.0, page_w - self.pos_pt.x())
        self.min_width = min(self.max_width, max(drag_width or MIN_WIDTH, 10.0))

        self.box = _Box(it)
        self.box.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.box.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.box.document().setDocumentMargin(0)
        self.box.setZValue(10)
        self.box.document().contentsChanged.connect(self._fit)
        self.bar = _Bar(viewer.viewport(), self)
        self._apply()

        viewer.viewport().installEventFilter(self)
        viewer.installEventFilter(self)
        viewer.zoomChanged.connect(self._place)
        viewer.horizontalScrollBar().valueChanged.connect(self._place_bar)
        viewer.verticalScrollBar().valueChanged.connect(self._place_bar)
        self.bar.show()
        viewer.setFocus(Qt.FocusReason.OtherFocusReason)
        viewer.scene_.setFocusItem(self.box, Qt.FocusReason.MouseFocusReason)

    # ── 모양 ─────────────────────────────────────────────────
    def _unit(self) -> float:
        return self.style["fontsize"] / BASE_PX   # 항목 한 칸이 몇 pt 인가

    def _apply(self):
        s = self.style
        f = QFont(FAMILY)
        f.setPixelSize(BASE_PX)
        f.setBold(s["bold"])
        f.setItalic(s["italic"])
        self.box.setFont(f)
        self.box.setDefaultTextColor(QColor.fromRgbF(*s["color"]))
        opt = self.box.document().defaultTextOption()
        opt.setAlignment((Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignRight)[s["align"]])
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.box.document().setDefaultTextOption(opt)
        self.bar.sync()
        self._place()

    def _fit(self):
        """너비는 처음 100pt 에서 가장 긴 줄만큼 늘고, 쪽 오른쪽 끝에서 줄을 바꾼다."""
        u = self._unit()
        doc = self.box.document()
        doc.setTextWidth(-1)
        want = doc.idealWidth() * u + A.TEXT_PAD
        w = min(self.max_width, max(self.min_width, want))
        if abs(self.box.textWidth() - w / u) > 1e-3:
            self.box.setTextWidth(w / u)
        else:
            doc.setTextWidth(w / u)
        self._place_bar()

    def _place(self, *_):
        z = self.v.zoom
        self.box.setScale(z * self._unit())
        self.box.setPos(self.pos_pt.x() * z, self.pos_pt.y() * z)
        self._fit()

    def _scene_rect(self) -> QRectF:
        return self.box.mapRectToScene(self.box.boundingRect())

    def _place_bar(self, *_):
        r = self.v.mapFromScene(self._scene_rect()).boundingRect()
        self.bar.adjustSize()
        y = r.top() - self.bar.height() - 4
        if y < 0:
            y = r.bottom() + 4
        x = max(0, min(r.left(), self.v.viewport().width() - self.bar.width()))
        self.bar.move(x, y)

    # ── 막대 단추 ────────────────────────────────────────────
    def set_style(self, **kw):
        self.style.update(kw)
        self._apply()

    def step_size(self, d: int):
        fs = self.style["fontsize"]
        if d > 0:
            nxt = next((s for s in SIZES if s > fs + 1e-6), SIZES[-1])
        else:
            nxt = next((s for s in reversed(SIZES) if s < fs - 1e-6), SIZES[0])
        self.set_style(fontsize=float(nxt))

    def pick_color(self):
        self._busy = True
        try:
            c = QColorDialog.getColor(QColor.fromRgbF(*self.style["color"]), self.v.window(), "글자색")
        finally:
            self._busy = False
        if not self.done:
            self.v.setFocus(Qt.FocusReason.OtherFocusReason)
            self.v.scene_.setFocusItem(self.box, Qt.FocusReason.OtherFocusReason)
            if c.isValid():
                self.set_style(color=(c.redF(), c.greenF(), c.blueF()))

    # ── 입력 넘기기 ──────────────────────────────────────────
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if self.done:
            return False
        et = e.type()
        if obj is self.v.viewport():
            if et == QEvent.Type.MouseButtonPress:
                if self._scene_rect().adjusted(-2, -2, 2, 2).contains(self.v.mapToScene(e.position().toPoint())):
                    QGraphicsView.mousePressEvent(self.v, e)   # 상자 안: 보기의 도구 처리는 건너뛰고 글 상자로
                    return True
                self.finish()
                return e.button() == Qt.MouseButton.LeftButton   # 왼쪽 누름은 끝내기만(가운데 끌기는 그대로)
            if et in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
                if self.v.scene_.mouseGrabberItem() is self.box or (
                        et == QEvent.Type.MouseButtonDblClick
                        and self._scene_rect().contains(self.v.mapToScene(e.position().toPoint()))):
                    {QEvent.Type.MouseMove: QGraphicsView.mouseMoveEvent,
                     QEvent.Type.MouseButtonRelease: QGraphicsView.mouseReleaseEvent,
                     QEvent.Type.MouseButtonDblClick: QGraphicsView.mouseDoubleClickEvent}[et](self.v, e)
                    return True
            return False
        if obj is self.v:
            if et == QEvent.Type.ShortcutOverride:
                e.accept()   # 창의 단축키(Delete·Ctrl+Z 등)보다 글 상자 입력이 먼저
                return True
            if et == QEvent.Type.KeyPress:
                if e.key() == Qt.Key.Key_Escape:
                    self.finish()
                    return True
                QGraphicsView.keyPressEvent(self.v, e)   # 보기의 키 처리(Space 손 도구·Enter 쪽 넘김)는 건너뜀
                return True
            if et == QEvent.Type.KeyRelease:
                QGraphicsView.keyReleaseEvent(self.v, e)
                return True
            if et == QEvent.Type.FocusOut and not self._busy and e.reason() not in (
                    Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason):
                self.finish()
        return False

    def text(self) -> str:
        return self.box.toPlainText()

    def page_rect(self) -> fitz.Rect:
        """쪽에 넣을 상자(회전 전 쪽 좌표). 너비는 화면 상자와 같게, 글이 그보다 길면 글에 맞춘다."""
        s = self.style
        w, h = A.text_box_size(self.text(), s["fontsize"], s["bold"], 0)
        u = self._unit()
        w = min(max(w, self.box.textWidth() * u), self.max_width)
        h = max(h, self.box.boundingRect().height() * u)
        z = self.v.zoom
        it = self.v.items[self.page]
        tl = it.pos() + QPointF(self.pos_pt.x() * z, self.pos_pt.y() * z)
        return self.v._scene_rect_to_page(self.page, QRectF(tl.x(), tl.y(), w * z, h * z))

    def finish(self):
        if self.done:
            return
        self.done = True
        text, rect = self.text(), self.page_rect()
        try:
            self.v.viewport().removeEventFilter(self)
            self.v.removeEventFilter(self)
            self.v.zoomChanged.disconnect(self._place)
            self.v.horizontalScrollBar().valueChanged.disconnect(self._place_bar)
            self.v.verticalScrollBar().valueChanged.disconnect(self._place_bar)
        except (RuntimeError, TypeError):
            pass
        self.bar.hide()
        self.bar.deleteLater()
        sc = self.box.scene()
        if sc is not None:
            sc.removeItem(self.box)
        self.on_done(text, rect, dict(self.style))
        self.deleteLater()
