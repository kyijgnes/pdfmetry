"""라운드 15 나: OCR 여러 언어 · OCR 창과 「이름_OCR」 새 탭 · 영역 OCR · 스캔 안내 막대 · 표 인식 · ocr_langs 옮기기.

python -m pytest app/tests/test_ocr_r15.py
윈도우 한국어 OCR 이 없는 PC 에서는 실제 인식 시험을 건너뛴다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_ocr_r15_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import ocr as O  # noqa: E402
from app.engine.tools import korean_font_path  # noqa: E402
from app.ui import context_menu as CM  # noqa: E402
from app.ui import dlg_ocr as DO  # noqa: E402
from app.ui import dlg_prefs as DP  # noqa: E402
from app.ui import scan_bar as SCB  # noqa: E402
from app.ui import table_tool as TT  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

W, H = 595, 842
HAS_KO = any(t.split("-")[0].lower() == "ko" for t in DO.languages())
need_ocr = pytest.mark.skipif(not HAS_KO, reason="이 PC 에 윈도우 한국어 OCR 이 없음")


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _scan_pdf(name: str = "스캔시험.pdf", pages: int = 3) -> str:
    """한·영 섞인 글을 그림으로 뜬 스캔 PDF(글자층 없음)."""
    src = fitz.open()
    for k in range(pages):
        pg = src.new_page(width=W, height=H)
        pg.insert_font(fontname="kr", fontfile=korean_font_path())
        pg.insert_text((60, 120), f"한국어 문장 OCR test {k + 1}", fontname="kr", fontsize=28)
        pg.insert_text((60, 400), "두번째 줄 area check", fontname="kr", fontsize=28)
    out = fitz.open()
    for pg in src:
        p = out.new_page(width=W, height=H)
        p.insert_image(p.rect, pixmap=pg.get_pixmap(dpi=300))
    path = os.path.join(tempfile.mkdtemp(), name)
    out.save(path)
    return path


def _two_tables_pdf() -> str:
    d = fitz.open(); pg = d.new_page(width=W, height=H)
    for y0 in (80, 320):
        for r in range(4):
            pg.draw_line((50, y0 + r * 20), (350, y0 + r * 20))
        for c in range(4):
            pg.draw_line((50 + c * 100, y0), (50 + c * 100, y0 + 60))
        for r in range(3):
            for c in range(3):
                pg.insert_text((55 + c * 100, y0 + 15 + r * 20), f"r{r}c{c}")
    path = os.path.join(tempfile.mkdtemp(), "표둘.pdf")
    d.save(path)
    return path


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


@pytest.fixture()
def boxes(monkeypatch):
    """알림 창을 띄우지 않고 문구를 모은다."""
    said: list[str] = []

    def grab(*a, **k):
        said.append(a[2] if len(a) > 2 else "")
        return QMessageBox.StandardButton.Ok
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(grab))
    return said


# ── 엔진: 여러 언어 ─────────────────────────────────────────────
def test_langs_of_takes_text_and_list():
    assert O.langs_of("ko") == ["ko"]
    assert O.langs_of("en-US, ko") == ["en-US", "ko"]
    assert O.langs_of(["ko", "en-US", "ko"]) == ["ko", "en-US"]
    assert O.langs_of([]) == ["ko"] and O.langs_of(None) == ["ko"]


def _w(text):
    return {"text": text, "x": 0, "y": 0, "w": 10, "h": 10, "line": 0}


def test_multi_language_picks_most_words_per_image(monkeypatch):
    calls = []

    def fake(pngs, lang):
        calls.append(lang)
        if lang == "ko":
            return [[_w("가"), _w("나"), _w("다")], [_w("x")]]
        if lang == "en-US":
            return [[_w("a")], [_w("one"), _w("two")]]
        raise O.NoLanguage("없음")
    monkeypatch.setattr(O, "_ocr_images_one", fake)
    got = O.ocr_images(["a.png", "b.png"], ["ko", "en-US", "ja"])
    assert calls == ["ko", "en-US", "ja"]   # 언어마다 한 번씩(없는 언어는 건너뜀)
    assert [w["text"] for w in got[0]] == ["가", "나", "다"]
    assert [w["text"] for w in got[1]] == ["one", "two"]
    with pytest.raises(O.NoLanguage):
        O.ocr_images(["a.png"], ["ja"])   # 고른 언어가 하나도 없으면 오류


@need_ocr
def test_two_languages_find_korean_and_english():
    doc = fitz.open(_scan_pdf())
    n = O.ocr_pages(doc, pages=[0], lang=["ko", "en-US"])
    text = doc[0].get_text()
    assert n > 0 and "한국어" in text and "test" in text.lower()
    assert doc[0].search_for("test") and doc[0].search_for("한국어")


# ── OCR 창 ───────────────────────────────────────────────────
def test_ocr_dialog_blocks_zero_languages(win, boxes):
    dlg = DO.OcrDialog(win, 5, 0, [], ["ko", "en-US"], ["ko"])
    dlg.langs.set_checked([])
    dlg.accept()
    assert dlg.result() == 0 and boxes == [DO.NO_LANG_TEXT]
    dlg.langs.set_checked(["ko", "en-US"])
    assert not dlg.langs.warn.isVisibleTo(dlg)


def test_ocr_dialog_many_languages_warns():
    dlg = DO.OcrDialog(None, 3, 0, [], ["ko", "en-US", "ja", "fr-FR"], ["ko"])
    dlg.langs.set_checked(["ko", "en-US", "ja"])
    assert not dlg.langs.warn.isVisibleTo(dlg)
    dlg.langs.set_checked(["ko", "en-US", "ja", "fr-FR"])
    assert dlg.langs.warn.isVisibleTo(dlg) and dlg.langs.warn.text() == DO.MANY_TEXT


def test_ocr_dialog_range_choices(win, boxes):
    dlg = DO.OcrDialog(win, 10, 2, [1, 3], ["ko"], ["ko"], "pick")
    assert dlg.r_pick.isChecked() and dlg.r_pick.text() == "선택된 페이지" and dlg.pages() == [1, 3]
    assert (dlg.start.value(), dlg.end.value()) == (2, 4)
    dlg.r_range.setChecked(True); dlg.start.setValue(5); dlg.end.setValue(3)
    dlg.accept()
    assert dlg.result() == 0 and "시작 페이지" in boxes[-1]
    one = DO.OcrDialog(win, 10, 2, [2], ["ko"], ["ko"], "all")
    assert one.r_all.isChecked() and one.r_pick.text() == "현재 페이지"
    none = DO.OcrDialog(win, 10, 2, [], ["ko"], ["ko"], "pick")
    assert not none.r_pick.isEnabled() and none.r_range.isChecked() and none.pages() == [2]


@need_ocr
def test_ocr_opens_new_tab_and_remembers_range(win, boxes, monkeypatch):
    path = _scan_pdf("원본문서.pdf")
    win.open_path(path); pump(0.3)
    t = win.cur()
    t.scan_bar.hide()
    seen = {}

    def fake_exec(self):
        seen["range_all"] = self.r_all.isChecked()
        self.langs.set_checked(["ko"])
        self.r_range.setChecked(True); self.start.setValue(2); self.end.setValue(3)
        return 1
    monkeypatch.setattr(DO.OcrDialog, "exec", fake_exec)
    win.ocr(t); pump(0.3)
    assert len(win.tabs) == 2
    new = win.cur()
    assert new is not t and new.doc.name.startswith("원본문서_OCR") and new.doc.ask_save
    assert new.viewer.current == 1   # 범위 첫 쪽으로
    assert new.doc.fitz[1].get_text().strip() and not new.doc.fitz[0].get_text().strip()
    assert not t.doc.fitz[1].get_text().strip() and not t.doc.modified   # 원본 탭은 그대로
    assert win.prefs["ocr_range"] == "pick" and win.prefs["ocr_langs"] == ["ko"]
    assert seen["range_all"]
    # 다시 열면 범위 선택을 기억한다
    dlg = DO.OcrDialog(win, t.doc.page_count, 0, [], ["ko"], DO.current_langs(win.prefs), win.prefs["ocr_range"])
    assert dlg.r_range.isChecked() and not dlg.r_all.isChecked()


def test_thumbnail_ocr_passes_picked_pages(win, monkeypatch):
    path = _scan_pdf("썸네일.pdf", pages=4)
    win.open_path(path); pump(0.2)
    got = {}
    monkeypatch.setattr(DO.OcrDialog, "exec", lambda self: (got.setdefault("p", self.picked), 0)[1])
    win.pages_action("ocr", [1, 2])
    assert got["p"] == [1, 2]


# ── 영역 OCR ─────────────────────────────────────────────────
def test_area_rects_select_delete_keys(win, boxes):
    win.open_path(_scan_pdf("영역키.pdf")); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("ocrarea"); pump(0.05)
    oa = t.ocr_area
    assert oa.active and oa.panel.isVisible() and not oa.panel.run.isEnabled()
    for pg, r in ((0, (50, 90, 500, 140)), (0, (50, 370, 500, 420)), (1, (50, 90, 500, 140))):
        win.on_request(t, "ocrarea", {"page": pg, "rect": fitz.Rect(r)})
    assert len(oa.rects) == 3 and oa.panel.run.isEnabled() and oa.picked == {2}
    v.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier))
    assert oa.picked == {0, 1, 2}
    oa.picked = {1}
    v.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert len(oa.rects) == 2 and oa.rects[1][0] == 1
    oa.panel.langs.set_checked([])
    oa.recognize()
    assert boxes[-1] == DO.NO_LANG_TEXT and len(oa.rects) == 2   # 언어 0개면 막힌다
    win.on_tool("select"); pump(0.05)
    assert not oa.active and not oa.rects and not oa.panel.isVisible()


@need_ocr
def test_area_ocr_three_rects_two_pages_then_undo(win, boxes):
    win.open_path(_scan_pdf("영역셋.pdf")); pump(0.2)
    t = win.cur(); d = t.doc
    win.on_tool("ocrarea"); pump(0.05)
    for pg, r in ((0, (50, 90, 500, 140)), (0, (50, 370, 500, 420)), (1, (50, 90, 500, 140))):
        win.on_request(t, "ocrarea", {"page": pg, "rect": fitz.Rect(r)})
    t.ocr_area.panel.langs.set_checked(["ko"])
    t.ocr_area.panel.run.click(); pump(0.3)
    assert t.viewer.tool == "select" and not t.ocr_area.rects   # 끝나면 도구를 끈다
    assert d.fitz[0].search_for("test") and d.fitz[0].search_for("area")
    assert d.fitz[1].search_for("test") and not d.fitz[1].search_for("area")   # 그린 자리만
    assert t.viewer.words(0)   # 글자 고르기가 된다
    t.search.edit.setText("test"); t.search.run(); pump(0.3)
    assert len(t.viewer.search_hits) >= 2
    win.on_action("undo"); pump(0.2)
    assert not d.fitz[0].get_text().strip() and not d.fitz[1].get_text().strip()   # Ctrl+Z 한 번에


def test_words_context_menu_has_area_ocr(win, monkeypatch):
    asked = []
    monkeypatch.setattr(DO.LangDialog, "exec", lambda self: (asked.append(self.langs.checked()), 0)[1])
    p = os.path.join(tempfile.mkdtemp(), "글자.pdf")
    dd = fitz.open(); dd.new_page().insert_text((72, 72), "hello world"); dd.save(p)
    win.open_path(p); pump(0.2)
    t = win.cur(); v = t.viewer
    v.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier))
    assert v.sel and v.sel.kind == "words"
    got = []
    v.requestDialog.connect(lambda k, data: got.append((k, data)))
    m = CM.build(v, 0, QPointF(10, 10))
    act = [a for a in m.actions() if a.text() == "영역 OCR"]
    assert act
    act[0].trigger()
    assert got and got[-1][0] == "ocr_words" and got[-1][1]["page"] == 0 and asked   # 언어 창을 띄운다


# ── 스캔 안내 막대 ─────────────────────────────────────────────
def test_scan_bar_shows_and_never_again(win):
    path = _scan_pdf("막대.pdf")
    win.open_path(path); pump(0.2)
    t = win.cur()
    assert t.scan_bar.isVisible() and t.scan_bar.label.text() == SCB.TEXT
    t.scan_bar.never.click(); pump(0.05)
    assert not t.scan_bar.isVisible() and SCB.is_off(win.settings, path)
    win.close_tab(win.tabs.index(t)); pump(0.1)
    win.open_path(path); pump(0.2)
    assert not win.cur().scan_bar.isVisible()


def test_scan_bar_not_for_text_pdf(win):
    p = os.path.join(tempfile.mkdtemp(), "글자문서.pdf")
    dd = fitz.open(); dd.new_page().insert_text((72, 72), "text"); dd.save(p)
    win.open_path(p); pump(0.2)
    assert not win.cur().scan_bar.isVisible()


def test_scan_bar_run_opens_ocr(win, monkeypatch):
    win.open_path(_scan_pdf("막대실행.pdf")); pump(0.2)
    t = win.cur()
    called = []
    monkeypatch.setattr(type(win), "ocr", lambda self, tab, picked=None: called.append(tab))
    t.scan_bar.run.click()
    assert called == [t] and not t.scan_bar.isVisible()


# ── 표 인식 ──────────────────────────────────────────────────
def test_table_recognize_two_tables_copy_html(win, boxes):
    win.open_path(_two_tables_pdf()); pump(0.2)
    t = win.cur()
    win.on_action("table"); pump(0.2)
    tool = t.table_tool
    assert tool.active and len(tool.tables) == 2
    assert boxes[-1] == TT.found_text(2) and "2개의 표" in boxes[-1]
    assert tool.copy_picked()
    md = QApplication.clipboard().mimeData()
    assert md.hasHtml() and "<table" in md.html() and "r1c1" in md.html()
    assert "r0c0\tr0c1\tr0c2" in md.text()
    # offscreen Qt 는 HTML 이 든 클립보드를 남긴 채 끝나면 죽고(종료 139), 그 전에 다음 창의 setStyleSheet 에서 죽기도 한다
    QApplication.clipboard().clear()
    # 두 번째 표를 눌러 고른다
    rr = tool._scene_rect(1)
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(0, 0), QPointF(0, 0), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert tool.press(ev, 0, rr.center()) and tool.picked == 1
    out = os.path.join(tempfile.mkdtemp(), "표.xlsx")
    import app.ui.window as Wm
    win._offer_open = lambda *a, **k: None
    assert tool.save_picked(out) == out and os.path.isfile(out)
    import openpyxl
    assert openpyxl.load_workbook(out).active["B2"].value == "r1c1"
    win.on_action("table"); pump(0.05)   # 다시 누르면 끈다
    assert not tool.active and not tool.tables
    del Wm


def test_table_recognize_no_table(win, boxes):
    p = os.path.join(tempfile.mkdtemp(), "표없음.pdf")
    dd = fitz.open(); dd.new_page().insert_text((72, 72), "no table here"); dd.save(p)
    win.open_path(p); pump(0.2)
    win.on_action("table"); pump(0.2)
    assert boxes[-1] == TT.found_text(0) == "인식 완료. 현재 파일에 표가 없습니다."


def test_table_copy_empty_fails():
    assert not TT.copy_rows([]) and not TT.copy_rows([["", ""]])


def test_tables_from_words_splits_two_blocks():
    def w(t, x, y):
        return {"text": t, "x": x, "y": y, "w": 20, "h": 10, "line": 0}
    words = [w("a", 0, 0), w("b", 100, 0), w("c", 0, 15), w("d", 100, 15),
             w("long", 0, 40), w("sentence", 25, 40), w("words", 50, 40),
             w("e", 0, 200), w("f", 100, 200), w("g", 0, 215), w("h", 100, 215)]
    got = O.tables_from_words(words)
    assert len(got) == 2 and got[0]["rows"] == [["a", "b"], ["c", "d"]]


# ── 설정 옮기기 ───────────────────────────────────────────────
def test_old_ocr_lang_moves_to_ocr_langs():
    s = {"prefs": {"ocr_lang": "en-US"}}
    w = MainWindow(s)
    try:
        assert s["prefs"]["ocr_langs"] == ["en-US"] and "ocr_lang" not in s["prefs"]
    finally:
        w.close()
    p = {"ocr_lang": "ko", "ocr_langs": ["ja"]}
    DO.migrate_langs(p)
    assert p == {"ocr_langs": ["ja"]}


def test_default_langs_english_and_korean():
    assert DO.default_langs(["ja", "ko", "en-US"]) == ["en-US", "ko"]
    assert DO.default_langs(["ko"]) == ["ko"]
    assert DO.default_langs(["ja"]) == ["ja"] and DO.default_langs([]) == ["ko"]
    assert DO.current_langs({}, ["ko", "en-GB"]) == ["en-GB", "ko"]


def test_prefs_ocr_page_checklist_saves_list(win, boxes):
    s = win.settings
    s["prefs"]["ocr_langs"] = ["ko"]
    dlg = DP.PreferencesDialog(win, s)
    dlg.show_page([k for k, _ in DP.PAGES].index("ocr"))
    assert dlg.ocr_langs.checked() == ["ko"]
    dlg.ocr_langs.set_checked([])
    assert not dlg._check_ocr() and boxes[-1] == DO.NO_LANG_TEXT
    dlg.ocr_langs.set_checked(["ko"] + ([] if len(dlg.ocr_langs.boxes) < 2 else [dlg.ocr_langs.boxes[1].property("tag")]))
    want = dlg.ocr_langs.checked()
    dlg.accept()
    assert s["prefs"]["ocr_langs"] == want and "ocr_lang" not in s["prefs"]
