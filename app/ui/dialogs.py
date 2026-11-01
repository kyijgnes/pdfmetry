"""대화상자 모음. 값만 모아 dict 로 돌려주고, 실제 일은 window.py 가 엔진에 시킨다."""
from __future__ import annotations

import os
import traceback
from html import escape
from typing import Callable

import fitz
from PySide6.QtCore import QEvent, QRegularExpression, QThread, Qt, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QRadioButton, QSpinBox, QTabWidget, QTextEdit, QToolButton, QVBoxLayout,
                               QWidget)

from ..engine import annots as A
from ..engine import pages as P
from ..engine.document import Document
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
class FieldPropsDialog(FormDialog):
    def __init__(self, parent, name: str, ftype: str, value, options: list[str], radio: bool = False):
        super().__init__("양식 칸 속성", parent)
        self.name = self.add("묶음 이름" if radio else "이름", QLineEdit(name))
        self.form.addRow("종류", QLabel(ftype))
        self.value = self.add("값", QLineEdit(str(value or "")))
        self.options = QPlainTextEdit("\n".join(options)); self.options.setPlaceholderText("한 줄에 하나(콤보·목록 박스)")
        self.options.setMaximumHeight(90)
        self.form.addRow("항목", self.options)

    def values(self) -> dict:
        return {"name": self.name.text(), "value": self.value.text(),
                "options": [l for l in self.options.toPlainText().splitlines() if l.strip()]}


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
    """읽기 모드에서 칸 값 넣기."""
    t = widget.field_type
    if t == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value in (False, "", "Off", None)
    if t == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        return True  # 누른 동그라미를 켠다(같은 묶음 나머지는 꺼진다)
    if t in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
        d = FormDialog(widget.field_name or "항목 고르기", parent)
        cb = d.add("값", QComboBox()); cb.addItems([str(v) for v in (widget.choice_values or [])]); cb.setEditable(t == fitz.PDF_WIDGET_TYPE_COMBOBOX)
        cb.setCurrentText(str(widget.field_value or ""))
        return cb.currentText() if d.exec() else None
    if t == fitz.PDF_WIDGET_TYPE_TEXT:
        d = FormDialog(widget.field_name or "값 넣기", parent)
        e = d.add("값", QLineEdit(str(widget.field_value or "")))
        return e.text() if d.exec() else None
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
class EncryptDialog(FormDialog):
    METHODS = [("aes128", "128 bit AES"), ("aes256", "256 bit AES · 한글 암호 가능"), ("rc4", "128 bit RC4 · 옛 프로그램용")]

    def __init__(self, parent, method: str | None = None):
        super().__init__("암호화", parent, "적용")
        self.open_pw = self.add("열기 암호", QLineEdit()); self.open_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.open_pw2 = self.add("열기 암호 확인", QLineEdit()); self.open_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.owner_pw = self.add("권한 암호", QLineEdit()); self.owner_pw.setEchoMode(QLineEdit.EchoMode.Password)
        g = QGroupBox("허용할 것"); gl = QGridLayout(g)
        self.perms = {}
        for n, (key, label) in enumerate((("allow_print", "인쇄"), ("allow_print_hq", "고화질 인쇄"), ("allow_copy", "글자·그림 복사"),
                                          ("allow_modify", "내용 바꾸기"), ("allow_annotate", "주석"), ("allow_forms", "양식 채우기"),
                                          ("allow_assemble", "쪽 구성"), ("allow_accessibility", "접근성 읽기"))):
            cb = QCheckBox(label); cb.setChecked(True); self.perms[key] = cb; gl.addWidget(cb, n // 2, n % 2)
        self.form.addRow(g)
        box = QWidget(); vl = QVBoxLayout(box); vl.setContentsMargins(0, 0, 0, 0)
        self.methods = {}
        for key, label in self.METHODS:   # 128 bit AES 가 기본, 이미 걸린 문서는 그 방식
            rb = QRadioButton(label); self.methods[key] = rb; vl.addWidget(rb)
        self.methods.get(method or "aes128", self.methods["aes128"]).setChecked(True)
        self.add("방식", box)
        box2 = QWidget(); vl2 = QVBoxLayout(box2); vl2.setContentsMargins(0, 0, 0, 0)
        self.here = QRadioButton("이 문서에 걸기 · 저장할 때 적용"); self.here.setChecked(True)
        self.new_file = QRadioButton("암호 건 사본을 새 파일로 저장")
        vl2.addWidget(self.here); vl2.addWidget(self.new_file)
        self.add("적용", box2)

    def accept(self):
        if self.open_pw.text() != self.open_pw2.text():
            QMessageBox.warning(self, "암호화", "열기 암호 두 칸이 다릅니다"); return
        from ..engine.tools import EngineError, check_encrypt_args
        try:   # 창을 닫기 전에 확인한다(같은 암호·한글 암호 등)
            check_encrypt_args(self.open_pw.text(), self.owner_pw.text(), self._method())
        except EngineError as e:
            QMessageBox.warning(self, "암호화", str(e)); return
        super().accept()

    def _method(self) -> str:
        return next(k for k, rb in self.methods.items() if rb.isChecked())

    def values(self) -> dict | None:
        # 권한 암호를 비우면 엔진이 무작위로 만든다(열기 암호를 권한 암호로 쓰면 인쇄·복사 막기가 무의미했다)
        d = {"user_pw": self.open_pw.text(), "owner_pw": self.owner_pw.text(), "method": self._method()}
        d.update({k: cb.isChecked() for k, cb in self.perms.items()})
        return d

    def to_new_file(self) -> bool:
        return self.new_file.isChecked()


class RedactSearchDialog(FormDialog):
    def __init__(self, parent, total: int, current: int):
        super().__init__("검색·교정", parent, "표시")
        self.pattern = self.add("찾을 글자", QLineEdit())
        self.regex = self.add("", QCheckBox("정규식으로"))
        self.presets = self.add("자주 쓰는 것", QComboBox())
        self.presets.addItems(["직접 입력", "전화번호(010-0000-0000)", "주민등록번호", "이메일 주소", "숫자 6자리 이상"])
        self.presets.currentIndexChanged.connect(self._preset)
        self.fill = self.add("가릴 색", ColorButton((0, 0, 0)))
        self.pages = self.add("적용할 쪽", PageRangeEdit(total, current))

    def _preset(self, k):
        # 앞뒤가 숫자면 안 잡는다(주민번호·학번 안의 숫자에 전화번호가 걸리던 것). 구분자는 - . 띄어쓰기, 외국인번호(5~8)도.
        pat = {1: r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)", 2: r"(?<!\d)\d{6}\s*-?\s*[1-8]\d{6}(?!\d)",
               3: r"[\w.+-]+@[\w-]+\.[\w.]+", 4: r"\d{6,}"}.get(k)
        if pat:
            self.pattern.setText(pat); self.regex.setChecked(True)

    def values(self) -> dict:
        return {"pattern": self.pattern.text(), "regex": self.regex.isChecked(), "fill": self.fill.color, "pages": self.pages.pages()}


