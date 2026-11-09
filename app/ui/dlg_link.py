"""링크 창(쪽 이동·웹·파일 셋과 외관)과 쪽 고르기 창. 값만 모아 돌려주고, 넣기·고치기는 window.py 가 한다.

기억하는 값(settings.json 의 prefs): link_style(기본 외관) · link_recent_urls(최근 URL 10개, 최근 것부터) · link_relative.
"""
from __future__ import annotations

import fitz
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QIntValidator, QPixmap, QWheelEvent
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollBar, QSpinBox, QVBoxLayout)

from ..engine import annots as A
from ..engine.document import Document
from .dialogs import ColorButton, settle_default

RECENT_MAX = 10
LINES = [("solid", "실선"), ("dash", "대시"), ("underline", "밑줄")]
HIGHLIGHTS = [("none", "없음"), ("invert", "거꾸로"), ("push", "밀어넣기"), ("outline", "윤곽선")]


def page_error(total: int) -> str:
    return f"쪽 번호는 1에서 {total} 사이로 입력하십시오."


def remember_url(prefs: dict, url: str):
    """최근 URL 맨 앞에 넣는다(같은 것은 앞으로 올리고 10개까지)."""
    url = url.strip()
    if not url:
        return
    rec = [u for u in prefs.get("link_recent_urls", []) if u != url]
    prefs["link_recent_urls"] = [url, *rec][:RECENT_MAX]


class PagePickDialog(QDialog):
    """쪽 미리보기를 굴려 이동할 쪽을 고른다."""
    WIDTH = 300

    def __init__(self, parent, doc: Document, page: int):
        super().__init__(parent)
        self.setWindowTitle("페이지 선택")
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("스크롤하여 페이지선택"))
        row = QHBoxLayout()
        self.preview = QLabel(); self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(self.WIDTH + 16, int(self.WIDTH * 1.42) + 16)
        self.preview.setStyleSheet("background:#808080;")
        self.bar = QScrollBar(Qt.Orientation.Vertical)
        self.bar.setRange(0, max(0, doc.page_count - 1)); self.bar.setPageStep(1)
        row.addWidget(self.preview); row.addWidget(self.bar)
        lay.addLayout(row)
        self.label = QLabel(); lay.addWidget(self.label)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = bb.button(QDialogButtonBox.StandardButton.Ok); ok.setText("선택"); ok.setDefault(True)
        settle_default(self, ok)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.bar.valueChanged.connect(self._show)
        self.bar.setValue(max(0, min(page, doc.page_count - 1)))
        self._show(self.bar.value())

    def _show(self, i: int):
        self.label.setText(f"페이지로 연결 {i + 1}")
        with self.doc.lock:
            pg = self.doc.fitz[i]
            r = pg.rect
            z = min(self.WIDTH / max(r.width, 1), self.WIDTH * 1.42 / max(r.height, 1))
            pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(img))

    def wheelEvent(self, e: QWheelEvent):
        self.bar.setValue(self.bar.value() + (-1 if e.angleDelta().y() > 0 else 1))

    def page(self) -> int:
        return self.bar.value()


