# -*- coding: utf-8 -*-
"""글 주석을 쪽 위에서 바로 쓰기: 타자기·텍스트 박스·텍스트 말풍선 입력 상자와 노트 팝업.

입력 상자는 쪽 그림의 자식 항목이라 넘기기·확대에 같이 움직인다. 밖을 누르거나 Esc·창 밖으로
포커스가 나가면 글과 상자 자리를 on_done 으로 넘기고 사라진다. 쪽에 굽는 일은 부른 쪽(창)이 한다.
노트 팝업은 보기 위에 뜨는 작은 창(내용 · 색 여섯 · 답글)이다.
"""
from __future__ import annotations

from typing import Callable

import fitz
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextOption, QTransform
from PySide6.QtWidgets import (QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsTextItem, QGraphicsView,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QStyleOptionGraphicsItem, QToolButton, QVBoxLayout, QWidget)

from ..engine import annots as A
from .props import SWATCHES

FAMILY = "Malgun Gothic"
BASE_PX = 100          # 글꼴은 이 크기로 그리고 항목 배율로 실제 pt 크기를 맞춘다
MIN_WIDTH = 120.0      # 처음 상자 너비(pt)
LINE = 1.33            # 맑은 고딕 한 줄 높이(글자 크기 배)
CALLOUT_GAP = (30.0, 40.0)   # 말풍선: 누른 자리(지시선 끝)에서 상자 왼쪽 위까지(오른쪽, 위)
KNEE = 12.0            # 말풍선 지시선이 상자 앞에서 꺾이는 길이(pt)
ARROW = 8.0            # 열린 화살촉 길이(pt)


def _pad(width: float) -> float:
    """상자 안 여백(pt): 테두리 + 2. 쪽에 구운 글상자도 이만큼 안쪽에 글을 쓴다."""
    return (width or 0) + 2


def comment_style(kind: str, style: dict, comment_font: dict | None) -> dict:
    """새 글 주석의 글자 크기·색. 환경 설정 「주석 텍스트 박스 글꼴」(prefs["comment_font"], 크기 pt·색 0~1 셋)이
    바탕이고, 그 도구에 따로 기억해 둔 값(기본값과 다른 것)이 있으면 그것을 쓴다. 크기는 5 아래면 5."""
    from ..engine import annot_style as S
    out = dict(style)
    if comment_font:
        dflt = S.DEFAULTS.get(kind, {})
        if "size" in comment_font and out.get("fontsize") == dflt.get("fontsize"):
            out["fontsize"] = float(comment_font["size"])
        if "color" in comment_font and tuple(out.get("text_color") or ()) == tuple(dflt.get("text_color") or ()):
            out["text_color"] = tuple(float(c) for c in comment_font["color"])
    out["fontsize"] = max(5.0, float(out["fontsize"]))
    if (comment_font or {}).get("font"):
        out["family"] = str(comment_font["font"])   # 글꼴 이름(빈 칸이면 맑은 고딕)
    return out


class _Box(QGraphicsTextItem):
    """바탕·테두리를 그 주석 모양대로 두른 입력 항목. 테두리가 없으면(타자기) 점선 안내 테두리."""

    def __init__(self, parent, ed: "InlineAnnot"):
        super().__init__(parent)
        self.ed = ed

    def boundingRect(self) -> QRectF:
        """끌어 그린 상자면 글이 적어도 그린 높이까지(항목 단위)."""
        r = super().boundingRect()
        h = self.ed.min_height / self.ed._unit()
        return QRectF(r.x(), r.y(), r.width(), max(r.height(), h))

    def paint(self, p: QPainter, opt: QStyleOptionGraphicsItem, w: QWidget | None = None):
        s, u = self.ed.style, self.ed._unit()
        r = self.boundingRect()
        if s.get("fill"):
            p.fillRect(r, QColor.fromRgbF(*s["fill"]))
        super().paint(p, opt, w)
        bw = s.get("width") or 0
        if s.get("border") and bw > 0:
            p.setPen(QPen(QColor.fromRgbF(*s["border"]), bw / u))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(bw / u / 2, bw / u / 2, -bw / u / 2, -bw / u / 2))
        else:
            p.setPen(QPen(QColor(0, 120, 215), 0, Qt.PenStyle.DashLine))   # 0 = 확대와 상관없이 한 화소
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)