class HiddenDataDialog(FormDialog):
    def __init__(self, parent):
        super().__init__("숨겨진 데이터 삭제", parent, "삭제")
        self.cbs = {}
        for key, label, on in (("metadata", "문서 속성(제목·작성자 등)", True), ("attachments", "첨부 파일", True),
                               ("javascript", "자바스크립트", True), ("annotations", "주석", False),
                               ("bookmarks", "북마크", False), ("form_fields", "양식 칸", False)):
            cb = QCheckBox(label); cb.setChecked(on); self.cbs[key] = cb; self.form.addRow(cb)

    def values(self) -> dict:
        return {k: cb.isChecked() for k, cb in self.cbs.items()}


# ── 도구 ───────────────────────────────────────────────────────
COMPRESS_CHOICES = [("high", "높음 · 이미지 72dpi, 용량 우선"), ("medium", "중간 · 이미지 120dpi"), ("low", "낮음 · 이미지 150dpi, 화질 우선")]


class CompressDialog(FormDialog):
    """압축 정도는 마지막에 고른 것으로 연다(처음엔 높음)."""
    def __init__(self, parent, size_bytes: int, level: str = "high", remove_metadata: bool = True):
        super().__init__("PDF 압축", parent, "압축")
        self.form.addRow("지금 크기", QLabel(f"{size_bytes / 1024:.0f} KB"))
        box = QWidget(); vl = QVBoxLayout(box); vl.setContentsMargins(0, 0, 0, 0)
        self.levels = {}
        for key, label in COMPRESS_CHOICES:
            rb = QRadioButton(label); self.levels[key] = rb; vl.addWidget(rb)
        self.levels.get(level, self.levels["high"]).setChecked(True)
        self.add("정도", box)
        self.meta = self.add("", QCheckBox("문서 정보·메타데이터 삭제")); self.meta.setChecked(remove_metadata)

    def values(self) -> dict:
        return {"level": next(k for k, rb in self.levels.items() if rb.isChecked()), "remove_metadata": self.meta.isChecked()}


