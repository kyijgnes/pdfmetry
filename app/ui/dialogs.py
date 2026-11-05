"""대화상자 모음. 값만 모아 dict 로 돌려주고, 실제 일은 window.py 가 엔진에 시킨다."""
from __future__ import annotations

import os
import traceback
from typing import Callable

import fitz
from PySide6.QtCore import QEvent, QRegularExpression, QThread, Qt, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QRadioButton, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..engine import annots as A
from ..engine import pages as P
from . import theme

PAGE_SIZES_MM = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "B4": (250, 353), "B5": (176, 250),
                 "Letter": (215.9, 279.4), "Legal": (215.9, 355.6)}


def mm2pt(v: float) -> float:
    return v * 72 / 25.4


def to_rgb01(c: QColor) -> tuple[float, float, float]:
    return (c.redF(), c.greenF(), c.blueF())


def from_rgb01(c) -> QColor:
    if c is None:
        return QColor(0, 0, 0, 0)
    if isinstance(c, str):   # 격자 색처럼 "#ff0000" 으로 적힌 값
        return QColor(c)
    return QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))


class ColorButton(QToolButton):
    """색 고르기 단추. none 허용(없음)."""
    def __init__(self, color=(1, 0, 0), allow_none: bool = False, parent=None):
        super().__init__(parent)
        self.allow_none = allow_none
        self.color = color
        self.setFixedSize(28, 22)
        self.clicked.connect(self.pick)
        self._paint()

    def _paint(self):
        line = theme.CUR["FIELD_LINE"]
        if self.color is None:  # 「없음」: 흰 바탕에 빨간 빗금
            bg = ("qlineargradient(x1:0,y1:1,x2:1,y2:0, stop:0 white, stop:0.47 white, stop:0.49 #E8383D, "
                  "stop:0.51 #E8383D, stop:0.53 white, stop:1 white)")
        else:
            bg = from_rgb01(self.color).name()
        self.setStyleSheet(f"QToolButton{{border:1px solid {line}; border-radius:4px; background:{bg};}}"
                           f"QToolButton:hover{{border-color:{theme.ACCENT};}}")

    def pick(self):
        dlg = QColorDialog(from_rgb01(self.color) if self.color else QColor("white"), self)
        if self.allow_none:
            dlg.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        if dlg.exec():
            c = dlg.currentColor()
            self.color = None if (self.allow_none and c.alpha() == 0) else to_rgb01(c)
            self._paint()

    def set_none(self):
        self.color = None
        self._paint()


class PageRangeEdit(QWidget):
    """쪽 범위 칸: 「모든·짝수·홀수 페이지」 / 「사용자 지정」 범위 글 + 「/ 전체 쪽 수」.

    짝수·홀수 거르기는 사용자 지정 범위에도 걸린다(「짝수 페이지」 + 1-10 → 2·4·…·10쪽). 범위 글은 엄격하게 읽어
    한 조각이라도 틀리거나 0쪽이면 오류다. odd_even 은 옛 호출부 호환용으로 받기만 한다(거르기 칸은 늘 보인다)."""
    HOW = (("모든 페이지", "all"), ("짝수 페이지", "even"), ("홀수 페이지", "odd"))

    def __init__(self, total: int, current: int = 0, parent=None, odd_even: bool = False):
        super().__init__(parent)
        self.total, self.current, self.odd_even = total, current, odd_even
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.all = QRadioButton(); self.all.setChecked(True); top.addWidget(self.all)
        self.which = QComboBox()
        for label, how in self.HOW:
            self.which.addItem(label, how)
        self.which.activated.connect(lambda: self.all.setChecked(True) if not self.rng.isChecked() else None)
        top.addWidget(self.which, 1)
        row = QHBoxLayout()
        self.rng = QRadioButton("사용자 지정")
        self.edit = QLineEdit("1"); self.edit.setMaxLength(200); self.edit.setPlaceholderText("예: 1,3,5,7-10")
        self.edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9,\- ]*"), self.edit))
        self.edit.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)   # 한글 입력기 끔
        self.edit.installEventFilter(self)
        self.edit.setEnabled(False)
        self.rng.toggled.connect(self._toggled)
        row.addWidget(self.rng); row.addWidget(self.edit, 1); row.addWidget(QLabel(f"/ {total}"))
        lay.addLayout(top); lay.addLayout(row)
        # 옛 호출부(「지금 쪽」 을 고르던 곳) 호환: 고르면 사용자 지정에 지금 쪽 번호를 넣는다
        self.cur = QRadioButton(self); self.cur.hide()
        self.cur.toggled.connect(lambda on: on and self._custom(str(current + 1)))

    def eventFilter(self, obj, ev):
        # 공백은 붙여넣기로만 들어온다(타자로는 숫자·「-」·「,」·지우기만)
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Space:
            return True
        return super().eventFilter(obj, ev)

    def _toggled(self, on: bool):
        self.edit.setEnabled(on)
        if on:
            self.edit.setFocus(); self.edit.selectAll()

    def _custom(self, text: str):
        self.rng.setChecked(True); self.edit.setText(text)

    def set_pages(self, pages: list[int]):
        """고른 쪽들로 채운다. 모든·짝수·홀수 쪽과 같으면 그것을, 아니면 사용자 지정 범위 글로."""
        pages = sorted(set(pages))
        every = list(range(self.total))
        for k, (_, how) in enumerate(self.HOW):
            if pages == P.subset(every, how):
                self.all.setChecked(True); self.which.setCurrentIndex(k); return
        self.which.setCurrentIndex(0)
        self._custom(P.format_range(pages))

    def pages(self) -> list[int]:
        how = self.which.currentData() or "all"
        try:
            got = P.subset(P.parse_range(self.edit.text(), self.total, strict=True) if self.rng.isChecked()
                           else range(self.total), how)
        except ValueError:
            got = []
        if not got:
            raise ValueError(P.RANGE_ERROR)
        return got

    def focus_error(self):
        """오류 문구를 띄운 뒤 부른다: 사용자 지정이면 글 칸으로 커서를 옮긴다(pages() 는 미리보기 갱신에도 불려 커서를 안 옮김)."""
        if self.rng.isChecked():
            self.edit.setFocus(); self.edit.selectAll()


