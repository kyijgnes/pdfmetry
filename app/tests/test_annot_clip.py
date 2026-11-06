"""주석 복사 자료·붙여넣기·여러 쪽에 적용(app/engine/annot_clip.py) 시험.

python -m pytest -q app/tests/test_annot_clip.py
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="rpdf_aclip_test_"))

import fitz  # noqa: E402
import pytest  # noqa: E402

from app.engine import annot_clip as C  # noqa: E402
from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402

P = fitz.Point


def png_file() -> str:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), 1)
    pix.set_rect(pix.irect, (200, 0, 0, 180))
    path = os.path.join(tempfile.mkdtemp(prefix="rpdf_aclip_png_"), "sign.png")
    pix.save(path)
    return path


def make_all(doc: Document) -> dict[str, int]:
    """종류마다 주석 하나(값을 기본에서 바꿔 둠)."""
    x = {}
    x["square"] = A.rect(doc, 0, fitz.Rect(50, 50, 150, 100), stroke=(0, 0, 1), fill=(1, 1, 0), width=3)
    A.update(doc, 0, x["square"], dashes=(3, 2), opacity=0.6, content="네모", author="윤", subject="검토")
    x["circle"] = A.oval(doc, 0, fitz.Rect(200, 50, 300, 120), stroke=(0, 0.5, 0), width=2)
    A.update(doc, 0, x["circle"], clouds=1)
    x["line"] = A.line(doc, 0, P(50, 150), P(200, 180), stroke=(1, 0, 0), width=2)
    A.update(doc, 0, x["line"], start=fitz.PDF_ANNOT_LE_OPEN_ARROW, end=fitz.PDF_ANNOT_LE_CIRCLE)
    x["polyline"] = A.polygon(doc, 0, [P(250, 150), P(300, 190), P(350, 150)], closed=False) \
        if "closed" in A.polygon.__code__.co_varnames else A.polygon(doc, 0, [P(250, 150), P(300, 190), P(350, 150)])
    x["ink"] = A.ink(doc, 0, [[P(50, 220), P(80, 250), P(110, 220)], [P(60, 260), P(100, 270)]], stroke=(0.3, 0, 0.6))
    x["textbox"] = A.free_text(doc, 0, fitz.Rect(200, 220, 340, 270), "글 상자\n두 줄", fontsize=13,
                               text_color=(0, 0, 0.7), fill=(0.9, 0.9, 1), border=(1, 0, 0), width=1.5, author="윤")
    x["callout"] = A.free_text(doc, 0, fitz.Rect(380, 220, 520, 260), "말풍선", fontsize=11, border=(0, 0, 0),
                               width=1, callout=P(360, 300))
    x["note"] = A.note(doc, 0, P(400, 60), "노트 내용", icon="Key", color=(0, 1, 0), author="윤")
    x["area_hl"] = A.area_highlight(doc, 0, fitz.Rect(50, 300, 200, 320), color=(0, 1, 1), opacity=0.5)
    x["highlight"] = A.text_markup(doc, 0, [fitz.Rect(220, 300, 300, 315), fitz.Rect(220, 320, 280, 335)],
                                   "Highlight", color=(1, 0.5, 0), content="강조")
    x["strike"] = A.text_markup(doc, 0, [fitz.Rect(320, 300, 400, 315)], "StrikeOut", color=(1, 0, 0))
    x["stamp"] = A.signature_image(doc, 0, fitz.Rect(50, 350, 170, 410), png_file(), author="윤")
    x["attach"] = A.attachment(doc, 0, P(250, 360), _attach_file(), icon="Tag", color=(1, 0, 1))
    x["measure"] = A.measure_annot(doc, 0, [P(300, 450), P(450, 450)], "distance", "5.29 cm", stroke=(0, 0, 1),
                                   ratio={"doc": 1, "doc_unit": "cm", "real": 2, "real_unit": "m"})
    x["area"] = A.measure_annot(doc, 0, [P(50, 500), P(150, 500), P(100, 560)], "영역", kind="area") \
        if False else A.measure_annot(doc, 0, [P(50, 500), P(150, 500), P(100, 560)], "area", "1 cm²")
    return x


def _attach_file() -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="rpdf_aclip_att_"), "메모.txt")
    with open(path, "wb") as f:
        f.write("첨부 내용".encode("utf-8"))
    return path


def near(a, b, tol=0.6) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(near(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(near(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(a - b) <= tol
    return a == b


KEYS = ("colors", "border", "opacity", "flags", "line_ends", "fontsize", "rotate", "icon", "name", "keys", "file",
        "rd", "callout")


def check_same(src: dict, got: dict, dx: float = 0, dy: float = 0):
    exp = C._map_spec(src, fitz.Matrix(1, 0, 0, 1, dx, dy))
    assert got["type"] == exp["type"]
    assert near(got["rect"], exp["rect"]), (got["rect"], exp["rect"])
    for k in ("content", "title", "subject"):
        assert got["info"].get(k, "") == exp["info"].get(k, ""), k
    assert near(got.get("vertices"), exp.get("vertices")), "vertices"
    for k in KEYS:
        assert near(got.get(k), exp.get(k), 0.02 if k == "colors" else 0.6), (exp["type"], k, got.get(k), exp.get(k))
    if exp["type"] == "Stamp":
        a, b = fitz.Pixmap(got["stamp"]["image"]), fitz.Pixmap(exp["stamp"]["image"])
        assert (a.width, a.height, a.alpha) == (b.width, b.height, b.alpha)


@pytest.fixture()
def doc():
    d = Document.blank(count=3)
    yield d


def test_spec_paste_every_kind_same_page(doc):
    xs = make_all(doc)
    specs = {k: C.annot_spec(doc, 0, x) for k, x in xs.items()}
    assert all(specs.values())
    n0 = len(doc._undo)
    new = C.paste_annots(doc, 0, list(specs.values()), 12, 20)
    assert len(new) == len(specs) and len(doc._undo) == n0 + 1          # 한 걸음
    for (k, s), x in zip(specs.items(), new):
        check_same(s, C.annot_spec(doc, 0, x), 12, 20)


def test_paste_other_document_and_page(doc):
    xs = make_all(doc)
    specs = [C.annot_spec(doc, 0, x) for x in xs.values()]
    other = Document.blank()
    new = C.paste_annots(other, 0, specs)
    assert len(new) == len(specs)
    for s, x in zip(specs, new):
        check_same(s, C.annot_spec(other, 0, x))
    new = C.paste_annots(doc, 2, specs, -5, 0)
    for s, x in zip(specs, new):
        check_same(s, C.annot_spec(doc, 2, x), -5, 0)


def test_values_after_save_reopen(doc):
    """붙여 넣은 측정 축척·첨부 파일·스탬프 이름이 저장 뒤에도 남는다."""
    xs = make_all(doc)
    src = {k: C.annot_spec(doc, 0, xs[k]) for k in ("measure", "attach", "stamp")}
    other = Document.blank()
    new = dict(zip(src, C.paste_annots(other, 0, list(src.values()))))
    back = Document.from_bytes(other.fitz.tobytes())
    m = A.measure_info(back, 0, new["measure"])
    assert m["ratio"] == "1 cm = 2 m" and m["value"] == "5.29 cm" and m["kind"] == "distance"
    a = A.get(back, 0, new["attach"])
    assert a.get_file() == "첨부 내용".encode("utf-8") and A.attach_info(a)["filename"] == src["attach"]["file"]["name"] == "메모.txt"
    assert back.fitz.xref_get_key(new["stamp"], "Subtype")[1] == "/Stamp"
    assert back.fitz.xref_get_key(new["stamp"], "Name")[1] == "/" + src["stamp"]["name"]


def test_paste_after_source_deleted_and_undo(doc):
    xs = make_all(doc)
    s = C.annot_spec(doc, 0, xs["textbox"])
    A.delete(doc, 0, xs["textbox"])
    [x] = C.paste_annots(doc, 0, [s])
    check_same(s, C.annot_spec(doc, 0, x))
    doc.undo()
    doc.undo()
    [x] = C.paste_annots(doc, 0, [s], 0, 100)
    check_same(s, C.annot_spec(doc, 0, x), 0, 100)


def test_locked_flag_kept(doc):
    x = A.rect(doc, 0, fitz.Rect(10, 10, 60, 60))
    A.update(doc, 0, x, locked=True)
    [y] = C.paste_annots(doc, 1, [C.annot_spec(doc, 0, x)])
    assert A.is_locked(doc, 1, y)


def test_replies_not_copied(doc):
    x = A.note(doc, 0, P(100, 100), "원 주석")
    A.add_reply(doc, 0, x, "답글")
    s = C.annot_spec(doc, 0, x)
    C.paste_annots(doc, 1, [s])
    assert len(list(doc.fitz[1].annots())) == 1
    r = A.replies(doc, 0, x)[0]["xref"]
    assert C.annot_spec(doc, 0, r)["reply"]
    with pytest.raises(ValueError):
        C.apply_to_pages(doc, 0, r, [1])


def test_skip_kinds(doc):
    A.add_link(doc, 0, fitz.Rect(10, 10, 50, 30), uri="https://example.com")
    lx = A._link_xrefs(doc, 0)[0]
    assert C.annot_spec(doc, 0, lx) is None
    assert C.annot_spec(doc, 0, 99999) is None
    assert C.paste_annots(doc, 0, [None]) == []


# ── 여러 쪽에 적용 ───────────────────────────────────────────────
def test_can_apply():
    for k in ("Square", "Circle", "Line", "PolyLine", "Polygon", "Ink", "FreeText", "Stamp", "Text"):
        assert C.can_apply(k)
    for k in ("Highlight", "Underline", "StrikeOut", "Squiggly", "Redact", "FileAttachment", "Caret", "Link", "Widget"):
        assert not C.can_apply(k)


def test_apply_to_pages_one_step_and_skip():
    d = Document.blank(count=4)
    d.fitz.new_page(width=100, height=100)                 # 4: 작은 쪽(자리가 쪽 밖)
    x = A.rect(d, 0, fitz.Rect(300, 600, 400, 700), stroke=(1, 0, 0), width=2)
    d._undo.clear()
    n, skipped = C.apply_to_pages(d, 0, x, range(d.page_count))
    assert (n, skipped) == (3, 1) and len(d._undo) == 1
    src = C.annot_spec(d, 0, x)
    for j in (1, 2, 3):
        [a] = list(d.fitz[j].annots())
        check_same(src, C.annot_spec(d, j, a.xref))
    assert not list(d.fitz[4].annots())
    d.undo()
    assert all(not list(d.fitz[j].annots()) for j in (1, 2, 3))
    assert len(list(d.fitz[0].annots())) == 1


def test_apply_to_rotated_page_same_screen_spot():
    d = Document.blank(count=2)
    d.fitz[1].set_rotation(90)
    src_r = fitz.Rect(100, 100, 200, 150)
    x = A.free_text(d, 0, src_r, "같은 자리", fontsize=12, border=(0, 0, 0), width=1)
    assert C.apply_to_pages(d, 0, x, [1]) == (1, 0)
    t = d.fitz[1]
    [a] = list(t.annots())
    vis_src = fitz.Rect(A.get(d, 0, x).rect) * d.fitz[0].rotation_matrix
    vis_new = fitz.Rect(a.rect) * t.rotation_matrix
    assert near(list(vis_src.normalize()), list(vis_new.normalize()), 1.0)
    assert C.annot_spec(d, 1, a.xref)["rotate"] == 90              # 돌린 쪽에서도 화면에 바로 선다


def test_apply_refused_kinds(doc):
    h = A.text_markup(doc, 0, [fitz.Rect(10, 10, 100, 30)], "Underline")
    with pytest.raises(ValueError):
        C.apply_to_pages(doc, 0, h, [1, 2])
    assert not doc.fitz[1].first_annot


def test_apply_every_allowed_kind(doc):
    xs = make_all(doc)
    for k, x in xs.items():
        s = C.annot_spec(doc, 0, x)
        if not C.can_apply(s["type"]):
            continue
        before = {a.xref for a in doc.fitz[1].annots()}
        assert C.apply_to_pages(doc, 0, x, [1]) == (1, 0), k
        [y] = [a.xref for a in doc.fitz[1].annots() if a.xref not in before]
        check_same(s, C.annot_spec(doc, 1, y))