class OcrDialog(FormDialog):
    def __init__(self, parent, total: int, current: int, langs: list[str]):
        super().__init__("OCR (글자 인식)", parent, "인식")
        self.form.addRow(QLabel("윈도우에 들어 있는 글자 인식으로 스캔 쪽에 찾기·복사가 되는 글자층을 넣습니다."))
        self.lang = self.add("언어", QComboBox()); self.lang.addItems(langs or ["ko"])
        self.dpi = self.add("해상도", QComboBox()); self.dpi.addItems(["200 dpi", "300 dpi", "400 dpi"]); self.dpi.setCurrentIndex(1)
        self.force = self.add("", QCheckBox("글자가 이미 있는 쪽도 다시 인식"))
        self.pages = self.add("적용할 쪽", PageRangeEdit(total, current))

    def values(self) -> dict:
        return {"lang": self.lang.currentText(), "dpi": int(self.dpi.currentText().split()[0]),
                "force": self.force.isChecked(), "pages": self.pages.pages()}


class ConvertDialog(FormDialog):
    """변환 설정: 형식·쪽·해상도·저장 위치."""
    FORMATS = [("Word (.docx)", "docx"), ("Excel (.xlsx)", "xlsx"), ("PowerPoint (.pptx)", "pptx"), ("이미지 (.png)", "png"),
               ("이미지 (.jpg)", "jpg"), ("텍스트 (.txt)", "txt"), ("HTML (.html)", "html"), ("RTF (.rtf)", "rtf"),
               ("EPUB (.epub)", "epub"), ("PDF/A (.pdf)", "pdfa"), ("한글 (.hwpx)", "hwpx")]

    def __init__(self, parent, total: int, current: int, default_fmt: str, default_out: str):
        super().__init__("변환", parent, "변환")
        self.fmt = self.add("형식", QComboBox())
        for label, key in self.FORMATS:
            self.fmt.addItem(label, key)
        idx = next((k for k, (_, key) in enumerate(self.FORMATS) if key == default_fmt), 0)
        self.fmt.setCurrentIndex(idx)
        self.dpi = self.add("이미지 해상도", QComboBox()); self.dpi.addItems(["100 dpi", "150 dpi", "200 dpi", "300 dpi", "600 dpi"]); self.dpi.setCurrentIndex(1)
        self.pages = self.add("쪽", PageRangeEdit(total, current))
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        self.out = QLineEdit(default_out); b = QPushButton("찾기"); b.clicked.connect(self._pick); hl.addWidget(self.out); hl.addWidget(b)
        self.add("저장 위치", row)
        self.fmt.currentIndexChanged.connect(self._sync)
        self.open_after = self.add("", QCheckBox("끝나면 폴더 열기")); self.open_after.setChecked(True)

    def _sync(self):
        key = self.fmt.currentData()
        base, _ = os.path.splitext(self.out.text())
        if key in ("png", "jpg"):
            self.out.setText(os.path.dirname(base) or base)
        else:
            ext = {"pdfa": ".pdf"}.get(key, "." + key)
            self.out.setText((base if not os.path.isdir(base) else os.path.join(base, "변환")) + ext)
        self.dpi.setEnabled(key in ("png", "jpg", "pptx"))

    def _pick(self):
        key = self.fmt.currentData()
        if key in ("png", "jpg"):
            d = QFileDialog.getExistingDirectory(self, "저장 폴더", self.out.text())
            if d: self.out.setText(d)
        else:
            p, _ = QFileDialog.getSaveFileName(self, "저장", self.out.text())
            if p: self.out.setText(p)

    def values(self) -> dict:
        return {"fmt": self.fmt.currentData(), "dpi": int(self.dpi.currentText().split()[0]), "pages": self.pages.pages(),
                "out": self.out.text(), "open_after": self.open_after.isChecked()}


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


class BatchDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("PDF 일괄 작업"); self.setMinimumSize(620, 520)
        lay = QVBoxLayout(self)
        self.op = QComboBox()
        self.op.addItem("변환", "convert"); self.op.addItem("압축", "compress"); self.op.addItem("워터마크", "watermark")
        self.op.addItem("암호화", "encrypt"); self.op.addItem("OCR", "ocr"); self.op.addItem("PDF 로 만들기(이미지·오피스·한글)", "create"); self.op.addItem("인쇄", "print")
        f = QFormLayout(); f.addRow("작업", self.op)
        self.fmt = QComboBox()
        for label, key in ConvertDialog.FORMATS: self.fmt.addItem(label, key)
        f.addRow("변환 형식", self.fmt)
        self.level = QComboBox(); self.level.addItems([label for _, label in COMPRESS_CHOICES]); f.addRow("압축 정도", self.level)
        self.wm = QLineEdit("대외비"); f.addRow("워터마크 글자", self.wm)
        self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.EchoMode.Password); f.addRow("열기 암호", self.pw)
        from PySide6.QtPrintSupport import QPrinterInfo
        self.printer = QComboBox(); self.printer.addItems(QPrinterInfo.availablePrinterNames())
        self.printer.setCurrentText(QPrinterInfo.defaultPrinterName()); f.addRow("프린터", self.printer)
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        self.out = QLineEdit(); b = QPushButton("폴더"); b.clicked.connect(self._pick); hl.addWidget(self.out); hl.addWidget(b)
        f.addRow("저장 폴더", row)
        lay.addLayout(f)
        self.files = FileListDialog(self, "", "", "모든 파일 (*)")
        self.files.setWindowFlags(Qt.WindowType.Widget)
        self.files.layout().itemAt(self.files.layout().count() - 1).widget().hide()
        lay.addWidget(self.files)
        lay.addWidget(_buttons(self, "시작"))
        self.op.currentIndexChanged.connect(self._sync); self._sync()

    def _sync(self):
        k = self.op.currentData()
        self.fmt.setEnabled(k == "convert"); self.level.setEnabled(k == "compress"); self.wm.setEnabled(k == "watermark"); self.pw.setEnabled(k == "encrypt")
        self.printer.setEnabled(k == "print"); self.out.setEnabled(k != "print")

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더")
        if d: self.out.setText(d)

    def values(self) -> dict:
        k = self.op.currentData()
        target = {"docx": "word", "xlsx": "excel", "png": "images", "jpg": "images", "txt": "text"}.get(self.fmt.currentData(), self.fmt.currentData())
        opts = {"convert": {"target": target, **({"fmt": "jpg"} if self.fmt.currentData() == "jpg" else {})}, "compress": {"level": COMPRESS_CHOICES[self.level.currentIndex()][0]},
                "watermark": {"text": self.wm.text()}, "encrypt": {"user_pw": self.pw.text()}, "ocr": {}, "create": {},
                "print": {"printer": self.printer.currentText()}}[k]
        return {"op": k, "inputs": self.files.paths(), "out_dir": self.out.text(), "options": opts}