def _buttons(dlg: QDialog, ok_text: str = "확인") -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    ok = bb.button(QDialogButtonBox.StandardButton.Ok)
    ok.setText(ok_text); ok.setDefault(True)  # 하는 일 단추는 강조색으로
    bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    return bb


class FormDialog(QDialog):
    """QFormLayout 하나짜리 간단 대화상자."""
    def __init__(self, title: str, parent=None, ok_text: str = "확인"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 12); self.lay.setSpacing(10)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.form.setHorizontalSpacing(10); self.form.setVerticalSpacing(6)
        self.lay.addLayout(self.form)
        self.bb = _buttons(self, ok_text)
        self.lay.addWidget(self.bb)

    def add(self, label: str, w: QWidget):
        self.form.addRow(label, w)
        return w


# ── 암호 ───────────────────────────────────────────────────────
def ask_password(parent, name: str) -> str | None:
    d = FormDialog("암호", parent, "열기")
    d.form.addRow(QLabel(f"「{name}」 은 암호가 걸려 있습니다."))
    e = d.add("암호", QLineEdit()); e.setEchoMode(QLineEdit.EchoMode.Password)
    return e.text() if d.exec() else None


# ── 글 상자 · 텍스트 추가 ──────────────────────────────────────
class TextBoxDialog(FormDialog):
    def __init__(self, parent, kind: str, props: dict, text: str = ""):
        title = {"textbox": "텍스트 박스", "callout": "텍스트 말풍선", "typewriter": "타자기", "addtext": "텍스트 추가",
                 "edit_block": "글자 고치기"}.get(kind, "글자")
        super().__init__(title, parent)
        self.text = QPlainTextEdit(text); self.text.setMinimumHeight(90)
        self.form.addRow("내용", self.text)
        self.size = self.add("글자 크기", QDoubleSpinBox()); self.size.setRange(4, 200); self.size.setValue(props.get("fontsize", 11))
        self.color = self.add("글자색", ColorButton(props.get("text_color", (0, 0, 0))))
        self.bold = self.add("", QCheckBox("굵게"))
        self.align = self.add("정렬", QComboBox()); self.align.addItems(["왼쪽", "가운데", "오른쪽", "양쪽"])
        if kind in ("textbox", "callout"):
            self.fill = self.add("바탕색", ColorButton(props.get("fill") or (1, 1, 1), allow_none=True))
            self.border = self.add("테두리색", ColorButton(props.get("stroke", (0, 0, 0)), allow_none=True))
            self.bw = self.add("테두리 두께", QDoubleSpinBox()); self.bw.setRange(0, 10); self.bw.setValue(1)
        else:
            self.fill = self.border = self.bw = None
        self.text.setFocus()

    def values(self) -> dict:
        return {"text": self.text.toPlainText(), "fontsize": self.size.value(), "text_color": self.color.color,
                "bold": self.bold.isChecked(), "align": self.align.currentIndex(),
                "fill": self.fill.color if self.fill else None, "border": self.border.color if self.border else None,
                "width": self.bw.value() if self.bw else 0}


