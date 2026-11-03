"""보기 회전 시험: 화면에서만 돌고(파일 /Rotate 그대로), 돌린 화면의 클릭·끌기가 맞는 쪽 좌표로 바뀐다.

python -m pytest app/tests/test_view_rotate.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_view_rotate_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.ui.window import MainWindow  # noqa: E402

W, H = 595, 842


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, rotate: int = 0):
    d = fitz.open()
    for _ in range(2):
        p = d.new_page(width=W, height=H)
        p.set_rotation(rotate)
    d.save(path); d.close()


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


def pdf_with(rotate: int) -> str:
    p = os.path.join(tempfile.mkdtemp(), f"보기회전{rotate}.pdf"); make_pdf(p, rotate)
    return p


def view_pt(rot: int, x: float, y: float) -> tuple[float, float]:
    """회전 없는 W×H 쪽의 (x, y) 가 시계 방향 rot 만큼 돌린 화면에서 놓이는 자리(손으로 푼 식)."""
    return {0: (x, y), 90: (H - y, x), 180: (W - x, H - y), 270: (y, W - x)}[rot]


def scene_of(v, page: int, vx: float, vy: float) -> QPointF:
    it = v.items[page]
    return QPointF(it.pos().x() + vx * v.zoom, it.pos().y() + vy * v.zoom)


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_click_maps_to_page_coords(win, rot):
    win.open_path(pdf_with(0)); pump(0.2)
    v = win.cur().viewer
    v.set_view_rotation(rot); pump(0.1)
    it = v.items[0]
    w, h = (H, W) if rot in (90, 270) else (W, H)
    assert abs(it.w - w * v.zoom) < 0.5 and abs(it.h - h * v.zoom) < 0.5   # 돌린 쪽 크기
    for x, y in ((100, 200), (0, 0), (W, H), (500, 30)):
        sp = scene_of(v, 0, *view_pt(rot, x, y))
        p = v.to_page(0, sp)
        assert abs(p.x - x) < 0.01 and abs(p.y - y) < 0.01, (rot, x, y, p)
        back = v.from_page_point(0, fitz.Point(x, y))
        assert abs(back.x() - sp.x()) < 0.01 and abs(back.y() - sp.y()) < 0.01
    r = v.from_page_rect(0, fitz.Rect(100, 200, 200, 260))
    corners = [view_pt(rot, 100, 200), view_pt(rot, 200, 260)]
    want = scene_of(v, 0, min(c[0] for c in corners), min(c[1] for c in corners))
    assert abs(r.left() - want.x()) < 0.01 and abs(r.top() - want.y()) < 0.01


@pytest.mark.parametrize("rot", [90, 270])
def test_file_rotation_and_view_rotation_compose(win, rot):
    """파일이 90° 돈 쪽을 화면에서 또 돌려도 클릭 ↔ 쪽 좌표가 오간다."""
    win.open_path(pdf_with(90)); pump(0.2)
    v = win.cur().viewer
    v.set_view_rotation(rot); pump(0.1)
    for x, y in ((100, 200), (550, 800)):
        sp = v.from_page_point(0, fitz.Point(x, y))
        p = v.to_page(0, sp)
        assert abs(p.x - x) < 0.01 and abs(p.y - y) < 0.01
    total = (90 + rot) % 360
    it = v.items[0]
    # 회전 합이 0·180 이면 세로 쪽, 90·270 이면 가로 쪽으로 보인다
    assert (it.w > it.h) == (total in (90, 270))


def test_ribbon_rotate_view_is_display_only_and_per_tab(win):
    a, b = pdf_with(0), pdf_with(90)
    win.open_path(a); pump(0.2)
    ta = win.cur()
    win.on_action("rotate_view_cw"); pump(0.1)
    win.on_action("rotate_view_cw"); pump(0.1)
    win.on_action("rotate_view_ccw"); pump(0.1)
    assert ta.viewer.view_rot == 90
    assert ta.doc.fitz[0].rotation == 0 and not ta.doc.modified   # 파일은 안 바뀜
    win.open_path(b); pump(0.2)
    tb = win.cur()
    assert tb is not ta and tb.viewer.view_rot == 0   # 탭마다 따로
    win.on_action("rotate_view_ccw"); pump(0.1)
    assert tb.viewer.view_rot == 270 and ta.viewer.view_rot == 90
    assert tb.doc.fitz[0].rotation == 90 and not tb.doc.modified


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_rect_annot_in_rotated_view_saved_in_place(win, rot):
    path = pdf_with(0)
    win.open_path(path); pump(0.2)
    t = win.cur(); v = t.viewer
    v.set_view_rotation(rot); pump(0.1)
    win.on_tool("rect"); pump(0.02)
    want = fitz.Rect(150, 250, 300, 330)
    a = scene_of(v, 0, *view_pt(rot, want.x0, want.y0))
    b = scene_of(v, 0, *view_pt(rot, want.x1, want.y1))
    v.ensureVisible(v.from_page_rect(0, want), 10, 10); pump(0.02)

    def mouse(kind, sp, buttons):
        vp = QPointF(v.mapFromScene(sp))
        QApplication.sendEvent(v.viewport(), QMouseEvent(kind, vp, v.viewport().mapToGlobal(vp), Qt.MouseButton.LeftButton,
                                                         buttons, Qt.KeyboardModifier.NoModifier))
    mouse(QEvent.Type.MouseButtonPress, a, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseMove, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2), Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseMove, b, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseButtonRelease, b, Qt.MouseButton.NoButton)
    pump(0.1)
    assert t.doc.modified
    assert win.save(t) is True
    pump(0.1)
    with fitz.open(path) as f:
        pg = f[0]
        assert pg.rotation == 0   # 보기 회전은 파일에 안 남는다
        rects = [fitz.Rect(x.rect) for x in pg.annots() if x.type[1] == "Square"]
        assert len(rects) == 1
        got = rects[0]
        tol = 1.5 + v.zoom   # 화면 화소 반올림·테두리 굵기만큼
        for g, w_ in zip(got, want):
            assert abs(g - w_) < tol, (rot, got, want)


def test_textbox_in_rotated_view_reads_upright_on_screen(win):
    """돌린 화면에서 만든 글상자는 그 화면에 바로 선다(글 방향 = 쪽 회전 + 보기 회전)."""
    win.open_path(pdf_with(0)); pump(0.2)
    v = win.cur().viewer
    v.set_view_rotation(90); pump(0.1)
    assert v.text_rotation(0) == 90
    v.set_view_rotation(0); pump(0.1)
    assert v.text_rotation(0) == 0


def test_typewriter_click_in_rotated_view(win):
    from PySide6.QtTest import QTest
    from app.ui.inline_annot import InlineAnnot
    path = pdf_with(0)
    win.open_path(path); pump(0.2)
    t = win.cur(); v = t.viewer
    v.set_view_rotation(90); pump(0.1)
    win.on_tool("typewriter"); pump(0.02)
    sp = scene_of(v, 0, *view_pt(90, 200, 300))
    v.ensureVisible(v.from_page_rect(0, fitz.Rect(150, 250, 250, 350)), 10, 10); pump(0.02)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, v.mapFromScene(sp)); pump(0.05)
    ed = next((o for o in v.findChildren(InlineAnnot) if not o.done), None)
    assert ed is not None
    QTest.keyClicks(v, "abc"); pump(0.05)
    assert ed.text() == "abc"
    ed.finish(); pump(0.1)
    pg = t.doc.fitz[0]   # 주석은 쪽 객체를 약하게만 잡는다
    annots = [a for a in pg.annots() if a.type[1] == "FreeText"]
    assert len(annots) == 1
    a = annots[0]
    assert a.rotation == 90   # 돌린 화면에 바로 서게
    r = fitz.Rect(a.rect)
    # 누른 자리(200, 300)가 상자 안쪽 가장자리 근처(돌린 화면의 왼쪽 위 = 쪽의 왼쪽 아래 쪽)
    assert r.x0 - 25 < 200 < r.x1 + 25 and r.y0 - 25 < 300 < r.y1 + 25, r
    assert t.doc.fitz[0].rotation == 0