class LinkDialog(QDialog):
    """링크 넣기·고치기. info 를 주면 그 링크 값으로 채운다(고치기)."""

    def __init__(self, parent, doc: Document, current: int, prefs: dict, info: dict | None = None,
                 view_dest: tuple[fitz.Point, float] | None = None):
        super().__init__(parent)
        self.setWindowTitle("링크")
        self.setMinimumWidth(440)
        self.doc, self.prefs, self.info = doc, prefs, info
        self.view_dest = view_dest or (fitz.Point(0, 0), 0.0)
        total = doc.page_count
        lay = QVBoxLayout(self)

        # 옵션
        box = QGroupBox("옵션"); g = QGridLayout(box); g.setColumnStretch(1, 1)
        self.r_goto = QRadioButton("페이지보기로 이동")
        self.r_web = QRadioButton("웹 페이지 열기")
        self.r_file = QRadioButton("파일 열기")
        self.kinds = QButtonGroup(self)
        for n, r in enumerate((self.r_goto, self.r_web, self.r_file)):
            self.kinds.addButton(r, n)
        self.page = QLineEdit(str(current + 1)); self.page.setPlaceholderText("페이지 번호 입력")
        self.page.setValidator(QIntValidator(1, max(1, total), self))
        self.pick = QPushButton("페이지 선택"); self.pick.clicked.connect(self._pick_page)
        self.url = QComboBox(); self.url.setEditable(True); self.url.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.url.addItems(prefs.get("link_recent_urls", []))
        self.url.setCurrentIndex(-1); self.url.lineEdit().setPlaceholderText("웹사이트 URL을 입력하세요")
        self.file = QLineEdit(); self.file.setPlaceholderText("파일 경로")
        self.browse = QPushButton("찾기"); self.browse.clicked.connect(self._pick_file)
        for b in (self.pick, self.browse):
            b.setAutoDefault(False)   # Enter 는 「적용」
        self.relative = QCheckBox("문서 기준 상대 경로로 저장")
        self.relative.setChecked(bool(prefs.get("link_relative", False)))
        self.relative.setEnabled(bool(doc.path))
        g.addWidget(self.r_goto, 0, 0, 1, 3)
        g.addWidget(self.page, 1, 1); g.addWidget(self.pick, 1, 2)
        g.addWidget(self.r_web, 2, 0, 1, 3)
        g.addWidget(self.url, 3, 1, 1, 2)
        g.addWidget(self.r_file, 4, 0, 1, 3)
        g.addWidget(self.file, 5, 1); g.addWidget(self.browse, 5, 2)
        g.addWidget(self.relative, 6, 1, 1, 2)
        g.setColumnMinimumWidth(0, 18)
        lay.addWidget(box)

        # 외관
        st = {**A.LINK_STYLE, **prefs.get("link_style", {})}
        if info:
            st = dict(info["style"])
        box = QGroupBox("외관"); f = QGridLayout(box); f.setColumnStretch(1, 1)
        self.visible = QCheckBox("보이는 직사각형")
        self.color = ColorButton(tuple(st["color"]))
        self.width_ = QSpinBox(); self.width_.setRange(1, 10)
        self.line = QComboBox(); self.line.addItems([t for _, t in LINES])
        self.highlight = QComboBox(); self.highlight.addItems([t for _, t in HIGHLIGHTS])
        self.link_type = QComboBox(); self.link_type.addItems(["직사각형 표시", "직사각형 표시 안 함"])
        f.addWidget(self.visible, 0, 0, 1, 2)
        for n, (text, w) in enumerate((("색상", self.color), ("선 두께", self.width_), ("선 스타일", self.line),
                                       ("하이라이트 스타일", self.highlight), ("연결 종류", self.link_type)), 1):
            f.addWidget(QLabel(text), n, 0)
            f.addWidget(w, n, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(box)

        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.bb.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText("적용"); self.ok.setDefault(True)
        settle_default(self, self.ok)
        self.bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.bb.accepted.connect(self.accept); self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)

        # 값 채우기
        self.visible.setChecked(bool(st["visible"]))
        self.width_.setValue(int(st["width"]))
        self.line.setCurrentIndex(next((n for n, (k, _) in enumerate(LINES) if k == st["line"]), 0))
        self.highlight.setCurrentIndex(next((n for n, (k, _) in enumerate(HIGHLIGHTS) if k == st["highlight"]), 1))
        self.r_goto.setChecked(True)
        if info:
            if info["kind"] == "goto":
                self.page.setText(str(info["to_page"] + 1))
            elif info["kind"] == "uri":
                self.r_web.setChecked(True); self.url.setCurrentText(info["uri"])
            elif info["kind"] == "file":
                self.r_file.setChecked(True); self.file.setText(info["file"])

        self.kinds.idToggled.connect(lambda *_: self._sync())
        self.page.textChanged.connect(self._sync)
        self.url.currentTextChanged.connect(self._sync)
        self.file.textChanged.connect(self._sync)
        self.visible.toggled.connect(self._visible)
        self.link_type.currentIndexChanged.connect(lambda n: self.visible.setChecked(n == 0))
        self._visible(self.visible.isChecked())
        self._sync()

    # ── 칸 켜고 끄기 ─────────────────────────────────────────
    def kind(self) -> str:
        return ("goto", "uri", "file")[max(0, self.kinds.checkedId())]

    def _visible(self, on: bool):
        self.width_.setEnabled(on); self.line.setEnabled(on)
        self.link_type.blockSignals(True); self.link_type.setCurrentIndex(0 if on else 1); self.link_type.blockSignals(False)

    def _sync(self):
        k = self.kind()
        self.page.setEnabled(k == "goto"); self.pick.setEnabled(k == "goto")
        self.url.setEnabled(k == "uri")
        for w in (self.file, self.browse):
            w.setEnabled(k == "file")
        self.relative.setEnabled(k == "file" and bool(self.doc.path))
        text = {"goto": self.page.text(), "uri": self.url.currentText(), "file": self.file.text()}[k]
        self.ok.setEnabled(bool(text.strip()))

    def _pick_page(self):
        cur = int(self.page.text()) - 1 if self.page.text().isdigit() else 0
        dlg = PagePickDialog(self, self.doc, cur)
        if dlg.exec():
            self.page.setText(str(dlg.page() + 1))

    def _pick_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "열 파일")
        if p:
            self.file.setText(p)

    # ── 값 ───────────────────────────────────────────────────
    def accept(self):
        if self.kind() == "goto":
            n = int(self.page.text()) if self.page.text().isdigit() else 0
            if not 1 <= n <= self.doc.page_count:
                QMessageBox.warning(self, self.windowTitle(), page_error(self.doc.page_count))
                self.page.setFocus(); self.page.selectAll()
                return
        super().accept()

    def style(self) -> dict:
        return {"visible": self.visible.isChecked(), "color": tuple(self.color.color or A.LINK_STYLE["color"]),
                "width": self.width_.value(), "line": LINES[self.line.currentIndex()][0],
                "highlight": HIGHLIGHTS[self.highlight.currentIndex()][0]}

    def target(self) -> dict:
        k = self.kind()
        if k == "goto":
            to_page = int(self.page.text()) - 1
            info = self.info
            if info and info["kind"] == "goto" and info["to_page"] == to_page:
                return {"to_page": to_page, "to_point": info["to_point"], "zoom": info["zoom"]}   # 고치기: 담아 둔 자리 그대로
            pt, zoom = self.view_dest
            return {"to_page": to_page, "to_point": pt, "zoom": zoom}
        if k == "uri":
            return {"uri": self.url.currentText().strip()}
        path = self.file.text().strip()
        return {"file": A.link_file_path(path, self.doc.path, self.relative.isChecked() and self.relative.isEnabled())}

    def values(self) -> dict:
        return {"target": self.target(), "style": self.style()}

    def remember(self):
        """적용한 외관을 다음 링크의 기본으로, 웹 주소는 최근 목록에 넣는다(prefs 만 바꾼다. 저장은 부르는 쪽)."""
        st = self.style()
        self.prefs["link_style"] = {**st, "color": list(st["color"])}
        if self.kind() == "uri":
            remember_url(self.prefs, self.url.currentText())
        if self.kind() == "file" and self.doc.path:
            self.prefs["link_relative"] = self.relative.isChecked()
