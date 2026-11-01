"""링크 화면 시험(창을 offscreen 으로 띄움): 링크 창 칸·적용 켜짐·최근 URL, 링크 도구로 고르기·옮기기·크기·고치기·지우기와 되돌리기,
손 도구로 올리면 상태줄 안내, 쪽 이동 링크의 자리·배율.

python -m pytest app/tests/test_link_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_link_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.ui import dlg_link  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

R = fitz.Rect(72, 72, 220, 110)


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


@pytest.fixture()
def pdf():
    p = os.path.join(tempfile.mkdtemp(), "링크시험.pdf")
    d = fitz.open()
    for _ in range(5):
        d.new_page()
    d.save(p); d.close()
    return p


def _mouse(v, kind, page_pt: fitz.Point, page: int = 0, buttons=Qt.MouseButton.LeftButton):
    """쪽 좌표 자리에 마우스 일을 바로 보낸다."""
    sp = v.from_page_point(page, page_pt)
    vp = QPointF(v.mapFromScene(sp))
    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease, "double": QEvent.Type.MouseButtonDblClick}[kind]
    btn = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    e = QMouseEvent(t, vp, QPointF(v.viewport().mapToGlobal(vp.toPoint())), btn, buttons, Qt.KeyboardModifier.NoModifier)
    getattr(v, {"press": "mousePressEvent", "move": "mouseMoveEvent", "release": "mouseReleaseEvent",
                "double": "mouseDoubleClickEvent"}[kind])(e)
    pump(0.02)


# ── 링크 창 ───────────────────────────────────────────────────────
def test_dialog_defaults_and_apply_enabled(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur()
    prefs = {"link_recent_urls": ["https://b.example", "https://a.example"]}
    dlg = dlg_link.LinkDialog(win, t.doc, 2, prefs)
    assert dlg.r_goto.isChecked() and dlg.page.text() == "3"   # 지금 보는 쪽
    assert dlg.ok.isEnabled()
    assert not dlg.url.isEnabled() and not dlg.file.isEnabled()
    dlg.r_web.setChecked(True)
    assert dlg.url.isEnabled() and not dlg.page.isEnabled()
    assert not dlg.ok.isEnabled()   # 웹 칸이 비었다
    assert [dlg.url.itemText(i) for i in range(dlg.url.count())] == prefs["link_recent_urls"]
    dlg.url.setCurrentText("https://c.example")
    assert dlg.ok.isEnabled()
    dlg.visible.setChecked(False)
    assert not dlg.width_.isEnabled() and not dlg.line.isEnabled() and dlg.link_type.currentIndex() == 1
    dlg.link_type.setCurrentIndex(0)
    assert dlg.visible.isChecked()
    st = dlg.style()
    assert st["visible"] and st["color"] == (0.5, 0.5, 0.5) and st["width"] == 1 and st["highlight"] == "invert"
    dlg.remember()
    assert prefs["link_recent_urls"][0] == "https://c.example" and prefs["link_style"]["width"] == 1


def test_recent_urls_keep_ten_newest_first():
    prefs: dict = {}
    for n in range(12):
        dlg_link.remember_url(prefs, f"https://{n}.example")
    dlg_link.remember_url(prefs, "https://5.example")
    rec = prefs["link_recent_urls"]
    assert len(rec) == 10 and rec[0] == "https://5.example" and rec.count("https://5.example") == 1
    assert rec[1] == "https://11.example"


def test_page_out_of_range_warns(win, pdf, monkeypatch):
    win.open_path(pdf); pump(0.2)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = dlg_link.LinkDialog(win, win.cur().doc, 0, {})
    dlg.page.setText("0")
    dlg.accept()
    assert shown and dlg.result() == 0


def test_file_target_relative(win, pdf):
    win.open_path(pdf); pump(0.2)
    dlg = dlg_link.LinkDialog(win, win.cur().doc, 0, {"link_relative": True})
    dlg.r_file.setChecked(True)
    assert dlg.relative.isEnabled() and dlg.relative.isChecked()
    dlg.file.setText(os.path.join(os.path.dirname(pdf), "부록", "표.xlsx"))
    assert dlg.target() == {"file": "부록/표.xlsx"}


# ── 창 흐름 ───────────────────────────────────────────────────────
def _fake_exec(setup):
    def run(self):
        setup(self)
        return 1
    return run


def test_add_link_through_window_remembers_style(win, pdf, monkeypatch):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer

    def setup(dlg):
        dlg.r_web.setChecked(True); dlg.url.setCurrentText("https://new.example")
        dlg.color.color = (0, 0, 1); dlg.width_.setValue(2); dlg.line.setCurrentIndex(1); dlg.highlight.setCurrentIndex(3)
    monkeypatch.setattr(dlg_link.LinkDialog, "exec", _fake_exec(setup))
    win.on_request(t, "link", {"page": 0, "rect": R}); pump(0.05)
    ls = A.links(t.doc, 0)
    assert len(ls) == 1 and ls[0]["uri"] == "https://new.example"
    st = A.link_style(t.doc, ls[0]["xref"])
    assert st == {"visible": True, "color": (0, 0, 1), "width": 2, "line": "dash", "highlight": "outline"}
    assert win.prefs["link_recent_urls"][0] == "https://new.example"
    assert win.prefs["link_style"]["color"] == [0, 0, 1]
    assert v.tool == "select"


def test_goto_link_stores_view_and_follows(win, pdf, monkeypatch):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer
    v.zoom_mode = "fixed"; v.set_zoom(1.25); pump(0.05)
    v.goto(3, remember=False); pump(0.05)
    v.verticalScrollBar().setValue(v.verticalScrollBar().value() + 100); pump(0.05)
    pt, zoom = v.link_dest_here()
    assert zoom == pytest.approx(1.25) and pt.y > 0

    def setup(dlg):
        assert dlg.page.text() == "4"
    monkeypatch.setattr(dlg_link.LinkDialog, "exec", _fake_exec(setup))
    win.on_request(t, "link", {"page": 0, "rect": R}); pump(0.05)
    info = A.link_info(t.doc, 0, A.links(t.doc, 0)[0]["xref"])
    assert info["to_page"] == 3 and info["zoom"] == pytest.approx(1.25) and abs(info["to_point"].y - pt.y) < 1
    # 다른 배율·자리에서 따라가면 담아 둔 배율·자리로 돌아온다
    v.set_zoom(1.0); v.goto(0, remember=False); pump(0.05)
    v.goto_link(info); pump(0.05)
    assert v.current == 3 and v.zoom == pytest.approx(1.25) and abs(v.view_pos()[1] - pt.y) < 2
    v.back(); pump(0.05)
    assert v.current == 0


# ── 고르기·옮기기·고치기·지우기 ───────────────────────────────────
def test_select_move_resize_edit_delete_undo(win, pdf, monkeypatch):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer; d = t.doc
    A.add_link(d, 0, R, uri="https://a.example"); t.changed([0]); pump(0.05)
    win.on_tool("link"); pump(0.02)
    # 누르고 끌면 옮겨진다
    _mouse(v, "press", fitz.Point(100, 90))
    assert v.sel and v.sel.kind == "link"
    _mouse(v, "move", fitz.Point(130, 150))
    _mouse(v, "release", fitz.Point(130, 150), buttons=Qt.MouseButton.NoButton)
    r = A.links(d, 0)[0]["from"]
    assert abs(r.x0 - 102) < 1.5 and abs(r.y0 - 132) < 1.5
    assert A.links(d, 0)[0]["uri"] == "https://a.example"
    # 오른쪽 아래 모서리를 끌면 크기가 바뀐다
    _mouse(v, "press", r.br)
    _mouse(v, "move", r.br + (40, 20))
    _mouse(v, "release", r.br + (40, 20), buttons=Qt.MouseButton.NoButton)
    r2 = A.links(d, 0)[0]["from"]
    assert abs(r2.x1 - (r.x1 + 40)) < 1.5 and abs(r2.y1 - (r.y1 + 20)) < 1.5 and abs(r2.x0 - r.x0) < 0.5
    # 두 번 누르면 같은 창으로 고친다
    asked = []
    v.requestDialog.connect(lambda k, data: asked.append(k))

    def setup(dlg):
        assert dlg.r_web.isChecked() and dlg.url.currentText() == "https://a.example"
        dlg.r_goto.setChecked(True); dlg.page.setText("2")
    monkeypatch.setattr(dlg_link.LinkDialog, "exec", _fake_exec(setup))
    _mouse(v, "double", r2.tl + (5, 5))
    assert "edit_link" in asked
    pump(0.05)
    info = A.link_info(d, 0, A.links(d, 0)[0]["xref"])
    assert info["kind"] == "goto" and info["to_page"] == 1
    # Delete 로 지우고, 되돌리기를 거듭하면 처음으로
    _mouse(v, "press", r2.tl + (5, 5)); _mouse(v, "release", r2.tl + (5, 5), buttons=Qt.MouseButton.NoButton)
    v.delete_selection(); pump(0.02)
    assert A.links(d, 0) == []
    for _ in range(3):
        assert d.undo()
    ls = A.links(d, 0)
    assert len(ls) == 1 and ls[0]["uri"] == "https://a.example" and abs(ls[0]["from"].y0 - 132) < 1.5


def test_hand_hover_shows_status(win, pdf):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); v = t.viewer; d = t.doc
    A.add_link(d, 0, R, to_page=2)
    A.add_link(d, 0, fitz.Rect(72, 300, 220, 330), uri="https://hover.example")
    t.changed([0]); pump(0.05)
    win.on_tool("hand")
    said = []
    v.status.connect(said.append)
    _mouse(v, "move", fitz.Point(100, 90), buttons=Qt.MouseButton.NoButton)
    assert said[-1] == "페이지로 이동: 3"
    assert win.msg_label.text() == "페이지로 이동: 3"
    _mouse(v, "move", fitz.Point(100, 310), buttons=Qt.MouseButton.NoButton)
    assert said[-1] == "링크 열기: https://hover.example"
    _mouse(v, "move", fitz.Point(400, 600), buttons=Qt.MouseButton.NoButton)
    assert said[-1] == ""
