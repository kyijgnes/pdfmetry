"""문서 탭 옮기기 시험(창을 offscreen 으로 띄움): 새 창으로 분리 · 다른 창에 합치기 · 마지막 탭이면 원래 창 닫힘 ·
고친 문서·실행 취소 기록 유지 · 신호가 새 창으로 · 끌면 창이 따라오고 다른 탭 줄에 닿으면 붙기 · 넘겨 온 파일은 마지막 창.

python -m pytest app/tests/test_tab_detach.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_tab_detach_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import pages as P  # noqa: E402
from app.ui import tab_detach  # noqa: E402
from app.ui.tab_detach import TabDrag  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 3):
    d = fitz.open()
    for i in range(n):
        d.new_page().insert_text((72, 100), f"page {i + 1}", fontsize=14)
    d.save(path); d.close()


@pytest.fixture()
def pdfs():
    root = tempfile.mkdtemp()
    out = []
    for name in ("가.pdf", "나.pdf", "다.pdf"):
        p = os.path.join(root, name); make_pdf(p); out.append(p)
    return out


@pytest.fixture()
def wins():
    made = []

    def new():
        w = MainWindow(); w.show(); w.resize(1000, 700); pump(0.05)
        made.append(w)
        return w
    yield new
    for w in list(tab_detach.windows) + made:
        for t in list(w.tabs):
            t.doc.modified = False
        w.close()
    pump(0.05)


def test_detach_moves_same_tab_and_keeps_edits(wins, pdfs):
    w = wins()
    for p in pdfs[:2]:
        w.open_path(p)
    pump(0.1)
    t = w.tabs[1]
    P.rotate(t.doc, [0], 90); t.changed([0])
    t.viewer.goto(2, remember=False)
    assert t.doc.modified and t.doc.can_undo()
    w2 = tab_detach.move_tab(t)
    pump(0.1)
    assert w2 is not w and w2.isVisible() and w.isVisible()
    assert w.tabs == [w.tabs[0]] and len(w.tabs) == 1 and w.doctabs.count() == 1
    assert w2.tabs == [t] and w2.cur() is t and w2.docstack.currentWidget() is t
    assert t.win is w2 and t.doc.modified and t.doc.can_undo()
    assert w2.doctabs.tabText(0).startswith("● ")
    assert w2.windowTitle().startswith("나.pdf") and w.windowTitle().startswith("가.pdf")
    assert w2.settings is w.settings   # 같은 프로그램 창끼리 최근 파일·설정을 함께 쓴다
    assert t.doc.undo() and not t.doc.can_undo()   # 실행 취소 기록도 그대로 따라왔다


def test_viewer_signals_follow_the_tab(wins, pdfs):
    w = wins()
    w.open_path(pdfs[0]); w.open_path(pdfs[1]); pump(0.1)
    t = w.tabs[0]
    w2 = tab_detach.move_tab(t); pump(0.05)
    w.say(""); w2.say("")
    t.viewer.status.emit("새 창 알림")
    assert w2.msg_label.text() == "새 창 알림" and w.msg_label.text() == ""
    seen = []
    w2.pages_action = lambda key, pages: seen.append(key)
    t.thumbs.pagesAction.emit("rotate_cw", [0])
    assert seen == ["rotate_cw"]


def test_last_tab_closes_source_without_asking(wins, pdfs):
    w = wins(); w2 = wins()
    w.open_path(pdfs[0]); w2.open_path(pdfs[1]); pump(0.1)
    t = w.tabs[0]
    t.doc.modified = True   # 저장 안 한 문서: 묻는 창이 뜨면 시험이 멈춘다
    tab_detach.move_tab(t, w2, 0)
    pump(0.1)
    assert not w.isVisible() and w not in tab_detach.windows
    assert w2.tabs[0] is t and len(w2.tabs) == 2 and t.doc.modified and not t.viewer.closed
    assert w2.cur() is t


class FakeMouse:
    """TabDrag 에 넣는 가짜 마우스: 자리와 왼쪽 단추."""
    def __init__(self, pos):
        self.pos = QPoint(pos); self.down = True

    def at(self, p, d: TabDrag):
        self.pos = QPoint(p); d.step(); pump(0.03)


def bar_point(w, i=None) -> QPoint:
    """w 탭 줄의 i 번 탭 가운데(없으면 탭 줄 오른쪽 빈자리) 화면 좌표."""
    if i is None:
        return w.tabrow.mapToGlobal(QPoint(w.tabrow.width() - 60, w.tabrow.height() // 2))
    return w.doctabs.mapToGlobal(w.doctabs.tabRect(i).center())


def test_live_drag_follows_mouse_and_snaps_to_other_bar(wins, pdfs):
    w = wins(); w2 = wins()
    w.move(0, 0); w2.move(2000, 0)   # 창 최소 폭이 넓어서 겹치지 않게 띄운다
    w.open_path(pdfs[0]); w.open_path(pdfs[1]); w2.open_path(pdfs[2]); pump(0.1)
    t = w.tabs[1]
    m = FakeMouse(bar_point(w, 1) + QPoint(0, 200))
    d = TabDrag(t, cursor=lambda: m.pos, pressed=lambda: m.down)
    d.start(); pump(0.05)
    w3 = t.win
    assert w3 not in (w, w2) and d.floating and len(w.tabs) == 1 and w3.isVisible()
    for p in (QPoint(500, 450), QPoint(700, 520)):   # 떠 있는 창이 탭을 마우스 밑에 둔 채 따라온다
        m.at(p, d)
        assert (bar_point(w3, 0) - p).manhattanLength() <= 2, (bar_point(w3, 0), p)
    m.at(bar_point(w2, 0) + QPoint(-5, 2), d)   # 다른 창 탭 줄에 닿으면 놓기 전에 바로 붙는다
    assert t.win is w2 and not d.floating and not w3.isVisible() and w3 not in tab_detach.windows
    assert [x.doc.name for x in w2.tabs] == ["나.pdf", "다.pdf"]
    m.at(bar_point(w2, 1) + QPoint(10, 0), d)   # 붙은 줄 안에서 자리 옮기기
    assert [x.doc.name for x in w2.tabs] == ["다.pdf", "나.pdf"] and w2.doctabs.tabText(1) == "나.pdf"
    m.at(QPoint(600, 600), d)   # 줄 아래로 벗어나면 다시 떼어 새 창
    w4 = t.win
    assert w4 not in (w, w2, w3) and d.floating and len(w2.tabs) == 1
    m.at(bar_point(w, None), d)   # 원래 창 탭 줄 빈자리: 끝에 붙는다
    assert t.win is w and [x.doc.name for x in w.tabs] == ["가.pdf", "나.pdf"] and not w4.isVisible()
    m.down = False; d.step()
    assert not d.timer.isActive() and tab_detach._drag is None


def test_live_drag_of_only_tab_moves_its_window(wins, pdfs):
    w = wins(); w2 = wins()
    w.move(0, 0); w2.move(2000, 0)   # 창 최소 폭이 넓어서 겹치지 않게 띄운다
    w.open_path(pdfs[0]); w2.open_path(pdfs[1]); pump(0.1)
    t = w.tabs[0]
    m = FakeMouse(bar_point(w, 0) + QPoint(0, 200))
    d = TabDrag(t, cursor=lambda: m.pos, pressed=lambda: m.down)
    d.start(); pump(0.05)
    assert t.win is w and d.floating   # 새 창을 만들지 않고 그 창이 따라온다
    m.at(QPoint(400, 600), d)
    assert (bar_point(w, 0) - QPoint(400, 600)).manhattanLength() <= 2
    m.at(bar_point(w2, 0), d)
    assert t.win is w2 and not w.isVisible() and len(w2.tabs) == 2
    m.down = False; d.step()
    assert tab_detach._drag is None


def test_tab_menu_detach_entry(wins, pdfs):
    w = wins()
    w.open_path(pdfs[0]); pump(0.05)
    got = {}

    def menu_items(pick=None):
        """탭 메뉴를 띄워 항목을 읽고, pick 이 있으면 그 항목을 눌러 닫는다."""
        got.clear()

        def peek():
            m = QApplication.activePopupWidget()
            assert isinstance(m, QMenu)
            got.update({x.text(): x.isEnabled() for x in m.actions() if x.text()})
            act = next((x for x in m.actions() if x.text() == pick), None)
            if act is not None:
                act.trigger()
            m.close()
        QTimer.singleShot(50, peek)
        w._tab_menu(w.doctabs.tabRect(0).center())
    menu_items()
    assert got["새 창으로 분리"] is False   # 탭이 하나뿐이면 흐리게
    w.open_path(pdfs[1]); pump(0.05)
    menu_items("새 창으로 분리"); pump(0.1)
    assert got["새 창으로 분리"] is True
    assert len(w.tabs) == 1 and w.tabs[0].doc.name == "나.pdf"
    w2 = [x for x in tab_detach.windows if x is not w and x.tabs and x.tabs[0].doc.name == "가.pdf"]
    assert w2 and w2[0].isVisible()


def test_dragging_tab_out_of_bar_tears_it_off(wins, pdfs, monkeypatch):
    w = wins()
    w.open_path(pdfs[0]); w.open_path(pdfs[1]); pump(0.1)
    torn = []
    monkeypatch.setattr(tab_detach, "start_drag", lambda win, i: torn.append((win, i)))
    bar = w.doctabs
    c = QPointF(bar.tabRect(0).center())

    def send(kind, p, buttons):
        ev = QMouseEvent(kind, p, QPointF(bar.mapToGlobal(p.toPoint())), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(bar, ev)
    send(QEvent.Type.MouseButtonPress, c, Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseMove, c + QPointF(0, 5), Qt.MouseButton.LeftButton)   # 줄 안에서는 떼지 않는다
    assert not torn
    send(QEvent.Type.MouseMove, c + QPointF(0, 120), Qt.MouseButton.LeftButton)
    assert torn == [(w, 0)]
    send(QEvent.Type.MouseButtonRelease, c + QPointF(0, 120), Qt.MouseButton.NoButton)
    assert len(w.tabs) == 2


def test_forwarded_files_go_to_last_active_window(wins, pdfs):
    w = wins(); w2 = wins()
    tab_detach.activated(w2)
    w.open_forwarded([pdfs[2]]); pump(0.05)
    assert not w.tabs and [t.doc.name for t in w2.tabs] == ["다.pdf"]


def test_receiver_moves_to_remaining_window(wins, pdfs):
    w = wins(); w2 = wins()

    class FakeReceiver:
        on_paths = None
        listening = 0
        def stop(self): pass
        def listen(self): self.listening += 1
    r = FakeReceiver(); r.on_paths = w.open_forwarded
    w._receiver = r
    w.close(); pump(0.05)
    assert w2._receiver is r and w._receiver is None and r.listening == 1
    tab_detach.activated(w2)
    r.on_paths([pdfs[0]]); pump(0.05)
    assert [t.doc.name for t in w2.tabs] == ["가.pdf"]
    w2._receiver = None
