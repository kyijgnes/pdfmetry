"""보안 탭: 암호화 창 · 권한 암호 창 · 보호된 문서 띠(「편집 사용」) · 띠가 떠 있는 동안 쓰기 명령 막기."""
from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ..engine import tools as T

FAKE = "●" * 8   # 이미 걸린 열기 암호 자리(진짜 암호가 아님)
PRINT_LEVELS = ("인쇄는 제한됩니다", "저해상도 인쇄 가능", "고해상도 인쇄 가능")
CHANGE_LEVELS = ("변경할 수 없습니다.", "페이지 삽입, 삭제 및 회전 허용", "양식 필드 채우기 및 서명 추가 허용",
                 "주석 달기, 양식 필드 채우기 및 서명 허용", "페이지 추출을 제외한 모든 작업 허용")
WRONG_OWNER = "비밀번호가 정확하지 않습니다. 기타 비밀번호로 재시도하십시오."
BAR_TEXT = "이 문서는 보호되어 있습니다. 권한 암호를 입력하여 편집을 활성화하세요."
BLOCKED = "보호된 문서입니다. 위 띠의 「편집 사용」 을 눌러 권한 암호를 넣으세요"


def perm_args(print_level: int, change_level: int, copy: bool) -> dict:
    """인쇄 3단계·변경 5단계·복사 → tools.encrypt_bytes 의 allow_* 여덟."""
    return T.perm_flags(print_level, change_level, copy)


