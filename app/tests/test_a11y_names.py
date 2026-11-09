"""화면 읽기 이름 시험(offscreen): 아이콘만 있는 단추에 이름이 있는지 · 화면 틀 문구.

python -m pytest app/tests/test_a11y_names.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_a11y_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabBar  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.ui import a11y  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


@pytest.fixture
def win():
    w = MainWindow(); w.show(); pump(0.2)
    yield w
    w.close()


def _pdf():
    path = os.path.join(tempfile.mkdtemp(prefix="rpdf_a11y_"), "a.pdf")
    d = fitz.open(); d.new_page(width=595, height=842).insert_text((72, 100), "x"); d.save(path); d.close()
    return path


def _chain(w):
    while w is not None:
        yield w; w = w.parentWidget()


def _frame_unnamed(win):
    """이 덩어리가 맡은 화면 틀(탭 줄·막대·상태줄·리본·시작 화면·왼쪽 패널)에서 이름 없는 단추."""
    out = []
    for root in (win.tabrow, win.navstrip, win.rightstrip, win.status, win.ribbon, win.start):
        out += a11y.unnamed_icon_buttons(root)
    t = win.cur()
    if t is not None:
        out += a11y.unnamed_icon_buttons(t.side) if hasattr(t, "side") else []
    # 업데이트 띠 단추는 글이 나중에 채워진다(update.py)
    # 가려진 곳: 색 단추가 든 가림 패널(redact_panel.py, 다른 덩어리)
    out = [b for b in out if not any(type(q).__name__ == "RedactPanel" for q in _chain(b))]
    return [b for b in out if b.objectName() != "accent" or b.isVisible()]


def test_start_screen_buttons_named(win):
    assert [[type(q).__name__+':'+q.objectName() for q in _chain(b)] for b in _frame_unnamed(win)] == []


def test_doc_screen_buttons_named(win):
    win.open_path(_pdf()); pump(0.3)
    assert [[type(q).__name__+':'+q.objectName() for q in _chain(b)] for b in _frame_unnamed(win)] == []
    sb = win.status
    names = {b.accessibleName() for b in sb.findChildren(type(sb.sb_hand))}
    assert {"첫 쪽", "이전 쪽", "다음 쪽", "마지막 쪽", "이전 보기", "다음 보기"} <= names
    assert sb.page_edit.accessibleName() == "쪽 번호"
    assert sb.zoom_slider.accessibleName() == "배율"


def test_tab_close_button_named(win):
    win.open_path(_pdf()); pump(0.3)
    tb = win.doctabs
    bs = [tb.tabButton(0, s) for s in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide)]
    assert any(b is not None and b.accessibleName() == "탭 닫기" for b in bs)


def test_search_panel_buttons_named(win):
    win.open_path(_pdf()); pump(0.3)
    from app.ui.search_panel import SearchPanel
    sp = win.cur().findChild(SearchPanel) or None
    if sp is None:
        win.toggle_side("search"); pump(0.2)
        sp = win.cur().findChild(SearchPanel)
    assert sp is not None
    assert [b.toolTip() for b in a11y.unnamed_icon_buttons(sp)] == []


def test_edit_hint_is_noun_form(win):
    from app.ui.window import MainWindow as M
    assert all("세요" not in v and "ㅂ니다" not in v for v in M.EDIT_HINTS.values())
