# -*- coding: utf-8 -*-
"""구성 탭 자르기·페이지 크기·페이지 박스: 엔진(여백 ↔ 박스, 쪽 키우기, 포함 관계)과 대화상자 칸·미리보기 맞물림."""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_pagebox_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.engine import pages as P  # noqa: E402
from app.engine.document import Document  # noqa: E402

CM = 72 / 2.54


def make(n: int = 3, size=(600, 800), rotate: int = 0) -> Document:
    f = fitz.open()
    for i in range(n):
        pg = f.new_page(width=size[0], height=size[1])
        pg.insert_text((72, 100), f"P{i + 1}")
        if rotate:
            pg.set_rotation(rotate)
    return Document.from_bytes(f.tobytes(), "t.pdf")


def keys(d: Document, i: int) -> dict:
    f = d.fitz
    return {k: P._box(f, f[i].xref, k) for k in ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")}


def M(top=0.0, left=0.0, bottom=0.0, right=0.0) -> dict:
    return {"top": top, "left": left, "bottom": bottom, "right": right}


# ── 엔진 ──────────────────────────────────────────────────────
def test_crop_margins_only_cropbox_and_undo():
    d = make()
    P.crop(d, [0, 2], margins=M(10, 20, 30, 40))
    k = keys(d, 0)
    assert k["MediaBox"] == [0, 0, 600, 800] and k["CropBox"] == [20, 30, 560, 790]
    assert k["TrimBox"] is None and keys(d, 1)["CropBox"] is None and keys(d, 2)["CropBox"] == [20, 30, 560, 790]
    assert P.box_margins(d.fitz, 0) == M(10, 20, 30, 40)
    assert d.undo() and keys(d, 0)["CropBox"] is None


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_crop_margins_follow_visible_sides(rot):
    d = make(1, rotate=rot)
    P.crop(d, [0], margins=M(10, 20, 30, 40))
    assert P.box_margins(d.fitz, 0) == M(10, 20, 30, 40)
    r = d.fitz[0].rect
    w, h = (800, 600) if rot in (90, 270) else (600, 800)
    assert (r.width, r.height) == (w - 60, h - 40)


def test_crop_margins_too_big_skips_page():
    d = make(1)
    P.crop(d, [0], margins=M(500, 0, 400, 0))
    assert keys(d, 0)["CropBox"] is None


def test_set_boxes_several_and_containment():
    d = make(1)
    P.set_boxes(d, [0], {"crop": M(10, 10, 10, 10), "trim": M(0, 0, 0, 0), "art": M(5, 5, 5, 5)})
    k = keys(d, 0)
    assert k["CropBox"] == [10, 10, 590, 790]
    assert k["TrimBox"] == [10, 10, 590, 790]      # 블리드가 없으면 자르기 박스 안으로
    assert k["ArtBox"] == [10, 10, 590, 790]       # 트림 안으로
    assert k["BleedBox"] is None


def test_set_boxes_disjoint_is_zero_rect():
    d = make(1)
    P.set_boxes(d, [0], {"bleed": M(0, 0, 0, 400), "trim": M(0, 300, 0, 0)})
    assert keys(d, 0)["TrimBox"] == [0, 0, 0, 0]


def test_set_boxes_grow_center_and_offset():
    d = make(2)
    P.crop(d, [1], margins=M(10, 10, 10, 10))
    P.set_boxes(d, [0, 1], {}, size=(800, 1000))
    assert keys(d, 0)["MediaBox"] == [-100, -100, 700, 900]
    assert keys(d, 1)["CropBox"] == [10, 10, 590, 790]                 # 안쪽이던 박스는 제자리
    assert d.fitz[0].get_text().strip() == "P1"
    d = make(1)
    P.set_boxes(d, [0], {"crop": M()}, size=(700, 900), offset=(0, 100))
    assert keys(d, 0)["MediaBox"] == [0, 0, 700, 900]                  # 왼쪽 위에서 x 0·y 100 → 위로 100
    assert keys(d, 0)["CropBox"] == [0, 0, 700, 900]


def test_set_boxes_never_shrinks():
    d = make(1)
    P.set_boxes(d, [0], {}, size=(300, 300))
    assert keys(d, 0)["MediaBox"] == [0, 0, 600, 800]


def test_set_boxes_untouched_full_box_follows_media():
    d = make(1)
    d.fitz[0].set_cropbox(fitz.Rect(0, 0, 600, 800))
    P.set_boxes(d, [0], {}, size=(800, 800))
    assert keys(d, 0)["CropBox"] == keys(d, 0)["MediaBox"] == [-100, 0, 700, 800]


# ── 대화상자 ──────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_crop_dialog_spins_preview_and_size(qapp):
    from PySide6.QtCore import QRectF
    from app.ui import dlg_pagebox as PB
    d = make(3)
    dlg = PB.CropDialog(None, d, [1], "cm")
    assert not dlg.ok.isEnabled() and dlg.rng.pages() == [1]
    assert [dlg.spins[s].value() for s in ("top", "left", "bottom", "right")] == [0, 0, 0, 0]
    dlg.spins["left"].setValue(1.0)
    assert dlg.ok.isEnabled()
    v = dlg.panel.view
    assert abs(v.box.left() - CM) < 0.01
    assert "잘린 페이지 크기: 20.17 x 28.22 cm" in dlg.panel.caption.text()
    v.box = QRectF(72, 36, 300, 400); v.changed.emit(QRectF(v.box))       # 끌기로 바뀐 셈
    assert abs(dlg.spins["top"].value() - 36 / CM) < 0.01 and abs(dlg.m["right"] - 228) < 0.01
    vals = dlg.values()
    assert vals["pages"] == [1] and abs(vals["margins"]["bottom"] - 364) < 0.01


def test_crop_dialog_drag_handles(qapp):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    from app.ui import dlg_pagebox as PB
    d = make(1)
    dlg = PB.CropDialog(None, d, [0], "mm")
    v = dlg.panel.view

    def mouse(kind, pt):
        btn = Qt.MouseButton.LeftButton
        ev = QMouseEvent(kind, QPointF(pt), QPointF(pt), btn, btn if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: v.mousePressEvent, QEvent.Type.MouseMove: v.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: v.mouseReleaseEvent}[kind](ev)
    br = v.to_px(v.box).bottomRight()
    mouse(QEvent.Type.MouseButtonPress, br)
    mouse(QEvent.Type.MouseMove, br - QPointF(40, 50))
    mouse(QEvent.Type.MouseButtonRelease, br - QPointF(40, 50))
    assert dlg.m["right"] > 0 and dlg.m["bottom"] > 0 and dlg.m["top"] == 0
    c = v.to_px(v.box).center()                      # 몸통 옮기기: 크기 그대로
    w = v.box.width()
    mouse(QEvent.Type.MouseButtonPress, c); mouse(QEvent.Type.MouseMove, c + QPointF(10, 10)); mouse(QEvent.Type.MouseButtonRelease, c + QPointF(10, 10))
    assert abs(v.box.width() - w) < 0.01 and dlg.m["left"] > 0
    out = QPointF(v.width() - 2, v.height() - 2)     # 밖을 끌지 않고 누르면 쪽 전체
    v.box = v.box.adjusted(0, 0, -50, -50)
    mouse(QEvent.Type.MouseButtonPress, out); mouse(QEvent.Type.MouseButtonRelease, out)
    assert dlg.m == {"top": 0, "left": 0, "bottom": 0, "right": 0}
    tl = v.to_px(v.box).topLeft()                    # 최소 18px 이하로 줄지 않음
    mouse(QEvent.Type.MouseButtonPress, tl); mouse(QEvent.Type.MouseMove, QPointF(v.width(), v.height())); mouse(QEvent.Type.MouseButtonRelease, QPointF(v.width(), v.height()))
    assert v.to_px(v.box).width() >= PB.MIN_PX - 0.5


def test_page_size_dialog(qapp):
    from app.ui import dlg_pagebox as PB
    d = make(2, size=(842, 595))
    dlg = PB.PageSizeDialog(None, d, [1], "mm")
    assert dlg.rng.all.isChecked() and dlg.rng.pages() == [0, 1]          # 고른 쪽이 와도 모든 페이지
    assert dlg.paper.currentText().startswith("A4") and dlg.r_fixed.isChecked()
    assert dlg.cur_lbl.text() == "297.0 x 209.9 mm"
    w, h = dlg.target_size(); assert (round(w), round(h)) == (595, 842)
    dlg.orient.button(1).click()
    w, h = dlg.target_size(); assert (round(w), round(h)) == (842, 595)
    assert "조정된 페이지 크기: 297.0 x 210.0 mm" in dlg.panel.caption.text()
    dlg.r_custom.setChecked(True)
    assert dlg.w.isEnabled() and not dlg.paper.isEnabled() and dlg.orient.checkedId() == 1
    dlg.orient.button(0).click()                                          # 사용자 지정은 두 칸을 바꿔 끼움
    assert round(dlg.w.pt()) == 595 and round(dlg.h.pt()) == 842
    dlg.w.setValue(0)
    assert not dlg.ok.isEnabled()
    assert len(PB.PAPERS) == 17 + 6 + 3 + 6 + 6 + 6 + 1


def test_page_box_dialog_remembers_each_box_and_resets(qapp):
    from app.ui import dlg_pagebox as PB
    d = make(2)
    dlg = PB.PageBoxDialog(None, d, [0], "pt")
    assert dlg.which.currentData() == "crop" and not dlg.ok.isEnabled()
    dlg.spins["top"].setValue(10)
    dlg.which.setCurrentIndex(2)                       # 트림
    assert dlg.spins["top"].value() == 0
    dlg.spins["left"].setValue(5)
    dlg.which.setCurrentIndex(0)
    assert dlg.spins["top"].value() == 10 and dlg.spins["left"].value() == 0
    vals = dlg.values()
    assert set(vals["margins"]) == {"crop", "trim"} and vals["size"] is None
    dlg._reset()
    assert dlg.values()["margins"] == {} and not dlg.ok.isEnabled() and dlg.spins["top"].value() == 0


def test_page_box_dialog_sizes(qapp):
    from app.ui import dlg_pagebox as PB
    d = make(1, size=(612, 792))                       # Letter
    dlg = PB.PageBoxDialog(None, d, [0], "pt")
    items = {dlg.paper.itemText(k): dlg.paper.model().item(k).isEnabled() for k in range(dlg.paper.count())}
    assert items["없음"] and not items["A4 (210 x 297 mm)"] and items["Letter (8.5 x 11 in)"] and items["A3 (297 x 420 mm)"]
    dlg.paper.setCurrentText("Legal (8.5 x 14 in)")
    assert dlg.ok.isEnabled() and dlg.new_size() == (612, 1008)
    assert dlg.ox.maximum() == 0 and abs(dlg.oy.maximum() - 216) < 0.01
    assert dlg.m["crop"] == M()                       # 쪽과 같던 자르기 박스는 넓힌 쪽을 따라감
    dlg.r_offset.setChecked(True); dlg.oy.setValue(16)
    vals = dlg.values()
    assert vals["offset"] == (0, 16) and vals["size"] == (612, 1008)
    P.set_boxes(d, vals["pages"], vals["margins"], vals["size"], vals["offset"])
    assert keys(d, 0)["MediaBox"] == [0, -200, 612, 808]


def test_window_routes_to_new_dialogs(qapp, monkeypatch):
    from app.ui import dlg_pagebox as PB
    from app.ui.window import MainWindow
    w = MainWindow()
    seen = []
    for name in ("CropDialog", "PageSizeDialog", "PageBoxDialog"):
        monkeypatch.setattr(getattr(PB, name), "exec", lambda self, n=name: seen.append((n, self.values()["pages"])) and False)
    p = os.path.join(tempfile.mkdtemp(), "w.pdf")
    f = fitz.open()
    for _ in range(4):
        f.new_page()
    f.save(p)
    w.open_path(p)
    for key in ("crop", "resize", "pagebox"):
        w.pages_action(key, [1, 2])
    assert seen == [("CropDialog", [1, 2]), ("PageSizeDialog", [0, 1, 2, 3]), ("PageBoxDialog", [1, 2])]
    for t in list(w.tabs):
        t.doc.modified = False
    w.close()
