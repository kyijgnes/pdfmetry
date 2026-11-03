"""문서 오른쪽 클릭 메뉴 · Tab 초점 이동 시험.

python -m pytest app/tests/test_context_menu.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_context_menu_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.ui import context_menu as CM  # noqa: E402
from app.ui.viewer import Selection  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_png(path: str):
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 30), False)
    pm.set_rect(pm.irect, (200, 40, 40))
    pm.save(path)


def make_pdf() -> str:
    d = fitz.open()
    p = d.new_page(width=595, height=842)
    p.insert_text((72, 100), "hello world sample text", fontsize=14)
    p.insert_text((72, 300), "second line here", fontsize=14)
    p2 = d.new_page(width=595, height=842)
    p2.insert_text((72, 100), "page two", fontsize=14)
    out = os.path.join(tempfile.mkdtemp(), "메뉴.pdf")
    d.save(out); d.close()
    return out


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


@pytest.fixture()
def tab(win):
    win.open_path(make_pdf()); pump(0.25)
    return win.cur()


def names(m) -> list[str]:
    return [a.text() for a in m.actions() if a.text()]


def trigger(m, text: str):
    a = next(a for a in m.actions() if a.text() == text)
    assert a.isEnabled(), text
    a.trigger(); pump(0.05)


def build(v, i=0):
    return CM.build(v, i, v.from_page_point(i, fitz.Point(300, 600)))


def select_words(v, n=2):
    ws = v.words(0)[:n]
    r = fitz.Rect(ws[0][:4])
    for w in ws:
        r |= fitz.Rect(w[:4])
    v._select_words(0, r, click=False, scene_pt=None)
    assert v.sel and v.sel.kind == "words"


# ── 글자 고름 ─────────────────────────────────────────────
def test_words_menu_items(tab):
    v = tab.viewer
    select_words(v)
    got = names(build(v))
    for want in ("복사", "모두 선택", "선택 취소", "강조", "삽입 기호", "노트를 텍스트에", "링크 만들기", "북마크 추가",
                 "이 글자 검색", "편집", "교정 표시"):
        assert want in got, want


def test_words_deselect_and_edit(win, tab):
    v = tab.viewer
    select_words(v)
    trigger(build(v), "선택 취소")
    assert v.sel is None
    select_words(v)
    trigger(build(v), "편집"); pump()
    assert v.edit_mode


def test_words_make_link(win, tab, monkeypatch):
    from app.ui import dlg_link
    monkeypatch.setattr(dlg_link.LinkDialog, "exec", lambda self: True)
    monkeypatch.setattr(dlg_link.LinkDialog, "values", lambda self: {"style": None, "target": {"uri": "https://example.com"}})
    v = tab.viewer
    select_words(v)
    before = len(tab.doc.fitz[0].get_links())
    trigger(build(v), "링크 만들기"); pump()
    links = tab.doc.fitz[0].get_links()
    assert len(links) == before + 1 and links[-1]["uri"].startswith("https://example.com")
    r = fitz.Rect(links[-1]["from"])
    w0 = fitz.Rect(v.words(0)[0][:4])
    assert r.x0 <= w0.x0 + 0.1 and r.y0 <= w0.y0 + 0.1 and r.x1 >= w0.x1 - 0.1 and r.y1 >= w0.y1 - 0.1   # 고른 글자를 덮는 자리
    assert tab.doc.undo()   # 되돌리기
    assert len(tab.doc.fitz[0].get_links()) == before


def test_words_note_on_text(win, tab, monkeypatch):
    from app.ui import window
    seen = []
    monkeypatch.setattr(window, "NotePopup", lambda v, i, pt, hit, *a, **k: seen.append((i, pt, hit)))
    v = tab.viewer
    select_words(v)
    first = fitz.Rect(v.words(0)[0][:4])
    trigger(build(v), "노트를 텍스트에")
    assert seen and seen[0][0] == 0 and abs(seen[0][1].x - first.x0) < 0.01 and abs(seen[0][1].y - first.y0) < 0.01


def test_words_caret(win, tab):
    v = tab.viewer
    select_words(v)
    trigger(build(v), "삽입 기호"); pump()
    assert any(a.type[1] == "Caret" for a in tab.doc.fitz[0].annots())


# ── 주석 ──────────────────────────────────────────────────
def select_annot(tab, xref):
    v = tab.viewer
    a = A.get(tab.doc, 0, xref)
    v.sel = Selection("annot", 0, xref, fitz.Rect(a.rect), text=a.info.get("content", ""),
                      extra={"kind": a.type[1], "kind_ko": "주석", "colors": {}, "opacity": 1, "width": 1})
    v.selectionChanged.emit(v.sel)
    return v.sel


def test_annot_reply_and_default(win, tab, monkeypatch):
    v = tab.viewer
    xref = A.text_markup(tab.doc, 0, [fitz.Rect(v.words(0)[0][:4])], "Highlight", content="본문 글")
    tab.changed([0])
    select_annot(tab, xref)
    m = build(v)
    for want in ("답글", "글 복사", "기본값으로", "속성", "삭제"):
        assert want in names(m), want
    monkeypatch.setattr(QInputDialog, "getMultiLineText", staticmethod(lambda *a, **k: ("받았습니다", True)))
    trigger(m, "답글"); pump()
    assert [r["content"] for r in A.replies(tab.doc, 0, xref)] == ["받았습니다"]
    trigger(build(v), "글 복사")
    assert QApplication.clipboard().text() == "본문 글"
    # 기본값으로: 속성 패널이 그 종류 기본값에 기억
    called = []
    monkeypatch.setattr(tab.props, "_use_as_default", lambda: called.append(1))
    assert tab.props.sel is not None
    trigger(build(v), "기본값으로")
    assert called == [1]


def test_reply_annot_has_no_reply_menu(tab):
    v = tab.viewer
    xref = A.text_markup(tab.doc, 0, [fitz.Rect(v.words(0)[0][:4])], "Highlight", content="a")
    rx = A.add_reply(tab.doc, 0, xref, "답")
    select_annot(tab, rx)
    got = names(build(v))
    assert "답글" not in got


# ── 링크 ──────────────────────────────────────────────────
def test_link_menu_open_and_copy(win, tab, monkeypatch):
    from app.ui import window
    v = tab.viewer
    A.add_link(tab.doc, 0, fitz.Rect(72, 90, 200, 110), uri="https://example.com/x")
    tab.changed([0])
    x = tab.doc.fitz[0].get_links()[0]["xref"]
    info = A.link_info(tab.doc, 0, x)
    v.sel = Selection("link", 0, x, fitz.Rect(info["rect"]), text="", extra={"kind": "Link", "rect": fitz.Rect(info["rect"])})
    m = build(v)
    for want in ("링크 열기", "주소 복사", "링크 고치기", "삭제"):
        assert want in names(m), want
    opened = []
    monkeypatch.setattr(window.QMessageBox, "question", staticmethod(lambda *a, **k: window.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(window.webbrowser, "open", lambda u: opened.append(u))
    trigger(m, "링크 열기")
    assert opened == ["https://example.com/x"]
    trigger(build(v), "주소 복사")
    assert QApplication.clipboard().text() == "https://example.com/x"


def test_link_goto_open(win, tab):
    v = tab.viewer
    A.add_link(tab.doc, 0, fitz.Rect(72, 90, 200, 110), to_page=1, to_point=fitz.Point(0, 0))
    tab.changed([0])
    x = tab.doc.fitz[0].get_links()[0]["xref"]
    info = A.link_info(tab.doc, 0, x)
    v.sel = Selection("link", 0, x, fitz.Rect(info["rect"]), text="", extra={"kind": "Link", "rect": fitz.Rect(info["rect"])})
    m = build(v)
    assert not next(a for a in m.actions() if a.text() == "주소 복사").isEnabled()
    trigger(m, "링크 열기"); pump(0.2)
    assert v.view_pos()[0] == 1


# ── 이미지 ────────────────────────────────────────────────
def test_image_rotate_flip_undo(win, tab):
    v = tab.viewer
    png = os.path.join(tempfile.mkdtemp(), "a.png"); make_png(png)
    A.add_image(tab.doc, 0, fitz.Rect(100, 400, 220, 460), png)
    tab.changed([0]); pump(0.1)
    info = tab.doc.fitz[0].get_image_info(xrefs=True)[0]
    rect = fitz.Rect(info["bbox"])
    v.sel = Selection("image", 0, -1, rect, extra={"xref": info["xref"]})
    got = names(build(v))
    for want in ("왼쪽 회전", "오른쪽 회전", "좌우 뒤집기", "상하 뒤집기", "이미지 바꾸기", "이미지 저장", "삭제"):
        assert want in got, want
    before = tab.doc.fitz[0].get_image_info()[0]["transform"]
    trigger(build(v), "오른쪽 회전"); pump(0.1)
    after = tab.doc.fitz[0].get_image_info()[0]
    assert tuple(after["transform"]) != tuple(before)
    wb, hb = rect.width, rect.height
    r2 = fitz.Rect(after["bbox"])
    assert abs(r2.width - hb) < 1 and abs(r2.height - wb) < 1   # 90° 돌아 가로세로가 바뀜
    trigger(build(v), "좌우 뒤집기"); pump(0.1)
    flipped = tab.doc.fitz[0].get_image_info()[0]["transform"]
    assert tuple(flipped) != tuple(after["transform"])
    assert tab.doc.undo() and tab.doc.undo()
    assert tuple(tab.doc.fitz[0].get_image_info()[0]["transform"]) == tuple(before)


# ── 빈 곳 · 편집 모드 ─────────────────────────────────────
def test_blank_menu_has_select_all_and_edit(tab):
    got = names(build(tab.viewer))
    for want in ("모두 선택", "편집", "확대", "축소", "이 쪽에 메모", "이 쪽 삭제", "인쇄", "문서 속성"):
        assert want in got, want


def test_edit_mode_blank_is_edit_menu(win, tab, monkeypatch):
    from app.ui import dlg_organize as organize
    asked = []
    monkeypatch.setattr(organize.PlaceDialog, "exec", lambda self: asked.append(self.windowTitle()) and False)
    v = tab.viewer
    win._set_edit(tab, True); pump(0.1)
    m = build(v)
    got = names(m)
    assert got == ["모두 선택", "텍스트 추가", "이미지 추가", "쪽 삽입", "편집 끝내기"]
    trigger(m, "텍스트 추가"); pump(0.1)
    assert v.tool == "addtext"
    win._set_edit(tab, True)
    trigger(build(v), "쪽 삽입"); pump(0.2)
    assert asked == ["빈 페이지 삽입"]   # 빈 쪽 삽입 창을 부탁함
    trigger(build(v), "편집 끝내기"); pump(0.1)
    assert not v.edit_mode


# ── Tab · Shift+Tab ───────────────────────────────────────
def key(v, k, mod=Qt.KeyboardModifier.NoModifier):
    v.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mod)); pump(0.05)


def test_tab_cycles_annots_links_in_page_order(tab):
    v = tab.viewer
    d = tab.doc
    a1 = A.text_markup(d, 0, [fitz.Rect(v.words(0)[0][:4])], "Highlight", content="a")   # 위쪽
    A.add_link(d, 0, fitz.Rect(72, 200, 200, 220), uri="https://example.com")             # 가운데
    a2 = A.text_markup(d, 0, [fitz.Rect(v.words(0)[-1][:4])], "Underline", content="b")  # 아래쪽(둘째 줄)
    tab.changed([0]); pump(0.1)
    link = d.fitz[0].get_links()[0]["xref"]
    seq = []
    for _ in range(3):
        key(v, Qt.Key.Key_Tab)
        seq.append((v.sel.kind, v.sel.xref))
    assert seq == [("annot", a1), ("link", link), ("annot", a2)]
    key(v, Qt.Key.Key_Backtab)
    assert (v.sel.kind, v.sel.xref) == ("link", link)
    key(v, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)   # Shift 만 붙은 Tab 도 거꾸로
    assert (v.sel.kind, v.sel.xref) == ("annot", a1)


def test_tab_goes_to_next_page_and_widgets(win):
    d = fitz.open()
    d.new_page(); d.new_page(); p, p2 = d[0], d[1]
    w = fitz.Widget(); w.field_name = "f1"; w.field_type = fitz.PDF_WIDGET_TYPE_TEXT; w.rect = fitz.Rect(72, 100, 200, 120)
    p2.add_widget(w)
    p.add_text_annot((100, 100), "노트")
    out = os.path.join(tempfile.mkdtemp(), "탭.pdf"); d.save(out); d.close()
    win.open_path(out); pump(0.25)
    t = win.cur(); v = t.viewer
    key(v, Qt.Key.Key_Tab)
    assert v.sel.kind == "annot" and v.sel.page == 0
    key(v, Qt.Key.Key_Tab)
    assert v.sel.kind == "widget" and v.sel.page == 1 and v.sel.text == "f1"
    # 더 없으면 그대로
    assert CM.focus_step(v, True) is False
    assert v.sel.kind == "widget"
    key(v, Qt.Key.Key_Backtab)
    assert v.sel.kind == "annot" and v.sel.page == 0


def test_tab_scrolls_target_into_view_when_rotated(win):
    d = fitz.open()
    for _ in range(3):
        d.new_page()
    d[2].add_text_annot((300, 700), "먼 노트")
    out = os.path.join(tempfile.mkdtemp(), "탭회전.pdf"); d.save(out); d.close()
    win.open_path(out); pump(0.25)
    v = win.cur().viewer
    v.set_view_rotation(90); pump(0.2)
    key(v, Qt.Key.Key_Tab)
    assert v.sel and v.sel.page == 2
    r = v.from_page_rect(2, v.sel.rect)
    vis = v.mapToScene(v.viewport().rect()).boundingRect()
    assert vis.contains(r.center())


def test_tab_without_targets_leaves_selection(tab):
    v = tab.viewer
    assert CM.focus_step(v, True) is False and v.sel is None


def test_real_tab_key_reaches_viewer(win, tab):
    """키 이벤트로 눌러도(Qt 의 초점 이동보다 먼저) 문서 안 대상이 골라진다."""
    from PySide6.QtTest import QTest
    v = tab.viewer
    xref = A.text_markup(tab.doc, 0, [fitz.Rect(v.words(0)[0][:4])], "Highlight", content="a")
    tab.changed([0]); pump(0.1)
    v.setFocus(); pump(0.05)
    QTest.keyClick(v, Qt.Key.Key_Tab); pump(0.05)
    assert v.sel and (v.sel.kind, v.sel.xref) == ("annot", xref)
    QTest.keyClick(v, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier); pump(0.05)
    assert v.sel.xref == xref   # 더 없으면 고른 채 (Qt 가 초점을 옮겨 갈 수 있다)
