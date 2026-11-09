"""화면 틀 시험(offscreen): 막대 폭·접기 · 상태줄 펼침 단추·단위·접기 · 스킨 · 빠른 실행 · 탭 줄.

python -m pytest app/tests/test_frame.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_frame_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragMoveEvent, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine.document import Document  # noqa: E402
from app.ui import sidebars as SB  # noqa: E402
from app.ui import tab_detach, theme  # noqa: E402
from app.ui import window as W  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 3):
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"page {i + 1}")
    d.save(path); d.close()
    return path


def _close_all():
    for x in list(tab_detach.windows):
        for t in list(x.tabs):
            t.doc.modified = False
        x.close()
    pump(0.05)


@pytest.fixture()
def folder():
    return tempfile.mkdtemp(prefix="rpdf_frame_")


@pytest.fixture()
def win():
    if os.path.exists(W.SETTINGS):
        os.remove(W.SETTINGS)   # 시험마다 처음 설정으로
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    _close_all()


def reopen(w) -> MainWindow:
    """창을 닫고(설정 저장) 새로 켠 창."""
    _close_all()
    w2 = MainWindow(); w2.show(); w2.resize(1200, 800); pump(0.05)
    return w2


def test_left_panel_width_dragged_and_remembered(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "a.pdf"))); pump(0.2)
    win.toggle_side("thumbs"); pump(0.05)
    t = win.cur()
    sp = t.split
    assert t.side.isVisible() and abs(sp.sizes()[0] - SB.LEFT_W) <= 2   # 처음 폭 252
    total = sum(sp.sizes())
    sp.setSizes([330, total - 330, 0]); sp.splitterMoved.emit(330, 1)   # 사람이 끈 것처럼
    assert win.settings["left_w"] == 330
    w2 = reopen(win)
    w2.open_path(os.path.join(folder, "a.pdf")); pump(0.2)
    t2 = w2.cur()
    assert t2.side.isVisible() and t2.side_key == "thumbs"   # 문서별 열린 패널도 되살림
    assert abs(t2.split.sizes()[0] - 330) <= 2
    assert t2.side.minimumWidth() == SB.MIN_W


def test_left_bar_collapse_to_strip_and_remembered(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "b.pdf"))); pump(0.2)
    win.toggle_side("bookmark")
    win.bars.left_btn.click(); pump(0.05)
    assert win.bars.left_strip.isVisible() and win.bars.left_strip.width() == SB.STRIP_W
    assert not win.navstrip.isVisible() and not win.cur().side.isVisible()
    w2 = reopen(win)
    assert w2.settings["left_collapsed"] is True
    w2.open_path(os.path.join(folder, "b.pdf")); pump(0.2)
    assert w2.bars.left_strip.isVisible() and not w2.navstrip.isVisible() and not w2.cur().side.isVisible()
    w2.bars.left_strip.button.click(); pump(0.05)   # 띠 단추로 다시 편다: 접을 때 열린 패널이 돌아온다
    assert w2.navstrip.isVisible() and not w2.bars.left_strip.isVisible()
    assert w2.cur().side.isVisible() and w2.cur().side_key == "bookmark"


def test_right_bar_collapse_and_width(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "c.pdf"))); pump(0.2)
    win.right_action("props"); pump(0.05)
    t = win.cur()
    # 속성 패널은 글자가 다 보이는 폭에 맞춰 고정된다(RIGHT_W 는 그 아래 한도). 오른쪽 칸은 그 폭 그대로
    want = max(SB.RIGHT_W, t.props.minimumWidth())
    assert t.props.isVisible() and abs(t.split.sizes()[2] - want) <= 2
    win.bars.set_right(True); pump(0.05)
    assert win.bars.right_strip.isVisible() and not win.rightstrip.isVisible() and not t.props.isVisible()
    win.on_action("side_right"); pump(0.05)   # Shift+F4 는 펴면서 속성을 연다
    assert win.rightstrip.isVisible() and t.props.isVisible()


def test_panel_head_close_button(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "d.pdf"))); pump(0.2)
    win.toggle_side("comments"); pump(0.05)
    t = win.cur()
    closes = [b for b in t.annots.findChildren(QPushButton, "panelclose")]
    assert closes
    closes[0].click(); pump(0.02)
    assert not t.side.isVisible() and not win.navbtns["comments"].isChecked()


def test_status_fit_and_layout_dropdowns(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "e.pdf"))); pump(0.2)
    st, v = win.status, win.cur().viewer
    acts = {a.data(): a for a in st.fit_menu.actions()}
    acts["fit_height"].trigger(); pump(0.05)
    assert v.zoom_mode == "fit_height" and st.checked_fit() == "fit_height"
    assert st.fit_btn.icon().cacheKey() == acts["fit_height"].icon().cacheKey()
    lay = {a.data(): a for a in st.layout_menu.actions() if a.data()}
    lay["facing"].trigger(); pump(0.05)
    assert v.layout_mode == "facing" and st.checked_layout() == "facing"
    assert st.layout_btn.icon().cacheKey() == lay["facing"].icon().cacheKey()
    win.on_action("zoomin"); pump(0.02)   # 고정 배율이면 맞춤 체크가 빠진다
    assert st.checked_fit() is None
    # 배율 칸 위 휠은 한 단계, 슬라이더는 단계표 차례
    z = v.zoom
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(st.zoom_box, ev); pump(0.02)
    assert v.zoom > z
    st.zoom_slider.setValue(7); pump(0.02)   # 단계표 여덟째 = 100%
    assert abs(v.zoom - 1.0) < 1e-6


def test_page_box_current_over_total(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "f.pdf"), n=12)); pump(0.2)
    v = win.cur().viewer
    v.goto(2); pump(0.05)
    assert win.page_edit.text() == "3 / 12"
    win.page_edit.setText("7 / 12"); win._goto_entered()
    assert v.current == 6


def test_size_label_follows_unit(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "g.pdf"))); pump(0.2)
    assert win.size_label.text() == "20.99 x 29.70 cm"
    win.prefs["unit"] = "mm"; win._sync_status(win.cur())
    assert win.size_label.text() == "209.9 x 297.0 mm"
    win.prefs["unit"] = "pt"; win._sync_status(win.cur())
    assert win.size_label.text() == "595 x 842 pt"


def test_status_collapse_and_peek(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "h.pdf"))); pump(0.2)
    st = win.status
    st.fold_btn.click(); pump(0.05)
    assert not st.isVisible() and st.hot.isVisible() and win.settings["status_collapsed"] is True
    QApplication.sendEvent(st.hot, QEvent(QEvent.Type.Enter))   # 마우스를 창 아래 끝에
    pump(0.15)
    assert not st.isVisible()   # 300ms 머물러야 나온다
    pump(0.4)
    assert st.isVisible() and st._peek
    pump(1.3)   # 마우스가 상태줄 밖(offscreen 커서)에 있으니 0.5초 뒤 숨는다
    assert not st.isVisible() and st.hot.isVisible()
    w2 = reopen(win)
    w2.open_path(os.path.join(folder, "h.pdf")); pump(0.2)
    assert not w2.status.isVisible() and w2.status.hot.isVisible()
    w2.status.set_collapsed(False); pump(0.02)
    assert w2.status.isVisible() and not w2.status.hot.isVisible()


def test_fullscreen_keeps_collapsed_status(win, folder):
    win.open_path(make_pdf(os.path.join(folder, "i.pdf"))); pump(0.2)
    win.status.set_collapsed(True)
    win.on_action("fullscreen"); pump(0.05)
    win.on_action("fullscreen"); pump(0.05)
    assert not win.status.isVisible() and win.status.hot.isVisible()


def test_skin_system_follows_windows(win):
    win.set_skin("system")
    assert win.prefs["theme"] == "system" and win.view_checked("skin_system") is True
    hints = QGuiApplication.styleHints()
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark); pump(0.02)
    assert theme.CUR["BG"] == theme.DARK["BG"]
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Light); pump(0.02)
    assert theme.CUR["BG"] == theme.LIGHT["BG"]
    win.on_action("skin_dark")
    assert theme.CUR["BG"] == theme.DARK["BG"]
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Light); pump(0.02)   # 다크로 정했으면 따라가지 않는다
    assert theme.CUR["BG"] == theme.DARK["BG"]
    win.set_skin("light")


def test_old_dark_pref_moves_to_theme():
    p = {"dark": True}
    assert theme.skin(p) == "dark" and p == {"theme": "dark"}
    p = {"dark": False}
    assert theme.skin(p) == "light" and "dark" not in p


def test_quick_access_hidden_and_remembered(win):
    win.ribbon.qat_acts["print"].setChecked(False)
    assert win.ribbon.quick["print"].isHidden() and win.prefs["qat"]["print"] is False
    win.ribbon.qat_acts["ribbon"].setChecked(False)   # 리본 끄기 = 아랫줄 접기
    assert win.ribbon.folded and win.ribbon.stack.isHidden() and win.settings["ribbon_folded"] is True
    win.ribbon.set_tab("view")
    w2 = reopen(win)
    assert w2.ribbon.quick["print"].isHidden() and not w2.ribbon.qat_acts["print"].isChecked()
    assert not w2.ribbon.quick["save"].isHidden()
    assert w2.ribbon.folded and not w2.ribbon.qat_acts["ribbon"].isChecked()
    assert w2.ribbon.current_tab == "view"
    w2.on_action("ribbon_fold")   # F8 은 체크를 따라 바꾼다
    assert not w2.ribbon.folded and w2.ribbon.qat_acts["ribbon"].isChecked()


def test_rename_file_follows_recent(win, folder):
    p = make_pdf(os.path.join(folder, "old.pdf"))
    win.open_path(p); pump(0.2)
    win.settings["favorites"].append(p)
    t = win.cur()
    assert win.rename_file(t, "새 이름")
    new = os.path.join(folder, "새 이름.pdf")
    assert os.path.isfile(new) and not os.path.exists(p)
    assert t.doc.path == new and win.doctabs.tabText(0).endswith("새 이름.pdf")
    assert win.settings["recent"][0]["path"] == new and win.settings["favorites"] == [new]
    assert "새 이름.pdf" in win.windowTitle()


def test_51st_tab_refused(win, monkeypatch):
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    for _ in range(W.MAX_TABS):
        win.add_doc(Document.blank())
    pump(0.1)
    assert len(win.tabs) == 50 and not said
    assert win.add_doc(Document.blank()) is None
    assert len(win.tabs) == 50 and said and "50" in said[0]


def test_tab_list_menu_and_drag_hover(win, folder):
    for n in ("p.pdf", "q.pdf"):
        win.open_path(make_pdf(os.path.join(folder, n)))
    pump(0.2)
    from PySide6.QtWidgets import QMenu
    m = QMenu(); win._fill_tab_list(m)
    names = [a.text() for a in m.actions()]
    assert names == ["p.pdf", "q.pdf"] and m.actions()[1].isChecked()
    m.actions()[0].trigger()
    assert win.doctabs.currentIndex() == 0
    bar = win.doctabs
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(os.path.join(folder, "p.pdf"))])
    pos = bar.tabRect(1).center()
    ev = QDragMoveEvent(pos, Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    bar.dragMoveEvent(ev)
    pump(0.2)
    assert bar.currentIndex() == 0   # 0.5초 머물러야
    pump(0.5)
    assert bar.currentIndex() == 1


def test_missing_recent_file_removed(win, folder, monkeypatch):
    gone = os.path.join(folder, "gone.pdf")
    win.settings["recent"].insert(0, {"path": gone, "time": 0})
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append((a[1], a[2])))
    win.start.openPath.emit(gone); pump(0.02)
    assert said and said[0][0] == "파일 열기 실패" and "존재하지 않습니다" in said[0][1]
    assert all(r["path"] != gone for r in win.settings["recent"]) and not win.tabs


def test_mail_over_20mb_refused(win, folder, monkeypatch):
    win.open_path(make_pdf(os.path.join(folder, "m.pdf"))); pump(0.2)
    monkeypatch.setattr(W, "MAIL_MAX", 10)
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[1]))
    win.mail(win.cur())
    assert said == ["문서 크기가 20MB를 초과했습니다"]


def test_quit_asks_when_many_tabs(win, folder, monkeypatch):
    for n in ("r.pdf", "s.pdf", "u.pdf"):
        win.open_path(make_pdf(os.path.join(folder, n)))
    pump(0.2)
    answer = [None]
    monkeypatch.setattr(W, "ask_close_many", lambda *a, **k: answer[0])
    win.on_action("quit"); pump(0.02)
    assert len(win.tabs) == 3 and win.isVisible()   # 그만둠
    answer[0] = "current"
    win.on_action("quit"); pump(0.02)
    assert len(win.tabs) == 2 and win.isVisible()
    win.close(); pump(0.02)   # 프로그램이 닫는 것(시험 끝 등)은 묻지 않는다
    assert not win.tabs


def test_remember_tool_per_document(win, folder):
    p = make_pdf(os.path.join(folder, "tool.pdf"))
    win.open_path(p); pump(0.2)
    win.on_tool("hand")
    win.close_tab(0); pump(0.05)
    assert win.settings["recent"][0]["view"]["tool"] == "hand" and win.settings["recent"][0]["view"]["side"] == ""
    win.open_path(p); pump(0.2)
    assert win.cur().viewer.tool == "hand"
