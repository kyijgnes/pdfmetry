"""양식 칸 속성 창 · 동작 편집 창 · 칸 고르기 창 · 계산 순서 창.

속성 창은 값을 바꿀 때마다 바로 문서에 넣고, 창을 연 동안의 바꿈은 되돌리기 하나로 묶는다(처음 바꿀 때 한 번만 기록).
"""
from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..engine import annots as A
from ..engine import form_calc as C
from ..engine import form_props as P
from .dialogs import ColorButton, FormDialog

TAB_NAMES = (("general", "일반"), ("design", "디자인"), ("options", "옵션"), ("actions", "동작"), ("format", "서식"),
             ("validate", "유효성 검사"), ("calc", "계산"), ("selchange", "선택 변경"), ("signed", "서명됨"))
TABS_BY_TYPE = {
    "text": ("general", "design", "options", "actions", "format", "validate", "calc"),
    "combo": ("general", "design", "options", "actions", "format", "validate", "calc"),
    "list": ("general", "design", "options", "actions", "selchange"),
    "checkbox": ("general", "design", "options", "actions"),
    "radio": ("general", "design", "options", "actions"),
    "button": ("general", "design", "options", "actions"),
    "signature": ("general", "design", "actions", "signed"),
}
COMMON_TABS = ("general", "design", "actions")
MSG_NAME_TAKEN = "입력한 이름은 이미 다른 필드에서 사용 중입니다. 다른 이름을 선택하거나 기존 필드와 일치하도록 필드 유형을 변경하십시오."
MSG_URL_EMPTY = "URL은 비워둘 수 없습니다."


def all_field_names(doc) -> list[str]:
    out = []
    with doc.lock:
        for i in range(doc.page_count):
            for w in doc.fitz[i].widgets():
                if w.field_name and w.field_name not in out:
                    out.append(w.field_name)
    return out


# ── 작은 창들 ───────────────────────────────────────────────────
class FieldPickDialog(FormDialog):
    """칸 이름 여러 개 고르기(체크 목록 + 모두 선택)."""

    def __init__(self, parent, names: list[str], picked: list[str], title: str = "필드 선택"):
        super().__init__(title, parent)
        self.list = QListWidget()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if n in picked else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.list.setMinimumHeight(min(320, 24 * max(3, len(names)) + 8))
        self.form.addRow(self.list)
        self.all = QCheckBox("모두 선택")
        self.all.toggled.connect(lambda on: [self.list.item(k).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                                             for k in range(self.list.count())])
        self.form.addRow(self.all)

    def values(self) -> list[str]:
        return [self.list.item(k).text() for k in range(self.list.count())
                if self.list.item(k).checkState() == Qt.CheckState.Checked]


class ScriptDialog(FormDialog):
    """자바스크립트 편집기(줄·열 표시). 우리 화면에서는 돌리지 않고 파일에 저장만 한다."""

    def __init__(self, parent, text: str = "", title: str = "자바 스크립트 편집기"):
        super().__init__(title, parent)
        self.setMinimumWidth(520)
        self.edit = QPlainTextEdit(text)
        self.edit.setMinimumHeight(220)
        self.form.addRow(self.edit)
        self.pos = QLabel()
        self.form.addRow(self.pos)
        self.edit.cursorPositionChanged.connect(self._pos)
        self._pos()

    def _pos(self):
        c = self.edit.textCursor()
        self.pos.setText(f"줄 {c.blockNumber() + 1}, 열 {c.positionInBlock() + 1}")

    def text(self) -> str:
        return self.edit.toPlainText()


class ActionDialog(FormDialog):
    """동작 하나 만들기·고치기."""

    def __init__(self, parent, doc, act: dict):
        title = dict(P.ACTIONS).get(act["type"], "동작")
        super().__init__(title, parent)
        self.doc, self.act = doc, dict(act)
        t = act["type"]
        if t == "goto":
            self.page = self.add("페이지", QSpinBox())
            self.page.setRange(1, max(1, doc.page_count)); self.page.setValue(int(act.get("page", 0)) + 1)
        elif t == "hide":
            self.fields = FieldPickList(all_field_names(doc), act.get("fields", []))
            self.form.addRow(self.fields)
            self.show_r, self.hide_r = QRadioButton("표시"), QRadioButton("숨기기")
            (self.hide_r if act.get("hide", True) else self.show_r).setChecked(True)
            row = QHBoxLayout(); row.addWidget(self.show_r); row.addWidget(self.hide_r); row.addStretch(1)
            self.form.addRow(row)
        elif t == "reset":
            self.form.addRow(QLabel("재설정할 필드 선택"))
            names = all_field_names(doc)
            self.fields = FieldPickList(names, act.get("fields") or names)
            self.form.addRow(self.fields)
        elif t == "uri":
            self.uri = self.add("URL", QLineEdit(act.get("uri", "")))
            self.uri.setMinimumWidth(320)
        elif t == "launch":
            self.file = QLineEdit(act.get("file", ""))
            b = QPushButton("찾아보기")
            b.clicked.connect(self._browse)
            row = QHBoxLayout(); row.addWidget(self.file, 1); row.addWidget(b)
            self.form.addRow("파일", row)
        else:
            self.js = QPlainTextEdit(act.get("script", "")); self.js.setMinimumHeight(200)
            self.form.addRow(self.js)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "파일 열기", os.path.dirname(self.file.text()))
        if p:
            self.file.setText(p)

    def accept(self):
        if self.act["type"] == "uri" and not self.uri.text().strip():
            QMessageBox.warning(self, "웹 링크 열기", MSG_URL_EMPTY)
            return
        super().accept()

    def values(self) -> dict:
        t = self.act["type"]
        if t == "goto":
            return {"type": t, "page": self.page.value() - 1}
        if t == "hide":
            return {"type": t, "fields": self.fields.values(), "hide": self.hide_r.isChecked()}
        if t == "reset":
            names = self.fields.values()
            return {"type": t, "fields": [] if len(names) == self.fields.count() else names}
        if t == "uri":
            return {"type": t, "uri": self.uri.text().strip()}
        if t == "launch":
            return {"type": t, "file": self.file.text().strip()}
        return {"type": "js", "script": self.js.toPlainText()}


