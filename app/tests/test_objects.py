"""편집 객체 엔진(app/engine/objects.py) 시험: 표식 줄·들어올리기·쌓는 순서·복사 자료·맞춤 분배.

python -m pytest -q app/tests/test_objects.py
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="rpdf_obj_test_"))

import fitz  # noqa: E402
import pytest  # noqa: E402

from app.engine import annots as A  # noqa: E402
from app.engine import objects as O  # noqa: E402
from app.engine.document import Document  # noqa: E402


def png(color=(255, 0, 0), alpha=255, w=20, h=10, corner=True) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), 1)
    pix.set_rect(pix.irect, (*color, alpha))
    for x in range(w // 2 if corner else 0):          # 왼쪽 위 칸만 다른 색: 돌림·뒤집힘이 화면에 드러나게
        for y in range(h // 2):
            pix.set_pixel(x, y, (0, 0, 255, alpha))
    return pix.tobytes("png")


def render(doc: Document, page: int = 0, clip=None) -> bytes:
    return doc.fitz[page].get_pixmap(matrix=fitz.Matrix(1, 1), clip=clip).samples


def diff(a: bytes, b: bytes) -> int:
    """다른 바이트 수(글자 가장자리 흐림 정도는 견딘다)."""
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 40) + abs(len(a) - len(b))


def image_order(doc: Document, page: int = 0) -> list[tuple]:
    """get_bboxlog 로 읽은 그림 그리는 차례(왼쪽 위 x 값)."""
    return [round(b[0]) for t, b in doc.fitz[page].get_bboxlog() if t == "fill-image"]


@pytest.fixture()
def three():
    """겹친 원문 그림 셋(x=100·140·180) 위에 글 한 줄."""
    d = Document.blank()
    p = d.fitz[0]
    for x, c in ((100, (255, 0, 0)), (140, (0, 160, 0)), (180, (0, 0, 255))):
        p.insert_image(fitz.Rect(x, 100, x + 100, 200), stream=png(c), keep_proportion=False)
    p.insert_text((100, 300), "Hello objects", fontsize=14)
    d.snapshot = d.snapshot   # noqa: PLW0127 (실제 snapshot 을 그대로 쓴다)
    return d


def make_text_object(doc: Document, page: int, rect: fitz.Rect, text: str, oid: int = 0) -> int:
    """덩어리 나가 쓸 글 객체 모양을 흉내 낸다: insert_htmlbox 로 Form 을 넣고 그 줄을 표식 줄로 바꾼다."""
    f = doc.fitz
    p = f[page]
    p.insert_htmlbox(rect, f"<p>{text}</p>")
    cx = p.get_contents()[-1]
    data = f.xref_stream(cx)
    import re
    m = re.search(rb"/(fzFrm\d+)\s+Do", data)
    name = m.group(1).decode()
    segs, _ = O._parse(O._joined(f, p))
    oid = oid or O._next_id(segs)
    pre = data[:data.rfind(b"q", 0, m.start())]
    cm = re.search(rb"q\s+(.*?)\s+cm", data[pre.__len__():])
    mat = cm.group(1).decode() + " cm " if cm else ""
    line = f"/RPDFObj <</Id {oid} /K /T>> BDC q {mat}/{name} Do Q EMC".encode()
    f.update_stream(cx, pre + line + b"\n")
    form = dict((x[1], x[0]) for x in p.get_xobjects())[name]
    f.xref_set_key(form, "RPDFText", fitz.get_pdf_str('{"text": "%s", "fontsize": 11}' % text))
    return oid


# ── 표식 줄 ──────────────────────────────────────────────────────
def test_parse_variants():
    """공백 없는 꼴 · q … Q 없이 /이름 Do 만 남은 꼴 · 글자열·인라인 그림 안의 q·Q 는 건너뜀."""
    data = (b"q (a ( q ) Q) Tj BI /W 1 /H 1 ID \x00Q\x00 EI Q\n"
            b"/RPDFObj<</Id 2/K/I>>BDC q 10 0 0 10 5 5 cm /RPDFA50 gs /Im1 Do Q EMC\n"
            b"/RPDFObj <</Id 7 /K /T>> BDC /Fm3 Do EMC")
    segs, dos = O._parse(data)
    objs = [s for s in segs if s["t"] == "obj"]
    assert [(s["id"], s["k"]) for s in objs] == [(2, "I"), (7, "T")]
    assert segs[0]["t"] == "raw" and segs[0]["ok"]
    m, gs, name = O._inner(data, objs[0])
    assert tuple(m) == (10, 0, 0, 10, 5, 5) and gs == "RPDFA50" and name == "Im1"
    m, gs, name = O._inner(data, objs[1])
    assert tuple(m) == (1, 0, 0, 1, 0, 0) and gs is None and name == "Fm3"
    assert [d["name"] for d in dos] == ["Im1", "Fm3"] and all(d["obj"] for d in dos)


def test_found_by_id_after_redaction(three):
    """교정(apply_redactions) 뒤 스트림·자원 이름이 바뀌어도 Id 로 같은 자리의 객체가 찾아진다."""
    d = three
    first = O.list_objects(d, 0)
    img = next(o for o in first if o["kind"] == "image" and round(o["rect"].x0) == 140)
    oid = O.lift(d, 0, img)
    rect0 = next(o for o in O.list_objects(d, 0) if o["id"] == oid)["rect"]
    names0 = set(O._names(d.fitz[0]))
    p = d.fitz[0]
    p.add_redact_annot(fitz.Rect(90, 290, 130, 310))
    p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    p = d.fitz[0]
    assert set(O._names(p)) != names0                   # 자원 이름이 바뀌었다
    again = [o for o in O.list_objects(d, 0) if o["id"] == oid]
    assert len(again) == 1 and O._close(again[0]["rect"], rect0, 0.5)
    p.clean_contents()
    again = [o for o in O.list_objects(d, 0) if o["id"] == oid]
    assert len(again) == 1 and O._close(again[0]["rect"], rect0, 0.5)
    # 옮기기도 그 뒤에 Id 로
    O.move(d, 0, [oid], 10, 0)
    r = next(o for o in O.list_objects(d, 0) if o["id"] == oid)["rect"]
    assert abs(r.x0 - rect0.x0 - 10) < 0.5


def test_ids_unique_per_page(three):
    d = three
    imgs = [o for o in O.list_objects(d, 0) if o["kind"] == "image"]
    ids = [O.lift(d, 0, o) for o in imgs]
    assert len(set(ids)) == 3 and sorted(ids) == [1, 2, 3]
    assert all(o["ours"] for o in O.list_objects(d, 0) if o["kind"] == "image")


# ── 들어올리기 ───────────────────────────────────────────────────
def test_lift_keeps_screen(three):
    d = three
    before = render(d)
    for o in [o for o in O.list_objects(d, 0) if o["kind"] == "image"]:
        O.lift(d, 0, o)
    assert diff(render(d), before) == 0
    assert image_order(d) == [100, 140, 180]


def test_lift_background_goes_behind():
    """글 아래 깔린 배경 그림은 원문 덩이 앞(before)으로 들어올려 글이 그대로 보인다."""
    d = Document.blank()
    p = d.fitz[0]
    p.insert_image(fitz.Rect(50, 50, 400, 400), stream=png((255, 255, 0)), keep_proportion=False)
    p.insert_text((100, 200), "On top of the image", fontsize=20)
    before = render(d)
    oid = O.lift(d, 0, O.find(d, 0, fitz.Rect(50, 50, 400, 400), "image"))
    assert diff(render(d), before) == 0
    seq = [o["kind"] for o in O.list_objects(d, 0)]
    assert seq == ["image", "text"] and oid == 1


def test_lift_rotated_flipped_page_offset():
    """돌린 쪽 · 미디어 상자가 0 에서 시작하지 않는 쪽 · 원래 행렬이 돌아간 그림도 같은 모양."""
    d = Document.blank()
    p = d.fitz[0]
    p.set_mediabox(fitz.Rect(20, 30, 615, 872))
    p.set_rotation(90)
    p.insert_image(fitz.Rect(100, 100, 250, 180), stream=png(), rotate=90, keep_proportion=False)
    before = render(d)
    O.lift(d, 0, next(o for o in O.list_objects(d, 0) if o["kind"] == "image"))
    assert diff(render(d), before) == 0


def test_image_in_form_refused():
    d = Document.blank()
    src = fitz.open()
    sp = src.new_page()
    sp.insert_image(fitz.Rect(100, 100, 200, 200), stream=png(), keep_proportion=False)
    d.fitz[0].show_pdf_page(fitz.Rect(0, 0, 595, 842), src, 0)
    item = next(o for o in O.list_objects(d, 0) if o["kind"] == "image")
    content = O._joined(d.fitz, d.fitz[0])
    with pytest.raises(ValueError, match="순서를 바꿀 수 없습니다"):
        O.order(d, 0, item, "front")
    assert not d.can_undo() and O._joined(d.fitz, d.fitz[0]) == content


# ── 쌓는 순서 ────────────────────────────────────────────────────
def test_order_four(three):
    d = three
    objs = {round(o["rect"].x0): o for o in O.list_objects(d, 0) if o["kind"] == "image"}
    a = O.order(d, 0, objs[100], "front")            # 원문 → 들어올려 맨 앞
    assert image_order(d) == [140, 180, 100]
    O.order(d, 0, a, "back")
    assert image_order(d) == [100, 140, 180]
    c = O.lift(d, 0, objs[180] | {"rect": O.find(d, 0, fitz.Rect(180, 100, 280, 200), "image")["rect"]})
    O.order(d, 0, a, "forward")                      # a 바로 뒤의 원문 덩이(140 이 든 것)를 넘는다
    assert image_order(d) == [140, 100, 180]
    O.order(d, 0, a, "forward")                      # c 와 자리 바꿈
    assert image_order(d) == [140, 180, 100]
    O.order(d, 0, a, "backward")
    assert image_order(d) == [140, 100, 180]
    O.order(d, 0, c, "backward")
    assert image_order(d) == [140, 180, 100]
    # list_objects 도 같은 차례
    assert [round(o["rect"].x0) for o in O.list_objects(d, 0) if o["kind"] == "image"] == [140, 180, 100]


def test_order_original_forward_backward(three):
    """원문 덩이 안 객체의 앞으로 = 그 덩이 바로 뒤, 뒤로 = 바로 앞."""
    d = three
    mid = O.find(d, 0, fitz.Rect(140, 100, 240, 200), "image")
    O.order(d, 0, mid, "forward")
    assert image_order(d) == [100, 180, 140]
    d2 = Document.blank()
    p = d2.fitz[0]
    for x in (100, 140, 180):
        p.insert_image(fitz.Rect(x, 100, x + 100, 200), stream=png(), keep_proportion=False)
    O.order(d2, 0, O.find(d2, 0, fitz.Rect(140, 100, 240, 200), "image"), "backward")
    assert image_order(d2) == [140, 100, 180]


def test_order_is_one_undo_step(three):
    d = three
    before = render(d)
    n0 = len(d._undo)
    O.order(d, 0, O.find(d, 0, fitz.Rect(100, 100, 200, 200), "image"), "front")
    assert len(d._undo) == n0 + 1 and diff(render(d), before) > 0
    d.undo()
    assert diff(render(d), before) == 0


def test_image_to_back_shows_text():
    """글 위 그림을 「맨 뒤로」 보내면 글이 보인다."""
    d = Document.blank()
    p = d.fitz[0]
    p.insert_text((100, 150), "Hidden text", fontsize=30)
    p.insert_image(fitz.Rect(80, 100, 400, 170), stream=png((255, 255, 255), corner=False), keep_proportion=False)
    clip = fitz.Rect(100, 120, 300, 155)
    assert min(render(d, clip=clip)) > 200          # 흰 그림이 글을 덮었다
    O.order(d, 0, O.find(d, 0, fitz.Rect(80, 100, 400, 170), "image"), "back")
    assert min(render(d, clip=clip)) < 60           # 글(검정)이 보인다


def test_text_object_order_and_list(three):
    """글 객체(K /T, Form): 목록에 한 번만(안쪽 글을 원문 글로 또 세지 않음), 순서 바꾸기, 같은 문서 붙여넣기."""
    d = three
    tid = make_text_object(d, 0, fitz.Rect(120, 120, 260, 160), "TXT")
    objs = O.list_objects(d, 0)
    t = [o for o in objs if o["kind"] == "text" and o["ours"]]
    assert len(t) == 1 and t[0]["id"] == tid
    assert not [o for o in objs if o["kind"] == "text" and not o["ours"] and o["rect"].intersects(t[0]["rect"])]
    assert objs[-1]["id"] == tid                       # 맨 위
    O.order(d, 0, tid, "back")
    assert O.list_objects(d, 0)[0]["id"] == tid
    s = O.spec(d, 0, tid)
    assert s["kind"] == "text" and s["style"]["text"] == "TXT" and s["form"] == t[0]["xref"]
    [new] = O.paste(d, 0, [s], 0, 300)
    got = next(o for o in O.list_objects(d, 0) if o["id"] == new)
    assert got["xref"] == t[0]["xref"] and abs(got["rect"].y0 - t[0]["rect"].y0 - 300) < 0.5
    assert "TXT" in d.fitz[0].get_textbox(got["rect"])
    assert O.delete(d, 0, [tid]) == 1
    assert "TXT" not in d.fitz[0].get_textbox(t[0]["rect"])


# ── 복사 자료 · 붙여넣기 ─────────────────────────────────────────
def test_spec_paste_other_document_same_look():
    d = Document.blank()
    p = d.fitz[0]
    p.insert_image(fitz.Rect(100, 100, 300, 200), stream=png(alpha=160), keep_proportion=False)
    item = O.find(d, 0, fitz.Rect(100, 100, 300, 200), "image")
    A.transform_image(d, 0, item["xref"], item["rect"], rotate=90, flip_h=True, opacity=0.5)
    item = O.find(d, 0, fitz.Rect(150, 50, 250, 250), "image")
    s = O.spec(d, 0, item)
    assert s["mask"] and abs(s["opacity"] - 0.5) < 0.01
    clip = fitz.Rect(s["rect"])
    look = render(d, clip=clip)
    other = Document.blank()
    [oid] = O.paste(other, 0, [s])
    got = next(o for o in O.list_objects(other, 0) if o["id"] == oid)
    assert O._close(got["rect"], clip, 0.5)
    assert diff(render(other, clip=clip), look) == 0
    # 다른 문서에 붙여도 쪽 좌표 차이만큼(쪽 크기 다른 문서)
    small = Document.blank(300, 300)
    [oid] = O.paste(small, 0, [s], -100, -40)
    r = next(o for o in O.list_objects(small, 0) if o["id"] == oid)["rect"]
    assert r.x0 >= 0 and r.y0 >= 0 and r.x1 <= 300 and r.y1 <= 300


def test_paste_same_document_reuses_image(three):
    d = three
    n = d.fitz.xref_length()
    s = O.spec(d, 0, O.find(d, 0, fitz.Rect(100, 100, 200, 200), "image"))
    assert d.fitz.xref_length() == n and not d.can_undo()     # 복사는 문서를 바꾸지 않는다
    ids = O.paste(d, 0, [s], 10, 10)
    assert d.fitz.xref_length() <= n + 2                       # 그림 객체를 새로 만들지 않음(마스크·상태 사전 정도)
    got = next(o for o in O.list_objects(d, 0) if o["id"] == ids[0])
    assert got["xref"] == s["xref"] and abs(got["rect"].x0 - 110) < 0.5
    assert O.list_objects(d, 0)[-1]["id"] == ids[0]          # 붙인 것은 맨 위
    # 다른 쪽에도
    d.fitz.new_page()
    [b] = O.paste(d, 1, [s])
    assert next(o for o in O.list_objects(d, 1) if o["id"] == b)["xref"] == s["xref"]


def test_paste_after_source_deleted_and_undo(three):
    d = three
    s = O.spec(d, 0, O.find(d, 0, fitz.Rect(180, 100, 280, 200), "image"))
    O.delete(d, 0, [O.find(d, 0, fitz.Rect(180, 100, 280, 200), "image")])
    assert len([o for o in O.list_objects(d, 0) if o["kind"] == "image"]) == 2
    [oid] = O.paste(d, 0, [s], 0, 250)              # 원본을 지운 뒤에도 붙는다
    d.undo()
    d.undo()
    assert len([o for o in O.list_objects(d, 0) if o["kind"] == "image"]) == 3
    [oid] = O.paste(d, 0, [s], 0, 250)              # 실행 취소 뒤에도
    r = next(o for o in O.list_objects(d, 0) if o["id"] == oid)["rect"]
    assert abs(r.y0 - 350) < 0.5


def test_paste_group_clamped_inside_page(three):
    d = three
    ss = [O.spec(d, 0, o) for o in O.list_objects(d, 0) if o["kind"] == "image"]
    ids = O.paste(d, 0, ss, 1000, 1000)
    rs = [o["rect"] for o in O.list_objects(d, 0) if o["id"] in ids]
    assert max(r.x1 for r in rs) <= 595.5 and max(r.y1 for r in rs) <= 842.5
    assert abs(min(r.x0 for r in rs) - (595 - 180)) < 0.5      # 묶음 모양은 그대로


def test_paste_original_text_writes_text(three):
    d = three
    item = O.find(d, 0, fitz.Rect(100, 285, 200, 305), "block")
    s = O.spec(d, 0, item)
    assert s["text"] == "Hello objects" and s["style"]["fontsize"] == 14
    other = Document.blank()
    O.paste(other, 0, [s], 0, 100)
    assert "Hello objects" in " ".join(other.fitz[0].get_text().split())


# ── 옮기기 · 지우기 ─────────────────────────────────────────────
def test_move_group_keeps_order(three):
    d = three
    objs = [o for o in O.list_objects(d, 0) if o["kind"] == "image"]
    ids = O.move(d, 0, objs, 0, 300)
    assert len(ids) == 3 and image_order(d) == [100, 140, 180]
    ys = [round(o["rect"].y0) for o in O.list_objects(d, 0) if o["kind"] == "image"]
    assert ys == [400, 400, 400]
    assert len(d._undo) == 1


def test_delete_original_image_only_that_one(three):
    d = three
    assert O.delete(d, 0, [O.find(d, 0, fitz.Rect(140, 100, 240, 200), "image")]) == 1
    assert image_order(d) == [100, 180]
    assert "Hello" in d.fitz[0].get_text()


# ── 맞춤 · 분배 ──────────────────────────────────────────────────
def test_arrange():
    rs = [fitz.Rect(10, 10, 30, 20), fitz.Rect(50, 40, 60, 80), fitz.Rect(100, 5, 140, 15)]
    assert [r.x0 for r in O.arrange(rs, "left")] == [10, 10, 10]
    assert [r.x1 for r in O.arrange(rs, "right")] == [140, 140, 140]
    assert [r.y0 for r in O.arrange(rs, "top")] == [5, 5, 5]
    assert [r.y1 for r in O.arrange(rs, "bottom")] == [80, 80, 80]
    assert [(r.x0 + r.x1) / 2 for r in O.arrange(rs, "hcenter")] == [75, 75, 75]
    assert [(r.y0 + r.y1) / 2 for r in O.arrange(rs, "vcenter")] == [42.5, 42.5, 42.5]
    pr = fitz.Rect(0, 0, 200, 100)
    out = O.arrange(rs[:1], "page_both", pr)
    assert (out[0].x0, out[0].y0, out[0].width, out[0].height) == (90, 45, 20, 10)
    assert O.arrange(rs[:1], "page_h", fitz.Rect(100, 0, 300, 50))[0].x0 == 190
    h = O.arrange(rs, "dist_h")
    gaps = sorted(h, key=lambda r: r.x0)
    assert abs((gaps[1].x0 - gaps[0].x1) - (gaps[2].x0 - gaps[1].x1)) < 1e-9 and h[0].x0 == 10 and h[2].x1 == 140
    v = sorted(O.arrange(rs, "dist_v"), key=lambda r: r.y0)
    assert abs((v[1].y0 - v[0].y1) - (v[2].y0 - v[1].y1)) < 1e-9
    assert [(r.width, r.height) for r in O.arrange(rs, "dist_v")] == [(r.width, r.height) for r in rs]
    with pytest.raises(ValueError):
        O.arrange(rs[:2], "dist_h")
    with pytest.raises(ValueError):
        O.arrange(rs, "page_v")
    assert O.arrange([], "left") == []


def test_arrange_then_move_one_step(three):
    """맞춤 계산 + 묶음 옮기기를 한 걸음으로(2단계 화면이 하는 순서)."""
    d = three
    objs = [o for o in O.list_objects(d, 0) if o["kind"] == "image"]
    for o in objs:
        O.lift(d, 0, o)
    d._undo.clear()
    objs = [o for o in O.list_objects(d, 0) if o["kind"] == "image"]
    new = O.arrange([o["rect"] for o in objs], "top")
    with O._one_step(d):
        for o, r in zip(objs, new):
            O.move(d, 0, [o["id"]], r.x0 - o["rect"].x0, r.y0 - o["rect"].y0 + 50)
    assert len(d._undo) == 1
    assert {round(o["rect"].y0) for o in O.list_objects(d, 0) if o["kind"] == "image"} == {150}


def test_original_text_order_follows_text_engine(three):
    """원문 글의 쌓는 순서는 annots.edit_block(덩어리 나의 글 객체)에 기댄다. 글 엔진이 글 객체를 쓰면 표식 글 객체가
    되고, 아직 쓰지 않으면 바꾸지 않고 막으며 문서도 그대로다(합치기 전 main)."""
    d = three
    item = O.find(d, 0, fitz.Rect(100, 285, 200, 305), "block")
    content = O._joined(d.fitz, d.fitz[0])
    try:
        oid = O.order(d, 0, item, "back")
    except ValueError:
        assert O._joined(d.fitz, d.fitz[0]) == content and not d.can_undo()
        return
    objs = O.list_objects(d, 0)
    assert objs[0]["id"] == oid and objs[0]["kind"] == "text" and len(d._undo) == 1
    assert "Hello objects" in " ".join(d.fitz[0].get_text().split())


# ── 라운드 17 이음새: 글 엔진이 쓴 글 객체를 객체 엔진이 읽고 순서를 바꾼다 ──
def test_textbox_objects_listed_and_ordered():
    d = Document.blank()
    a = A.add_text(d, 0, fitz.Rect(100, 100, 260, 140), "첫째 글", 12)
    b = A.add_text(d, 0, fitz.Rect(100, 200, 260, 240), "둘째 글", 12)
    texts = [o for o in O.list_objects(d, 0) if o["kind"] == "text" and o["ours"]]
    assert [o["id"] for o in texts] == [a, b]
    O.order(d, 0, texts[0], "front")
    after = [o["id"] for o in O.list_objects(d, 0) if o["kind"] == "text" and o["ours"]]
    assert after == [b, a]
    assert "첫째 글" in d.fitz[0].get_text() and "둘째 글" in d.fitz[0].get_text()


def test_blocked_order_rolls_back_with_batch():
    # Document.batch() 를 쓰는 길에서도 막힌 순서 바꾸기는 문서·실행 취소를 되돌린다
    test_image_in_form_refused()
