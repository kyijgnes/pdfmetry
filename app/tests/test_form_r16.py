"""라운드 16 가: 양식 칸 만들기 · 여러 칸 고르기 · 우클릭 · 복사본 · 탭 순서 · 인식 시험.

python -m pytest app/tests/test_form_r16.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pdfm_form_r16_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui import context_menu as CM  # noqa: E402
from app.ui import dialogs as D  # noqa: E402
from app.ui import form_tools as FT  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(pages: int = 2, draw=None) -> str:
    d = fitz.open()
    for _ in range(pages):
        d.new_page(width=595, height=842)
    if draw:
        draw(d)
    out = os.path.join(tempfile.mkdtemp(), "양식.pdf")
    d.save(out); d.close()
    return out


def mem_doc(pages: int = 1) -> Document:
    d = Document(); d.fitz.close()
    f = fitz.open()
    for _ in range(pages):
        f.new_page()
    d.fitz = f
    return d


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)
    FT.CLIP.clear()


def open_form(win, pages=2, draw=None):
    win.open_path(make_pdf(pages, draw)); pump(0.25)
    t = win.cur()
    win.ribbon.set_toggle("form_edit", True); win.on_toggle("form_edit", True)
    return t, t.viewer


def vp(v, i: int, x: float, y: float) -> QPoint:
    return v.mapFromScene(v.from_page_rect(i, fitz.Rect(x, y, x, y)).topLeft())


def click(v, i, x, y, mod=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, mod, vp(v, i, x, y)); pump(0.03)


def widgets(t, i=0):
    return list(t.doc.fitz[i].widgets())


# ── 엔진 ────────────────────────────────────────────────────────
def test_first_name_has_number_and_radio_group_spans_pages():
    d = mem_doc(2)
    x = A.add_field(d, 0, fitz.Rect(50, 50, 200, 80), "text")
    A.add_field(d, 0, fitz.Rect(50, 100, 64, 114), "radio")
    A.add_field(d, 1, fitz.Rect(50, 100, 64, 114), "radio")
    names = [w.field_name for i in range(2) for w in d.fitz[i].widgets()]
    assert names == ["텍스트1", "라디오 버튼1", "라디오 버튼1"]   # 다른 쪽에 찍은 라디오도 한 묶음
    assert A.add_field(d, 0, fitz.Rect(50, 150, 200, 180), "text") != x
    assert [w.field_name for w in d.fitz[0].widgets()][-1] == "텍스트2"


def test_duplicate_fields_grid():
    d = mem_doc()
    x = A.add_field(d, 0, fitz.Rect(50, 50, 150, 70), "text", value="값")
    made = A.duplicate_fields(d, [(0, x)], 3, 2, 120, 30)   # 아래 3줄 × 옆 2칸 = 원본 포함 6칸
    ws = list(d.fitz[0].widgets())
    assert len(made) == 5 and len(ws) == 6
    assert {(round(w.rect.x0), round(w.rect.y0)) for w in ws} == {(50 + 120 * c, 50 + 30 * r) for r in range(3) for c in range(2)}
    assert len({w.field_name for w in ws}) == 6
    d.undo()
    assert len(list(d.fitz[0].widgets())) == 1   # 되돌리기 한 번


def test_tab_order_survives_save(tmp_path):
    d = mem_doc()
    xs = [A.add_field(d, 0, fitz.Rect(50, 50 + 40 * k, 150, 70 + 40 * k), "text") for k in range(3)]
    d.fitz.new_page()
    want = [xs[2], xs[0], xs[1]]
    A.set_tab_order(d, 0, want)
    out = str(tmp_path / "탭.pdf"); d.fitz.save(out)
    g = fitz.open(out)
    assert [w.xref for w in g[0].widgets()] == want
    g.close()


def test_recognize_checkbox_and_nothing():
    d = mem_doc()
    p = d.fitz[0]
    p.draw_rect(fitz.Rect(100, 100, 112, 112), color=(0, 0, 0))
    p.draw_line((100, 200), (300, 200))
    assert A.recognize_fields(d) == 2
    assert sorted(w.field_type_string for w in d.fitz[0].widgets()) == ["CheckBox", "Text"]
    e = mem_doc()
    assert A.recognize_fields(e) == 0 and not e.can_undo()


# ── 화면 ────────────────────────────────────────────────────────
def test_click_makes_default_size(win):
    t, v = open_form(win)
    win.on_tool("ftext"); pump(0.05)
    click(v, 0, 100, 100)
    ws = widgets(t)
    assert len(ws) == 1 and ws[0].field_name == "텍스트1"
    r = ws[0].rect
    assert abs(r.width - 150) < 0.5 and abs(r.height - 30) < 0.5 and abs(r.x0 - 100) < 1.5 and abs(r.y0 - 100) < 1.5
    assert v.tool == "form"   # 도구 선택 유지가 꺼져 있으면 한 칸 뒤 선택으로
    win.on_tool("fcheck"); pump(0.05)
    click(v, 0, 100, 300)
    assert abs(widgets(t)[-1].rect.width - 25) < 0.5


def test_keep_tool_remembered(win):
    t, v = open_form(win)
    win.ribbon.set_toggle("form_keep", True); win.on_toggle("form_keep", True)
    win.on_tool("ftext"); pump(0.05)
    for k in range(3):
        click(v, 0, 100, 100 + 60 * k)
    assert len(widgets(t)) == 3 and v.tool == "ftext"
    w2 = MainWindow()
    try:
        assert FT.keep_on(w2.prefs)
    finally:
        w2.close()
    win.on_toggle("form_keep", False)
    assert not FT.keep_on(win.prefs)


def _three(t):
    d = t.doc
    return [A.add_field(d, 0, fitz.Rect(60 + 30 * k, 100 + 50 * k, 200 + 30 * k, 120 + 50 * k), "text") for k in range(3)]


def test_ctrl_click_three_then_align_left_without_dialog(win, monkeypatch):
    t, v = open_form(win)
    _three(t); t.changed(None); pump(0.1)
    monkeypatch.setattr(D, "FieldAlignDialog", lambda *a, **k: (_ for _ in ()).throw(AssertionError("창이 뜨면 안 됨")))
    for k in range(3):
        click(v, 0, 100 + 30 * k, 110 + 50 * k, CTRL)
    assert len(v.sel_fields) == 3
    win.on_action("align_left"); pump(0.05)
    assert {round(w.rect.x0) for w in widgets(t)} == {60}
    win.on_action("undo"); pump(0.05)
    assert sorted(round(w.rect.x0) for w in widgets(t)) == [60, 90, 120]   # Ctrl+Z 한 번


def test_band_select_ctrl_a_and_group_delete(win):
    t, v = open_form(win)
    _three(t); t.changed(None); pump(0.1)
    v.select_all_fields(0)
    assert len(v.sel_fields) == 3
    v.clear_selection()
    a, b = vp(v, 0, 40, 90), vp(v, 0, 300, 180)
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(v.viewport(), b); pump(0.02)
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b); pump(0.03)
    assert len(v.sel_fields) == 2   # 끈 네모에 걸친 두 칸
    v.delete_selection(); pump(0.05)
    assert len(widgets(t)) == 1
    t.doc.undo()
    assert len(widgets(t)) == 3


def test_context_menu_copy_paste_and_items(win):
    t, v = open_form(win)
    _three(t); t.changed(None); pump(0.1)
    v.select_fields([(0, widgets(t)[0].xref)])
    m = CM.build(v, 0, v.from_page_rect(0, fitz.Rect(70, 110, 70, 110)).topLeft())
    labels = [a.text() for a in m.actions() if not a.isSeparator()]
    assert labels == ["속성", "자르기", "복사", "붙여넣기", "삭제", "모두 선택", "맞춤", "배포", "여러 필드 복사본 만들기",
                      "탭 순서 설정", "기본값으로 설정"]
    win.on_action("field_copy"); win.on_action("field_paste"); pump(0.05)
    names = [w.field_name for w in widgets(t)]
    assert len(names) == 4 and names[-1] == "텍스트4"   # 텍스트1~3 이 있으니 다음 번호
    # 다른 탭(다른 문서)에도 붙는다
    t2, v2 = open_form(win, 1)
    win.on_action("field_paste"); pump(0.05)
    assert [w.field_name for w in widgets(t2)] == ["텍스트1"]


def test_set_default_only_picked(win):
    t, v = open_form(win)
    xs = _three(t)
    for x in xs:
        A.set_field_value(t.doc, 0, x, "가")
    t.changed(None)
    v.select_fields([(0, xs[0])])
    win.on_action("field_set_default")
    f = t.doc.fitz
    assert f.xref_get_key(xs[0], "DV")[1] == "가" and f.xref_get_key(xs[1], "DV")[0] == "null"


def test_tab_order_mode(win, monkeypatch):
    t, v = open_form(win)
    xs = _three(t); t.changed(None); pump(0.1)
    monkeypatch.setattr(FT, "_ask", lambda *a: True)
    win.on_action("tab_order")
    for k in (2, 0, 1):
        click(v, 0, 100 + 30 * k, 110 + 50 * k)
    assert A.tab_order(t.doc, 0) == [xs[2], xs[0], xs[1]]
    assert not FT.TabOrderMode.get(t).active


def test_recognize_message_when_none(win, monkeypatch):
    seen = []
    monkeypatch.setattr(FT, "_info", lambda w, title, text: seen.append(text))
    t, v = open_form(win)
    win.on_action("recognize")
    assert seen == [FT.NO_FIELDS]
    t2, _ = open_form(win, 1, lambda d: d[0].draw_rect(fitz.Rect(100, 100, 112, 112), color=(0, 0, 0)))
    win.on_action("recognize")
    assert [w.field_type_string for w in widgets(t2)] == ["CheckBox"]


def test_copies_dialog(win):
    t, v = open_form(win)
    x = _three(t)[0]; t.changed(None)
    v.select_fields([(0, x)])
    dlg = FT.CopiesDialog(win, t, FT.picked(v))
    dlg.down.setValue(3); dlg.across.setValue(2)
    dlg.nudge(0, 1)
    dlg.accept()
    assert len(dlg.made) == 5 and len(widgets(t)) == 8
    t.doc.undo()
    assert len(widgets(t)) == 3 and round(widgets(t)[0].rect.y0) == 100   # 옮기기와 복사본이 되돌리기 한 번
