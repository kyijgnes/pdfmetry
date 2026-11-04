"""OCR 창(「OCR PDF」)과 OCR 언어 고르기.

- 언어는 윈도우에 깔린 문자 인식 언어를 체크 목록으로 여러 개 고른다(`ocr_langs`). 엔진이 언어마다 돌려 낱말이 많은 결과를 쓴다.
- 범위는 「모든 페이지」 / 「선택된 페이지」 / 「범위 [시작] 부터 [끝]」, 고른 종류를 `ocr_range` 로 기억한다.
- 옛 설정 `ocr_lang`(글자 하나)은 `migrate_langs` 가 처음 읽을 때 `ocr_langs` 로 옮긴다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from .dlg_prefs import lang_name

NO_LANG_TEXT = "OCR을 수행할 언어를 선택하십시오."
MANY_TEXT = "세개 이상의 언어는 인식 속도를 늦출 수 있습니다."
NONE_INSTALLED = "윈도우 설정 > 언어에서 OCR 언어를 더하세요"
DPIS = (200, 300, 400)

_cache: list[str] | None = None


def languages(refresh: bool = False) -> list[str]:
    """이 PC 윈도우 OCR 언어 태그(PowerShell 을 부르므로 한 번만 읽어 둔다)."""
    global _cache
    if _cache is None or refresh:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from ..engine import ocr
            _cache = ocr.available_languages()
        except Exception:  # noqa: BLE001
            _cache = []
        finally:
            QApplication.restoreOverrideCursor()
    return list(_cache)


def migrate_langs(prefs: dict) -> None:
    """옛 `ocr_lang`(글자 하나)을 `ocr_langs`(목록)로 옮기고 지운다."""
    if "ocr_lang" not in prefs:
        return
    old = prefs.pop("ocr_lang")
    if not prefs.get("ocr_langs") and isinstance(old, str) and old.strip():
        prefs["ocr_langs"] = [old.strip()]


def default_langs(avail: list[str]) -> list[str]:
    """설치된 것 가운데 영어·한국어. 둘 다 없으면 첫 언어, 아무것도 없으면 ko."""
    got = [t for base in ("en", "ko") for t in avail if t.split("-")[0].lower() == base][:2]
    seen: list[str] = []
    for t in got:
        if t not in seen:
            seen.append(t)
    return seen or avail[:1] or ["ko"]


def current_langs(prefs: dict, avail: list[str] | None = None) -> list[str]:
    migrate_langs(prefs)
    cur = prefs.get("ocr_langs")
    if isinstance(cur, str):
        cur = [cur]
    cur = [str(x) for x in (cur or []) if str(x).strip()]
    return cur or default_langs(avail if avail is not None else languages())


def _same(a: str, b: str) -> bool:
    """「ko」 처럼 짧게 적은 값과 「ko-KR」 은 같은 언어로 본다."""
    return a == b or a.split("-")[0].lower() == b.lower() or b.split("-")[0].lower() == a.lower()


class LangList(QWidget):
    """언어 체크 목록 + 3개 넘으면 경고 줄(+ 고른 언어 이름 줄)."""
    changed = Signal()

    def __init__(self, avail: list[str], chosen: list[str], names_line: bool = False, parent=None):
        super().__init__(parent)
        tags = list(avail)
        for c in chosen:   # 고른 값이 이 PC 에 없어도 목록에 보여 끌 수 있게
            if not any(_same(c, t) for t in tags):
                tags.append(c)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(4, 4, 4, 4); bl.setSpacing(2)
        self.boxes: list[QCheckBox] = []
        for tag in tags:
            cb = QCheckBox(lang_name(tag)); cb.setProperty("tag", tag)
            cb.setChecked(any(_same(c, tag) for c in chosen))
            cb.toggled.connect(self._changed)
            bl.addWidget(cb); self.boxes.append(cb)
        if not avail:
            bl.addWidget(QLabel(NONE_INSTALLED))
        bl.addStretch(1)
        sc = QScrollArea(); sc.setWidget(box); sc.setWidgetResizable(True); sc.setMinimumHeight(110)
        v.addWidget(sc, 1)
        self.names = QLabel(""); self.names.setWordWrap(True)
        self.names.setVisible(names_line)
        v.addWidget(self.names)
        self.warn = QLabel(MANY_TEXT); self.warn.setWordWrap(True)
        v.addWidget(self.warn)
        self._show_names = names_line
        self._changed()

    def _changed(self):
        langs = self.checked()
        self.warn.setVisible(len(langs) > 3)
        if self._show_names:
            self.names.setText("、 ".join(lang_name(t).split(" (")[0] for t in langs))
        self.changed.emit()

    def checked(self) -> list[str]:
        return [cb.property("tag") for cb in self.boxes if cb.isChecked()]

    def set_checked(self, tags: list[str]):
        for cb in self.boxes:
            cb.setChecked(cb.property("tag") in tags)


class OcrDialog(QDialog):
    """OCR PDF. exec() 가 참이면 values(). 「일괄 OCR」 을 누르면 batch=True 로 닫힌다."""

    def __init__(self, parent, total: int, current: int, picked: list[int], avail: list[str], chosen: list[str],
                 range_mode: str = "all", dpi: int = 300, force: bool = False):
        super().__init__(parent)
        self.setWindowTitle("OCR PDF")
        self.setMinimumWidth(420)
        self.total, self.current = total, current
        self.picked = sorted(set(p for p in picked if 0 <= p < total))
        self.batch = False
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)

        g = QGroupBox("언어 인식"); gl = QVBoxLayout(g)
        self.langs = LangList(avail, chosen); gl.addWidget(self.langs)
        lay.addWidget(g, 1)

        g = QGroupBox("페이지 범위"); gl = QVBoxLayout(g)
        self.r_all = QRadioButton("모든 페이지")
        self.r_pick = QRadioButton("현재 페이지" if len(self.picked) == 1 else "선택된 페이지")
        self.r_pick.setEnabled(bool(self.picked))
        self.r_range = QRadioButton("범위")
        self.group = QButtonGroup(self)
        for r in (self.r_all, self.r_pick, self.r_range):
            self.group.addButton(r)
        gl.addWidget(self.r_all); gl.addWidget(self.r_pick)
        row = QHBoxLayout(); row.addWidget(self.r_range)
        first, last = (self.picked[0], self.picked[-1]) if self.picked else (current, current)
        self.start = QSpinBox(); self.start.setRange(1, max(1, total)); self.start.setValue(first + 1)
        self.end = QSpinBox(); self.end.setRange(1, max(1, total)); self.end.setValue(last + 1)
        row.addWidget(self.start); row.addWidget(QLabel("부터")); row.addWidget(self.end)
        row.addWidget(QLabel(f"/ 총 {total}")); row.addStretch(1)
        gl.addLayout(row)
        for w in (self.start, self.end):
            w.valueChanged.connect(lambda _=0: self.r_range.setChecked(True))
        if range_mode == "pick":
            (self.r_pick if self.picked else self.r_range).setChecked(True)
        else:
            self.r_all.setChecked(True)
        lay.addWidget(g)

        g = QGroupBox("인식 설정"); gl = QHBoxLayout(g)
        gl.addWidget(QLabel("해상도"))
        self.dpi = QComboBox()
        for d in DPIS:
            self.dpi.addItem(f"{d} dpi", d)
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(dpi)))
        gl.addWidget(self.dpi)
        self.force = QCheckBox("글자가 이미 있는 쪽도 다시 인식"); self.force.setChecked(force)
        gl.addWidget(self.force); gl.addStretch(1)
        lay.addWidget(g)

        bb = QDialogButtonBox()
        self.batch_btn = QPushButton("일괄 OCR"); bb.addButton(self.batch_btn, QDialogButtonBox.ButtonRole.ResetRole)
        ok = bb.addButton("적용", QDialogButtonBox.ButtonRole.AcceptRole); ok.setDefault(True)
        bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        self.batch_btn.clicked.connect(self._batch)
        self.ok = ok
        lay.addWidget(bb)

    def _batch(self):
        self.batch = True
        self.reject()

    def pages(self) -> list[int]:
        if self.r_pick.isChecked() and self.picked:
            return list(self.picked)
        if self.r_range.isChecked():
            a, b = self.start.value(), self.end.value()
            if a > b:
                raise ValueError("시작 페이지는 끝 페이지 번호보다 작거나 같아야 합니다")
            return list(range(a - 1, b))
        return list(range(self.total))

    def accept(self):
        try:
            self.pages()
        except ValueError as e:
            QMessageBox.warning(self, "OCR PDF", str(e)); self.start.setFocus(); self.start.selectAll()
            return
        if not self.langs.checked():
            QMessageBox.warning(self, "OCR PDF", NO_LANG_TEXT)
            return
        super().accept()

    def values(self) -> dict:
        return {"langs": self.langs.checked(), "pages": self.pages(),
                "range": "all" if self.r_all.isChecked() else "pick",
                "dpi": int(self.dpi.currentData()), "force": self.force.isChecked()}


class LangDialog(QDialog):
    """영역 OCR(글자 고름 오른쪽 클릭)에서 언어만 고르는 작은 창."""

    def __init__(self, parent, avail: list[str], chosen: list[str]):
        super().__init__(parent)
        self.setWindowTitle("영역 OCR")
        self.setMinimumWidth(320)
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12)
        g = QGroupBox("언어 인식"); gl = QVBoxLayout(g)
        self.langs = LangList(avail, chosen); gl.addWidget(self.langs)
        lay.addWidget(g, 1)
        bb = QDialogButtonBox()
        bb.addButton("적용", QDialogButtonBox.ButtonRole.AcceptRole).setDefault(True)
        bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def accept(self):
        if not self.langs.checked():
            QMessageBox.warning(self, "영역 OCR", NO_LANG_TEXT)
            return
        super().accept()
