"""측정 설정 창: 축척(문서 거리 = 실제 거리) · 스냅 · 라벨. 값은 settings.json 의 measure 에 기억한다."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from ..engine import annots as A
from .dialogs import _buttons

SNAPS = [("path", "경로"), ("end", "끝점"), ("mid", "중점"), ("cross", "교차점")]
SNAP_READY = {"end", "mid"}   # 경로·교차점 스냅은 아직 없음(단추는 보이되 꺼 둠)


class MeasureDialog(QDialog):
    def __init__(self, parent, values: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("측정 설정")
        v = A.measure_ratio(values)
        lay = QVBoxLayout(self)
        g = QGroupBox("축척"); row = QHBoxLayout(g)
        lo, hi = A.MEASURE_RANGE
        self.doc, self.real = QDoubleSpinBox(), QDoubleSpinBox()
        self.doc_unit, self.real_unit = QComboBox(), QComboBox()
        for sp, val in ((self.doc, v["doc"]), (self.real, v["real"])):
            sp.setRange(lo, hi); sp.setDecimals(2); sp.setValue(val)
        for c, val in ((self.doc_unit, v["doc_unit"]), (self.real_unit, v["real_unit"])):
            c.addItems(list(A.MEASURE_UNITS)); c.setCurrentText(val)
        for w in (self.doc, self.doc_unit, QLabel("="), self.real, self.real_unit):
            row.addWidget(w)
        lay.addWidget(g)
        g = QGroupBox("스냅"); row = QHBoxLayout(g)
        self.snaps: dict[str, QPushButton] = {}
        for key, label in SNAPS:
            b = QPushButton(label); b.setCheckable(True)
            b.setChecked(bool(v["snap"].get(key)) and key in SNAP_READY)
            b.setEnabled(key in SNAP_READY)
            self.snaps[key] = b; row.addWidget(b)
        row.addStretch(1)
        lay.addWidget(g)
        g = QGroupBox("라벨"); row = QHBoxLayout(g)
        self.label = QLineEdit(v.get("label", "")); self.label.setPlaceholderText("결과 앞에 붙일 글자")
        row.addWidget(self.label)
        lay.addWidget(g)
        lay.addWidget(_buttons(self))

    def values(self) -> dict:
        return A.measure_ratio({"doc": self.doc.value(), "doc_unit": self.doc_unit.currentText(),
                                "real": self.real.value(), "real_unit": self.real_unit.currentText(),
                                "snap": {k: b.isChecked() for k, b in self.snaps.items()},
                                "label": self.label.text().strip()})
