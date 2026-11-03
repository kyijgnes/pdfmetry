"""왼쪽 패널 내용 시험: 썸네일 쪽 복사·붙여넣기·끌어 놓기 · 북마크 모양·새 북마크·목차 쪽 · 첨부 열기 · 레이어 고치기.

python -m pytest app/tests/test_left_panels.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_panels_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import outline as OL  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui import sidebar as SB  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 4, word: str = "쪽"):
    d = fitz.open()
    for i in range(n):
        p = d.new_page()
        p.insert_font(fontname="malgun", fontfile=r"C:\Windows\Fonts\malgun.ttf")
        p.insert_text((72, 100), f"{word} {i + 1}", fontsize=14, fontname="malgun")
    d.save(path); d.close()
    return path


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


@pytest.fixture()
def two(tmp_path):
    return make_pdf(str(tmp_path / "가.pdf"), 4, "가"), make_pdf(str(tmp_path / "나.pdf"), 3, "나")


def _tab_of(win, path):
    return next(t for t in win.tabs if os.path.normcase(t.doc.path or "") == os.path.normcase(path))


def _page_words(doc: Document) -> list[str]:
    return [doc.fitz[i].get_text().split()[0] if doc.fitz[i].get_text().split() else "" for i in range(doc.page_count)]


class _FakeDrop:
    """dropEvent 에 넘길 가짜 놓기(끌어 온 곳·자료·자리)."""
    def __init__(self, src, md, pos):
        self._src, self._md, self._pos = src, md, pos

    def source(self): return self._src
    def mimeData(self): return self._md
    def position(self): return QPointF(self._pos)
    def setDropAction(self, _a): pass
    def accept(self): pass


# ── 썸네일 ──────────────────────────────────────────────────────────
def test_thumb_copy_paste_to_other_tab(win, two):
    a, b = two
    win.open_path(a); win.open_path(b); pump(0.2)
    ta, tb = _tab_of(win, a), _tab_of(win, b)
    _select_tab(win, ta)
    ta.show_side("thumbs"); pump(0.1)
    g = ta.thumbs.list
    g.clearSelection(); g.item(1).setSelected(True); g.item(2).setSelected(True)
    g.setFocus()
    QTest.keyClick(g, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier); pump(0.05)
    assert SB.page_clip["n"] == 2
    _select_tab(win, tb)
    tb.show_side("thumbs"); pump(0.1)
    gb = tb.thumbs.list
    gb.clearSelection(); gb.item(0).setSelected(True); gb.setFocus()
    QTest.keyClick(gb, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier); pump(0.1)
    assert tb.doc.page_count == 5
    assert _page_words(tb.doc) == ["나", "가", "가", "나", "나"]
    assert [tb.doc.fitz[i].get_text().split()[1] for i in range(5)] == ["1", "2", "3", "2", "3"]
    assert ta.doc.page_count == 4   # 원본은 그대로
    assert tb.doc.undo() and tb.doc.page_count == 3   # 되돌리기 한 걸음


def _select_tab(win, t):
    i = win.tabs.index(t)
    win.doctabs.setCurrentIndex(i)
    pump(0.05)
    assert win.cur() is t


def test_thumb_drag_to_other_doc_copies(win, two):
    a, b = two
    win.open_path(a); win.open_path(b); pump(0.2)
    ta, tb = _tab_of(win, a), _tab_of(win, b)
    ga, gb = ta.thumbs.list, tb.thumbs.list
    ga.clearSelection(); ga.item(3).setSelected(True)
    tb.show_side("thumbs"); pump(0.1)
    r = gb.visualItemRect(gb.item(0))
    ev = _FakeDrop(ga, QMimeData(), QPoint(r.left() + 2, r.center().y()))   # 첫 칸 왼쪽 → 맨 앞
    gb.dropEvent(ev); pump(0.1)
    assert tb.doc.page_count == 4 and _page_words(tb.doc)[0] == "가"
    assert ta.doc.page_count == 4


def test_thumb_drop_explorer_pdf_inserts_there(win, two, tmp_path):
    a, _ = two
    extra = make_pdf(str(tmp_path / "다.pdf"), 2, "다")
    win.open_path(a); pump(0.2)
    t = win.cur(); t.show_side("thumbs"); pump(0.1)
    g = t.thumbs.list
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(extra)])
    r = g.visualItemRect(g.item(1))
    g.dropEvent(_FakeDrop(None, md, QPoint(r.right() - 2, r.center().y()))); pump(0.1)   # 둘째 칸 오른쪽 → 그 뒤
    assert _page_words(t.doc) == ["가", "가", "다", "다", "가", "가"]


def test_thumb_ctrl_wheel_and_menu(win, two):
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); t.show_side("thumbs"); pump(0.1)
    vp = t.thumbs.list.viewport()
    w0 = t.thumbs.thumb_w
    ev = QWheelEvent(QPointF(5, 5), QPointF(vp.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(vp, ev)
    assert t.thumbs.thumb_w > w0
    t.thumbs.list.item(0).setSelected(True)
    names = [a.objectName() for a in t.thumbs.build_menu().actions() if a.objectName()]
    assert names[:3] == ["copy", "paste", "delete"]
    for k in ("replace", "extract", "ocr", "resize", "pagebox", "pagelabel", "print", "export_images", "zoom_in", "zoom_out"):
        assert k in names


# ── 북마크 ──────────────────────────────────────────────────────────
def test_new_bookmark_without_dialog_opens_editor(win, two, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: pytest.fail("창을 띄우면 안 된다")))
    win.open_path(two[0]); pump(0.2)
    t = win.cur()
    win.on_action("bookmark_add"); pump(0.05)
    tr = t.bookmarks.tree
    assert tr.topLevelItem(0).text(0) == "새 북마크" and tr.state() == tr.State.EditingState
    assert t.doc.bookmarks()[0].title == "새 북마크"


def test_bookmark_bold_red_survives_save(win, two, tmp_path):
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); bm = t.bookmarks
    bm.add(page=2); pump(0.05)
    it = bm.tree.topLevelItem(0)
    bm.set_style([it], {"bold": True, "italic": False, "color": "#FF0000"})
    assert it.font(0).bold()
    out = str(tmp_path / "저장.pdf")
    t.doc.fitz.save(out)
    d = Document(out)
    b = d.bookmarks()[0]
    assert b.page == 2 and OL.style_of(b.dest) == {"bold": True, "italic": False, "color": "#FF0000"}
    d.close()


def test_bookmark_default_style_and_font_size(win, two):
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); bm = t.bookmarks
    win.prefs["bm_style"] = {"bold": False, "italic": True, "color": "#0000FF"}
    bm.add(); pump(0.05)
    assert OL.style_of(t.doc.bookmarks()[0].dest)["italic"]
    bm.set_font(bm._step(1))
    assert win.prefs["bm_font_pt"] == 10.5 and abs(bm.tree.font().pointSizeF() - 10.5) < 0.01


def test_bookmark_repoint_asks_and_delete_key(win, two, monkeypatch):
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); bm = t.bookmarks
    bm.add(page=0); bm.tree.closePersistentEditor(bm.tree.topLevelItem(0)); pump(0.05)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1:3]) or QMessageBox.StandardButton.Yes))
    t.viewer.goto(3); pump(0.05)
    bm._repoint(bm.tree.topLevelItem(0))
    assert asked[0] == ("북마크 대상", "북마크 대상을 현재 페이지로 설정하시겠습니까?")
    assert t.doc.bookmarks()[0].page == t.viewer.current
    bm.tree.setFocus(); bm.tree.topLevelItem(0).setSelected(True)
    QTest.keyClick(bm.tree, Qt.Key.Key_Delete); pump(0.05)
    assert t.doc.bookmarks() == [] and bm.empty.isVisibleTo(bm)


def test_empty_bookmarks_auto_button(win, two):
    win.open_path(two[0]); pump(0.2)
    t = win.cur()
    called = []
    win.auto_bookmark = lambda tab: called.append(tab)
    assert t.bookmarks.empty.isVisibleTo(t.bookmarks)
    t.bookmarks.auto_btn.click()
    assert called == [t]


def test_toc_page_links_to_bookmark(win, two, monkeypatch):
    from app.ui import dlg_bookmark as DB
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); bm = t.bookmarks
    t.doc.set_flat_toc([[1, "첫 장", 1], [1, "셋째 장", 3]]); bm.reload()
    monkeypatch.setattr(DB.TocDialog, "exec", lambda self: True)
    bm.make_toc(); pump(0.1)
    assert t.doc.page_count == 5
    links = t.doc.fitz[0].get_links()
    assert [l["page"] for l in links] == [1, 3]
    assert "셋째 장" in t.doc.fitz[0].get_text()
    assert t.thumbs.list.count() == 5
    assert [b.page for b in t.doc.bookmarks()] == [1, 3]   # 북마크도 밀린 쪽을 그대로 가리킴


# ── 첨부 ───────────────────────────────────────────────────────────
def _attach(path, tmp_path, name, data=b"%PDF"):
    f = tmp_path / name
    f.write_bytes(data)
    return str(f)


def test_attachment_pdf_opens_new_tab_exe_blocked(win, two, tmp_path, monkeypatch):
    a, b = two
    win.open_path(a); pump(0.2)
    t = win.cur(); att = t.attachments
    t.doc.add_attachment(b)
    t.doc.add_attachment(_attach(a, tmp_path, "도구.exe", b"MZ"))
    att.reload()
    names = [att.tree.topLevelItem(k).data(0, att.FILE) for k in range(att.tree.topLevelItemCount())]
    assert names == ["나.pdf", "도구.exe"]
    n = len(win.tabs)
    att.tree.setCurrentItem(att.tree.topLevelItem(0)); att.open_current(); pump(0.2)
    assert len(win.tabs) == n + 1 and win.cur().doc.page_count == 3
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *x, **k: warned.append(x[2])))
    started = []
    monkeypatch.setattr(os, "startfile", lambda p: started.append(p), raising=False)
    _select_tab(win, t)
    att.tree.setCurrentItem(att.tree.topLevelItem(1)); att.open_current()
    assert warned and "저장" in warned[0] and not started


def test_attachment_allow_always_remembered(win, two, tmp_path, monkeypatch):
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); att = t.attachments
    t.doc.add_attachment(_attach(two[0], tmp_path, "메모.txt", b"hi")); att.reload()
    started = []
    monkeypatch.setattr(os, "startfile", lambda p: started.append(p), raising=False)

    def pick(box):
        box.done(0)
        hit = next(b for b in box.buttons() if b.text() == "항상 허용")
        monkeypatch.setattr(type(box), "clickedButton", lambda self, h=hit: h)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pick(self))
    att.tree.setCurrentItem(att.tree.topLevelItem(0)); att.open_current()
    assert win.prefs["attach_allow"] == {".txt": "always"} and len(started) == 1
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("다시 묻지 않는다"))
    att.open_current()
    assert len(started) == 2


def test_attachment_desc_same_name_and_page_attachment(win, two, tmp_path, monkeypatch):
    win.open_path(two[0]); pump(0.2)
    t = win.cur(); att = t.attachments
    p = _attach(two[0], tmp_path, "자료.txt", b"one")
    t.doc.add_attachment(p)
    with t.doc.lock:
        t.doc.fitz[1].add_file_annot((50, 50), b"page data", "쪽첨부.txt")
    att.reload()
    assert att.tree.topLevelItem(1).text(0) == "2 페이지"
    assert att.data_of(att.tree.topLevelItem(1).child(0)) == b"page data"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("새 설명", True)))
    att.tree.setCurrentItem(att.tree.topLevelItem(0)); att.edit_desc()
    assert t.doc.attachments()[0]["desc"] == "새 설명"
    (tmp_path / "자료.txt").write_bytes(b"two")
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([p], "")))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    att.add()
    assert len(t.doc.attachments()) == 1 and t.doc.get_attachment(t.doc.attachments()[0]["name"]) == b"two"


# ── 레이어 ──────────────────────────────────────────────────────────
def _layered(path):
    s = fitz.open()
    p = s.new_page()
    for i, (n, on) in enumerate((("위층", True), ("아래층", False))):
        p.insert_font(fontname="malgun", fontfile=r"C:\Windows\Fonts\malgun.ttf")
        p.insert_text((50, 50 + 30 * i), n, oc=s.add_ocg(n, on=on), fontname="malgun")
    s.new_page()
    s.save(path); s.close()
    return path


def test_layer_rename_delete_lock_survive_save(win, tmp_path, monkeypatch):
    src = _layered(str(tmp_path / "레이어.pdf"))
    win.open_path(src); pump(0.2)
    t = win.cur(); lp = t.layers
    assert lp.tree.topLevelItemCount() == 2
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("바꾼 층", True)))
    lp.rename(lp.tree.topLevelItem(0)); pump(0.05)
    assert lp.tree.topLevelItem(0).text(0) == "바꾼 층"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    lp.delete(lp.tree.topLevelItem(1)); pump(0.05)
    assert lp.tree.topLevelItemCount() == 1
    x = lp.tree.topLevelItem(0).data(0, lp.XREF)
    OL.set_layer_locked(t.doc, x, True); lp.reload()
    it = lp.tree.topLevelItem(0)
    assert it.text(0) == "바꾼 층 (잠김)" and not it.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert t.doc.modified
    out = str(tmp_path / "저장.pdf"); t.doc.fitz.save(out)
    d = Document(out)
    rows = OL.layer_rows(d)
    assert [(r["name"], r["locked"]) for r in rows] == [("바꾼 층", True)]
    d.close()


def test_layer_props_reset_and_visible_scope(win, tmp_path):
    src = _layered(str(tmp_path / "레이어.pdf"))
    win.open_path(src); pump(0.2)
    t = win.cur(); lp = t.layers
    x = lp.tree.topLevelItem(1).data(0, lp.XREF)
    OL.set_layer_props(t.doc, x, {"name": "아래층", "intent": "view", "on": True, "locked": False,
                                  "view": "when_on", "print": "never", "export": "always"})
    p = OL.layer_props(t.doc, x)
    assert p["on"] and p["print"] == "never" and p["export"] == "always"
    lp.reload()
    lp.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked); pump(0.05)
    assert not OL.layer_rows(t.doc)[0]["on"]
    lp.reset(); pump(0.05)
    assert OL.layer_rows(t.doc)[0]["on"]
    t.viewer.goto(1); pump(0.05)
    lp.set_scope("visible")
    assert lp.tree.topLevelItemCount() == 0   # 둘째 쪽은 레이어를 안 씀
    lp.set_scope("all")
    assert lp.tree.topLevelItemCount() == 2


def test_dialogs_values(win, two):
    from app.ui.dlg_bookmark import BookmarkPropsDialog, TocDialog
    from app.ui.dlg_layer import LayerPropsDialog
    got = []
    d = BookmarkPropsDialog(win, {"bold": True, "italic": True, "color": "#FF0000"}, got.append)
    assert d.values() == {"bold": True, "italic": True, "color": "#FF0000"}
    d.shape.setCurrentIndex(0); d.default_btn.click()
    assert got == [{"bold": False, "italic": False, "color": "#FF0000"}]
    v = TocDialog(win).values()
    assert v["title"] == "목차" and v["size"] == 12.0 and v["name"]
    p = {"name": "층", "intent": "design", "on": False, "locked": True, "view": "never", "print": "always", "export": "when_on"}
    assert LayerPropsDialog(win, p).values() == p


def test_thumbs_follow_view_rotation(win, two):
    """보기 회전을 바꾸면 썸네일도 돌아 다시 그려진다(파일 /Rotate 는 그대로)."""
    a, _ = two
    win.open_path(a); pump(0.2)
    t = _tab_of(win, a)
    t.show_side("thumbs"); pump(0.3)
    g = t.thumbs.list
    h0 = g.item(0).sizeHint().height()
    win.on_action("rotate_view_cw"); pump(0.5)
    assert t.thumbs.thumbs.rot == 90
    it = g.item(0)
    pm = it.data(SB.ThumbDelegate.PIX)
    assert pm.width() > pm.height()                 # 세로 쪽이 가로로 누웠다
    assert it.sizeHint().height() < h0
    assert t.doc.fitz[0].rotation == 0
    win.on_action("rotate_view_ccw"); pump(0.5)
    assert t.thumbs.thumbs.rot == 0 and g.item(0).data(SB.ThumbDelegate.PIX).height() > g.item(0).data(SB.ThumbDelegate.PIX).width()
