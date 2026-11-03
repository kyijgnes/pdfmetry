"""레이어 속성 창: 이름 · 쓰임 · 처음 상태 · 잠금 · 보이기/인쇄/내보내기 처음 상태."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFormLayout, QLineEdit, QMessageBox, QVBoxLayout

from ..engine import outline as OL
from .dialogs import _buttons

INTENTS = (("보기", "view"), ("참조", "design"))
STATES = (("열기", True), ("닫기", False))


class LayerPropsDialog(QDialog):
    def __init__(self, parent, props: dict):
        super().__init__(parent)
        self.setWindowTitle("레이어 속성")
        self.setMinimumWidth(320)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(props.get("name", "")); form.addRow("레이어 이름", self.name)
        self.intent = QComboBox()
        for label, _ in INTENTS:
            self.intent.addItem(label)
        self.intent.setCurrentIndex(1 if props.get("intent") == "design" else 0)
        form.addRow("쓰임", self.intent)
        self.state = QComboBox()
        for label, _ in STATES:
            self.state.addItem(label)
        self.state.setCurrentIndex(0 if props.get("on", True) else 1)
        form.addRow("처음 상태", self.state)
        self.locked = QCheckBox("잠금"); self.locked.setChecked(bool(props.get("locked"))); form.addRow("", self.locked)
        self.usage = {}
        for key, label in (("view", "보이기"), ("print", "인쇄"), ("export", "내보내기")):
            c = QComboBox()
            for _, name in OL.USAGE_NAMES:
                c.addItem(name)
            c.setCurrentIndex([k for k, _ in OL.USAGE_NAMES].index(props.get(key, "when_on")))
            self.usage[key] = c
            form.addRow(label, c)
        lay.addLayout(form)
        lay.addWidget(_buttons(self))

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, self.windowTitle(), "레이어 이름을 넣으십시오.")
            return
        super().accept()

    def values(self) -> dict:
        out = {"name": self.name.text().strip(), "intent": INTENTS[self.intent.currentIndex()][1],
               "on": STATES[self.state.currentIndex()][1], "locked": self.locked.isChecked()}
        for key, c in self.usage.items():
            out[key] = OL.USAGE_NAMES[c.currentIndex()][0]
        return out
