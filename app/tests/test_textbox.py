"""글 엔진 insert_htmlbox 전환(설계 17 덩어리 나): 글 객체 표식·서식 되읽기·새 서식 저장·쌓는 순서·글 주석 글꼴·
빈 네모 막기·Document.batch()·실행 취소 사본 크기."""
from __future__ import annotations

import os
import re
import tempfile

import fitz
import pytest

from app.engine import annots as A
from app.engine import deco as E
from app.engine import fonts as F
from app.engine import textbox as T
from app.engine.document import Document

ALT = "Batang"   # 맑은 고딕이 아닌 한글 설치 글꼴(이 PC 에 있는 것)


def _alt_family() -> str:
    fams = F.font_families()
    for name in (ALT, "Gulim", "Dotum", "Gungsuh"):
        if name in fams:
            return name
    pytest.skip("맑은 고딕 말고 한글 설치 글꼴이 없음")


def _ink(page: fitz.Page, r: fitz.Rect) -> int:
    pix = page.get_pixmap(clip=r, dpi=72)
    return sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 160)


def _reopen(d: Document) -> Document:
    path = os.path.join(tempfile.mkdtemp(), "t.pdf")
    d.save(path)
    return Document(path)


def _fonts_in_page_text(page: fitz.Page) -> set[str]:
    return {s["font"] for b in page.get_text("dict")["blocks"] if b.get("type") == 0
            for l in b["lines"] for s in l["spans"] if s["text"].strip()}


# ── 글 객체 ──────────────────────────────────────────────────────
def test_add_text_makes_marked_object_and_style_reads_back():
    d = Document()
    r = fitz.Rect(100, 100, 300, 140)
    oid = A.add_text(d, 0, r, "새 글 abc\n둘째 줄", 14, (0.8, 0, 0), True, 1, italic=True,
                     style={"underline": True, "strike": False, "line_height": 1.8, "letter_spacing": 1.5})
    assert oid == 1
    p = d.fitz[0]
    data = b"".join(d.fitz.xref_stream(x) for x in p.get_contents())
    assert re.search(rb"/RPDFObj\s*<</Id 1 /K /T>> BDC q [-\d. ]+ cm /RPDFTx\d+ Do Q EMC", data)
    objs = T.text_objects(p)
    assert len(objs) == 1 and objs[0]["id"] == 1
    st = A.block_style(d, 0, objs[0]["rect"])
    assert st["fontsize"] == 14 and st["bold"] and st["italic"] and st["align"] == 1 and st["underline"]
    assert st["line_height"] == 1.8 and st["letter_spacing"] == 1.5 and not st["strike"]
    assert tuple(round(c, 2) for c in st["color"]) == (0.8, 0, 0)
    # 글 찾기·복사가 된다(표준 Form XObject)
    assert "새 글 abc" in p.get_text() and "둘째 줄" in p.get_text()
    assert A.add_text(d, 0, r, "   ") == 0   # 빈 글은 아무것도 안 함


def test_text_blocks_merge_object_into_one_block():
    d = Document()
    A.add_text(d, 0, fitz.Rect(100, 100, 220, 140), "첫 줄 아주 길게 써서 접히는 글\n둘째 문단\n\n넷째", 12)
    blocks = [b for b in d.text_blocks(0) if b.get("type") == 0]
    mine = [b for b in blocks if b.get("rpdf_id")]
    assert len(mine) == 1 and len(blocks) == 1
    text = "\n".join("".join(s["text"] for s in l["spans"]) for l in mine[0]["lines"])
    assert text == "첫 줄 아주 길게 써서 접히는 글\n둘째 문단\n\n넷째"   # 자동 줄바꿈이 새 줄로 갈라지지 않음