# ── 주석 속성 ──────────────────────────────────────────────────
class AnnotPropsDialog(FormDialog):
    def __init__(self, parent, sel):
        super().__init__(f"{sel.extra.get('kind_ko', '주석')} 속성", parent)
        cols = sel.extra.get("colors", {})
        self.stroke = self.add("선·글자 색", ColorButton(tuple(cols.get("stroke")) if cols.get("stroke") else (1, 0, 0), allow_none=True))
        self.fill = self.add("채움색", ColorButton(tuple(cols.get("fill")) if cols.get("fill") else None, allow_none=True))
        self.width = self.add("두께", QDoubleSpinBox()); self.width.setRange(0, 30); self.width.setValue(float(sel.extra.get("width", 1) or 0))
        self.opacity = self.add("불투명도", QDoubleSpinBox()); self.opacity.setRange(0.05, 1); self.opacity.setSingleStep(0.05)
        op = sel.extra.get("opacity", 1.0)
        self.opacity.setValue(1.0 if op is None or op < 0 else float(op))
        self.content = QPlainTextEdit(sel.text or ""); self.content.setMinimumHeight(70)
        self.form.addRow("내용", self.content)
        self.author = self.add("작성자", QLineEdit(""))

    def values(self) -> dict:
        return {"stroke": self.stroke.color, "fill": self.fill.color, "width": self.width.value(),
                "opacity": self.opacity.value(), "content": self.content.toPlainText(),
                "author": self.author.text() or None}


# ── 양식 칸 ────────────────────────────────────────────────────
class FieldAlignDialog(FormDialog):
    """한 쪽의 양식 칸을 여럿 골라 맞춘다. 뷰어에서 고른 칸은 미리 체크해 둔다."""
    HOWS = [("left", "왼쪽 맞춤"), ("right", "오른쪽 맞춤"), ("top", "위쪽 맞춤"), ("bottom", "아래쪽 맞춤"),
            ("hcenter", "가로 가운데"), ("vcenter", "세로 가운데"), ("dist_h", "가로로 같은 간격"), ("dist_v", "세로로 같은 간격"),
            ("page_h", "쪽 가로 가운데"), ("page_v", "쪽 세로 가운데"), ("page_both", "쪽 한가운데")]

    def __init__(self, parent, page_no: int, widgets: list[tuple[int, str, str]], picked: set[int], how: str):
        super().__init__(f"양식 칸 맞추기 · {page_no + 1}쪽", parent, "맞추기")
        self.list = QListWidget()
        for xref, name, kind in widgets:
            ko = A.FIELD_TYPE_KO.get(kind.lower().replace("radiobutton", "radio").replace("combobox", "combo").replace("listbox", "list"), kind)
            it = QListWidgetItem(f"{name}  ({ko})"); it.setData(Qt.ItemDataRole.UserRole, xref)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if xref in picked or not picked else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.list.setMinimumHeight(min(320, 26 * len(widgets) + 8))
        self.add("맞출 칸", self.list)
        self.how = self.add("맞추기", QComboBox()); self.how.addItems([l for _, l in self.HOWS])
        self.how.setCurrentIndex(next((k for k, (h, _) in enumerate(self.HOWS) if h == how), 0))

    def values(self) -> dict:
        xs = [self.list.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.list.count())
              if self.list.item(k).checkState() == Qt.CheckState.Checked]
        return {"xrefs": xs, "how": self.HOWS[self.how.currentIndex()][0]}


