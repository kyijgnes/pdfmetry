"""구성 탭 자르기·페이지 크기·페이지 박스 대화상자와 공용 미리보기(끌어서 상자 정하기).

길이 칸은 환경 설정의 측정 단위(prefs["unit"], 기본 cm)를 따르고, 속은 pt 로 들고 있는다.
여백은 화면에 보이는 방향(회전 반영)에서 MediaBox 안쪽으로 들어온 거리다. 값만 모아 돌려주고 일은 window.py 가 엔진에 시킨다."""
from __future__ import annotations

import fitz
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from ..engine import pages as P
from ..engine.document import Document, del_keys
from .dialogs import PageRangeEdit, _buttons

BAD_RANGE = "유효한 페이지 범위를 입력하십시오."
UNITS = {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "pt": 1.0}   # 단위 하나가 몇 pt
MIN_PX = 18           # 화면에서 상자 최소 한 변
HANDLE = 8            # 모서리 손잡이 한 변(px)
RENDER_PX = 900       # 미리보기 그림의 긴 변(px)

_IN, _MM = 72.0, 72 / 25.4
PAPERS = [(n, w * _IN, h * _IN, "in", w, h) for n, w, h in (
    ("Letter", 8.5, 11), ("Legal", 8.5, 14), ("Executive", 7.25, 10.5), ("Ledger", 17, 11), ("Tabloid", 11, 17),
    ("ANSI C", 17, 22), ("ANSI D", 22, 34), ("ANSI E", 34, 44), ("ANSI F", 28, 40),
    ("ARCH A", 9, 12), ("ARCH B", 12, 18), ("ARCH C", 18, 24), ("ARCH D", 24, 36), ("ARCH E", 36, 48),
    ("ARCH E1", 30, 42), ("ARCH E2", 26, 38), ("ARCH E3", 27, 39))]
PAPERS += [(n, w * _MM, h * _MM, "mm", w, h) for n, w, h in (
    ("A5", 148, 210), ("A4", 210, 297), ("A3", 297, 420), ("A2", 420, 594), ("A1", 594, 841), ("A0", 841, 1189))]
PAPERS += [(n, w * _IN, h * _IN, "in", w, h) for n, w, h in (
    ("Oversize A2", 18.903, 24.611), ("Oversize A1", 24.611, 35.431), ("Oversize A0", 35.431, 49.014))]
PAPERS += [(n, w * _MM, h * _MM, "mm", w, h) for n, w, h in (
    ("B5", 176, 250), ("B4", 250, 353), ("B3", 353, 500), ("B2", 500, 707), ("B1", 707, 1000), ("B0", 1000, 1414),
    ("C5", 162, 229), ("C4", 229, 324), ("C3", 324, 458), ("C2", 458, 648), ("C1", 648, 917), ("C0", 917, 1297),
    ("JIS B5", 182, 257), ("JIS B4", 257, 364), ("JIS B3", 364, 515), ("JIS B2", 515, 728), ("JIS B1", 728, 1030),
    ("JIS B0", 1030, 1456))]
PAPERS += [("Oversize 92", 92 * _IN, 92 * _IN, "in", 92, 92)]
BOXES = (("crop", "자르기 박스"), ("bleed", "블리드 박스"), ("trim", "트림 박스"), ("art", "아트 박스"))
ZERO = {"top": 0.0, "left": 0.0, "bottom": 0.0, "right": 0.0}


def _warn(parent, text: str):
    QMessageBox.warning(parent, "PDF메트리", text)


def _paper_text(p) -> str:
    return f"{p[0]} ({p[4]:g} x {p[5]:g} {p[3]})"


def render_media(doc: Document, i: int) -> tuple[QImage, float, float]:
    """쪽 i 의 MediaBox 전체(자르기 전)를 보이는 방향으로 그린다. (그림, 가로 pt, 세로 pt)"""
    with doc.lock:
        rot, media = P.page_geom(doc.fitz, i)
        w, h = P.view_size(media, rot)
        try:
            tmp = fitz.open()
            tmp.insert_pdf(doc.fitz, from_page=i, to_page=i)
            del_keys(tmp, tmp[0].xref, ("CropBox",))
            page = tmp[0]
        except Exception:  # noqa: BLE001  사본을 못 뜨면 보이는 영역만
            page = doc.fitz[i]
        z = RENDER_PX / max(w, h, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), annots=True)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    return img, w, h


