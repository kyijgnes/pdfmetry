"""라운드 18 다: 보기 회전은 화면에서만. test_view_rotate.py 에 없는 끝 조건을 실제 마우스로 확인한다.
① 90·180·270 으로 돌려도 「수정됨」이 안 되고 저장해도 쪽 /Rotate 그대로
② 돌린 화면에서 주석 고르기·끌기, 편집 객체 고르기·끌기가 제자리(쪽 자체가 90 돈 문서와 겹친 경우 포함)
③ 탭마다 따로, 새로 연 문서·새 문서는 0
python -m pytest app/tests/test_view_rotate_r18.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pdfm_view_rotate_r18_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import objects as O  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

W, H = 595, 842
LEFT = Qt.MouseButton.LeftButton


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def png(color=(255, 0, 0), w=20, h=10) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), 0)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


ANNOT = fitz.Rect(150, 250, 270, 330)   # 회전 전 쪽 좌표
IMAGE = fitz.Rect(320, 420, 420, 480)


def make_pdf(file_rot: int = 0, annot: bool = False, image: bool = False) -> str:
    d = fitz.open()
    for _ in range(2):
        p = d.new_page(width=W, height=H)
        if image:
            p.insert_image(IMAGE, stream=png(), keep_proportion=False)
        if annot:
            a = p.add_rect_annot(ANNOT)
            a.set_colors(stroke=(0, 0, 1)); a.update()
        p.set_rotation(file_rot)
    out = os.path.join(tempfile.mkdtemp(), f"보기회전r18_{file_rot}.pdf")
    d.save(out); d.close()
    return out


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


def view_pt(rot: int, x: float, y: float) -> tuple[float, float]:
    """회전 전 W×H 쪽의 (x, y) 가 시계 방향으로 모두 rot(쪽 회전 + 보기 회전) 돈 화면에서 놓이는 자리(손으로 푼 식)."""
    return {0: (x, y), 90: (H - y, x), 180: (W - x, H - y), 270: (y, W - x)}[rot % 360]


def scene_of(v, page: int, vx: float, vy: float) -> QPointF:
    it = v.items[page]
    return QPointF(it.pos().x() + vx * v.zoom, it.pos().y() + vy * v.zoom)


def mouse(v, kind, sp: QPointF, buttons):
    vp = QPointF(v.mapFromScene(sp))
    QApplication.sendEvent(v.viewport(), QMouseEvent(kind, vp, v.viewport().mapToGlobal(vp), LEFT, buttons,
                                                     Qt.KeyboardModifier.NoModifier))


def drag(v, a: QPointF, b: QPointF):
    mouse(v, QEvent.Type.MouseButtonPress, a, LEFT)
    for k in (0.25, 0.5, 0.75, 1.0):
        mouse(v, QEvent.Type.MouseMove, QPointF(a.x() + (b.x() - a.x()) * k, a.y() + (b.y() - a.y()) * k), LEFT)
    mouse(v, QEvent.Type.MouseButtonRelease, b, Qt.MouseButton.NoButton)
    pump(0.1)


def click(v, sp: QPointF):
    mouse(v, QEvent.Type.MouseButtonPress, sp, LEFT)
    mouse(v, QEvent.Type.MouseButtonRelease, sp, Qt.MouseButton.NoButton)
    pump(0.05)


def rotated(win, path: str, view_rot: int):
    """열고 리본 「보기 회전 → 시계 방향」을 view_rot/90 번 누른다."""
    win.open_path(path); pump(0.2)
    t = win.cur(); v = t.viewer
    for _ in range(view_rot // 90):
        win.on_action("rotate_view_cw"); pump(0.05)
    assert v.view_rot == view_rot
    return t, v


def show(v, r: fitz.Rect):
    v.ensureVisible(v.from_page_rect(0, r), 40, 40); pump(0.03)


def close_rect(got: fitz.Rect, want: fitz.Rect, tol: float):
    return all(abs(g - w_) < tol for g, w_ in zip(fitz.Rect(got), want))


# ── ① 파일은 안 바뀜 ─────────────────────────────────────────────
@pytest.mark.parametrize("file_rot", [0, 90])
def test_view_rotation_never_marks_modified_and_save_keeps_rotate(win, file_rot):
    path = make_pdf(file_rot)
    win.open_path(path); pump(0.2)
    t = win.cur()
    for key in ("rotate_view_cw", "rotate_view_cw", "rotate_view_cw"):   # 90 → 180 → 270
        win.on_action(key); pump(0.05)
        assert not t.doc.modified, t.viewer.view_rot
        assert all(p.rotation == file_rot for p in t.doc.fitz)
    assert t.viewer.view_rot == 270
    t.doc.modified = True   # 사용자가 「저장」을 눌러 억지로 쓰게 해도
    assert win.save(t) is True
    pump(0.05)
    with fitz.open(path) as f:
        assert [p.rotation for p in f] == [file_rot, file_rot]


# ── ② 돌린 화면에서 주석 고르기·끌기 ───────────────────────────────
@pytest.mark.parametrize("file_rot,view_rot", [(0, 0), (0, 90), (0, 180), (0, 270), (90, 90), (90, 270)])
def test_annot_select_and_drag_in_rotated_view(win, file_rot, view_rot):
    t, v = rotated(win, make_pdf(file_rot, annot=True), view_rot)
    tot = file_rot + view_rot
    win.on_tool("select"); pump(0.02)   # 손 도구는 고르기만, 끌기는 선택 도구
    show(v, ANNOT)
    c = ANNOT.tl + (ANNOT.br - ANNOT.tl) * 0.5
    a = scene_of(v, 0, *view_pt(tot, c.x, c.y))
    click(v, a)
    assert v.sel is not None and v.sel.kind == "annot" and v.sel.page == 0, v.sel
    # 화면에서 오른쪽으로 60, 아래로 30 끈다 → 쪽에서는 화면 회전을 거꾸로 푼 만큼 움직여야 한다
    b = QPointF(a.x() + 60 * v.zoom, a.y() + 30 * v.zoom)
    drag(v, a, b)
    bx, by = view_pt(tot, ANNOT.x0, ANNOT.y0)   # 쪽의 왼쪽 위가 화면에서 놓인 자리 → 끈 만큼 옮긴 화면 자리
    want_tl_view = (bx + 60, by + 30)
    pg = t.doc.fitz[0]
    got = fitz.Rect(next(iter(pg.annots())).rect)
    # 화면에서 옮긴 자리를 쪽 좌표로 손으로 되돌린다
    inv = {0: lambda x, y: (x, y), 90: lambda x, y: (y, H - x), 180: lambda x, y: (W - x, H - y),
           270: lambda x, y: (W - y, x)}[tot % 360]
    px, py = inv(*want_tl_view)
    want = fitz.Rect(px, py, px + ANNOT.width, py + ANNOT.height)
    assert close_rect(got, want, 2 + v.zoom), (file_rot, view_rot, got, want)
    assert t.doc.modified and pg.rotation == file_rot


# ── ② 돌린 화면에서 편집 객체(그림) 고르기·끌기 ─────────────────────
@pytest.mark.parametrize("file_rot,view_rot", [(0, 90), (0, 180), (0, 270), (90, 90), (90, 180)])
def test_edit_object_select_and_drag_in_rotated_view(win, file_rot, view_rot):
    t, v = rotated(win, make_pdf(file_rot, image=True), view_rot)
    tot = file_rot + view_rot
    win._set_edit(t, True); pump(0.05)
    show(v, IMAGE)
    c = IMAGE.tl + (IMAGE.br - IMAGE.tl) * 0.5
    a = scene_of(v, 0, *view_pt(tot, c.x, c.y))
    click(v, a)
    assert v.sel is not None and v.sel.kind == "image", v.sel
    assert close_rect(v.sel.rect, IMAGE, 1.5), (v.sel.rect, IMAGE)
    # 고른 상태에서 몸통을 끌어 옮긴다: 화면 아래로 40
    b = QPointF(a.x(), a.y() + 40 * v.zoom)
    drag(v, a, b)
    dx, dy = {0: (0, 40), 90: (40, 0), 180: (0, -40), 270: (-40, 0)}[tot % 360]   # 화면 아래 = 쪽에서 이 방향
    imgs = [o for o in O.list_objects(t.doc, 0) if o["kind"] == "image"]
    assert len(imgs) == 1
    want = IMAGE + (dx, dy, dx, dy)
    assert close_rect(imgs[0]["rect"], want, 2 + v.zoom), (file_rot, view_rot, imgs[0]["rect"], want)
    assert t.doc.fitz[0].rotation == file_rot


# ── ③ 탭마다 따로, 새 문서는 0 ────────────────────────────────────
def test_per_tab_and_new_documents_start_upright(win):
    t1, v1 = rotated(win, make_pdf(0), 180)
    win.open_path(make_pdf(90)); pump(0.2)
    t2 = win.cur()
    assert t2 is not t1 and t2.viewer.view_rot == 0
    win.on_action("rotate_view_ccw"); pump(0.05)
    assert t2.viewer.view_rot == 270 and v1.view_rot == 180
    win.doctabs.setCurrentIndex(win.tabs.index(t1)); pump(0.05)   # 탭을 오가도 각자 그대로
    assert win.cur() is t1 and v1.view_rot == 180 and t2.viewer.view_rot == 270
    assert t1.thumbs.thumbs.rot == 180 and t2.thumbs.thumbs.rot == 270   # 썸네일도 그 탭 각을 따른다
    win.on_action("create_blank"); pump(0.2)   # 새 문서는 바로 선 화면으로
    t3 = win.cur()
    assert t3 not in (t1, t2) and t3.viewer.view_rot == 0 and t3.thumbs.thumbs.rot == 0
    assert not t1.doc.modified and not t2.doc.modified


# ── 돌린 화면에서 건너뛰기·Tab 차례·맞춤도 화면 기준 ─────────────────
def test_goto_rect_lands_on_rotated_spot(win):
    """주석 목록·검색 결과로 건너뛸 때 회전 전 높이가 아니라 돌린 화면의 자리로 간다."""
    t, v = rotated(win, make_pdf(0, annot=True), 90)
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum()); pump(0.05)
    far = fitz.Rect(ANNOT.x0, 700, ANNOT.x1, 760)   # 회전 전 아래쪽 → 돌린 화면에서는 왼쪽
    v.goto(1, far.y0, rect=far); pump(0.05)
    sr = v.from_page_rect(1, far)
    vis = v.mapToScene(v.viewport().rect()).boundingRect()
    assert vis.contains(sr), (vis, sr)
    t.pick_annot(0, next(iter(t.doc.fitz[0].annots())).xref); pump(0.05)   # 주석 목록에서 고르기
    vis = v.mapToScene(v.viewport().rect()).boundingRect()
    assert vis.contains(v.from_page_rect(0, ANNOT))


def test_tab_order_follows_rotated_screen(win):
    from app.ui import context_menu as CM

    def doc(d):
        for p in d:
            for r in (fitz.Rect(400, 50, 450, 80), fitz.Rect(50, 700, 100, 730)):   # 회전 전 오른쪽 위 · 왼쪽 아래
                a = p.add_rect_annot(r); a.update()
    d = fitz.open(); d.new_page(width=W, height=H); doc(d)
    path = os.path.join(tempfile.mkdtemp(), "tab.pdf"); d.save(path); d.close()
    t, v = rotated(win, path, 90)
    v.set_view_rotation(0)
    assert [round(fitz.Rect(r).y0, -1) for _, _, r in CM.page_targets(v, 0)] == [50, 700]
    v.set_view_rotation(90)
    order = [round(fitz.Rect(r).y0, -1) for _, _, r in CM.page_targets(v, 0)]
    assert order == [700, 50]   # 시계 방향 90 화면에서는 회전 전 왼쪽(아래)이 위로 온다


def test_align_left_uses_rotated_screen(win):
    from app.ui import edit_tools as ET

    def two(p):
        p.insert_image(fitz.Rect(100, 100, 160, 140), stream=png(), keep_proportion=False)
        p.insert_image(fitz.Rect(300, 400, 360, 440), stream=png((0, 0, 255)), keep_proportion=False)
    d = fitz.open(); p = d.new_page(width=W, height=H); two(p)
    path = os.path.join(tempfile.mkdtemp(), "align.pdf"); d.save(path); d.close()
    t, v = rotated(win, path, 90)
    win._set_edit(t, True); pump(0.05)
    v.select_objects(0, [o for o in O.list_objects(t.doc, 0) if o["kind"] == "image"])
    ET.align(win, t, "left"); pump(0.05)
    # 시계 방향 90 화면의 왼쪽 = 회전 전 쪽의 아래(y 큰 쪽): 두 그림의 y1 이 같아진다
    ys = sorted(round(fitz.Rect(o["rect"]).y1) for o in O.list_objects(t.doc, 0) if o["kind"] == "image")
    assert ys[0] == ys[1] == 440, ys