def test_edit_original_becomes_object_and_object_edits_in_place():
    d = Document()
    d.fitz[0].insert_text((100, 300), "Original line", fontsize=12, fontname="helv")
    blk = next(b for b in d.text_blocks(0) if b.get("type") == 0)
    oid = A.edit_block(d, 0, fitz.Rect(blk["bbox"]), "Changed line", 12, (0, 0, 1), False, 0)
    assert oid >= 1 and "Original" not in d.fitz[0].get_text() and "Changed line" in d.fitz[0].get_text()
    o = T.text_objects(d.fitz[0])[0]
    # 다시 고치면 같은 Id 로 제자리에서(밑줄은 style 에 없으면 그대로)
    A.edit_block(d, 0, o["rect"], "Changed again", 16, (0, 0, 0), True, 0, style={"underline": True})
    A.edit_block(d, 0, o["rect"], "Changed third", 16, (0, 0, 0), True, 0)
    objs = T.text_objects(d.fitz[0])
    assert [x["id"] for x in objs] == [oid]
    assert objs[0]["style"]["underline"] and objs[0]["style"]["bold"] and objs[0]["style"]["fontsize"] == 16
    # 글 객체는 상자 너비에서 줄을 바꾸고 아래로 늘어난다(글이 상자 밖에서 잘리지 않음)
    assert d.fitz[0].get_text().split() == ["Changed", "third"]
    assert objs[0]["rect"].height > o["rect"].height + 10
    # 빈 글이면 줄째 들어냄(밑줄 선까지)
    assert A.edit_block(d, 0, objs[0]["rect"], "") == 0
    assert not T.text_objects(d.fitz[0]) and "Changed" not in d.fitz[0].get_text()
    assert _ink(d.fitz[0], fitz.Rect(90, 280, 400, 340)) == 0


def test_new_styles_survive_save_and_reopen():
    d = Document()
    r = fitz.Rect(80, 80, 400, 160)
    A.add_text(d, 0, r, "밑줄 취소선 간격", 18, (0, 0, 0), style={"underline": True, "strike": True,
                                                                 "line_height": 2.0, "letter_spacing": 3})
    d2 = _reopen(d)
    o = T.text_objects(d2.fitz[0])[0]
    st = o["style"]
    assert st["underline"] and st["strike"] and st["line_height"] == 2.0 and st["letter_spacing"] == 3
    assert A.block_style(d2, 0, o["rect"])["underline"]
    # 밑줄·취소선은 선으로 그려진다: 같은 글을 서식 없이 쓴 것보다 잉크가 많다
    plain = Document()
    A.add_text(plain, 0, r, "밑줄 취소선 간격", 18, (0, 0, 0), style={"line_height": 2.0, "letter_spacing": 3})
    assert _ink(d2.fitz[0], o["rect"]) > _ink(plain.fitz[0], o["rect"]) + 50
    # 문자 간격은 글을 넓힌다
    wide = T.text_width({"text": "abc", "fontsize": 18, "letter_spacing": 3}, "abc")
    assert wide == pytest.approx(T.text_width({"fontsize": 18}, "abc") + 9)
    # 다시 연 문서에서도 고칠 수 있다(제자리)
    A.edit_block(d2, 0, o["rect"], "다시 고침", 18, (0, 0, 0))
    assert [x["id"] for x in T.text_objects(d2.fitz[0])] == [o["id"]]
    assert T.text_objects(d2.fitz[0])[0]["style"]["strike"]


def _draw_order(page: fitz.Page) -> list[str]:
    """그리는 차례대로 글 객체 첫 글자."""
    out = []
    for kind, bbox in page.get_bboxlog():
        if "text" in kind:
            t = page.get_textbox(fitz.Rect(bbox)).strip()[:1]
            if t and (not out or out[-1] != t):
                out.append(t)
    return out