class UnitBox(QDoubleSpinBox):
    """pt 값을 들고 단위로 보여 주는 숫자 칸."""
    def __init__(self, unit: str, max_units: float, parent=None):
        super().__init__(parent)
        self.k = UNITS.get(unit, UNITS["cm"])
        self.setDecimals(2 if unit in ("cm", "in") else 1)
        self.setSingleStep(0.1 if unit in ("cm", "in") else 1)
        self.setRange(0, max_units); self.setSuffix(f" {unit}")
        self.setMinimumWidth(96)

    def pt(self) -> float:
        return self.value() * self.k

    def set_pt(self, v: float):
        self.blockSignals(True); self.setValue(v / self.k); self.blockSignals(False)


def fmt_size(w: float, h: float, unit: str) -> str:
    k = UNITS.get(unit, UNITS["cm"])
    d = 2 if unit in ("cm", "in") else 1
    return f"{w / k:.{d}f} x {h / k:.{d}f} {unit}"


# ── 공용 미리보기 ─────────────────────────────────────────────
class BoxPreview(QWidget):
    """쪽 그림 위에 상자 하나. 판(sheet)은 pt, 왼쪽 위가 0.
    끌기: 모서리 손잡이로 크기, 몸통으로 옮기기, 상자 밖에서 끌면 새 상자, 끌지 않고 밖을 누르면 판 전체."""
    changed = Signal(QRectF)
    BASE = QSize(300, 380)
    PAD = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.img: QImage | None = None
        self.sheet = QRectF(0, 0, 595, 842)
        self.img_rect = QRectF(self.sheet)
        self.box = QRectF(self.sheet)
        self.editable = True
        self.zoom = 1.0
        self._drag = None
        self.setMouseTracking(True)
        self.setFixedSize(self.BASE)

    # 바깥에서 부르는 것
    def set_page(self, img: QImage, w: float, h: float, sheet: tuple[float, float] | None = None, at: QRectF | None = None):
        """img(가로 w·세로 h pt)를 sheet 크기 판의 at 자리에 놓는다(둘 다 없으면 판 = 쪽)."""
        self.img = img
        self.sheet = QRectF(0, 0, *(sheet or (w, h)))
        self.img_rect = QRectF(at) if at is not None else QRectF(0, 0, w, h)
        self.update()

    def set_box(self, r: QRectF):
        self.box = QRectF(r).intersected(self.sheet) if r.isValid() else QRectF(self.sheet)
        self.update()

    def set_zoom(self, z: float):
        self.zoom = max(0.5, min(4.0, z))
        self.setFixedSize(int(self.BASE.width() * self.zoom), int(self.BASE.height() * self.zoom))
        self.update()

    def margins(self) -> dict:
        s, b = self.sheet, self.box
        return {"top": b.top(), "left": b.left(), "bottom": s.bottom() - b.bottom(), "right": s.right() - b.right()}

    def set_margins(self, m: dict):
        s = self.sheet
        self.set_box(QRectF(QPointF(m["left"], m["top"]), QPointF(s.right() - m["right"], s.bottom() - m["bottom"])))

    # 좌표
    def _scale(self) -> float:
        return min((self.width() - 2 * self.PAD) / max(self.sheet.width(), 1), (self.height() - 2 * self.PAD) / max(self.sheet.height(), 1))

    def _origin(self) -> QPointF:
        s = self._scale()
        return QPointF((self.width() - self.sheet.width() * s) / 2, (self.height() - self.sheet.height() * s) / 2)

    def to_px(self, r: QRectF) -> QRectF:
        s, o = self._scale(), self._origin()
        return QRectF(o.x() + r.x() * s, o.y() + r.y() * s, r.width() * s, r.height() * s)

    def to_pt(self, p: QPointF) -> QPointF:
        s, o = self._scale(), self._origin()
        q = QPointF((p.x() - o.x()) / s, (p.y() - o.y()) / s)
        return QPointF(min(max(q.x(), 0), self.sheet.width()), min(max(q.y(), 0), self.sheet.height()))

    def _corners(self) -> list[QPointF]:
        r = self.to_px(self.box)
        return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]

    def _hit(self, pos: QPointF):
        for k, c in enumerate(self._corners()):
            if abs(pos.x() - c.x()) <= HANDLE and abs(pos.y() - c.y()) <= HANDLE:
                return ("corner", k)
        if self.to_px(self.box).contains(pos):
            return ("move", None)
        return None

    # 그리기
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#E9ECEF"))
        sheet = self.to_px(self.sheet)
        p.fillRect(sheet, QColor("white"))
        if self.img is not None:
            p.drawImage(self.to_px(self.img_rect), self.img)
        if self.img_rect != self.sheet:   # 넓히거나 맞춘 판: 원래 쪽 자리를 점선으로
            p.setPen(QPen(QColor("#ADB5BD"), 1, Qt.PenStyle.DashLine)); p.drawRect(self.to_px(self.img_rect))
        p.setPen(QPen(QColor("#ADB5BD"), 1)); p.drawRect(sheet)
        box = self.to_px(self.box)
        shade = QColor(0, 0, 0, 72)
        for r in (QRectF(sheet.left(), sheet.top(), sheet.width(), box.top() - sheet.top()),
                  QRectF(sheet.left(), box.bottom(), sheet.width(), sheet.bottom() - box.bottom()),
                  QRectF(sheet.left(), box.top(), box.left() - sheet.left(), box.height()),
                  QRectF(box.right(), box.top(), sheet.right() - box.right(), box.height())):
            if r.width() > 0 and r.height() > 0:
                p.fillRect(r, shade)
        p.setPen(QPen(QColor("#868E96"), 1)); p.drawRect(box)
        if self.editable:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#228BE6"))
            for c in self._corners():
                p.drawRect(QRectF(c.x() - HANDLE / 2, c.y() - HANDLE / 2, HANDLE, HANDLE))
        p.end()

    # 끌기
    def mousePressEvent(self, ev):
        if not self.editable or ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position()
        hit = self._hit(pos)
        if hit and hit[0] == "corner":
            opp = self._corners()[(hit[1] + 2) % 4]
            self._drag = ("corner", self.to_pt(opp))
        elif hit:
            self._drag = ("move", self.to_pt(pos) - self.box.topLeft())
        else:
            self._drag = ("press", pos)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._drag is None:
            hit = self._hit(pos) if self.editable else None
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if hit and hit[0] == "corner" and hit[1] in (0, 2) else
                           Qt.CursorShape.SizeBDiagCursor if hit and hit[0] == "corner" else
                           Qt.CursorShape.SizeAllCursor if hit else Qt.CursorShape.CrossCursor)
            return
        kind, a = self._drag
        if kind == "press":
            if (pos - a).manhattanLength() < 4:
                return
            self._drag = kind, a = ("corner", self.to_pt(a))
        q = self.to_pt(pos)
        if kind == "corner":
            self._resize(a, q)
        else:
            w, h = self.box.width(), self.box.height()
            x = min(max(q.x() - a.x(), 0), self.sheet.width() - w)
            y = min(max(q.y() - a.y(), 0), self.sheet.height() - h)
            self.box = QRectF(x, y, w, h)
        self.update(); self.changed.emit(QRectF(self.box))

    def _resize(self, fixed: QPointF, q: QPointF):
        m = MIN_PX / max(self._scale(), 1e-6)
        x0, x1 = sorted((fixed.x(), q.x())); y0, y1 = sorted((fixed.y(), q.y()))
        if x1 - x0 < m:
            x0, x1 = (x0, x0 + m) if q.x() >= fixed.x() else (x1 - m, x1)
        if y1 - y0 < m:
            y0, y1 = (y0, y0 + m) if q.y() >= fixed.y() else (y1 - m, y1)
        self.box = QRectF(QPointF(x0, y0), QPointF(x1, y1)).intersected(self.sheet)

    def mouseReleaseEvent(self, ev):
        if self._drag and self._drag[0] == "press":
            self.box = QRectF(self.sheet); self.update(); self.changed.emit(QRectF(self.box))
        self._drag = None


