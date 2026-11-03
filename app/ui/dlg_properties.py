"""속성 창(Ctrl+D): 왼쪽 단추 넷(설명 · 보기 · 보안 · 글꼴), 아래 「적용」·「취소」.

「적용」은 설명 쪽의 바뀐 칸과 보기 쪽(초기 보기)만 돌려준다. 실제로 쓰는 것은 window.properties.
단어 수는 settings["word_count"][경로] 에 기억해 다음에 열 때 바로 보인다(저장은 부르는 쪽).
"""
from __future__ import annotations

import os
import re
import subprocess

import fitz
from PySide6.QtCore import QDateTime, QLocale, Qt, QTimeZone
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..engine.document import Document
from . import dialogs as D
from . import theme

UNIT_PT = {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "pt": 1.0, "pc": 12.0}   # 단위 하나가 몇 pt
PAGES = (("desc", "설명"), ("view", "보기"), ("security", "보안"), ("fonts", "글꼴"))
MODES = (("none", "페이지만"), ("outlines", "북마크 및 페이지"), ("thumbs", "썸네일 및 페이지"))
LAYOUTS = (("default", "기본"), ("single", "단일 페이지 보기"), ("continuous", "단일 페이지 계속"),
           ("facing", "두 페이지 보기"), ("facing_continuous", "두 페이지 계속"))
ZOOMS = (("default", "기본"), ("actual", "실제 크기"), ("fit_page", "창 맞추기"), ("fit_width", "너비 맞춤"),
         ("fit_height", "높이 맞춤"), *((n / 100, f"{n}%") for n in (10, 25, 50, 75, 100, 125, 150, 200, 400, 800,
                                                                    1600, 2400, 3200, 6400)))


def nav_buttons(dlg: QDialog, pages, left: QVBoxLayout) -> QButtonGroup:
    """왼쪽 단추 줄(속성·환경 설정 같이 씀). 고른 단추는 강조색 글자에 옅은 바탕."""
    dlg.setStyleSheet(f"QPushButton#navpage {{ text-align: left; padding: 6px 12px; }}"
                      f"QPushButton#navpage:checked {{ background: {theme.CUR['SELECT_BG']}; color: {theme.ACCENT};"
                      f" border-color: {theme.ACCENT}; font-weight: bold; }}")
    group = QButtonGroup(dlg)
    for n, (_, name) in enumerate(pages):
        b = QPushButton(name); b.setCheckable(True); b.setAutoDefault(False); b.setMinimumWidth(140)
        b.setObjectName("navpage"); group.addButton(b, n); left.addWidget(b)
    left.addStretch(1)
    return group


def size_text(n: int) -> str:
    """바이트를 1024 진법으로, 소수 한 자리까지(0 이면 떼고)."""
    units = ("B", "KB", "MB", "GB", "TB")
    v, k = float(n), 0
    while v >= 1024 and k < len(units) - 1:
        v /= 1024; k += 1
    return f"{v:.1f}".rstrip("0").rstrip(".") + " " + units[k]


def date_text(raw: str | None) -> str:
    """PDF 날짜(「D:20261103143000+09'00'」)를 지역 시간 짧은 형식으로. 못 읽으면 빈칸."""
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?", raw or "")
    if not m:
        return ""
    y, mo, d, h, mi, s = (int(g) if g else dflt for g, dflt in zip(m.groups()[:6], (0, 1, 1, 0, 0, 0)))
    sign, oh, om = m.group(7), int(m.group(8) or 0), int(m.group(9) or 0)
    off = 0 if sign in (None, "Z", "z") else (1 if sign == "+" else -1) * (oh * 3600 + om * 60)
    dt = QDateTime.fromString(f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}", "yyyy-MM-ddTHH:mm:ss")
    if not dt.isValid():
        return ""
    dt.setTimeZone(QTimeZone(off) if sign else QTimeZone.systemTimeZone())
    return QLocale().toString(dt.toLocalTime(), QLocale.FormatType.ShortFormat)


