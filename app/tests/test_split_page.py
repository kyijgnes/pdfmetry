# -*- coding: utf-8 -*-
"""페이지 분할(분할선): 엔진이 선 자리대로 나누는지, 창의 선 더하기·지우기·등거리·제한·검사."""
from __future__ import annotations

import os
import sys

import fitz
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.engine import pages as P  # noqa: E402
from app.engine.document import Document  # noqa: E402


def make(n: int, size=(600, 800)) -> Document:
    f = fitz.open()
    for i in range(n):
        pg = f.new_page(width=size[0], height=size[1])
        pg.insert_text((20, 40), f"TL{i + 1}")      # 왼쪽 위
        pg.insert_text((400, 700), f"BR{i + 1}")    # 오른쪽 아래
        pg.add_text_annot((450, 720), "메모")
    return Document.from_bytes(f.tobytes(), "S.pdf")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── 엔진 ──────────────────────────────────────────────────────
def test_uneven_lines_sizes_and_order():
    d = make(2)
    m = P.split_grid(d, [1], xs=[0.25], ys=[0.75])   # 둘째 쪽만, 세로선 1/4 · 가로선 3/4
    f = d.fitz
    assert f.page_count == 5 and m == [0, 1, 1, 1, 1]
    sizes = [(round(f[i].rect.width), round(f[i].rect.height)) for i in range(1, 5)]
    assert sizes == [(150, 600), (450, 600), (150, 200), (450, 200)]   # 왼쪽 위 → 오른쪽 → 다음 줄
    assert "TL2" in f[1].get_text() and "BR2" in f[4].get_text() and "BR2" not in f[1].get_text()
    assert any(True for _ in f[4].annots())          # 주석이 따라온다
    assert "TL1" in f[0].get_text() and f[0].rect.width == pytest.approx(600)
    assert d.undo() and d.fitz.page_count == 2       # 되돌리기 한 번


def test_lines_on_rotated_page_follow_screen():
    d = make(1)
    d.fitz[0].set_rotation(90)                       # 보이는 쪽은 800 × 600
    P.split_grid(d, [0], xs=[0.25])
    f = d.fitz
    assert [round(f[i].rect.width) for i in range(2)] == [200, 600]
    assert all(round(f[i].rect.height) == 600 for i in range(2))


def test_old_cols_rows_call_and_bad_lines():
    d = make(1)
    assert P.split_grid(d, [0], 2, 1) == [0, 0]
    with pytest.raises(ValueError):
        P.split_grid(d, [0], xs=[], ys=[])
    with pytest.raises(ValueError):
        P.split_grid(d, [0], xs=[1.2])
    with pytest.raises(ValueError):
        P.split_grid(d, [0], xs=[k / 12 for k in range(1, 12)])   # 11개


# ── 창 ────────────────────────────────────────────────────────
def test_gap_and_area_helpers():
    from app.ui.dlg_splitpage import area_ok, widest_gap_middle
    assert widest_gap_middle([]) == 0.5
    assert widest_gap_middle([0.5]) == 0.25            # 같은 너비면 앞 구간
    assert widest_gap_middle([0.2, 0.4]) == pytest.approx(0.7)
    assert area_ok([0.5]) and area_ok([0.09]) and not area_ok([0.5, 0.55])


def test_dialog_defaults_and_lines(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from app.ui.dlg_splitpage import TOO_MANY, TOO_SMALL, SplitPageDialog
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: said.append(a[2]))
    d = make(4)
    dlg = SplitPageDialog(None, d, 2, [2])
    pv = dlg.preview
    assert pv.lines == [["v", 0.5]] and dlg.ok.isEnabled()          # 세로선 하나가 가운데
    assert dlg.range.pages() == [2]                                  # 지금 쪽
    assert not dlg.delete.isEnabled()
    dlg.add_v.click()
    assert pv.positions("v") == [0.25, 0.5] and dlg.delete.isEnabled()
    dlg.add_h.click(); assert pv.positions("h") == [0.5]
    # 끌어 옮기기: 화면 좌표로 세로선을 3/4 자리로
    pv.resize(500, 500)
    r = pv.page_rect()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    def mouse(kind, x, y):
        ev = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        {QMouseEvent.Type.MouseButtonPress: pv.mousePressEvent, QMouseEvent.Type.MouseMove: pv.mouseMoveEvent,
         QMouseEvent.Type.MouseButtonRelease: pv.mouseReleaseEvent}[kind](ev)
    mouse(QMouseEvent.Type.MouseButtonPress, r.x() + 0.5 * r.width(), r.y() + 0.2 * r.height())
    mouse(QMouseEvent.Type.MouseMove, r.x() + 0.75 * r.width(), r.y() + 0.2 * r.height())
    mouse(QMouseEvent.Type.MouseButtonRelease, r.x() + 0.75 * r.width(), r.y() + 0.2 * r.height())
    assert pv.positions("v") == [0.25, pytest.approx(0.75)]
    # 등거리: 「가로 등거리」는 세로선을 고르게
    dlg.dist_v.click(); assert pv.positions("v") == pytest.approx([1 / 3, 2 / 3])
    # 고른 선 Delete
    from PySide6.QtGui import QKeyEvent
    pv.sel = 0
    pv.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert len(pv.lines) == 2
    # 방향마다 10개
    for _ in range(12):
        dlg.add_h.click()
    assert len(pv.positions("h")) == 10 and said[-1] == TOO_MANY
    # 너무 작은 칸이면 적용 안 됨
    dlg._apply(); assert said[-1] == TOO_SMALL and dlg.result() == 0
    dlg.dist_h.click(); dlg._apply(); assert dlg.result() == 1           # 1/11 ≈ 9.1% 는 통과
    vals = dlg.values()
    assert vals["pages"] == [2] and len(vals["xs"]) == 1 and len(vals["ys"]) == 10
    # 선이 없으면 적용 꺼짐
    pv.lines.clear(); pv.sel = None; pv.changed.emit()
    assert not dlg.ok.isEnabled()
