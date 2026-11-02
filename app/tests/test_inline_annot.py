"""글 주석 쪽 위 바로 쓰기 시험: 타자기·텍스트 박스·말풍선 입력 상자, 두 번 눌러 고치기, 노트 팝업, 답글 엔진.

python -m pytest app/tests/test_inline_annot.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_inline_annot_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui.inline_annot import InlineAnnot, NotePopup  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

RED = (1.0, 0.0, 0.0)


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 1):
    d = fitz.open()
    for _ in range(n):
        d.new_page(width=595, height=842)
    d.save(path)


def _reopen(d: Document) -> fitz.Document:
    return fitz.open("pdf", d.to_bytes())


# ── 답글 엔진 ─────────────────────────────────────────────────
def test_reply_is_irt_text_annot_and_survives_save():
    d = Document()
    d.fitz.new_page()
    n = A.note(d, 0, fitz.Point(50, 50), "원 노트")
    r1 = A.add_reply(d, 0, n, "첫 답글")
    r2 = A.add_reply(d, 0, n, "둘째 답글", "철수")
    f = _reopen(d)
    pg = f[0]   # 주석은 쪽 객체를 약하게만 잡는다
    kinds = {a.xref: a for a in pg.annots()}
    for x in (r1, r2):
        assert kinds[x].type[1] == "Text"
        assert f.xref_get_key(x, "IRT") == ("xref", f"{n} 0 R")
        assert f.xref_get_key(x, "RT")[1] == "/R"
        assert not (kinds[x].flags & fitz.PDF_ANNOT_IS_HIDDEN)   # 숨김 비트는 안 씀
    assert fitz.Rect(kinds[r1].rect) == fitz.Rect(kinds[n].rect)
    reps = A.replies(d, 0, n)
    assert [r["content"] for r in reps] == ["첫 답글", "둘째 답글"]
    assert reps[0]["author"] == (os.environ.get("USERNAME") or os.environ.get("USER", ""))   # 빈 작성자는 사용자 이름
    assert reps[1]["author"] == "철수"
    assert A.is_reply(d, r1) and not A.is_reply(d, n)


def test_reply_draws_nothing_on_page():
    d = Document()
    d.fitz.new_page()
    n = A.note(d, 0, fitz.Point(300, 300), "")
    before = d.fitz[0].get_pixmap(clip=fitz.Rect(290, 290, 330, 330)).samples
    A.add_reply(d, 0, n, "답글")
    assert d.fitz[0].get_pixmap(clip=fitz.Rect(290, 290, 330, 330)).samples == before


def test_update_delete_reply_and_delete_parent_drops_replies():
    d = Document()
    d.fitz.new_page()
    n = A.note(d, 0, fitz.Point(50, 50), "노트")
    r1 = A.add_reply(d, 0, n, "답")
    r2 = A.add_reply(d, 0, r1, "답의 답")
    assert A.update_reply(d, 0, r1, "고친 답")
    assert A.replies(d, 0, n)[0]["content"] == "고친 답"
    assert not A.update_reply(d, 0, n, "원 노트는 답글이 아님")
    assert A.delete_reply(d, 0, r1)
    xs = [a.xref for a in d.fitz[0].annots()]
    assert r1 not in xs and r2 not in xs and n in xs
    A.add_reply(d, 0, n, "또 답")
    A.delete(d, 0, n)
    assert list(d.fitz[0].annots()) == []
    assert d.undo() and len(A.replies(d, 0, n)) == 1   # 지우기는 한 걸음


def test_note_icon_is_comment():
    d = Document()
    d.fitz.new_page()
    n = A.note(d, 0, fitz.Point(50, 50), "노트")
    assert d.fitz.xref_get_key(n, "Name")[1] == "/Comment"


def test_edit_free_text_keeps_style_and_callout():
    d = Document()
    d.fitz.new_page()
    tip = fitz.Point(100, 200)
    x = A.free_text(d, 0, fitz.Rect(150, 100, 270, 130), "말", 14, RED, None, (0, 0, 1), 1,
                    callout=[tip, fitz.Point(138, 115), fitz.Point(150, 115)], author="나")
    assert d.fitz.xref_get_key(x, "LE")[1] == "/OpenArrow"
    n = A.edit_free_text(d, 0, x, "고친 말풍선\n둘째 줄", fitz.Rect(150, 100, 290, 150))
    info = A.freetext_info(d, 0, n)
    assert info["kind"] == "callout" and info["text"] == "고친 말풍선\n둘째 줄"
    assert info["text_color"] == RED and info["border"] == (0, 0, 1) and info["author"] == "나"
    assert abs(info["callout"][0][0] - tip.x) < 0.5 and abs(info["callout"][0][1] - tip.y) < 0.5
    assert d.undo() and A.freetext_info(d, 0, x)["text"] == "말"   # 고치기는 한 걸음
    assert A.edit_free_text(d, 0, x, "  ") is None and list(d.fitz[0].annots()) == []   # 비우면 지움


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
    p = os.path.join(tempfile.mkdtemp(), "글주석.pdf"); make_pdf(p)
    return p


def editor(v) -> InlineAnnot | None:
    eds = [o for o in v.findChildren(InlineAnnot) if not o.done]
    return eds[0] if eds else None


def popup(v) -> NotePopup | None:
    ps = [o for o in v.viewport().findChildren(NotePopup) if not o.done]
    return ps[0] if ps else None


def page_pos(v, page: int, x: float, y: float) -> QPoint:
    it = v.items[page]
    return v.mapFromScene(it.pos().x() + x * v.zoom, it.pos().y() + y * v.zoom)


def click_page(v, page: int, x: float, y: float, double: bool = False):
    v.goto(page); pump(0.1)
    vp = page_pos(v, page, x, y)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp); pump(0.05)
    if double:
        QTest.mouseDClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp); pump(0.05)


def type_text(w, text: str):
    for ch in text:
        if ch.isascii():
            QTest.keyClicks(w, ch)
        else:
            e = QInputMethodEvent("", [])
            e.setCommitString(ch)
            QApplication.sendEvent(w, e)
    pump(0.02)


def click_away(v):
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3)); pump(0.05)


@pytest.mark.parametrize("kind", ["typewriter", "textbox", "callout"])
def test_click_type_bake_and_reopen(win, pdf, kind):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool(kind)
    click_page(v, 0, 200, 300)
    ed = editor(v)
    assert ed is not None   # 대화상자 없이 그 자리 입력
    type_text(v, "Hi 글 주석")
    assert ed.text() == "Hi 글 주석"
    click_away(v)
    assert editor(v) is None
    out = os.path.join(tempfile.mkdtemp(), "저장.pdf")
    t.doc.save(out)
    st = t.props.store.get(kind)
    with fitz.open(out) as f:
        pg = f[0]
        anns = [a for a in pg.annots()]
        assert len(anns) == 1 and anns[0].type[1] == "FreeText"
        dd = Document(out)
        info = A.freetext_info(dd, 0, anns[0].xref)
        assert info["text"] == "Hi 글 주석" and info["kind"] == kind
        assert info["text_color"] == tuple(st["text_color"])
        assert info["author"] == (os.environ.get("USERNAME") or os.environ.get("USER", ""))
        if kind == "typewriter":
            assert info["width"] == 0 and info["border"] is None
        else:
            assert info["width"] == st["width"] and info["border"] == tuple(st["border"])
        if kind == "callout":
            tip = info["callout"][0]
            assert abs(tip[0] - 200) < 1 and abs(tip[1] - 300) < 1   # 지시선 끝 = 누른 자리
            assert f.xref_get_key(anns[0].xref, "LE")[1] == "/OpenArrow"
            assert info["box"].x0 > 200 and info["box"].y1 < 300    # 상자는 오른쪽 위
        else:
            r = info["box"]
            assert r.x0 < 200 < r.x1 and r.y0 < 300 < r.y1
        dd.close()
    assert t.doc.undo() and list(t.doc.fitz[0].annots()) == []   # 실행 취소 한 걸음


def test_empty_input_leaves_nothing(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("textbox")
    click_page(v, 0, 200, 300)
    assert editor(v) is not None
    QTest.keyClick(v, Qt.Key.Key_Escape); pump(0.05)
    assert editor(v) is None and list(t.doc.fitz[0].annots()) == [] and not t.doc.can_undo()


def test_double_click_edits_in_place(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    x = A.free_text(t.doc, 0, fitz.Rect(100, 100, 260, 130), "처음", 14, RED, None, RED, 1)
    t.changed([0]); pump(0.1)
    win.on_tool("select")
    click_page(v, 0, 130, 115, double=True)
    ed = editor(v)
    assert ed is not None and ed.text() == "처음" and ed.editing
    type_text(v, " 고침")
    click_away(v)
    pg = t.doc.fitz[0]
    anns = list(pg.annots())
    assert len(anns) == 1 and anns[0].info["content"] == "처음 고침"
    assert not (anns[0].flags & fitz.PDF_ANNOT_IS_HIDDEN)
    assert t.doc.undo() and next(t.doc.fitz[0].annots()).info["content"] == "처음"   # 한 걸음
    assert x


def test_note_popup_content_color_and_reply(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("note")
    click_page(v, 0, 300, 400)
    p = popup(v)
    assert p is not None and p.xref is None
    type_text(p.text, "노트 내용")
    p.swatches[3].click()
    p.reply.setText("첫 답글"); p.reply_btn.click(); pump(0.05)
    assert p.xref is not None and [r["content"] for r in A.replies(t.doc, 0, p.xref)] == ["첫 답글"]
    xref = p.xref
    QTest.keyClick(p.text, Qt.Key.Key_Escape); pump(0.05)
    assert popup(v) is None
    a = A.get(t.doc, 0, xref)
    assert a.info["content"] == "노트 내용"
    assert t.doc.fitz.xref_get_key(xref, "Name")[1] == "/Comment"
    from app.ui.props import SWATCHES
    assert all(abs(x - y) < 0.01 for x, y in zip(a.colors["stroke"], SWATCHES[3]))
    # 노트 도구로 그 노트를 다시 누르면 같은 노트가 열린다(답글 줄 보임)
    click_page(v, 0, 305, 405)
    p = popup(v)
    assert p is not None and p.xref == xref and p.reply_box.count() == 1
    p.finish(); pump(0.05)


def test_empty_note_leaves_nothing_and_caret_without_dialog(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("note")
    click_page(v, 0, 300, 400)
    assert popup(v) is not None
    popup(v).finish(); pump(0.05)
    assert list(t.doc.fitz[0].annots()) == []
    win.on_tool("caret")
    click_page(v, 0, 100, 100)
    pg = t.doc.fitz[0]
    anns = list(pg.annots())
    assert len(anns) == 1 and anns[0].type[1] == "Caret"


def test_textbox_drag_sets_box_size(win, pdf):
    """텍스트 상자 도구로 끌면 끈 크기대로 상자가 생긴다(글이 짧아도). 한 번 누르면 예전처럼 누른 자리에."""
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("textbox")
    v.goto(0); pump(0.1)
    a, b = page_pos(v, 0, 100, 150), page_pos(v, 0, 400, 330)
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for k in range(1, 6):
        QTest.mouseMove(v.viewport(), a + (b - a) * k / 5); pump(0.01)
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b); pump(0.05)
    ed = editor(v)
    assert ed is not None and ed.fixed_width
    type_text(v, "짧은 글")
    click_away(v)
    xs = [x for x in t.doc.fitz[0].annots() if x.type[1] == "FreeText"]
    assert len(xs) == 1
    r = A.freetext_info(t.doc, 0, xs[0].xref)["box"]
    assert abs(r.x0 - 100) < 2 and abs(r.y0 - 150) < 2 and abs(r.x1 - 400) < 2 and abs(r.y1 - 330) < 4
    # 한 번 누르면 정해진 너비(끈 크기 아님)
    win.on_tool("textbox")
    click_page(v, 0, 100, 450)
    ed = editor(v)
    assert ed is not None and not ed.fixed_width and ed.min_height == 0
    type_text(v, "x")
    click_away(v)
    r2 = [A.freetext_info(t.doc, 0, x.xref)["box"] for x in t.doc.fitz[0].annots()
          if x.type[1] == "FreeText" and x.xref != xs[0].xref][0]
    assert r2.x0 < 100 < r2.x1 and r2.height < 60
