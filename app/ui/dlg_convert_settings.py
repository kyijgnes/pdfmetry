"""「설정 변환」 모음 창: 왼쪽 형식 단추, 오른쪽에 그 형식 설정(쪽 범위 빼고), [적용]·[취소].

칸은 변환 창 설정과 같은 FormatFields 이고 저장도 같은 settings["convert"][형식] 이라, 여기서 고친 값이
변환 창 [설정] 에 그대로 나온다. 「이미지」 는 여섯 그림 형식에 같은 값을 쓴다."""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .dlg_convert import IMAGE_KEYS, FormatFields, ocr_languages

# (단추 이름, 형식 키)
PAGES = [("Word", "docx"), ("Excel", "xlsx"), ("PowerPoint", "pptx"), ("이미지", "png"), ("텍스트", "txt"),
         ("RTF", "rtf"), ("Epub", "epub"), ("HTML", "html")]


class ConvertSettingsDialog(QDialog):
    def __init__(self, parent, store: dict, prefs: dict | None = None, start: str = "docx", langs_fn=ocr_languages):
        super().__init__(parent)
        self.setWindowTitle("설정 변환")
        self.setMinimumWidth(560)
        self.store, self.prefs = store, prefs
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)
        body = QHBoxLayout(); lay.addLayout(body, 1)
        left = QVBoxLayout(); left.setSpacing(4); body.addLayout(left)
        self.stack = QStackedWidget(); body.addWidget(self.stack, 1)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        self.fields: dict[str, FormatFields] = {}
        for i, (label, key) in enumerate(PAGES):
            b = QPushButton(label); b.setCheckable(True); b.setAutoDefault(False)
            self.group.addButton(b, i); left.addWidget(b); self.buttons[key] = b
            page = QWidget(); pv = QVBoxLayout(page); pv.setContentsMargins(0, 0, 0, 0)
            title = QLabel(f"{label} 설정"); f = title.font(); f.setBold(True); title.setFont(f)
            pv.addWidget(title)
            form = QFormLayout(); form.setHorizontalSpacing(10); form.setVerticalSpacing(6); pv.addLayout(form)
            pv.addStretch(1)
            self.fields[key] = FormatFields(form, self, key, store.get(key), prefs, langs_fn)
            self.stack.addWidget(page)
        left.addStretch(1)
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        # 언어 칸은 공용 값 하나라 한쪽에서 고치면 다른 쪽도 같이
        self._langs = [ff.lang for ff in self.fields.values() if ff.lang is not None]
        for ll in self._langs:
            ll.list.itemChanged.connect(lambda _it, src=ll: self._share_langs(src))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = bb.button(QDialogButtonBox.StandardButton.Ok); ok.setText("적용"); ok.setDefault(True)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.show_format(start)

    def _share_langs(self, src):
        chosen = src.langs()
        for ll in self._langs:
            if ll is not src:
                ll.list.blockSignals(True); ll.set_langs(chosen); ll.list.blockSignals(False); ll._sync()

    def show_format(self, key: str):
        key = "png" if key in IMAGE_KEYS else key
        idx = next((i for i, (_l, k) in enumerate(PAGES) if k == key), 0)
        self.group.button(idx).setChecked(True); self.stack.setCurrentIndex(idx)

    def accept(self):
        for i, (label, key) in enumerate(PAGES):
            err = self.fields[key].error()
            if err:
                self.group.button(i).setChecked(True); self.stack.setCurrentIndex(i)
                QMessageBox.warning(self, "설정 변환", err[0]); err[1].setFocus(); return
        for key, ff in self.fields.items():
            vals = ff.values()
            for k in (IMAGE_KEYS if key == "png" else (key,)):
                self.store[k] = {**self.store.get(k, {}), **vals}
        self.fields["docx"].commit_langs()   # 언어 칸은 모두 같은 값
        super().accept()
