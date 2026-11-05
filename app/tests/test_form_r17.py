"""라운드 17 가: 양식 칸 한글 글꼴(맑은 고딕 임베드) · 단추 아이콘 · 잠긴 칸 옮기기·지우기 막기 시험.

python -m pytest app/tests/test_form_r17.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pdfm_form_r17_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine import form_props as P  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui import form_tools as FT  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def blank(tmp_path, name="양식.pdf") -> str:
    f = fitz.open(); f.new_page(width=595, height=842)
    out = str(tmp_path / name); f.save(out); f.close()
    return out


def icon_png(tmp_path) -> str:
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pm.set_rect(pm.irect, (255, 0, 0))
    out = str(tmp_path / "아이콘.png"); pm.save(out)
    return out


def ap_of(f: fitz.Document, x: int, state="N") -> tuple[int, bytes]:
    ap = int(f.xref_get_key(x, f"AP/{state}")[1].split()[0])
    return ap, f.xref_stream(ap) or b""


def fonts_of(f: fitz.Document) -> list[str]:
    out = []
    for i in range(1, f.xref_length()):
        b = f.xref_get_key(i, "BaseFont")
        if b[0] == "name":
            out.append(b[1])
    return out


# ── 한글 글꼴 ───────────────────────────────────────────────────
def test_korean_values_survive_save_and_reopen(tmp_path):
    d = Document(blank(tmp_path))
    x = A.add_field(d, 0, fitz.Rect(50, 50, 250, 80), "text")
    A.set_field_value(d, 0, x, "홍길동 abc")
    y = A.add_field(d, 0, fitz.Rect(50, 100, 250, 130), "combo", options=["가나", "다라"])
    A.set_field_value(d, 0, y, "다라")
    A.add_field(d, 0, fitz.Rect(50, 150, 250, 250), "list", options=["사과", "배"])
    A.add_field(d, 0, fitz.Rect(50, 300, 200, 330), "button")   # 기본 이름 「버튼」
    out = str(tmp_path / "저장.pdf"); d.save(out)
    g = fitz.open(out)
    text = g[0].get_text()
    for s in ("홍길동 abc", "다라", "사과", "배", "버튼"):
        assert s in text
    for w in g[0].widgets():
        assert "/Malgun " in g.xref_get_key(w.xref, "DA")[1]
        ap, s = ap_of(g, w.xref)
        assert b"/Malgun" in s and b"Batang" not in g.xref_object(ap).encode()
    names = fonts_of(g)
    assert any("Malgun" in n for n in names) and not any("Batang" in n for n in names)
    assert os.path.getsize(out) < 400_000   # 맑은 고딕은 쓴 글자만(통째면 7MB 넘음)
    # 화면에도 글자가 찍힌다(흰 바탕이 아님)
    pix = g[0].get_pixmap(clip=fitz.Rect(52, 52, 120, 78))
    assert any(pix.pixel(xx, yy)[0] < 128 for xx in range(pix.width) for yy in range(pix.height))
    g.close()


def test_reopened_field_takes_new_korean_letters(tmp_path):
    d = Document(blank(tmp_path))
    x = A.add_field(d, 0, fitz.Rect(50, 50, 250, 80), "text")
    A.set_field_value(d, 0, x, "가나다")
    first = str(tmp_path / "1.pdf"); d.save(first)
    e = Document(first)
    w = next(e.fitz[0].widgets())
    assert w.text_font == "Malgun"
    A.set_field_value(e, 0, w.xref, "뷁똠방각하")   # 줄인 글꼴에 없던 글자
    second = str(tmp_path / "2.pdf"); e.save(second)
    g = fitz.open(second)
    assert "뷁똠방각하" in g[0].get_text()
    assert next(g[0].widgets()).field_value == "뷁똠방각하"
    g.close()


def test_ascii_field_keeps_helvetica_and_malgun_is_a_choice():
    d = Document(); d.fitz.close(); d.fitz = fitz.open(); d.fitz.new_page()
    x = A.add_field(d, 0, fitz.Rect(50, 50, 250, 80), "text", value="abc")
    assert "/Helv " in d.fitz.xref_get_key(x, "DA")[1]
    assert ("Malgun", "맑은 고딕") in P.FONTS
    P.write(d, [(0, x)], {"font": "Malgun"})
    assert P.read(d, 0, x)["font"] == "Malgun"
    assert b"/Malgun" in ap_of(d.fitz, x)[1]
    P.write(d, [(0, x)], {"font": "Cour"})
    assert P.read(d, 0, x)["font"] == "Cour"
    A.set_field_value(d, 0, x, "한글")    # Courier 로는 못 쓰는 글자면 맑은 고딕으로
    assert P.read(d, 0, x)["font"] == "Malgun"


def test_centered_korean_is_recentered():
    d = Document(); d.fitz.close(); d.fitz = fitz.open(); d.fitz.new_page()
    x = A.add_field(d, 0, fitz.Rect(50, 50, 250, 80), "text")
    P.write(d, [(0, x)], {"align": 1})
    A.set_field_value(d, 0, x, "가운데 abc")
    blocks = [b for b in d.fitz[0].get_text("dict")["blocks"] if b.get("lines")]
    bx = blocks[0]["bbox"]
    mid = (bx[0] + bx[2]) / 2
    assert abs(mid - 150) < 6


# ── 단추 아이콘 ─────────────────────────────────────────────────
def test_button_icon_survives_save(tmp_path):
    d = Document(blank(tmp_path))
    x = A.add_field(d, 0, fitz.Rect(50, 50, 200, 110), "button", label="보내기")
    P.write(d, [(0, x)], {"layout": 2, "icon": ("N", icon_png(tmp_path))})
    assert P.read(d, 0, x)["icons"] == {"N": True, "D": False, "R": False}
    P.write(d, [(0, x)], {"captions": {"N": "다시", "D": "", "R": ""}})   # 라벨을 바꿔도 아이콘은 남는다
    out = str(tmp_path / "아이콘.pdf"); d.save(out)
    g = fitz.open(out)
    w = next(g[0].widgets())
    r = P.read(g, 0, w.xref)
    assert r["icons"]["N"] and r["layout"] == 2
    _, s = ap_of(g, w.xref)
    assert b"/FmIcon Do" in s
    assert "다시" in g[0].get_text()
    pix = g[0].get_pixmap(clip=fitz.Rect(120, 55, 130, 75))   # 단추 위쪽 가운데 = 아이콘
    px = pix.pixel(pix.width // 2, pix.height // 2)
    assert px[0] > 200 and px[1] < 80
    g.close()


def test_icon_only_hides_label_and_clear_removes(tmp_path):
    d = Document(blank(tmp_path))
    x = A.add_field(d, 0, fitz.Rect(50, 50, 200, 110), "button", label="보내기")
    P.write(d, [(0, x)], {"layout": 1, "icon": ("N", icon_png(tmp_path))})
    assert "보내기" not in d.fitz[0].get_text()
    P.write(d, [(0, x)], {"icon": ("N", "")})
    assert not P.read(d, 0, x)["icons"]["N"]
    assert b"FmIcon" not in ap_of(d.fitz, x)[1]


def test_damaged_icon_file(tmp_path):
    bad = tmp_path / "깨진.png"; bad.write_bytes(b"not a picture")
    d = Document(blank(tmp_path))
    with pytest.raises(ValueError, match="파일을 로드하지 못했습니다"):
        P.make_icon(d, str(bad))


def test_dialog_icon_buttons_follow_layout(tmp_path):
    from app.ui.dlg_field_props import FieldPropsDialog
    d = Document(blank(tmp_path))
    x = A.add_field(d, 0, fitz.Rect(50, 50, 200, 110), "button")
    dlg = FieldPropsDialog(None, d, [(0, x)])
    assert not dlg.icon_pick.isEnabled()   # 「라벨만」 이면 아이콘 못 고름(알PDF 대로)
    dlg.btn_layout.setCurrentIndex(1)
    assert dlg.icon_pick.isEnabled() and dlg.icon_state.text() == "없음"
    dlg._icon_set(icon_png(tmp_path))
    assert dlg.icon_state.text() == "그림 있음" and dlg.icon_clear.isEnabled()
    assert P.read(d, 0, x)["icons"]["N"]
    dlg.states.setCurrentRow(1)
    assert dlg.icon_state.text() == "없음"
    dlg.close()


# ── 잠긴 칸 ─────────────────────────────────────────────────────
@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)
    FT.CLIP.clear()


def _drag(v, a, b):
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(v.viewport(), b); pump(0.02)
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b); pump(0.05)


def test_locked_field_cannot_move_or_delete(win, tmp_path):
    win.open_path(blank(tmp_path)); pump(0.25)
    t = win.cur(); v = t.viewer
    win.ribbon.set_toggle("form_edit", True); win.on_toggle("form_edit", True)
    lx = A.add_field(t.doc, 0, fitz.Rect(100, 100, 200, 130), "text")
    fx = A.add_field(t.doc, 0, fitz.Rect(100, 200, 200, 230), "text")
    P.write(t.doc, [(0, lx)], {"locked": True})
    t.changed(None); pump(0.1)

    def vp(x, y):
        return v.mapFromScene(v.from_page_rect(0, fitz.Rect(x, y, x, y)).topLeft())

    def rect(x):
        return next(fitz.Rect(w.rect) for w in t.doc.fitz[0].widgets() if w.xref == x)

    _drag(v, vp(150, 115), vp(250, 315))
    assert rect(lx) == fitz.Rect(100, 100, 200, 130)   # 잠긴 칸은 그대로
    _drag(v, vp(150, 215), vp(250, 315))
    assert abs(rect(fx).x0 - 200) < 2   # 잠기지 않은 칸은 옮겨짐
    # 둘을 같이 골라 지우면 잠긴 칸은 남는다
    v.select_fields([(0, lx), (0, fx)])
    v.delete_selection(); pump(0.05)
    assert [w.xref for w in t.doc.fitz[0].widgets()] == [lx]
    v.select_fields([(0, lx)])
    v.delete_selection(); pump(0.05)
    assert len(list(t.doc.fitz[0].widgets())) == 1


def test_align_leaves_locked_field(win, tmp_path):
    win.open_path(blank(tmp_path)); pump(0.25)
    t = win.cur(); v = t.viewer
    win.ribbon.set_toggle("form_edit", True); win.on_toggle("form_edit", True)
    xs = [A.add_field(t.doc, 0, fitz.Rect(60 + 40 * k, 100 + 50 * k, 160 + 40 * k, 130 + 50 * k), "text") for k in range(3)]
    P.write(t.doc, [(0, xs[2])], {"locked": True})
    t.changed(None); pump(0.1)
    v.select_fields([(0, x) for x in xs])
    assert FT.align(win, t, "left")
    x0 = {w.xref: round(w.rect.x0) for w in t.doc.fitz[0].widgets()}
    assert x0[xs[0]] == x0[xs[1]] == 60 and x0[xs[2]] == 140
