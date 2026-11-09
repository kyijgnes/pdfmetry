"""PDF 비교: 「파일 비교」 고르기 창과 결과 탭 오른쪽 「변경 내용 목록」 패널.

결과 PDF(engine.compare.build) 는 상자를 종류·대상마다 레이어에 그려 두었다. 패널의 숨기기 · 필터는 그 레이어를
화면에서만 끄고 켠다(파일은 그대로). 이전/다음 · 목록 누르기는 그 상자로 가서 강조한다.
"""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from ..engine import compare as C
from . import icons
from .dialogs import settle_default

SAME_DOC = "비교할 두 가지 문서를 선택하십시오."
PDF_FILTER = "PDF 파일 (*.pdf)"


class CompareSelectDialog(QDialog):
    """「이전 파일」(지금 문서) · 「새 파일」. 칸마다 [파일 선택] 메뉴: 파일 찾아보기 · 열린 문서."""

    def __init__(self, parent, current: tuple[str, str | None, object] | None, open_docs: list[tuple[str, str | None, object]]):
        """current: (이름, 경로, 탭) 지금 문서. open_docs: [(이름, 경로, 탭)] 고를 수 있는 열린 문서."""
        super().__init__(parent)
        self.setWindowTitle("파일 비교"); self.setMinimumWidth(520)
        self.open_docs = open_docs
        self.picked: list[dict | None] = [None, None]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("비교할 파일을 선택하세요."))
        grid = QGridLayout()
        self.edits: list[QLineEdit] = []
        for k, label in enumerate(("이전 파일", "새 파일")):
            ed = QLineEdit(); ed.setReadOnly(True); self.edits.append(ed)
            b = QToolButton(); b.setText("파일 선택"); b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            m = QMenu(b)
            m.addAction("파일 찾아보기", lambda k=k: self.browse(k))
            if open_docs:
                m.addSeparator()
                for name, path, tab in open_docs:
                    m.addAction(name, lambda k=k, n=name, p=path, t=tab: self.pick(k, {"name": n, "path": p, "tab": t}))
            b.setMenu(m)
            grid.addWidget(QLabel(label), k, 0); grid.addWidget(ed, k, 1); grid.addWidget(b, k, 2)
        lay.addLayout(grid)
        bb = QDialogButtonBox()
        self.go = bb.addButton("비교", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); cancel.setAutoDefault(False)
        settle_default(self, self.go)
        lay.addWidget(bb)
        if current is not None:
            name, path, tab = current
            self.pick(0, {"name": name, "path": path, "tab": tab})

    def pick(self, k: int, item: dict):
        self.picked[k] = item
        self.edits[k].setText(item.get("path") or item["name"])

    def browse(self, k: int):
        p, _ = QFileDialog.getOpenFileName(self, "파일 찾아보기", "", PDF_FILTER)
        if p:
            self.pick(k, {"name": os.path.basename(p), "path": os.path.abspath(p), "tab": None})

    @staticmethod
    def _key(item: dict | None):
        if not item:
            return None
        if item.get("path"):
            return ("p", os.path.normcase(os.path.abspath(item["path"])))
        return ("t", id(item.get("tab")) if item.get("tab") is not None else item["name"])

    def accept(self):
        a, b = self.picked
        if not a or not b or self._key(a) == self._key(b):
            QMessageBox.warning(self, "파일 비교", SAME_DOC); return
        super().accept()

    def values(self) -> tuple[dict, dict]:
        return self.picked[0], self.picked[1]


def _swatch(kind: str) -> QPixmap:
    pm = QPixmap(12, 12); pm.fill(QColor(*C.LINE[kind]))
    return pm


