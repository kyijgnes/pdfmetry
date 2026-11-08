"""꾸미기 다섯 가지(워터마크·배경·머리글/바닥글·페이지 번호·베이츠)의 공용 창 틀과 흐름.

창 하나 = DecoDialog 하위 클래스: KIND, build()(왼쪽 묶음 채우기), settings()(엔진 설정, 쪽 범위 빼고),
load(settings, pages), can_apply(). 칸이 바뀌면 self.changed() 한 번만 부르면 틀이 「적용」 켜고 끄기와
미리보기 다시 그리기를 맡는다. 미리보기는 엔진 render_preview 하나로 그려 실제 결과와 늘 같다.
길이 칸은 환경 설정의 단위(prefs["unit"], 기본 cm)로 보이고 settings() 에는 pt 로 낸다.
「템플릿에 추가」 를 켜고 적용하면 지금 설정과 쪽 범위를 그 종류의 템플릿으로 저장한다(engine.deco_store).
"""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDoubleValidator, QFont, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSlider, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from ..engine import deco as E
from ..engine import deco_store as S
from ..engine import fonts as F
from ..engine import pages as P
from ..engine.document import Document
from . import fitwidth
from .dialogs import ColorButton, PageRangeEdit, _buttons
from .dlg_pagebox import BAD_RANGE, UNITS, UnitBox, _watch

