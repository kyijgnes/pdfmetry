"""속성 창 · 환경 설정 창 · 열 때 초기 보기 시험(창을 offscreen 으로 띄움).

python -m pytest app/tests/test_props_prefs.py
"""
from __future__ import annotations

import os
import copy
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_props_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine.document import Document  # noqa: E402
from app.ui import dlg_prefs as DP  # noqa: E402
from app.ui import dlg_properties as DPR  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 5):
    d = fitz.open()
    for i in range(n):
        p = d.new_page()
        p.insert_font(fontname="malgun", fontfile=r"C:\Windows\Fonts\malgun.ttf")
        p.insert_text((72, 100), f"{i + 1}쪽 사과 하나 둘", fontsize=14, fontname="malgun")
    d.subset_fonts()
    d.save(path); d.close()


@pytest.fixture()
def pdf():
    p = os.path.join(tempfile.mkdtemp(), "속성시험.pdf"); make_pdf(p)
    return p


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    saved = copy.deepcopy(w.settings["prefs"]), copy.deepcopy(w.settings.get("hotkeys"))
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    # 창을 닫으면 설정을 파일에 쓴다. 바꾼 환경 설정이 같은 과정의 다른 시험으로 번지지 않게 되돌린다
    w.prefs.clear(); w.prefs.update(saved[0])
    if saved[1] is None:
        w.settings.pop("hotkeys", None)
    else:
        w.settings["hotkeys"] = saved[1]
    w.close(); pump(0.05)


def test_initial_view_roundtrip(pdf):
    d = Document(pdf)
    assert d.initial_view() == {"mode": "none", "layout": "default", "zoom": "default", "page": 0}
    assert d.set_initial_view({"layout": "facing", "zoom": "fit_width", "page": 2, "mode": "outlines"})
    d.save(); d.close()
    d = Document(pdf)
    assert d.initial_view() == {"mode": "outlines", "layout": "facing", "zoom": "fit_width", "page": 2}
    assert not d.set_initial_view(d.initial_view()) and not d.modified   # 같은 값이면 고친 상태가 안 된다
    assert d.set_initial_view({"zoom": 1.5, "layout": "default"})
    assert d.initial_view()["zoom"] == 1.5 and d.initial_view()["layout"] == "default"


def test_open_follows_document_initial_view(win, pdf):
    d = Document(pdf); d.set_initial_view({"layout": "facing", "zoom": "fit_width", "page": 2}); d.save(); d.close()
    win.open_path(pdf); pump(0.4)
    v = win.cur().viewer
    assert v.layout_mode == "facing" and v.zoom_mode == "fit_width"
    assert v.current in (2, 3)   # 두 쪽 보기라 짝의 어느 쪽이든
    assert v.tool == "select"    # 기본 선택 모드


def test_open_fixed_zoom_pref(win, pdf):
    win.prefs["default_zoom"] = "p50"; win.prefs["default_layout"] = "single"
    win.open_path(pdf); pump(0.4)
    v = win.cur().viewer
    assert v.layout_mode == "single" and v.zoom_mode == "fixed" and abs(v.zoom - 0.5) < 1e-6


