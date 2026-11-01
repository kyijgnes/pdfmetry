"""편집 켜기·대상 고르기·이미지 추가 시험(창을 offscreen 으로 띄움).

python -m pytest app/tests/test_edit_mode.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_edit_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


def _png(w: int, h: int) -> str:
    p = os.path.join(tempfile.mkdtemp(), "그림.png")
    im = QImage(w, h, QImage.Format.Format_RGB32); im.fill(QColor(200, 30, 30)); im.save(p)
    return p


@pytest.fixture()
def pdf():
    """글 한 덩어리와 그림 하나가 있는 한 쪽짜리."""
    p = os.path.join(tempfile.mkdtemp(), "편집시험.pdf")
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((72, 100), "hello block", fontsize=14)
    pg.insert_image(fitz.Rect(300, 300, 400, 380), filename=_png(100, 80))
    d.save(p); d.close()
    return p


def _mouse(v, kind, page_pt: fitz.Point, page: int = 0):
    sp = v.from_page_point(page, page_pt)
    vp = QPointF(v.mapFromScene(sp))
    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove, "release": QEvent.Type.MouseButtonRelease}[kind]
    btn = Qt.MouseButton.NoButton if kind in ("move", "release") else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.LeftButton if kind == "press" else Qt.MouseButton.NoButton
    e = QMouseEvent(t, vp, QPointF(v.viewport().mapToGlobal(vp.toPoint())), Qt.MouseButton.LeftButton if kind != "move" else btn,
                    held, Qt.KeyboardModifier.NoModifier)
    getattr(v, {"press": "mousePressEvent", "move": "mouseMoveEvent", "release": "mouseReleaseEvent"}[kind])(e)
    pump(0.02)


def _open(win, pdf):
    win.open_path(pdf); pump(0.2)
    return win.cur()


def test_edit_button_is_split_with_target_menu(win, pdf):
    _open(win, pdf)
    b = win.ribbon.buttons["edit_all"][-1]
    assert b.popupMode() == b.ToolButtonPopupMode.MenuButtonPopup
    names = [a.text() for a in b.menu().actions()]
    assert names == ["모두", "텍스트", "이미지", "링크"]
    assert b.text() == "모두 편집"
    win.on_action("edit_target_image"); pump(0.02)
    assert b.text() == "이미지 편집" and win.cur().viewer.edit_mode and win.cur().viewer.edit_target == "image"
    assert [a.isChecked() for a in b.menu().actions()] == [False, False, True, False]


def test_off_returns_to_previous_tool(win, pdf):
    t = _open(win, pdf); v = t.viewer
    win.on_tool("note"); assert v.tool == "note"
    win.on_toggle("edit_all", True); assert v.edit_mode and v.tool == "edit"
    win.on_toggle("edit_all", False)
    assert not v.edit_mode and v.tool == "note"


def test_target_filters_blocks(win, pdf):
    t = _open(win, pdf); v = t.viewer
    win.on_action("edit_target_text"); pump(0.02)
    assert {b["type"] for b in v._edit_blocks(0)} == {0}
    assert not v._select_block_or_image(0, v.from_page_point(0, fitz.Point(350, 340)))
    assert v._select_block_or_image(0, v.from_page_point(0, fitz.Point(80, 95))) and v.sel.kind == "block"
    win.on_action("edit_target_image")
    assert {b["type"] for b in v._edit_blocks(0)} == {1}
    assert not v._select_block_or_image(0, v.from_page_point(0, fitz.Point(80, 95)))
    assert v._select_block_or_image(0, v.from_page_point(0, fitz.Point(350, 340))) and v.sel.kind == "image"
    win.on_action("edit_target_all")
    assert {b["type"] for b in v._edit_blocks(0)} == {0, 1}


def test_link_target_enables_link_edit(win, pdf):
    t = _open(win, pdf); v = t.viewer
    assert not v._links_editable()
    win.on_action("edit_target_link"); pump(0.02)
    assert v._links_editable() and v._edit_blocks(0) == []
    win.on_action("edit_target_text")
    assert not v._links_editable()


def test_addtext_and_addimage_turn_on_edit(win, pdf, monkeypatch):
    t = _open(win, pdf); v = t.viewer
    win.on_action("edit_target_link")
    win.on_tool("addtext")
    assert v.edit_mode and v.edit_target == "all" and v.tool == "addtext"
    win.on_action("edit_target_text")
    p = _png(50, 50)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (p, ""))
    win.on_tool("addimage")
    assert v.edit_mode and v.edit_target == "all" and v.tool == "addimage"
    assert win.ribbon.buttons["edit_all"][-1].text() == "모두 편집"


def test_unreadable_image_warns(win, pdf, monkeypatch):
    t = _open(win, pdf); v = t.viewer
    bad = os.path.join(tempfile.mkdtemp(), "나쁜.png"); open(bad, "wb").write(b"not an image")
    shown = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (bad, ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    win.on_tool("addimage")
    assert shown and shown[0].startswith("읽을 수 없는 사진 파일입니다")
    assert v.tool != "addimage"


def test_click_places_image_and_undo(win, pdf, monkeypatch):
    t = _open(win, pdf); v = t.viewer; d = t.doc
    p = _png(80, 40)   # 80x40 px = 60x30 pt
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (p, ""))
    n0 = len(A.images_on_page(d, 0))
    win.on_tool("addimage"); pump(0.05)
    _mouse(v, "move", fitz.Point(200, 500))
    assert v._img_hover is not None
    _mouse(v, "press", fitz.Point(200, 500)); _mouse(v, "release", fitz.Point(200, 500)); pump(0.1)
    ims = A.images_on_page(d, 0)
    assert len(ims) == n0 + 1
    r = max(ims, key=lambda im: im["rect"].y0)["rect"]
    assert abs(r.width - 60) < 1 and abs(r.height - 30) < 1
    assert abs((r.x0 + r.x1) / 2 - 200) < 1 and abs((r.y0 + r.y1) / 2 - 500) < 1
    assert v.tool == "edit" and v.edit_mode
    assert d.undo()
    assert len(A.images_on_page(d, 0)) == n0


def test_big_image_shrinks_to_page(win, pdf, monkeypatch):
    t = _open(win, pdf); v = t.viewer; d = t.doc
    p = _png(2000, 1000)   # 1500x750 pt, 쪽(595x842)보다 크다
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (p, ""))
    win.on_tool("addimage"); pump(0.05)
    _mouse(v, "press", fitz.Point(20, 20)); _mouse(v, "release", fitz.Point(20, 20)); pump(0.1)
    r = max(A.images_on_page(d, 0), key=lambda im: im["rect"].width)["rect"]
    pr = d.fitz[0].rect
    assert r.x0 >= -0.5 and r.x1 <= pr.x1 + 0.5 and r.y0 >= -0.5 and r.y1 <= pr.y1 + 0.5
    assert abs(r.width / r.height - 2) < 0.05


def test_drop_image_menu_adds_image_tool(win, pdf, monkeypatch):
    t = _open(win, pdf); v = t.viewer
    p = _png(30, 30)
    win._drop_image = p
    win.on_tool("addimage")
    assert v.tool == "addimage" and v.props["image_path"] == p and win._drop_image == ""
