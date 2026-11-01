"""구성 탭 대화상자: 회전·페이지 삭제 확인·삽입 장소(빈 페이지·PDF에서)·바꾸기. 값만 모아 돌려주고 일은 window.py 가 엔진에 시킨다."""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox, QVBoxLayout,
                               QWidget)

from ..engine import pages as P
from ..engine.document import Document
from .dialogs import PageRangeEdit, _buttons, ask_password

BAD_RANGE = "유효한 페이지 범위를 입력하십시오."
PREVIEW = 180   # 미리보기 칸 한 변(px)
PDF_FILTER = "PDF 파일 (*.pdf);;모든 파일 (*)"


def _warn(parent, text: str):
    QMessageBox.warning(parent, "PDF메트리", text)


def same_file(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def page_from_text(text: str, labels: list[str]) -> int | None:
    """쪽 칸 글 → 0-based 쪽. 레이블로 먼저 찾고, 맞는 레이블이 없으면 쪽 번호로 읽는다."""
    text = (text or "").strip()
    if not text:
        return None
    if text in labels:
        return labels.index(text)
    if text.isdigit() and 1 <= int(text) <= len(labels):
        return int(text) - 1
    return None


def _thumb(f: fitz.Document, i: int, angle: int = 0) -> QPixmap:
    """쪽 i 를 PREVIEW 칸에 맞게 그리고 angle 만큼(시계 방향) 더 돌린다."""
    page = f[i]
    zoom = PREVIEW / max(page.rect.width, page.rect.height, 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True)
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    if angle % 360:
        img = img.transformed(QTransform().rotate(angle % 360), Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(img)


def _preview_label() -> QLabel:
    lb = QLabel()
    lb.setFixedSize(PREVIEW + 12, PREVIEW + 12)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setStyleSheet("background: #E9ECEF; border: 1px solid #CED4DA;")
    return lb


def _angle_icon(angle: int) -> QIcon:
    """쪽 모양(접힌 귀퉁이 + 글줄)을 angle 만큼 돌린 그림."""
    pm = QPixmap(40, 40); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(20, 20); p.rotate(angle)
    pen = QPen(QColor("#495057")); pen.setWidthF(1.6); p.setPen(pen); p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([QPointF(-10, -14), QPointF(4, -14), QPointF(10, -8), QPointF(10, 14), QPointF(-10, 14)]))
    p.drawPolyline(QPolygonF([QPointF(4, -14), QPointF(4, -8), QPointF(10, -8)]))
    p.setPen(QPen(QColor("#228BE6"), 2))
    for y in (-4, 1, 6):
        p.drawLine(QPointF(-6, y), QPointF(6, y))
    p.end()
    return QIcon(pm)


def _watch(w: QWidget, slot):
    """범위 칸 안의 라디오·글 칸·고르기 칸이 바뀌면 slot 을 부른다(범위 칸 속 구성에 기대지 않게)."""
    from PySide6.QtWidgets import QComboBox
    for b in w.findChildren(QRadioButton):
        b.toggled.connect(slot)
    for e in w.findChildren(QLineEdit):
        e.textChanged.connect(slot)
    for c in w.findChildren(QComboBox):
        c.currentIndexChanged.connect(slot)


# ── 회전 ──────────────────────────────────────────────────────
class RotateDialog(QDialog):
    ANGLES = (0, 90, 180, 270)

    def __init__(self, parent, doc: Document, current: int, pages: list[int]):
        super().__init__(parent)
        self.setWindowTitle("페이지 회전")
        self.doc = doc
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(14)
        left = QVBoxLayout(); lay.addLayout(left)
        box = QGroupBox("방향"); row = QHBoxLayout(box)
        self.group = QButtonGroup(self); self.buttons = []
        for k, a in enumerate(self.ANGLES):
            b = QRadioButton(f"{a}°"); b.setIcon(_angle_icon(a)); b.setIconSize(QSize(32, 32))
            self.group.addButton(b, k); self.buttons.append(b); row.addWidget(b)
        self.buttons[0].setChecked(True)
        left.addWidget(box)
        rbox = QGroupBox("페이지 범위"); rl = QVBoxLayout(rbox)
        self.rng = PageRangeEdit(doc.page_count, current); self.rng.set_pages(pages); rl.addWidget(self.rng)
        left.addWidget(rbox); left.addStretch(1)
        left.addWidget(_buttons(self))
        self.preview = _preview_label(); lay.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        self.group.idToggled.connect(lambda *_: self._update())
        _watch(self.rng, lambda *_: self._update())
        self._update()

    def angle(self) -> int:
        return self.ANGLES[max(0, self.group.checkedId())]

    def _update(self):
        try:
            first = self.rng.pages()[0]
        except ValueError:
            self.preview.clear(); return
        with self.doc.lock:
            self.preview.setPixmap(_thumb(self.doc.fitz, first, self.angle()))

    def accept(self):
        try:
            self.rng.pages()
        except ValueError:
            _warn(self, BAD_RANGE); return
        super().accept()

    def values(self) -> dict:
        return {"pages": self.rng.pages(), "degrees": self.angle()}


# ── 페이지 삭제 ───────────────────────────────────────────────
def confirm_delete(parent, total: int, count: int) -> bool:
    """고른 쪽 count 장을 지워도 되는지 묻는다(한 장도 묻는다). 전부면 경고만 하고 거짓."""
    if count <= 0:
        return False
    if count >= total:
        QMessageBox.information(parent, "페이지 삭제", "모든 페이지를 삭제할 수 없습니다. 하나 이상의 페이지를 남겨주세요.")
        return False
    box = QMessageBox(QMessageBox.Icon.Question, "페이지 삭제", "페이지를 삭제하시겠습니까?", parent=parent)
    yes = box.addButton("삭제", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("아니요", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


# ── 삽입 장소(빈 페이지 · PDF에서) ────────────────────────────
class PlaceDialog(QDialog):
    """끼울 장소: 첫 페이지 · 마지막 페이지 · 페이지 [칸] / 전체, 그리고 앞/뒤. 마지막에 고른 것을 prefs 에 기억한다."""
    KEY = "insert_place"

    def __init__(self, parent, title: str, labels: list[str], current: int, prefs: dict, extra: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self.labels, self.prefs = labels, prefs
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)
        if extra is not None:
            lay.addWidget(extra)
        where = QGroupBox("장소"); g = QGridLayout(where)
        self.r_first = QRadioButton("첫 페이지"); self.r_last = QRadioButton("마지막 페이지"); self.r_page = QRadioButton("페이지")
        self.page = QLineEdit(labels[current] if 0 <= current < len(labels) else "1"); self.page.setFixedWidth(70)
        self.total_lbl = QLabel(f"/ {len(labels)}")
        g.addWidget(self.r_first, 0, 0, 1, 3); g.addWidget(self.r_last, 1, 0, 1, 3)
        g.addWidget(self.r_page, 2, 0); g.addWidget(self.page, 2, 1); g.addWidget(self.total_lbl, 2, 2)
        g.setColumnStretch(3, 1)
        lay.addWidget(where)
        pos = QGroupBox("위치"); pl = QVBoxLayout(pos)
        self.r_before = QRadioButton("페이지 앞에 삽입"); self.r_after = QRadioButton("페이지 뒤에 삽입")
        pl.addWidget(self.r_before); pl.addWidget(self.r_after)
        lay.addWidget(pos)
        lay.addWidget(_buttons(self))
        last = prefs.get(self.KEY) or {}
        {"first": self.r_first, "last": self.r_last}.get(last.get("at"), self.r_page).setChecked(True)
        (self.r_before if last.get("before") else self.r_after).setChecked(True)
        self.r_page.toggled.connect(self.page.setEnabled); self.page.setEnabled(self.r_page.isChecked())

    def place(self) -> str:
        return "first" if self.r_first.isChecked() else "last" if self.r_last.isChecked() else "page"

    def check(self) -> bool:
        if self.place() == "page" and page_from_text(self.page.text(), self.labels) is None:
            _warn(self, BAD_RANGE); self.page.setFocus(); self.page.selectAll()
            return False
        return True

    def at(self) -> int:
        """끼울 자리(0-based)."""
        pg = page_from_text(self.page.text(), self.labels) if self.place() == "page" else 0
        return P.insert_index(self.place(), pg or 0, self.r_before.isChecked(), len(self.labels))

    def remember(self):
        self.prefs[self.KEY] = {"at": self.place(), "before": self.r_before.isChecked()}

    def accept(self):
        if self.check():
            self.remember()
            super().accept()


def _open_pdf(parent, p: str, xfa_ok: bool = True) -> tuple[str | None, int] | None:
    """파일을 열어 (암호, 쪽 수). 암호는 맞을 때까지 묻는다. 못 열거나 암호를 취소하면 None."""
    try:
        src = fitz.open(p)
    except Exception as e:  # noqa: BLE001
        _warn(parent, f"「{os.path.basename(p)}」 을 열 수 없습니다: {e}"); return None
    pw = None
    while src.needs_pass:
        pw = ask_password(parent, os.path.basename(p))
        if pw is None:
            src.close(); return None
        if not src.authenticate(pw):
            _warn(parent, "암호가 맞지 않습니다")
    if not xfa_ok and P.is_xfa(src):
        _warn(parent, "현재 기능으로 XFA 파일을 지원하지 않습니다."); src.close(); return None
    n = src.page_count if src.is_pdf else fitz.open("pdf", src.convert_to_pdf()).page_count
    src.close()
    return pw, n


def source_range_row(total: int) -> tuple[QWidget, QLineEdit]:
    """PDF에서 삽입: 파일이 하나일 때 가져올 쪽 칸(비우면 전부)."""
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    e = QLineEdit(); e.setPlaceholderText("비우면 전부")
    h.addWidget(QLabel("가져올 쪽")); h.addWidget(e, 1); h.addWidget(QLabel(f"/ {total}"))
    return w, e


def open_sources(parent, paths: list[str], own_path: str | None) -> list[tuple[str, str | None, int]] | None:
    """PDF에서 삽입할 파일들을 열어 본다: 자기 자신이면 경고하고 None, 암호는 묻고, 취소·못 연 파일·XFA 는 뺀다.
    돌려주는 값: (경로, 암호, 쪽 수) 목록."""
    if any(same_file(p, own_path) for p in paths):
        _warn(parent, "자신에게 삽입할 수 없습니다. 삽입할 다른 문서를 선택하십시오.")
        return None
    out = []
    for p in paths:
        got = _open_pdf(parent, p, xfa_ok=False)
        if got:
            out.append((p, *got))
    return out


# ── 바꾸기 ────────────────────────────────────────────────────
class _ClickLabel(QLabel):
    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        self._on_click()


class ReplaceDialog(QDialog):
    def __init__(self, parent, doc: Document, pages: list[int]):
        super().__init__(parent)
        self.setWindowTitle("바꾸기")
        self.doc = doc
        self.path: str | None = None
        self.password: str | None = None
        self.src_total = 0
        n = doc.page_count
        pages = sorted(set(pages)) or [0]
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)
        row = QHBoxLayout(); lay.addLayout(row)

        tgt = QGroupBox("교체할 페이지 선택"); tl = QVBoxLayout(tgt)
        self.t_from, self.t_to = self._spin(n, pages[0] + 1), self._spin(n, pages[-1] + 1)
        tl.addLayout(self._range_row(self.t_from, self.t_to, QLabel(f"/ {n}")))
        tl.addWidget(QLabel(f"파일  {doc.name}"))
        self.t_prev = _preview_label(); tl.addWidget(self.t_prev, 0, Qt.AlignmentFlag.AlignHCenter)
        row.addWidget(tgt)

        src = QGroupBox("교체 페이지 선택"); sl = QVBoxLayout(src)
        fr = QHBoxLayout(); fr.addWidget(QLabel("파일"))
        self.file = QLineEdit(); self.file.setReadOnly(True); self.file.setPlaceholderText("선택된 파일이 없습니다")
        browse = QPushButton("찾아보기"); browse.clicked.connect(self.browse)
        fr.addWidget(self.file, 1); fr.addWidget(browse); sl.addLayout(fr)
        self.s_from, self.s_to = self._spin(1, 1), self._spin(1, 1)
        self.s_total = QLabel("/ 0")
        sl.addLayout(self._range_row(self.s_from, self.s_to, self.s_total))
        self.s_prev = _ClickLabel(self.browse)
        self.s_prev.setFixedSize(PREVIEW + 12, PREVIEW + 12); self.s_prev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.s_prev.setStyleSheet("background: #E9ECEF; border: 1px solid #CED4DA;")
        self.s_prev.setText("찾아보기")
        sl.addWidget(self.s_prev, 0, Qt.AlignmentFlag.AlignHCenter)
        row.addWidget(src)
        for w in (self.s_from, self.s_to):
            w.setEnabled(False)

        self.keep = QCheckBox("교체된 페이지에 댓글 유지"); lay.addWidget(self.keep)
        lay.addWidget(_buttons(self))
        self.t_from.valueChanged.connect(self._t_preview)
        self.s_from.valueChanged.connect(self._s_preview)
        self._t_preview()

    @staticmethod
    def _spin(top: int, value: int) -> QSpinBox:
        s = QSpinBox(); s.setRange(1, max(1, top)); s.setValue(min(max(1, value), max(1, top)))
        return s

    @staticmethod
    def _range_row(a: QSpinBox, b: QSpinBox, total: QLabel) -> QHBoxLayout:
        h = QHBoxLayout(); h.addWidget(a); h.addWidget(QLabel("부터")); h.addWidget(b); h.addWidget(total); h.addStretch(1)
        return h

    def _t_preview(self):
        with self.doc.lock:
            self.t_prev.setPixmap(_thumb(self.doc.fitz, self.t_from.value() - 1))

    def _s_preview(self):
        if not self.path:
            return
        try:
            src = P.open_source(self.path, self.password)
        except Exception:  # noqa: BLE001
            return
        self.s_prev.setPixmap(_thumb(src, self.s_from.value() - 1))
        src.close()

    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "교체 페이지가 든 PDF", os.path.dirname(self.doc.path or ""), PDF_FILTER)
        if p:
            self.set_source(p)

    def set_source(self, p: str) -> bool:
        """원본 파일을 정한다. 자기 자신·못 여는 파일·암호 취소면 거짓."""
        if same_file(p, self.doc.path):
            _warn(self, "문서는 자신으로 대체 할 수 없습니다. 다른 문서를 선택하십시오."); return False
        got = _open_pdf(self, p)
        if not got:
            return False
        pw, n = got
        self.path, self.password, self.src_total = p, pw, n
        self.file.setText(os.path.basename(p))
        self.s_total.setText(f"/ {n}")
        for w in (self.s_from, self.s_to):
            w.blockSignals(True); w.setRange(1, max(1, n)); w.setValue(1); w.setEnabled(True); w.blockSignals(False)
        self._s_preview()
        return True

    def check(self) -> bool:
        if self.t_from.value() > self.t_to.value():
            _warn(self, BAD_RANGE); self.t_from.setFocus(); return False
        if not self.path:
            _warn(self, "파일이 선택되지 않았습니다."); return False
        if self.s_from.value() > self.s_to.value():
            _warn(self, BAD_RANGE); self.s_from.setFocus(); return False
        return True

    def accept(self):
        if self.check():
            super().accept()

    def values(self) -> dict:
        return {"pages": list(range(self.t_from.value() - 1, self.t_to.value())), "path": self.path,
                "password": self.password, "src_pages": list(range(self.s_from.value() - 1, self.s_to.value())),
                "keep_annots": self.keep.isChecked()}
