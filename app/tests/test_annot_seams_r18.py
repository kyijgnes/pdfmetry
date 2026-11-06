"""라운드 18 주석 이음새: 한글 첨부 이름 왕복, 회전 쪽·원점이 0 아닌 쪽의 그림 바꾸기, 회전이 다른 쪽의 스탬프.

모두 저장한 뒤 다시 연 결과로 본다."""
from __future__ import annotations

import os
import tempfile

import fitz
import pytest

from app.engine import annot_clip as AC
from app.engine import annots as A
from app.engine.document import Document

BLUE, RED = (0, 0, 255), (255, 0, 0)


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(), name)


def _reopen(doc: Document) -> fitz.Document:
    out = _tmp("다시.pdf")
    doc.fitz.save(out, garbage=3, deflate=True)
    return fitz.open(out)


def _two_tone(w: int = 40, h: int = 20) -> bytes:
    """왼쪽 반 파랑, 오른쪽 반 빨강 그림."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h))
    pix.set_rect(pix.irect, RED)
    pix.set_rect(fitz.IRect(0, 0, w // 2, h), BLUE)
    return pix.tobytes("png")


def _view_color(page: fitz.Page, pt: fitz.Point) -> tuple:
    """보이는 화면(쪽 회전 뒤) 좌표의 색. get_pixmap 의 clip 은 보이는 화면 좌표다."""
    return page.get_pixmap(clip=fitz.Rect(pt.x - 1, pt.y - 1, pt.x + 1, pt.y + 1)).pixel(0, 0)[:3]


def _near(a, b, tol=0.6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _close(c, want, tol=40) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(c, want))


# ── 1. 한글 첨부 이름 ─────────────────────────────────────────────
def test_korean_attachment_name_round_trip():
    src = _tmp("빈.pdf")
    g = fitz.open()
    g.new_page()
    g.new_page()
    g.save(src)
    att = _tmp("한글 첨부 파일.txt")
    with open(att, "wb") as fh:
        fh.write("내용".encode("utf-8"))
    doc = Document(src)
    x = A.attachment(doc, 0, fitz.Point(50, 50), att)
    h = _reopen(doc)
    pg = h[0]
    a = pg.first_annot          # garbage 저장은 객체 번호를 바꾼다
    fi = A.attach_info(a)
    assert fi["filename"] == "한글 첨부 파일.txt"
    assert fi["description"] == "한글 첨부 파일.txt"
    assert a.get_file().decode("utf-8") == "내용"
    h.close()

    # 복사해 다른 쪽에 붙여도 이름이 그대로(garbage 저장이 열린 문서의 객체 번호도 바꿔서 다시 찾는다)
    pg0 = doc.fitz[0]
    e = AC.annot_spec(doc, 0, pg0.first_annot.xref)
    assert e["file"]["name"] == "한글 첨부 파일.txt"
    xs = AC.paste_annots(doc, 1, [e], 10, 10)
    assert len(xs) == 1
    h = _reopen(doc)
    pg = h[1]
    fi = A.attach_info(pg.first_annot)
    assert fi["filename"] == "한글 첨부 파일.txt"
    h.close()


def test_attach_info_restores_utf8_bytes_name():
    """다른 프로그램이 이름을 UTF-8 낱바이트로 적은 파일도 바르게 읽는다."""
    g = fitz.open()
    pg = g.new_page()
    a = pg.add_file_annot((50, 50), b"x", "a.txt")
    raw = "한글.txt".encode("utf-8")
    lit = "(" + "".join(f"\\{b:03o}" for b in raw) + ")"
    for k in ("F", "UF"):
        g.xref_set_key(a.xref, f"FS/{k}", lit)
    out = _tmp("utf8.pdf")
    g.save(out)
    h = fitz.open(out)
    hp = h[0]
    assert A.attach_info(hp.first_annot)["filename"] == "한글.txt"


# ── 2. 회전 쪽 · 원점이 0 아닌 쪽에서 그림 바꾸기 ─────────────────────
def _image_page(rot: int, org: tuple[float, float], crop: bool) -> str:
    """쪽 좌표 (100,120)-(200,170) 에 그림 하나를 원문 줄(cm … Do)로 그린 쪽. 미디어 상자 시작점 org."""
    g = fitz.open()
    p = g.new_page(width=500, height=700)
    p.insert_image(fitz.Rect(0, 0, 10, 10), stream=_two_tone())
    name = p.get_images(full=True)[0][7]
    ox, oy = org
    g.xref_set_key(p.xref, "MediaBox", f"[{ox} {oy} {ox + 500} {oy + 700}]")
    cx, cy = (ox, oy)
    if crop:
        g.xref_set_key(p.xref, "CropBox", f"[{ox + 7} {oy + 11} {ox + 490} {oy + 690}]")
    # 쪽 좌표(크롭 상자 왼쪽 위 기준, 아래로 y) → PDF 좌표
    top = (oy + 690) if crop else (oy + 700)
    left = (ox + 7) if crop else ox
    g.update_stream(p.get_contents()[0], f"q 100 0 0 50 {100 + left} {top - 170} cm /{name} Do Q".encode())
    p.set_rotation(rot)
    out = _tmp(f"그림{rot}.pdf")
    g.save(out)
    return out


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("org,crop", [((0, 0), False), ((30, 50), False), ((-40, 20), True)])
def test_transform_image_rotated_page_with_origin(rot, org, crop):
    doc = Document(_image_page(rot, org, crop))
    info = doc.fitz[0].get_image_info(xrefs=True)[0]
    r0 = fitz.Rect(info["bbox"])
    assert _near(r0, (100, 120, 200, 170))
    # 시계 90 도: 가운데 (150,145) 를 두고 세워진다
    r = A.transform_image(doc, 0, info["xref"], r0, rotate=90)
    assert _near(r, (125, 95, 175, 195))
    # 옮기고 늘리기
    r = A.transform_image(doc, 0, info["xref"], r, new_rect=fitz.Rect(300, 400, 350, 500))
    assert _near(r, (300, 400, 350, 500))
    h = _reopen(doc)
    hp = h[0]
    assert _near(hp.get_image_info()[0]["bbox"], (300, 400, 350, 500))
    # 쪽 좌표에서 시계 90 도로 돌았으니 파랑(왼쪽)이 위로, 빨강이 아래로. 보이는 화면 좌표로 바꿔 색을 본다
    m = hp.rotation_matrix
    assert _close(_view_color(hp, fitz.Point(325, 425) * m), BLUE)
    assert _close(_view_color(hp, fitz.Point(325, 475) * m), RED)
    # 보이는 화면의 좌우 뒤집기: 다시 열어 그 화면의 왼쪽·오른쪽 색이 바뀌었는지
    doc2 = Document(_image_page(rot, org, crop))
    info = doc2.fitz[0].get_image_info(xrefs=True)[0]
    pg = doc2.fitz[0]
    vis = (fitz.Rect(info["bbox"]) * pg.rotation_matrix).normalize()
    lp, rp = fitz.Point(vis.x0 + 3, (vis.y0 + vis.y1) / 2), fitz.Point(vis.x1 - 3, (vis.y0 + vis.y1) / 2)
    before = (_view_color(pg, lp), _view_color(pg, rp))
    A.transform_image(doc2, 0, info["xref"], fitz.Rect(info["bbox"]), flip_h=True)
    h2 = _reopen(doc2)
    hp2 = h2[0]
    assert _near((hp2.get_image_info()[0]["bbox"]), info["bbox"])
    assert (_view_color(hp2, lp), _view_color(hp2, rp)) == (before[1], before[0])


# ── 3. 회전이 다른 쪽의 스탬프 ─────────────────────────────────────
def _rotated_doc(rots) -> tuple[str, str]:
    d = tempfile.mkdtemp()
    img = os.path.join(d, "도장.png")
    fitz.Pixmap(_two_tone()).save(img)
    g = fitz.open()
    for r in rots:
        p = g.new_page(width=500, height=700)
        p.set_rotation(r)
    src = os.path.join(d, "회전.pdf")
    g.save(src)
    return src, img


def _check_upright(pg: fitz.Page, size=(100, 50)):
    """그 쪽 첫 주석이 보이는 화면에서 가로·세로 크기 그대로, 왼쪽 파랑·오른쪽 빨강으로 바로 섰는지."""
    a = pg.first_annot
    v = (a.rect * pg.rotation_matrix).normalize()
    assert _near((v.width, v.height), size, 1.0), (pg.rotation, v)
    cy = (v.y0 + v.y1) / 2
    assert _close(_view_color(pg, fitz.Point(v.x0 + v.width * 0.25, cy)), BLUE), pg.rotation
    assert _close(_view_color(pg, fitz.Point(v.x0 + v.width * 0.75, cy)), RED), pg.rotation


@pytest.mark.parametrize("src_rot", [0, 90, 270])
def test_stamp_apply_to_pages_follows_rotation(src_rot):
    rots = [src_rot] + [r for r in (0, 90, 180, 270) if r != src_rot]
    src, img = _rotated_doc(rots)
    doc = Document(src)
    p0 = doc.fitz[0]
    vis = fitz.Rect(100, 100, 200, 150)                       # 보이는 화면에서 가로 100, 세로 50
    x = A.stamp_image(doc, 0, (vis * p0.derotation_matrix).normalize(), img)
    n, skipped = AC.apply_to_pages(doc, 0, x, range(1, len(rots)))
    assert (n, skipped) == (len(rots) - 1, 0)
    h = _reopen(doc)
    for i in range(len(rots)):
        pg = h[i]
        _check_upright(pg)
        v = (pg.first_annot.rect * pg.rotation_matrix).normalize()
        assert _near(v, vis, 1.0)                            # 보이는 화면 기준 같은 자리


def test_stamp_paste_to_rotated_pages_upright():
    src, img = _rotated_doc([0, 90, 180, 270])
    doc = Document(src)
    x = A.stamp_image(doc, 0, fitz.Rect(100, 100, 200, 150), img)
    e = AC.annot_spec(doc, 0, x)
    for j in (1, 2, 3):
        assert len(AC.paste_annots(doc, j, [e])) == 1
    h = _reopen(doc)
    for i in range(4):
        _check_upright(h[i])
