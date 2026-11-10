"""글 엔진 전환의 화면 쪽: 글자 막대 밑줄·취소선, 속성 패널 새 서식 칸, 환경 설정 「주석 텍스트 박스 글꼴」.

python -m pytest app/tests/test_textbox_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_textbox_ui_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine import fonts as F  # noqa: E402
from app.engine import textbox as T  # noqa: E402

from app.ui import dlg_prefs as DP  # noqa: E402
from app.ui.inline_annot import InlineAnnot  # noqa: E402
from app.ui.inline_text import InlineText  # noqa: E402
from app.ui.window import MainWindow, save_settings  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _alt() -> str:
    fams = F.font_families()
    for name in ("Batang", "Gulim", "Dotum"):
        if name in fams:
            return name
    pytest.skip("맑은 고딕 말고 한글 설치 글꼴이 없음")


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    for k in ("edit_font", "comment_font", "addtext_style"):   # 다른 시험 파일에 새지 않게
        w.prefs.pop(k, None)
    save_settings(w.settings)
    w.close(); pump(0.05)


@pytest.fixture()
def pdf():
    p = os.path.join(tempfile.mkdtemp(), "글엔진.pdf")
    d = fitz.open(); d.new_page(width=595, height=842); d.save(p); d.close()
    return p


def click_page(v, x: float, y: float):
    v.goto(0); pump(0.1)
    it = v.items[0]
    vp = v.mapFromScene(it.pos().x() + x * v.zoom, it.pos().y() + y * v.zoom)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp); pump(0.05)


def type_and_leave(v, text: str):
    e = QInputMethodEvent("", []); e.setCommitString(text); QApplication.sendEvent(v, e); pump(0.02)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3)); pump(0.05)


def test_addtext_bar_underline_strike_reach_page(win, pdf):
    win.prefs.pop("addtext_style", None)
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("addtext")
    click_page(v, 100, 200)
    ed = next(o for o in v.findChildren(InlineText) if not o.done)
    ed.bar.underline.click(); ed.bar.strike.click(); pump(0.02)
    assert ed.style["underline"] and ed.style["strike"]
    assert ed.box.font().underline() and ed.box.font().strikeOut()
    ed.set_style(letter_spacing=2.0, line_height=1.6)
    assert ed.box.font().letterSpacing() > 0
    type_and_leave(v, "밑줄 글")
    o = T.text_objects(t.doc.fitz[0])[0]
    st = o["style"]
    assert st["underline"] and st["strike"] and st["letter_spacing"] == 2.0 and st["line_height"] == 1.6
    assert win.prefs["addtext_style"]["underline"]   # 마지막 서식 기억


def test_props_panel_new_fields_apply_to_object(win, pdf):
    from app.engine.document import Document
    d = Document(pdf)
    A.add_text(d, 0, fitz.Rect(72, 80, 300, 110), "패널 글", 14)
    d.save(pdf); d.close()
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_toggle("edit_all", True); pump(0.02)
    o = T.text_objects(t.doc.fitz[0])[0]
    c = o["rect"].tl + (o["rect"].br - o["rect"].tl) * 0.5
    click_page(v, *(c * t.doc.fitz[0].rotation_matrix))
    cp = t.props.edit_props
    assert v.sel is not None and v.sel.kind == "block" and v.sel.text == "패널 글"
    assert not cp.underline.isChecked() and cp.line_height.value() == 0
    cp.underline.setChecked(True); cp.spacing.setValue(1.5); cp.line_height.setValue(2.0)
    pump(0.45)
    st = T.text_objects(t.doc.fitz[0])[0]["style"]
    assert st["underline"] and st["letter_spacing"] == 1.5 and st["line_height"] == 2.0 and st["fontsize"] == 14
    assert [x["id"] for x in T.text_objects(t.doc.fitz[0])] == [o["id"]]   # 제자리
    win.on_action("undo"); pump(0.05)
    assert not T.text_objects(t.doc.fitz[0])[0]["style"]["letter_spacing"]


def test_prefs_comment_font_saved_and_used(win, pdf):
    fam = _alt()
    dlg = DP.PreferencesDialog(win, win.settings)
    dlg.show_page(next(n for n, p in enumerate(DP.PAGES) if p[0] == "font"))
    assert dlg.font_of(dlg.cf_font, "") == F.DEFAULT_FAMILY
    dlg.set_font(dlg.cf_font, fam)
    dlg.accept()
    assert win.prefs["comment_font"]["font"] == fam
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("textbox")
    click_page(v, 150, 300)
    ed = next(o for o in v.findChildren(InlineAnnot) if not o.done)
    assert ed.style["family"] == fam and ed.box.font().family() != ""
    type_and_leave(v, "글꼴 주석")
    a = next(x for x in t.doc.fitz[0].annots() if x.type[1] == "FreeText")
    assert A.freetext_info(t.doc, 0, a.xref)["family"] == fam
    fonts = {s["font"] for b in t.doc.fitz[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]}
    assert any(fam.lower() in f.lower() for f in fonts)