def pdf_version(fmt: str | None) -> str:
    m = re.search(r"(\d+\.\d+)", fmt or "")
    return m.group(1) if m else "1.7"


def permissions(f: fitz.Document) -> list[tuple[str, bool]]:
    """보안 쪽 「문서 제한 사항 요약」 열 줄."""
    p = f.permissions
    if not (f.metadata or {}).get("encryption"):
        p = -1   # 암호화 안 된 문서는 다 허용
    on = lambda flag: bool(p & flag)   # noqa: E731
    modify, copy, form = on(fitz.PDF_PERM_MODIFY), on(fitz.PDF_PERM_COPY), on(fitz.PDF_PERM_FORM)
    return [("인쇄", on(fitz.PDF_PERM_PRINT) or on(fitz.PDF_PERM_PRINT_HQ)), ("문서 변경", modify),
            ("문서 조립", on(fitz.PDF_PERM_ASSEMBLE)), ("내용 복사", copy),
            ("접근성을 위해 내용 복제", on(fitz.PDF_PERM_ACCESSIBILITY)), ("페이지 내보내기", modify and copy),
            ("주석", on(fitz.PDF_PERM_ANNOTATE)), ("양식 필드 채우기", form), ("서명", form), ("임시 페이지 작성", form)]


def is_pdfa(f: fitz.Document) -> bool:
    try:
        return "pdfaid:part" in (f.get_xml_metadata() or "")
    except Exception:  # noqa: BLE001
        return False


def font_rows(f: fitz.Document) -> list[tuple[str, str, str, str]]:
    """(보이는 이름, 포함 표시, 유형, 인코딩) 이름 차례. 이름+유형이 같으면 하나만."""
    seen: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for i in range(f.page_count):
        for _xref, ext, ftype, base, _name, enc, *_ in f[i].get_fonts(full=True):
            sub = bool(re.match(r"^[A-Z]{6}\+", base or ""))
            name = (base or "")[7:] if sub else (base or "(이름 없음)")
            embed = "" if ext in ("n/a", "", None) else ("(내장 서브셋 글꼴)" if sub else "(포함된 글꼴)")
            kind = f"{ftype} (CID)" if ftype == "Type0" else ftype
            encoding = enc or "사용자 정의"
            seen.setdefault((name, kind), (name, embed, kind, encoding))
    return sorted(seen.values(), key=lambda r: r[0].lower())


def count_words(doc: Document, progress=None) -> int:
    n, total = 0, doc.page_count
    for i in range(total):
        with doc.lock:
            n += len(doc.fitz[i].get_text("words"))
        if progress:
            progress(i + 1, total, "계산 중")
    return n