# ── 속성 · 환경 설정 ───────────────────────────────────────────
class PropertiesDialog(QDialog):
    def __init__(self, parent, doc: Document, path: str | None):
        super().__init__(parent)
        self.setWindowTitle("파일 속성"); self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        tabs = QTabWidget(); lay.addWidget(tabs)
        md = doc.metadata()
        w1 = QWidget(); f1 = QFormLayout(w1)
        self.edits = {}
        for key, label in (("title", "제목"), ("author", "작성자"), ("subject", "주제"), ("keywords", "키워드"),
                           ("creator", "만든 프로그램"), ("producer", "PDF 생성기")):
            e = QLineEdit(md.get(key) or ""); self.edits[key] = e; f1.addRow(label, e)
        f1.addRow("만든 날", QLabel(md.get("creationDate") or "")); f1.addRow("고친 날", QLabel(md.get("modDate") or ""))
        tabs.addTab(w1, "설명")
        w2 = QWidget(); f2 = QFormLayout(w2)
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        r = doc.fitz[0].rect if doc.page_count else fitz.Rect(0, 0, 0, 0)
        f2.addRow("파일", QLabel(path or "(저장 안 됨)")); f2.addRow("크기", QLabel(f"{size / 1024:.1f} KB"))
        f2.addRow("쪽 수", QLabel(str(doc.page_count)))
        f2.addRow("쪽 크기", QLabel(f"{r.width / 72 * 25.4:.1f} × {r.height / 72 * 25.4:.1f} mm"))
        f2.addRow("PDF 버전", QLabel(md.get("format") or ""))
        f2.addRow("암호", QLabel("걸림" if doc.fitz.is_encrypted else "없음"))
        f2.addRow("양식", QLabel("있음" if doc.fitz.is_form_pdf else "없음"))
        f2.addRow("글자층", QLabel("있음" if any(doc.has_text(i) for i in range(min(3, doc.page_count))) else "없음(스캔본일 수 있음)"))
        tabs.addTab(w2, "정보")
        w3 = QWidget(); f3 = QFormLayout(w3)
        perms = doc.fitz.permissions
        for flag, label in ((fitz.PDF_PERM_PRINT, "인쇄"), (fitz.PDF_PERM_COPY, "복사"), (fitz.PDF_PERM_MODIFY, "바꾸기"),
                            (fitz.PDF_PERM_ANNOTATE, "주석"), (fitz.PDF_PERM_FORM, "양식"), (fitz.PDF_PERM_ASSEMBLE, "쪽 구성")):
            f3.addRow(label, QLabel("허용" if perms & flag else "금지"))
        tabs.addTab(w3, "보안")
        fonts = sorted({f[3] for i in range(min(doc.page_count, 30)) for f in doc.fitz[i].get_fonts()})
        w4 = QListWidget(); w4.addItems(fonts); tabs.addTab(w4, f"글꼴 ({len(fonts)})")
        lay.addWidget(_buttons(self, "저장"))

    def values(self) -> dict:
        return {k: e.text() for k, e in self.edits.items()}


class PreferencesDialog(QDialog):
    def __init__(self, parent, prefs: dict):
        super().__init__(parent)
        self.setWindowTitle("환경 설정"); self.setMinimumWidth(460)
        lay = QVBoxLayout(self); tabs = QTabWidget(); lay.addWidget(tabs)
        w = QWidget(); f = QFormLayout(w)
        self.author = QLineEdit(prefs.get("author", "")); f.addRow("주석 작성자 이름", self.author)
        self.zoom = QComboBox(); self.zoom.addItems(["창 너비에 맞추기", "창 크기에 맞추기", "실제 크기", "마지막으로 쓴 확대"])
        self.zoom.setCurrentIndex(["fit_width", "fit_page", "actual", "last"].index(prefs.get("default_zoom", "last")))
        f.addRow("열 때 확대", self.zoom)
        self.layout_ = QComboBox(); self.layout_.addItems(["연속", "한 쪽씩", "두 쪽", "두 쪽 연속"])
        self.layout_.setCurrentIndex(["continuous", "single", "facing", "facing_continuous"].index(prefs.get("default_layout", "continuous")))
        f.addRow("열 때 보기", self.layout_)
        self.dark = QCheckBox("어두운 화면"); self.dark.setChecked(prefs.get("dark", False)); f.addRow(self.dark)
        self.winsize = QComboBox(); self.winsize.addItems(["마지막으로 닫은 크기", "화면 가득", "보통 크기"])
        self.winsize.setCurrentIndex(["last", "max", "normal"].index(prefs.get("window_size", "last"))); f.addRow("열 때 창 크기", self.winsize)
        self.restore = QCheckBox("시작할 때 지난번 문서 다시 열기"); self.restore.setChecked(prefs.get("restore", False)); f.addRow(self.restore)
        self.recent_n = QSpinBox(); self.recent_n.setRange(5, 100); self.recent_n.setValue(prefs.get("recent_n", 30)); f.addRow("최근 파일 개수", self.recent_n)
        tabs.addTab(w, "일반")
        w2 = QWidget(); f2 = QFormLayout(w2)
        self.default_dir = QLineEdit(prefs.get("default_dir", "")); f2.addRow("변환 저장 폴더", self.default_dir)
        self.share_dir = QLineEdit(prefs.get("share_dir", "")); f2.addRow("공유폴더", self.share_dir)
        self.ocr_lang = QLineEdit(prefs.get("ocr_lang", "ko")); f2.addRow("OCR 언어", self.ocr_lang)
        self.unit = QComboBox(); self.unit.addItems(["cm", "mm", "in", "pt"]); self.unit.setCurrentText(prefs.get("unit", "cm")); f2.addRow("측정 단위", self.unit)
        tabs.addTab(w2, "폴더·도구")
        lay.addWidget(_buttons(self, "저장"))

    def values(self) -> dict:
        return {"author": self.author.text(), "default_zoom": ["fit_width", "fit_page", "actual", "last"][self.zoom.currentIndex()],
                "default_layout": ["continuous", "single", "facing", "facing_continuous"][self.layout_.currentIndex()],
                "dark": self.dark.isChecked(), "restore": self.restore.isChecked(),
                "window_size": ["last", "max", "normal"][self.winsize.currentIndex()], "recent_n": self.recent_n.value(),
                "default_dir": self.default_dir.text(), "share_dir": self.share_dir.text(), "ocr_lang": self.ocr_lang.text(),
                "unit": self.unit.currentText()}