def test_edit_and_move_keep_drawing_order():
    d = Document()
    A.add_text(d, 0, fitz.Rect(100, 100, 300, 140), "AAAA", 20)
    A.add_text(d, 0, fitz.Rect(400, 400, 500, 440), "BBBB", 20)
    A.add_text(d, 0, fitz.Rect(100, 600, 300, 640), "CCCC", 20)
    ids = [o["id"] for o in T.text_objects(d.fitz[0])]
    assert ids == [1, 2, 3] and _draw_order(d.fitz[0]) == ["A", "B", "C"]
    a = T.text_objects(d.fitz[0])[0]
    A.edit_block(d, 0, a["rect"], "AAAA2", 24, (0, 0, 0))                          # 고치기
    a = T.text_objects(d.fitz[0])[0]
    A.edit_block(d, 0, a["rect"], "AAAA2", 24, (0, 0, 0), new_rect=a["rect"] + (0, 300, 0, 300))   # 옮기기
    assert [o["id"] for o in T.text_objects(d.fitz[0])] == [1, 2, 3]
    assert _draw_order(d.fitz[0]) == ["A", "B", "C"]
    assert T.text_objects(d.fitz[0])[0]["rect"].y0 == pytest.approx(a["rect"].y0 + 300)
    # 교정(다른 자리 글 지우기) 뒤에도 Id 로 찾힌다
    d.fitz[0].insert_text((300, 800), "redact me", fontsize=10)
    from app.engine.document import redact_only
    redact_only(d.fitz[0], [fitz.Rect(290, 785, 400, 805)])
    assert [o["id"] for o in T.text_objects(d.fitz[0])] == [1, 2, 3]


def test_rotated_page_and_view_rotation_text_direction():
    d = Document()
    d.fitz[0].set_rotation(90)
    A.add_text(d, 0, fitz.Rect(100, 100, 140, 300), "돌린 글", 14, rotate=90)
    dirs = {l["dir"] for b in d.fitz[0].get_text("dict")["blocks"] for l in b.get("lines", [])}
    assert dirs == {(0.0, -1.0)}
    o = T.text_objects(d.fitz[0])[0]
    assert o["style"]["rotate"] == 90
    A.edit_block(d, 0, o["rect"], "돌린 글 고침", 14, (0, 0, 0))
    dirs = {l["dir"] for b in d.fitz[0].get_text("dict")["blocks"] for l in b.get("lines", [])}
    assert dirs == {(0.0, -1.0)}


def test_family_and_fake_styles():
    fam = _alt_family()
    d = Document()
    A.add_text(d, 0, fitz.Rect(100, 100, 300, 140), "글꼴 고르기", 14, style={"family": fam})
    assert any(fam.lower() in f.lower().replace(" ", "") or fam.lower() in f.lower() for f in _fonts_in_page_text(d.fitz[0]))
    o = T.text_objects(d.fitz[0])[0]
    assert A.block_style(d, 0, o["rect"])["family"] == fam
    # 기울임 파일이 없는 글꼴은 객체 줄 행렬로 기울인다
    A.add_text(d, 0, fitz.Rect(100, 300, 300, 340), "기울임", 14, italic=True)
    data = d.fitz.xref_stream(d.fitz[0].get_contents()[-1])
    m = re.findall(rb"q ([-\d. ]+) cm /RPDFTx\d+ Do Q", data)[-1].split()
    assert float(m[2]) != 0   # c 칸(기울기)


# ── 빈 네모 막기 ─────────────────────────────────────────────────
def test_deco_text_not_blank_after_text_objects():
    """글 객체를 쓴 뒤(그리고 저장해 다시 연 뒤) 꾸미기 글자가 빈 네모가 아니다(insert_font 경로)."""
    ref = Document()
    E.draw_text_box(ref.fitz[0], "뷁뚫꿿쀍", {"size": 24}, anchor=fitz.Point(300, 600))
    want = _ink(ref.fitz[0], fitz.Rect(150, 560, 450, 640))
    assert want > 200
    d = Document()
    E.draw_text_box(d.fitz[0], "가나", {"size": 24}, anchor=fitz.Point(300, 200))   # 먼저 맑은 고딕을 넣어 둠
    A.add_text(d, 0, fitz.Rect(100, 300, 300, 340), "글 객체 하나", 14)
    # 열린 문서의 꾸미기 글꼴은 줄이지 않는다(글 객체 글꼴만 임시 문서에서 줄여 옴)
    assert not any(re.match(r"[A-Z]{6}\+", f[3] or "") for f in d.fitz[0].get_fonts() if not f[4].startswith("F"))
    E.draw_text_box(d.fitz[0], "뷁뚫꿿쀍", {"size": 24}, anchor=fitz.Point(300, 600))
    assert _ink(d.fitz[0], fitz.Rect(150, 560, 450, 640)) == want
    d2 = _reopen(d)
    A.add_text(d2, 0, fitz.Rect(100, 400, 300, 440), "또 하나", 14)
    E.draw_text_box(d2.fitz[0], "뷁뚫꿿쀍", {"size": 24}, anchor=fitz.Point(300, 750))
    assert _ink(d2.fitz[0], fitz.Rect(150, 710, 450, 790)) == want