def fill_field(parent, widget: fitz.Widget):
    """읽기 모드에서 칸 값 넣기. 숨긴 칸·읽기 전용 칸은 넣지 않고, 서식·범위에 안 맞으면 알림만 띄운다."""
    from ..engine import form_calc as FC
    from ..engine import form_props as FP
    t = widget.field_type
    f = widget.parent.parent
    fl = FP._int(f, widget.xref, "F", FP.F_PRINT)
    if fl & (FP.F_HIDDEN | FP.F_NOVIEW) or FP._int(f, FP._holder(f, widget.xref), "Ff") & FP.FF_READONLY:
        return None
    if t == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value in (False, "", "Off", None)
    if t == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        return True  # 누른 동그라미를 켠다(같은 묶음 나머지는 꺼진다)
    name = widget.field_name or ""
    fmt = FC.parse_format(FC.js_text(widget.script_format), FC.js_text(widget.script_stroke))
    val = FC.parse_validate(FC.js_text(widget.script_change))
    if t in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
        opts = [(str(v[0]), str(v[1])) if isinstance(v, (tuple, list)) else (str(v), str(v))
                for v in (widget.choice_values or [])]
        d = FormDialog(name or "항목 고르기", parent)
        cb = d.add("값", QComboBox()); cb.addItems([n for _, n in opts])
        cb.setEditable(t == fitz.PDF_WIDGET_TYPE_COMBOBOX)
        cur = str(widget.field_value or "")
        cb.setCurrentText(next((n for e, n in opts if e == cur), cur))
        while d.exec():
            txt = cb.currentText()
            out = next((e for e, n in opts if n == txt), txt)   # 보이는 이름 → 내보낼 값
            msg = FC.check_input(fmt, out, name) or FC.check_range(val, out, fmt.get("sep", 0))
            if not msg:
                return out
            QMessageBox.warning(parent, name or "값", msg)
        return None
    if t == fitz.PDF_WIDGET_TYPE_TEXT:
        d = FormDialog(name or "값 넣기", parent)
        e = d.add("값", QLineEdit(str(widget.field_value or "")))
        while d.exec():
            msg = FC.check_input(fmt, e.text(), name) or FC.check_range(val, e.text(), fmt.get("sep", 0))
            if not msg:
                return e.text()
            QMessageBox.warning(parent, name or "값", msg)
        return None
    return None


# ── 페이지 ─────────────────────────────────────────────────────
class InsertBlankDialog(FormDialog):
    def __init__(self, parent, total: int, current: int):
        super().__init__("빈 페이지 끼우기", parent)
        self.where = self.add("자리", QComboBox()); self.where.addItems(["지금 쪽 앞", "지금 쪽 뒤", "맨 앞", "맨 뒤"]); self.where.setCurrentIndex(1)
        self.count = self.add("장 수", QSpinBox()); self.count.setRange(1, 200); self.count.setValue(1)
        self.size = self.add("크기", QComboBox()); self.size.addItems(["지금 쪽과 같게"] + list(PAGE_SIZES_MM))
        self.orient = self.add("방향", QComboBox()); self.orient.addItems(["세로", "가로"])
        self.total, self.current = total, current

    def values(self) -> dict:
        at = {0: self.current, 1: self.current + 1, 2: 0, 3: self.total}[self.where.currentIndex()]
        w = h = None
        if self.size.currentIndex() > 0:
            mm = PAGE_SIZES_MM[self.size.currentText()]
            w, h = mm2pt(mm[0]), mm2pt(mm[1])
            if self.orient.currentIndex() == 1:
                w, h = h, w
        return {"at": at, "count": self.count.value(), "width": w, "height": h}


class CropDialog(FormDialog):
    def __init__(self, parent, total: int, current: int, rect: fitz.Rect | None, page_rect: fitz.Rect):
        super().__init__("자르기", parent)
        r = rect or page_rect
        self.form.addRow(QLabel(f"쪽 크기 {page_rect.width / 72 * 2.54:.2f} × {page_rect.height / 72 * 2.54:.2f} cm"))
        self.vals = {}
        for key, label, v in (("x0", "왼쪽(cm)", r.x0), ("y0", "위(cm)", r.y0), ("x1", "오른쪽(cm)", r.x1), ("y1", "아래(cm)", r.y1)):
            sb = QDoubleSpinBox(); sb.setRange(0, 500); sb.setDecimals(2); sb.setValue(v / 72 * 2.54)
            self.vals[key] = self.add(label, sb)
        self.pages = self.add("적용할 쪽", PageRangeEdit(total, current))
        if rect is not None:
            self.pages.cur.setChecked(True)

    def values(self) -> dict:
        v = {k: sb.value() / 2.54 * 72 for k, sb in self.vals.items()}
        return {"rect": fitz.Rect(v["x0"], v["y0"], v["x1"], v["y1"]), "pages": self.pages.pages()}


# 꾸미기 다섯 창(워터마크·배경·머리글·페이지 번호·베이츠)은 dlg_deco · dlg_deco_mark · dlg_deco_text 에 있다.


