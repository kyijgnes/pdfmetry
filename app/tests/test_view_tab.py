"""보기 탭 시험(offscreen): 확대 단계 · 배율 칸 · 쪽 칸 되돌림 · 전체 화면 · 바둑판 배열 · 표지 따로 · 배경 · 격자 · 눈금자 · ←→.

python -m pytest app/tests/test_view_tab.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_viewtab_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QValidator  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.ui import shortcuts as K  # noqa: E402
from app.ui import tab_detach  # noqa: E402
from app.ui import tile as tiler  # noqa: E402
from app.ui import viewer as V  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 6):
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"page {i + 1}", fontsize=14)
    d.save(path); d.close()


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for x in list(tab_detach.windows):
        for t in list(x.tabs):
            t.doc.modified = False
        if x is not w:
            x.close()
    w.close(); pump(0.05)


@pytest.fixture()
def pdf():
    p = os.path.join(tempfile.mkdtemp(), "보기탭.pdf"); make_pdf(p)
    return p


def test_zoom_steps_5_to_1600(win, pdf):
    win.open_path(pdf); pump(0.2)
    v = win.cur().viewer
    assert V.PageView.STEPS[0] == 0.05 and V.PageView.STEPS[-1] == 16.0
    v.zoom_mode = "fixed"; v.set_zoom(15.0); v.zoom_in(); assert v.zoom == 16.0
    v.zoom_in(); assert v.zoom == 16.0
    v.set_zoom(0.06); v.zoom_out(); assert v.zoom == 0.05
    v.set_zoom(100); assert v.zoom == 16.0
    v.set_zoom(1.0); v.zoom_in(); assert v.zoom == 1.25
    # 아주 크게 확대하면 그림은 상한까지만 그리고 늘려 보인다
    v.set_zoom(16.0)
    r = v.page_rect_pt(0)
    rz = v.render_zoom(0)
    assert rz < 16.0 and r.width * r.height * rz * rz <= V.RENDER_MAX_PX * 1.001


def test_zoom_box_steps_and_filter(win, pdf):
    win.open_path(pdf); pump(0.2)
    box = win.zoom_box
    items = [box.itemText(i) for i in range(box.count())]
    # 배율 칸 목록에는 배율만(맞춤은 상태줄 맞춤 방식 펼침), 슬라이더 눈금은 단계표 차례
    assert items[0] == "5%" and "33.3%" in items and "1600%" in items and "창 너비" not in items
    assert win.zoom_slider.minimum() == 0 and win.zoom_slider.maximum() == len(V.PageView.STEPS) - 1
    val = box.validator()
    assert val.validate("12a", 3)[0] == QValidator.State.Invalid
    assert val.validate("250%", 4)[0] == QValidator.State.Acceptable
    box.setEditText("250%"); win._zoom_box()
    assert abs(win.cur().viewer.zoom - 2.5) < 1e-6
    box.setEditText("99999"); win._zoom_box()
    assert win.cur().viewer.zoom == 16.0 and box.currentText() == "1600%"
    box.setEditText("창 크기"); win._zoom_box()
    assert win.cur().viewer.zoom_mode == "fit_page"


def test_page_box_reverts_on_bad_input(win, pdf):
    win.open_path(pdf); pump(0.2)
    v = win.cur().viewer
    v.goto(2); pump(0.05)
    win.page_edit.setText("99"); win._goto_entered()
    assert v.current == 2 and win.page_edit.text() == "3 / 6"   # 쪽 칸 하나에 「현재 / 전체」
    win.page_edit.setText("가나"); win._goto_entered()
    assert win.page_edit.text() == "3 / 6"
    win.page_edit.setText("5"); win._goto_entered()
    assert v.current == 4


def test_fullscreen_only_page_and_esc(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur()
    win.on_action("fullscreen"); pump(0.1)
    fs = win._fullscreen
    assert fs.active
    for w in (win.ribbon, win.status, win.tabrow, win.navstrip, win.rightstrip):
        assert not w.isVisible()
    assert t.viewer.isVisible() and fs.hint.isVisible()
    assert win.view_checked("fullscreen") is True
    fs._esc.activated.emit(); pump(0.05)   # Esc
    assert not fs.active
    for w in (win.ribbon, win.status, win.tabrow, win.navstrip, win.rightstrip):
        assert w.isVisible()
    win.on_action("fullscreen"); win.on_action("fullscreen")   # F11 두 번
    assert not fs.active and win.ribbon.isVisible()


def test_tile_horizontal_three_windows_keep_edits(win, pdf):
    paths = [pdf]
    for name in ("둘.pdf", "셋.pdf"):
        p = os.path.join(os.path.dirname(pdf), name); make_pdf(p, 3); paths.append(p)
    for p in paths:
        win.open_path(p)
    pump(0.2)
    t0 = win.tabs[0]
    A.rect(t0.doc, 0, fitz.Rect(50, 50, 150, 150), (1, 0, 0), None, 1, 1.0, "")
    t0.doc.modified = True
    assert tiler.doc_count() == 3
    wins = tiler.tile(win, horizontal=True); pump(0.1)
    assert len(wins) == 3 and all(len(w.tabs) == 1 for w in wins)
    docs = [w.tabs[0] for w in wins]
    assert t0 in docs and t0.doc.modified and len(list(t0.doc.fitz[0].annots())) == 1
    pos = {(w.frameGeometry().x(), w.frameGeometry().y()) for w in wins}
    assert len(pos) == 3 and len({y for _, y in pos}) >= 2   # 위아래로 둘 이상(화면이 낮으면 앞줄에 하나 더)


def test_tile_plan_shapes():
    area = QRect(0, 0, 1600, 900)
    assert tiler.plan_shape(3, area, True) == (3, 1)    # 가로: 세 줄
    assert tiler.plan_shape(3, area, False) == (3, 1)   # 세로: 세 칸
    # 줄 높이가 최소보다 작아지면 줄 수를 줄이고 앞줄부터 하나씩 더 얹는다
    assert tiler.plan_shape(5, area, True, min_h=300) == (3, 2)
    spots = tiler.plan(5, area, True, min_h=300)
    assert [r.y() for r in spots] == [0, 0, 300, 300, 600]
    assert tiler.plan_shape(2, QRect(0, 0, 500, 200), True, min_h=300) == (2, 1)   # 둘 이상이면 줄은 둘 이상


def test_tile_enabled_by_doc_count(win, pdf):
    win.open_path(pdf); pump(0.2)
    assert not win.ribbon.buttons["tile"][0].isEnabled()
    p = os.path.join(os.path.dirname(pdf), "둘.pdf"); make_pdf(p, 2); win.open_path(p); pump(0.2)
    assert win.ribbon.buttons["tile"][0].isEnabled()


def test_cover_separate_shifts_pairs(win, pdf):
    win.open_path(pdf); pump(0.2)
    v = win.cur().viewer
    v.set_layout_mode("facing_continuous"); pump(0.05)
    y = lambda i: v.items[i].pos().y()   # noqa: E731
    assert y(0) == y(1) and y(2) == y(3)
    win.on_action("layout_cover"); pump(0.05)
    assert v.cover_separate and win.view_checked("layout_cover")
    assert y(0) < y(1) and y(1) == y(2) and y(3) == y(4)
    v.set_layout_mode("facing"); v.goto(0); pump(0.05)
    assert v.visible_pages_for_layout() == [0]
    v.next_page(); assert v.visible_pages_for_layout() == [1, 2]
    v.next_page(); assert v.current == 3
    v.prev_page(); assert v.current == 1
    v.prev_page(); assert v.current == 0


def test_background_modes_remembered(win, pdf):
    win.open_path(pdf); pump(0.3)
    v = win.cur().viewer
    win.on_action("canvas_eye"); pump(0.3)
    assert v.bg_mode == "eye" and win.prefs["view_bg"] == "eye" and win.view_checked("canvas_eye")
    assert v.page_color() != QColor(255, 255, 255)
    img = QImage(4, 4, QImage.Format.Format_RGB888); img.fill(QColor(255, 255, 255))
    assert V.tint_image(img, "eye").pixelColor(0, 0) == QColor(*V.BG_MODES["eye"][1])
    night = V.tint_image(img, "night").pixelColor(0, 0)
    assert night.red() < 30   # 흰 종이가 어둡게
    win.on_action("canvas_white")   # 옛 이름도 받는다
    assert v.bg_mode == "day"
    win.on_action("canvas_parchment")
    # 다시 켠 창도 같은 배경
    # 같은 파일은 이미 연 창으로 가므로 사본을 연다
    import shutil
    pdf2 = str(pdf).replace(".pdf", "_2.pdf"); shutil.copy(pdf, pdf2)
    w2 = MainWindow(); w2.show(); w2.open_path(pdf2); pump(0.3)
    try:
        assert w2.prefs.get("view_bg") == "parchment" and w2.cur().viewer.bg_mode == "parchment"
    finally:
        for t in w2.tabs: t.doc.modified = False
        w2.close()


def test_grid_spec_from_prefs():
    g = V.grid_spec(None)
    assert abs(g["w"] - 72 / 2.54) < 1e-6 and g["sub"] == 3 and g["color"] == QColor("#FF0000")
    g = V.grid_spec({"unit": "mm", "grid": {"w": 14.0, "h": "1in", "left": "2", "top": 0, "sub": 2, "color": "#00FF00"}})
    assert abs(g["w"] - 14.0) < 1e-6 and abs(g["h"] - 72) < 1e-6 and abs(g["left"] - 2 * 72 / 25.4) < 1e-6   # 숫자는 pt, 글자는 unit
    assert g["sub"] == 2 and g["color"] == QColor("#00FF00")
    assert V.grid_spec({"grid": {"color": [0, 0, 255]}})["color"] == QColor(0, 0, 255)


def test_grid_and_ruler_draw(win, pdf):
    win.prefs["grid"] = {"w": 72 / 2.54, "h": 72 / 2.54, "left": 0, "top": 0, "sub": 1, "color": "#0000FF"}
    win.open_path(pdf); pump(0.3)
    v = win.cur().viewer
    v.zoom_mode = "fixed"; v.set_zoom(1.0); v.goto(0); pump(0.3)
    win.on_action("toggle_grid"); pump(0.1)
    assert v.show_grid and win.view_checked("toggle_grid")
    img = v.viewport().grab().toImage()
    it = v.items[0]
    o = v.mapFromScene(it.pos())
    step = 72 / 2.54
    x = o.x() + round(step * 3)   # 3cm 세로선
    blues = sum(1 for dx in (-1, 0, 1) for yy in range(o.y() + 200, o.y() + 260)
                if (c := img.pixelColor(x + dx, yy)).blue() > c.red() + 40)
    assert blues > 20
    win.on_action("toggle_ruler"); pump(0.1)
    assert v.show_ruler and all(r.isVisible() for r in v._rulers)
    assert v.viewportMargins().left() == V.RULER
    win.on_action("toggle_ruler"); pump(0.05)
    assert not v.show_ruler and v.viewportMargins().left() == 0


def test_left_right_flip_in_continuous(win, pdf):
    win.open_path(pdf); pump(0.3)
    v = win.cur().viewer
    v.set_layout_mode("continuous"); v.set_zoom_mode("fit_page"); pump(0.2); v.goto(0); pump(0.05)
    assert v.horizontalScrollBar().maximum() <= 0
    v.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
    assert v.current == 1
    v.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier))
    assert v.current == 0


def test_zoom_area_tool(win, pdf):
    win.open_path(pdf); pump(0.3)
    v = win.cur().viewer
    v.zoom_mode = "fixed"; v.set_zoom(1.0); pump(0.05)
    win.on_action("zoom_area")
    assert v.tool == "zoomarea"
    r = v.items[0].sceneBoundingRect()
    from PySide6.QtCore import QRectF
    v.zoom_to_rect(QRectF(r.left() + 50, r.top() + 50, 100, 80))
    assert v.zoom > 4 and v.zoom_mode == "fixed"


def test_view_menu_checks_and_keys(win, pdf):
    win.open_path(pdf); pump(0.2)
    v = win.cur().viewer
    v.set_zoom_mode("fit_width")
    assert win.view_checked("zoom_width") and not win.view_checked("zoom_page")
    assert win.view_checked("layout_continuous") and win.view_checked("canvas_default") in (True, False)
    assert win.view_checked("auto_bookmark") is None
    keys = K.current(None)
    assert keys["maximize"] == ["Ctrl+F10"] and keys["batch_convert"] == ["Ctrl+F3"]
    assert not K.clashes(keys)
    labels = [a.text() for b in win.ribbon.buttons["zoom_menu"] for a in b.menu().actions()]
    assert "전체 화면" in labels and "영역 확대" in labels