# ── 글 주석 ──────────────────────────────────────────────────────
def test_freetext_drawn_with_chosen_installed_font_after_color_size_move():
    fam = _alt_family()
    d = Document()
    x = A.free_text(d, 0, fitz.Rect(100, 100, 300, 160), "글꼴 주석 abc", 14, (1, 0, 0), fill=(1, 1, 0.8),
                    border=(0, 0, 1), width=1, family=fam)

    def font_ok(page) -> bool:
        return any(fam.lower() in f.lower() for f in _fonts_in_page_text(page))

    assert font_ok(d.fitz[0]) and A.freetext_info(d, 0, x)["family"] == fam
    x = A.update(d, 0, x, text_color=(0, 0.5, 0), fontsize=18)
    assert font_ok(d.fitz[0])
    x = A.move_by(d, 0, x, 30, 40)
    assert font_ok(d.fitz[0])
    x = A.edit_free_text(d, 0, x, "고친 글", fitz.Rect(130, 140, 330, 200))
    assert font_ok(d.fitz[0]) and "고친 글" in d.fitz[0].get_text()
    span = next(s for b in d.fitz[0].get_text("dict")["blocks"] for l in b["lines"] for s in l["spans"] if s["text"].strip())
    assert span["color"] == 0x008000 and round(span["size"]) == 18
    # 저장하고 다시 열어도 그 글꼴
    d2 = _reopen(d)
    a = next(iter(d2.fitz[0].annots()))   # 저장하면 객체 번호가 바뀐다
    assert font_ok(d2.fitz[0]) and A.freetext_info(d2, 0, a.xref)["family"] == fam
    # /DS 에 글꼴 이름(다른 편집기가 다시 그릴 때)
    assert A._is_rich(d2.fitz, a) and fam in (d2.fitz.xref_get_key(a.xref, "DS")[1] or "")


def test_freetext_default_font_and_export_family():
    d = Document()
    x = A.free_text(d, 0, fitz.Rect(100, 100, 300, 160), "맑은 고딕", 12)
    assert A.freetext_info(d, 0, x)["family"] == F.DEFAULT_FAMILY
    assert any("Malgun" in f for f in _fonts_in_page_text(d.fitz[0]))
    e = A.export_annots(d)
    assert e[0]["family"] == F.DEFAULT_FAMILY
    fam = _alt_family()
    e[0]["family"] = fam
    d2 = Document()
    assert A.import_annots(d2, e) == (1, 0)
    a = next(iter(d2.fitz[0].annots()))
    assert A.freetext_info(d2, 0, a.xref)["family"] == fam


def test_old_freetext_left_alone_until_touched():
    """옛 글 주석(우리 외관 없음)은 손대기 전까지 외관을 그대로 둔다. /DS 글꼴이 설치 글꼴이면 고칠 때 그 글꼴."""
    fam = _alt_family()
    d = Document()
    p = d.fitz[0]
    a = p.add_freetext_annot(fitz.Rect(100, 100, 300, 160), "옛 글", fontsize=12, richtext=True,
                             style=f"font-family: {fam}; font-size: 12pt")
    a.update()
    ap0 = a._getAP()
    assert T.annot_font(d.fitz, a.xref) == ""
    assert A.freetext_info(d, 0, a.xref)["family"] == fam
    assert next(iter(p.annots()))._getAP() == ap0
    x = A.update(d, 0, a.xref, fontsize=14)
    assert T.annot_font(d.fitz, x) == fam