# ── 보안 ───────────────────────────────────────────────────────
class FileListDialog(QDialog):
    """파일 여러 개 모으기(합치기·일괄·파일에서 생성). 순서 바꾸기 됨."""
    def __init__(self, parent, title: str, ok_text: str, filters: str, extra: QWidget | None = None, paths: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumSize(560, 420)
        self.filters = filters
        lay = QVBoxLayout(self)
        self.list = QListWidget(); self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setAcceptDrops(True)
        lay.addWidget(QLabel("파일을 끌어다 놓거나 [추가] 를 누르세요. 끌어서 순서를 바꿉니다."))
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for label, fn in (("추가", self.add), ("폴더 추가", self.add_dir), ("위로", lambda: self.move(-1)), ("아래로", lambda: self.move(1)), ("빼기", self.remove)):
            b = QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        if extra is not None:
            lay.addWidget(extra)
        lay.addWidget(_buttons(self, ok_text))
        self.setAcceptDrops(True)
        for p in (paths or []):
            self._add(p)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    self._add(os.path.join(p, f))
            else:
                self._add(p)

    def _add(self, p: str):
        if os.path.isfile(p):
            it = QListWidgetItem(os.path.basename(p)); it.setData(Qt.ItemDataRole.UserRole, p); it.setToolTip(p)
            self.list.addItem(it)

    def add(self):
        for p in QFileDialog.getOpenFileNames(self, "파일 추가", "", self.filters)[0]:
            self._add(p)

    def add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "폴더")
        if d:
            for f in sorted(os.listdir(d)):
                self._add(os.path.join(d, f))

    def move(self, d: int):
        rows = sorted((self.list.row(it) for it in self.list.selectedItems()), reverse=(d > 0))
        for r in rows:
            nr = r + d
            if 0 <= nr < self.list.count():
                it = self.list.takeItem(r); self.list.insertItem(nr, it); it.setSelected(True)

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def paths(self) -> list[str]:
        return [self.list.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.list.count())]


# ── 진행 표시 + 일꾼 ───────────────────────────────────────────
class Worker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self):
        try:
            res = self.fn(*self.args, progress=self._prog, **self.kwargs) if "progress" in self.fn.__code__.co_varnames else self.fn(*self.args, **self.kwargs)
            self.finished_ok.emit(res)
        except Exception as e:  # noqa: BLE001
            if any(c.__name__ in ("EngineError", "DocumentError") for c in type(e).__mro__):
                self.failed.emit(f"!{e}")  # 한국어로 쓴 안내(코드 흔적 없이 주의 창으로)
            else:
                self.failed.emit(f"{e}\n\n{traceback.format_exc()[-800:]}")

    def _prog(self, done: int, total: int, msg: str = ""):
        self.progress.emit(done, total, msg)


FAIL_TEXT = {"OCR": ("OCR 실패", "오류! OCR을 완료하지 못했습니다."),
             "평면화": ("PDF를 평면화하지 못했습니다", "오류! PDF를 평면화하지 못했습니다.")}


class ProgressDialog(QDialog):
    """fn 을 따로 돌리며 진행을 보여 준다. run() 이 결과를 돌려주고, 실패하면 None + 메시지."""
    def __init__(self, parent, title: str, fn: Callable, *args, **kwargs):
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        lay = QVBoxLayout(self)
        self.label = QLabel("준비 중"); lay.addWidget(self.label)
        self.bar = QProgressBar(); self.bar.setRange(0, 0); self.bar.setTextVisible(False); lay.addWidget(self.bar)   # 막대가 6px 라 글자는 뺀다
        self.result = None
        self.error: str | None = None
        self.worker = Worker(fn, *args, **kwargs)
        self.worker.progress.connect(self._prog)
        self.worker.finished_ok.connect(self._ok)
        self.worker.failed.connect(self._fail)

    def _prog(self, done, total, msg):
        if total:
            self.bar.setRange(0, total); self.bar.setValue(done)
        self.label.setText(msg or f"{done} / {total}")

    def _ok(self, res):
        self.result = res; self.accept()

    def _fail(self, msg):
        self.error = msg; self.reject()

    def fail_text(self) -> tuple[str, str] | None:
        """실패 창 제목·첫 줄. 변환·OCR·평면화는 정해 둔 문구, 그 밖은 None(창 제목에 까닭만)."""
        t = self.windowTitle()
        if t.endswith("로 변환"):
            return "변환 실패", f"오류! {t[:-4]}(으)로 변환하지 못했습니다."
        return FAIL_TEXT.get(t)

    def run(self):
        self.worker.start()
        self.exec()
        self.worker.wait(3000)
        if self.error:
            guided = self.error.startswith("!")
            if guided:
                self.error = self.error[1:]
            why = self.error if guided else self.error.split("\n\n")[0]
            title, msg = self.windowTitle(), why
            if fixed := self.fail_text():
                title, msg = fixed[0], fixed[1] + (f"\n{why}" if why else "")
            (QMessageBox.warning if guided else QMessageBox.critical)(self.parent(), title, msg)
        return self.result
