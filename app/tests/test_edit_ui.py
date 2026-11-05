"""라운드 17 다: 편집 객체·주석 화면 잇기(docs/편집 객체 설계.md 5절 다 줄의 끝 조건).

세 모드의 Ctrl+C·X·V 와 오른쪽 클릭 자르기·복사·붙여넣기 · 다른 탭 문서에 붙기 · 다른 프로그램의 그림·글 붙이기 ·
객체 셋 왼쪽 맞춤·가로 분배가 실행 취소 한 번에 풀림 · 글 위 그림 「맨 뒤로」 · 원문 글 「맨 뒤로」 · 주석 「여러 쪽에 적용」.

python -m pytest app/tests/test_edit_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pdfm_edit_ui_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import objects as O  # noqa: E402
from app.ui import clipboard as CB  # noqa: E402
from app.ui import context_menu as CM  # noqa: E402
from app.ui import edit_tools as ET  # noqa: E402
from app.ui import form_tools as FT  # noqa: E402
from app.ui.dlg_apply_pages import ApplyPagesDialog  # noqa: E402
from app.ui.props_arrange import ArrangeBox  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def png(color=(255, 0, 0), w=20, h=10) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), 0)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


def make_pdf(draw=None, pages: int = 1, name: str = "편집.pdf") -> str:
    d = fitz.open()
    for _ in range(pages):
        d.new_page(width=595, height=842)
    if draw:
        draw(d)
    out = os.path.join(tempfile.mkdtemp(), name)
    d.save(out); d.close()
    return out


def three_images(d):
    p = d[0]
    for x, c in ((100, (255, 0, 0)), (200, (0, 160, 0)), (330, (0, 0, 255))):
        p.insert_image(fitz.Rect(x, 100 + x / 10, x + 60, 160 + x / 10), stream=png(c), keep_proportion=False)


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)
    CB.clear()
    QApplication.clipboard().clear()


def open_doc(win, draw=None, pages: int = 1, name: str = "편집.pdf"):
    win.open_path(make_pdf(draw, pages, name)); pump(0.2)
    t = win.cur()
    return t, t.viewer


def edit_on(win, t):
    win._set_edit(t, True)
    pump(0.05)


def key(v, k, mods=CTRL):
    QTest.keyClick(v, k, mods)
    pump(0.05)


def menu_action(v, i: int, sp: QPointF, label: str):
    m = CM.build(v, i, sp)
    acts = [a for a in m.actions()]
    for a in list(acts):
        if a.menu():
            acts += a.menu().actions()
    hit = [a for a in acts if a.text() == label]
    assert hit, f"메뉴에 「{label}」 없음: {[a.text() for a in acts]}"
    return hit[0]


def annots(t, page: int = 0) -> list[tuple[str, fitz.Rect]]:
    pg = t.doc.fitz[page]
    return [(a.type[1], fitz.Rect(a.rect)) for a in pg.annots()]


def widgets(t, page: int = 0) -> list[int]:
    pg = t.doc.fitz[page]
    return [w.xref for w in pg.widgets()]


def objs(t, page: int = 0, kind: str | None = None):
    return [o for o in O.list_objects(t.doc, page) if kind in (None, o["kind"])]


# ── 손·선택 모드: 주석 ────────────────────────────────────────────
def square_doc(d):
    a = d[0].add_rect_annot(fitz.Rect(100, 100, 200, 160))
    a.set_colors(stroke=(0, 0, 1)); a.update()


def select_annot(v, page: int = 0):
    pg = v.doc.fitz[page]   # 쪽을 붙잡아 둔다(주석보다 먼저 풀리면 죽음)
    a = next(iter(pg.annots()))
    x, r = a.xref, fitz.Rect(a.rect)
    CM._select(v, page, ("annot", x, r))
    return x


def test_annot_ctrl_c_v_x(win):
    t, v = open_doc(win, square_doc)
    select_annot(v)
    key(v, Qt.Key.Key_C)
    assert len(CB.ANNOTS) == 1 and CB.mode_of(v) == "annots"
    key(v, Qt.Key.Key_V)
    rects = sorted(r.x0 for _, r in annots(t))
    assert len(rects) == 2 and abs(rects[1] - rects[0] - 10) < 1     # 같은 문서 같은 쪽이면 10pt 비켜서
    assert v.sel and v.sel.kind == "annot"                            # 붙인 것을 고른 상태
    key(v, Qt.Key.Key_X)
    assert len(annots(t)) == 1 and len(CB.ANNOTS) == 1
    win.on_action("undo"); pump(0.05)
    assert len(annots(t)) == 2                     # 자르기는 한 걸음


def test_annot_right_click_cut_copy_paste(win):
    t, v = open_doc(win, square_doc)
    select_annot(v)
    sp = v.from_page_point(0, fitz.Point(150, 130))
    menu_action(v, 0, sp, "복사").trigger(); pump(0.05)
    assert len(CB.ANNOTS) == 1
    v.clear_selection()
    sp2 = v.from_page_point(0, fitz.Point(300, 400))
    act = menu_action(v, 0, sp2, "붙여넣기")
    assert act.isEnabled()
    act.trigger(); pump(0.05)
    xs = annots(t)
    got = [r for _, r in xs if r.x0 > 250]
    assert len(got) == 1 and abs(got[0].x0 - 300) < 1.5 and abs(got[0].y0 - 400) < 1.5   # 누른 자리에 왼쪽 위
    assert len(xs) == 2
    select_annot(v)
    menu_action(v, 0, sp, "자르기").trigger(); pump(0.05)
    assert len(annots(t)) == 1


def test_annot_paste_other_tab(win):
    t1, v1 = open_doc(win, square_doc, name="원본.pdf")
    select_annot(v1)
    key(v1, Qt.Key.Key_C)
    t2, v2 = open_doc(win, None, name="다른.pdf")
    assert win.cur() is t2
    key(v2, Qt.Key.Key_V)
    a = annots(t2)
    assert len(a) == 1 and abs(a[0][1].x0 - 99) < 1   # 다른 문서는 같은 자리
    assert a[0][0] == "Square"


def test_annot_apply_pages(win):
    t, v = open_doc(win, square_doc, pages=4)
    select_annot(v)
    sp = v.from_page_point(0, fitz.Point(150, 130))
    assert menu_action(v, 0, sp, "여러 쪽에 적용").isEnabled()
    said = []
    win.say = said.append
    n, m = ET.apply_to(win, t, [0, 1, 2, 3])
    assert (n, m) == (3, 0)
    assert all(len(annots(t, j)) == 1 for j in range(4))
    assert said[-1] == "3쪽에 적용, 0쪽 건너뜀"
    win.on_action("undo"); pump(0.05)
    assert sum(len(annots(t, j)) for j in range(4)) == 1   # 한 걸음


def test_apply_pages_dialog_range():
    dlg = ApplyPagesDialog(None, 5, 0, "직사각형")
    dlg.range.rng.setChecked(True); dlg.range.edit.setText("2-4")
    dlg.accept()
    assert dlg.pages() == [1, 2, 3]


def test_markup_annot_apply_menu_off(win):
    def hl(d):
        d[0].insert_text((100, 100), "Hello", fontsize=14)
        d[0].add_highlight_annot(fitz.Rect(100, 88, 140, 102))
    t, v = open_doc(win, hl)
    select_annot(v)
    assert not menu_action(v, 0, v.from_page_point(0, fitz.Point(110, 95)), "여러 쪽에 적용").isEnabled()


# ── 편집 모드: 편집 객체 ──────────────────────────────────────────
def test_edit_ctrl_c_v_x_and_select_all(win):
    t, v = open_doc(win, three_images)
    edit_on(win, t)
    key(v, Qt.Key.Key_A)
    assert len(v.sel_objs) == 3
    first = objs(t)[0]
    v.select_objects(0, [first])
    key(v, Qt.Key.Key_C)
    assert len(CB.OBJECTS) == 1 and CB.ours_on_system()
    assert not QApplication.clipboard().image().isNull()            # 시스템 클립보드에 그림도 올림
    key(v, Qt.Key.Key_V)
    assert len(objs(t, kind="image")) == 4
    new = [o for o in objs(t) if o["ours"]]
    assert new and abs(new[-1]["rect"].x0 - first["rect"].x0 - 10) < 1
    assert v.sel is not None and v.sel.kind == "image"               # 붙인 것을 고른 상태
    key(v, Qt.Key.Key_X)
    assert len(objs(t, kind="image")) == 3
    win.on_action("undo"); pump(0.05)
    assert len(objs(t, kind="image")) == 4 and v.sel is None         # 실행 취소 뒤 고른 것 풂


def test_edit_right_click_and_other_tab(win):
    t1, v1 = open_doc(win, three_images, name="그림.pdf")
    edit_on(win, t1)
    o = objs(t1)[1]
    sp = v1.from_page_point(0, fitz.Point(o["rect"].x0 + 5, o["rect"].y0 + 5))
    CM.pick_object_at(v1, 0, sp)
    assert v1.sel and v1.sel.kind == "image"
    menu_action(v1, 0, sp, "복사").trigger(); pump(0.05)
    t2, v2 = open_doc(win, None, name="빈.pdf")
    edit_on(win, t2)
    sp2 = v2.from_page_point(0, fitz.Point(50, 500))
    act = menu_action(v2, 0, sp2, "붙여넣기")
    assert act.isEnabled()
    act.trigger(); pump(0.05)
    got = objs(t2, kind="image")
    assert len(got) == 1 and abs(got[0]["rect"].x0 - 50) < 1 and abs(got[0]["rect"].y0 - 500) < 1
    pix = t2.doc.fitz[0].get_pixmap(clip=fitz.Rect(55, 505, 60, 510))
    assert pix.pixel(1, 1)[1] > 120 and pix.pixel(1, 1)[0] < 80     # 초록 그림 그대로


def test_edit_paste_from_other_program_image_and_text(win):
    t, v = open_doc(win, None)
    edit_on(win, t)
    img = QImage(96, 48, QImage.Format.Format_RGB32); img.fill(QColor(0, 0, 255))
    QApplication.clipboard().setImage(img); pump(0.05)
    assert CB.can_paste("objects")
    key(v, Qt.Key.Key_V)
    im = objs(t, kind="image")
    assert len(im) == 1
    r = im[0]["rect"]
    assert abs(r.width - 72) < 1 and abs((r.x0 + r.x1) / 2 - 297.5) < 1   # 96px = 72pt, 쪽 가운데
    QApplication.clipboard().setText("다른 프로그램 글"); pump(0.05)
    key(v, Qt.Key.Key_V)
    assert "다른 프로그램 글" in t.doc.fitz[0].get_text()
    assert any(o["kind"] == "text" and o["ours"] for o in objs(t))


def test_align_left_and_distribute_one_undo_each(win):
    t, v = open_doc(win, three_images)
    edit_on(win, t)
    ET.select_all(v)
    assert len(v.sel_objs) == 3
    before = sorted((round(o["rect"].x0), round(o["rect"].y0)) for o in objs(t))
    win.on_action("obj_align_left"); pump(0.05)
    xs = {round(o["rect"].x0, 1) for o in objs(t)}
    assert xs == {100.0} and len(v.sel_objs) == 3                   # 고른 것이 그대로 남음
    win.on_action("undo"); pump(0.05)
    assert sorted((round(o["rect"].x0), round(o["rect"].y0)) for o in objs(t)) == before   # 한 번에 풀림
    ET.select_all(v)
    # 가운데 그림을 오른쪽으로 비켜 두고 가로 분배
    win.on_action("obj_align_dist_h"); pump(0.05)
    rs = sorted((o["rect"] for o in objs(t)), key=lambda r: r.x0)
    gaps = [round(rs[k + 1].x0 - rs[k].x1, 1) for k in range(2)]
    assert gaps[0] == gaps[1]
    win.on_action("undo"); pump(0.05)
    assert sorted((round(o["rect"].x0), round(o["rect"].y0)) for o in objs(t)) == before


def test_align_needs_two(win):
    t, v = open_doc(win, three_images)
    edit_on(win, t)
    v.select_objects(0, objs(t)[:1])
    win.on_action("obj_align_left"); pump(0.05)
    assert not t.doc.can_undo()
    win.on_action("obj_align_page_both"); pump(0.05)                 # 쪽 가운데는 하나부터
    r = objs(t)[-1]["rect"] if objs(t)[-1]["ours"] else next(o["rect"] for o in objs(t) if o["ours"])
    assert abs((r.x0 + r.x1) / 2 - 297.5) < 1 and abs((r.y0 + r.y1) / 2 - 421) < 1


def test_image_to_back_shows_text(win):
    def draw(d):
        p = d[0]
        p.insert_text((100, 150), "Hidden text", fontsize=30)
        p.insert_image(fitz.Rect(80, 100, 400, 170), stream=png((255, 255, 255)), keep_proportion=False)
    t, v = open_doc(win, draw)
    edit_on(win, t)
    clip = fitz.Rect(100, 120, 300, 155)
    assert min(t.doc.fitz[0].get_pixmap(clip=clip).samples) > 200
    img = next(o for o in objs(t) if o["kind"] == "image")
    v.select_objects(0, [img])
    sp = v.from_page_point(0, fitz.Point(390, 165))
    menu_action(v, 0, sp, "맨 뒤로 보내기").trigger(); pump(0.05)
    assert min(t.doc.fitz[0].get_pixmap(clip=clip).samples) < 60
    win.on_action("undo"); pump(0.05)
    assert min(t.doc.fitz[0].get_pixmap(clip=clip).samples) > 200


def test_original_text_to_back(win):
    def draw(d):
        p = d[0]
        p.insert_image(fitz.Rect(80, 100, 400, 170), stream=png((200, 230, 255)), keep_proportion=False)
        p.insert_text((100, 150), "Front text", fontsize=30)
    t, v = open_doc(win, draw)
    edit_on(win, t)
    txt = next(o for o in objs(t) if o["kind"] == "text")
    v.select_objects(0, [txt])
    assert v.sel.kind == "block"
    win.on_action("obj_back"); pump(0.05)
    order = [o["kind"] for o in objs(t)]
    assert order[0] == "text" and objs(t)[0]["ours"]                 # 글이 맨 뒤(먼저 그림)
    assert "Front text" in t.doc.fitz[0].get_text()
    assert len(t.doc._undo) == 1


def test_group_move_and_ctrl_click(win):
    t, v = open_doc(win, three_images)
    edit_on(win, t)
    a, b, _ = objs(t)
    v.select_objects(0, [a])
    v._toggle_object(0, fitz.Rect(b["rect"]).tl + (5, 5))
    assert len(v.sel_objs) == 2
    v._toggle_object(0, fitz.Rect(b["rect"]).tl + (5, 5))             # 다시 누르면 뺌
    assert not v.sel_objs and v.sel.rect == a["rect"]
    c = objs(t)[2]
    v.select_objects(0, [a, b])
    v._move_objects(0, 0, 30)
    ys = sorted(round(o["rect"].y0) for o in objs(t))
    assert ys == sorted([round(a["rect"].y0 + 30), round(b["rect"].y0 + 30), round(c["rect"].y0)])
    moved = [o for o in objs(t) if o["ours"]]
    assert len(moved) == 2 and len(v.sel_objs) == 2
    assert len(t.doc._undo) == 1


def test_arrange_box_buttons(win):
    t, v = open_doc(win, three_images)
    edit_on(win, t)
    box = ArrangeBox(v)
    v.select_objects(0, objs(t)[:1])
    box.show_sel(v.sel)
    assert not box.isHidden()
    assert box.buttons["front"].isEnabled() and box.buttons["page_both"].isEnabled()
    assert not box.buttons["left"].isEnabled() and not box.buttons["dist_h"].isEnabled()
    ET.select_all(v); box.show_sel(v.sel)
    assert box.buttons["dist_h"].isEnabled()
    v.set_edit_mode(False); box.show_sel(None)
    assert box.isHidden()


# ── 양식 편집: 양식 칸(옛 칸, 공용 클립보드로 옮긴 뒤) ────────────────────
def test_form_clip_uses_shared_slot(win):
    def draw(d):
        w = fitz.Widget(); w.field_type = fitz.PDF_WIDGET_TYPE_TEXT; w.field_name = "이름"
        w.rect = fitz.Rect(100, 100, 250, 130); d[0].add_widget(w)
    t, v = open_doc(win, draw)
    win.ribbon.set_toggle("form_edit", True); win.on_toggle("form_edit", True)
    pg = t.doc.fitz[0]
    x = next(iter(pg.widgets())).xref
    v.select_fields([(0, x)])
    key(v, Qt.Key.Key_C)
    assert FT.CLIP is CB.FIELDS and len(CB.FIELDS) == 1 and not CB.ANNOTS and not CB.OBJECTS
    key(v, Qt.Key.Key_V)
    assert len(widgets(t)) == 2


def test_forget_on_close(win):
    t, v = open_doc(win, square_doc)
    select_annot(v)
    key(v, Qt.Key.Key_C)
    did = id(t.doc)
    assert CB.ANNOTS[0]["doc"] == did
    win.close_tab(win.tabs.index(t)); pump(0.05)
    assert CB.ANNOTS[0]["doc"] is None and CB.SOURCE["annots"][0] is None