class InlineAnnot(QObject):
    """쪽 위에서 바로 쓰는 글 주석 상자 하나. finish() 뒤에는 다시 쓰지 않는다.

    kind: textbox · callout · typewriter. style: fontsize · text_color · fill · border · width · opacity.
    새로 쓰기는 scene_pt(누른 자리, 장면 좌표). 말풍선이면 그 자리가 지시선 끝이고 상자는 오른쪽 위.
    drag(장면 좌표 네모)를 주면 끌어 그린 자리·크기로 상자를 연다(너비는 그 너비에서 줄을 바꾸고, 높이는 적어도 그만큼).
    고치기는 box(회전 전 쪽 좌표의 상자)·text·callout(지시선 꼭짓점, 쪽 좌표)을 준다.
    on_done(text, page_rect, callout_points): 끝날 때 한 번. callout_points 는 새 말풍선만(아니면 None).
    """

    def __init__(self, viewer, page: int, kind: str, style: dict,
                 on_done: Callable[[str, fitz.Rect, list | None], None], scene_pt: QPointF | None = None,
                 box: fitz.Rect | None = None, text: str = "", callout: list | None = None, drag: QRectF | None = None):
        super().__init__(viewer)
        self.v = viewer
        self.page = page
        self.kind = kind
        self.style = dict(style)
        if kind == "typewriter":
            self.style.update(fill=None, border=None, width=0)
        self.on_done = on_done
        self.done = False
        self.editing = box is not None
        self.min_height = 0.0   # 끌어 그린 높이(pt). 글이 적어도 상자는 이만큼
        self.fixed_width = False
        it = viewer.items[page]
        z = viewer.zoom
        page_w, page_h = it.w / z, it.h / z
        # 고치는 글이 화면에서 돌아 보이는 각(시계 방향): 글 방향과 지금 화면 방향(쪽 회전 + 보기 회전)의 차이
        self.turn = (viewer.text_rotation(page) - int(self.style.get("rotate") or 0)) % 360 if box is not None else 0
        if box is not None:
            r = viewer.from_page_rect(page, box)
            r = QRectF((r.left() - it.pos().x()) / z, (r.top() - it.pos().y()) / z, r.width() / z, r.height() / z)
            # 글이 시작하는 모서리(돌린 상자의 왼쪽 위)를 붙박고, 글이 늘면 그쪽에서 자란다
            self.pos_pt = {90: r.topRight(), 180: r.bottomRight(), 270: r.bottomLeft()}.get(self.turn, r.topLeft())
            self.min_width = max(10.0, r.height() if self.turn in (90, 270) else r.width())
            # 고칠 때 지시선은 그대로(장면 좌표로 바꿔 둔다)
            self.tip_pt = None
            self._fixed_leader = [self._disp(viewer.from_page_point(page, fitz.Point(q))) for q in callout or []]
        elif drag is not None and kind != "callout":
            local = drag.topLeft() - it.pos()
            self.pos_pt = QPointF(max(0.0, local.x() / z), max(0.0, local.y() / z))
            self.min_width = max(10.0, drag.width() / z)
            self.min_height = drag.height() / z
            self.fixed_width = True
            self.tip_pt = None
            self._fixed_leader = []
        else:
            local = scene_pt - it.pos()
            click = QPointF(local.x() / z, local.y() / z)
            self._fixed_leader = []
            self.min_width = MIN_WIDTH
            if kind == "callout":
                self.tip_pt = click
                x = click.x() + CALLOUT_GAP[0]
                if x + MIN_WIDTH > page_w:   # 오른쪽에 자리가 없으면 왼쪽 위로
                    x = max(0.0, click.x() - CALLOUT_GAP[0] - MIN_WIDTH)
                h = self.style["fontsize"] * LINE + 2 * _pad(self.style.get("width"))
                self.pos_pt = QPointF(x, max(0.0, click.y() - CALLOUT_GAP[1] - h / 2))
            else:
                self.tip_pt = None
                # 누른 자리가 첫 줄 가운데에 오게 줄 높이 절반을 올린다
                pad = _pad(self.style.get("width"))
                self.pos_pt = QPointF(max(0.0, click.x() - pad),
                                      max(0.0, click.y() - self.style["fontsize"] * LINE / 2 - pad))
        x, y = self.pos_pt.x(), self.pos_pt.y()
        # 줄 방향으로 쪽 끝까지 남은 길이, 줄이 쌓이는 방향으로 남은 길이
        self.max_width = max(10.0, {90: page_h - y, 180: x, 270: y}.get(self.turn, page_w - x))
        self.min_width = min(self.min_width, self.max_width)
        self.room_h = {90: x, 180: y, 270: page_w - x}.get(self.turn, page_h - y)

        self.box = _Box(it, self)
        self.box.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.box.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.box.setZValue(10)
        self.box.setOpacity(self.style.get("opacity", 1.0))
        self.leader = QGraphicsPathItem(it) if (kind == "callout") else None
        if self.leader is not None:
            self.leader.setZValue(9)
        f = QFont(self.style.get("family") or FAMILY)   # 쪽에 굽는 글꼴과 같게(환경 설정 「주석 텍스트 박스 글꼴」)
        f.setPixelSize(BASE_PX)
        self.box.setFont(f)
        self.box.setDefaultTextColor(QColor.fromRgbF(*self.style["text_color"]))
        opt = self.box.document().defaultTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.box.document().setDefaultTextOption(opt)
        self.box.document().setDocumentMargin(_pad(self.style.get("width")) / self._unit())
        if text:
            self.box.setPlainText(text)
            c = self.box.textCursor()
            c.movePosition(c.MoveOperation.End)
            self.box.setTextCursor(c)
        self.box.document().contentsChanged.connect(self._fit)
        self._place()

        viewer.viewport().installEventFilter(self)
        viewer.installEventFilter(self)
        viewer.zoomChanged.connect(self._place)
        viewer.setFocus(Qt.FocusReason.OtherFocusReason)
        viewer.scene_.setFocusItem(self.box, Qt.FocusReason.MouseFocusReason)

    def _disp(self, scene_pt: QPointF) -> QPointF:
        it = self.v.items[self.page]
        return QPointF((scene_pt.x() - it.pos().x()) / self.v.zoom, (scene_pt.y() - it.pos().y()) / self.v.zoom)

    # ── 모양 ─────────────────────────────────────────────────
    def _unit(self) -> float:
        return self.style["fontsize"] / BASE_PX   # 항목 한 칸이 몇 pt 인가

    def _fit(self):
        """너비는 처음 너비에서 가장 긴 줄만큼 늘고, 쪽 오른쪽 끝에서 줄을 바꾼다. 끌어 그린 상자는 그 너비 그대로."""
        u = self._unit()
        doc = self.box.document()
        doc.setTextWidth(-1)
        want = doc.idealWidth() * u + A.TEXT_PAD
        w = min(self.max_width, self.min_width if self.fixed_width else max(self.min_width, want))
        if abs(self.box.textWidth() - w / u) > 1e-3:
            self.box.setTextWidth(w / u)
        else:
            doc.setTextWidth(w / u)
        self._draw_leader()

    def _place(self, *_):
        z = self.v.zoom
        self.box.setScale(z * self._unit())
        self.box.setRotation(self.turn)
        self.box.setPos(self.pos_pt.x() * z, self.pos_pt.y() * z)
        self._fit()

    def _turned(self, w: float, h: float) -> QRectF:
        """글 방향 w×h(pt) 상자를 화면에 돌려 놓은 네모(화면에 보이는 쪽 기준 pt)."""
        return QTransform().rotate(self.turn).mapRect(QRectF(0, 0, w, h)).translated(self.pos_pt)

    def _box_pt(self) -> QRectF:
        """상자(화면에 보이는 쪽 기준 pt)."""
        u = self._unit()
        r = self.box.boundingRect()
        return self._turned(r.width() * u, r.height() * u)

    def leader_points(self) -> list[QPointF]:
        """말풍선 지시선 꼭짓점(화면 쪽 pt): 끝(누른 자리) → 꺾임 → 상자 옆 가운데."""
        if self._fixed_leader:
            return self._fixed_leader
        if self.tip_pt is None:
            return []
        b = self._box_pt()
        my = b.center().y()
        if self.tip_pt.x() <= b.left():
            return [self.tip_pt, QPointF(b.left() - KNEE, my), QPointF(b.left(), my)]
        return [self.tip_pt, QPointF(b.right() + KNEE, my), QPointF(b.right(), my)]

    def _draw_leader(self):
        if self.leader is None:
            return
        pts = self.leader_points()
        if len(pts) < 2:
            return
        z = self.v.zoom
        path = QPainterPath(pts[0] * z)
        for q in pts[1:]:
            path.lineTo(q * z)
        # 끝의 열린 화살촉
        a, b = pts[0], pts[1]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        n = max(1e-6, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / n, dy / n
        for s in (1, -1):
            ax = a.x() + ARROW * (ux * 0.866 - s * uy * 0.5)
            ay = a.y() + ARROW * (uy * 0.866 + s * ux * 0.5)
            path.moveTo(a * z)
            path.lineTo(QPointF(ax, ay) * z)
        st = self.style
        c = st.get("border") or st["text_color"]
        self.leader.setPen(QPen(QColor.fromRgbF(*c), max(st.get("width") or 0, 1) * z))
        self.leader.setPath(path)

    def _scene_rect(self) -> QRectF:
        return self.box.mapRectToScene(self.box.boundingRect())

    # ── 입력 넘기기(텍스트 추가와 같은 방식) ─────────────────────
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if self.done:
            return False
        et = e.type()
        if obj is self.v.viewport():
            if et == QEvent.Type.MouseButtonPress:
                if self._scene_rect().adjusted(-2, -2, 2, 2).contains(self.v.mapToScene(e.position().toPoint())):
                    QGraphicsView.mousePressEvent(self.v, e)   # 상자 안: 보기의 도구 처리는 건너뛰고 상자로
                    return True
                self.finish()
                return e.button() == Qt.MouseButton.LeftButton   # 왼쪽 누름은 끝내기만
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
                e.accept()   # 창의 단축키(Delete·Ctrl+Z 등)보다 입력이 먼저
                return True
            if et == QEvent.Type.KeyPress:
                if e.key() == Qt.Key.Key_Escape:
                    self.finish()
                    return True
                QGraphicsView.keyPressEvent(self.v, e)
                return True
            if et == QEvent.Type.KeyRelease:
                QGraphicsView.keyReleaseEvent(self.v, e)
                return True
            if et == QEvent.Type.FocusOut and e.reason() not in (
                    Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason):
                self.finish()
        return False

    def text(self) -> str:
        return self.box.toPlainText()

    def page_rect(self) -> fitz.Rect:
        """쪽에 넣을 상자(회전 전 쪽 좌표). 글이 쪽 글상자에서 잘리지 않게 줄 높이에 조금 여유를 둔다."""
        u = self._unit()
        br = self.box.boundingRect()
        fs = self.style["fontsize"]
        h = min(max(br.height() * u, (self.text().count("\n") + 1) * fs * LINE + 2 * _pad(self.style.get("width"))) + fs * 0.2,
                self.room_h)
        b = self._turned(br.width() * u, h)
        z = self.v.zoom
        it = self.v.items[self.page]
        tl = it.pos() + QPointF(b.left() * z, b.top() * z)
        return self.v._scene_rect_to_page(self.page, QRectF(tl.x(), tl.y(), b.width() * z, b.height() * z))

    def page_callout(self) -> list[fitz.Point] | None:
        if self.tip_pt is None or self._fixed_leader:
            return None
        it = self.v.items[self.page]
        z = self.v.zoom
        return [self.v.to_page(self.page, it.pos() + q * z) for q in self.leader_points()]

    def finish(self):
        if self.done:
            return
        self.done = True
        text, rect, callout = self.text(), self.page_rect(), self.page_callout()
        try:
            self.v.viewport().removeEventFilter(self)
            self.v.removeEventFilter(self)
            self.v.zoomChanged.disconnect(self._place)
        except (RuntimeError, TypeError):
            pass
        for item in (self.box, self.leader):
            if item is not None and item.scene() is not None:
                item.scene().removeItem(item)
        self.on_done(text, rect, callout)
        self.deleteLater()


class NotePopup(QFrame):
    """노트 팝업: 내용 · 색 여섯 · 답글 목록과 답장 입력줄. 닫을 때(닫기·Esc·밖을 누름) 내용·색을 넣는다.

    xref 가 None 이면 새 노트: 닫을 때 내용이 있거나 답글을 달 때 그 자리에 만든다.
    on_change(page): 문서를 바꿀 때마다(목록·쪽 다시 그리기). on_closed(): 닫힌 뒤 한 번.
    """

    def __init__(self, viewer, page: int, point: fitz.Point, xref: int | None, color, author: str,
                 on_change: Callable[[int], None], on_closed: Callable[[], None] | None = None):
        super().__init__(viewer.viewport())
        self.v, self.doc = viewer, viewer.doc
        self.page, self.point, self.xref = page, fitz.Point(point), xref
        self.author = A.user_author(author)
        self.on_change, self.on_closed = on_change, on_closed
        self.done = False
        content = ""
        if xref is not None:
            a = A.get(self.doc, page, xref)
            if a:
                content = a.info.get("content", "")
                c = a.colors.get("stroke")
                color = tuple(c) if c else color
        self.color0, self.content0 = tuple(color), content
        self.color = tuple(color)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setFixedWidth(260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.head = QLabel(self)
        lay.addWidget(self.head)
        self.text = QPlainTextEdit(content, self)
        self.text.setFixedHeight(80)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.swatches: list[QToolButton] = []
        for c in SWATCHES:
            b = QToolButton(self)
            b.setCheckable(True)
            b.setFixedSize(22, 22)
            b.setStyleSheet(f"QToolButton {{ background: {QColor.fromRgbF(*c).name()}; border: 1px solid #888; }}"
                            "QToolButton:checked { border: 2px solid #000; }")
            b.clicked.connect(lambda _=False, c=c: self.set_color(c))
            row.addWidget(b)
            self.swatches.append(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.reply_box = QVBoxLayout()
        self.reply_box.setSpacing(2)
        lay.addLayout(self.reply_box)
        row = QHBoxLayout()
        self.reply = QLineEdit(self)
        self.reply.setPlaceholderText("답장 입력")
        self.reply.returnPressed.connect(self.add_reply)
        row.addWidget(self.reply, 1)
        self.reply_btn = QPushButton("답글", self)
        self.reply_btn.clicked.connect(self.add_reply)
        row.addWidget(self.reply_btn)
        lay.addLayout(row)
        self.close_btn = QPushButton("닫기", self)
        self.close_btn.clicked.connect(self.finish)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignRight)
        self._sync_swatches()
        self._load_replies()

        viewer.viewport().installEventFilter(self)
        viewer.zoomChanged.connect(self._place)
        viewer.horizontalScrollBar().valueChanged.connect(self._place)
        viewer.verticalScrollBar().valueChanged.connect(self._place)
        self._place()
        self.show()
        self.raise_()
        self.text.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── 모양 ─────────────────────────────────────────────────
    def _sync_swatches(self):
        for b, c in zip(self.swatches, SWATCHES):
            b.setChecked(all(abs(x - y) < 0.01 for x, y in zip(c, self.color)))

    def _place(self, *_):
        sp = self.v.from_page_point(self.page, self.point)
        vp = self.v.mapFromScene(sp)
        self.adjustSize()
        x = vp.x() + int(24 * self.v.zoom)
        if x + self.width() > self.v.viewport().width():
            x = max(0, vp.x() - self.width() - 8)
        y = max(0, min(vp.y(), self.v.viewport().height() - self.height()))
        self.move(x, y)

    def _load_replies(self):
        while self.reply_box.count():
            w = self.reply_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        reps = A.replies(self.doc, self.page, self.xref) if self.xref is not None else []
        a = A.get(self.doc, self.page, self.xref) if self.xref is not None else None
        who = (a.info.get("title", "") if a else "") or self.author
        self.head.setText(f"노트 · {who}" if who else "노트")
        for r in reps:
            w = QWidget(self)
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            row.addWidget(QLabel(r["author"] or "", w))
            ed = QLineEdit(r["content"], w)
            ed.editingFinished.connect(lambda ed=ed, x=r["xref"], old=r["content"]: self._edit_reply(x, ed, old))
            row.addWidget(ed, 1)
            b = QPushButton("삭제", w)
            b.clicked.connect(lambda _=False, x=r["xref"]: self._delete_reply(x))
            row.addWidget(b)
            self.reply_box.addWidget(w)
        self._place()

    # ── 바꾸기 ───────────────────────────────────────────────
    def set_color(self, c):
        self.color = tuple(c)
        self._sync_swatches()

    def _ensure_note(self) -> bool:
        if self.xref is None:
            self.xref = A.note(self.doc, self.page, self.point, self.text.toPlainText(), color=self.color,
                               author=self.author)
            self.color0, self.content0 = self.color, self.text.toPlainText()
            self.on_change(self.page)
        return self.xref is not None

    def add_reply(self):
        t = self.reply.text().strip()
        if not t or not self._ensure_note():
            return
        A.add_reply(self.doc, self.page, self.xref, t, self.author)
        self.reply.clear()
        self.on_change(self.page)
        self._load_replies()

    def _edit_reply(self, xref: int, ed: QLineEdit, old: str):
        if self.done or ed.text() == old:
            return
        A.update_reply(self.doc, self.page, xref, ed.text())
        self.on_change(self.page)
        self._load_replies()

    def _delete_reply(self, xref: int):
        A.delete_reply(self.doc, self.page, xref)
        self.on_change(self.page)
        self._load_replies()

    # ── 닫기 ─────────────────────────────────────────────────
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish()
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if not self.done and obj is self.v.viewport() and e.type() == QEvent.Type.MouseButtonPress:
            self.finish()   # 팝업 밖(쪽)을 누르면 닫는다. 누름은 그대로 도구로 간다
        return False

    def finish(self):
        if self.done:
            return
        fw = self.focusWidget()
        if isinstance(fw, QLineEdit) and fw is not self.reply:
            fw.editingFinished.emit()   # 고치던 답글을 먼저 넣는다
        self.done = True
        text = self.text.toPlainText()
        try:
            self.v.viewport().removeEventFilter(self)
            self.v.zoomChanged.disconnect(self._place)
            self.v.horizontalScrollBar().valueChanged.disconnect(self._place)
            self.v.verticalScrollBar().valueChanged.disconnect(self._place)
        except (RuntimeError, TypeError):
            pass
        if self.xref is None:
            if text.strip():
                self._ensure_note()
        elif text != self.content0 or self.color != self.color0:
            A.update(self.doc, self.page, self.xref, content=text, stroke=self.color)
            self.on_change(self.page)
        self.hide()
        self.deleteLater()
        if self.on_closed:
            self.on_closed()