class FieldPickList(QListWidget):
    def __init__(self, names: list[str], picked: list[str]):
        super().__init__()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if n in picked else Qt.CheckState.Unchecked)
            self.addItem(it)
        self.setMinimumHeight(min(260, 24 * max(3, len(names)) + 8))

    def values(self) -> list[str]:
        return [self.item(k).text() for k in range(self.count()) if self.item(k).checkState() == Qt.CheckState.Checked]


def _describe(a: dict) -> str:
    t = a["type"]
    head = dict(P.ACTIONS).get(t, t)
    if t == "goto":
        return f"{head}: {a.get('page', 0) + 1}쪽"
    if t == "hide":
        return f"{head}: {'숨기기' if a.get('hide', True) else '표시'} {', '.join(a.get('fields', []))}"
    if t == "reset":
        return f"{head}: {', '.join(a.get('fields') or ['모든 필드'])}"
    if t == "uri":
        return f"{head}: {a.get('uri', '')}"
    if t == "launch":
        return f"{head}: {os.path.basename(a.get('file', ''))}"
    first = (a.get("script", "").strip().splitlines() or [""])[0]
    return f"{head}: {first[:40]}"


class CalcOrderDialog(FormDialog):
    """계산이 걸린 칸의 계산 차례(AcroForm CO)."""

    def __init__(self, parent, doc):
        super().__init__("계산 순서", parent)
        self.doc = doc
        self.list = QListWidget()
        for c in P.calc_fields(doc):
            it = QListWidgetItem(c["name"]); it.setData(Qt.ItemDataRole.UserRole, c["xref"])
            self.list.addItem(it)
        self.list.setMinimumHeight(220)
        self.form.addRow(self.list)
        up, down = QPushButton("위로"), QPushButton("아래로")
        up.clicked.connect(lambda: self._move(-1)); down.clicked.connect(lambda: self._move(1))
        row = QHBoxLayout(); row.addWidget(up); row.addWidget(down); row.addStretch(1)
        self.form.addRow(row)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _move(self, d: int):
        r = self.list.currentRow()
        if r < 0 or not 0 <= r + d < self.list.count():
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)

    def order(self) -> list[int]:
        return [self.list.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.list.count())]