FILE_NOT_EXIST = "이 파일이 존재하지 않습니다."
FILE_DAMAGED = "파일을 로드하지 못했습니다. 파일이 손상되었는지 확인하세요."
FONT_SIZES = (5, 5.5, 6.5, 7.5, 8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")
PREVIEW_PX = 360   # 미리보기 그림의 긴 변


def _num(v: float) -> str:
    return f"{v:g}"


# ── 칸 묶음 ───────────────────────────────────────────────────
class FontRow(QWidget):
    """글꼴 · 크기(목록 또는 1~1638 직접) · 굵게 · 기울임 · 밑줄 · 색."""
    changed = Signal()

    def __init__(self, size: float = 11, color=(0, 0, 0), parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        top = QHBoxLayout(); top.setSpacing(6)
        self.family = QComboBox(); self.family.addItems(F.font_families()); self.family.setMinimumWidth(170)
        self.family.setMaxVisibleItems(16)
        self.size = QComboBox(); self.size.setEditable(True)
        self.size.addItems([_num(v) for v in FONT_SIZES])
        fitwidth.fit(self.size, fitwidth.combo_texts(self.size) + ["1638"], floor=64)   # 1638 = 받는 가장 큰 값
        val = QDoubleValidator(1, 1638, 1, self.size); val.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.size.setValidator(val)
        top.addWidget(self.family, 1); top.addWidget(self.size)
        low = QHBoxLayout(); low.setSpacing(4)
        self.bold, self.italic, self.underline = (self._toggle(t, k) for t, k in (("B", "bold"), ("I", "italic"), ("U", "underline")))
        self.color = ColorButton(color)
        for w in (self.bold, self.italic, self.underline):
            low.addWidget(w)
        low.addSpacing(6); low.addWidget(self.color); low.addStretch(1)
        lay.addLayout(top); lay.addLayout(low)
        self.set_value({"size": size, "color": color})
        self.family.currentIndexChanged.connect(self.changed)
        self.size.currentTextChanged.connect(self.changed)
        self.color.clicked.connect(self.changed)   # 색 고르기 창이 닫힌 뒤(먼저 걸린 pick 다음에 불린다)

    def _toggle(self, text: str, style: str) -> QToolButton:
        b = QToolButton(); b.setObjectName("orient"); b.setText(text); b.setCheckable(True); b.setFixedSize(28, 24)
        f = QFont(b.font()); f.setBold(style == "bold"); f.setItalic(style == "italic"); f.setUnderline(style == "underline")
        b.setFont(f)
        b.toggled.connect(self.changed)
        return b

    def value(self) -> dict:
        try:
            size = min(max(float(self.size.currentText()), 1), 1638)
        except ValueError:
            size = 11.0
        name = self.family.currentText()
        return {"name": "" if name == F.DEFAULT_FAMILY else name, "size": size, "color": tuple(self.color.color or (0, 0, 0)),
                "bold": self.bold.isChecked(), "italic": self.italic.isChecked(), "underline": self.underline.isChecked()}

    def set_value(self, f: dict):
        f = E.norm_font(f)
        for w in (self.family, self.size, self.bold, self.italic, self.underline):
            w.blockSignals(True)
        k = self.family.findText(f["name"] or F.DEFAULT_FAMILY)
        self.family.setCurrentIndex(max(k, 0))
        self.size.setCurrentText(_num(f["size"]))
        self.bold.setChecked(f["bold"]); self.italic.setChecked(f["italic"]); self.underline.setChecked(f["underline"])
        self.color.color = tuple(f["color"]); self.color._paint()
        for w in (self.family, self.size, self.bold, self.italic, self.underline):
            w.blockSignals(False)


class PositionGrid(QWidget):
    """3×3 자리 단추. cells=6 이면 가운데 줄을 끈다(머리글/바닥글·페이지 번호)."""
    changed = Signal()
    MARKS = ("◤", "▲", "◥", "◀", "●", "▶", "◣", "▼", "◢")

    def __init__(self, cells: int = 9, value: str = "center", parent=None):
        super().__init__(parent)
        g = QGridLayout(self); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(2)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self.btns: dict[str, QToolButton] = {}
        for k, (cell, mark) in enumerate(zip(E.CELLS9, self.MARKS)):
            b = QToolButton(); b.setObjectName("orient"); b.setText(mark); b.setCheckable(True); b.setFixedSize(28, 24)
            b.setEnabled(cells == 9 or cell in E.CELLS6)
            self.group.addButton(b); g.addWidget(b, k // 3, k % 3); self.btns[cell] = b
        self.set_value(value)
        self.group.buttonToggled.connect(lambda _b, on: on and self.changed.emit())

    def value(self) -> str:
        return next((c for c, b in self.btns.items() if b.isChecked()), "center")

    def set_value(self, cell: str):
        b = self.btns.get(cell) or self.btns["center"]
        self.group.blockSignals(True); b.setChecked(True); self.group.blockSignals(False)


class LookRow(QWidget):
    """회전(-45° · 0° · 45° 빠른 단추 + -360~360) · 불투명도(밀대 0~100 과 % 칸이 같이 움직임)."""
    changed = Signal()

    def __init__(self, rotate: float = 0, opacity: float = 1.0, parent=None):
        super().__init__(parent)
        g = QGridLayout(self); g.setContentsMargins(0, 0, 0, 0); g.setHorizontalSpacing(6)
        row = QHBoxLayout(); row.setSpacing(4)
        for deg in (-45, 0, 45):
            b = QPushButton(f"{deg}°"); b.setFixedWidth(48); b.clicked.connect(lambda _, v=deg: self.rotate.setValue(v))
            row.addWidget(b)
        self.rotate = QSpinBox(); self.rotate.setRange(-360, 360); self.rotate.setSuffix("°")
        row.addWidget(self.rotate); row.addStretch(1)
        g.addWidget(QLabel("회전"), 0, 0); g.addLayout(row, 0, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setRange(0, 100)
        self.opacity = QSpinBox(); self.opacity.setRange(0, 100); self.opacity.setSuffix(" %")
        self.slider.valueChanged.connect(self.opacity.setValue); self.opacity.valueChanged.connect(self.slider.setValue)
        orow = QHBoxLayout(); orow.addWidget(self.slider, 1); orow.addWidget(self.opacity)
        g.addWidget(QLabel("불투명도"), 1, 0); g.addLayout(orow, 1, 1)
        self.set_value(rotate, opacity)
        self.rotate.valueChanged.connect(self.changed); self.opacity.valueChanged.connect(self.changed)

    def value(self) -> tuple[float, float]:
        return float(self.rotate.value()), self.opacity.value() / 100

    def set_value(self, rotate: float, opacity: float):
        self.rotate.setValue(int(round(rotate))); self.opacity.setValue(int(round(opacity * 100)))


class ScaleRow(QWidget):
    """「페이지 비율」(쪽에 맞춘 크기의 %) / 「최대 척도」(원래 크기의 %), 최대 1600."""
    changed = Signal()

    def __init__(self, mode: str = "page", page_pct: float = 50, max_pct: float = 100, parent=None):
        super().__init__(parent)
        g = QGridLayout(self); g.setContentsMargins(0, 0, 0, 0)
        self.page = QRadioButton("페이지 비율"); self.max = QRadioButton("최대 척도")
        self.page_pct, self.max_pct = QSpinBox(), QSpinBox()
        for k, (r, sb, v) in enumerate(((self.page, self.page_pct, page_pct), (self.max, self.max_pct, max_pct))):
            sb.setRange(1, 1600); sb.setSuffix(" %"); sb.setValue(int(v))
            g.addWidget(r, k, 0); g.addWidget(sb, k, 1)
            sb.valueChanged.connect(self.changed)
        (self.page if mode == "page" else self.max).setChecked(True)
        self._sync()
        self.page.toggled.connect(self._sync)

    def _sync(self):
        self.page_pct.setEnabled(self.page.isChecked()); self.max_pct.setEnabled(not self.page.isChecked())
        self.changed.emit()

    def value(self) -> tuple[str, float]:
        if self.page.isChecked():
            return "page", float(self.page_pct.value())
        return "max", float(self.max_pct.value())

    def set_value(self, mode: str, pct: float):
        (self.page if mode == "page" else self.max).setChecked(True)
        (self.page_pct if mode == "page" else self.max_pct).setValue(int(round(pct)))


class FileRow(QWidget):
    """파일 경로 칸 + 찾아보기 + PDF 면 「페이지」(쓸 쪽)."""
    changed = Signal()

    def __init__(self, pdf: bool = True, parent=None):
        super().__init__(parent)
        self.pdf = pdf
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        row = QHBoxLayout()
        self.path = QLineEdit(); self.path.setPlaceholderText("파일 경로")
        b = QPushButton("찾아보기"); b.clicked.connect(self._pick)
        row.addWidget(self.path, 1); row.addWidget(b)
        self.page_row = QWidget(); pr = QHBoxLayout(self.page_row); pr.setContentsMargins(0, 0, 0, 0)
        self.page = QSpinBox(); self.page.setRange(1, 1); self.of = QLabel("/ 1")
        pr.addWidget(QLabel("페이지")); pr.addWidget(self.page); pr.addWidget(self.of); pr.addStretch(1)
        lay.addLayout(row); lay.addWidget(self.page_row)
        self.page_row.hide()
        self.path.textChanged.connect(self._path_changed); self.page.valueChanged.connect(self.changed)

    def _pick(self):
        exts = IMAGE_EXT + ((".pdf",) if self.pdf else ())
        flt = f"모든 파일 지원 ({' '.join('*' + e for e in exts)})"
        p, _ = QFileDialog.getOpenFileName(self, "파일 열기", os.path.dirname(self.path.text()), flt)
        if p:
            self.path.setText(os.path.normpath(p))

    def is_pdf(self) -> bool:
        return self.path.text().strip().lower().endswith(".pdf")

    def _path_changed(self):
        n = 1
        if self.is_pdf() and os.path.isfile(self.path.text().strip()):
            try:
                with fitz.open(self.path.text().strip()) as d:
                    n = max(d.page_count, 1)
            except Exception:  # noqa: BLE001
                n = 1
        self.page.setMaximum(n); self.of.setText(f"/ {n}")
        self.page_row.setVisible(self.is_pdf())
        self.changed.emit()

    def value(self) -> tuple[str, int]:
        return self.path.text().strip(), self.page.value() - 1

    def set_value(self, path: str | None, page: int = 0):
        self.path.setText(path or ""); self.page.setValue(int(page) + 1)

    def check(self) -> str | None:
        """적용 직전 확인: 오류 문구 또는 None."""
        path = self.value()[0]
        if not os.path.isfile(path):
            return FILE_NOT_EXIST
        try:
            if self.is_pdf():
                with fitz.open(path) as d:
                    if d.page_count < 1:
                        return FILE_DAMAGED
            else:
                from PIL import Image
                with Image.open(path) as im:
                    im.verify()
        except Exception:  # noqa: BLE001
            return FILE_DAMAGED
        return None


class MarginGrid(QWidget):
    """여백 넷(위쪽 · 아래쪽 · 왼쪽 · 오른쪽), 단위 칸(-1000~1000). 값은 pt."""
    changed = Signal()
    NAMES = (("top", "위쪽"), ("bottom", "아래쪽"), ("left", "왼쪽"), ("right", "오른쪽"))

    def __init__(self, unit: str, margins: dict | None = None, parent=None):
        super().__init__(parent)
        g = QGridLayout(self); g.setContentsMargins(0, 0, 0, 0)
        self.spins: dict[str, UnitBox] = {}
        for k, (side, label) in enumerate(self.NAMES):
            sb = UnitBox(unit, 1000); sb.setMinimum(-1000)
            g.addWidget(QLabel(label), k // 2, (k % 2) * 2); g.addWidget(sb, k // 2, (k % 2) * 2 + 1)
            self.spins[side] = sb
            sb.valueChanged.connect(self.changed)
        self.set_value(margins or E.ZERO_MARGINS)

    def value(self) -> dict:
        return {side: sb.pt() for side, sb in self.spins.items()}

    def set_value(self, m: dict):
        for side, sb in self.spins.items():
            sb.set_pt(float((m or {}).get(side, 0)))


# ── 미리보기 ──────────────────────────────────────────────────
class DecoPreview(QWidget):
    """쪽 미리보기 + 범위 안 쪽 넘기기."""
    pageChanged = Signal(int)

    def __init__(self, total: int, parent=None):
        super().__init__(parent)
        self.total, self.pages, self.k = total, [0], 0
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self.view = QLabel(); self.view.setFixedSize(QSize(PREVIEW_PX, PREVIEW_PX))
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter); self.view.setObjectName("decopreview")
        self.view.setStyleSheet("QLabel#decopreview{background:#E4E6EA; border-radius:4px;}")
        lay.addWidget(self.view)
        row = QHBoxLayout()
        self.prev = QToolButton(); self.prev.setText("◀"); self.next = QToolButton(); self.next.setText("▶")
        self.where = QLabel(); self.where.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1); row.addWidget(self.prev); row.addWidget(self.where); row.addWidget(self.next); row.addStretch(1)
        lay.addLayout(row); lay.addStretch(1)
        self.prev.clicked.connect(lambda: self._go(-1)); self.next.clicked.connect(lambda: self._go(1))

    def page(self) -> int:
        return self.pages[self.k]

    def set_pages(self, pages: list[int], current: int | None = None):
        keep = self.page() if current is None else current
        self.pages = sorted(set(pages)) or [keep]
        self.k = self.pages.index(keep) if keep in self.pages else 0
        self._show()

    def _go(self, step: int):
        self.k = min(max(self.k + step, 0), len(self.pages) - 1); self._show(); self.pageChanged.emit(self.page())

    def _show(self):
        self.where.setText(f"{self.page() + 1} / {self.total}")
        self.prev.setEnabled(self.k > 0); self.next.setEnabled(self.k < len(self.pages) - 1)

    def set_image(self, pix: fitz.Pixmap):
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888).copy()
        self.view.setPixmap(QPixmap.fromImage(img))


# ── 창 틀 ─────────────────────────────────────────────────────
def _warn(parent, text: str):
    QMessageBox.warning(parent, "PDF메트리", text)


def range_of(rng: PageRangeEdit) -> dict:
    """범위 칸 → 템플릿에 적는 범위(다른 문서에도 맞게 쪽 목록이 아니라 고른 꼴 그대로)."""
    return S.norm_range({"how": rng.which.currentData() or "all",
                         "custom": rng.edit.text() if rng.rng.isChecked() else None})


def set_range(rng: PageRangeEdit, r: dict):
    """템플릿 범위로 범위 칸을 채운다."""
    r = S.norm_range(r)
    rng.which.setCurrentIndex(max(rng.which.findData(r["how"]), 0))
    if r["custom"]:
        rng._custom(r["custom"])
    else:
        rng.all.setChecked(True)


class DecoDialog(QDialog):
    """왼쪽 설정 묶음 · 오른쪽 미리보기 · 아래 「템플릿에 추가」 · 「적용」 · 「취소」."""
    KIND = ""

    def __init__(self, parent, doc: Document, current: int = 0, update: bool = False, unit: str = "cm",
                 hide: tuple[str, ...] = ()):
        super().__init__(parent)
        name = E.DECO_KINDS[self.KIND]
        self.setWindowTitle(f"{name} 업데이트" if update else name)
        self.doc, self.unit, self.hide = doc, unit if unit in UNITS else "cm", tuple(hide)
        self._ready = False
        outer = QVBoxLayout(self); outer.setContentsMargins(16, 14, 16, 12); outer.setSpacing(10)
        body = QHBoxLayout(); body.setSpacing(16); outer.addLayout(body)
        self.left = QVBoxLayout(); self.left.setSpacing(10); body.addLayout(self.left)
        self.preview = DecoPreview(doc.page_count); body.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        self.build()
        box = QGroupBox("페이지 범위"); bl = QVBoxLayout(box)
        self.rng = PageRangeEdit(doc.page_count, current); bl.addWidget(self.rng)
        self.left.addWidget(box); self.left.addStretch(1)
        _watch(self.rng, lambda *_: self.changed())
        bottom = QHBoxLayout()
        self.to_template = QCheckBox("템플릿에 추가")
        self.tpl_name = QLineEdit(); self.tpl_name.setPlaceholderText("템플릿 이름"); self.tpl_name.setMaxLength(64)
        self.tpl_name.setFixedWidth(180); self.tpl_name.setEnabled(False)
        self.to_template.toggled.connect(self.tpl_name.setEnabled)
        self.tpl_id: str | None = None   # 템플릿 창에서 고치는 템플릿
        self.saved: dict | None = None   # 적용하며 저장한 템플릿
        self.bb = _buttons(self, "적용"); self.ok = self.bb.button(QDialogButtonBox.StandardButton.Ok)
        bottom.addWidget(self.to_template); bottom.addWidget(self.tpl_name); bottom.addStretch(1); bottom.addWidget(self.bb)
        outer.addLayout(bottom)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        self.preview.pageChanged.connect(lambda _: self.refresh())
        self.preview.set_pages(list(range(doc.page_count)), current)
        self._ready = True
        self.changed()

    # 하위 클래스가 채우는 것
    def build(self):
        raise NotImplementedError

    def settings(self) -> dict:
        raise NotImplementedError

    def can_apply(self) -> bool:
        return True

    def check(self) -> str | None:
        """적용 직전 확인(파일 오류 등). 오류 문구 또는 None."""
        return None

    # 틀
    def group(self, title: str) -> QVBoxLayout:
        box = QGroupBox(title); lay = QVBoxLayout(box); lay.setSpacing(8)
        self.left.addWidget(box)
        return lay

    @staticmethod
    def row(label: str, *widgets) -> QHBoxLayout:
        r = QHBoxLayout(); r.setSpacing(6)
        if label:
            lb = QLabel(label); lb.setMinimumWidth(64); r.addWidget(lb)
        for w in widgets:
            r.addWidget(w)
        r.addStretch(1)
        return r

    def watch(self, *widgets):
        """칸이 바뀌면 changed()."""
        for w in widgets:
            sig = getattr(w, "changed", None)
            if isinstance(w, (FontRow, PositionGrid, LookRow, ScaleRow, FileRow, MarginGrid)):
                sig.connect(self.changed)
            elif isinstance(w, (QLineEdit, QPlainTextEdit)):
                w.textChanged.connect(self.changed)
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.changed)
            elif isinstance(w, QAbstractSpinBox):
                w.valueChanged.connect(self.changed)
            elif isinstance(w, QAbstractButton):
                (w.toggled if w.isCheckable() else w.clicked).connect(self.changed)

    def changed(self, *_):
        if not self._ready:
            return
        self.ok.setEnabled(self.can_apply())
        self._timer.start()

    def pages(self) -> list[int]:
        return self.rng.pages()

    def values(self) -> dict:
        """엔진 add_decoration 설정 그대로 + pages."""
        return {**self.settings(), "pages": self.pages()}

    def load(self, settings: dict, pages: list[int]):
        """문서에 적어 둔 설정으로 채운다(업데이트). 하위 클래스는 칸을 채운 뒤 이것을 부른다."""
        self.rng.set_pages(pages)
        self.preview.set_pages(pages)
        self.changed()

    def refresh(self):
        try:
            pages = self.pages()
        except ValueError:
            pages = None
        if pages is not None:
            self.preview.set_pages(pages)
        try:
            s = self.settings()
        except Exception:  # noqa: BLE001  칸을 채우는 중
            s = None
        pno = self.preview.page()
        with self.doc.lock:
            if s is None or not pages:
                pix = E.render_preview(self.doc.fitz, pno, self.KIND, {"v": E.SETTINGS_VERSION}, [], PREVIEW_PX, self.hide)
            else:
                pix = E.render_preview(self.doc.fitz, pno, self.KIND, s, pages, PREVIEW_PX, self.hide)
        self.preview.set_image(pix)

    def accept(self):
        try:
            self.pages()
        except ValueError:
            _warn(self, BAD_RANGE); self.rng.focus_error(); return
        err = self.check()
        if err:
            _warn(self, err); return
        if self.to_template.isChecked() and not self._save_template():
            return
        super().accept()

    # 템플릿
    def as_template(self, t: dict | None = None):
        """템플릿 창의 「+」·고치기: 적용하면 넣지 않고 템플릿으로만 저장한다. t 는 고칠 템플릿."""
        self.to_template.setChecked(True); self.to_template.hide()
        self.setWindowTitle(f"{E.DECO_KINDS[self.KIND]} 템플릿")
        self.ok.setText("저장")
        if t:
            self.tpl_id = t["id"]; self.tpl_name.setText(t.get("name") or "")
            set_range(self.rng, t.get("range"))

    def no_template(self):
        """문서 안의 것을 고칠 때(템플릿 창의 편집 모드): 「템플릿에 추가」 를 뺀다."""
        self.to_template.setChecked(False); self.to_template.hide(); self.tpl_name.hide()

    def _save_template(self) -> bool:
        s = self.settings()
        name = self.tpl_name.text().strip() or S.summary(self.KIND, s)
        tid = self.tpl_id
        same = S.find_name(self.KIND, name)
        if same and same["id"] != tid:
            if QMessageBox.question(self, E.DECO_KINDS[self.KIND],
                                    f'이름 "{name}"이(가) 이미 존재합니다. 내용을 바꾸시겠습니까?') != QMessageBox.StandardButton.Yes:
                self.tpl_name.setFocus(); self.tpl_name.selectAll(); return False
        try:
            self.saved = S.save(self.KIND, name, s, range_of(self.rng), same["id"] if same else tid)
        except OSError:
            _warn(self, FILE_NOT_EXIST); return False
        if same and tid and same["id"] != tid:   # 고치던 것을 같은 이름의 것에 덮었으니 고치던 것은 지운다
            S.remove(self.KIND, tid)
        return True


# ── 흐름: 추가 · 템플릿에서 추가 · 업데이트 · 삭제 ────────────────
def dialog_class(kind: str):
    from . import dlg_deco_mark as M
    from . import dlg_deco_text as X
    return {"watermark": M.WatermarkDialog, "background": M.BackgroundDialog, "header_footer": X.HeaderFooterDialog,
            "page_number": X.PageNumberDialog, "bates": X.BatesDialog}[kind]


def related_kinds(kind: str) -> tuple[str, ...]:
    """함께 세고 지우는 종류. 페이지 번호는 머리글/바닥글의 한 갈래라, 머리글/바닥글은 둘 다 본다."""
    return ("header_footer", "page_number") if kind == "header_footer" else (kind,)


def describe(g: dict) -> str:
    """업데이트 목록 한 줄: 「1-3쪽 · 대외비」."""
    return f"{P.format_range(g['pages'])}쪽 · {E.DECO_KINDS[g['kind']]} · {S.summary(g['kind'], g['settings'])}"


def run_decorate(win, t, kind: str, how: str):
    """꾸미기 추가(add)·템플릿에서 추가(template)·업데이트(update)·삭제(remove). 한 번의 적용이 실행 취소 한 단계."""
    d = t.doc
    name = E.DECO_KINDS[kind]
    kinds = related_kinds(kind)
    groups = [g for k in kinds for g in E.decorations(d.fitz, k)]
    if how == "remove":
        if not groups:
            QMessageBox.information(win, name, f"이 문서에는 {name}이(가) 없습니다."); return
        if QMessageBox.question(win, name, f"모든 {name}을(를) 제거하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        d.snapshot()
        for k in kinds:
            E.remove_decorations(d.fitz, k)
        t.changed(None); return
    pick = None
    if how in ("add", "template") and groups:
        box = QMessageBox(QMessageBox.Icon.Question, name,
                          f"이 파일에 {name}을(를) 이미 있습니다. 새로운 것을 추가하시겠습니까?", parent=win)
        yes = box.addButton("예", QMessageBox.ButtonRole.YesRole)
        box.addButton("아니요", QMessageBox.ButtonRole.NoRole)
        edit = box.addButton("기존 편집", QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is edit:
            how = "update"
        elif box.clickedButton() is not yes:
            return
    if how == "update":
        if not groups:
            QMessageBox.information(win, name, f"이 문서에는 {name}이(가) 없습니다."); return
        pick = 0
    unit = getattr(win, "prefs", {}).get("unit", "cm")
    if how == "template" or (how == "update" and len(groups) > 1):
        from .dlg_deco_template import TemplateDialog
        dlg = TemplateDialog(win, d, kind, t.viewer.current, unit=unit, groups=groups if how == "update" else None)
        if not dlg.exec():
            return
        d.snapshot()
        if how == "template":
            stem = os.path.splitext(d.name)[0]
            for s, pages in dlg.chosen():
                E.add_decoration(d.fitz, kind, pages=pages, file_name=stem, **s)
        else:   # 그 종류를 전부 지운 뒤 고친 목록을 다시 넣는다
            left = dlg.groups
            for k in kinds:
                E.replace_decorations(d.fitz, k, [g for g in left if g["kind"] == k])
        t.changed(None); return
    stem = os.path.splitext(d.name)[0]
    dlg = dialog_class(kind)(win, d, t.viewer.current, update=pick is not None, unit=unit,
                             hide=kinds if pick is not None else ())
    if pick is not None:
        g = groups[pick]
        s = g["settings"]
        if g["kind"] == "page_number" and kind == "header_footer":
            from ..engine.deco_text import page_number_as_header
            s = page_number_as_header(s)
        dlg.load(E.upgrade_settings(kind, s, stem), g["pages"])
    if not dlg.exec():
        return
    vals = dlg.values()
    pages = vals.pop("pages")
    d.snapshot()
    if pick is None:
        E.add_decoration(d.fitz, kind, pages=pages, file_name=stem, **vals)
    else:
        groups[pick] = {"kind": kind, "settings": vals, "pages": pages}
        for k in kinds:
            E.replace_decorations(d.fitz, k, [g for g in groups if g["kind"] == k])
    t.changed(None)
