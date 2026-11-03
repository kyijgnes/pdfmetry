"""라운드 14 빈틈 시험: 인쇄·속성 패널의 피카스 단위, 보기 회전한 화면의 스탬프·서명·글상자 고치기.

python -m pytest app/tests/test_r14_gaps.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_r14_gaps_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine import stamps as S  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui import dlg_pagebox as PB  # noqa: E402
from app.ui import printing as PR  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

W, H = 595, 842


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _pdf(rotate: int = 0, image: bool = False) -> str:
    p = os.path.join(tempfile.mkdtemp(), "빈틈시험.pdf")
    d = fitz.open()
    pg = d.new_page(width=W, height=H)
    if image:
        pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 50), False)
        pm.clear_with(200)
        pg.insert_image(fitz.Rect(300, 300, 400, 350), pixmap=pm)
    pg.set_rotation(rotate)
    d.save(p); d.close()
    return p


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


@pytest.fixture()
def store():
    return S.StampStore(tempfile.mkdtemp())


def _mouse(v, kind, sp: QPointF):
    vp = QPointF(v.mapFromScene(sp))
    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove, "release": QEvent.Type.MouseButtonRelease}[kind]
    held = Qt.MouseButton.LeftButton if kind != "release" else Qt.MouseButton.NoButton
    e = QMouseEvent(t, vp, QPointF(v.viewport().mapToGlobal(vp.toPoint())),
                    Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton, held,
                    Qt.KeyboardModifier.NoModifier)
    getattr(v, {"press": "mousePressEvent", "move": "mouseMoveEvent", "release": "mouseReleaseEvent"}[kind])(e)
    pump(0.02)


# ── 1. 인쇄 창 피카스 ──────────────────────────────────────────────
def test_print_units_same_as_pagebox():
    assert list(PR._UNITS) == list(PB.UNITS) and PR._UNITS["pc"] == 12.0
    d = fitz.open(); d.new_page()
    dlg = PR.PrintDialog(None, d, 0, None, unit="pc")
    assert dlg.unit == "pc"   # 피카스가 cm 로 떨어지지 않는다
    assert dlg.overlap.suffix() == " (pc)" and dlg.overlap.decimals() == 2
    dlg.overlap.setValue(1.0)   # 1 pc = 12 pt
    assert dlg.overlap.value() * PR._UNITS[dlg.unit] == pytest.approx(12.0)
    dlg.deleteLater()


# ── 2. 속성 패널 피카스 자릿수 ─────────────────────────────────────
def test_props_image_size_pc_two_decimals(win):
    win.prefs["unit"] = "pc"
    win.open_path(_pdf(image=True)); pump(0.2)
    t = win.cur(); v = t.viewer; cp = t.props.edit_props
    win.on_toggle("edit_all", True); pump(0.02)
    sp = v.from_page_point(0, fitz.Point(350, 330))
    _mouse(v, "press", sp); _mouse(v, "release", sp)
    assert v.sel.kind == "image"
    assert cp.w.suffix() == " pc" and cp.w.decimals() == 2 and cp.w.singleStep() == pytest.approx(0.1)
    assert cp.w.decimals() == PB.UnitBox("cm", 100).decimals()   # cm 와 같은 자릿수
    assert cp.w.value() == pytest.approx(100 / 12, abs=0.01)


# ── 3. 보기 회전한 화면의 스탬프·서명 ───────────────────────────────
def _ink(pix: fitz.Pixmap) -> list[int]:
    """작게 줄인 회색 화소."""
    g = fitz.Pixmap(fitz.csGRAY, pix) if pix.n > 1 else pix
    g = fitz.Pixmap(g, 40, 40, None)
    return list(g.samples)


def _shot(d: Document, r: fitz.Rect, rot: int) -> list[int]:
    """쪽을 rot 만큼 돌려 본 화면에서 그 네모 안."""
    return _ink(d.fitz[0].get_pixmap(matrix=fitz.Matrix(2, 2).prerotate(rot), clip=r))


def _diff(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


@pytest.mark.parametrize("spec", [{"group": "standard", "id": "Approved"}, {"group": "sign", "id": "SignHere"}])
@pytest.mark.parametrize("view_rot", [90, 270])
def test_stamp_upright_in_rotated_view(store, spec, view_rot):
    ref = Document(_pdf())
    r0 = S.default_rect(ref, 0, fitz.Point(300, 400), spec, st=store)
    S.add(ref, 0, r0, spec, st=store)
    d = Document(_pdf())
    r = S.default_rect(d, 0, fitz.Point(300, 400), spec, st=store, view_rot=view_rot)
    assert abs(r.width - r0.height) < 1 and abs(r.height - r0.width) < 1   # 돌린 화면에서 같은 크기로 보이게
    S.add(d, 0, r, spec, st=store, view_rot=view_rot)
    want = _shot(ref, r0, 0)
    assert _diff(_shot(d, r, view_rot), want) < 12              # 돌린 화면에서 바로 선다
    assert _diff(_shot(d, r, view_rot), want) < _diff(_shot(d, r, (view_rot + 180) % 360), want)


def test_stamp_rotated_file_page_and_view(store):
    """파일이 90° 돈 쪽을 화면에서 또 90° 돌리면 180° 로 굽는다."""
    spec = {"group": "standard", "id": "Approved"}
    d = Document(_pdf(rotate=90))
    w, h = S.natural_size(S.face(spec, st=store))
    r = S.default_rect(d, 0, fitz.Point(300, 400), spec, st=store, view_rot=90)
    assert abs(r.width - w) < 1 and abs(r.height - h) < 1
    r = S.default_rect(d, 0, fitz.Point(5000, 5000), spec, st=store, view_rot=90)
    assert fitz.Rect(0, 0, W, H).contains(r)   # 쪽 밖으로 안 나간다


def test_stamp_click_in_rotated_view_passes_view_rotation(win, monkeypatch):
    win.open_path(_pdf()); pump(0.2)
    t = win.cur(); v = t.viewer
    v.set_view_rotation(90); pump(0.1)
    spec = {"group": "standard", "id": "Approved"}
    seen = []
    real = S.add
    monkeypatch.setattr(S, "add", lambda *a, **k: (seen.append(k.get("view_rot")), real(*a, **k))[1])
    v.props["stamp_spec"] = spec
    win.on_tool("stamp_go"); pump(0.02)
    assert v.tool == "stamp"
    sp = v.from_page_point(0, fitz.Point(300, 400))
    v.ensureVisible(v.from_page_rect(0, fitz.Rect(250, 350, 350, 450)), 10, 10); pump(0.02)
    _mouse(v, "press", sp); _mouse(v, "release", sp)
    pump(0.05)
    assert seen == [90]
    pg = t.doc.fitz[0]
    stamps = [a for a in pg.annots() if a.type[1] == "Stamp"]
    assert len(stamps) == 1
    r = fitz.Rect(stamps[0].rect)
    assert r.height > r.width   # 가로로 긴 스탬프가 돌린 화면에서 가로로 보이게 쪽에서는 세로로 길다


# ── 4. 보기 회전한 화면에서 글상자 고치기 ──────────────────────────
def _edit(win, rotate_text: int, view_rot: int, box=fitz.Rect(100, 150, 300, 190)):
    from app.ui.inline_annot import InlineAnnot
    win.open_path(_pdf()); pump(0.2)
    t = win.cur(); v = t.viewer
    x = A.free_text(t.doc, 0, box, "고칠 글", 12, (0, 0, 0), None, (0, 0, 0), 1, rotate=rotate_text)
    v.set_view_rotation(view_rot); pump(0.1)
    info = A.freetext_info(t.doc, 0, x)
    got = []
    ed = InlineAnnot(v, 0, info["kind"], info, lambda *a: got.append(a), box=info["box"], text=info["text"],
                     callout=info["callout"])
    pump(0.05)
    return v, info, ed, got


@pytest.mark.parametrize("view_rot", [90, 180, 270])
def test_edit_box_turns_with_view(win, view_rot):
    v, info, ed, got = _edit(win, 0, view_rot)
    assert ed.box.rotation() == view_rot   # 글이 돌아 보이는 만큼 고치기 상자도 돈다
    want = v.from_page_rect(0, info["box"])
    sr = ed._scene_rect()
    tol = 4 * v.zoom
    assert abs(sr.left() - want.left()) < tol + abs(sr.width() - want.width())
    assert abs(sr.top() - want.top()) < tol + abs(sr.height() - want.height())
    # 모양: 돌린 화면에서 상자는 쪽 상자와 같은 방향으로 길다
    assert (sr.width() > sr.height()) == (want.width() > want.height())
    ed.finish(); pump(0.05)
    rect = got[0][1]
    _v0, _i0, ed0, got0 = _edit(win, 0, 0)   # 돌리지 않은 화면에서 고친 것과 같은 자리·크기로 넣는다
    ed0.finish(); pump(0.05)
    ref = got0[0][1]
    assert all(abs(a - b) < 2 for a, b in zip(rect, ref)), (rect, ref)
    assert abs(rect.x0 - info["box"].x0) < 2 and abs(rect.y0 - info["box"].y0) < 2   # 글이 시작하는 모서리는 그대로


def test_edit_box_upright_when_text_matches_view(win):
    """돌린 화면에서 만든 글(글 방향 = 보기 회전)은 고칠 때 상자가 안 돈다."""
    v, info, ed, got = _edit(win, 90, 90, box=fitz.Rect(100, 150, 140, 350))
    assert ed.box.rotation() == 0
    want = v.from_page_rect(0, info["box"])
    sr = ed._scene_rect()
    assert sr.width() > sr.height() and want.width() > want.height()
    ed.finish(); pump(0.05)
    rect = got[0][1]
    b = info["box"]
    assert abs(rect.x0 - b.x0) < 10 and abs(rect.y0 - b.y0) < 6 and abs(rect.y1 - b.y1) < 6, (rect, b)
