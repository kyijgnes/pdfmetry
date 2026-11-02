"""스탬프·서명·측정 시험: 진짜 Stamp 주석, 동적 스탬프의 이름·날짜, 사용자 스탬프·서명 저장, 축척 측정, 스냅,
옛 글상자 스탬프 호환, 창(offscreen)에서 한 번 눌러 찍기·끌어 찍기·측정 설정.

python -m pytest app/tests/test_stamps.py
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_stamp_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import annots as A  # noqa: E402
from app.engine import stamps as S  # noqa: E402
from app.engine.document import Document  # noqa: E402

NOW = dt.datetime(2026, 11, 2, 14, 20)
CM = 72 / 2.54


def _pdf(n: int = 2, rotate: int = 0) -> str:
    p = os.path.join(tempfile.mkdtemp(), "스탬프시험.pdf")
    d = fitz.open()
    for _ in range(n):
        pg = d.new_page()
        if rotate:
            pg.set_rotation(rotate)
    d.save(p); d.close()
    return p


def _reopen(d: Document) -> fitz.Document:
    out = os.path.join(tempfile.mkdtemp(), "saved.pdf")
    d.fitz.save(out)
    return fitz.open(out)


_KEEP: list = []


def _ann(f: fitz.Document, i: int = 0) -> list:
    """쪽의 주석 목록(쪽 개체를 붙잡아 둔다. 놓으면 주석이 쪽에서 떨어진다)."""
    pg = f[i]
    _KEEP.append(pg)
    return list(pg.annots())


def _ap_image(f: fitz.Document, xref: int) -> bool:
    """외관이 우리 그림(/I 그림 XObject)을 그리는가."""
    t, ap = f.xref_get_key(xref, "AP")
    m = re.search(r"/N (\d+) 0 R", ap or "")
    return bool(m) and f.xref_get_key(int(m.group(1)), "Resources/XObject/I")[0] == "xref"


def _ink_in(f: fitz.Document, page: int, r: fitz.Rect) -> int:
    """그 네모 안 흰색이 아닌 화소 수."""
    pix = f[page].get_pixmap(clip=r, dpi=72)
    return sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i:i + 3] != b"\xff\xff\xff")


@pytest.fixture()
def store():
    st = S.StampStore(tempfile.mkdtemp())
    return st


# ── 표준 스탬프 ───────────────────────────────────────────────────
def test_standard_stamp_is_real_stamp_annot(store):
    d = Document(_pdf())
    r = fitz.Rect(100, 100, 260, 140)
    x = S.add(d, 0, r, {"group": "standard", "id": "Approved"}, author="윤성지", st=store)
    f = _reopen(d)
    a = next(a for a in _ann(f) if a.xref == x)
    assert a.type[1] == "Stamp"
    assert f.xref_get_key(x, "Name")[1] == "/Approved"
    assert a.info["content"] == "승인됨" and a.info["title"] == "윤성지"
    assert _ap_image(f, x)
    assert _ink_in(f, 0, a.rect) > 50


def test_all_builtin_stamps_draw(store):
    d = Document(_pdf())
    y = 20
    for g, items in S.BUILTIN.items():
        for sid, *_ in items:
            r = S.default_rect(d, 0, fitz.Point(300, y), {"group": g, "id": sid}, author="a", st=store)
            S.add(d, 0, r, {"group": g, "id": sid}, author="a", now=NOW, st=store)
            y += 28
    assert len(list(_ann(d.fitz))) == sum(len(v) for v in S.BUILTIN.values())
    assert all(a.type[1] == "Stamp" for a in _ann(d.fitz))
    assert len(S.GROUPS) == 5 and len(S.STANDARD) == 12 and len(S.SIGN) == 5 and len(S.DYNAMIC) == 5


def test_appearance_survives_update_and_move(store):
    d = Document(_pdf())
    x = S.add(d, 0, fitz.Rect(100, 100, 260, 140), {"group": "standard", "id": "Draft"}, st=store)
    A.update(d, 0, x, opacity=0.5)
    x = A.update(d, 0, x, rect=fitz.Rect(150, 300, 330, 350))
    f = _reopen(d)
    a = next(a for a in _ann(f) if a.xref == x)
    assert _ap_image(f, x) and f.xref_get_key(x, "Name")[1] == "/Draft"
    assert abs(a.rect.x0 - 150) < 1 and _ink_in(f, 0, a.rect) > 50


def test_dynamic_stamp_has_name_and_date(store):
    fc = S.face({"group": "dynamic", "id": "Reviewed"}, author="윤성지", now=NOW, st=store)
    assert fc["lines"][0][0] == "REVIEWED"
    assert fc["lines"][1][0] == "윤성지, 2026.11.02 14:20"
    store.set_identity("김영편입", "자연관")
    fc = S.face({"group": "digital", "id": "Identity"}, author="윤성지", now=NOW, st=store)
    assert [t for t, _ in fc["lines"]] == ["윤성지", "김영편입 자연관", "2026.11.02 14:20"]


def test_rotated_page_keeps_display_size(store):
    d = Document(_pdf(rotate=90))
    spec = {"group": "sign", "id": "SignHere"}
    r = S.default_rect(d, 0, fitz.Point(300, 400), spec, st=store)
    w, h = S.natural_size(S.face(spec, st=store))
    assert abs(r.width - h) < 1 and abs(r.height - w) < 1    # 쪽이 돌아 있어 쪽 좌표에서는 가로·세로가 바뀐다
    S.add(d, 0, r, spec, st=store)


def test_click_size_remembers_drag(store):
    d = Document(_pdf())
    spec = {"group": "standard", "id": "Final"}
    w, h = S.natural_size(S.face(spec, st=store))
    S.remember_drag(d, 0, fitz.Rect(0, 0, w * 2, h * 2), spec, st=store)
    r = S.default_rect(d, 0, fitz.Point(300, 300), spec, st=store)
    assert abs(r.width - w * 2) < 0.5
    assert S.StampStore(store.folder).scale == pytest.approx(2.0, abs=0.01)   # 다시 읽어도


# ── 사용자 스탬프 · 서명 저장 ──────────────────────────────────────
def _png(path: str):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 30), 1)
    pix.clear_with(0); pix.set_rect(fitz.IRect(5, 5, 55, 25), (0, 0, 200, 255))
    pix.save(path)
    return path


def test_custom_stamps_and_signatures_persist(store):
    img = _png(os.path.join(tempfile.mkdtemp(), "도장.png"))
    cid = store.put_custom({"type": "image", "name": "내 도장", "category": "결재", "width": 80, "height": 40},
                           S.image_png(img))
    did = store.put_custom({"type": "dynamic", "name": "받음", "style": "circle", "color": "blue", "text": "받음",
                            "date_fmt": "%Y/%m/%d", "identity": "name"})
    sig = store.add_signature(open(img, "rb").read(), "draw")
    again = S.StampStore(store.folder)        # 앱을 다시 켠 것처럼
    assert [e["name"] for e in again.custom()] == ["내 도장", "받음"]
    assert again.categories() == ["결재"]
    assert [os.path.basename(sig)] == [e["file"] for e in again.signatures()]
    fc = S.face({"group": "custom", "id": did}, author="윤성지", now=NOW, st=again)
    assert fc["shape"] == "circle" and fc["lines"][1][0] == "윤성지, 2026/11/02"
    d = Document(_pdf())
    x1 = S.add(d, 0, S.default_rect(d, 0, fitz.Point(200, 200), {"group": "custom", "id": cid}, st=again),
               {"group": "custom", "id": cid}, st=again)
    x2 = S.add(d, 0, fitz.Rect(100, 400, 250, 460), {"group": "signature", "file": sig}, author="윤성지", st=again)
    f = _reopen(d)
    kinds = {a.xref: (a.type[1], a.info["subject"]) for a in _ann(f)}
    assert kinds[x1] == ("Stamp", "스탬프") and kinds[x2] == ("Stamp", "서명")
    again.delete_custom(cid); again.delete_signature(again.signatures()[0]["id"])
    assert len(S.StampStore(store.folder).custom()) == 1 and not S.StampStore(store.folder).signatures()


def test_pdf_page_as_custom_stamp():
    src = os.path.join(tempfile.mkdtemp(), "원본.pdf")
    d = fitz.open(); d.new_page(width=200, height=100).insert_text((20, 50), "PAGE"); d.save(src); d.close()
    png = S.pdf_page_png(src, 0)
    assert fitz.Pixmap(png).width > 200
    with pytest.raises(ValueError):
        S.image_png(src + ".없음")


def test_old_freetext_stamp_still_opens():
    d = Document(_pdf())
    x = A.stamp_text(d, 0, fitz.Rect(100, 600, 200, 640), "승인")
    f = _reopen(d)
    a = _ann(f)[0]
    assert a.type[1] == "FreeText" and a.info["subject"] == "Stamp"
    assert S.is_stamp(Document(f.name), x)


# ── 측정 ─────────────────────────────────────────────────────────
def test_scale_2cm_is_1m():
    ratio = {"doc": 2, "doc_unit": "cm", "real": 1, "real_unit": "m"}
    pts = [fitz.Point(100, 100), fitz.Point(100 + 2 * CM, 100)]
    assert A.measure(pts, "distance", ratio=ratio) == "1.00 m"
    sq = [fitz.Point(0, 0), fitz.Point(4 * CM, 0), fitz.Point(4 * CM, 2 * CM), fitz.Point(0, 2 * CM)]
    assert A.measure(sq, "area", ratio=ratio) == "2.00 m²"
    assert A.measure(sq, "perimeter", ratio=ratio) == "5.00 m"     # 세 변 4+2+4cm(닫지 않음)
    assert A.ratio_text(ratio) == "2 cm = 1 m"
    assert list(A.MEASURE_UNITS) == ["pt", "in", "mm", "cm", "p", "ft", "yd", "m", "km", "mile", "µm"]
    assert A.measure([fitz.Point(0, 0), fitz.Point(72, 0)], "distance", 1.0, "in") == "1.00 in"   # 옛 부르는 꼴
    assert A.measure_ratio({"doc": 0, "real": 5e9})["doc"] == 0.01


def test_measure_annot_keeps_scale_after_reopen():
    d = Document(_pdf())
    ratio = {"doc": 2, "doc_unit": "cm", "real": 1, "real_unit": "m"}
    pts = [fitz.Point(100, 100), fitz.Point(100 + 2 * CM, 100)]
    x = A.measure_annot(d, 0, pts, "distance", "1.00 m", (1, 0, 0), "a", ratio=ratio)
    y = A.measure_annot(d, 0, [fitz.Point(100, 300), fitz.Point(200, 300), fitz.Point(200, 400)], "perimeter",
                        "둘레 1 m", (1, 0, 0), ratio=ratio)
    f = _reopen(d)
    a = next(a for a in _ann(f) if a.xref == x)
    assert a.line_ends == (fitz.PDF_ANNOT_LE_CLOSED_ARROW, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
    assert a.colors["stroke"] == a.colors["fill"] == [1.0, 0.0, 0.0] and a.border["width"] == 1
    assert f.xref_get_key(x, "Cap")[1] == "true"
    b = next(a for a in _ann(f) if a.xref == y)
    assert b.line_ends == (fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_OPEN_ARROW)
    assert f.xref_get_key(y, "Cap")[0] == "null"         # 한글 라벨은 캡션 없이
    info = A.measure_info(Document(f.name), 0, x)
    assert info == {"ratio": "2 cm = 1 m", "value": "1.00 m", "kind": "distance"}


def test_snap_endpoints_and_midpoints():
    d = fitz.open(); pg = d.new_page()
    pg.draw_line((100, 100), (300, 100))
    pts = A.snap_points(pg)
    assert A.nearest_snap(pts, fitz.Point(103, 102), 8) == fitz.Point(100, 100)
    assert A.nearest_snap(pts, fitz.Point(198, 97), 8) == fitz.Point(200, 100)
    assert A.nearest_snap(pts, fitz.Point(198, 97), 8, {"end": True, "mid": False}) is None
    assert A.nearest_snap(pts, fitz.Point(150, 150), 8) is None


# ── 창 ──────────────────────────────────────────────────────────
def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _mouse(v, kind, page_pt: fitz.Point, page: int = 0):
    sp = v.from_page_point(page, page_pt)
    vp = QPointF(v.mapFromScene(sp))
    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease}[kind]
    btn = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    e = QMouseEvent(t, vp, QPointF(v.viewport().mapToGlobal(vp.toPoint())), btn, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
    getattr(v, {"press": "mousePressEvent", "move": "mouseMoveEvent", "release": "mouseReleaseEvent"}[kind])(e)
    pump(0.02)


@pytest.fixture()
def win():
    from app.ui.window import MainWindow
    S.reset_store()
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


def test_click_and_drag_place_stamp(win):
    win.open_path(_pdf()); pump(0.2)
    t = win.cur(); v = t.viewer
    v.props["stamp_spec"] = {"group": "standard", "id": "Confidential"}
    win.on_tool("stamp_go")
    assert v.tool == "stamp"
    _mouse(v, "press", fitz.Point(300, 300)); _mouse(v, "release", fitz.Point(300, 300))
    a = [x for x in _ann(t.doc.fitz)]
    assert len(a) == 1 and a[0].type[1] == "Stamp"
    assert abs((a[0].rect.x0 + a[0].rect.x1) / 2 - 300) < 1
    win.on_tool("stamp_go")
    _mouse(v, "press", fitz.Point(100, 500)); _mouse(v, "move", fitz.Point(300, 560))
    _mouse(v, "release", fitz.Point(300, 560))
    a = [x for x in _ann(t.doc.fitz)]
    assert len(a) == 2 and abs(a[1].rect.width - 200) < 2


def test_measure_tool_uses_settings(win):
    win.open_path(_pdf()); pump(0.2)
    t = win.cur(); v = t.viewer
    win.prefs["measure"] = {"doc": 2, "doc_unit": "cm", "real": 1, "real_unit": "m", "label": "벽",
                            "snap": {"end": False, "mid": False}}
    win.on_tool("m_distance")
    _mouse(v, "press", fitz.Point(100, 100)); _mouse(v, "move", fitz.Point(100 + 2 * CM, 100))
    _mouse(v, "release", fitz.Point(100 + 2 * CM, 100))
    a = _ann(t.doc.fitz)[0]
    assert a.info["content"] == "벽 1.00 m"
    assert A.measure_info(t.doc, 0, a.xref)["ratio"] == "2 cm = 1 m"


def test_dialogs_build_and_save(win):
    from app.ui import dlg_measure, dlg_signature, dlg_stamp
    st = S.StampStore(tempfile.mkdtemp())
    m = QMenu()
    picked = []
    dlg_stamp.fill_stamp_menu(m, picked.append, lambda k: None)
    names = [a.text() for a in m.actions()]
    assert names[:5] == [g[1] for g in S.GROUPS] and "스탬프 관리" in names and "ID 편집" in names
    std = next(a for a in m.actions() if a.text() == "표준 비즈니스").menu()
    std.actions()[0].trigger()
    assert picked == [{"group": "standard", "id": "Approved"}]

    dyn = dlg_stamp.DynamicStampDialog(win, "윤성지", st=st)
    dyn.text.setText("받았음"); dyn.styles.button(4).click()
    dyn.accept()
    assert st.get_custom(dyn.saved_id)["style"] == "circle"

    sig = dlg_signature.SignatureDialog(win, st=st)
    sig.pad.strokes = [[QPointF(20, 100), QPointF(200, 60), QPointF(400, 120)]]
    sig.accept()
    assert sig.path and os.path.exists(sig.path) and len(st.signatures()) == 1
    sig2 = dlg_signature.SignatureDialog(win, st=st)
    sig2.tabs.setCurrentIndex(1); sig2.type_text.setText("윤성지")
    assert sig2.new_image()[0] is not None
    sig2.accept()
    assert len(st.signatures()) == 2

    md = dlg_measure.MeasureDialog(win, {"doc": 2, "doc_unit": "cm", "real": 1, "real_unit": "m"})
    assert not md.snaps["path"].isEnabled() and md.snaps["end"].isChecked()
    vals = md.values()
    assert A.ratio_text(vals) == "2 cm = 1 m" and vals["snap"]["mid"] is True