# ── 속성 창 ─────────────────────────────────────────────────────
class FieldPropsDialog(QDialog):
    def __init__(self, parent, doc, items: list[tuple[int, int]], on_change: Callable[[list[int]], None] | None = None):
        super().__init__(parent)
        self.doc, self.items = doc, list(items)
        self.on_change = on_change or (lambda pages: None)
        self._snapped = False
        self._loading = True
        self.props = [P.read(doc, pg, x) for pg, x in self.items]
        self.props = [p for p in self.props if p]
        self.p = self.props[0] if self.props else {"type": "text"}
        kinds = {p["type"] for p in self.props}
        self.kind = self.p["type"] if len(kinds) == 1 else ""
        tabs = TABS_BY_TYPE.get(self.kind, COMMON_TABS)
        self.setWindowTitle("양식 칸 속성" if len(self.props) <= 1 else f"양식 칸 속성 · {len(self.props)}칸")
        self.setMinimumSize(560, 440)

        lay = QVBoxLayout(self)
        body = QHBoxLayout()
        self.tabs = QListWidget(); self.tabs.setFixedWidth(120)
        self.stack = QStackedWidget()
        body.addWidget(self.tabs); body.addWidget(self.stack, 1)
        lay.addLayout(body, 1)
        builders = {"general": self._general, "design": self._design, "options": self._options, "actions": self._actions,
                    "format": self._format, "validate": self._validate, "calc": self._calc,
                    "selchange": self._selchange, "signed": self._signed}
        self.tab_keys = []
        for key, label in TAB_NAMES:
            if key not in tabs:
                continue
            page = QWidget()
            builders[key](page)
            self.stack.addWidget(page)
            self.tabs.addItem(label)
            self.tab_keys.append(key)
        self.tabs.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.tabs.setCurrentRow(0)
        foot = QHBoxLayout()
        self.lock = QCheckBox("잠금"); self.lock.setChecked(any(p.get("locked") for p in self.props))
        self.lock.toggled.connect(self._lock)
        close = QPushButton("닫기"); close.setDefault(True); close.clicked.connect(self.accept)
        foot.addWidget(self.lock); foot.addStretch(1); foot.addWidget(close)
        lay.addLayout(foot)
        self._lock_panels(self.lock.isChecked())
        self._loading = False

    # 반영
    def apply(self, changes: dict):
        if self._loading or not self.props:
            return
        if not self._snapped:
            self.doc.snapshot()
            self._snapped = True
        else:
            self.doc.modified = True
        pages = P.write(self.doc, self.items, changes)
        if changes.keys() & {"format", "calc", "options", "export"}:
            pages = sorted(set(pages) | set(C.recalc(self.doc)))
        self.on_change(pages)

    def _lock(self, on: bool):
        self.apply({"locked": on})
        self._lock_panels(on)

    def _lock_panels(self, on: bool):
        self.stack.setEnabled(not on)
        self.tabs.setEnabled(not on)

    @staticmethod
    def _form(page: QWidget) -> QFormLayout:
        f = QFormLayout(page)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        return f

    def _check(self, text: str, key: str, value: bool, invert: bool = False) -> QCheckBox:
        c = QCheckBox(text); c.setChecked(bool(value))
        c.toggled.connect(lambda on: self.apply({key: (not on) if invert else on}))
        return c

    # 일반
    def _general(self, page):
        f = self._form(page)
        self.name = QLineEdit(self.p.get("name", ""))
        self.name.setEnabled(len(self.props) == 1)
        self.name.editingFinished.connect(self._rename)
        f.addRow("이름", self.name)
        tip = QLineEdit(self.p.get("tooltip", ""))
        tip.editingFinished.connect(lambda: self.apply({"tooltip": tip.text()}))
        f.addRow("도구 설명", tip)
        vis = QComboBox(); vis.addItems([l for _, l in P.VISIBILITY])
        vis.setCurrentIndex(next((k for k, (v, _) in enumerate(P.VISIBILITY) if v == self.p.get("visibility")), 0))
        vis.currentIndexChanged.connect(lambda k: self.apply({"visibility": P.VISIBILITY[k][0]}))
        f.addRow("폼 필드", vis)
        f.addRow(self._check("읽기 전용", "readonly", self.p.get("readonly")))
        req = self._check("필수 항목", "required", self.p.get("required"))
        req.setEnabled(not any(p["type"] in ("button", "list") for p in self.props))
        f.addRow(req)

    def _rename(self):
        new = self.name.text().strip()
        if self._loading or not new or new == self.p.get("name"):
            return
        if P.name_conflict(self.doc, self.items, new):
            QMessageBox.warning(self, "이름", MSG_NAME_TAKEN)
            self.name.setText(self.p.get("name", ""))
            return
        if not self._snapped:
            self.doc.snapshot(); self._snapped = True
        pg, x = self.items[0]
        A.rename_field(self.doc, pg, x, new)
        self.p["name"] = new
        self.on_change([pg])

    # 디자인
    def _design(self, page):
        f = self._form(page)
        box = QGroupBox("테두리와 색"); bf = QFormLayout(box)
        bc = ColorButton(self.p.get("border_color"), allow_none=True)
        bc.clicked.connect(lambda: self.apply({"border_color": bc.color}))
        fc = ColorButton(self.p.get("fill_color"), allow_none=True)
        fc.clicked.connect(lambda: self.apply({"fill_color": fc.color}))
        bf.addRow("테두리 색", bc); bf.addRow("채우기 색", fc)
        bw = QComboBox(); bw.addItems([l for _, l in P.BORDER_WIDTHS])
        bw.setCurrentIndex(max(0, min(2, int(self.p.get("border_width", 1)) - 1)))
        bw.currentIndexChanged.connect(lambda k: self.apply({"border_width": P.BORDER_WIDTHS[k][0]}))
        bs = QComboBox(); bs.addItems([l for _, l in P.BORDER_STYLES])
        bs.setCurrentIndex(next((k for k, (v, _) in enumerate(P.BORDER_STYLES) if v == self.p.get("border_style")), 0))
        bs.currentIndexChanged.connect(lambda k: self.apply({"border_style": P.BORDER_STYLES[k][0]}))
        bf.addRow("선 두께", bw); bf.addRow("선 스타일", bs)
        f.addRow(box)
        tbox = QGroupBox("텍스트"); tf = QFormLayout(tbox)
        font = QComboBox(); font.addItems([l for _, l in P.FONTS])
        font.setCurrentIndex(next((k for k, (v, _) in enumerate(P.FONTS) if v == self.p.get("font")), 0))
        font.currentIndexChanged.connect(lambda k: self.apply({"font": P.FONTS[k][0]}))
        font.setEnabled(self.kind not in ("checkbox", "radio", "signature"))
        size = QComboBox(); size.addItems(["자동"] + [f"{s:g}" for s in P.FONT_SIZES[1:]])
        cur = float(self.p.get("fontsize", 0) or 0)
        size.setCurrentIndex(next((k for k, s in enumerate(P.FONT_SIZES) if abs(s - cur) < 0.01), 0))
        size.currentIndexChanged.connect(lambda k: self.apply({"fontsize": P.FONT_SIZES[k]}))
        tc = ColorButton(self.p.get("text_color") or (0, 0, 0))
        tc.clicked.connect(lambda: self.apply({"text_color": tc.color}))
        tf.addRow("글꼴", font); tf.addRow("크기", size); tf.addRow("텍스트 색", tc)
        f.addRow(tbox)

    # 옵션
    def _options(self, page):
        {"text": self._opt_text, "checkbox": self._opt_check, "radio": self._opt_check, "combo": self._opt_choice,
         "list": self._opt_choice, "button": self._opt_button}.get(self.kind, lambda p: None)(page)

    def _opt_text(self, page):
        f = self._form(page)
        al = QComboBox(); al.addItems(["왼쪽", "가운데", "오른쪽"]); al.setCurrentIndex(int(self.p.get("align", 0)) % 3)
        al.currentIndexChanged.connect(lambda k: self.apply({"align": k}))
        f.addRow("맞춤", al)
        dv = QLineEdit(self.p.get("default", "")); dv.editingFinished.connect(lambda: self.apply({"default": dv.text()}))
        f.addRow("기본값", dv)
        self.multiline = self._check("여러 줄", "multiline", self.p.get("multiline"))
        self.multiline.toggled.connect(self._format_allowed)
        f.addRow(self.multiline)
        f.addRow(self._check("긴 텍스트 스크롤", "noscroll", not self.p.get("noscroll"), invert=True))
        f.addRow(self._check("암호", "password", self.p.get("password")))
        f.addRow(self._check("맞춤법 검사", "nospell", not self.p.get("nospell"), invert=True))
        f.addRow(self._check("필드는 파일 선택에 사용됩니다.", "fileselect", self.p.get("fileselect")))
        n = int(self.p.get("maxlen", 0))
        comb = bool(self.p.get("comb"))
        self.lim_on = QCheckBox("제한"); self.lim_on.setChecked(n > 0 and not comb)
        self.lim_n = QSpinBox(); self.lim_n.setRange(0, 9999); self.lim_n.setValue(n if n and not comb else 0)
        self.comb_on = QCheckBox("문자 수"); self.comb_on.setChecked(comb)
        self.comb_n = QSpinBox(); self.comb_n.setRange(0, 9999); self.comb_n.setValue(n if comb else 0)
        for on, num, label in ((self.lim_on, self.lim_n, "설정"), (self.comb_on, self.comb_n, "설정")):
            row = QHBoxLayout(); row.addWidget(on); row.addWidget(num); row.addWidget(QLabel(label)); row.addStretch(1)
            f.addRow(row)
        self.lim_on.toggled.connect(lambda on: self._maxlen("limit", on))
        self.comb_on.toggled.connect(lambda on: self._maxlen("comb", on))
        self.lim_n.valueChanged.connect(lambda v: self.lim_on.isChecked() and self.apply({"maxlen": v}))
        self.comb_n.valueChanged.connect(lambda v: self.comb_on.isChecked() and self.apply({"maxlen": v}))

    def _maxlen(self, which: str, on: bool):
        if self._loading:
            return
        num, other = (self.lim_n, self.comb_on) if which == "limit" else (self.comb_n, self.lim_on)
        self._loading = True
        if on and num.value() == 0:
            num.setValue(1)
        if on and other.isChecked():
            other.setChecked(False)
        self._loading = False
        self.apply({"maxlen": num.value() if on else 0, "comb": on and which == "comb"})

    def _opt_check(self, page):
        f = self._form(page)
        st = QComboBox(); st.addItems([l for _, l, _ in P.CHECK_STYLES])
        st.setCurrentIndex(next((k for k, (v, _, _) in enumerate(P.CHECK_STYLES) if v == self.p.get("style")), 0))
        st.currentIndexChanged.connect(lambda k: self.apply({"style": P.CHECK_STYLES[k][0]}))
        radio = self.kind == "radio"
        f.addRow("단추 스타일" if radio else "확인란 스타일", st)
        ex = QLineEdit(self.p.get("export", "Yes")); ex.setEnabled(len(self.props) == 1)
        ex.editingFinished.connect(lambda: ex.text().strip() and self.apply({"export": ex.text().strip()}))
        f.addRow("라디오 버튼 선택" if radio else "값 내보내기", ex)
        f.addRow(self._check("버튼은 기본적으로 선택되어 있습니다." if radio else "확인란은 기본적으로 선택되어 있습니다.",
                             "default_on", self.p.get("default_on")))
        if radio:
            f.addRow(self._check("동일한 이름과 버튼 선택을 가진 버튼이 동시에 선택됩니다.", "unison", self.p.get("unison")))

    def _opt_choice(self, page):
        f = self._form(page)
        self.opts = list(self.p.get("options", []))
        self.item = QLineEdit(); self.export = QLineEdit()
        add = QPushButton("추가"); add.clicked.connect(self._opt_add)
        f.addRow("아이템", self.item)
        row = QHBoxLayout(); row.addWidget(self.export, 1); row.addWidget(add)
        f.addRow("값 내보내기", row)
        self.olist = QListWidget(); self.olist.setMinimumHeight(120)
        f.addRow("아이템 리스트", self.olist)
        btns = QHBoxLayout()
        for text, fn in (("삭제", self._opt_del), ("위로", lambda: self._opt_move(-1)), ("아래로", lambda: self._opt_move(1))):
            b = QPushButton(text); b.clicked.connect(fn); btns.addWidget(b)
        btns.addStretch(1)
        f.addRow(btns)
        f.addRow(self._check("항목 정렬", "sort", self.p.get("sort")))
        if self.kind == "combo":
            f.addRow(self._check("사용자가 사용자 정의 텍스트를 입력하도록 허용", "edit", self.p.get("edit")))
        f.addRow(self._check("선택한 값을 즉시 입력", "commit", self.p.get("commit")))
        if self.kind == "list":
            f.addRow(self._check("다중 선택", "multi", self.p.get("multi")))
        self._opt_fill(self.p.get("default", ""))
        self.olist.currentRowChanged.connect(self._opt_pick)

    def _opt_fill(self, default: str = ""):
        self._loading, was = True, self._loading
        self.olist.clear()
        for e, n in self.opts:
            self.olist.addItem(n if e == n else f"{n}  ({e})")
        k = next((k for k, (e, _) in enumerate(self.opts) if e == default), -1)
        self.olist.setCurrentRow(k)
        self._loading = was

    def _opt_apply(self):
        k = self.olist.currentRow()
        ch = {"options": list(self.opts)}
        if 0 <= k < len(self.opts):
            ch["default"] = self.opts[k][0]
        self.apply(ch)

    def _opt_add(self):
        n = self.item.text().strip()
        if not n:
            return
        e = self.export.text().strip() or n
        self.opts.append((e, n))
        self.item.clear(); self.export.clear()
        self._opt_fill(); self.olist.setCurrentRow(len(self.opts) - 1)
        self._opt_apply()

    def _opt_del(self):
        k = self.olist.currentRow()
        if 0 <= k < len(self.opts):
            del self.opts[k]
            self._opt_fill(); self._opt_apply()

    def _opt_move(self, d: int):
        k = self.olist.currentRow()
        if 0 <= k < len(self.opts) and 0 <= k + d < len(self.opts):
            self.opts[k], self.opts[k + d] = self.opts[k + d], self.opts[k]
            self._opt_fill(); self.olist.setCurrentRow(k + d); self._opt_apply()

    def _opt_pick(self, k: int):
        if 0 <= k < len(self.opts):
            self.apply({"default": self.opts[k][0]})

    def _opt_button(self, page):
        f = self._form(page)
        lay = QComboBox(); lay.addItems(list(P.BUTTON_LAYOUTS)); lay.setCurrentIndex(int(self.p.get("layout", 0)) % 7)
        lay.currentIndexChanged.connect(lambda k: self.apply({"layout": k}))
        f.addRow("레이아웃", lay)
        hl = QComboBox(); hl.addItems([l for _, l in P.HIGHLIGHTS])
        hl.setCurrentIndex(next((k for k, (v, _) in enumerate(P.HIGHLIGHTS) if v == self.p.get("highlight")), 0))
        hl.currentIndexChanged.connect(lambda k: self.apply({"highlight": P.HIGHLIGHTS[k][0]}))
        f.addRow("동작", hl)
        self.caps = dict(self.p.get("captions", {"N": "", "D": "", "R": ""}))
        self.states = QListWidget(); self.states.addItems(["위로", "아래로", "롤오버"]); self.states.setMaximumHeight(80)
        self.cap = QLineEdit()
        f.addRow("상태", self.states)
        f.addRow("라벨", self.cap)
        keys = ("N", "D", "R")
        self.states.currentRowChanged.connect(lambda k: self.cap.setText(self.caps.get(keys[k], "")) if k >= 0 else None)
        self.cap.editingFinished.connect(self._caption)
        self.icons = dict(self.p.get("icons", {"N": False, "D": False, "R": False}))
        self.icon_state = QLabel()
        self.icon_pick = QPushButton("아이콘 선택"); self.icon_pick.setAutoDefault(False)
        self.icon_clear = QPushButton("지우기"); self.icon_clear.setAutoDefault(False)
        self.icon_pick.clicked.connect(self._icon_pick)
        self.icon_clear.clicked.connect(lambda: self._icon_set(""))
        row = QHBoxLayout(); row.addWidget(self.icon_state, 1); row.addWidget(self.icon_pick); row.addWidget(self.icon_clear)
        f.addRow("아이콘", row)
        self.states.currentRowChanged.connect(lambda _k: self._icon_show())
        lay.currentIndexChanged.connect(lambda _k: self._icon_show())
        self.btn_layout = lay
        self.states.setCurrentRow(0)
        self._icon_show()

    def _icon_key(self) -> str:
        return ("N", "D", "R")[max(0, self.states.currentRow())]

    def _icon_show(self):
        """레이아웃이 「라벨만」 이면 아이콘 단추를 끈다(알PDF 대로)."""
        on = self.btn_layout.currentIndex() != 0
        has = bool(self.icons.get(self._icon_key()))
        self.icon_state.setText("그림 있음" if has else "없음")
        self.icon_pick.setEnabled(on)
        self.icon_clear.setEnabled(on and has)

    def _icon_pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "아이콘 선택", "", P.ICON_FILTER)
        if not path:
            return
        try:
            import fitz
            fitz.Pixmap(path)
        except Exception:
            QMessageBox.warning(self, "아이콘 선택", P.ICON_DAMAGED)
            return
        self._icon_set(path)

    def _icon_set(self, path: str):
        k = self._icon_key()
        self.apply({"icon": (k, path)})
        self.icons[k] = bool(path)
        self._icon_show()

    def _caption(self):
        k = self.states.currentRow()
        if k < 0:
            return
        self.caps[("N", "D", "R")[k]] = self.cap.text()
        self.apply({"captions": dict(self.caps)})

    # 동작
    def _actions(self, page):
        f = self._form(page)
        self.acts = {k: list(v) for k, v in self.p.get("actions", {}).items()}
        self.trig = QComboBox(); self.trig.addItems([l for _, l in P.TRIGGERS])
        self.act_kind = QComboBox(); self.act_kind.addItems([l for _, l in P.ACTIONS])
        add = QPushButton("추가"); add.clicked.connect(self._act_add)
        f.addRow("트리거 선택", self.trig)
        row = QHBoxLayout(); row.addWidget(self.act_kind, 1); row.addWidget(add)
        f.addRow("작업 선택", row)
        self.alist = QListWidget(); self.alist.setMinimumHeight(140)
        f.addRow(self.alist)
        btns = QHBoxLayout()
        for text, fn in (("위로", lambda: self._act_move(-1)), ("아래로", lambda: self._act_move(1)),
                         ("편집", self._act_edit), ("삭제", self._act_del)):
            b = QPushButton(text); b.clicked.connect(fn); btns.addWidget(b)
        btns.addStretch(1)
        f.addRow(btns)
        self.alist.itemDoubleClicked.connect(lambda _: self._act_edit())
        self._act_fill()

    def _act_fill(self, select: tuple[str, int] | None = None):
        self.alist.clear()
        for key, label in P.TRIGGERS:
            for i, a in enumerate(self.acts.get(key, [])):
                it = QListWidgetItem(f"{label} · {_describe(a)}"); it.setData(Qt.ItemDataRole.UserRole, (key, i))
                self.alist.addItem(it)
                if select == (key, i):
                    self.alist.setCurrentItem(it)

    def _act_cur(self):
        it = self.alist.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _act_add(self):
        kind = P.ACTIONS[self.act_kind.currentIndex()][0]
        dlg = ActionDialog(self, self.doc, {"type": kind})
        if not dlg.exec():
            return
        key = P.TRIGGERS[self.trig.currentIndex()][0]
        self.acts.setdefault(key, []).append(dlg.values())
        self._act_fill((key, len(self.acts[key]) - 1))
        self.apply({"actions": self.acts})

    def _act_edit(self):
        cur = self._act_cur()
        if not cur:
            return
        key, i = cur
        dlg = ActionDialog(self, self.doc, self.acts[key][i])
        if dlg.exec():
            self.acts[key][i] = dlg.values()
            self._act_fill(cur)
            self.apply({"actions": self.acts})

    def _act_del(self):
        cur = self._act_cur()
        if not cur:
            return
        key, i = cur
        del self.acts[key][i]
        if not self.acts[key]:
            del self.acts[key]
        self._act_fill()
        self.apply({"actions": self.acts})

    def _act_move(self, d: int):
        cur = self._act_cur()
        if not cur:
            return
        key, i = cur
        lst = self.acts[key]
        if 0 <= i + d < len(lst):
            lst[i], lst[i + d] = lst[i + d], lst[i]
            self._act_fill((key, i + d))
            self.apply({"actions": self.acts})

    # 서식
    def _format(self, page):
        v = QVBoxLayout(page)
        fmt = dict(self.p.get("format", {"kind": "none"}))
        kinds = (("none", "없음"), ("number", "번호"), ("percent", "백분율"), ("date", "날짜"), ("time", "시간"),
                 ("special", "특수"), ("custom", "사용자 지정"))
        self.fmt_kind = QComboBox(); self.fmt_kind.addItems([l for _, l in kinds])
        row = QFormLayout(); row.addRow("형식 카테고리 선택", self.fmt_kind); v.addLayout(row)
        self.fmt_stack = QStackedWidget(); v.addWidget(self.fmt_stack); v.addStretch(1)
        self.sample = QLabel(); v.addWidget(self.sample)
        self.fmt_kinds = [k for k, _ in kinds]
        w = {}
        # 번호·백분율
        for k in ("number", "percent"):
            pg = QWidget(); f = QFormLayout(pg)
            dec = QSpinBox(); dec.setRange(0, 10); dec.setValue(int(fmt.get("dec", 2)) if fmt.get("kind") == k else 2)
            sep = QComboBox(); sep.addItems(list(C.SEP_STYLES))
            sep.setCurrentIndex(int(fmt.get("sep", 0)) if fmt.get("kind") == k else 0)
            f.addRow("소수 자릿수", dec); f.addRow("구분 기호 스타일", sep)
            w[k] = {"dec": dec, "sep": sep}
            if k == "number":
                cur = QComboBox(); cur.addItems(["없음"] + list(C.CURRENCIES[1:]))
                cur.setCurrentIndex(C.CURRENCIES.index(fmt.get("currency", "")) if fmt.get("currency", "") in C.CURRENCIES else 0)
                neg = int(fmt.get("neg", 0)) if fmt.get("kind") == k else 0
                par, red = QCheckBox("괄호 표시"), QCheckBox("빨간색 텍스트 사용")
                par.setChecked(neg in (2, 3)); red.setChecked(neg in (1, 3))
                f.addRow("통화 기호", cur); f.addRow("음수 스타일", par); f.addRow("", red)
                w[k].update(cur=cur, par=par, red=red)
            self.fmt_stack.addWidget(pg)
        # 날짜·시간
        for k, lst in (("date", C.DATE_FORMATS), ("time", C.TIME_FORMATS)):
            pg = QWidget(); f = QFormLayout(pg)
            box = QComboBox(); box.addItems(list(lst) + ["사용자 정의"])
            custom = QLineEdit()
            have = fmt.get("fmt", "") if fmt.get("kind") == k else (lst[12] if k == "date" else lst[0])
            if have in lst:
                box.setCurrentIndex(lst.index(have))
            else:
                box.setCurrentIndex(len(lst)); custom.setText(have)
            custom.setEnabled(box.currentIndex() == len(lst))
            box.currentIndexChanged.connect(lambda i, c=custom, n=len(lst): c.setEnabled(i == n))
            f.addRow("서식", box); f.addRow("사용자 정의", custom)
            w[k] = {"box": box, "custom": custom, "list": lst}
            self.fmt_stack.addWidget(pg)
        # 특수
        pg = QWidget(); f = QFormLayout(pg)
        sb = QComboBox(); sb.addItems(list(C.SPECIALS) + ["임의 마스크"])
        sb.setCurrentIndex(int(fmt.get("which", 0)) if fmt.get("kind") == "special" else 0)
        mask = QLineEdit(fmt.get("mask", "")); mask.setEnabled(sb.currentIndex() == 4)
        sb.currentIndexChanged.connect(lambda i: mask.setEnabled(i == 4))
        f.addRow("특수", sb); f.addRow("마스크", mask)
        w["special"] = {"box": sb, "mask": mask}
        self.fmt_stack.addWidget(pg)
        # 사용자 지정
        pg = QWidget(); f = QFormLayout(pg)
        self.fmt_scripts = {"format": fmt.get("format", "") if fmt.get("kind") == "custom" else "",
                            "keystroke": fmt.get("keystroke", "") if fmt.get("kind") == "custom" else ""}
        for key, label in (("format", "사용자 정의 형식 스크립트"), ("keystroke", "키 입력 스크립트")):
            b = QPushButton("편집"); b.clicked.connect(lambda _=False, kk=key: self._fmt_script(kk))
            f.addRow(label, b)
        self.fmt_stack.addWidget(pg)
        self.fmt_stack.insertWidget(0, QWidget())  # 없음
        self.fmt_w = w
        self.fmt_kind.setCurrentIndex(self.fmt_kinds.index(fmt.get("kind", "none")) if fmt.get("kind") in self.fmt_kinds else 0)
        self.fmt_stack.setCurrentIndex(self.fmt_kind.currentIndex())
        self.fmt_kind.currentIndexChanged.connect(lambda k: (self.fmt_stack.setCurrentIndex(k), self._fmt_apply()))
        for d in w.values():
            for ctl in d.values():
                if isinstance(ctl, QSpinBox):
                    ctl.valueChanged.connect(self._fmt_apply)
                elif isinstance(ctl, QComboBox):
                    ctl.currentIndexChanged.connect(self._fmt_apply)
                elif isinstance(ctl, QCheckBox):
                    ctl.toggled.connect(self._fmt_apply)
                elif isinstance(ctl, QLineEdit):
                    ctl.editingFinished.connect(self._fmt_apply)
        self._fmt_sample()
        self._format_allowed(bool(self.p.get("multiline")))

    def _format_allowed(self, multiline: bool):
        if hasattr(self, "fmt_kind"):
            self.fmt_kind.setEnabled(not multiline)
            self.fmt_stack.setEnabled(not multiline)

    def fmt_value(self) -> dict:
        k = self.fmt_kinds[self.fmt_kind.currentIndex()]
        w = self.fmt_w
        if k in ("number", "percent"):
            d = {"kind": k, "dec": w[k]["dec"].value(), "sep": w[k]["sep"].currentIndex()}
            if k == "number":
                cur = C.CURRENCIES[w[k]["cur"].currentIndex()]
                d.update(currency=cur, neg=(2 if w[k]["par"].isChecked() else 0) + (1 if w[k]["red"].isChecked() else 0))
            return d
        if k in ("date", "time"):
            box, lst = w[k]["box"], w[k]["list"]
            f = lst[box.currentIndex()] if box.currentIndex() < len(lst) else w[k]["custom"].text().strip()
            return {"kind": k, "fmt": f or lst[0]}
        if k == "special":
            return {"kind": k, "which": w[k]["box"].currentIndex(), "mask": w[k]["mask"].text()}
        if k == "custom":
            return {"kind": k, **self.fmt_scripts}
        return {"kind": "none"}

    def _fmt_sample(self):
        d = self.fmt_value()
        ex = {"number": "-4123.03", "percent": "12.3456", "date": "2020-08-20", "time": "13:45:30",
              "special": "1234567890"}.get(d["kind"])
        self.sample.setText(f"예시: {C.format_value(d, ex)}" if ex else "")

    def _fmt_apply(self, *_):
        self._fmt_sample()
        self.apply({"format": self.fmt_value()})

    def _fmt_script(self, key: str):
        dlg = ScriptDialog(self, self.fmt_scripts.get(key, ""))
        if dlg.exec():
            self.fmt_scripts[key] = dlg.text()
            self._fmt_apply()

    # 유효성 검사
    def _validate(self, page):
        f = self._form(page)
        val = self.p.get("validate", {"kind": "none"})
        self.val_g = QButtonGroup(self)
        r0 = QRadioButton("필드 값이 확인되지 않았습니다")
        r1 = QRadioButton("필드 값이 범위 내에 있습니다:")
        r2 = QRadioButton("사용자 지정 유효성 검사 스크립트를 실행합니다.")
        for k, r in enumerate((r0, r1, r2)):
            self.val_g.addButton(r, k)
        self.vmin, self.vmax = QLineEdit(), QLineEdit()
        if val.get("kind") == "range":
            self.vmin.setText("" if val.get("min") is None else C._num_text(val["min"]))
            self.vmax.setText("" if val.get("max") is None else C._num_text(val["max"]))
        self.val_script = val.get("script", "") if val.get("kind") == "custom" else ""
        {"range": r1, "custom": r2}.get(val.get("kind"), r0).setChecked(True)
        f.addRow(r0); f.addRow(r1)
        row = QHBoxLayout(); row.addWidget(QLabel("최소")); row.addWidget(self.vmin)
        row.addWidget(QLabel("최대")); row.addWidget(self.vmax)
        f.addRow(row)
        b = QPushButton("편집"); b.clicked.connect(self._val_edit)
        row2 = QHBoxLayout(); row2.addWidget(r2); row2.addWidget(b); row2.addStretch(1)
        f.addRow(row2)
        self.val_g.idToggled.connect(lambda i, on: on and self._val_apply())
        self.vmin.editingFinished.connect(self._val_apply); self.vmax.editingFinished.connect(self._val_apply)

    def _val_edit(self):
        dlg = ScriptDialog(self, self.val_script)
        if dlg.exec():
            self.val_script = dlg.text()
            self.val_g.button(2).setChecked(True)
            self._val_apply()

    def _val_apply(self):
        k = self.val_g.checkedId()
        if k == 1:
            lo, hi = C.to_number(self.vmin.text()), C.to_number(self.vmax.text())
            self.apply({"validate": {"kind": "range", "min": lo, "max": hi}})
        elif k == 2:
            self.apply({"validate": {"kind": "custom", "script": self.val_script}})
        else:
            self.apply({"validate": {"kind": "none"}})

    # 계산
    def _calc(self, page):
        f = self._form(page)
        c = self.p.get("calc", {"kind": "none"})
        self.calc_g = QButtonGroup(self)
        rs = [QRadioButton("값이 계산되지 않음"), QRadioButton("값은"), QRadioButton("단순화된 필드 표기법"),
              QRadioButton("사용자 지정 계산 스크립트 실행")]
        for k, r in enumerate(rs):
            self.calc_g.addButton(r, k)
        self.calc_op = QComboBox(); self.calc_op.addItems([l for _, l in C.CALC_OPS])
        ops = [o for o, _ in C.CALC_OPS]
        self.calc_op.setCurrentIndex(ops.index(c.get("op", "SUM")) if c.get("op") in ops else 0)
        self.calc_fields = list(c.get("fields", []))
        self.calc_label = QLineEdit(", ".join(self.calc_fields)); self.calc_label.setReadOnly(True)
        pick = QPushButton("고르기"); pick.clicked.connect(self._calc_pick)
        self.calc_expr = c.get("expr", "")
        self.calc_script = c.get("script", "") if c.get("kind") == "custom" else ""
        rs[{"simple": 1, "notation": 2, "custom": 3}.get(c.get("kind"), 0)].setChecked(True)
        f.addRow(rs[0])
        row = QHBoxLayout(); row.addWidget(rs[1]); row.addWidget(self.calc_op); row.addWidget(QLabel("다음 필드 중:"))
        row.addStretch(1); f.addRow(row)
        row = QHBoxLayout(); row.addWidget(self.calc_label, 1); row.addWidget(pick); f.addRow(row)
        for k, fn in ((2, self._calc_expr_edit), (3, self._calc_script_edit)):
            b = QPushButton("편집"); b.clicked.connect(fn)
            row = QHBoxLayout(); row.addWidget(rs[k]); row.addWidget(b); row.addStretch(1); f.addRow(row)
        self.calc_g.idToggled.connect(lambda i, on: on and self._calc_apply())
        self.calc_op.currentIndexChanged.connect(lambda _: self.calc_g.checkedId() == 1 and self._calc_apply())

    def _calc_pick(self):
        me = {p.get("name") for p in self.props}
        names = [n for n in all_field_names(self.doc) if n not in me]
        dlg = FieldPickDialog(self, names, self.calc_fields)
        if dlg.exec():
            self.calc_fields = dlg.values()
            self.calc_label.setText(", ".join(self.calc_fields))
            if self.calc_g.checkedId() == 1:
                self._calc_apply()
            else:
                self.calc_g.button(1).setChecked(True)

    def _calc_expr_edit(self):
        dlg = ScriptDialog(self, self.calc_expr, "단순화된 필드 표기법")
        if dlg.exec():
            self.calc_expr = dlg.text().strip()
            if self.calc_g.checkedId() == 2:
                self._calc_apply()
            else:
                self.calc_g.button(2).setChecked(True)

    def _calc_script_edit(self):
        dlg = ScriptDialog(self, self.calc_script)
        if dlg.exec():
            self.calc_script = dlg.text()
            if self.calc_g.checkedId() == 3:
                self._calc_apply()
            else:
                self.calc_g.button(3).setChecked(True)

    def _calc_apply(self):
        k = self.calc_g.checkedId()
        if k == 1:
            d = {"kind": "simple", "op": C.CALC_OPS[self.calc_op.currentIndex()][0], "fields": self.calc_fields}
            if not self.calc_fields:
                d = {"kind": "none"}
        elif k == 2:
            d = {"kind": "notation", "expr": self.calc_expr} if self.calc_expr else {"kind": "none"}
        elif k == 3:
            d = {"kind": "custom", "script": self.calc_script} if self.calc_script.strip() else {"kind": "none"}
        else:
            d = {"kind": "none"}
        self.apply({"calc": d})

    # 선택 변경
    def _selchange(self, page):
        f = self._form(page)
        f.addRow(QLabel("목록 상자 선택이 변경되는 경우:"))
        self.sel_script = self.p.get("selchange", "")
        self.sel_g = QButtonGroup(self)
        r0, r1 = QRadioButton("아무 작업도 하지 않음"), QRadioButton("이 스크립트를 실행하십시오.")
        self.sel_g.addButton(r0, 0); self.sel_g.addButton(r1, 1)
        (r1 if self.sel_script else r0).setChecked(True)
        b = QPushButton("편집"); b.clicked.connect(self._sel_edit)
        f.addRow(r0)
        row = QHBoxLayout(); row.addWidget(r1); row.addWidget(b); row.addStretch(1); f.addRow(row)
        self.sel_g.idToggled.connect(lambda i, on: on and self.apply({"selchange": self.sel_script if i == 1 else ""}))

    def _sel_edit(self):
        dlg = ScriptDialog(self, self.sel_script)
        if dlg.exec():
            self.sel_script = dlg.text()
            if self.sel_g.checkedId() == 1:
                self.apply({"selchange": self.sel_script})
            else:
                self.sel_g.button(1).setChecked(True)

    # 서명됨
    def _signed(self, page):
        f = self._form(page)
        sl = self.p.get("sig_lock", {"action": "none", "fields": []})
        self.sig_g = QButtonGroup(self)
        r0, r1 = QRadioButton("동작 없음"), QRadioButton("읽기 전용으로 표시:")
        self.sig_g.addButton(r0, 0); self.sig_g.addButton(r1, 1)
        self.sig_how = QComboBox(); self.sig_how.addItems([l for _, l in P.SIG_LOCKS[1:]])
        hows = [k for k, _ in P.SIG_LOCKS[1:]]
        self.sig_how.setCurrentIndex(hows.index(sl["action"]) if sl.get("action") in hows else 0)
        self.sig_fields = list(sl.get("fields", []))
        pick = QPushButton("선택"); pick.clicked.connect(self._sig_pick)
        (r1 if sl.get("action", "none") != "none" else r0).setChecked(True)
        f.addRow(r0)
        row = QHBoxLayout(); row.addWidget(r1); row.addWidget(self.sig_how); row.addWidget(pick); row.addStretch(1)
        f.addRow(row)
        self.sig_g.idToggled.connect(lambda i, on: on and self._sig_apply())
        self.sig_how.currentIndexChanged.connect(lambda _: self.sig_g.checkedId() == 1 and self._sig_apply())

    def _sig_pick(self):
        dlg = FieldPickDialog(self, all_field_names(self.doc), self.sig_fields)
        if dlg.exec():
            self.sig_fields = dlg.values()
            self._sig_apply()

    def _sig_apply(self):
        if self.sig_g.checkedId() != 1:
            self.apply({"sig_lock": {"action": "none"}})
            return
        how = P.SIG_LOCKS[1:][self.sig_how.currentIndex()][0]
        fields = self.sig_fields
        if how == "Include" and not fields:
            fields = [self.p.get("name", "")]
        self.apply({"sig_lock": {"action": how, "fields": fields}})