def test_properties_unchanged_keeps_clean(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur()
    dlg = DPR.PropertiesDialog(win, t.doc, 0, win.settings)
    dlg.show_page(1)
    assert dlg.changed_metadata() == {}
    assert not t.doc.set_initial_view(dlg.initial_view()) and not t.doc.modified
    dlg.edits["title"].setText("새 제목")
    assert dlg.changed_metadata() == {"title": "새 제목"}
    dlg.reject()


def test_properties_view_page_and_fonts(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur()
    dlg = DPR.PropertiesDialog(win, t.doc, 0, win.settings)
    dlg.show_page(1)
    dlg.layout_.setCurrentIndex(dlg.layout_.findData("facing"))
    dlg.zoom.setCurrentIndex(dlg.zoom.findData("fit_width"))
    dlg.page.setValue(3)
    assert dlg.initial_view() == {"mode": "none", "layout": "facing", "zoom": "fit_width", "page": 2}
    dlg.show_page(3)
    tree = dlg.built["fonts"]
    names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert any("(내장 서브셋 글꼴)" in n for n in names), names
    dlg.reject()


def test_permissions_ten_rows(tmp_path):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 1)
    out = tmp_path / "locked.pdf"
    d = fitz.open(str(src))
    d.save(str(out), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
           permissions=fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ACCESSIBILITY)
    d.close()
    rows = dict(DPR.permissions(fitz.open(str(out))))
    assert len(rows) == 10
    assert rows["인쇄"] and rows["내용 복사"] and rows["접근성을 위해 내용 복제"]
    assert not rows["문서 변경"] and not rows["주석"] and not rows["양식 필드 채우기"] and not rows["페이지 내보내기"]
    assert all(dict(DPR.permissions(fitz.open(str(src)))).values())   # 암호 없으면 다 허용


def test_date_and_size_text():
    assert DPR.size_text(1536) == "1.5 KB" and DPR.size_text(1024) == "1 KB" and DPR.size_text(10) == "10 B"
    assert DPR.date_text("D:20261103143000+09'00'") != "" and DPR.date_text("") == ""
    assert DPR.pdf_version("PDF 1.7") == "1.7"


def test_prefs_pages_persist(win):
    s = win.settings
    dlg = DP.PreferencesDialog(win, s)
    for n in range(len(DP.PAGES)):
        if DP.PAGES[n][0] != "ocr":   # OCR 은 윈도우 언어 목록을 불러 느리다
            dlg.show_page(n)
    dlg.recent_n.setValue(7)
    dlg.layout_.setCurrentIndex(dlg.layout_.findData("facing"))
    dlg.zoom.setCurrentIndex(dlg.zoom.findData("p200"))
    dlg.select_mode.setCurrentIndex(dlg.select_mode.findData("hand"))
    dlg.unit_group.button(4).click()   # 피카스
    dlg.grid_sub.setValue(5)
    dlg.ef_size.setEditText("18")
    dlg.blank_name.setText("  새 문서.pdf ")
    dlg.blank_count.setValue(3)
    dlg.pw_edits[0][0].setText("abc")
    dlg.pw_edits[0][0].untouched = False
    dlg.accept()
    p = s["prefs"]
    assert p["recent_n"] == 7 and p["default_layout"] == "facing" and p["default_zoom"] == "p200"
    assert p["select_mode"] == "hand" and p["unit"] == "pc" and p["grid"]["sub"] == 5
    assert abs(p["grid"]["w"] - 72 / 2.54) < 0.05   # 단위를 바꿔도 속값(pt)은 그대로
    assert p["edit_font"]["size"] == 18 and p["blank"]["name"] == "새 문서" and p["blank"]["count"] == 3
    assert len(p["preset_pw"]) == 1 and "abc" not in p["preset_pw"][0] and DP.unprotect(p["preset_pw"][0]) == "abc"
    again = DP.PreferencesDialog(win, s)
    again.show_page(0); again.show_page(4)
    assert again.recent_n.value() == 7 and again.zoom.currentData() == "p200"
    assert again.pw_edits[0][0].untouched and again.pw_edits[0][0].text() != "abc"
    again.accept()
    assert DP.unprotect(s["prefs"]["preset_pw"][0]) == "abc"   # 손대지 않은 칸은 그대로 남는다


def test_prefs_only_visited_pages_saved(win):
    s = win.settings
    s["prefs"].pop("blank", None)
    dlg = DP.PreferencesDialog(win, s)
    dlg.accept()
    assert "blank" not in s["prefs"]


def test_blank_name_rule():
    assert DP.blank_name(" a:b?.PDF ") == "a_b_" and DP.blank_name("   ") == "제목 없음"
