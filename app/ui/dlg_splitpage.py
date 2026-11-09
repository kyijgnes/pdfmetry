"""페이지 분할 창: 쪽 위에 가로·세로 분할선을 놓고 끌어 옮긴 뒤, 범위의 각 쪽을 선 자리대로 여러 쪽으로 나눈다.
run_split_page 가 창부터 결과 안내까지 맡고, window.py 는 부르기만 한다."""
from __future__ import annotations

import fitz
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..engine import pages as P
from . import dialogs as D

MAX_LINES = 10          # 방향마다
MIN_AREA = 0.09         # 나눈 칸 한 변이 쪽 너비·높이의 이만큼은 돼야 한다
PREVIEW = 420
GRAB = 6                # 선을 잡는 거리(px)
TOO_MANY = "최대 10개의 가로/세로 분할선을 추가할 수 있습니다. 새로운 선을 추가하기 전에 초과한 선을 삭제하세요."
TOO_SMALL = "분할 영역은 페이지 크기의 10%보다 작을 수 없습니다. 조정한 후 다시 시도하세요."
FAILED = "페이지를 분할하지 못했습니다."


def widest_gap_middle(pos: list[float]) -> float:
    """0~1 사이에 이미 선이 pos 에 있을 때, 가장 넓은 빈 구간의 가운데."""
    edges = [0.0] + sorted(pos) + [1.0]
    a, b = max(zip(edges, edges[1:]), key=lambda g: g[1] - g[0])
    return (a + b) / 2


def area_ok(pos: list[float]) -> bool:
    edges = [0.0] + sorted(pos) + [1.0]
    return all(b - a >= MIN_AREA - 1e-9 for a, b in zip(edges, edges[1:]))


class SplitPreview(QWidget):
    """쪽 그림 위에 분할선을 그리고 고르기·끌기·지우기를 받는다. 선은 [방향, 자리] 이고 방향 "v" 는 세로선(가로 자리),
    "h" 는 가로선(세로 자리), 자리는 보이는 쪽 너비·높이에 대한 비율."""
    changed = Signal()

    def __init__(self, pix: QPixmap, parent=None):
        super().__init__(parent)
        self.pix = pix
        self.lines: list[list] = []
        self.sel: int | None = None
        self._drag = False
        self.setMinimumSize(PREVIEW + 20, PREVIEW + 20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    # ── 자리 계산 ──
    def page_rect(self) -> QRectF:
        w, h = max(self.pix.width(), 1), max(self.pix.height(), 1)
        k = min((self.width() - 20) / w, (self.height() - 20) / h)
        return QRectF((self.width() - w * k) / 2, (self.height() - h * k) / 2, w * k, h * k)

    def positions(self, kind: str) -> list[float]:
        return sorted(p for k, p in self.lines if k == kind)

    def _hit(self, pt: QPointF) -> int | None:
        r = self.page_rect()
        best, gap = None, GRAB + 1
        for i, (k, p) in enumerate(self.lines):
            d = abs(pt.x() - (r.x() + p * r.width())) if k == "v" else abs(pt.y() - (r.y() + p * r.height()))
            inside = r.top() - GRAB <= pt.y() <= r.bottom() + GRAB if k == "v" else r.left() - GRAB <= pt.x() <= r.right() + GRAB
            if inside and d < gap:
                best, gap = i, d
        return best

    # ── 바꾸기 ──
    def add(self, kind: str) -> bool:
        pos = self.positions(kind)
        if len(pos) >= MAX_LINES:
            return False
        self.lines.append([kind, widest_gap_middle(pos)])
        self.sel = len(self.lines) - 1
        self._done()
        return True

    def delete_selected(self):
        if self.sel is not None:
            del self.lines[self.sel]
            self.sel = None
            self._done()

    def distribute(self, kind: str):
        idx = sorted((i for i, (k, _) in enumerate(self.lines) if k == kind), key=lambda i: self.lines[i][1])
        for n, i in enumerate(idx, 1):
            self.lines[i][1] = n / (len(idx) + 1)
        self._done()

    def _done(self):
        self.update()
        self.changed.emit()

    # ── 마우스·글쇠 ──
    def mousePressEvent(self, ev):
        self.setFocus()
        if ev.button() == Qt.MouseButton.LeftButton:
            self.sel = self._hit(ev.position())
            self._drag = self.sel is not None
            self._done()

    def mouseMoveEvent(self, ev):
        r = self.page_rect()
        if self._drag and self.sel is not None:
            k = self.lines[self.sel][0]
            t = (ev.position().x() - r.x()) / r.width() if k == "v" else (ev.position().y() - r.y()) / r.height()
            self.lines[self.sel][1] = min(0.99, max(0.01, t))
            self._done()
            return
        hit = self._hit(ev.position())
        if hit is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.SplitHCursor if self.lines[hit][0] == "v" else Qt.CursorShape.SplitVCursor)

    def mouseReleaseEvent(self, ev):
        self._drag = False

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#E9ECEF"))
        r = self.page_rect()
        p.drawPixmap(r, self.pix, QRectF(self.pix.rect()))
        p.setPen(QPen(QColor("#CED4DA"), 1)); p.drawRect(r)
        for i, (k, t) in enumerate(self.lines):
            on = i == self.sel
            p.setPen(QPen(QColor("#E03131" if on else "#1C7ED6"), 3 if on else 2, Qt.PenStyle.SolidLine if on else Qt.PenStyle.DashLine))
            if k == "v":
                x = r.x() + t * r.width(); p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            else:
                y = r.y() + t * r.height(); p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.end()