class PreviewPanel(QWidget):
    """미리보기 + 쪽 넘기기(범위 안에서) + 확대·축소 + 크기 글 한 줄."""
    pageChanged = Signal(int)

    def __init__(self, total: int, parent=None):
        super().__init__(parent)
        self.total, self.pages, self.k = total, [0], 0
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self.view = BoxPreview()
        self.scroll = QScrollArea(); self.scroll.setWidget(self.view); self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setFixedSize(BoxPreview.BASE.width() + 4, BoxPreview.BASE.height() + 4)
        lay.addWidget(self.scroll)
        row = QHBoxLayout()
        self.prev = QToolButton(); self.prev.setText("◀"); self.next = QToolButton(); self.next.setText("▶")
        self.where = QLabel(); self.where.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.out = QToolButton(); self.out.setText("－"); self.inn = QToolButton(); self.inn.setText("＋")
        for w in (self.prev, self.where, self.next):
            row.addWidget(w)
        row.addStretch(1); row.addWidget(self.out); row.addWidget(self.inn)
        lay.addLayout(row)
        self.caption = QLabel(); lay.addWidget(self.caption)
        self.prev.clicked.connect(lambda: self._go(-1)); self.next.clicked.connect(lambda: self._go(1))
        self.out.clicked.connect(lambda: self.view.set_zoom(self.view.zoom / 1.25))
        self.inn.clicked.connect(lambda: self.view.set_zoom(self.view.zoom * 1.25))

    def page(self) -> int:
        return self.pages[self.k]

    def set_pages(self, pages: list[int]):
        keep = self.page()
        self.pages = list(pages) or [keep]
        self.k = self.pages.index(keep) if keep in self.pages else 0
        self._show(keep != self.page())

    def _go(self, step: int):
        self.k = min(max(self.k + step, 0), len(self.pages) - 1); self._show(True)

    def _show(self, moved: bool):
        self.where.setText(f"{self.page() + 1} / {self.total}")
        self.prev.setEnabled(self.k > 0); self.next.setEnabled(self.k < len(self.pages) - 1)
        if moved:
            self.pageChanged.emit(self.page())


