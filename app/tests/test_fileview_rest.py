"""파일·보기 남은 것 시험: 피카스 단위, 변환 실패 문구, 환경 설정 글꼴을 새 글·글 주석의 처음 값으로.

python -m pytest app/tests/test_fileview_rest.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_fileview_rest_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annot_style as S  # noqa: E402
from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.engine.tools import EngineError  # noqa: E402
from app.ui import dialogs as D  # noqa: E402
from app.ui import dlg_pagebox as PB  # noqa: E402
from app.ui.dlg_deco import MarginGrid  # noqa: E402
from app.ui.inline_annot import InlineAnnot, comment_style  # noqa: E402
from app.ui.inline_text import InlineText, start_style  # noqa: E402
from app.ui.window import MainWindow, save_settings  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


# ── 1. 피카스 ─────────────────────────────────────────────────
def test_pica_unit_box_and_size_text():
    b = PB.UnitBox("pc", 1000)
    assert b.suffix() == " pc" and b.decimals() == 2
    b.set_pt(30); assert abs(b.value() - 2.5) < 1e-9 and abs(b.pt() - 30) < 1e-9
    assert PB.fmt_size(612, 792, "pc") == "51.00 x 66.00 pc"
    assert PB.UnitBox("모름", 10).suffix() == " cm"   # 모르는 단위는 cm 로(꼬리도 cm)


def test_pica_in_pagebox_and_deco_dialogs():
    d = Document()
    dlg = PB.PageSizeDialog(None, d, [0], "pc")
    assert dlg.unit == "pc" and dlg.w.suffix() == " pc"
    assert "pc" in dlg.cur_lbl.text()
    dlg.close()
    g = MarginGrid("pc", {"top": 36, "bottom": 0, "left": 0, "right": 0})
    assert all(sb.suffix() == " pc" for sb in g.findChildren(PB.UnitBox))


# ── 2. 변환 실패 문구 ─────────────────────────────────────────
@pytest.fixture()
def boxes(monkeypatch):
    got = []
    monkeypatch.setattr(QMessageBox, "warning", lambda p, t, m, *a: got.append(("warning", t, m)))
    monkeypatch.setattr(QMessageBox, "critical", lambda p, t, m, *a: got.append(("critical", t, m)))
    return got


def _boom(exc):
    def fn(progress=None):
        raise exc
    return fn


def test_convert_failure_uses_fixed_text(boxes):
    assert D.ProgressDialog(None, "Word로 변환", _boom(RuntimeError("깨진 쪽"))).run() is None
    kind, title, msg = boxes[-1]
    assert kind == "critical" and title == "변환 실패"
    assert msg == "오류! Word(으)로 변환하지 못했습니다.\n깨진 쪽"


def test_guided_failure_keeps_reason_under_fixed_text(boxes):
    D.ProgressDialog(None, "한글로 변환", _boom(EngineError("한글이 없습니다."))).run()
    assert boxes[-1] == ("warning", "변환 실패", "오류! 한글(으)로 변환하지 못했습니다.\n한글이 없습니다.")
    D.ProgressDialog(None, "OCR", _boom(EngineError("인식기가 없습니다."))).run()
    assert boxes[-1][1:] == ("OCR 실패", "오류! OCR을 완료하지 못했습니다.\n인식기가 없습니다.")
    D.ProgressDialog(None, "평면화", _boom(RuntimeError("x"))).run()
    assert boxes[-1][1] == "PDF를 평면화하지 못했습니다"


def test_other_failures_unchanged(boxes):
    dlg = D.ProgressDialog(None, "PDF 압축", _boom(EngineError("더 줄일 수 없습니다.")))
    dlg.run()
    assert boxes[-1] == ("warning", "PDF 압축", "더 줄일 수 없습니다.") and dlg.error == "더 줄일 수 없습니다."
    assert D.ProgressDialog(None, "분할", lambda progress=None: 7).run() == 7 and len(boxes) == 1


# ── 3. 글꼴 처음 값 ───────────────────────────────────────────
def test_start_style_follows_edit_font_then_last():
    s = start_style(None, None)
    assert s["fontsize"] == 14 and s["color"] == (0, 0, 0) and not s["bold"]   # 설정 기본값(14·검정)
    ef = {"font": "굴림", "style": "bold", "size": 20, "color": [0, 0, 1]}
    s = start_style(None, ef)
    assert s["fontsize"] == 20 and s["bold"] and not s["italic"] and s["color"] == (0, 0, 1)
    last = {**s, "fontsize": 9, "align": 2}
    assert start_style(last, ef)["fontsize"] == 9 and start_style(last, ef)["align"] == 2   # 바탕이 같으면 지난 서식
    ef2 = {**ef, "style": "italic"}
    s2 = start_style(last, ef2)
    assert s2["fontsize"] == 20 and s2["italic"] and not s2["bold"] and s2["align"] == 0   # 설정을 바꾸면 새 설정으로


def test_comment_style_base_and_remembered():
    d = S.DEFAULTS["textbox"]
    cf = {"size": 9, "color": [0, 0, 1]}
    s = comment_style("textbox", d, cf)
    assert s["fontsize"] == 9 and s["text_color"] == (0, 0, 1) and s["border"] == d["border"]
    s = comment_style("typewriter", {**S.DEFAULTS["typewriter"], "fontsize": 18.0}, cf)
    assert s["fontsize"] == 18 and s["text_color"] == (0, 0, 1)   # 도구에 기억한 크기는 그대로
    assert comment_style("textbox", d, {"size": 3, "color": [1, 0, 0]})["fontsize"] == 5   # 5 아래는 5
    assert comment_style("textbox", d, None) == {**d, "fontsize": 14.0}


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
    p = os.path.join(tempfile.mkdtemp(), "글꼴.pdf")
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


def test_window_add_text_uses_edit_font(win, pdf):
    win.prefs["edit_font"] = {"font": "맑은 고딕", "style": "italic", "size": 22, "color": [0, 0.5, 0]}
    win.prefs.pop("addtext_style", None)
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("addtext")
    click_page(v, 100, 200)
    ed = next(o for o in v.findChildren(InlineText) if not o.done)
    assert ed.style["fontsize"] == 22 and ed.style["italic"] and ed.style["color"] == (0, 0.5, 0)
    type_and_leave(v, "글꼴")
    span = t.doc.fitz[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
    assert abs(span["size"] - 22) < 0.5


def test_window_textbox_uses_comment_font(win, pdf):
    win.prefs["comment_font"] = {"size": 10, "color": [0, 0, 1]}
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_tool("textbox")
    click_page(v, 150, 300)
    ed = next(o for o in v.findChildren(InlineAnnot) if not o.done)
    assert ed.style["fontsize"] == 10 and tuple(ed.style["text_color"]) == (0, 0, 1)
    type_and_leave(v, "주석")
    a = next(x for x in t.doc.fitz[0].annots() if x.type[1] == "FreeText")
    info = A.freetext_info(t.doc, 0, a.xref)
    assert abs(info["fontsize"] - 10) < 0.1 and tuple(round(c, 2) for c in info["text_color"]) == (0, 0, 1)