# ── 실행 취소 ────────────────────────────────────────────────────
def test_document_batch_is_one_undo_step():
    d = Document()
    with d.batch():
        A.add_text(d, 0, fitz.Rect(100, 100, 300, 140), "하나", 12)
        with d.batch():   # 겹쳐 써도 된다
            A.add_text(d, 0, fitz.Rect(100, 200, 300, 240), "둘", 12)
        A.free_text(d, 0, fitz.Rect(100, 300, 300, 340), "셋", 12)
    assert len(d._undo) == 1 and d.can_undo()
    assert d.undo()
    assert not T.text_objects(d.fitz[0]) and not list(d.fitz[0].annots())
    assert d.redo() and len(T.text_objects(d.fitz[0])) == 2
    A.add_text(d, 0, fitz.Rect(100, 400, 300, 440), "넷", 12)   # 묶음 밖은 다시 한 걸음씩
    assert len(d._undo) == 2


def test_undo_copies_small_after_20_edits(capsys):
    """편집 20번 뒤 실행 취소 사본 크기(보고용 수치도 찍는다). 글꼴을 임시 문서에서 줄여 오므로 작게 머문다."""
    d = Document()
    A.add_text(d, 0, fitz.Rect(100, 100, 300, 140), "처음 글", 12)
    for k in range(19):
        o = T.text_objects(d.fitz[0])[0]
        A.edit_block(d, 0, o["rect"], f"고친 글 {k} 가나다라마바사", 12 + k % 3, (0, 0, 0), bool(k % 2), 0,
                     style={"underline": bool(k % 3)})
    sizes = [len(b) for b in d._undo]
    assert len(sizes) == 20
    total = sum(sizes)
    with capsys.disabled():
        print(f"\n[실행 취소 사본] 20걸음 합계 {total / 1e6:.2f}MB · 마지막 {sizes[-1] / 1e6:.2f}MB")
    assert sizes[-1] < 3_000_000 and total < 40_000_000


def test_freeing_old_form_keeps_objects_shared_by_dedupe():
    """같은 객체 합치기(garbage=3)로 두 글 객체가 글꼴을 같이 쓰게 된 뒤 하나를 고쳐도 다른 하나는 그대로 그려진다."""
    d = Document.blank(count=2)
    A.add_text(d, 0, fitz.Rect(100, 100, 300, 140), "똑같은 글", 14)
    A.add_text(d, 1, fitz.Rect(100, 100, 300, 140), "똑같은 글", 14)
    d.dedupe = True
    d2 = _reopen(d)
    f = d2.fitz
    shared = T._reach(f, [T.text_objects(f[0])[0]["xref"]]) & T._reach(f, [T.text_objects(f[1])[0]["xref"]])
    assert shared   # 합치기로 묶인 객체가 있다
    before = _ink(f[1], fitz.Rect(90, 90, 320, 150))
    A.edit_block(d2, 0, T.text_objects(f[0])[0]["rect"], "바뀐 글", 14, (0, 0, 0))
    assert _ink(d2.fitz[1], fitz.Rect(90, 90, 320, 150)) == before and "똑같은 글" in d2.fitz[1].get_text()


# ── 글 상자 크기 ─────────────────────────────────────────────────
def test_box_size_uses_style():
    w0, h0 = A.text_box_size("abc\nde", 10)
    w1, h1 = A.text_box_size("abc\nde", 10, style={"letter_spacing": 2, "line_height": 2})
    assert w1 == pytest.approx(w0 + 6) and h1 == pytest.approx(2 * 10 * 2 + T.TEXT_PAD)