class ComparePanel(QWidget):
    """「변경 내용 목록」: 총 변경 항목 · 종류별 개수와 숨기기 · 텍스트/이미지 필터 · 이전/다음 · 항목 목록."""

    def __init__(self, tab, items: list[dict], name_a: str, name_b: str):
        super().__init__()
        self.setObjectName("comparepanel"); self.setMinimumWidth(240); self.setMaximumWidth(380)
        self.tab, self.items = tab, items
        self.hidden_kinds: set[str] = set()
        self.hidden_whats: set[str] = set()
        self.current = -1
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8)
        head = QLabel("변경 내용 목록"); head.setStyleSheet("font-weight:bold;"); lay.addWidget(head)
        lay.addWidget(QLabel(f"{name_a} ↔ {name_b}"))
        self.total = QLabel(); lay.addWidget(self.total)
        row = QHBoxLayout()
        self.kind_btns: dict[str, QToolButton] = {}
        n = C.counts(items)
        for kind in C.KINDS:
            b = QToolButton(); b.setCheckable(True); b.setChecked(True)
            b.setIcon(_swatch(kind)); b.setText(f"{C.KIND_NAMES[kind]} {n[kind]}")
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.toggled.connect(lambda on, k=kind: self.set_kind(k, on))
            self.kind_btns[kind] = b; row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        row = QHBoxLayout()
        self.prev_btn = QPushButton(icons.icon("prev", 14), "이전 변경 내용"); self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QPushButton(icons.icon("next", 14), "다음 변경 내용"); self.next_btn.clicked.connect(lambda: self.step(1))
        row.addWidget(self.prev_btn); row.addWidget(self.next_btn); lay.addLayout(row)
        row = QHBoxLayout()
        self.filter_btn = QToolButton(); self.filter_btn.setText("필터"); self.filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        fm = QMenu(self.filter_btn)
        self.filter_acts: dict[str, QAction] = {}
        for what, label in (("text", "텍스트"), ("image", "이미지")):
            a = fm.addAction(label); a.setCheckable(True); a.setChecked(True)
            a.toggled.connect(lambda on, w=what: self.set_what(w, on)); self.filter_acts[what] = a
        self.filter_btn.setMenu(fm)
        self.show_btn = QToolButton(); self.show_btn.setText("표시"); self.show_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sm = QMenu(self.show_btn)
        sm.addAction("모두 숨기기", lambda: self.set_all(False))
        sm.addAction("모두 표시", lambda: self.set_all(True))
        sm.addSeparator()
        self.kind_acts: dict[str, QAction] = {}
        for kind in C.KINDS:
            self.kind_acts[kind] = sm.addAction("", lambda k=kind: self.kind_btns[k].toggle())
        self.show_btn.setMenu(sm)
        row.addWidget(self.filter_btn); row.addWidget(self.show_btn); row.addStretch(); lay.addLayout(row)
        self.list = QListWidget(); self.list.itemClicked.connect(lambda it: self.select(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.list, 1)
        self._refill()

    # ── 보이는 항목 ──
    def visible(self) -> list[dict]:
        return [it for it in self.items if it["kind"] not in self.hidden_kinds
                and (it["what"] == "page" or it["what"] not in self.hidden_whats)]

    def _refill(self):
        self.list.clear()
        for it in self.visible():
            li = QListWidgetItem(C.item_title(it)); li.setData(Qt.ItemDataRole.UserRole, it["id"])
            li.setIcon(_swatch(it["kind"]))
            self.list.addItem(li)
            if it["id"] == self.current:
                li.setSelected(True)
        self.total.setText(f"총 변경 항목: {len(self.visible())}")
        for kind, a in self.kind_acts.items():
            a.setText(f"{C.KIND_NAMES[kind]} 항목 " + ("숨기기" if kind not in self.hidden_kinds else "표시"))
        has = bool(self.visible())
        self.prev_btn.setEnabled(has); self.next_btn.setEnabled(has)

    def _apply_layers(self):
        """숨긴 종류·대상의 레이어를 화면에서 끈다(파일은 그대로, 보기 설정)."""
        doc = self.tab.doc
        for ly in doc.layers():
            kind = next((k for k in C.KINDS for w in C.WHATS if ly["name"] == C.layer_name(k, w)), None)
            what = next((w for k in C.KINDS for w in C.WHATS if ly["name"] == C.layer_name(k, w)), None)
            if kind is None:
                continue
            on = kind not in self.hidden_kinds and (what == "page" or what not in self.hidden_whats)
            doc.set_layer(ly["number"], on)
        if self.current >= 0 and self.items[self.current] not in self.visible():
            self.current = -1
            self.tab.show_hits([], -1, jump=False)
        self.tab.view_changed()
        if hasattr(self.tab, "layers"):
            try:
                self.tab.layers.reload()
            except Exception:  # noqa: BLE001
                pass
        self._refill()

    def set_kind(self, kind: str, on: bool):
        (self.hidden_kinds.discard if on else self.hidden_kinds.add)(kind)
        self._apply_layers()

    def set_what(self, what: str, on: bool):
        (self.hidden_whats.discard if on else self.hidden_whats.add)(what)
        self._apply_layers()

    def set_all(self, on: bool):
        for b in self.kind_btns.values():
            b.blockSignals(True); b.setChecked(on); b.blockSignals(False)
        self.hidden_kinds = set() if on else set(C.KINDS)
        self._apply_layers()

    # ── 옮겨 다니기 ──
    def step(self, d: int):
        vis = self.visible()
        if not vis:
            return
        ids = [it["id"] for it in vis]
        if self.current in ids:
            k = (ids.index(self.current) + d) % len(ids)
        else:
            k = 0 if d > 0 else len(ids) - 1
        self.select(ids[k])

    def select(self, item_id: int):
        it = self.items[item_id]
        self.current = item_id
        rects = [fitz.Rect(r) for r in it["rects"]]
        self.tab.show_hits([(it["page"], rects)], 0, jump=True)
        for k in range(self.list.count()):
            li = self.list.item(k)
            li.setSelected(li.data(Qt.ItemDataRole.UserRole) == item_id)
            if li.isSelected():
                self.list.scrollToItem(li)


def attach_panel(tab, panel: QWidget) -> None:
    """결과 탭 보기 오른쪽에 패널을 붙인다(보기와 같은 칸에 나란히)."""
    sp = tab.split
    i = sp.indexOf(tab.view_stack)
    box = QWidget(sp); hl = QHBoxLayout(box); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(0)
    sp.insertWidget(i, box)
    hl.addWidget(tab.view_stack, 1); hl.addWidget(panel)   # 보기를 옮기면 가르개 칸에서 저절로 빠진다
    sp.setStretchFactor(i, 1)
    tab.compare_box, tab.compare_panel = box, panel