def _page_pixmap(doc, pno: int) -> QPixmap:
    with doc.lock:
        page = doc.fitz[pno]
        zoom = PREVIEW / max(page.rect.width, page.rect.height, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(img)


class SplitPageDialog(QDialog):
    def __init__(self, parent, doc, current: int, pages: list[int] | None = None):
        super().__init__(parent)
        self.setWindowTitle("페이지 분할")
        total = doc.page_count
        lay = QVBoxLayout(self)
        body = QHBoxLayout(); lay.addLayout(body, 1)

        side = QWidget(); side.setFixedWidth(280); body.addWidget(side)
        left = QVBoxLayout(side); left.setContentsMargins(0, 0, 0, 0)
        head = QLabel("분할선"); head.setStyleSheet("font-weight: bold;"); left.addWidget(head)
        self.add_h = QPushButton("가로 분할선 추가"); left.addWidget(self.add_h)
        self.add_v = QPushButton("세로 분할선 추가"); left.addWidget(self.add_v)
        left.addSpacing(12)
        rhead = QLabel("페이지 범위"); rhead.setStyleSheet("font-weight: bold;"); left.addWidget(rhead)
        self.range = D.PageRangeEdit(total, current); left.addWidget(self.range)
        self.range.set_pages(pages or [current])
        left.addStretch(1)

        right = QVBoxLayout(); body.addLayout(right, 1)
        ahead = QLabel("구분선 조정"); ahead.setStyleSheet("font-weight: bold;"); right.addWidget(ahead)
        self.preview = SplitPreview(_page_pixmap(doc, (pages or [current])[0])); right.addWidget(self.preview, 1)
        tools = QHBoxLayout(); right.addLayout(tools)
        self.dist_v = QPushButton("가로 등거리"); tools.addWidget(self.dist_v)
        self.dist_h = QPushButton("세로 등거리"); tools.addWidget(self.dist_h)
        self.delete = QPushButton("분할선 삭제"); tools.addWidget(self.delete)
        tools.addStretch(1)

        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.bb.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText("적용")
        D.settle_default(self, self.ok)
        self.bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.bb.accepted.connect(self._apply); self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)

        self.add_h.clicked.connect(lambda: self._add("h"))
        self.add_v.clicked.connect(lambda: self._add("v"))
        self.dist_v.clicked.connect(lambda: self.preview.distribute("v"))
        self.dist_h.clicked.connect(lambda: self.preview.distribute("h"))
        self.delete.clicked.connect(self.preview.delete_selected)
        self.preview.changed.connect(self._sync)
        self.preview.lines.append(["v", 0.5])   # 처음엔 세로선 하나를 가운데
        self._sync()

    def _add(self, kind: str):
        if not self.preview.add(kind):
            QMessageBox.warning(self, "페이지 분할", TOO_MANY)
        self.preview.setFocus()

    def _sync(self):
        self.ok.setEnabled(bool(self.preview.lines))
        self.delete.setEnabled(self.preview.sel is not None)
        self.dist_v.setEnabled(len(self.preview.positions("v")) > 0)
        self.dist_h.setEnabled(len(self.preview.positions("h")) > 0)

    def _apply(self):
        if not self.preview.lines:
            return
        if not (area_ok(self.preview.positions("v")) and area_ok(self.preview.positions("h"))):
            QMessageBox.warning(self, "페이지 분할", TOO_SMALL); return
        try:
            self.range.pages()
        except ValueError as e:
            QMessageBox.warning(self, "페이지 분할", str(e)); self.range.focus_error(); return
        self.accept()

    def values(self) -> dict:
        return {"pages": self.range.pages(), "xs": self.preview.positions("v"), "ys": self.preview.positions("h")}


def run_split_page(win, t, pages: list[int]):
    """페이지 분할 창 → 나누기(되돌리기 한 번) → 썸네일 새로 그리기 → 상태 줄 안내."""
    d = t.doc
    dlg = SplitPageDialog(win, d, pages[0] if pages else t.viewer.current, pages)
    if not dlg.exec():
        return
    vals = dlg.values()

    def job(progress=None):
        try:
            return P.split_grid(d, vals["pages"], xs=vals["xs"], ys=vals["ys"], progress=progress)
        except Exception as e:  # noqa: BLE001
            from ..engine.tools import EngineError
            raise EngineError(FAILED) from e

    m = D.ProgressDialog(win, "페이지 분할", job).run()
    if m is None:
        return
    t.changed(None, structural=True, mapping=m)
    win.say(f"쪽 {len(vals['pages'])}장을 나눠 모두 {d.page_count}쪽이 됐습니다")