class PropertiesDialog(QDialog):
    def __init__(self, parent, doc: Document, current: int, settings: dict, on_security=None):
        """on_security(kind): 「보안 정책」을 바꿨을 때 부른다(kind = "password" · "none"). 끝나면 다시 읽는다."""
        super().__init__(parent)
        self.doc, self.current, self.settings, self.on_security = doc, current, settings, on_security
        self.setWindowTitle("속성"); self.setFixedSize(720, 560)
        self.md = doc.metadata()
        self.view0 = doc.initial_view()
        lay = QVBoxLayout(self)
        row = QHBoxLayout(); lay.addLayout(row, 1)
        left = QVBoxLayout(); left.setSpacing(2); row.addLayout(left)
        self.stack = QStackedWidget(); row.addWidget(self.stack, 1)
        self.group = nav_buttons(self, PAGES, left)
        self.built: dict[str, QWidget] = {}
        for _ in PAGES:
            holder = QWidget(); QVBoxLayout(holder).setContentsMargins(12, 0, 0, 0); self.stack.addWidget(holder)
        self.group.idClicked.connect(self.show_page)
        bb = D._buttons(self, "적용")
        self.apply_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        lay.addWidget(bb)
        f = doc.fitz
        self.apply_btn.setEnabled(bool(f.permissions & fitz.PDF_PERM_MODIFY or not (f.metadata or {}).get("encryption"))
                                  and not is_pdfa(f))
        self.show_page(0)

    # ── 쪽 고르기(처음 열 때 짓는다) ─────────────────────────────
    def show_page(self, n: int):
        key = PAGES[n][0]
        self.group.button(n).setChecked(True)
        if key not in self.built:
            w = getattr(self, f"_build_{key}")()
            self.built[key] = w
            self.stack.widget(n).layout().addWidget(w)
        self.stack.setCurrentIndex(n)

    def _build_desc(self) -> QWidget:
        doc, md = self.doc, self.md
        w = QWidget(); f = QFormLayout(w); f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        path = doc.path or ""
        name = QLabel(doc.name); fn = name.font(); fn.setPointSizeF(10.5); fn.setBold(True); name.setFont(fn)
        f.addRow(name)
        if path:
            link = QLabel(f"<a href='#'>{path}</a>"); link.setWordWrap(True)
            link.linkActivated.connect(lambda _: self._reveal(path)); f.addRow(link)
            if os.path.isfile(path):
                f.addRow("파일 크기:", QLabel(size_text(os.path.getsize(path))))
        unit = (getattr(self.parent(), "prefs", None) or {}).get("unit", "cm")
        unit = unit if unit in UNIT_PT else "cm"
        r = doc.fitz[max(0, min(self.current, doc.page_count - 1))].rect
        k = UNIT_PT[unit]
        f.addRow("페이지 크기:", QLabel(f"{r.width / k:.2f} x {r.height / k:.2f} {unit}"))
        f.addRow("페이지 번호:", QLabel(str(doc.page_count)))
        wc = QHBoxLayout(); self.words = QLabel(""); wc.addWidget(self.words)
        self.count_btn = QPushButton("수"); self.count_btn.clicked.connect(self._count); wc.addWidget(self.count_btn); wc.addStretch(1)
        known = (self.settings.get("word_count") or {}).get(path) if path else None
        if known is not None:
            self.words.setText(f"{known:,}")
        f.addRow("단어:", wc)
        self.edits: dict[str, QLineEdit] = {}
        for key, label, hint in (("title", "제목:", "제목 입력"), ("subject", "주제:", "카테고리 입력"),
                                 ("keywords", "키워드:", "키워드 입력"), ("author", "작성자:", "작성자 입력")):
            e = QLineEdit(md.get(key) or ""); e.setPlaceholderText(hint); e.setReadOnly(not self.apply_btn.isEnabled())
            self.edits[key] = e; f.addRow(label, e)
        f.addRow("만든 날짜:", QLabel(date_text(md.get("creationDate"))))
        f.addRow("마지막 수정:", QLabel(date_text(md.get("modDate"))))
        f.addRow("PDF 작성 프로그램:", QLabel(md.get("producer") or ""))
        f.addRow("PDF 버전:", QLabel(pdf_version(md.get("format"))))
        f.addRow("프로그램:", QLabel(md.get("creator") or ""))
        enc = bool((doc.fitz.metadata or {}).get("encryption")) or bool(doc.password)
        f.addRow("암호:", QLabel("걸림" if enc else "없음"))
        f.addRow("양식:", QLabel("있음" if doc.fitz.is_form_pdf else "없음"))
        f.addRow("글자층:", QLabel("있음" if any(doc.has_text(i) for i in range(min(3, doc.page_count))) else "없음(스캔본일 수 있음)"))
        return w

    def _reveal(self, path: str):
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except OSError:
            pass

    def _count(self):
        n = D.ProgressDialog(self, "단어 수", count_words, self.doc).run()
        if n is None:
            return
        self.words.setText(f"{n:,}")
        if self.doc.path:
            self.settings.setdefault("word_count", {})[self.doc.path] = n

    def _build_view(self) -> QWidget:
        v = self.view0
        w = QWidget(); f = QFormLayout(w)
        title = QLabel("보기 설정"); fb = title.font(); fb.setBold(True); title.setFont(fb); f.addRow(title)
        self.mode = QComboBox(); self.layout_ = QComboBox(); self.zoom = QComboBox()
        for box, items, val in ((self.mode, MODES, v["mode"]), (self.layout_, LAYOUTS, v["layout"]), (self.zoom, ZOOMS, v["zoom"])):
            for data, text in items:
                box.addItem(text, data)
            i = box.findData(val)
            if i < 0 and isinstance(val, float):   # 목록에 없는 배율은 그 값을 한 줄 더해 보인다
                box.addItem(f"{val * 100:g}%", val); i = box.count() - 1
            box.setCurrentIndex(max(0, i))
        f.addRow("탐색 탭", self.mode); f.addRow("레이아웃", self.layout_); f.addRow("보기", self.zoom)
        pr = QHBoxLayout(); self.page = QSpinBox(); self.page.setRange(1, max(1, self.doc.page_count))
        self.page.setValue(v["page"] + 1); pr.addWidget(self.page); pr.addWidget(QLabel(f"/ {self.doc.page_count}")); pr.addStretch(1)
        f.addRow("페이지 열기", pr)
        for c in (self.mode, self.layout_, self.zoom, self.page):
            c.setEnabled(self.apply_btn.isEnabled())
        return w

    def _security_mode(self) -> str:
        sec = getattr(self.doc, "security", None)
        if sec is not None:   # 저장할 때 걸거나 뺄 암호가 있다
            return "password" if sec else "none"
        return "password" if (self.doc.fitz.metadata or {}).get("encryption") or self.doc.password else "none"

    def _build_security(self) -> QWidget:
        w = QWidget(); f = QFormLayout(w)
        self.policy = QComboBox(); self.policy.addItem("보호 없음", "none"); self.policy.addItem("암호 보호", "password")
        f.addRow("보안 정책", self.policy)
        title = QLabel("문서 제한 사항 요약"); fb = title.font(); fb.setBold(True); title.setFont(fb); f.addRow(title)
        self.perm_labels: list[QLabel] = []
        for name, _ in permissions(self.doc.fitz):
            lab = QLabel(); self.perm_labels.append(lab); f.addRow(name + ":", lab)
        self._fill_security()
        self.policy.currentIndexChanged.connect(self._policy_changed)
        return w

    def _fill_security(self):
        self.policy.blockSignals(True)
        self.policy.setCurrentIndex(self.policy.findData(self._security_mode()))
        self.policy.blockSignals(False)
        for lab, (_, ok) in zip(self.perm_labels, permissions(self.doc.fitz)):
            lab.setText("허용" if ok else "허용되지 않음")

    def _policy_changed(self):
        if self.on_security:
            self.on_security(self.policy.currentData())
        self._fill_security()   # 창을 취소했으면 원래 값으로 돌아간다

    def _build_fonts(self) -> QWidget:
        tree = QTreeWidget(); tree.setHeaderHidden(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with self.doc.lock:
                rows = font_rows(self.doc.fitz)
        finally:
            QApplication.restoreOverrideCursor()
        for name, embed, kind, enc in rows:
            it = QTreeWidgetItem([f"{name} {embed}".strip()])
            QTreeWidgetItem(it, [f"유형: {kind}"]); QTreeWidgetItem(it, [f"인코딩: {enc}"])
            tree.addTopLevelItem(it)
        tree.expandAll()
        return tree

    # ── 돌려줄 값 ────────────────────────────────────────────────
    def changed_metadata(self) -> dict:
        """설명 쪽에서 바뀐 칸만."""
        if "desc" not in self.built:
            return {}
        return {k: e.text() for k, e in self.edits.items() if e.text() != (self.md.get(k) or "")}

    def initial_view(self) -> dict | None:
        """보기 쪽을 열어 봤으면 그 값(바뀌었는지는 Document.set_initial_view 가 본다)."""
        if "view" not in self.built:
            return None
        return {"mode": self.mode.currentData(), "layout": self.layout_.currentData(),
                "zoom": self.zoom.currentData(), "page": self.page.value() - 1}
