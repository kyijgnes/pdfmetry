"""텍스트 추가 시험: 쪽을 한 번 누르고 바로 치는 글 상자(offscreen 창)와 글 넣기 엔진.

python -m pytest app/tests/test_inline_text.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_inline_text_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui.inline_text import InlineText  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, rotations=(0, 90)):
    d = fitz.open()
    for rot in rotations:
        p = d.new_page(width=595, height=842)
        p.set_rotation(rot)
    d.save(path); d.close()


def screen_dir(page: fitz.Page) -> tuple[float, float]:
    """쪽에 든 첫 줄의 글 방향을 화면(회전 뒤) 기준으로."""
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            d = fitz.Point(ln["dir"]) * fitz.Matrix(page.rotation)
            return round(d.x, 3), round(d.y, 3)
    raise AssertionError("글이 없다")


# ── 엔진 ─────────────────────────────────────────────────────
def test_empty_text_leaves_nothing():
    d = Document()
    assert not A.add_text(d, 0, fitz.Rect(50, 50, 150, 70), "  \n ")
    assert not d.can_undo() and d.fitz[0].get_text().strip() == ""


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("italic", [False, True])
def test_add_text_stands_upright_on_rotated_page(rot, italic):
    d = Document()
    d.fitz[0].set_rotation(rot)
    p = d.fitz[0]
    w, h = A.text_box_size("Hello 한글", 10, min_width=100)
    r = fitz.Rect(60, 80, 60 + w, 80 + h) * p.derotation_matrix   # 화면에서 본 자리를 회전 전 좌표로
    r.normalize()
    assert A.add_text(d, 0, r, "Hello 한글", 10, italic=italic)
    assert screen_dir(p) == (1, 0)
    got = fitz.open("pdf", d.to_bytes())[0].get_text().strip()
    assert got == "Hello 한글"


def test_text_box_size_grows_with_longest_line():
    w1, h1 = A.text_box_size("가", 10, min_width=100)
    w2, h2 = A.text_box_size("가나다라마바사아자차카타파하 가나다라마바사", 10, min_width=100)
    w3, h3 = A.text_box_size("가\n나", 10)
    assert w1 == 100 and w2 > 100
    assert abs(h3 - (2 * h1 - A.TEXT_PAD)) < 0.01   # 줄 수만큼 높이


def test_add_text_defaults_and_one_undo():
    d = Document()
    w, h = A.text_box_size("abc", 10)
    assert A.add_text(d, 0, fitz.Rect(50, 50, 50 + w, 50 + h), "abc")
    span = d.fitz[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
    assert span["size"] == 10 and span["color"] == 0
    assert d.undo() and d.fitz[0].get_text().strip() == "" and not d.can_undo()


# ── 화면 ─────────────────────────────────────────────────────
@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


@pytest.fixture()
def pdf():
    p = os.path.join(tempfile.mkdtemp(), "글넣기.pdf"); make_pdf(p)
    return p


def editor(v) -> InlineText | None:
    eds = [o for o in v.findChildren(InlineText) if not o.done]
    return eds[0] if eds else None


def click_page(win, v, page: int, x: float, y: float):
    """쪽 왼쪽 위에서 (x, y) pt 떨어진 화면 자리를 누른다."""
    v.goto(page); pump(0.1)
    it = v.items[page]
    vp = v.mapFromScene(it.pos().x() + x * v.zoom, it.pos().y() + y * v.zoom)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp); pump(0.05)


def type_text(v, text: str):
    """영문은 키로, 한글은 입력기가 조합을 끝내 넘기는 것처럼 보낸다(QTest 는 한글 키를 못 만든다)."""
    for ch in text:
        if ch.isascii():
            QTest.keyClicks(v, ch)
        else:
            e = QInputMethodEvent("", [])
            e.setCommitString(ch)
            QApplication.sendEvent(v, e)
    pump(0.02)


def click_away(v):
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3)); pump(0.05)


def test_click_type_and_bake(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("addtext")
    assert v.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor
    click_page(win, v, 0, 100, 120)
    ed = editor(v)
    assert ed is not None and ed.bar.isVisible()
    assert abs(ed.box.textWidth() * ed._unit() - 100) < 0.5        # 처음 너비 100pt
    assert abs(ed.pos_pt.y() - (120 - 14 * 1.33 / 2)) < 0.2          # 줄 높이 절반 올림
    type_text(v, "Hello 한글 입력")
    assert ed.text() == "Hello 한글 입력"
    click_away(v)
    assert editor(v) is None and v.edit_mode and v.tool == "edit"   # 텍스트 추가는 편집을 켠다
    out = os.path.join(tempfile.mkdtemp(), "저장.pdf")
    t.doc.save(out)
    with fitz.open(out) as d:
        assert d[0].get_text().strip() == "Hello 한글 입력"
        r = d[0].search_for("Hello")[0]
        assert abs(r.x0 - 100) < 3 and abs((r.y0 + r.y1) / 2 - 120) < 4   # 누른 자리에 첫 줄 가운데
    assert t.doc.undo() and t.doc.fitz[0].get_text().strip() == ""   # 실행 취소 한 번에 사라짐


def test_rotated_page_and_style_remembered(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("addtext")
    click_page(win, v, 1, 80, 200)
    ed = editor(v)
    ed.bar.bold.click(); ed.bar.bigger.click(); ed.bar.aligns[1].click(); pump(0.02)
    assert ed.style["bold"] and ed.style["fontsize"] == 16 and ed.style["align"] == 1
    type_text(v, "회전 쪽")
    QTest.keyClick(v, Qt.Key.Key_Escape); pump(0.05)
    assert editor(v) is None
    p = t.doc.fitz[1]
    assert p.get_text().strip() == "회전 쪽" and screen_dir(p) == (1, 0)
    assert win.prefs["addtext_style"]["bold"] and win.prefs["addtext_style"]["fontsize"] == 16
    win.on_tool("addtext")
    click_page(win, v, 0, 100, 300)
    ed = editor(v)
    assert ed.style["bold"] and ed.style["fontsize"] == 16 and ed.bar.bold.isChecked()   # 지난 서식으로 시작
    click_away(v)


def test_empty_click_leaves_nothing(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("addtext")
    click_page(win, v, 0, 100, 120)
    assert editor(v) is not None
    click_away(v)
    assert editor(v) is None and not t.doc.can_undo() and t.doc.fitz[0].get_text().strip() == ""


def test_keys_go_to_box_not_viewer(win, pdf):
    """Space·Enter·Delete 는 글 상자로 간다(손 도구·쪽 넘기기·지우기가 아님)."""
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("addtext")
    click_page(win, v, 0, 100, 120)
    ed = editor(v)
    for k in (Qt.Key.Key_A, Qt.Key.Key_Space, Qt.Key.Key_B, Qt.Key.Key_Return, Qt.Key.Key_C):
        QTest.keyClick(v, k)
    pump(0.02)
    assert ed.text() == "a b\nc" and v.tool == "addtext"
    click_away(v)
    assert t.doc.fitz[0].get_text().strip().splitlines() == ["a b", "c"]