def _watch(w: QWidget, slot):
    """범위 칸 속 라디오·글 칸·고르기 칸이 바뀌면 slot."""
    from PySide6.QtWidgets import QLineEdit
    for b in w.findChildren(QRadioButton):
        b.toggled.connect(slot)
    for e in w.findChildren(QLineEdit):
        e.textChanged.connect(slot)
    for c in w.findChildren(QComboBox):
        c.currentIndexChanged.connect(slot)


class _Base(QDialog):
    """왼쪽 칸들 · 오른쪽 미리보기 · 아래 적용/취소."""
    def __init__(self, parent, title: str, doc: Document, unit: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.doc, self.unit = doc, unit if unit in UNITS else "cm"
        outer = QVBoxLayout(self); outer.setContentsMargins(16, 14, 16, 12); outer.setSpacing(10)
        body = QHBoxLayout(); body.setSpacing(16); outer.addLayout(body)
        self.left = QVBoxLayout(); self.left.setSpacing(10); body.addLayout(self.left)
        self.panel = PreviewPanel(doc.page_count); body.addWidget(self.panel, 0, Qt.AlignmentFlag.AlignTop)
        self.bb = _buttons(self, "적용"); outer.addWidget(self.bb)
        self.ok = self.bb.button(QDialogButtonBox.StandardButton.Ok)
        self._img = {}
        self.panel.pageChanged.connect(lambda _: self.refresh())

    def range_box(self, pages: list[int] | None) -> PageRangeEdit:
        box = QGroupBox("페이지 범위"); lay = QVBoxLayout(box)
        cur = pages[0] if pages else 0
        self.rng = PageRangeEdit(self.doc.page_count, cur)
        if pages:
            self.rng.set_pages(pages)
        lay.addWidget(self.rng); self.left.addWidget(box)
        _watch(self.rng, lambda *_: self._range_changed())
        return self.rng

    def _range_changed(self):
        try:
            self.panel.set_pages(self.rng.pages())
        except ValueError:
            pass
        self.touch()

    def touch(self):
        """사용자가 무언가 바꿨다."""
        self.refresh()

    def image(self, i: int):
        if i not in self._img:
            self._img[i] = render_media(self.doc, i)
        return self._img[i]

    def accept(self):
        try:
            self.rng.pages()
        except ValueError:
            _warn(self, BAD_RANGE); return
        super().accept()

    def refresh(self):
        raise NotImplementedError


def margin_grid(title: str, order: tuple, unit: str, slot) -> tuple[QGroupBox, dict]:
    box = QGroupBox(title); g = QGridLayout(box)
    names = {"top": "위쪽", "left": "왼쪽", "bottom": "아래쪽", "right": "오른쪽"}
    spins = {}
    for k, side in enumerate(order):
        sb = UnitBox(unit, 100000); sb.valueChanged.connect(slot)
        g.addWidget(QLabel(names[side]), k // 2, (k % 2) * 2); g.addWidget(sb, k // 2, (k % 2) * 2 + 1)
        spins[side] = sb
    return box, spins


# ── 자르기 ────────────────────────────────────────────────────
class CropDialog(_Base):
    def __init__(self, parent, doc: Document, pages: list[int], unit: str = "cm"):
        super().__init__(parent, "자르기 설정", doc, unit)
        head = QLabel("자르기 영역 조정"); head.setStyleSheet("font-weight: 600;")
        self.left.addWidget(head)
        self.left.addWidget(QLabel("미리보기의 상자를 끌어 남길 영역 지정"))
        box, self.spins = margin_grid("자르기 여백", ("top", "left", "bottom", "right"), self.unit, self._typed)
        self.left.addWidget(box)
        first = pages[0] if pages else 0
        self.m = P.box_margins(doc.fitz, first, "crop")
        self.range_box(pages)
        self.left.addStretch(1)
        self.panel.set_pages(self.rng.pages())
        self.panel.view.changed.connect(self._dragged)
        self.ok.setEnabled(False)
        self._sync_spins(); self.refresh()

    def _sync_spins(self):
        for side, sb in self.spins.items():
            sb.set_pt(self.m[side])

    def _typed(self, *_):
        self.m = {side: sb.pt() for side, sb in self.spins.items()}
        self.touch()

    def _dragged(self, _r):
        self.m = self.panel.view.margins(); self._sync_spins(); self.touch()

    def touch(self):
        self.ok.setEnabled(True); self.refresh()

    def refresh(self):
        img, w, h = self.image(self.panel.page())
        v = self.panel.view
        v.set_page(img, w, h); v.set_margins(self.m)
        cw, ch = w - self.m["left"] - self.m["right"], h - self.m["top"] - self.m["bottom"]
        self.panel.caption.setText(f"잘린 페이지 크기: {fmt_size(max(cw, 0), max(ch, 0), self.unit)}")

    def accept(self):
        rot, media = P.page_geom(self.doc.fitz, self.panel.page())
        w, h = P.view_size(media, rot)
        if w - self.m["left"] - self.m["right"] <= 0 or h - self.m["top"] - self.m["bottom"] <= 0:
            _warn(self, "자르기 여백이 페이지보다 큽니다."); return
        super().accept()

    def values(self) -> dict:
        return {"pages": self.rng.pages(), "margins": dict(self.m)}


def _orient_icon(landscape: bool) -> QIcon:
    pm = QPixmap(28, 28); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setPen(QPen(QColor("#495057"), 1.6)); p.setBrush(QColor("white"))
    p.drawRect(QRectF(3, 7, 22, 15) if landscape else QRectF(7, 3, 15, 22)); p.end()
    return QIcon(pm)


# ── 페이지 크기 ───────────────────────────────────────────────
class PageSizeDialog(_Base):
    def __init__(self, parent, doc: Document, pages: list[int], unit: str = "cm"):
        super().__init__(parent, "페이지 크기", doc, unit)
        cur = pages[0] if pages else 0
        row = QHBoxLayout(); row.addWidget(QLabel("현재 페이지 크기"))
        self.cur_lbl = QLabel(); self.cur_lbl.setStyleSheet("font-weight: 600;"); row.addWidget(self.cur_lbl); row.addStretch(1)
        self.left.addLayout(row)
        box = QGroupBox("페이지 크기"); g = QGridLayout(box)
        self.r_fixed = QRadioButton("고정 크기"); self.r_custom = QRadioButton("사용자 지정")
        self.paper = QComboBox()
        for p in PAPERS:
            self.paper.addItem(_paper_text(p))
        self.paper.setCurrentIndex(next(k for k, p in enumerate(PAPERS) if p[0] == "A4"))
        r = doc.fitz[cur].rect
        self.w = UnitBox(self.unit, 9999); self.h = UnitBox(self.unit, 9999)
        self.w.set_pt(r.width); self.h.set_pt(r.height)
        g.addWidget(self.r_fixed, 0, 0); g.addWidget(self.paper, 0, 1, 1, 3)
        g.addWidget(self.r_custom, 1, 0); g.addWidget(self.w, 1, 1); g.addWidget(QLabel("X"), 1, 2); g.addWidget(self.h, 1, 3)
        self.left.addWidget(box)
        ob = QGroupBox("방향"); ol = QHBoxLayout(ob)
        self.orient = QButtonGroup(self)
        for k, (text, land) in enumerate((("세로", False), ("가로", True))):
            b = QToolButton(); b.setText(text); b.setIcon(_orient_icon(land)); b.setIconSize(QSize(28, 28)); b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon); b.setMinimumWidth(64)
            self.orient.addButton(b, k); ol.addWidget(b)
        ol.addStretch(1)
        self.orient.button(0).setChecked(True)
        self.left.addWidget(ob)
        self.fit = QCheckBox("내용을 비율대로 맞추기"); self.fit.setChecked(True); self.left.addWidget(self.fit)
        self.range_box(None)   # 고른 쪽이 넘어와도 모든 페이지
        self.left.addStretch(1)
        self.panel.view.editable = False
        self.panel.set_pages(self.rng.pages())
        if cur in self.panel.pages:
            self.panel.k = self.panel.pages.index(cur); self.panel._show(False)
        self.r_fixed.setChecked(True)
        self.r_fixed.toggled.connect(self._mode); self.paper.currentIndexChanged.connect(lambda *_: self.refresh())
        self.w.valueChanged.connect(lambda *_: self.refresh()); self.h.valueChanged.connect(lambda *_: self.refresh())
        self.orient.idClicked.connect(self._oriented)
        self.fit.toggled.connect(lambda *_: self.refresh())
        self._mode()

    def landscape(self) -> bool:
        return self.orient.checkedId() == 1

    def _mode(self, *_):
        custom = self.r_custom.isChecked()
        self.paper.setEnabled(not custom); self.w.setEnabled(custom); self.h.setEnabled(custom)
        if custom:
            self.orient.button(1 if self.w.pt() > self.h.pt() else 0).setChecked(True)
        self.refresh()

    def _oriented(self, k: int):
        if self.r_custom.isChecked() and (self.w.pt() > self.h.pt()) != (k == 1):
            w, h = self.w.pt(), self.h.pt(); self.w.set_pt(h); self.h.set_pt(w)
        self.refresh()

    def target_size(self) -> tuple[float, float]:
        if self.r_custom.isChecked():
            return self.w.pt(), self.h.pt()
        p = PAPERS[self.paper.currentIndex()]
        a, b = sorted((p[1], p[2]))
        return (b, a) if self.landscape() else (a, b)

    def refresh(self):
        i = self.panel.page()
        r = self.doc.fitz[i].rect
        self.cur_lbl.setText(fmt_size(r.width, r.height, self.unit))
        w, h = self.target_size()
        self.ok.setEnabled(w > 0 and h > 0)
        self.panel.caption.setText(f"조정된 페이지 크기: {fmt_size(w, h, self.unit)}")
        if w <= 0 or h <= 0:
            return
        img, mw, mh = self.image(i)
        m = P.box_margins(self.doc.fitz, i, "crop")
        cw, ch = mw - m["left"] - m["right"], mh - m["top"] - m["bottom"]
        if self.fit.isChecked():
            s = min(w / cw, h / ch); sx = sy = s; ox, oy = (w - cw * s) / 2, (h - ch * s) / 2
        else:
            sx, sy, ox, oy = w / cw, h / ch, 0.0, 0.0
        # 그림은 MediaBox 전체라 보이던 영역(cw×ch)이 판에 맞게 놓이도록 바깥까지 같은 비율로 늘린다
        at = QRectF(ox - m["left"] * sx, oy - m["top"] * sy, mw * sx, mh * sy)
        v = self.panel.view
        v.set_page(img, mw, mh, (w, h), at); v.set_box(QRectF(0, 0, w, h))

    def values(self) -> dict:
        w, h = self.target_size()
        return {"pages": self.rng.pages(), "width": w, "height": h, "fit": self.fit.isChecked()}


# ── 페이지 박스 ───────────────────────────────────────────────
class PageBoxDialog(_Base):
    def __init__(self, parent, doc: Document, pages: list[int], unit: str = "cm"):
        super().__init__(parent, "페이지 박스", doc, unit)
        self.first = pages[0] if pages else 0
        rot, media = P.page_geom(doc.fitz, self.first)
        self.cur_w, self.cur_h = P.view_size(media, rot)
        top = QHBoxLayout(); top.addWidget(QLabel("적용 대상"))
        self.which = QComboBox()
        for key, text in BOXES:
            self.which.addItem(text, key)
        top.addWidget(self.which, 1)
        self.reset_btn = QPushButton("다시 설정"); top.addWidget(self.reset_btn)
        self.left.addLayout(top)
        box, self.spins = margin_grid("자르기 여백", ("top", "bottom", "left", "right"), self.unit, self._typed)
        self.left.addWidget(box)
        sb = QGroupBox("페이지 크기"); g = QGridLayout(sb)
        self.r_fixed = QRadioButton("고정 크기"); self.r_custom = QRadioButton("사용자 지정")
        self.paper = QComboBox(); self.paper.addItem("없음")
        land = self.cur_w > self.cur_h
        for p in PAPERS:
            self.paper.addItem(_paper_text(p))
            w, h = self._oriented(p, land)
            if w + 0.01 < self.cur_w or h + 0.01 < self.cur_h:   # 지금 쪽보다 작은 용지는 못 고름
                self.paper.model().item(self.paper.count() - 1).setEnabled(False)
        self.w = UnitBox(self.unit, 100000); self.h = UnitBox(self.unit, 100000)
        self.w.set_pt(self.cur_w); self.h.set_pt(self.cur_h)
        g.addWidget(self.r_fixed, 0, 0); g.addWidget(self.paper, 0, 1, 1, 3)
        g.addWidget(self.r_custom, 1, 0); g.addWidget(self.w, 1, 1); g.addWidget(QLabel("X"), 1, 2); g.addWidget(self.h, 1, 3)
        self.left.addWidget(sb)
        pb = QGroupBox("위치"); pg = QGridLayout(pb)
        self.r_center = QRadioButton("가운데"); self.r_offset = QRadioButton("오프셋")
        self.ox = UnitBox(self.unit, 0); self.oy = UnitBox(self.unit, 0)
        pg.addWidget(self.r_center, 0, 0, 1, 4); pg.addWidget(self.r_offset, 1, 0)
        pg.addWidget(QLabel("X"), 1, 1); pg.addWidget(self.ox, 1, 2); pg.addWidget(QLabel("Y"), 1, 3); pg.addWidget(self.oy, 1, 4)
        self.left.addWidget(pb)
        self.range_box(pages)
        self.left.addStretch(1)
        self.panel.set_pages(self.rng.pages())
        self.base = {k: P.box_margins(doc.fitz, self.first, k) for k, _ in BOXES}
        self.r_fixed.setChecked(True); self.r_center.setChecked(True)
        self._reset_state()
        self.which.currentIndexChanged.connect(lambda *_: self._show_box())
        self.reset_btn.clicked.connect(self._reset)
        self.r_fixed.toggled.connect(lambda *_: self._sized()); self.paper.currentIndexChanged.connect(lambda *_: self._sized())
        self.w.valueChanged.connect(lambda *_: self._sized()); self.h.valueChanged.connect(lambda *_: self._sized())
        self.r_center.toggled.connect(lambda *_: self._sized())
        self.ox.valueChanged.connect(lambda *_: self._sized()); self.oy.valueChanged.connect(lambda *_: self._sized())
        self.panel.view.changed.connect(self._dragged)
        self._sized(False)

    @staticmethod
    def _oriented(p, land: bool) -> tuple[float, float]:
        a, b = sorted((p[1], p[2]))
        return (b, a) if land else (a, b)

    def _reset_state(self):
        self.touched: set[str] = set()
        self.m = {k: dict(v) for k, v in self.base.items()}
        self.ok.setEnabled(False)

    def _reset(self):
        """모든 박스 여백을 처음 값으로, 자르기 박스로, 크기 없음·가운데로."""
        for w in (self.which, self.paper, self.w, self.h, self.r_fixed, self.r_center, self.ox, self.oy):
            w.blockSignals(True)
        self.which.setCurrentIndex(0); self.paper.setCurrentIndex(0); self.r_fixed.setChecked(True); self.r_center.setChecked(True)
        self.w.set_pt(self.cur_w); self.h.set_pt(self.cur_h); self.ox.set_pt(0); self.oy.set_pt(0)
        for w in (self.which, self.paper, self.w, self.h, self.r_fixed, self.r_center, self.ox, self.oy):
            w.blockSignals(False)
        self._reset_state(); self._sized(False)

    def key(self) -> str:
        return self.which.currentData()

    def new_size(self) -> tuple[float, float] | None:
        """넓힐 크기(보이는 방향 pt). 「없음」이거나 지금보다 크지 않으면 None."""
        if self.r_custom.isChecked():
            w, h = self.w.pt(), self.h.pt()
        elif self.paper.currentIndex() == 0:
            return None
        else:
            w, h = self._oriented(PAPERS[self.paper.currentIndex() - 1], self.cur_w > self.cur_h)
        w, h = max(w, self.cur_w), max(h, self.cur_h)
        return (w, h) if w > self.cur_w + 0.01 or h > self.cur_h + 0.01 else None

    def offset(self) -> tuple[float, float]:
        s = self.new_size() or (self.cur_w, self.cur_h)
        ex, ey = s[0] - self.cur_w, s[1] - self.cur_h
        if self.r_center.isChecked():
            return ex / 2, ey / 2
        return min(self.ox.pt(), ex), min(self.oy.pt(), ey)

    def _sized(self, user: bool = True):
        s = self.new_size() or (self.cur_w, self.cur_h)
        ex, ey = s[0] - self.cur_w, s[1] - self.cur_h
        for sb, e in ((self.ox, ex), (self.oy, ey)):
            sb.blockSignals(True); sb.setMaximum(e / sb.k); sb.blockSignals(False)
        on = self.r_offset.isChecked()
        self.ox.setEnabled(on); self.oy.setEnabled(on); self.paper.setEnabled(self.r_fixed.isChecked())
        self.w.setEnabled(self.r_custom.isChecked()); self.h.setEnabled(self.r_custom.isChecked())
        ox, oy = self.offset()
        # 손대지 않은 박스: 지금 자리 그대로(쪽과 같던 박스는 넓힌 쪽을 따라감), 엔진 set_boxes 와 같은 규칙
        for k, b in self.base.items():
            if k in self.touched:
                continue
            if all(abs(v) < 0.01 for v in b.values()):
                self.m[k] = dict(ZERO)
            else:
                self.m[k] = {"top": b["top"] + oy, "left": b["left"] + ox,
                             "bottom": b["bottom"] + ey - oy, "right": b["right"] + ex - ox}
        if user:
            self.ok.setEnabled(self.can_apply())
        self._show_box()

    def _show_box(self):
        m = self.m[self.key()]
        for side, sb in self.spins.items():
            sb.set_pt(m[side])
        self.refresh()

    def _typed(self, *_):
        self.m[self.key()] = {side: sb.pt() for side, sb in self.spins.items()}
        self.touched.add(self.key()); self.ok.setEnabled(self.can_apply()); self.refresh()

    def _dragged(self, _r):
        self.m[self.key()] = self.panel.view.margins(); self.touched.add(self.key())
        for side, sb in self.spins.items():
            sb.set_pt(self.m[self.key()][side])
        self.ok.setEnabled(self.can_apply()); self.refresh()

    def can_apply(self) -> bool:
        return bool(self.touched or self.new_size())

    def touch(self):
        self.ok.setEnabled(self.can_apply()); self.refresh()

    def refresh(self):
        img, w, h = self.image(self.panel.page())
        s = self.new_size()
        ox, oy = self.offset()
        v = self.panel.view
        if s:
            v.set_page(img, w, h, s, QRectF(ox, oy, w, h))
        else:
            v.set_page(img, w, h)
        m = self.m[self.key()]
        v.set_margins(m)
        sw, sh = s or (w, h)
        self.panel.caption.setText(f"잘린 페이지 크기: "
                                   f"{fmt_size(max(sw - m['left'] - m['right'], 0), max(sh - m['top'] - m['bottom'], 0), self.unit)}")

    def values(self) -> dict:
        s = self.new_size()
        return {"pages": self.rng.pages(), "margins": {k: dict(self.m[k]) for k in self.touched},
                "size": s, "offset": None if s is None or self.r_center.isChecked() else self.offset()}
