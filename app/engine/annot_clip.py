"""주석 복사 자료(spec)·붙여넣기·여러 쪽에 적용(설계 17, docs/편집 객체 설계.md 4.2·4.4).

자료는 export_annots 항목 꼴에 스탬프·서명(외관 그림)·첨부(파일 바이트·이름·아이콘)·측정 선(축척·라벨 열쇠)을 더한 것.
객체 번호에 기대지 않으므로 원본을 지우거나 실행 취소해도, 다른 문서에도 붙는다. 답글은 담지 않는다.
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

import fitz

from . import annots as A
from .document import Document
from .objects import _image_bytes, _one_step

APPLY_KINDS = ("Square", "Circle", "Line", "PolyLine", "Polygon", "Ink", "FreeText", "Stamp", "Text")
SKIP_KINDS = ("Link", "Widget", "Popup")
MEASURE_KEYS = ("IT", "Measure", "Cap")
_HIDDEN = 2


def can_apply(kind: str) -> bool:
    """「여러 쪽에 적용」이 되는 종류: 도형·선·연필·글상자·스탬프·서명·노트."""
    return kind in APPLY_KINDS


def annot_spec(doc: Document, page: int, xref: int) -> dict | None:
    """주석 하나의 복사 자료. 링크·양식 칸·팝업·없는 주석은 None."""
    with doc.lock:
        a = A.get(doc, page, xref)
        if a is None:
            return None
        f = doc.fitz
        kind = a.type[1]
        if kind in SKIP_KINDS:
            return None
        e = {"page": page, "type": kind, "rect": list(a.rect), "info": dict(a.info), "colors": A.annot_colors(doc, a),
             "vertices": a.vertices if kind in A.VERTEX_KINDS or kind in A.MARKUP_KINDS else None,
             "opacity": a.opacity, "border": a.border, "flags": a.flags & ~_HIDDEN,
             "reply": A._irt(f, xref) > 0, "page_rotation": a._page.rotation, "doc": id(doc)}
        rd = A._rd(f, a)
        if rd:
            e["rd"] = rd
        if kind in ("Line", "PolyLine") and a.line_ends:
            e["line_ends"] = list(a.line_ends)
        if kind == "FreeText":
            e["fontsize"] = A._freetext_style(doc, a)[0]
            e["rotate"] = max(a.rotation, 0)
            if f.xref_get_key(xref, "IT")[1] == "/FreeTextCallout":
                e["callout"] = a.vertices
        if kind == "Text":
            e["icon"] = (f.xref_get_key(xref, "Name")[1] or "/Note").lstrip("/")
        if kind == "Stamp":
            e["stamp"] = _stamp_face(f, a)
            e["name"] = (f.xref_get_key(xref, "Name")[1] or "").lstrip("/")
        if kind == "FileAttachment":
            fi = A.attach_info(a)
            e["file"] = {"data": a.get_file(), "name": fi.get("filename", "file"), "desc": fi.get("description", ""),
                         "icon": (f.xref_get_key(xref, "Name")[1] or "/PushPin").lstrip("/")}
        keys = {k: f.xref_get_key(xref, k) for k in MEASURE_KEYS}
        keys = {k: v for k, v in keys.items() if v[0] != "null"}
        if keys:
            e["keys"] = keys
        return e


def _stamp_face(f: fitz.Document, a: fitz.Annot) -> dict:
    """스탬프 외관 그림: 외관 안의 그림 하나를 그대로(알파 포함), 그림이 아니면 외관을 그려 PNG 로."""
    t, v = f.xref_get_key(a.xref, "AP/N")
    if t == "xref":
        ap = int(v.split()[0])
        t2, v2 = f.xref_get_key(ap, "Resources/XObject")
        refs = [int(x) for x in re.findall(r"(\d+) 0 R", v2)] if t2 == "dict" else []
        if t2 == "xref":
            refs = [int(x) for x in re.findall(r"(\d+) 0 R", f.xref_object(int(v2.split()[0])))]
        imgs = [x for x in refs if f.xref_get_key(x, "Subtype")[1] == "/Image"]
        if len(imgs) == 1 and re.fullmatch(rb"\s*(?:q\s+)?/\S+\s+Do\s*(?:Q\s*)?", f.xref_stream(ap) or b""):
            img, mask = _image_bytes(f, imgs[0])
            if mask:
                pix = fitz.Pixmap(fitz.Pixmap(img), fitz.Pixmap(mask))
                return {"image": pix.tobytes("png")}
            return {"image": img}
    r = a.rect
    s = max(1.0, min(8.0, 600 / max(r.width, r.height, 1)))
    return {"image": a.get_pixmap(matrix=fitz.Matrix(s, s), alpha=True).tobytes("png")}


# ── 자리 옮기기 ───────────────────────────────────────────────────
def _pt(q, m: fitz.Matrix):
    p = fitz.Point(q) * m
    return (p.x, p.y)


def _map_spec(e: dict, m: fitz.Matrix, turn: int = 0) -> dict:
    """자료의 자리(네모·꼭짓점·말풍선 선)를 행렬로 옮긴 사본. turn: 글상자 글 방향에 더할 각."""
    e = dict(e)
    e["rect"] = list(fitz.Rect(e["rect"]).transform(m).normalize()) if not m.is_rectilinear else \
        list((fitz.Rect(e["rect"]) * m).normalize())
    v = e.get("vertices")
    if v:
        if isinstance(v[0], (list, tuple)) and v[0] and isinstance(v[0][0], (list, tuple)):
            e["vertices"] = [[_pt(q, m) for q in s] for s in v]
        else:
            e["vertices"] = [_pt(q, m) for q in v]
    if e.get("callout"):
        e["callout"] = [_pt(q, m) for q in e["callout"]]
    if turn and e["type"] == "FreeText":
        e["rotate"] = (e.get("rotate", 0) + turn) % 360
    return e


def _turn_png(img: bytes, turn: int) -> bytes:
    """스탬프 외관 그림을 쪽 회전 turn(시계 방향 90 배수)에 맞춰 돌린 PNG(stamps.render_png 와 같은 방향, 알파 유지)."""
    pix = fitz.Pixmap(img)
    tmp = fitz.open()
    try:
        pg = tmp.new_page(width=max(1, pix.width), height=max(1, pix.height))
        pg.insert_image(pg.rect, stream=img)
        return pg.get_pixmap(matrix=fitz.Matrix(1, 1).prerotate(-turn), alpha=True).tobytes("png")
    finally:
        tmp.close()


def _turn_rect(r: fitz.Rect, turn: int) -> fitz.Rect:
    """가운데를 그대로 두고 90·270 이면 가로·세로를 바꾼 네모(회전이 다른 쪽에 붙일 때 보이는 크기를 지킨다)."""
    r = fitz.Rect(r)
    if turn % 180 == 0:
        return r
    c = (r.tl + r.br) / 2
    w, h = r.height / 2, r.width / 2
    return fitz.Rect(c.x - w, c.y - h, c.x + w, c.y + h)


# ── 붙이기 ───────────────────────────────────────────────────────
def _put(doc: Document, page: int, e: dict) -> int | None:
    """자료 하나를 쪽에 만든다(실행 취소 자리는 부르는 쪽이 잡음). 새 xref."""
    f = doc.fitz
    p = f[page]
    before = {a.xref for a in p.annots()}
    kind, r = e["type"], fitz.Rect(e["rect"])
    info, cols = e.get("info") or {}, e.get("colors") or {}
    op = e.get("opacity")
    op = op if op is not None and op >= 0 else 1.0
    if kind == "Stamp":
        img = e["stamp"]["image"]
        turn = (p.rotation - e.get("page_rotation", p.rotation)) % 360
        if turn:                     # 외관 그림은 원본 쪽 회전에 맞춰 돌려 구운 것: 이 쪽 회전만큼 더 돌린다
            img = _turn_png(img, turn)
        a = p.add_stamp_annot(r, stamp=img)
        if e.get("name"):
            f.xref_set_key(a.xref, "Name", "/" + e["name"])
        a = p.load_annot(a.xref)
        if op < 1:
            a.set_opacity(op)
        a.set_info(content=info.get("content", ""), title=info.get("title", ""), subject=info.get("subject", ""))
        a.update()
        x = a.xref
    elif kind == "FileAttachment":
        fd = e["file"]
        icon = fd.get("icon") if fd.get("icon") in A.ATTACH_ICONS else "PushPin"
        a = p.add_file_annot(r.tl, fd["data"], fd["name"], desc=fd.get("desc") or fd["name"], icon=icon)
        A.set_attach_name(f, a.xref, fd["name"])
        if cols.get("stroke"):
            a.set_colors(stroke=cols["stroke"])
        if op < 1:
            a.set_opacity(op)
        a.set_info(content=info.get("content", ""), title=info.get("title", ""), subject=info.get("subject", ""))
        a.update()
        a.set_rect(r)
        x = a.xref
    else:
        if not A._import_one(doc, dict(e, page=page)):
            return None
        new = [a.xref for a in f[page].annots() if a.xref not in before]
        if not new:
            return None
        x = new[-1]
    if e.get("keys"):          # 측정 축척·캡션 열쇠: 넣은 뒤 다시 그려야 캡션 자리까지 원본과 같다
        for k, (t, v) in e["keys"].items():
            f.xref_set_key(x, k, v)
        pg = f[page]                       # 쪽을 붙잡아 둔다(놓치면 update 가 풀린 주석을 만짐)
        a = pg.load_annot(x)
        a.update()
    flags = e.get("flags")
    if flags:
        f.xref_set_key(x, "F", str(int(flags)))
    return x


def paste_annots(doc: Document, page: int, specs: Sequence[dict], dx: float = 0, dy: float = 0) -> list[int]:
    """복사 자료들을 쪽에 붙인다(쪽 좌표로 dx·dy 비켜서). 새 xref 들(못 만든 것은 빠짐). 실행 취소 한 걸음."""
    specs = [s for s in specs if s]
    if not specs:
        return []
    m = fitz.Matrix(1, 0, 0, 1, dx, dy)
    out = []
    with doc.lock, _one_step(doc):
        rot = doc.fitz[page].rotation
        for e in specs:
            e = _map_spec(e, m)
            if e["type"] == "Stamp":     # 회전이 다른 쪽에 붙이면 보이는 가로·세로를 지킨다(그림은 _put 이 돌림)
                e["rect"] = list(_turn_rect(e["rect"], rot - e.get("page_rotation", rot)))
            x = _put(doc, page, e)
            if x:
                out.append(x)
    return out


def apply_to_pages(doc: Document, page: int, xref: int, pages: Iterable[int]) -> tuple[int, int]:
    """주석 하나를 고른 쪽마다 보이는 화면 기준 같은 자리에 만든다(원본 쪽은 건너뜀). 자리가 그 쪽 밖이면 건너뛴다.
    (넣은 수, 건너뛴 수). 실행 취소 한 걸음. 안 되는 종류면 ValueError."""
    e = annot_spec(doc, page, xref)
    if e is None or not can_apply(e["type"]) or e.get("reply"):
        raise ValueError("이 주석은 여러 쪽에 적용할 수 없습니다")
    pages = [j for j in dict.fromkeys(pages) if j != page and 0 <= j < doc.page_count]
    n = skipped = 0
    if not pages:
        return 0, 0
    with doc.lock, _one_step(doc):
        f = doc.fitz
        src = f[page]
        to_view = src.rotation_matrix
        for j in pages:
            t = f[j]
            m = to_view * ~t.rotation_matrix
            e2 = _map_spec(e, m, (t.rotation - src.rotation) % 360)
            vis = (fitz.Rect(e2["rect"]) * t.rotation_matrix).normalize()
            tr = t.rect
            if vis.x0 < tr.x0 - 0.5 or vis.y0 < tr.y0 - 0.5 or vis.x1 > tr.x1 + 0.5 or vis.y1 > tr.y1 + 0.5:
                skipped += 1
                continue
            if _put(doc, j, e2):
                n += 1
            else:
                skipped += 1
    return n, skipped
