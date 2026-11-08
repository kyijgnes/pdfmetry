"""글자가 잘리는 칸 시험(offscreen): 상태줄 쪽 번호·확대 배율 · 리본 페이지 범위 · 꾸미기 글자 크기 · 인쇄 시트당 페이지 · 일괄 편집 작업 목록.

칸 폭이 고정 숫자가 아니라 글자 폭(fontMetrics)으로 잡혀서, 가장 긴 글자를 넣어도 입력칸 안쪽이 그 글자보다 넓은지 본다.
python -m pytest app/tests/test_clip_fields.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_clip_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.ui import fitwidth as FW  # noqa: E402
from app.ui import printing as PR  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

PAD = 2 * 2   # QLineEdit 이 글자 양옆에 두는 안쪽 여백


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(n: int) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="rpdf_clip_"), "a.pdf")
    d = fitz.open()
    for _ in range(n):
        d.new_page(width=200, height=200)
    d.save(path); d.close()
    return path


def inner_width(w) -> int:
    """글자가 실제로 놓이는 폭(편집 칸이 든 목록이면 그 안 입력칸)."""
    le = w.lineEdit() if isinstance(w, QComboBox) else w
    return le.width() if le is not w else w.contentsRect().width()


def fits(w, text: str) -> bool:
    fm = w.fontMetrics()
    if isinstance(w, QLineEdit):
        # 스타일시트 테두리(1)·좌우 여백(6)을 빼고도 글자 + 안쪽 여백이 들어가야 한다
        return w.width() - 2 * 1 - 2 * 6 >= fm.horizontalAdvance(text) + PAD
    return inner_width(w) >= fm.horizontalAdvance(text) + PAD


@pytest.fixture()
def win():
    w = MainWindow(); w.resize(1456, 790); w.show(); pump()
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close()


def test_page_box_four_digits(win):
    win.open_path(make_pdf(286)); pump(0.3)
    sb = win.status
    sb.show_page(285, 286); pump()
    assert sb.page_edit.text() == "286 / 286"
    assert fits(sb.page_edit, "286 / 286")
    assert fits(sb.page_edit, "9999 / 9999")   # 네 자리 쪽 수까지
    base = sb.page_edit.width()
    sb.show_page(12344, 12345); pump()            # 다섯 자리면 넓어진다
    assert sb.page_edit.width() > base and fits(sb.page_edit, "12345 / 12345")
    sb.show_page(0, 12, "Cover-Front"); pump()    # 긴 쪽 레이블도 다 보인다
    assert fits(sb.page_edit, "Cover-Front / 12")
    sb.show_page(0, 12); pump()                   # 다시 짧아지면 네 자리 폭으로 돌아온다
    assert sb.page_edit.width() == base


def test_zoom_box_shows_largest_percent(win):
    win.open_path(make_pdf(2)); pump(0.3)
    box = win.status.zoom_box
    for i in range(box.count()):
        assert fits(box, box.itemText(i)), box.itemText(i)


def test_refit_after_theme_change(win):
    """밝게/어둡게를 바꿔 스타일시트를 다시 입혀도 폭이 따라온다."""
    win.open_path(make_pdf(2)); pump(0.3)
    box = win.status.zoom_box
    QApplication.instance().setStyleSheet(QApplication.instance().styleSheet() + "\nQComboBox { padding-left: 20px; }")
    pump()
    try:
        assert fits(box, "1600%")
    finally:
        from app.ui import theme
        QApplication.instance().setStyleSheet(theme.qss(theme.is_dark(win.settings.get("prefs", {}))))
        pump()


def test_ribbon_range_placeholder(win):
    win.open_path(make_pdf(2)); pump(0.3)
    combos = [c for c in win.findChildren(QComboBox)
              if c.lineEdit() is not None and c.lineEdit().placeholderText() == "페이지 번호 입력"]
    assert combos
    for c in combos:
        c.show(); pump(0.02)
        assert fits(c, "페이지 번호 입력")
        for i in range(c.count()):
            assert fits(c, c.itemText(i))


def test_font_size_box():
    from app.ui.dlg_deco import FontRow
    row = FontRow(); row.show(); pump()
    for t in ("100", "1638"):
        assert fits(row.size, t), t
    row.close()


def test_print_nup_custom(win):
    d = fitz.open(make_pdf(4))
    dlg = PR.PrintDialog(None, d, 0, None)
    dlg.show(); pump()
    try:
        nup = dlg.nup
        style_w = FW.need(nup, FW.combo_texts(nup))
        assert nup.width() >= style_w
        fm = nup.fontMetrics()
        assert nup.width() >= fm.horizontalAdvance("사용자 지정...") + 2 * 6 + 18   # 여백 + 펼침 단추
    finally:
        dlg.close(); d.close()


def test_batch_task_list(win):
    from app.ui import dlg_batch as DB
    dlg = DB.BatchDialog(win, "convert", [make_pdf(1)], langs_fn=lambda: [])
    dlg.show(); pump()
    try:
        lst = dlg.tasks
        fm = lst.fontMetrics()
        widest = max(fm.horizontalAdvance(t) for _k, n, d in DB.TASKS for t in (n, d))
        bar = lst.verticalScrollBar().sizeHint().width()
        assert lst.viewport().width() - bar >= widest
    finally:
        dlg.close()


def test_wider_boxes_do_not_raise_window_min_width(win):
    """쪽 칸·배율 칸이 넓어져도 상태줄은 창 최소 폭을 밀지 않는다: 좁으면 확대 슬라이더가 숨는다."""
    win.open_path(make_pdf(9999)); pump(0.3)
    sb = win.status
    win.resize(win.minimumSizeHint().width(), 700); pump()
    assert sb.width() >= sb.layout().minimumSize().width() - sb._slider_room()
    if sb.zoom_slider.isHidden():
        assert sb.zoom_box.isVisible() and fits(sb.zoom_box, "1600%")
    win.resize(1800, 700); pump()
    assert not sb.zoom_slider.isHidden()   # 넓히면 다시 보인다