# ── 비교 결과 ──────────────────────────────────────────────────
class CompareDialog(QDialog):
    def __init__(self, parent, name_a: str, name_b: str, result: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("파일 비교"); self.setMinimumSize(700, 500)
        lay = QVBoxLayout(self)
        changed = [r for r in result if r.get("text_diff") or r.get("changed_rects_a") or r.get("changed_rects_b")]
        lay.addWidget(QLabel(f"「{name_a}」 ↔ 「{name_b}」: 달라진 쪽 {len(changed)} / {len(result)}"))
        self.text = QTextEdit(); self.text.setReadOnly(True)
        html = []
        name_a, name_b = escape(name_a), escape(name_b)
        for r in result:
            diffs = r.get("text_diff") or []
            if not diffs and not (r.get("changed_rects_a") or r.get("changed_rects_b")) and not r.get("only_in"):
                continue
            pa, pb = r.get("page_a", r["page"]), r.get("page_b", r["page"])
            if r.get("only_in") == "a":
                html.append(f"<h4>{name_a} {pa + 1}쪽 · {name_b} 에 없는 쪽</h4>")
                continue
            if r.get("only_in") == "b":
                html.append(f"<h4>{name_b} {pb + 1}쪽 · {name_a} 에 없는 쪽</h4>")
                continue
            html.append(f"<h4>{pa + 1}쪽</h4>" if pa == pb else f"<h4>{name_a} {pa + 1}쪽 ↔ {name_b} {pb + 1}쪽</h4>")
            if not diffs:
                html.append("<p>글자는 같고 그림·배치가 다릅니다.</p>")
            for d in diffs:
                op, a, b = d[0], escape(d[1]), escape(d[2])
                if op == "delete":
                    html.append(f"<p><span style='background:#fdd;text-decoration:line-through'>{a}</span></p>")
                elif op == "insert":
                    html.append(f"<p><span style='background:#dfd'>{b}</span></p>")
                else:
                    html.append(f"<p><span style='background:#fdd;text-decoration:line-through'>{a}</span> → <span style='background:#dfd'>{b}</span></p>")
        self.text.setHtml("".join(html) or "<p>두 문서가 같습니다.</p>")
        lay.addWidget(self.text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        bb.rejected.connect(self.reject); bb.clicked.connect(self.reject); lay.addWidget(bb)
        self.result = result


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

    def run(self):
        self.worker.start()
        self.exec()
        self.worker.wait(3000)
        if self.error and self.error.startswith("!"):
            self.error = self.error[1:]
            QMessageBox.warning(self.parent(), self.windowTitle(), self.error)
        elif self.error:
            QMessageBox.critical(self.parent(), self.windowTitle(), self.error.split("\n\n")[0])
        return self.result
