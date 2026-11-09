"""교정·숨은 데이터: 처음 안내 창 · 교정 적용 확인 창 · 교정 띠 · 숨은 데이터 고르기 창과 진행."""
from __future__ import annotations

import fitz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QProgressDialog, QPushButton, QVBoxLayout)

from ..engine import tools as T
from .dialogs import settle_default

REDACT_TIP = ("선택한 민감한 텍스트 및 이미지를 검은색으로 완전히 가릴 수 있습니다. 교정에는 두 단계가 필요합니다:",
              ["1. 교정하기를 원하는 영역 선택", "2. 「적용」 을 선택하여 교정 적용"])
HIDDEN_TIP = ("PDF에서 숨겨진 데이터를 검색 및 삭제하여 배포 시 PDF에 포함된 민감한 정보가 유출되지 않도록 하세요.",
              ["1. 삭제할 숨겨진 데이터 선택", "2. 「적용」 을 눌러 삭제"])
APPLY_TEXT = "수정을 통해 민감한 정보를 영구적으로 제거할 수 있습니다. 문서가 저장되면 이 작업은 취소할 수 없습니다. 진행하시겠습니까?"
BAR_TEXT = "교정을 영구적으로 적용합니다."
HIDDEN_CONFIRM = "선택한 숨겨진 데이터를 삭제하시겠습니까? 문서를 저장한 후에는 이 작업을 취소할 수 없습니다."
HIDDEN_FAIL = "숨겨진 데이터 삭제에 실패했습니다."
HIDDEN_ITEMS = (("metadata", "메타데이터"), ("attachments", "첨부 파일"), ("bookmarks", "북마크"),
                ("annotations", "주석 및 마크업"), ("form_fields", "양식 필드"), ("hidden_text", "숨겨진 텍스트"),
                ("hidden_layers", "숨겨진 레이어"), ("search_index", "내장된 검색 인덱스"),
                ("cropped", "삭제했거나 자른 콘텐츠"), ("links_js", "링크, 작업 및 JavaScript"))


def _save(win):
    try:
        from .window import save_settings
        save_settings(win.settings)
    except Exception:  # noqa: BLE001
        pass


class TipDialog(QDialog):
    """처음 한 번 보이는 안내 창. 「다시 보지 않기」."""

    def __init__(self, parent, title: str, text: str, steps: list[str]):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lb = QLabel(text); lb.setWordWrap(True); lay.addWidget(lb)
        for s in steps:
            lay.addWidget(QLabel(s))
        self.never = QCheckBox("다시 보지 않기"); lay.addWidget(self.never)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        settle_default(self, bb.button(QDialogButtonBox.StandardButton.Ok))
        bb.accepted.connect(self.accept); lay.addWidget(bb)


def show_tip(win, key: str, title: str, tip) -> None:
    """prefs[key] 가 켜져 있지 않으면 안내 창을 띄우고, 「다시 보지 않기」 면 기억한다."""
    if win.prefs.get(key):
        return
    dlg = TipDialog(win, title, tip[0], tip[1])
    dlg.exec()
    if dlg.never.isChecked():
        win.prefs[key] = True; _save(win)


# ── 교정 ───────────────────────────────────────────────────────
class RedactApplyDialog(QDialog):
    def __init__(self, parent, meta: bool = False):
        super().__init__(parent)
        self.setWindowTitle("수정")
        lay = QVBoxLayout(self)
        lb = QLabel(APPLY_TEXT); lb.setWordWrap(True); lay.addWidget(lb)
        self.meta = QCheckBox("파일에서 메타데이터 삭제"); self.meta.setChecked(meta); lay.addWidget(self.meta)
        bb = QDialogButtonBox()
        bb.addButton("계속", QDialogButtonBox.ButtonRole.AcceptRole); bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        settle_default(self, bb.buttons()[0])
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)


