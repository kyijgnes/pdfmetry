"""인쇄 대화상자 프린터 칸: 머리가 같은 긴 프린터 이름이 화면에서 구분되는지.

python -m pytest app/tests/test_printer_names.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="rpdf_prnames_test_"))

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.ui import fitwidth as FW  # noqa: E402
from app.ui import printing as PR  # noqa: E402

NAMES = ["PDF메트리", "SINDOH N923 PCL (19층)", "SINDOH N803 PCL 192.168.0.68",
         "SINDOH N800/MF Series PCL(69)", "SINDOH N800/MF Series PCL(68)"]


def pump():
    for _ in range(3):
        app.processEvents()


@pytest.fixture
def fake_printers(monkeypatch):
    class Info(PR.QPrinterInfo):
        @staticmethod
        def availablePrinterNames():
            return list(NAMES)

        @staticmethod
        def defaultPrinterName():
            return NAMES[3]
    monkeypatch.setattr(PR, "QPrinterInfo", Info)


@pytest.mark.parametrize("width", [60, 90, 120, 160, 200])
def test_elide_keeps_distinct_part(width):
    fm = QFontMetrics(app.font())
    shown = [FW.distinct_elide(t, NAMES, fm, width) for t in NAMES]
    for t, s in zip(NAMES, shown):
        assert fm.horizontalAdvance(s) <= width, (t, s)
    pcl = shown[3:]
    assert pcl[0] != pcl[1]
    if width >= 90:   # 끝의 번호가 괄호째 남는다
        assert pcl[0].endswith("(69)") and pcl[1].endswith("(68)")
        assert len(set(shown)) == len(shown), shown


def test_elide_untouched_when_fits():
    fm = QFontMetrics(app.font())
    for t in NAMES:
        assert FW.distinct_elide(t, NAMES, fm, 10 ** 4) == t


def test_dialog_left_column_keeps_width_and_shows_full_name(fake_printers):
    d = fitz.open(); d.new_page()
    dlg = PR.PrintDialog(None, d, 0, None)
    dlg.show(); pump()
    try:
        c = dlg.printer
        assert isinstance(c, FW.NameCombo)
        left = c.parentWidget()
        assert left.width() == dlg.COL_W           # 긴 이름이 있어도 왼쪽 열은 원래 폭
        assert not hasattr(dlg, "left_w")
        shown = []
        for t in NAMES:
            c.setCurrentText(t); pump()
            shown.append(c.shown_text())
            if c.is_elided():                      # 줄였으면 칸 아래 전체 이름 줄과 툴팁
                assert dlg.printer_full.isVisible() and dlg.printer_full.text() == t
                assert c.toolTip() == t
            else:                                  # 다 보이면 줄을 숨기고 툴팁도 없다
                assert shown[-1] == t
                assert not dlg.printer_full.isVisible() and c.toolTip() == ""
        assert len(set(shown)) == len(NAMES), shown   # 줄여도 서로 구분된다
        c.setCurrentText(NAMES[3]); pump(); a = c.shown_text()
        c.setCurrentText(NAMES[4]); pump(); b = c.shown_text()
        assert c.is_elided() and a != b and a.endswith("(69)") and b.endswith("(68)")
        assert dlg.printer_full.isVisible()
        c.setCurrentText(NAMES[0]); pump()         # 짧은 이름이면 줄이 숨는다
        assert not c.is_elided() and not dlg.printer_full.isVisible()
    finally:
        dlg.close(); d.close()


def test_popup_wider_than_narrow_combo():
    host = QWidget(); lay = QVBoxLayout(host)
    c = FW.NameCombo(); c.addItems(NAMES); c.setFixedWidth(150); lay.addWidget(c)
    host.show(); pump()
    try:
        c.showPopup(); pump()
        v = c.view()
        assert v.window().width() > c.width()
        assert v.viewport().width() >= v.sizeHintForColumn(0)
        # 칸이 좁아도 닫힌 칸 글자는 갈리는 끝이 남는다
        c.setCurrentIndex(3); a = c.shown_text()
        c.setCurrentIndex(4); b = c.shown_text()
        assert a != b and a.endswith("9)") and b.endswith("8)")
    finally:
        c.hidePopup(); host.close()
