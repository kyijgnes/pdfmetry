"""암호 문서 열기: 비밀번호 창 · 사전 설정 비밀번호(윈도우 DPAPI 로 덮어 설정에 둔다).

설정 열쇠: prefs["preset_pw"] = DPAPI 로 덮은 글자(base64) 셋까지, prefs["preset_pw_auto"] = 열 때 먼저 풀어 보기.
환경 설정 화면은 `set_presets`·`presets` 만 부른다(평문은 설정 파일에 남지 않는다).
"""
from __future__ import annotations

import base64
import ctypes
import sys
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QVBoxLayout)

MAX_PRESETS = 3
_ENTROPY = b"PDFmetry preset password"


# ── DPAPI ─────────────────────────────────────────────────────
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    b = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    b._buf = buf   # 버퍼가 먼저 사라지지 않게
    return b


def _crypt(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise OSError("윈도우에서만 비밀번호를 덮어 둘 수 있습니다")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, ent, out = _blob(data), _blob(_ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN(1): 창을 띄우지 않는다
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 1, ctypes.byref(out)):
        raise OSError("비밀번호를 덮거나 풀지 못했습니다")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    """이 윈도우 사용자만 풀 수 있게 덮어 base64 글자로."""
    return base64.b64encode(_crypt(text.encode("utf-8"), True)).decode("ascii")


def unprotect(s: str) -> str | None:
    try:
        return _crypt(base64.b64decode(s), False).decode("utf-8")
    except Exception:  # noqa: BLE001  다른 PC·다른 사용자로 옮긴 설정
        return None


def presets(prefs: dict) -> list[str]:
    """설정에 둔 사전 설정 비밀번호(푼 것). 못 푸는 것은 뺀다."""
    out = []
    for s in prefs.get("preset_pw") or []:
        pw = unprotect(s) if isinstance(s, str) and s else None
        if pw:
            out.append(pw)
    return out[:MAX_PRESETS]


def set_presets(prefs: dict, passwords: list[str]):
    prefs["preset_pw"] = [protect(p) for p in passwords if p][:MAX_PRESETS]


def try_presets(prefs: dict, check: Callable[[str], bool]) -> str | None:
    for pw in presets(prefs):
        if check(pw):
            return pw
    return None


# ── 창 ────────────────────────────────────────────────────────
class PresetEditor(QDialog):
    """사전 설정 비밀번호 셋 · 「항상 위의 비밀번호를 사용」. 둔 비밀번호는 다시 보이지 않고, 칸에 처음 치면 비워진다."""

    def __init__(self, parent, prefs: dict):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox, QFormLayout
        self.setWindowTitle("사전 설정 비밀번호")
        self.prefs = prefs
        lay = QVBoxLayout(self); form = QFormLayout(); lay.addLayout(form)
        have = list(prefs.get("preset_pw") or [])[:MAX_PRESETS]
        self.edits: list[QLineEdit] = []
        for k in range(MAX_PRESETS):
            e = QLineEdit(); e.setEchoMode(QLineEdit.EchoMode.Password); e.setMinimumWidth(240)
            e.kept = have[k] if k < len(have) else ""   # 덮은 글자 그대로(고치지 않으면 그대로 둔다)
            if e.kept:
                e.setText("●●●●●●"); e.touched = False
                e.textEdited.connect(lambda _t, e=e: self._first_key(e))
            else:
                e.touched = True
            form.addRow(f"비밀번호 {k + 1}", e); self.edits.append(e)
        self.auto = QCheckBox("항상 위의 비밀번호를 사용"); self.auto.setChecked(bool(prefs.get("preset_pw_auto")))
        lay.addWidget(self.auto)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("확인"); bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    @staticmethod
    def _first_key(e: QLineEdit):
        if not e.touched:
            e.touched = True
            typed = e.text().replace("●", "")
            e.setText(typed)

    def apply(self):
        out = []
        for e in self.edits:
            if not e.touched:
                out.append(e.kept)
            elif e.text():
                out.append(protect(e.text()))
        self.prefs["preset_pw"] = out
        self.prefs["preset_pw_auto"] = self.auto.isChecked()


def edit_presets(parent, prefs: dict) -> bool:
    dlg = PresetEditor(parent, prefs)
    if dlg.exec():
        dlg.apply(); return True
    return False


class PasswordDialog(QDialog):
    """「{이름}은(는) 보호됩니다」. 틀리면 창을 닫지 않고 그 자리에서 다시 받는다.
    check(pw) 가 True 면 닫고 `password` 에 맞은 비밀번호가 남는다."""

    WRONG = "비밀번호가 정확하지 않습니다. 다시 입력하십시오."

    def __init__(self, parent, name: str, prefs: dict, check: Callable[[str], bool], on_presets: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("비밀번호")
        self.prefs, self.check, self.password = prefs, check, None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{name}은(는) 보호됩니다. 문서를 열 비밀번호를 입력하십시오."))
        self.typed = QRadioButton("비밀번호 입력"); self.typed.setChecked(True)
        self.edit = QLineEdit(); self.edit.setEchoMode(QLineEdit.EchoMode.Password); self.edit.setMinimumWidth(280)
        self.edit.textEdited.connect(lambda _: (self.typed.setChecked(True), self.err.setText("")))
        self.preset = QRadioButton("사전 설정 비밀번호 사용")
        self.preset.setEnabled(bool(presets(prefs)))
        g = QButtonGroup(self); g.addButton(self.typed); g.addButton(self.preset)
        lay.addWidget(self.typed); lay.addWidget(self.edit)
        row = QHBoxLayout(); row.addWidget(self.preset)
        if on_presets is not None:
            link = QPushButton("사전 설정 비밀번호 관리"); link.setFlat(True); link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setStyleSheet("color: palette(link); text-decoration: underline; border: none;")
            link.clicked.connect(lambda: (on_presets(), self.preset.setEnabled(bool(presets(self.prefs)))))
            row.addWidget(link)
        row.addStretch(); lay.addLayout(row)
        self.err = QLabel(""); self.err.setStyleSheet("color: #d23c3c;"); lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Yes).setText("예")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self.try_open); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.edit.setFocus()

    def try_open(self) -> bool:
        pw = try_presets(self.prefs, self.check) if self.preset.isChecked() else (self.edit.text() if self.check(self.edit.text()) else None)
        if pw is None:
            self.err.setText(self.WRONG); self.edit.selectAll(); self.edit.setFocus()
            return False
        self.password = pw
        self.accept()
        return True


def ask(parent, name: str, prefs: dict, check: Callable[[str], bool], on_presets=None) -> str | None:
    """암호 문서를 열 비밀번호. 「항상 사전 설정 비밀번호 사용」 이면 먼저 셋으로 풀어 보고, 맞으면 묻지 않는다."""
    if prefs.get("preset_pw_auto"):
        pw = try_presets(prefs, check)
        if pw is not None:
            return pw
    dlg = PasswordDialog(parent, name, prefs, check, on_presets)
    return dlg.password if dlg.exec() else None
