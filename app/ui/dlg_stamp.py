"""스탬프 메뉴와 사용자 정의 스탬프 창(사용자 지정 만들기 · 동적 만들기 · 스탬프 관리 · ID 편집).

창은 값을 모아 engine/stamps.py 의 저장소(stamps.json)에 넣고, 찍기는 뷰어가 한다(viewer.props["stamp_spec"]).
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..engine import fonts as F
from ..engine import stamps as S
from .dialogs import _buttons

PREVIEW_W, PREVIEW_H = 260, 110


def _pixmap(png: bytes) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(png, "PNG")
    return pm


def _icon(spec: dict, author: str) -> QIcon | None:
    try:
        return QIcon(_pixmap(S.preview_png(spec, 32, author=author)))
    except Exception:  # noqa: BLE001  미리보기를 못 그려도 이름은 보인다
        return None


def fill_stamp_menu(m: QMenu, pick, action) -> None:
    """스탬프 메뉴(이름만): 묶음 다섯 · 스탬프 만들기 ▸ · 스탬프 관리 · ID 편집. 미리보기는 관리 창에서 본다.
    pick(spec) 은 스탬프를 고를 때, action(key) 는 아래 항목(stamp_create 등)을 누를 때 부른다."""
    st = S.store()
    for g, name in S.GROUPS:
        sub = m.addMenu(name)
        if g == "custom":
            items = st.custom()
            if not items:
                a = sub.addAction("저장한 스탬프 없음"); a.setEnabled(False)
            cats: dict[str, list[dict]] = {}
            for e in items:
                cats.setdefault(e.get("category", ""), []).append(e)
            for cat in sorted(cats):
                if cat:
                    sub.addSection(cat)
                for e in cats[cat]:
                    sub.addAction(e.get("name") or "스탬프", lambda s={"group": "custom", "id": e["id"]}: pick(s))
            continue
        for sid, title, text, *_ in S.BUILTIN[g]:
            sub.addAction(f"{title} {text}".strip(), lambda s={"group": g, "id": sid}: pick(s))
    m.addSeparator()
    mk = m.addMenu("스탬프 만들기")
    mk.addAction("사용자 지정 스탬프 만들기", lambda: action("stamp_create"))
    mk.addAction("사용자 지정 동적 스탬프 만들기", lambda: action("stamp_create_dyn"))
    m.addAction("스탬프 관리", lambda: action("stamp_manage"))
    m.addAction("ID 편집", lambda: action("stamp_identity"))


class _Preview(QLabel):
    def __init__(self):
        super().__init__()
        self.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#ffffff; border:1px solid #c8c8c8;")

    def show_png(self, png: bytes | None, msg: str = ""):
        if not png:
            self.setPixmap(QPixmap()); self.setText(msg)
            return
        pm = _pixmap(png).scaled(PREVIEW_W - 12, PREVIEW_H - 12, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pm)


def _size_row(w: float, h: float) -> tuple[QWidget, QDoubleSpinBox, QDoubleSpinBox]:
    box = QWidget(); hl = QHBoxLayout(box); hl.setContentsMargins(0, 0, 0, 0)
    ws, hs = QDoubleSpinBox(), QDoubleSpinBox()
    for sp, v in ((ws, w), (hs, h)):
        sp.setRange(5, 2000); sp.setDecimals(0); sp.setSuffix(" pt"); sp.setValue(v)
    hl.addWidget(QLabel("너비")); hl.addWidget(ws); hl.addWidget(QLabel("높이")); hl.addWidget(hs); hl.addStretch(1)
    return box, ws, hs


def _category_combo(st: S.StampStore, value: str) -> QComboBox:
    c = QComboBox(); c.setEditable(True); c.addItems(st.categories()); c.setCurrentText(value)
    c.lineEdit().setPlaceholderText("범주 입력")
    return c


def _date_combo(value: str) -> QComboBox:
    c = QComboBox()
    for label, fmt in S.DATE_FORMATS:
        c.addItem(label, fmt)
    c.setCurrentIndex(max(0, c.findData(value)))
    return c


class CustomStampDialog(QDialog):
    """사용자 지정 스탬프(그림·PDF 한 쪽). 확인하면 저장소에 넣는다(self.saved_id)."""

    def __init__(self, parent, entry: dict | None = None, st: S.StampStore | None = None):
        super().__init__(parent)
        self.setWindowTitle("스탬프 편집")
        self.st = st or S.store()
        self.entry = dict(entry or {"type": "image", "name": "New Stamp"})
        self.saved_id: str | None = None
        self._png: bytes | None = None
        if entry and entry.get("file") and os.path.exists(self.st.image_path(entry["file"])):
            with open(self.st.image_path(entry["file"]), "rb") as fp:
                self._png = fp.read()
        lay = QVBoxLayout(self)
        f = QFormLayout()
        frow = QWidget(); fl = QHBoxLayout(frow); fl.setContentsMargins(0, 0, 0, 0)
        self.file = QLineEdit(entry.get("source", "") if entry else ""); self.file.setReadOnly(True)
        self.file.setPlaceholderText("이미지·PDF 파일")
        b = QPushButton("찾아보기"); b.clicked.connect(self._browse)
        fl.addWidget(self.file, 1); fl.addWidget(b)
        f.addRow("파일", frow)
        self.page = QSpinBox(); self.page.setRange(1, 1); self.page.setEnabled(False)
        self.page.valueChanged.connect(self._load)
        f.addRow("페이지", self.page)
        self.name = QLineEdit(self.entry.get("name", "New Stamp")); f.addRow("이름", self.name)
        self.category = _category_combo(self.st, self.entry.get("category", "")); f.addRow("범주", self.category)
        self.date = _date_combo(self.entry.get("date_fmt", "")); f.addRow("날짜", self.date)
        box, self.w, self.h = _size_row(self.entry.get("width") or 120, self.entry.get("height") or 60)
        f.addRow("크기", box)
        lay.addLayout(f)
        self.preview = _Preview(); lay.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.bb = _buttons(self, "저장")
        lay.addWidget(self.bb)
        self.date.currentIndexChanged.connect(self._show)
        self.w.valueChanged.connect(self._show); self.h.valueChanged.connect(self._show)
        self._show()

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "스탬프로 쓸 파일", "",
                                           "이미지·PDF (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *.pdf)")
        if not p:
            return
        self.file.setText(p)
        if p.lower().endswith(".pdf"):
            try:
                import fitz
                with fitz.open(p) as src:
                    n = src.page_count
            except Exception:  # noqa: BLE001
                QMessageBox.warning(self, "스탬프 편집", "이미지 형식이 지원되지 않습니다.")
                return
            self.page.blockSignals(True); self.page.setRange(1, max(1, n)); self.page.setValue(1)
            self.page.blockSignals(False); self.page.setEnabled(n > 1)
        else:
            self.page.setRange(1, 1); self.page.setEnabled(False)
        self._load(fit=True)

    def _load(self, *_a, fit: bool = False):
        p = self.file.text()
        if not p:
            return
        try:
            png = S.pdf_page_png(p, self.page.value() - 1) if p.lower().endswith(".pdf") else S.image_png(p)
        except (ValueError, RuntimeError):
            QMessageBox.warning(self, "스탬프 편집", "이미지 형식이 지원되지 않습니다.")
            return
        self._png = png
        if fit or not self.entry.get("width"):
            img = QImage.fromData(png)
            if img.width() and img.height():
                h = 60.0
                self.w.blockSignals(True); self.h.blockSignals(True)
                self.w.setValue(round(h * img.width() / img.height())); self.h.setValue(h)
                self.w.blockSignals(False); self.h.blockSignals(False)
        self._show()

    def _face(self) -> dict:
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "pdfmetry_stamp_preview.png")
        with open(path, "wb") as fp:
            fp.write(self._png or b"")
        lines = [(S._date(self.date.currentData(), None), 0.5)] if self.date.currentData() else []
        return {"shape": "none", "color": (0, 0, 0), "lines": lines, "image": path, "font": "", "fontsize": 0,
                "vertical": False, "width": self.w.value(), "height": self.h.value()}

    def _show(self):
        if not self._png:
            self.preview.show_png(None, "파일을 고르세요")
            self.bb.buttons()[0].setEnabled(False)
            return
        self.bb.buttons()[0].setEnabled(True)
        fc = self._face()
        self.preview.show_png(S.render_png(fc, fc["width"], fc["height"], px=400))

    def accept(self):
        if not self._png:
            return
        e = dict(self.entry, type="image", name=self.name.text().strip() or "New Stamp",
                 category=self.category.currentText().strip(), date_fmt=self.date.currentData(),
                 width=self.w.value(), height=self.h.value(), source=self.file.text())
        self.saved_id = self.st.put_custom(e, self._png)
        super().accept()


class DynamicStampDialog(QDialog):
    """사용자 지정 동적 스탬프(꼴·색·글자·날짜·신원). 찍을 때 날짜와 이름을 굽는다."""

    def __init__(self, parent, author: str, entry: dict | None = None, st: S.StampStore | None = None,
                 identity_edit=None):
        super().__init__(parent)
        self.setWindowTitle("스탬프 편집")
        self.st = st or S.store()
        self.author = author
        self.identity_edit = identity_edit
        e = self.entry = dict(entry or {"type": "dynamic", "name": "New Stamp", "style": "rect", "color": "red",
                                        "text": "Text here", "date_fmt": S.DATE_FORMATS[1][1], "identity": "name",
                                        "font": "", "fontsize": 0, "vertical": False})
        self.saved_id: str | None = None
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.name = QLineEdit(e.get("name", "New Stamp")); f.addRow("이름", self.name)
        self.category = _category_combo(self.st, e.get("category", "")); f.addRow("범주", self.category)
        self.styles = QButtonGroup(self)
        srow = QWidget(); sl = QHBoxLayout(srow); sl.setContentsMargins(0, 0, 0, 0)
        for k, (key, label) in enumerate(S.STYLES):
            b = QPushButton(label); b.setCheckable(True); b.setChecked(key == e.get("style", "rect"))
            self.styles.addButton(b, k); sl.addWidget(b)
        f.addRow("모양", srow)
        self.colors = QButtonGroup(self)
        crow = QWidget(); cl = QHBoxLayout(crow); cl.setContentsMargins(0, 0, 0, 0)
        for k, (key, (label, rgb)) in enumerate(S.COLORS.items()):
            b = QPushButton(label); b.setCheckable(True); b.setChecked(key == e.get("color", "red"))
            r, g, bl = (int(x * 255) for x in rgb)
            b.setStyleSheet(f"QPushButton {{ color: rgb({r},{g},{bl}); font-weight: bold; }}")
            self.colors.addButton(b, k); cl.addWidget(b)
        cl.addStretch(1)
        f.addRow("색", crow)
        self.text = QLineEdit(e.get("text", "Text here")); f.addRow("글자", self.text)
        self.date = _date_combo(e.get("date_fmt", "")); f.addRow("날짜", self.date)
        irow = QWidget(); il = QHBoxLayout(irow); il.setContentsMargins(0, 0, 0, 0)
        self.identity = QComboBox()
        for key, label in S.IDENTITIES:
            self.identity.addItem(label, key)
        self.identity.setCurrentIndex(max(0, self.identity.findData(e.get("identity", "name"))))
        ib = QPushButton("ID 편집"); ib.clicked.connect(self._edit_identity)
        il.addWidget(self.identity, 1); il.addWidget(ib)
        f.addRow("신원", irow)
        self.font = QComboBox(); self.font.addItem("기본(맑은 고딕)", "")
        for fam in F.font_families():
            self.font.addItem(fam, fam)
        self.font.setCurrentIndex(max(0, self.font.findData(e.get("font", ""))))
        f.addRow("글꼴", self.font)
        self.size = QComboBox(); self.size.addItem("자동", 0)
        for s in (8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72):
            self.size.addItem(str(s), s)
        self.size.setCurrentIndex(max(0, self.size.findData(int(e.get("fontsize") or 0))))
        f.addRow("크기", self.size)
        self.orient = QComboBox(); self.orient.addItems(["가로", "세로"])
        self.orient.setCurrentIndex(1 if e.get("vertical") else 0)
        f.addRow("방향", self.orient)
        box, self.w, self.h = _size_row(e.get("width") or 0, e.get("height") or 0)
        self.auto_size = not (e.get("width") and e.get("height"))
        f.addRow("크기(pt)", box)
        lay.addLayout(f)
        self.preview = _Preview(); lay.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(_buttons(self, "저장"))
        for sig in (self.styles.idClicked, self.colors.idClicked, self.text.textChanged, self.date.currentIndexChanged,
                    self.identity.currentIndexChanged, self.font.currentIndexChanged, self.size.currentIndexChanged,
                    self.orient.currentIndexChanged):
            sig.connect(lambda *_a: self._show(refit=True))
        self.w.valueChanged.connect(self._size_edited); self.h.valueChanged.connect(self._size_edited)
        self._show(refit=self.auto_size)

    def _size_edited(self, *_a):
        self.auto_size = False
        self._show()

    def _edit_identity(self):
        if self.identity_edit and self.identity_edit():
            self._show(refit=True)

    def values(self) -> dict:
        return dict(self.entry, type="dynamic", name=self.name.text().strip() or "New Stamp",
                    category=self.category.currentText().strip(), style=S.STYLES[max(0, self.styles.checkedId())][0],
                    color=list(S.COLORS)[max(0, self.colors.checkedId())], text=self.text.text(),
                    date_fmt=self.date.currentData(), identity=self.identity.currentData(), font=self.font.currentData(),
                    fontsize=self.size.currentData(), vertical=self.orient.currentIndex() == 1,
                    width=self.w.value(), height=self.h.value())

    def _show(self, refit: bool = False):
        e = self.values()
        tmp = S.StampStore(None)
        tmp.data["identity"] = self.st.identity()
        if refit and self.auto_size:
            e.pop("width"); e.pop("height")
        tmp.data["custom"] = [dict(e, id="preview")]
        fc = S.face({"group": "custom", "id": "preview"}, author=self.author, st=tmp)
        if refit and self.auto_size:
            w, h = S.natural_size(fc)
            for sp, v in ((self.w, w), (self.h, h)):
                sp.blockSignals(True); sp.setValue(round(v)); sp.blockSignals(False)
        self.preview.show_png(S.render_png(fc, self.w.value(), self.h.value(), px=400))

    def accept(self):
        self.saved_id = self.st.put_custom(self.values())
        super().accept()


class StampManageDialog(QDialog):
    """사용자 스탬프 목록 · 이름 · 미리보기 · 추가(사용자 지정·동적) · 고치기 · 삭제. 바꾼 것은 바로 저장한다."""

    def __init__(self, parent, author: str, st: S.StampStore | None = None, identity_edit=None):
        super().__init__(parent)
        self.setWindowTitle("스탬프 관리")
        self.st = st or S.store()
        self.author = author
        self.identity_edit = identity_edit
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget(); self.list.setIconSize(QSize(64, 24)); self.list.setMinimumWidth(220)
        left.addWidget(self.list, 1)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        f = QFormLayout()
        self.name = QLineEdit(); self.name.editingFinished.connect(self._rename); f.addRow("이름", self.name)
        right.addLayout(f)
        self.preview = _Preview(); right.addWidget(self.preview)
        row = QHBoxLayout()
        add = QPushButton("추가")
        m = QMenu(add)
        m.addAction("사용자 지정 스탬프 만들기", lambda: self._create("image"))
        m.addAction("사용자 지정 동적 스탬프 만들기", lambda: self._create("dynamic"))
        add.setMenu(m)
        self.edit_btn = QPushButton("고치기"); self.edit_btn.clicked.connect(self._edit)
        self.del_btn = QPushButton("삭제"); self.del_btn.clicked.connect(self._delete)
        for b in (add, self.edit_btn, self.del_btn):
            row.addWidget(b)
        right.addLayout(row)
        right.addStretch(1)
        close = QPushButton("닫기"); close.setDefault(True); close.clicked.connect(self.accept)
        right.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(right)
        self.list.currentRowChanged.connect(self._pick)
        self._fill()

    def _fill(self, select: str | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.st.custom():
            label = e.get("name") or "스탬프"
            if e.get("category"):
                label += f" ({e['category']})"
            it = QListWidgetItem(label); it.setData(Qt.ItemDataRole.UserRole, e["id"])
            ic = _icon({"group": "custom", "id": e["id"]}, self.author)
            if ic:
                it.setIcon(ic)
            self.list.addItem(it)
            if e["id"] == select:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._pick()

    def _cur(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _pick(self, *_a):
        sid = self._cur()
        on = sid is not None
        self.edit_btn.setEnabled(on); self.del_btn.setEnabled(on); self.name.setEnabled(on)
        if not on:
            self.name.clear(); self.preview.show_png(None, "저장한 스탬프 없음")
            return
        self.name.setText(self.st.get_custom(sid).get("name", ""))
        try:
            self.preview.show_png(S.preview_png({"group": "custom", "id": sid}, 90, author=self.author))
        except ValueError as ex:
            self.preview.show_png(None, str(ex))

    def _rename(self):
        sid = self._cur()
        e = self.st.get_custom(sid) if sid else None
        if e and self.name.text().strip() and self.name.text().strip() != e.get("name"):
            self.st.put_custom(dict(e, name=self.name.text().strip()))
            self._fill(sid)

    def _create(self, kind: str):
        dlg = CustomStampDialog(self, st=self.st) if kind == "image" else \
            DynamicStampDialog(self, self.author, st=self.st, identity_edit=self.identity_edit)
        if dlg.exec():
            self._fill(dlg.saved_id)

    def _edit(self):
        sid = self._cur()
        e = self.st.get_custom(sid) if sid else None
        if not e:
            return
        dlg = CustomStampDialog(self, e, st=self.st) if e.get("type") == "image" else \
            DynamicStampDialog(self, self.author, e, st=self.st, identity_edit=self.identity_edit)
        if dlg.exec():
            self._fill(sid)

    def _delete(self):
        sid = self._cur()
        if sid and QMessageBox.question(self, "스탬프 관리", "고른 스탬프를 삭제할까요?") == QMessageBox.StandardButton.Yes:
            self.st.delete_custom(sid)
            self._fill()


class IdentityDialog(QDialog):
    """ID 편집: 디지털·동적 스탬프에 들어갈 이름·회사·부서. 이름은 새 주석의 작성자로도 쓴다."""

    def __init__(self, parent, name: str, company: str, department: str):
        super().__init__(parent)
        self.setWindowTitle("ID 편집")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("디지털 스탬프에 들어갈 신원 정보"))
        f = QFormLayout()
        login = QLineEdit(os.environ.get("USERNAME", "") or os.environ.get("USER", "")); login.setReadOnly(True)
        f.addRow("로그인 이름", login)
        self.name = QLineEdit(name); self.name.setPlaceholderText(login.text()); f.addRow("이름", self.name)
        self.company = QLineEdit(company); f.addRow("회사", self.company)
        self.department = QLineEdit(department); f.addRow("부서/직위", self.department)
        lay.addLayout(f)
        lay.addWidget(_buttons(self))

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "company": self.company.text().strip(),
                "department": self.department.text().strip()}
