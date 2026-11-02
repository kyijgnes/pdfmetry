"""주석 리본·도구 동작 시험: 새 리본 차례 · 켜진 도구 다시 누르면 빠른 스타일 · 도구 계속 쓰기 기억 · 주석 숨기면 도구 끔 ·
글자 표시 도구 글 흐름 고르기 · 모양 더보기 넷 · Shift 고정 · 지우개 반지름.

python -m pytest app/tests/test_annot_ribbon.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_annot_ribbon_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annot_shapes as SH  # noqa: E402
from app.engine import annot_style as S  # noqa: E402
from app.engine import annots as A  # noqa: E402
from app.ui import ribbon as R  # noqa: E402
from app.ui import window as W  # noqa: E402
from app.ui.quick_style import QuickStyle, eraser_radius, style_key  # noqa: E402
from app.ui.viewer import constrain, flow_rects  # noqa: E402

LINE1 = "첫째 줄 사과 배 감 귤"
LINE2 = "둘째 줄 포도 밤 대추 끝"


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str):
    d = fitz.open()
    p = d.new_page(width=595, height=842)
    p.insert_font(fontname="malgun", fontfile=r"C:\Windows\Fonts\malgun.ttf")
    p.insert_text((72, 100), LINE1, fontsize=14, fontname="malgun")
    p.insert_text((72, 122), LINE2, fontsize=14, fontname="malgun")
    d.save(path); d.close()


@pytest.fixture()
def pdf():
    p = os.path.join(tempfile.mkdtemp(), "주석리본.pdf"); make_pdf(p)
    return p


@pytest.fixture()
def win():
    w = W.MainWindow(); w.show(); w.resize(1300, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


def _word(words, text):
    return next(w for w in words if w[4] == text)


def _mouse(v, kind, scene_pt: QPointF, buttons, mods=Qt.KeyboardModifier.NoModifier):
    vp = QPointF(v.mapFromScene(scene_pt))
    e = QMouseEvent(kind, vp, v.viewport().mapToGlobal(vp), Qt.MouseButton.LeftButton, buttons, mods)
    QApplication.sendEvent(v.viewport(), e)


def drag(v, page: int, a: fitz.Point, b: fitz.Point, mods=Qt.KeyboardModifier.NoModifier):
    sa, sb = v.from_page_point(page, a), v.from_page_point(page, b)
    v.ensureVisible(v.from_page_rect(page, fitz.Rect(a, b).normalize()), 10, 10); pump(0.02)
    _mouse(v, QEvent.Type.MouseButtonPress, sa, Qt.MouseButton.LeftButton, mods)
    mid = QPointF((sa.x() + sb.x()) / 2, (sa.y() + sb.y()) / 2)
    _mouse(v, QEvent.Type.MouseMove, mid, Qt.MouseButton.LeftButton, mods)
    _mouse(v, QEvent.Type.MouseMove, sb, Qt.MouseButton.LeftButton, mods)
    _mouse(v, QEvent.Type.MouseButtonRelease, sb, Qt.MouseButton.NoButton, mods)
    pump(0.02)


# ── 리본 ─────────────────────────────────────────────────────
def test_comment_tab_order_follows_groups():
    items = next(it for k, _, it in R.TABS if k == "comment")
    keys = [it[0] for it in items if it != R.SEP]
    want = ["highlight", "areahl", "underline", "strike", "markup_more", "annot_color", "rect", "oval", "arrow", "shape_more",
            "note", "measure", "pencil", "eraser", "stamp", "signature", "typewriter", "textbox", "callout", "attach",
            "show_annots", "keep_tool", "snapshot"]
    assert keys == want
    more = next(it for it in items if it != R.SEP and it[0] == "shape_more")
    sub = dict((m[0], m) for m in more[4])
    assert [m[0] for m in sub["shape_more2"][2]] == ["cross", "bent_arrow", "balloon"] and "triangle" in sub
    assert more[5] == "line"


def test_reclick_active_tool_opens_quick_style_instead_of_off(win, pdf):
    win.open_path(pdf); pump(0.1); t = win.cur()
    win.ribbon.set_tab("comment")
    b = win.ribbon.buttons["rect"][0]
    got = []
    win.ribbon.quickStyle.connect(lambda k, btn: got.append(k))
    b.click(); pump(0.02)
    assert t.viewer.tool == "rect" and b.isChecked()
    b.click(); pump(0.05)
    assert got == ["rect"] and t.viewer.tool == "rect" and b.isChecked()
    q = win._quick
    assert isinstance(q, QuickStyle) and q.isVisible()
    q.hide(); pump(0.02)
    # 손 도구 단추는 다시 눌러도 그대로(주석 도구만 빠른 스타일)
    win.on_tool("hand"); assert t.viewer.tool == "hand"


def test_quick_style_applies_and_remembers(tmp_path):
    store = S.StyleStore(str(tmp_path / "styles.json"))

    class V:
        props = {"stroke": (1, 0, 0), "fill": None, "width": 1.0, "opacity": 1.0}
    settings = {}
    q = QuickStyle("rect", V, settings, lambda: None, store=store)
    q.show(); pump(0.02)
    q.opacity.setValue(40); q.width.setValue(35)
    q._pick((49 / 255, 131 / 255, 1))
    assert V.props["opacity"] == 0.4 and V.props["width"] == 3.5 and V.props["stroke"][2] == 1
    q.hide()
    again = S.StyleStore(str(tmp_path / "styles.json")).get("rect")
    assert again["opacity"] == 0.4 and again["width"] == 3.5
    # 모양 더보기 넷은 다각형 칸을 같이 쓴다
    assert style_key("triangle") == "polygon" and style_key("hand") is None


def test_eraser_quick_style_saves_radius():
    saved = []

    class V:
        props = {}
        r = None

        @classmethod
        def set_eraser_radius(cls, r):
            cls.r = r
    settings = {}
    q = QuickStyle("eraser", V, settings, lambda: saved.append(dict(settings)))
    q.show(); pump(0.02)
    q.radius.setValue(22); q.hide()
    assert V.r == 22 and saved[-1]["eraser_radius"] == 22 and eraser_radius(settings) == 22
    assert eraser_radius({}) == 10 and eraser_radius({"eraser_radius": 99}) == 30


# ── 도구 계속 쓰기 ───────────────────────────────────────────
def test_keep_tool_defaults():
    s = {}
    assert R.keep_tool(s, "highlight") and R.keep_tool(s, "typewriter") and R.keep_tool(s, "eraser")
    assert not R.keep_tool(s, "stamp") and not R.keep_tool(s, "signature") and not R.keep_tool(s, "attach")


def test_keep_tool_toggle_persists_and_eraser_grays(win, pdf):
    win.open_path(pdf); pump(0.1); t = win.cur()
    kb = win.ribbon.buttons["keep_tool"][0]
    win.on_tool("rect")
    assert kb.isEnabled() and kb.isChecked()
    kb.click(); pump(0.02)   # 끔
    assert W.load_settings()["keep_tool"]["rect"] is False   # 파일에 남아 다시 켜도 그대로
    win._tool_done("rect")
    assert t.viewer.tool == "select"
    win.on_tool("rect")
    assert not kb.isChecked()
    win.on_tool("highlight"); win._tool_done("highlight")
    assert t.viewer.tool == "highlight" and kb.isChecked()
    win.on_tool("eraser")
    assert not kb.isEnabled() and not kb.isChecked()
    win.on_tool("hand")
    assert not kb.isEnabled()
    # 새 창(앱을 다시 켠 것)에서도 그대로
    w2 = W.MainWindow(); w2.open_path(pdf); pump(0.1)
    try:
        w2.on_tool("rect")
        assert not w2.ribbon.buttons["keep_tool"][0].isChecked()
    finally:
        for x in w2.tabs:
            x.doc.modified = False
        w2.close()
    win.settings["keep_tool"]["rect"] = True; W.save_settings(win.settings)


def test_hiding_annots_turns_tools_off(win, pdf):
    win.open_path(pdf); pump(0.1); t = win.cur()
    win.on_tool("pencil")
    win.on_toggle("show_annots", False)
    assert t.viewer.tool == "select"
    assert not win.ribbon.buttons["rect"][0].isEnabled() and not win.ribbon.buttons["keep_tool"][0].isEnabled()
    assert win.ribbon.buttons["show_annots"][0].isEnabled() and win.ribbon.buttons["snapshot"][0].isEnabled()
    win.on_tool("rect")
    assert t.viewer.tool == "select"
    win.on_toggle("show_annots", True)
    assert win.ribbon.buttons["rect"][0].isEnabled()


# ── 글자 표시: 글 흐름대로 ───────────────────────────────────
def test_flow_rects_two_lines(pdf):
    d = fitz.open(pdf); words = d[0].get_text("words")
    a = _word(words, "배"); b = _word(words, "밤")
    rects = flow_rects(words, fitz.Point(a[0] + 1, (a[1] + a[3]) / 2), fitz.Point(b[2] - 1, (b[1] + b[3]) / 2))
    assert len(rects) == 2
    first = [w[4] for w in words if fitz.Rect(w[:4]).intersects(rects[0])]
    second = [w[4] for w in words if fitz.Rect(w[:4]).intersects(rects[1])]
    assert first == ["배", "감", "귤"] and second == ["둘째", "줄", "포도", "밤"]
    # 거꾸로 끌어도 같다
    back = flow_rects(words, fitz.Point(b[2] - 1, (b[1] + b[3]) / 2), fitz.Point(a[0] + 1, (a[1] + a[3]) / 2))
    assert back == rects


def test_highlight_drag_follows_text_flow(win, pdf):
    win.open_path(pdf); pump(0.2); t = win.cur(); v = t.viewer
    win.on_tool("highlight")
    words = v.words(0)
    a = _word(words, "배"); b = _word(words, "밤")
    drag(v, 0, fitz.Point(a[0] + 1, (a[1] + a[3]) / 2), fitz.Point(b[2] - 1, (b[1] + b[3]) / 2))
    pg = t.doc.fitz[0]; annots = list(pg.annots())
    assert len(annots) == 1 and annots[0].type[1] == "Highlight"
    vs = annots[0].vertices
    quads = [fitz.Quad(vs[k:k + 4]).rect for k in range(0, len(vs), 4)]
    got = [w[4] for w in words if any(fitz.Rect(w[:4]).intersects(q) and (fitz.Rect(w[:4]) & q).width > 1 for q in quads)]
    assert got == ["배", "감", "귤", "둘째", "줄", "포도", "밤"]
    assert v.tool == "highlight"   # 계속 쓰기 기본 켬


# ── 모양 넷 ──────────────────────────────────────────────────
def test_shape_points_fit_rect():
    r = fitz.Rect(100, 100, 200, 180)
    counts = {"triangle": 3, "cross": 12}
    for k in SH.SHAPES:
        pts = SH.shape_points(k, r)
        assert len(pts) >= counts.get(k, 10)
        bb = fitz.Rect(pts[0], pts[0])
        for q in pts:
            bb |= q
        assert r.x0 - 0.01 <= bb.x0 and bb.x1 <= r.x1 + 0.01 and r.y0 - 0.01 <= bb.y0 and bb.y1 <= r.y1 + 0.01
        assert bb.width > r.width * 0.9 and bb.height > r.height * 0.9, k


def test_four_shapes_drawn_and_survive_save(win, pdf, tmp_path):
    win.open_path(pdf); pump(0.2); t = win.cur(); v = t.viewer
    y = 200
    for k in SH.SHAPES:
        win.on_tool(k)
        assert v.tool == k
        drag(v, 0, fitz.Point(100, y), fitz.Point(180, y + 60))
        y += 90
    out = str(tmp_path / "모양.pdf")
    t.doc.fitz.save(out)
    d = fitz.open(out)
    pg = d[0]; polys = [a for a in pg.annots() if a.type[1] == "Polygon"]
    assert len(polys) == 4
    assert sorted(len(a.vertices) for a in polys)[:2] == [3, 12]


def test_shift_constrain():
    a = QPointF(0, 0)
    sq = constrain("rect", a, QPointF(30, 10))
    assert (sq.x(), sq.y()) == (30, 30)
    ln = constrain("line", a, QPointF(100, 8))
    assert abs(ln.y()) < 1e-6 and abs(ln.x() - (100 ** 2 + 64) ** 0.5) < 1e-6
    assert constrain("pencil", a, QPointF(5, 7)) == QPointF(5, 7)


def test_eraser_radius_from_settings(win, pdf):
    win.open_path(pdf); pump(0.1); t = win.cur(); v = t.viewer
    win.settings["eraser_radius"] = 18
    win.on_tool("eraser")
    assert v.props["eraser_radius"] == 18
    A.ink(t.doc, 0, [[fitz.Point(300, 400), fitz.Point(360, 400)]])
    # 반지름 18px(확대 1 기준) 안쪽이면 지운다
    p = fitz.Point(330, 400 + 14 / v.zoom)
    assert A.erase_ink_at(t.doc, 0, p, radius=v._eraser_r())
    win.settings.pop("eraser_radius")
