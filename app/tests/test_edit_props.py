"""편집 속성 패널 시험: 그림 돌리기·뒤집기·불투명도·크기·옮기기, 글자 덩어리 모양 바꾸기, 끌어 옮기기.

python -m pytest app/tests/test_edit_props.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_props_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _pix() -> fitz.Pixmap:
    """100x50 그림: 왼쪽 위 네모만 파랑, 나머지 빨강."""
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 50), False)
    for y in range(50):
        for x in range(100):
            pm.set_pixel(x, y, (0, 0, 255) if x < 50 and y < 25 else (255, 0, 0))
    return pm


def _make(rotation: int = 0, text: bool = True) -> str:
    p = os.path.join(tempfile.mkdtemp(), "속성시험.pdf")
    d = fitz.open(); pg = d.new_page()
    if text:
        pg.insert_text((72, 100), "hello block", fontsize=14)
    pg.insert_image(fitz.Rect(300, 300, 400, 350), pixmap=_pix())
    pg.set_rotation(rotation)
    d.save(p); d.close()
    return p


def _img(doc: Document) -> dict:
    return A.images_on_page(doc, 0)[0]


def _color_at(page: fitz.Page, pt: fitz.Point) -> tuple:
    """보이는 쪽 좌표 한 점의 색(그린 것 기준)."""
    pm = page.get_pixmap(clip=fitz.Rect(pt.x - 1, pt.y - 1, pt.x + 1, pt.y + 1) * page.derotation_matrix)
    return pm.pixel(0, 0)


def _reopen(doc: Document) -> fitz.Document:
    out = os.path.join(tempfile.mkdtemp(), "다시.pdf")
    doc.fitz.save(out, garbage=3, deflate=True)
    return fitz.open(out)


def _near(a, b, tol=0.1) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ── 엔진 ────────────────────────────────────────────────────────
def test_rotate_clockwise_and_keep_center():
    doc = Document(_make())
    im = _img(doc)
    r = A.transform_image(doc, 0, im["xref"], im["rect"], rotate=90)
    assert _near(r, (325, 275, 375, 375)) and _near(_img(doc)["rect"], r)
    # 파랑(그림 왼쪽 위)이 오른쪽으로 돌아 오른쪽 위로
    page = doc.fitz[0]
    assert _color_at(page, fitz.Point(362, 290))[2] > 200
    assert _color_at(page, fitz.Point(338, 290))[0] > 200


def test_flip_and_opacity_survive_save():
    doc = Document(_make())
    im = _img(doc)
    r = A.transform_image(doc, 0, im["xref"], im["rect"], flip_h=True, opacity=0.4)
    assert _near(r, (300, 300, 400, 350))
    assert A.image_opacity(doc, 0, im["xref"], r) == pytest.approx(0.4)
    g = _reopen(doc)
    info = g[0].get_image_info(xrefs=True)[0]
    assert _near(info["bbox"], (300, 300, 400, 350))
    assert info["transform"][0] < 0                  # 좌우가 뒤집힘
    c = g[0].get_pixmap(clip=fitz.Rect(374, 305, 376, 307)).pixel(0, 0)   # 파랑이 오른쪽 위로, 흰 바탕과 섞여 옅게
    assert c[2] == 255 and 140 < c[0] < 170
    assert g.xref_get_key(g[0].xref, "Resources/ExtGState/RPDFA40")[0] != "null"


def test_move_resize_and_twice():
    doc = Document(_make())
    im = _img(doc)
    r = A.transform_image(doc, 0, im["xref"], im["rect"], new_rect=fitz.Rect(10, 10, 210, 110))
    assert _near(r, (10, 10, 210, 110)) and _near(_img(doc)["rect"], r)
    r = A.transform_image(doc, 0, im["xref"], r, rotate=-90, opacity=0.5)
    r = A.transform_image(doc, 0, im["xref"], r, new_rect=fitz.Rect(50, 60, 100, 160))
    assert _near(_img(doc)["rect"], (50, 60, 100, 160))
    assert A.image_opacity(doc, 0, im["xref"], r) == pytest.approx(0.5)
    g = _reopen(doc)
    assert _near(g[0].get_image_info()[0]["bbox"], (50, 60, 100, 160))
    # 실행 취소 세 번이면 처음 자리
    for _ in range(3):
        assert doc.undo()
    assert _near(_img(doc)["rect"], (300, 300, 400, 350))


def test_rotated_page_flip_is_visual():
    doc = Document(_make(rotation=90, text=False))
    im = _img(doc)
    page = doc.fitz[0]
    vis = fitz.Rect(im["rect"]) * page.rotation_matrix
    vis.normalize()
    before = _color_at(page, fitz.Point(vis.x1 - 3, vis.y0 + 3))
    A.transform_image(doc, 0, im["xref"], im["rect"], flip_h=True)
    page = doc.fitz[0]
    after = _color_at(page, fitz.Point(vis.x0 + 3, vis.y0 + 3))
    assert before == after                           # 보이는 화면에서 오른쪽 위 색이 왼쪽 위로


def test_edit_block_style_and_place():
    doc = Document(_make())
    blk = next(b for b in doc.text_blocks(0) if b["type"] == 0)
    r = fitz.Rect(blk["bbox"])
    st = A.block_style(doc, 0, r)
    assert st["fontsize"] == pytest.approx(14) and not st["bold"]
    A.edit_block(doc, 0, r, "hello block", 20, (1, 0, 0), True, 0, italic=True, new_rect=fitz.Rect(72, 400, 300, 440))
    spans = [s for b in doc.fitz[0].get_text("dict")["blocks"] if b["type"] == 0 for l in b["lines"] for s in l["spans"]]
    assert len(spans) == 1 and spans[0]["size"] == pytest.approx(20) and spans[0]["bbox"][1] >= 395
    assert spans[0]["color"] == 0xFF0000
    doc.undo()
    assert "hello block" in doc.fitz[0].get_text()
    assert A.block_style(doc, 0, r)["fontsize"] == pytest.approx(14)


# ── 화면 ────────────────────────────────────────────────────────
@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


def _mouse(v, kind, page_pt: fitz.Point, page: int = 0):
    sp = v.from_page_point(page, page_pt)
    vp = QPointF(v.mapFromScene(sp))
    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove, "release": QEvent.Type.MouseButtonRelease}[kind]
    held = Qt.MouseButton.LeftButton if kind != "release" else Qt.MouseButton.NoButton
    e = QMouseEvent(t, vp, QPointF(v.viewport().mapToGlobal(vp.toPoint())),
                    Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton, held,
                    Qt.KeyboardModifier.NoModifier)
    getattr(v, {"press": "mousePressEvent", "move": "mouseMoveEvent", "release": "mouseReleaseEvent"}[kind])(e)
    pump(0.02)


def _edit(win, path):
    win.open_path(path); pump(0.2)
    t = win.cur()
    win.on_toggle("edit_all", True); pump(0.02)
    return t


def test_panel_shows_text_fields_and_applies(win):
    t = _edit(win, _make()); v = t.viewer; cp = t.props.edit_props
    _mouse(v, "press", fitz.Point(80, 95)); _mouse(v, "release", fitz.Point(80, 95))
    assert v.sel.kind == "block" and not cp.isHidden() and not cp.text_box.isHidden() and cp.image_box.isHidden()
    assert cp.size.value() == pytest.approx(14)
    cp.size.setValue(24); cp.bold.setChecked(True)
    pump(0.45)
    sizes = {round(s["size"]) for b in t.doc.fitz[0].get_text("dict")["blocks"] if b["type"] == 0
             for l in b["lines"] for s in l["spans"]}
    assert sizes == {24}
    win.on_action("undo"); pump(0.05)
    sizes = {round(s["size"]) for b in t.doc.fitz[0].get_text("dict")["blocks"] if b["type"] == 0
             for l in b["lines"] for s in l["spans"]}
    assert sizes == {14}


def test_panel_image_buttons(win):
    t = _edit(win, _make()); v = t.viewer; cp = t.props.edit_props
    _mouse(v, "press", fitz.Point(350, 330)); _mouse(v, "release", fitz.Point(350, 330))
    assert v.sel.kind == "image" and not cp.image_box.isHidden() and cp.text_box.isHidden()
    assert cp.opacity.value() == 100
    cp.rot_r.click(); pump(0.05)
    assert _near(_img(t.doc)["rect"], (325, 275, 375, 375)) and _near(v.sel.rect, (325, 275, 375, 375))
    cp.opacity.setValue(50); pump(0.05)
    assert A.image_opacity(t.doc, 0, v.sel.extra["xref"], v.sel.rect) == pytest.approx(0.5)
    cp._size_done(); assert len(t.doc._undo) == 2      # 안 고쳤으면 그대로
    cp.w.set_pt(100); cp.h.set_pt(200); cp._size_done(); pump(0.05)
    assert _near(_img(t.doc)["rect"], (325, 275, 425, 475), tol=0.3)
    cp.center_both.click(); pump(0.05)
    pr = t.doc.fitz[0].rect
    r = _img(t.doc)["rect"]
    assert _near(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2), (pr.width / 2, pr.height / 2))


def test_drag_moves_and_corner_resizes_image(win):
    t = _edit(win, _make()); v = t.viewer
    _mouse(v, "press", fitz.Point(350, 330)); _mouse(v, "move", fitz.Point(360, 340)); _mouse(v, "move", fitz.Point(370, 350))
    _mouse(v, "release", fitz.Point(370, 350))
    assert _near(_img(t.doc)["rect"], (320, 320, 420, 370), tol=0.6)
    _mouse(v, "press", fitz.Point(420, 370)); _mouse(v, "move", fitz.Point(470, 400)); _mouse(v, "release", fitz.Point(470, 400))
    assert _near(_img(t.doc)["rect"], (320, 320, 470, 400), tol=0.6)
    g = _reopen(t.doc)
    assert _near(g[0].get_image_info()[0]["bbox"], (320, 320, 470, 400), tol=0.6)


def test_drag_moves_text_block(win):
    t = _edit(win, _make()); v = t.viewer
    _mouse(v, "press", fitz.Point(80, 95)); _mouse(v, "move", fitz.Point(80, 200)); _mouse(v, "move", fitz.Point(80, 295))
    _mouse(v, "release", fitz.Point(80, 295))
    blk = [b for b in t.doc.fitz[0].get_text("dict")["blocks"] if b["type"] == 0]
    assert len(blk) == 1 and blk[0]["bbox"][1] > 250
    assert "hello block" in t.doc.fitz[0].get_text()