class RedactBar(QFrame):
    """교정 표시를 다는 동안 문서 위 한 줄: [교정 적용]."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setObjectName("redactBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout(self); h.setContentsMargins(12, 4, 8, 4); h.setSpacing(8)
        self.label = QLabel(BAR_TEXT); h.addWidget(self.label, 1)
        self.run = QPushButton("교정 적용"); self.run.setDefault(True); h.addWidget(self.run)
        self.close_btn = QPushButton("✕"); self.close_btn.setFlat(True); self.close_btn.setFixedWidth(28); h.addWidget(self.close_btn)
        self.run.clicked.connect(lambda: apply(self.tab.win, self.tab))
        self.close_btn.clicked.connect(self.hide)
        self.restyle()
        self.hide()

    def restyle(self):
        from . import theme
        c = theme.CUR or theme.LIGHT
        self.setStyleSheet(f"QFrame#redactBar {{ background: {c.get('WARN_BG', '#FDEBEC')}; border: none;"
                           f" border-bottom: 1px solid {c.get('WARN_LINE', '#F5C9CB')}; }}")

    def showEvent(self, e):
        self.restyle()
        super().showEvent(e)


def attach(tab, layout) -> RedactBar:
    """교정 띠를 넣고 교정 도구로 새로 단 표시에 도구 속성(오버레이 글 등)을 얹는다."""
    bar = RedactBar(tab)
    layout.insertWidget(0, bar)
    tab._redact_seen = None
    tab.viewer.toolDone.connect(lambda tool: tool == "redact" and _style_new(tab))
    return bar


def _redact_xrefs(doc: fitz.Document) -> set[tuple[int, int]]:
    out = set()
    for pno in range(doc.page_count):
        page = doc[pno]
        if not page.first_annot:
            continue
        out.update((pno, a.xref) for a in page.annots(types=[fitz.PDF_ANNOT_REDACT]))
    return out


def tool_style(props: dict) -> dict | None:
    """교정 도구의 오버레이 속성(바꾼 것이 없으면 None)."""
    st = {"text": props.get("redact_text") or "", "fontsize": float(props.get("redact_fontsize") or 0),
          "text_color": tuple(props.get("redact_text_color") or (0, 0, 0)), "align": int(props.get("redact_align") or 0)}
    return st if (st["text"] or st["fontsize"] or st["align"] or st["text_color"] != (0, 0, 0)) else None


def tool_started(win, tab) -> None:
    """교정 도구를 켰다: 처음 안내 · 띠 · 지금 있는 표시 기억(새로 단 것에만 속성을 얹게)."""
    show_tip(win, "redact_tip_off", "교정 표시", REDACT_TIP)
    tab._redact_seen = _redact_xrefs(tab.doc.fitz)
    bar = getattr(tab, "redact_bar", None)
    if bar is not None:
        bar.show()


def _style_new(tab) -> None:
    st = tool_style(tab.viewer.props)
    now = _redact_xrefs(tab.doc.fitz)
    seen = tab._redact_seen if tab._redact_seen is not None else now
    tab._redact_seen = now
    if not st:
        return
    pages = set()
    for pno, x in now - seen:
        T.set_redact_style(tab.doc.fitz, pno, x, **st); pages.add(pno)
    if pages:
        tab.viewer.refresh_pages(sorted(pages))


def marked(tab) -> None:
    """교정 표시를 달았다(검색 & 교정 등): 띠를 보인다."""
    bar = getattr(tab, "redact_bar", None)
    if bar is not None:
        bar.show()


def apply(win, tab) -> bool:
    """교정 적용 확인 창 → 적용(메타데이터 체크면 문서 정보도). 적용했으면 True."""
    d = tab.doc
    if not _redact_xrefs(d.fitz):
        win.say("교정 표시가 없습니다"); return False
    dlg = RedactApplyDialog(win, bool(win.prefs.get("redact_meta", False)))
    if not dlg.exec():
        return False
    meta = dlg.meta.isChecked()
    if win.prefs.get("redact_meta", False) != meta:
        win.prefs["redact_meta"] = meta; _save(win)
    d.snapshot()
    with d.lock:
        n = T.apply_redactions(d.fitz, metadata=meta)
    tab.changed(None)
    bar = getattr(tab, "redact_bar", None)
    if bar is not None:
        bar.hide()
    win.say(f"교정 {n}곳을 적용했습니다")
    return True


# ── 숨은 데이터 ───────────────────────────────────────────────────
class HiddenDataDialog(QDialog):
    """삭제할 숨은 데이터 고르기. 「겹치는 개체」 는 보이기만(꺼짐)."""

    def __init__(self, parent, chosen: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("숨겨진 데이터 삭제")
        chosen = chosen or {}
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("삭제할 숨겨진 데이터를 선택합니다."))
        self.cbs = {}
        for key, label in HIDDEN_ITEMS:
            cb = QCheckBox(label); cb.setChecked(bool(chosen.get(key, True))); self.cbs[key] = cb; lay.addWidget(cb)
        self.overlap = QCheckBox("겹치는 개체 (지원 안 함)"); self.overlap.setEnabled(False); lay.addWidget(self.overlap)
        bb = QDialogButtonBox()
        self.ok = bb.addButton("적용", QDialogButtonBox.ButtonRole.AcceptRole)
        settle_default(self, self.ok)
        bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        for cb in self.cbs.values():
            cb.toggled.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        self.ok.setEnabled(any(cb.isChecked() for cb in self.cbs.values()))

    def values(self) -> dict:
        return {k: cb.isChecked() for k, cb in self.cbs.items()}


def hidden(win, tab) -> bool:
    """안내 → 고르기 → 확인 → 진행 → 삭제. 지웠으면 True."""
    show_tip(win, "hidden_tip_off", "숨겨진 데이터 삭제", HIDDEN_TIP)
    dlg = HiddenDataDialog(win, win.prefs.get("hidden_data") or {})
    if not dlg.exec():
        return False
    vals = dlg.values()
    win.prefs["hidden_data"] = vals; _save(win)
    box = QMessageBox(QMessageBox.Icon.Question, "숨겨진 데이터 삭제", HIDDEN_CONFIRM, parent=win)
    go = box.addButton("제거", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(go)
    box.exec()
    if box.clickedButton() is not go:
        return False
    d = tab.doc
    prog = QProgressDialog("숨겨진 데이터 삭제...", "", 0, 100, win)
    prog.setWindowTitle("숨겨진 데이터 삭제"); prog.setCancelButton(None)
    prog.setWindowModality(Qt.WindowModality.WindowModal); prog.setMinimumDuration(300)

    def step(done, total, _msg):
        prog.setValue(int(done * 100 / max(1, total))); QApplication.processEvents()

    d.snapshot()
    try:
        with d.lock:
            T.remove_hidden_data(d.fitz, progress=step, **vals)
    except Exception:  # noqa: BLE001
        prog.close()
        try:   # 반쯤 지운 채로 두지 않는다
            d.undo()
        except Exception:  # noqa: BLE001
            pass
        tab.changed(None, structural=True)
        QMessageBox.warning(win, "숨겨진 데이터 삭제", HIDDEN_FAIL)
        return False
    prog.close()
    tab.changed(None, structural=True)
    win.say("숨겨진 데이터를 삭제했습니다")
    return True


# ── 속성 패널의 교정 카드(채우기 색 · 오버레이 글 · 크기 · 글자 색 · 맞춤) ─────────────
class RedactProps(QFrame):
    """교정 도구를 켰거나 교정 표시를 골랐을 때 속성 패널에 보이는 카드."""

    def __init__(self, viewer):
        super().__init__()
        from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit
        from .dialogs import ColorButton
        from .sidebar import group_card
        self.viewer = viewer
        self.sel = None
        self._loading = False
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        box, f = group_card("교정"); lay.addWidget(box)
        self.fill = ColorButton((0, 0, 0)); f.addRow("채우기 색", self.fill)
        self.use_text = QCheckBox("오버레이 텍스트"); f.addRow(self.use_text)
        self.text = QLineEdit(); self.text.setPlaceholderText("교정 자리에 쓸 글"); f.addRow("글", self.text)
        self.size = QDoubleSpinBox(); self.size.setRange(0, 72); self.size.setDecimals(1); self.size.setSpecialValueText("자동")
        f.addRow("글자 크기", self.size)
        self.color = ColorButton((1, 0, 0)); f.addRow("글자 색", self.color)
        self.align = QComboBox(); self.align.addItems(list(T.REDACT_ALIGN)); f.addRow("맞춤", self.align)
        for w in (self.fill, self.color):
            w.clicked.connect(self._edited)
        self.use_text.toggled.connect(self._edited); self.text.editingFinished.connect(self._edited)
        self.size.valueChanged.connect(self._edited); self.align.currentIndexChanged.connect(self._edited)
        self.hide()

    def _redact_annot(self, sel):
        if sel is None or getattr(sel, "kind", "") != "annot" or self.viewer is None:
            return None
        try:
            from ..engine import annots as A
            a = A.get(self.viewer.doc, sel.page, sel.xref)
        except Exception:  # noqa: BLE001
            return None
        return a if a is not None and a.type[0] == fitz.PDF_ANNOT_REDACT else None

    def show_sel(self, sel, tool: str) -> None:
        a = self._redact_annot(sel)
        self.sel = sel if a is not None else None
        on = a is not None or (sel is None and tool == "redact")
        self.setVisible(on)
        if not on:
            return
        if a is not None:
            st = T.redact_style(self.viewer.doc.fitz, a.xref)
        else:
            p = self.viewer.props
            st = {"fill": p.get("redact_fill", (0, 0, 0)), "text": p.get("redact_text", ""),
                  "fontsize": p.get("redact_fontsize", 0), "text_color": p.get("redact_text_color", (1, 0, 0)),
                  "align": p.get("redact_align", 0)}
        self._loading = True
        try:
            self.fill.color = tuple(st["fill"]) if st.get("fill") else (0, 0, 0); self.fill._paint()
            self.use_text.setChecked(bool(st.get("text"))); self.text.setText(st.get("text") or "")
            self.size.setValue(float(st.get("fontsize") or 0))
            self.color.color = tuple(st.get("text_color") or (1, 0, 0)); self.color._paint()
            self.align.setCurrentIndex(int(st.get("align") or 0))
            self.text.setEnabled(self.use_text.isChecked())
        finally:
            self._loading = False

    def values(self) -> dict:
        return {"fill": self.fill.color, "text": self.text.text() if self.use_text.isChecked() else "",
                "fontsize": self.size.value(), "text_color": self.color.color, "align": self.align.currentIndex()}

    def _edited(self, *_):
        self.text.setEnabled(self.use_text.isChecked())
        if self._loading:
            return
        v = self.values()
        if self.sel is not None:
            d = self.viewer.doc
            d.snapshot()
            with d.lock:
                T.set_redact_style(d.fitz, self.sel.page, self.sel.xref, **v)
            d.modified = True
            self.viewer.refresh_pages([self.sel.page])
        else:
            p = self.viewer.props
            p["redact_fill"] = v["fill"]; p["redact_text"] = v["text"]; p["redact_fontsize"] = v["fontsize"]
            p["redact_text_color"] = v["text_color"]; p["redact_align"] = v["align"]