def _pw_edit(placeholder: str) -> tuple[QLineEdit, QLineEdit]:
    """암호 칸 + 끝의 눈 단추(누르는 동안이 아니라 켜고 끄기로 보이기)."""
    e = QLineEdit(); e.setEchoMode(QLineEdit.EchoMode.Password); e.setPlaceholderText(placeholder)
    act = QAction("보기", e); act.setCheckable(True)
    from . import icons
    try:
        act.setIcon(icons.icon("view"))
    except Exception:  # noqa: BLE001
        pass
    act.toggled.connect(lambda on: e.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
    e.addAction(act, QLineEdit.ActionPosition.TrailingPosition)
    e.eye = act
    return e, e


class EncryptDialog(QDialog):
    """암호화 창. current: {"open": 열기 암호 있음, "open_pw": 아는 열기 암호, "perm": 파일 /P 또는 None,
    "owner_pw": 아는 권한 암호}. 두 체크를 다 끄고 저장하면 암호 빼기(removes())."""
    METHODS = [("aes128", "128 bit AES"), ("aes256", "256 bit AES · 한글 암호 가능"), ("rc4", "128 bit RC4 · 옛 프로그램용")]

    def __init__(self, parent, method: str | None = None, current: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("암호화")
        cur = current or {}
        self._open_pw0 = cur.get("open_pw") if cur.get("open") else None
        self._owner_pw0 = cur.get("owner_pw")
        lay = QVBoxLayout(self)
        self.form = QFormLayout(); lay.addLayout(self.form)
        self.use_open = QCheckBox("문서를 열 때 암호를 요구합니다.")
        _, self.open_pw = _pw_edit("암호를 입력")
        self.form.addRow(self.use_open); self.form.addRow("열기 암호", self.open_pw)
        self.use_owner = QCheckBox("문서 보안 설정 변경이나 특정 기능 사용 시 암호를 요구합니다.")
        _, self.owner_pw = _pw_edit("비밀번호 입력(공개 비밀번호와 다름)")
        self.form.addRow(self.use_owner); self.form.addRow("권한 암호", self.owner_pw)
        self.print_level = QComboBox(); self.print_level.addItems(PRINT_LEVELS)
        self.change_level = QComboBox(); self.change_level.addItems(CHANGE_LEVELS)
        self.copy = QCheckBox("텍스트, 이미지 및 기타 내용의 복사가 가능합니다.")
        self.form.addRow("인쇄", self.print_level); self.form.addRow("변경", self.change_level); self.form.addRow(self.copy)
        box = QWidget(); vl = QVBoxLayout(box); vl.setContentsMargins(0, 0, 0, 0)
        self.methods = {}
        for key, label in self.METHODS:   # 128 bit AES 가 기본, 이미 걸린 문서는 그 방식
            rb = QRadioButton(label); self.methods[key] = rb; vl.addWidget(rb)
        self.methods.get(method or "aes128", self.methods["aes128"]).setChecked(True)
        self.method_box = box
        self.form.addRow("암호화 수준", box)
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        self.new_file = QCheckBox("새 파일로 저장"); hl.addWidget(self.new_file); hl.addStretch()
        self.here = self.new_file   # 일괄 편집은 이 줄을 숨긴다(늘 새 파일)
        self.form.addRow(row)
        self.buttons = QDialogButtonBox()
        self.save_btn = self.buttons.addButton("저장", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        # 처음 값: 이미 걸린 열기 암호는 가짜 글자, 권한 제한은 파일 값, 권한 암호가 없으면 인쇄 제한·변경 불가
        perm = cur.get("perm")
        self.use_open.setChecked(bool(cur.get("open")))
        if self._open_pw0:
            self.open_pw.setText(FAKE)
        restricted = perm is not None and (perm & T.PERM_ALL) != T.PERM_ALL
        self.use_owner.setChecked(restricted)
        if restricted and self._owner_pw0:
            self.owner_pw.setText(FAKE)
        pr, ch, cp = T.perm_levels(perm) if restricted else (0, 0, perm is None or bool(perm & 16))
        self.print_level.setCurrentIndex(pr); self.change_level.setCurrentIndex(ch); self.copy.setChecked(cp)
        self._open_touched = self._owner_touched = False
        self.open_pw.textEdited.connect(lambda *_: self._touch("open"))
        self.owner_pw.textEdited.connect(lambda *_: self._touch("owner"))
        for w in (self.use_open, self.use_owner):
            w.toggled.connect(self._sync)
        self.open_pw.textChanged.connect(self._sync); self.owner_pw.textChanged.connect(self._sync)
        self._sync()

    def _touch(self, which: str):
        if which == "open" and not self._open_touched:
            self._open_touched = True
            if FAKE in self.open_pw.text():   # 가짜 글자에 이어 쳤으면 가짜는 지운다
                self.open_pw.setText(self.open_pw.text().replace(FAKE, ""))
        if which == "owner" and not self._owner_touched:
            self._owner_touched = True
            if FAKE in self.owner_pw.text():
                self.owner_pw.setText(self.owner_pw.text().replace(FAKE, ""))

    def _sync(self, *_):
        o, w = self.use_open.isChecked(), self.use_owner.isChecked()
        self.open_pw.setEnabled(o)
        for x in (self.owner_pw, self.print_level, self.change_level, self.copy):
            x.setEnabled(w)
        self.method_box.setEnabled(o or w)
        ok = (not o or bool(self.open_pw.text())) and (not w or bool(self.owner_pw.text()))
        self.save_btn.setEnabled(ok)

    def _method(self) -> str:
        return next(k for k, rb in self.methods.items() if rb.isChecked())

    def _user(self) -> str:
        if not self.use_open.isChecked():
            return ""
        t = self.open_pw.text()
        return self._open_pw0 if (t == FAKE and not self._open_touched and self._open_pw0) else t

    def _owner(self) -> str:
        if not self.use_owner.isChecked():
            return ""
        t = self.owner_pw.text()
        return self._owner_pw0 if (t == FAKE and not self._owner_touched and self._owner_pw0) else t

    def removes(self) -> bool:
        """두 체크를 다 껐다: 저장할 때 암호를 뺀다."""
        return not self.use_open.isChecked() and not self.use_owner.isChecked()

    def accept(self):
        if self.removes():
            if not self.new_file.isVisibleTo(self):   # 일괄 편집에서는 뺄 암호가 없다
                QMessageBox.warning(self, "암호화", "열기 암호나 권한 암호 가운데 하나는 넣어야 합니다"); return
            super().accept(); return
        try:   # 창을 닫기 전에 확인한다(같은 암호·한글 암호 등)
            T.check_encrypt_args(self._user(), self._owner(), self._method())
        except T.EngineError as e:
            msg = str(e)
            if "달라야" in msg:
                msg = "열려 있는 비밀번호와 권한 비밀번호가 부동하여야 합니다."
            QMessageBox.warning(self, "암호화", msg); return
        super().accept()

    def values(self) -> dict:
        """tools.encrypt_bytes 인자. 권한 체크를 끄면 모든 권한(열기 암호로 열면 다 됨)."""
        d = {"user_pw": self._user(), "owner_pw": self._owner(), "method": self._method()}
        if self.use_owner.isChecked():
            d.update(perm_args(self.print_level.currentIndex(), self.change_level.currentIndex(), self.copy.isChecked()))
        else:
            d.update(perm_args(2, 4, True))
        return d

    def to_new_file(self) -> bool:
        return self.new_file.isChecked()


class OwnerPasswordDialog(QDialog):
    """권한 암호 받기. 맞으면 문서의 제한을 풀고 닫는다."""

    def __init__(self, parent, fdoc):
        super().__init__(parent)
        self.setWindowTitle("권한 암호")
        self.fdoc = fdoc
        self.password = ""
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("편집을 활성화하려면 비밀번호를 입력하세요."))
        _, self.pw = _pw_edit("권한 암호"); lay.addWidget(self.pw)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def accept(self):
        if not T.unlock_owner(self.fdoc, self.pw.text()):
            QMessageBox.warning(self, "권한 암호", WRONG_OWNER); self.pw.selectAll(); return
        self.password = self.pw.text()
        super().accept()


def unlock(win, tab) -> bool:
    """권한 암호를 받아 탭 문서의 제한을 푼다. 풀렸거나 원래 제한이 없으면 True."""
    d = tab.doc
    if not T.is_restricted(d.fitz):
        return True
    dlg = OwnerPasswordDialog(win, d.fitz)
    if not dlg.exec():
        return False
    d.password = dlg.password   # 다시 불러올 때(저장·되돌리기)도 권한 암호로 푼다
    d.fitz._pdfapp_pw = dlg.password
    bar = getattr(tab, "protect_bar", None)
    if bar is not None:
        bar.hide()
    return True


def current(doc) -> dict:
    """암호화 창 처음 값."""
    f = doc.fitz
    enc = bool((f.metadata or {}).get("encryption"))
    sec = getattr(doc, "security", None)
    if sec:   # 걸어 두고 아직 저장 안 한 암호
        perm = sum(flag for flag, k in ((4, "allow_print"), (2048, "allow_print_hq"), (16, "allow_copy"), (8, "allow_modify"),
                                        (32, "allow_annotate"), (256, "allow_forms"), (1024, "allow_assemble"),
                                        (512, "allow_accessibility")) if sec.get(k, True))
        return {"open": bool(sec.get("user_pw")), "open_pw": sec.get("user_pw"), "perm": perm if sec.get("owner_pw") else None,
                "owner_pw": sec.get("owner_pw")}
    if sec == {} or not enc:
        return {"open": False, "perm": None}
    perm = T.file_permissions(f)
    opw = getattr(doc, "open_password", None)
    try:   # 열기 암호가 있는지는 파일 바이트를 다시 열어 본다(풀어 연 문서는 needs_pass 가 꺼져 있다)
        import fitz
        chk = fitz.open("pdf", f.tobytes(encryption=fitz.PDF_ENCRYPT_KEEP))
        has_open = bool(chk.needs_pass); chk.close()
    except Exception:  # noqa: BLE001
        has_open = bool(opw)
    owner = doc.password if doc.password and doc.password != opw else None
    return {"open": has_open, "open_pw": opw, "perm": perm, "owner_pw": owner}


# ── 보호된 문서 띠 ────────────────────────────────────────────────
class ProtectBar(QFrame):
    """권한이 모자란 채 열린 문서 위 한 줄: 「편집 사용」 → 권한 암호 창."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setObjectName("protectBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout(self); h.setContentsMargins(12, 4, 8, 4); h.setSpacing(8)
        self.label = QLabel(BAR_TEXT); h.addWidget(self.label, 1)
        self.run = QPushButton("편집 사용"); self.run.setDefault(True); h.addWidget(self.run)
        self.run.clicked.connect(lambda: unlock(self.tab.win, self.tab))
        self.restyle()
        self.hide()

    def restyle(self):
        from . import theme
        c = theme.CUR or theme.LIGHT
        self.setStyleSheet(f"QFrame#protectBar {{ background: {c.get('WARN_BG', '#FDEBEC')}; border: none;"
                           f" border-bottom: 1px solid {c.get('WARN_LINE', '#F5C9CB')}; }}")

    def showEvent(self, e):
        self.restyle()
        super().showEvent(e)

    def check(self):
        self.setVisible(T.is_restricted(self.tab.doc.fitz))


def attach(tab, layout) -> ProtectBar:
    """DocTab 의 세로 틀 맨 위에 띠를 넣고 검사한다."""
    bar = ProtectBar(tab)
    layout.insertWidget(0, bar)
    bar.check()
    return bar


# ── 쓰기 명령 막기 ────────────────────────────────────────────────
# 보기·읽기·내보내기는 막지 않는다
_READ_KEYS = {"hand", "select", "zoomin", "zoomout", "search", "snapshot", "print", "properties", "thumb_out", "thumb_in",
              "page_range", "extract", "show_annots", "keep_tool", "form_export", "form_preview", "encrypt", "home_more",
              "edit_more", "page_more", "form_more", "tools_more", "insert", "markup_more", "shape_more", "measure",
              "m_distance", "m_perimeter", "m_area", "annot_color", "digital_sign", "sign_manage"}
_WRITE_TABS = ("edit", "comment", "page", "form")
_WRITE_EXTRA = {"edit_all", "addtext", "crop", "ocr", "ocrarea", "deskew", "redact", "redact_search", "redact_apply", "hidden",
                "sign_apply", "rotate_pages", "delete_blank", "dsign_clear"}


def _write_keys() -> set[str]:
    from . import ribbon as R
    keys = set(_WRITE_EXTRA)
    for name, _lbl, items in R.TABS:
        if name not in _WRITE_TABS:
            continue
        for it in items:
            if it == R.SEP:
                continue
            keys.add(it[0])
            if it[3] == "menu" and it[4]:
                keys.update(m[0] for m in it[4] if m and m[0])
    return keys - _READ_KEYS


WRITE_KEYS: set[str] = set()


def blocks(win, key: str) -> bool:
    """띠가 떠 있는(권한 모자란) 문서에서 쓰기 명령이면 막고 True."""
    t = win.cur() if hasattr(win, "cur") else None
    if t is None or not T.is_restricted(t.doc.fitz):
        return False
    if not WRITE_KEYS:
        WRITE_KEYS.update(_write_keys())
    if key not in WRITE_KEYS and not key.startswith("align_"):
        return False
    bar = getattr(t, "protect_bar", None)
    if bar is not None:
        bar.show()
    win.say(BLOCKED)
    return True
