"""주석·양식·링크·텍스트/이미지 편집. 전부 Document 위에서 snapshot() 뒤에 바꾼다.

색은 (r,g,b) 0~1 튜플. 좌표는 PDF 점(pt), 쪽 좌표계(회전 반영 전 fitz 기준).
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Sequence

import fitz

from .document import Document, _text_rotation, del_keys, korean_font_name, redact_only
from . import fonts as F
from .fonts import korean_fontfile

Color = tuple[float, float, float]

KIND_NAMES = {
    "Highlight": "강조", "Underline": "밑줄", "StrikeOut": "취소선", "Squiggly": "구불구불한 선",
    "Square": "직사각형", "Circle": "타원", "Line": "선", "Polygon": "다각형", "PolyLine": "연결된 선",
    "Ink": "연필", "FreeText": "텍스트 박스", "Text": "노트", "Stamp": "스탬프", "FileAttachment": "첨부 파일",
    "Caret": "삽입 기호", "Redact": "교정 표시", "Link": "링크", "Widget": "양식 칸",
}


@dataclass
class AnnotInfo:
    page: int
    xref: int
    kind: str
    kind_ko: str
    rect: fitz.Rect
    content: str
    author: str
    color: Color | None
    modified: str
    locked: bool = False
    irt: int = 0          # 답글이면 원 주석 xref
    marked: str = ""      # 글자 표시 주석이 덮은 글자


# ── 공통 ─────────────────────────────────────────────────────────
def _finish(annot: fitz.Annot, stroke: Color | None = None, fill: Color | None = None,
            width: float | None = None, opacity: float | None = None, content: str = "", author: str = ""):
    if stroke is not None or fill is not None:
        annot.set_colors(stroke=stroke, fill=fill)
    if width is not None:
        annot.set_border(width=width)
    if opacity is not None:
        annot.set_opacity(opacity)
    if content:
        annot.set_info(content=content)
    if author:
        annot.set_info(title=author)
    annot.update()
    return annot


def list_annots(doc: Document, pages: Sequence[int] | None = None) -> list[AnnotInfo]:
    out: list[AnnotInfo] = []
    rng = pages if pages is not None else range(doc.page_count)
    with doc.lock:
        for i in rng:
            for a in doc.fitz[i].annots():
                info = a.info
                kind = a.type[1]
                cols = annot_colors(doc, a)
                col = cols.get("stroke") or cols.get("fill")
                out.append(AnnotInfo(i, a.xref, kind, KIND_NAMES.get(kind, kind), fitz.Rect(a.rect),
                                     info.get("content", ""), info.get("title", ""),
                                     tuple(col) if col else None, info.get("modDate", "") or info.get("creationDate", ""),
                                     _locked(a), _irt(doc.fitz, a.xref),
                                     _marked_text(doc.fitz[i], a) if kind in MARKUP_KINDS else ""))
    return out


# 선 끝(PDF_ANNOT_LE_* 번호 차례 그대로)의 PDF 이름
LE_NAMES = ("None", "Square", "Circle", "Diamond", "OpenArrow", "ClosedArrow", "Butt", "ROpenArrow", "RClosedArrow", "Slash")


def line_ends(doc: Document, a: fitz.Annot) -> tuple[int, int]:
    """(시작, 끝) 선 끝. 말풍선은 /LE 가 이름 하나라 PyMuPDF 가 못 읽어 직접 읽는다."""
    t, v = doc.fitz.xref_get_key(a.xref, "LE")
    if t == "name":
        n = v.lstrip("/")
        return (LE_NAMES.index(n) if n in LE_NAMES else 0), 0
    return tuple(a.line_ends or (0, 0))


def _marked_text(p: fitz.Page, a: fitz.Annot) -> str:
    """글자 표시 주석이 덮은 글자(줄마다 한 줄)."""
    v = a.vertices or []
    lines = []
    for k in range(0, len(v) - 3, 4):
        t = p.get_textbox(fitz.Quad(v[k:k + 4]).rect).strip()
        if t:
            lines.append(" ".join(t.split()))
    return "\n".join(lines)


_LOCK = fitz.PDF_ANNOT_IS_LOCKED | fitz.PDF_ANNOT_IS_LOCKED_CONTENTS


def _locked(a: fitz.Annot) -> bool:
    return bool(a.flags & fitz.PDF_ANNOT_IS_LOCKED)


def is_locked(doc: Document, page: int, xref: int) -> bool:
    """잠긴 주석(옮기기·고치기·지우기 막힘)."""
    with doc.lock:
        a = get(doc, page, xref)
        return a is not None and _locked(a)


def get(doc: Document, page: int, xref: int) -> fitz.Annot | None:
    p = doc.fitz[page]
    for a in p.annots():
        if a.xref == xref:
            # 주석은 쪽 객체를 약하게만 잡는다. 쪽이 먼저 사라지면 풀린 주석이 되어 만지는 순간 튕긴다.
            a._page = p
            return a
    return None


def delete(doc: Document, page: int, xref: int) -> bool:
    """주석 하나를 지운다. 잠긴 주석은 그대로 두고 False."""
    if is_locked(doc, page, xref):
        return False
    doc.snapshot()
    with doc.lock:
        a = get(doc, page, xref)
        if a:
            _drop_replies(a._page, xref)   # 답글이 지운 주석을 가리킨 채 남지 않게
            a._page.delete_annot(a)
        return a is not None


def delete_all(doc: Document, pages: Sequence[int] | None = None):
    doc.snapshot()
    with doc.lock:
        for i in (pages if pages is not None else range(doc.page_count)):
            p = doc.fitz[i]
            for a in list(p.annots()):
                if not _locked(a):
                    p.delete_annot(a)


VERTEX_KINDS = ("Line", "PolyLine", "Polygon", "Ink")  # 꼭짓점으로 그리는 주석(set_rect 로는 안 옮겨짐)


def _freetext_style(doc: Document, a: fitz.Annot) -> tuple[float, Color]:
    """글상자의 지금 글자 크기·글자색(/DA). 리치텍스트 글상자는 /DS(CSS) 로 그리므로 거기서 읽는다."""
    f = doc.fitz
    da = f.xref_get_key(a.xref, "DA")[1] or ""
    m = re.search(r"([\d.]+)\s+Tf", da)
    c = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", da)
    size = float(m.group(1)) if m else 11.0
    color = tuple(float(x) for x in c.groups()) if c else (0.0, 0.0, 0.0)
    # 리치텍스트 글상자는 /DS(CSS) 대로 그린다
    if _is_rich(f, a):
        ds = f.xref_get_key(a.xref, "DS")[1] or ""
        m = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", ds) or re.search(r"font\s*:[^;]*?([\d.]+)\s*pt", ds)
        if m:
            size = float(m.group(1))
        c = re.search(r"(?<![\w-])color\s*:\s*#([0-9a-fA-F]{6})", ds)
        # /DS 에 색이 없으면 MuPDF 는 검정으로 그린다(/DA 의 색을 안 씀)
        color = tuple(int(c.group(1)[k:k + 2], 16) / 255 for k in (0, 2, 4)) if c else (0.0, 0.0, 0.0)
    return size, color


def _is_rich(f: fitz.Document, a: fitz.Annot) -> bool:
    return f.xref_get_key(a.xref, "RC")[0] != "null" or f.xref_get_key(a.xref, "DS")[0] != "null"


def _freetext_border(a: fitz.Annot) -> Color | None:
    """글상자 테두리 색: 모양(AP) 첫 RG. MuPDF 는 테두리를 글자색으로 그리므로 우리가 고쳐 둔 색이 거기 있다."""
    if not ((a.border or {}).get("width") or 0):
        return None
    try:
        m = re.search(rb"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+RG", a._getAP())
    except Exception:  # noqa: BLE001
        m = None
    return tuple(float(x) for x in m.groups()) if m else None


def _rc_xml(text: str) -> str:
    """글상자 리치텍스트(/RC). 줄마다 문단 하나."""
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    paras = "".join(f"<p>{esc(line)}</p>" for line in (text.split("\n") or [""]))
    return ('<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" xfa:APIVersion="Acrobat:8.0.0" '
            f'xfa:spec="2.4">{paras}</body>')


def _ds_css(fontsize: float, color: Color) -> str:
    hexc = "".join(f"{round(max(0, min(1, c)) * 255):02x}" for c in color)
    return f"font-family: Malgun Gothic, sans-serif; font-size: {fontsize:g}pt; color: #{hexc};"


def _ft_redraw(f: fitz.Document, a: fitz.Annot, text: str, fontsize: float, text_color: Color,
               fill: Color | None, border: Color | None, rich: bool, **kw) -> None:
    """글상자 모양을 다시 그린다. 리치텍스트면 /RC·/DS 를 먼저 쓰고(set_info 가 /RC 를 지운다),
    그린 뒤 테두리 색을 고친다(MuPDF 는 테두리를 글자색으로 그린다)."""
    if rich:
        f.xref_set_key(a.xref, "DS", fitz.get_pdf_str(_ds_css(fontsize, text_color)))
        f.xref_set_key(a.xref, "RC", fitz.get_pdf_str(_rc_xml(text)))
    a.update(fontsize=fontsize, text_color=text_color, fill_color=fill, **kw)
    if rich and border is not None and tuple(border) != tuple(text_color):
        r, g, b = border
        ap = re.sub(rb"[\d.]+\s+[\d.]+\s+[\d.]+\s+RG", f"{r:g} {g:g} {b:g} RG".encode(), a._getAP(), count=1)
        a._setAP(ap)


def annot_colors(doc: Document, a: fitz.Annot) -> dict:
    """화면에 보일 색: stroke=선·글자 색, fill=채움색, border=글상자 테두리 색.
    글상자는 PyMuPDF 가 채움색을 stroke 로 돌려주므로 바로잡는다."""
    if a.type[1] == "FreeText":
        return {"stroke": _freetext_style(doc, a)[1], "fill": a.colors.get("stroke") or a.colors.get("fill"),
                "border": _freetext_border(a)}
    return a.colors


def _recreate_moved(p: fitz.Page, a: fitz.Annot, dx: float, dy: float) -> int:
    """꼭짓점 주석을 옮긴 꼭짓점으로 새로 만들고 속성을 옮긴 뒤 옛것을 지운다. 새 xref 를 돌려준다."""
    kind, v = a.type[1], a.vertices
    f, old = p.parent, a.xref
    keep = [(k, f.xref_get_key(old, k)) for k in ("NM", "CreationDate", "IT", "Measure", "Cap", "F")]   # 측정 축척·잠금 등
    sh = lambda q: (q[0] + dx, q[1] + dy)  # noqa: E731
    if kind == "Ink":
        n = p.add_ink_annot([[sh(q) for q in s] for s in v])
    elif kind == "Line":
        n = p.add_line_annot(sh(v[0]), sh(v[-1]))
    elif kind == "Polygon":
        n = p.add_polygon_annot([sh(q) for q in v])
    else:
        n = p.add_polyline_annot([sh(q) for q in v])
    if kind in ("Line", "PolyLine") and a.line_ends:
        n.set_line_ends(*a.line_ends)
    cols = a.colors
    n.set_colors(stroke=cols.get("stroke"), fill=cols.get("fill") if kind in ("Line", "Polygon") else None)
    b = a.border or {}
    n.set_border(width=b.get("width", 1), dashes=b.get("dashes") or None, clouds=b.get("clouds", 0) or 0)
    if a.opacity is not None and a.opacity >= 0:
        n.set_opacity(a.opacity)
    info = a.info
    n.set_info(content=info.get("content", ""), title=info.get("title", ""), subject=info.get("subject", ""))
    n.update()
    for k, (t, val) in keep:
        if t != "null":
            f.xref_set_key(n.xref, k, val)
    p.delete_annot(a)
    for r in p.annots():   # 옮긴 주석에 달린 답글이 새 주석을 가리키게
        if _irt(f, r.xref) == old:
            f.xref_set_key(r.xref, "IRT", f"{n.xref} 0 R")
    return n.xref


def update(doc: Document, page: int, xref: int, *, stroke: Color | None = None, fill: Color | None = None,
           width: float | None = None, opacity: float | None = None, content: str | None = None,
           author: str | None = None, rect: fitz.Rect | None = None, fontsize: float | None = None,
           text_color: Color | None = None, border: Color | None = None,
           dashes: Sequence[float] | None = None, subject: str | None = None, clouds: int | None = None,
           start: int | None = None, end: int | None = None, icon: str | None = None,
           locked: bool | None = None) -> int | None:
    """주석을 고친다. 꼭짓점 주석을 옮기면 새로 만들어지므로 (새) xref 를 돌려준다.

    글상자: stroke(또는 text_color)=글자색, border=테두리 색, fill=바탕색. dashes=() 는 실선.
    clouds: 구름 테두리 세기(0 없음·1·2, 사각형·타원·다각형). start·end: 선 끝(PDF_ANNOT_LE_*, 선·연결된 선,
    말풍선은 start=지시선 끝). icon: 첨부 아이콘(Graph·Paperclip·PushPin·Tag). locked: 잠그기·풀기.
    잠긴 주석은 locked=False 로 풀기 전까지 다른 것을 바꾸지 않는다(그대로 xref 를 돌려준다)."""
    if is_locked(doc, page, xref) and locked is not False:
        return xref
    doc.snapshot()
    with doc.lock:
        a = get(doc, page, xref)
        if not a:
            return None
        kind = a.type[1]
        if locked is not None:
            a.set_flags(a.flags | _LOCK if locked else a.flags & ~_LOCK)
        if rect is not None:
            old = fitz.Rect(a.rect)
            if kind in VERTEX_KINDS:
                return _recreate_moved(a._page, a, rect.x0 - old.x0, rect.y0 - old.y0)
            if kind in ("Square", "Circle"):
                # a.rect 는 테두리만큼 넓혀져 나온다. 그대로 넣으면 옮길 때마다 테두리 두께만큼 커진다.
                bw = ((a.border or {}).get("width") or 0) / 2
                rect = fitz.Rect(rect.x0 + bw, rect.y0 + bw, rect.x1 - bw, rect.y1 - bw)
            a.set_rect(rect)
        f = doc.fitz
        if kind == "FreeText":
            # 고치기 전 값(set_info 가 /RC 를 지우고, 다시 그리면 테두리가 글자색이 되므로 먼저 읽어 둔다)
            fs0, tc0 = _freetext_style(doc, a)
            bc0 = _freetext_border(a)
            rich0 = _is_rich(f, a)
        if kind != "FreeText" and (stroke is not None or fill is not None):
            a.set_colors(stroke=stroke, fill=[] if fill == () else fill)  # () = 채움 없애기(None 은 그대로 둠)
        if width is not None and kind not in ("Caret", "Text", "FileAttachment") + MARKUP_KINDS:
            a.set_border(width=width)
        if dashes is not None and kind not in ("FreeText", "Caret", "Text", "FileAttachment") + MARKUP_KINDS:
            w = (a.border or {}).get("width", 1)
            a.set_border({"width": w, "dashes": list(dashes), "style": "D"} if dashes else {"width": w, "dashes": [], "style": "S"})
        if clouds is not None and kind in ("Square", "Circle", "Polygon"):
            b = a.border or {}
            a.set_border(width=b.get("width", 1), dashes=list(b.get("dashes") or ()) if not clouds else [], clouds=clouds)
        if start is not None and kind == "FreeText":   # 말풍선 /LE 는 이름 하나(지시선 끝)
            f.xref_set_key(a.xref, "LE", "/" + LE_NAMES[start])
        elif (start is not None or end is not None) and kind in ("Line", "PolyLine"):
            le = a.line_ends or (0, 0)
            a.set_line_ends(le[0] if start is None else start, le[1] if end is None else end)
        if icon is not None and kind == "FileAttachment":
            a.set_name(icon)
        if opacity is not None:
            a.set_opacity(opacity)
        if content is not None:
            a.set_info(content=content)
        if author is not None:
            a.set_info(title=author)
        if subject is not None:
            a.set_info(subject=subject)
        if kind == "FreeText":
            # 글상자는 set_colors 를 못 쓴다. 안 준 값은 지금 값을 그대로 쓴다(안 그러면 크기 12·검정으로 돌아감).
            tc = tuple(text_color or stroke or tc0)
            # 보통 글상자는 테두리가 글자색을 따라간다(글자색만 바꿨는데 리치로 바뀌어 옛 테두리 색이 남지 않게)
            bc = tuple(border) if border is not None else bc0 if rich0 or bc0 is None else tc
            rich = rich0 or (bc is not None and bc != tc)
            text = content if content is not None else a.info.get("content", "")
            _ft_redraw(f, a, text, fontsize or fs0, tc, None if fill == () else fill, bc, rich)
        else:
            a.update()
        return a.xref


def move_by(doc: Document, page: int, xref: int, dx: float, dy: float) -> int | None:
    a = get(doc, page, xref)
    if not a:
        return None
    r = fitz.Rect(a.rect)
    return update(doc, page, xref, rect=r + (dx, dy, dx, dy))


# ── 글자 표시 주석 ────────────────────────────────────────────────
def _quads_for(page: fitz.Page, rect: fitz.Rect) -> list[fitz.Quad]:
    words = page.get_text("words", clip=rect)
    quads = []
    for w in words:
        r = fitz.Rect(w[:4]) & rect
        if r.is_empty or r.height < 1:
            continue
        quads.append(fitz.Rect(w[:4]).quad)
    return quads


def text_markup(doc: Document, page: int, rects: list[fitz.Rect], kind: str, color: Color = (1, 1, 0),
                opacity: float = 1.0, content: str = "", author: str = "") -> int | None:
    """kind: Highlight · Underline · StrikeOut · Squiggly. rects 는 글자 선택 영역(들)."""
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        quads: list[fitz.Quad] = []
        for r in rects:
            quads.extend(_quads_for(p, r))
        if not quads:
            quads = [fitz.Rect(r).quad for r in rects]
        fn = {"Highlight": p.add_highlight_annot, "Underline": p.add_underline_annot,
              "StrikeOut": p.add_strikeout_annot, "Squiggly": p.add_squiggly_annot}[kind]
        a = fn(quads)
        _finish(a, stroke=color, opacity=opacity, content=content, author=author)
        return a.xref


def area_highlight(doc: Document, page: int, rect: fitz.Rect, color: Color = (1, 1, 0), opacity: float = 0.4,
                   author: str = "") -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_rect_annot(rect)
        _finish(a, stroke=color, fill=color, width=0, opacity=opacity, author=author)
        return a.xref


def caret(doc: Document, page: int, point: fitz.Point, content: str = "", author: str = "",
          color: Color = (0.5, 0, 0.5), opacity: float = 1.0) -> int:
    doc.snapshot()
    with doc.lock:
        # add_caret_annot 은 점을 받는다. 네모를 넘기면 자리 값이 깨진(-2147483648) 주석이 생긴다
        a = doc.fitz[page].add_caret_annot(fitz.Point(point))
        a.set_rect(fitz.Rect(point.x - 6, point.y - 10, point.x + 6, point.y + 2))  # 꼭짓점이 누른 자리 위
        _finish(a, stroke=color, opacity=opacity, content=content, author=author)
        return a.xref


# ── 도형 ─────────────────────────────────────────────────────────
def rect(doc: Document, page: int, r: fitz.Rect, stroke: Color = (1, 0, 0), fill: Color | None = None,
         width: float = 1.5, opacity: float = 1.0, author: str = "") -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_rect_annot(r)
        _finish(a, stroke=stroke, fill=fill, width=width, opacity=opacity, author=author)
        return a.xref


def oval(doc: Document, page: int, r: fitz.Rect, stroke: Color = (1, 0, 0), fill: Color | None = None,
         width: float = 1.5, opacity: float = 1.0, author: str = "") -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_circle_annot(r)
        _finish(a, stroke=stroke, fill=fill, width=width, opacity=opacity, author=author)
        return a.xref


def line(doc: Document, page: int, p1: fitz.Point, p2: fitz.Point, stroke: Color = (1, 0, 0), width: float = 1.5,
         arrow: bool = False, opacity: float = 1.0, author: str = "", ends: Sequence[int] | None = None) -> int:
    """ends: (시작, 끝) 선 끝(PDF_ANNOT_LE_*). 없으면 arrow 일 때 끝만 열린 화살촉."""
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_line_annot(p1, p2)
        if ends is None and arrow:
            ends = (fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        if ends and any(ends):
            a.set_line_ends(*ends)
        _finish(a, stroke=stroke, fill=stroke, width=width, opacity=opacity, author=author)
        return a.xref


def polygon(doc: Document, page: int, pts: list[fitz.Point], stroke: Color = (1, 0, 0), fill: Color | None = None,
            width: float = 1.5, closed: bool = True, cloud: bool = False, opacity: float = 1.0, author: str = "",
            ends: Sequence[int] | None = None) -> int:
    """ends: 연결된 선(closed=False)의 (시작, 끝) 선 끝."""
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        a = p.add_polygon_annot(pts) if closed else p.add_polyline_annot(pts)
        if not closed and ends and any(ends):
            a.set_line_ends(*ends)
        if cloud:
            try:
                a.set_border(width=width, clouds=2)
            except Exception:  # noqa: BLE001
                pass
        _finish(a, stroke=stroke, fill=fill, width=width, opacity=opacity, author=author)
        return a.xref


def ink(doc: Document, page: int, strokes: list[list[fitz.Point]], stroke: Color = (0, 0, 1), width: float = 2.0,
        opacity: float = 1.0, author: str = "") -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_ink_annot([[(p.x, p.y) for p in s] for s in strokes])
        _finish(a, stroke=stroke, width=width, opacity=opacity, author=author)
        return a.xref


def _seg_point_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg_dist(a, b, c, d) -> float:
    """선분 ab 와 cd 사이 가장 가까운 거리(만나면 0)."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cross(c, d, a), cross(c, d, b), cross(a, b, c), cross(a, b, d)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return 0.0
    return min(_seg_point_dist(*a, *c, *d), _seg_point_dist(*b, *c, *d),
               _seg_point_dist(*c, *a, *b), _seg_point_dist(*d, *a, *b))


def erase_ink_at(doc: Document, page: int, point: fitz.Point, radius: float = 6,
                 prev: fitz.Point | None = None) -> bool:
    """지우개: 지우개가 지나간 길(prev → point)에 닿는 연필 주석을 지운다.

    꼭짓점만 보지 않고 꼭짓점 사이 선분까지 본다(빠르게 그은 연필은 꼭짓점이 듬성듬성하다)."""
    e0 = (prev.x, prev.y) if prev is not None else (point.x, point.y)
    e1 = (point.x, point.y)
    near = fitz.Rect(min(e0[0], e1[0]), min(e0[1], e1[1]), max(e0[0], e1[0]), max(e0[1], e1[1]))
    with doc.lock:
        p = doc.fitz[page]
        hit = []
        for a in p.annots(types=[fitz.PDF_ANNOT_INK]):
            if _locked(a):
                continue
            reach = radius + ((a.border or {}).get("width") or 1) / 2
            if not fitz.Rect(near.x0 - reach, near.y0 - reach, near.x1 + reach, near.y1 + reach).intersects(a.rect):
                continue
            for s in a.vertices or []:
                pts = [tuple(q) for q in s]
                if not pts:
                    continue
                segs = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]  # 점 하나짜리 획
                if any(_seg_seg_dist(q0, q1, e0, e1) <= reach for q0, q1 in segs):
                    hit.append(a)
                    break
        if not hit:
            return False
        doc.snapshot()
        for a in hit:
            p.delete_annot(a)
    return True


# ── 글 상자 · 노트 ────────────────────────────────────────────────
def free_text(doc: Document, page: int, r: fitz.Rect, text: str, fontsize: float = 11, text_color: Color = (0, 0, 0),
              fill: Color | None = None, border: Color | None = None, width: float = 0, opacity: float = 1.0,
              callout=None, author: str = "", typewriter: bool = False, rotate: int | None = None) -> int:
    """텍스트 박스(테두리·바탕 있음) · 타자기(typewriter=True, 테두리 없음) · 말풍선(callout).
    rotate: 글 방향(없으면 쪽 회전, 보기 회전으로 돌린 화면에서는 그 화면에 바로 서게 넘겨 받는다).

    callout 은 지시선 끝 점 하나(상자 위 가운데로 잇는다) 또는 끝 점부터 상자까지의 꼭짓점 목록.
    지시선 끝은 열린 화살촉. PyMuPDF 는 richtext 가 아니면 테두리 색을 따로 못 준다(테두리는 글자색).
    테두리 색이 글자색과 다르면 richtext 로 만든다.
    """
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        if isinstance(callout, fitz.Point):
            callout = (callout, fitz.Point((r.x0 + r.x1) / 2, r.y0))
        if typewriter:
            fill, border, width = None, None, 0
        return _add_free_text(p, r, text, fontsize, text_color, fill, border, width, opacity, callout,
                              p.rotation if rotate is None else rotate % 360, author).xref  # 회전된 쪽에서도 보이는 화면에 바로 서게


def _add_free_text(p: fitz.Page, r: fitz.Rect, text: str, fontsize: float, text_color: Color, fill: Color | None,
                   border: Color | None, width: float, opacity: float, callout, rotate: int, author: str = "") -> fitz.Annot:
    """글상자 하나(free_text·주석 가져오기·고치기가 같이 씀). callout 은 말풍선 선의 꼭짓점들(없으면 None)."""
    kw = {"rotate": rotate}
    if callout:
        kw["callout"] = tuple(fitz.Point(q) for q in callout)
        kw["line_end"] = fitz.PDF_ANNOT_LE_OPEN_ARROW
    text_color = tuple(text_color)
    rich = border is not None and tuple(border) != text_color
    if rich:
        a = p.add_freetext_annot(r, text, fontsize=fontsize, text_color=text_color, fill_color=fill,
                                 border_color=border, border_width=width, opacity=opacity, richtext=True,
                                 style=_ds_css(fontsize, text_color), **kw)
    else:
        a = p.add_freetext_annot(r, text, fontsize=fontsize, fontname="malgun", text_color=text_color,
                                 fill_color=fill, border_width=width if border is not None else 0,
                                 opacity=opacity, **kw)
    # 리치텍스트 글상자는 /Contents 가 비어 주석 목록·내보내기·속성 창에 내용이 안 나온다
    a.set_info(content=text, title=author or None)
    _ft_redraw(p.parent, a, text, fontsize, text_color, fill, tuple(border) if border is not None else None, rich)
    return a


def freetext_info(doc: Document, page: int, xref: int) -> dict | None:
    """쪽 위에서 다시 고칠 글상자의 값. kind 는 textbox · callout · typewriter, box 는 글이 들어가는 네모."""
    with doc.lock:
        a = get(doc, page, xref)
        if not a or a.type[1] != "FreeText":
            return None
        f = doc.fitz
        fs, tc = _freetext_style(doc, a)
        width = (a.border or {}).get("width") or 0
        fill = annot_colors(doc, a).get("fill") or None
        box = _inner(a.rect, _rd(f, a))
        box = fitz.Rect(box.x0 + width / 2, box.y0 + width / 2, box.x1 - width / 2, box.y1 - width / 2)
        callout = a.vertices if f.xref_get_key(a.xref, "IT")[1] == "/FreeTextCallout" else None
        kind = "callout" if callout else "typewriter" if not width and not fill else "textbox"
        op = a.opacity
        return {"kind": kind, "text": a.info.get("content", ""), "box": box, "fontsize": fs, "text_color": tc,
                "fill": tuple(fill) if fill else None, "border": _freetext_border(a) if width else None,
                "width": width, "opacity": 1.0 if op is None or op < 0 else op, "callout": callout,
                "rotate": max(a.rotation, 0), "author": a.info.get("title", ""), "subject": a.info.get("subject", "")}


def edit_free_text(doc: Document, page: int, xref: int, text: str, box: fitz.Rect | None = None) -> int | None:
    """글상자의 글(과 글이 들어갈 네모)을 바꾼다. 빈 글이면 지우고 None. 한 걸음에 실행 취소된다.

    다시 만들어 그린다(말풍선은 set_rect 로 지시선이 흐트러진다). 지시선 꼭짓점·모양·작성자는 그대로."""
    info = freetext_info(doc, page, xref)
    if info is None or is_locked(doc, page, xref):
        return None if info is None else xref
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        a = get(doc, page, xref)
        if not text.strip():
            _drop_replies(p, xref)
            p.delete_annot(a)
            return None
        f = doc.fitz
        keep = [(k, f.xref_get_key(xref, k)) for k in ("NM", "CreationDate")]
        p.delete_annot(a)
        b = fitz.Rect(box or info["box"])
        callout = info["callout"]
        if callout:   # 상자가 줄어도 지시선이 상자에 닿게(상자 쪽 끝 점을 상자 안으로)
            q = callout[-1]
            callout = list(callout[:-1]) + [(min(max(q[0], b.x0), b.x1), min(max(q[1], b.y0), b.y1))]
        n = _add_free_text(p, b, text, info["fontsize"], info["text_color"], info["fill"],
                           info["border"], info["width"], info["opacity"], callout, info["rotate"],
                           info["author"])
        for k, (t, v) in keep:
            if t != "null":
                f.xref_set_key(n.xref, k, v)
        if info["subject"]:
            f.xref_set_key(n.xref, "Subj", fitz.get_pdf_str(info["subject"]))
        for r in p.annots():   # 이 글상자에 달린 답글이 새 글상자를 가리키게
            if _irt(f, r.xref) == xref:
                f.xref_set_key(r.xref, "IRT", f"{n.xref} 0 R")
        return n.xref


def peek_hide(doc: Document, page: int, xref: int, hidden: bool) -> None:
    """쪽 위에서 고치는 동안만 주석을 숨긴다(실행 취소 기록 없이 /F 숨김 비트만). 끝나면 꼭 False 로 되돌린다."""
    with doc.lock:
        f = doc.fitz
        t, v = f.xref_get_key(xref, "F")
        fl = int(v) if t == "int" else 0
        f.xref_set_key(xref, "F", str(fl | 2 if hidden else fl & ~2))


def user_author(author: str = "") -> str:
    """작성자 칸이 비었으면 Windows 사용자 이름(새 주석·답글에 쓴다)."""
    return author or os.environ.get("USERNAME", "") or os.environ.get("USER", "")


def note(doc: Document, page: int, point: fitz.Point, content: str, icon: str = "Comment", color: Color = (1, 1, 0),
         author: str = "") -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_text_annot(point, content, icon=icon)
        _finish(a, stroke=color, content=content, author=author)
        return a.xref


# ── 답글(/IRT 로 이어진 노트. 다른 PDF 뷰어에서도 답글로 보인다) ─────────────
def _irt(f: fitz.Document, xref: int) -> int:
    t, v = f.xref_get_key(xref, "IRT")
    try:
        return int(v.split()[0]) if t == "xref" else 0
    except (ValueError, IndexError):
        return 0


def _hide_ap(a: fitz.Annot) -> None:
    """답글은 쪽에 아이콘을 그리지 않는다(빈 모양). 원 주석과 같은 자리라 눌러도 원 주석이 골라진다."""
    a._setAP(b" ")


def _drop_replies(p: fitz.Page, xref: int) -> None:
    f = p.parent
    for k in [a.xref for a in p.annots() if _irt(f, a.xref) == xref]:
        _drop_replies(p, k)
        a = next((a for a in p.annots() if a.xref == k), None)
        if a:
            p.delete_annot(a)


def is_reply(doc: Document, xref: int) -> bool:
    return _irt(doc.fitz, xref) > 0


def replies(doc: Document, page: int, xref: int) -> list[dict]:
    """그 주석에 바로 달린 답글들(단 차례). 칸: xref · content · author · modified."""
    out = []
    with doc.lock:
        f = doc.fitz
        for a in f[page].annots():
            if a.type[1] == "Text" and _irt(f, a.xref) == xref:
                info = a.info
                out.append({"xref": a.xref, "content": info.get("content", ""), "author": info.get("title", ""),
                            "modified": info.get("modDate", "") or info.get("creationDate", "")})
    return out


def add_reply(doc: Document, page: int, parent_xref: int, text: str, author: str = "") -> int | None:
    """주석에 답글을 단다: /IRT 가 원 주석을 가리키고 /RT /R 인 노트(Text) 주석. 작성자가 비면 Windows 사용자 이름."""
    doc.snapshot()
    with doc.lock:
        par = get(doc, page, parent_xref)
        if not par:
            return None
        f = doc.fitz
        r = fitz.Rect(par.rect)
        a = par._page.add_text_annot(r.tl, text, icon="Comment")
        col = par.colors.get("stroke") or par.colors.get("fill")
        _finish(a, stroke=tuple(col) if col else None, content=text, author=user_author(author))
        a.set_rect(r)
        _hide_ap(a)
        f.xref_set_key(a.xref, "IRT", f"{parent_xref} 0 R")
        f.xref_set_key(a.xref, "RT", "/R")
        return a.xref


def update_reply(doc: Document, page: int, xref: int, text: str) -> bool:
    doc.snapshot()
    with doc.lock:
        a = get(doc, page, xref)
        if not a or not _irt(doc.fitz, xref):
            return False
        a.set_info(content=text)
        a.update()
        _hide_ap(a)
        return True


def delete_reply(doc: Document, page: int, xref: int) -> bool:
    """답글 하나(와 거기 달린 답글)를 지운다."""
    doc.snapshot()
    with doc.lock:
        a = get(doc, page, xref)
        if not a or not _irt(doc.fitz, xref):
            return False
        _drop_replies(a._page, xref)
        a._page.delete_annot(a)
        return True


ATTACH_ICONS = ("Graph", "Paperclip", "PushPin", "Tag")


def attachment(doc: Document, page: int, point: fitz.Point, path: str, author: str = "", icon: str = "PushPin",
               color: Color | None = None, opacity: float | None = None) -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_file_annot(point, open(path, "rb").read(), os.path.basename(path),
                                          desc=os.path.basename(path), icon=icon if icon in ATTACH_ICONS else "PushPin")
        _finish(a, stroke=color, opacity=opacity, author=author)
        return a.xref


# ── 주석 내보내기 · 가져오기(JSON) ──────────────────────────────────
MARKUP_KINDS = ("Highlight", "Underline", "StrikeOut", "Squiggly")
FILL_KINDS = ("Square", "Circle", "Line", "Polygon", "PolyLine", "Redact")  # 채움색(/IC)이 있는 것


def _rd(f: fitz.Document, a: fitz.Annot) -> list[float] | None:
    """/RD: 바깥 네모(a.rect)와 실제로 그리는 안쪽 네모의 차이. 테두리·말풍선 선만큼 바깥이 넓다."""
    t, v = f.xref_get_key(a.xref, "RD")
    try:
        nums = [float(x) for x in v.strip("[] ").split()] if t == "array" else []
    except ValueError:
        return None
    return nums if len(nums) == 4 and any(nums) else None


def _inner(r: fitz.Rect, rd: Sequence[float] | None) -> fitz.Rect:
    """바깥 네모에서 /RD 를 걷은 안쪽 네모. MuPDF 는 /RD 를 PDF 좌표의 왼·아래·오른·위 차례로 쓴다(쪽 좌표의 아래=y1)."""
    r = fitz.Rect(r)
    return fitz.Rect(r.x0 + rd[0], r.y0 + rd[3], r.x1 - rd[2], r.y1 - rd[1]) if rd else r


def export_annots(doc: Document) -> list[dict]:
    """주석 전부를 JSON 에 담을 자료로(링크·양식 칸·팝업은 뺌). import_annots 가 되살린다."""
    out = []
    with doc.lock:
        f = doc.fitz
        for i in range(doc.page_count):
            p = f[i]
            for a in p.annots():
                kind = a.type[1]
                if kind in ("Link", "Widget", "Popup"):
                    continue
                e = {"page": i, "type": kind, "rect": list(a.rect), "info": a.info, "colors": annot_colors(doc, a),
                     "vertices": a.vertices if kind in VERTEX_KINDS or kind in MARKUP_KINDS else None,
                     "opacity": a.opacity, "border": a.border}
                rd = _rd(f, a)
                if rd:
                    e["rd"] = rd
                if kind == "Line" and a.line_ends:
                    e["line_ends"] = list(a.line_ends)
                if kind == "FreeText":
                    e["fontsize"] = _freetext_style(doc, a)[0]  # 있으면 colors 가 stroke=글자색·fill=바탕색(10-19 3차부터)
                    e["rotate"] = max(a.rotation, 0)
                    if f.xref_get_key(a.xref, "IT")[1] == "/FreeTextCallout":
                        e["callout"] = a.vertices
                if kind == "Text":
                    e["icon"] = (f.xref_get_key(a.xref, "Name")[1] or "/Note").lstrip("/")
                out.append(e)
    return out


def import_annots(doc: Document, data: list[dict]) -> tuple[int, int]:
    """export_annots 자료를 되살린다(되돌리기 한 걸음). (넣은 수, 못 넣은 수)."""
    doc.snapshot()
    n = skipped = 0
    with doc.lock:
        for e in data:
            try:
                ok = _import_one(doc, e)
            except Exception:  # noqa: BLE001
                ok = False
            n += ok
            skipped += not ok
    return n, skipped


def _import_one(doc: Document, e: dict) -> bool:
    page = e.get("page", -1)
    if not isinstance(page, int) or not 0 <= page < doc.page_count:
        return False
    p = doc.fitz[page]
    kind, r = e.get("type"), fitz.Rect(e["rect"])
    info, cols, b = e.get("info") or {}, e.get("colors") or {}, e.get("border") or {}
    v, rd = e.get("vertices"), e.get("rd")
    op = e.get("opacity")
    op = op if op is not None and op >= 0 else 1.0
    if kind == "FreeText":
        if "fontsize" in e:
            fs, tc, fill = e["fontsize"], cols.get("stroke") or (0, 0, 0), cols.get("fill") or None
        else:  # 10-19 2차까지 내보낸 파일: 글자 크기가 없고 colors 의 stroke 가 바탕색
            fs, tc, fill = 11, (0, 0, 0), cols.get("stroke") or None
        width = b.get("width") or 0
        box = _inner(r, rd)
        box = fitz.Rect(box.x0 + width / 2, box.y0 + width / 2, box.x1 - width / 2, box.y1 - width / 2)  # MuPDF 가 테두리 반만큼 넓혀 둔 것
        # 테두리 색이 없는 옛 파일은 글자색으로(그때는 테두리를 글자색으로 그렸다)
        bc = tuple(cols.get("border") or tc) if width else None
        a = _add_free_text(p, box, info.get("content", ""), fs, tuple(tc), fill, bc,
                           width, op, e.get("callout"), e.get("rotate", 0), info.get("title", ""))
        if info.get("subject"):  # 다시 그리면 고쳐 둔 테두리 색이 풀리므로 열쇠만 쓴다
            doc.fitz.xref_set_key(a.xref, "Subj", fitz.get_pdf_str(info["subject"]))
        return True
    if kind in MARKUP_KINDS:
        fn = {"Highlight": p.add_highlight_annot, "Underline": p.add_underline_annot,
              "StrikeOut": p.add_strikeout_annot, "Squiggly": p.add_squiggly_annot}[kind]
        quads = [fitz.Quad(*v[k:k + 4]) for k in range(0, len(v) - 3, 4)] if v and len(v[0]) == 2 \
            and not isinstance(v[0][0], (list, tuple)) else []
        a = fn(quads or r)  # 줄마다 하나씩(두 줄 강조가 두 줄을 덮는 네모 하나가 되지 않게)
    elif kind in ("Square", "Circle"):
        a = (p.add_rect_annot if kind == "Square" else p.add_circle_annot)(r)
    elif kind == "Text":
        a = p.add_text_annot(r.tl, info.get("content", ""), icon=e.get("icon") or "Note")
    elif kind == "Ink" and v:
        a = p.add_ink_annot(v if isinstance(v[0][0], (list, tuple)) else [v])
    elif kind == "Line" and v:
        a = p.add_line_annot(v[0], v[-1])
        if e.get("line_ends"):
            a.set_line_ends(*e["line_ends"])
    elif kind in ("Polygon", "PolyLine") and v:
        a = (p.add_polygon_annot if kind == "Polygon" else p.add_polyline_annot)(v)
    elif kind == "Redact":
        a = p.add_redact_annot(r)
    elif kind == "Caret":
        a = p.add_caret_annot(r.tl)
    else:
        return False
    stroke, fill = cols.get("stroke") or None, cols.get("fill") or None
    if kind not in FILL_KINDS:
        fill = None
    if stroke or fill:
        a.set_colors(stroke=stroke, fill=fill)
    if kind not in MARKUP_KINDS and kind not in ("Text", "Caret", "Redact") and b.get("width") is not None:
        kw = {"width": b["width"]}
        if b.get("dashes"):
            kw["dashes"] = list(b["dashes"])
        if (b.get("clouds") or 0) > 0:
            kw["clouds"] = b["clouds"]
        a.set_border(**kw)
    if kind in ("Square", "Circle") and rd:
        a.update()  # 새 테두리 두께로 /RD 를 먼저 맞춘다
        a.set_rect(_inner(r, rd))  # 그 /RD 만큼 다시 넓힌다(바깥 네모를 그대로 넣으면 가져올 때마다 커짐)
    elif kind == "Caret":
        a.set_rect(r)
    if op < 1:
        a.set_opacity(op)
    a.set_info(content=info.get("content", ""), title=info.get("title", ""), subject=info.get("subject", ""))
    a.update()
    return True


# ── 스탬프 · 서명 ─────────────────────────────────────────────────
# 새 스탬프·서명은 진짜 Stamp 주석(engine/stamps.py). 아래 글자 스탬프는 옛 버전이 찍던 모양(테두리 글상자,
# 주제 「Stamp」)으로, 이미 찍힌 것을 읽고 고치는 호환용이다.


def stamp_text(doc: Document, page: int, r: fitz.Rect, text: str, color: Color = (0.8, 0, 0), author: str = "") -> int:
    """옛 글자 스탬프: 테두리 상자 + 글자(같은 색)."""
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        dr = fitz.Rect(r) * p.rotation_matrix  # 보이는 화면의 가로·세로
        fs = max(8, min(abs(dr.height) * 0.55, abs(dr.width) / max(1, len(text)) * 1.6))
        a = p.add_freetext_annot(r, text, fontsize=fs, fontname="malgun", text_color=color, border_width=2,
                                 align=fitz.TEXT_ALIGN_CENTER, rotate=p.rotation)
        a.set_info(title=author, subject="Stamp")
        a.update()
        return a.xref


def stamp_image(doc: Document, page: int, r: fitz.Rect, image_path: str, author: str = "") -> int:
    """그림 스탬프: 그림을 외관으로 쓰는 Stamp 주석(지우고 옮길 수 있다)."""
    from . import stamps as S
    return S.add(doc, page, r, {"group": "signature", "file": image_path}, author=author)


def signature_image(doc: Document, page: int, r: fitz.Rect, image_path: str, author: str = "") -> int:
    """서명 그림을 Stamp 주석으로 넣는다(쪽 내용으로 굽는 것은 도구 탭 「평면화」)."""
    from . import stamps as S
    return S.add(doc, page, r, {"group": "signature", "file": image_path}, author=author)


# ── 측정 ─────────────────────────────────────────────────────────
# 단위 하나가 몇 pt 인가(차례가 설정 창 목록 차례). μm 는 Helvetica 로 그리는 선 캡션에서도 보이게 µ(U+00B5).
MEASURE_UNITS: dict[str, float] = {"pt": 1.0, "in": 72.0, "mm": 72 / 25.4, "cm": 72 / 2.54, "p": 12.0, "ft": 864.0,
                                   "yd": 2592.0, "m": 7200 / 2.54, "km": 7200000 / 2.54, "mile": 72.0 * 63360,
                                   "µm": 72 / 25400}
MEASURE_RANGE = (0.01, 1000000.0)
# 축척: 문서 거리 doc doc_unit = 실제 거리 real real_unit. 스냅은 끝점·중점만 쓴다(경로·교차점은 아직).
MEASURE_DEFAULT = {"doc": 1.0, "doc_unit": "cm", "real": 1.0, "real_unit": "cm",
                   "snap": {"path": False, "end": True, "mid": True, "cross": False}, "label": ""}
MEASURE_IT = {"distance": "/LineDimension", "perimeter": "/PolyLineDimension", "area": "/PolygonDimension"}


def measure_ratio(ratio: dict | None = None, unit: str = "cm", scale: float = 1.0) -> dict:
    """축척 설정 한 벌(빠진 칸은 기본값). ratio 가 없으면 옛 값(단위·배율)으로."""
    r = {k: (dict(v) if isinstance(v, dict) else v) for k, v in MEASURE_DEFAULT.items()}
    if ratio:
        for k in r:
            if k in ratio:
                r[k] = dict(r[k], **ratio[k]) if isinstance(r[k], dict) and isinstance(ratio[k], dict) else ratio[k]
    else:
        r.update(doc_unit=unit, real_unit=unit, real=scale)
    for k in ("doc_unit", "real_unit"):
        if r[k] == "μm":
            r[k] = "µm"
        if r[k] not in MEASURE_UNITS:
            r[k] = "cm"
    lo, hi = MEASURE_RANGE
    for k in ("doc", "real"):
        try:
            r[k] = min(hi, max(lo, float(r[k])))
        except (TypeError, ValueError):
            r[k] = 1.0
    return r


def ratio_text(ratio: dict) -> str:
    """「2 cm = 1 m」"""
    r = measure_ratio(ratio)
    return f"{r['doc']:g} {r['doc_unit']} = {r['real']:g} {r['real_unit']}"


def measure_factor(ratio: dict) -> float:
    """1pt 가 실제 몇 real_unit 인가."""
    r = measure_ratio(ratio)
    return 1 / MEASURE_UNITS[r["doc_unit"]] / r["doc"] * r["real"]


def measure_value(points: list[fitz.Point], kind: str, ratio: dict | None = None) -> float | None:
    k = measure_factor(ratio or {})
    if kind == "distance" and len(points) >= 2:
        return math.dist(points[0], points[1]) * k
    if kind == "perimeter" and len(points) >= 2:
        return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)) * k
    if kind == "area" and len(points) >= 3:
        s = 0.0
        for i in range(len(points)):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % len(points)]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2 * k * k
    return None


def measure(points: list[fitz.Point], kind: str, scale: float = 1.0, unit: str = "cm", ratio: dict | None = None) -> str:
    """kind: distance · perimeter · area. ratio(축척 설정)를 주면 그 축척, 아니면 1pt 를 unit 으로 바꿔 scale 배."""
    r = measure_ratio(ratio, unit, scale)
    v = measure_value(points, kind, r)
    if v is None:
        return ""
    return f"{v:.2f} {r['real_unit']}" + ("²" if kind == "area" else "")


def measure_annot(doc: Document, page: int, points: list[fitz.Point], kind: str, label: str,
                  stroke: Color = (1, 0, 0), author: str = "", *, width: float = 1.0, opacity: float = 1.0,
                  ratio: dict | None = None) -> int:
    """측정 주석. 거리는 양끝 닫힌 화살촉(채움=선 색)과 값 캡션, 둘레는 양끝 열린 화살촉, 영역은 다각형.
    축척은 /Measure 사전(/R 「2 cm = 1 m」)으로 주석에 남겨 다시 열어도 보인다."""
    r = measure_ratio(ratio)
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        p = f[page]
        if kind == "distance":
            a = p.add_line_annot(points[0], points[1])
            a.set_line_ends(fitz.PDF_ANNOT_LE_CLOSED_ARROW, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            a.set_colors(stroke=stroke, fill=stroke)
        elif kind == "perimeter":
            a = p.add_polyline_annot(points)
            a.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_OPEN_ARROW)
            a.set_colors(stroke=stroke)
        else:
            a = p.add_polygon_annot(points)
            a.set_colors(stroke=stroke)
        a.set_border(width=width)
        if opacity < 1:
            a.set_opacity(opacity)
        a.set_info(content=label, title=author, subject={"distance": "거리 측정", "perimeter": "둘레 측정",
                                                         "area": "영역 측정"}.get(kind, "측정"))
        u = r["real_unit"] + ("²" if kind == "area" else "")
        c = measure_factor(r) ** (2 if kind == "area" else 1)
        f.xref_set_key(a.xref, "IT", MEASURE_IT.get(kind, "/LineDimension"))
        f.xref_set_key(a.xref, "Measure", "<< /Type /Measure /Subtype /RL /R %s /X [ << /Type /NumberFormat /U %s /C %s /D 100 >> ] >>"
                       % (fitz.get_pdf_str(ratio_text(r)), fitz.get_pdf_str(u), f"{c:.9g}"))
        if kind == "distance":
            try:   # 선 캡션은 Helvetica 로 그려진다. 한글 라벨이면 캡션 없이 내용에만
                label.encode("cp1252")
                f.xref_set_key(a.xref, "Cap", "true")
            except UnicodeEncodeError:
                pass
        a.update()
        return a.xref


def measure_info(doc: Document, page: int, xref: int) -> dict | None:
    """측정 주석의 값(내용)·축척 글자(/Measure /R)·종류. 측정 주석이 아니면 None."""
    f = doc.fitz
    t, v = f.xref_get_key(xref, "Measure/R")
    if t != "string":
        return None
    a = get(doc, page, xref)
    return {"ratio": v, "value": a.info.get("content", "") if a else "",
            "kind": {"/LineDimension": "distance", "/PolyLineDimension": "perimeter",
                     "/PolygonDimension": "area"}.get(f.xref_get_key(xref, "IT")[1], "")}


def snap_points(page: fitz.Page) -> list[tuple[fitz.Point, str]]:
    """쪽 그림(선·네모·곡선)의 끝점(end)과 선분 가운데(mid). 측정 스냅이 이 점에 붙는다."""
    out: list[tuple[fitz.Point, str]] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return out
    for dr in drawings:
        for it in dr.get("items", []):
            op = it[0]
            if op in ("re", "qu"):
                q = fitz.Rect(it[1]).quad if op == "re" else it[1]
                cs = [fitz.Point(q.ul), fitz.Point(q.ur), fitz.Point(q.lr), fitz.Point(q.ll)]
                out += [(c, "end") for c in cs]
                out += [((cs[i] + cs[(i + 1) % 4]) / 2, "mid") for i in range(4)]
            elif op == "l":
                a, b = fitz.Point(it[1]), fitz.Point(it[2])
                out += [(a, "end"), (b, "end"), ((a + b) / 2, "mid")]
            elif op == "c":
                out += [(fitz.Point(it[1]), "end"), (fitz.Point(it[4]), "end")]
        if len(out) > 20000:   # 아주 복잡한 도면에서 멈추지 않게
            break
    return out


def nearest_snap(points: list[tuple[fitz.Point, str]], pt: fitz.Point, radius: float,
                 snap: dict | None = None) -> fitz.Point | None:
    """반지름 안에서 가장 가까운 스냅 점(켠 종류만). 없으면 None."""
    snap = snap or MEASURE_DEFAULT["snap"]
    best, bd = None, radius
    for q, kind in points:
        if not snap.get(kind):
            continue
        dd = math.dist(q, pt)
        if dd <= bd:
            best, bd = q, dd
    return best


# ── 링크 ─────────────────────────────────────────────────────────
# 외관: visible(보이는 직사각형) · color · width(1~10) · line(solid·dash·underline) · highlight(none·invert·push·outline)
LINK_STYLE = {"visible": True, "color": (0.5, 0.5, 0.5), "width": 1, "line": "solid", "highlight": "invert"}
_LINE_BS = {"solid": "S", "dash": "D", "underline": "U"}
_HL_H = {"none": "N", "invert": "I", "outline": "O", "push": "P"}


def links(doc: Document, page: int) -> list[dict]:
    with doc.lock:
        return doc.fitz[page].get_links()


def _link_xrefs(doc: Document, page: int) -> list[int]:
    return [x for x, t, _ in doc.fitz[page].annot_xrefs() if t == fitz.PDF_ANNOT_LINK]


def link_file_path(path: str, doc_path: str | None, relative: bool) -> str:
    """파일 링크에 담을 경로. relative 면 문서 폴더 기준(드라이브가 다르면 그대로 절대 경로).
    역슬래시는 PDF 문자열에서 escape 로 먹혀 C:Users… 가 되므로 / 로 바꾼다."""
    if relative and doc_path and os.path.isabs(path):
        try:
            path = os.path.relpath(path, os.path.dirname(os.path.abspath(doc_path)))
        except ValueError:
            pass
    return path.replace("\\", "/")


def resolve_link_file(path: str, doc_path: str | None) -> str:
    """파일 링크 경로 → 열 수 있는 경로(상대 경로는 문서 폴더 기준)."""
    if path and not os.path.isabs(path) and doc_path:
        path = os.path.join(os.path.dirname(os.path.abspath(doc_path)), path)
    return os.path.normpath(path) if path else path


def _nums(v: str) -> list[float]:
    out = []
    for n in re.findall(r"-?\d*\.?\d+", v):
        try:
            out.append(float(n))
        except ValueError:
            pass
    return out


def link_style(doc: Document, xref: int) -> dict:
    """링크 외관을 읽는다. 테두리 두께가 0 이거나 색이 없으면 보이지 않는 직사각형."""
    st = dict(LINK_STYLE)
    with doc.lock:
        f = doc.fitz
        width = 1.0   # PDF 기본값: /Border [0 0 1]
        t, v = f.xref_get_key(xref, "BS/W")
        tb, vb = f.xref_get_key(xref, "Border")
        if t != "null":
            width = (_nums(v) or [1.0])[0]
        elif tb == "array":
            nums = _nums(vb.split("[", 2)[1] if vb.count("[") > 1 else vb)
            width = nums[2] if len(nums) >= 3 else 1.0
        t, v = f.xref_get_key(xref, "BS/S")
        line = {"/D": "dash", "/U": "underline"}.get(v, "solid") if t == "name" else "solid"
        if line == "solid" and tb == "array" and vb.count("[") > 1:
            line = "dash"
        t, v = f.xref_get_key(xref, "C")
        color = None
        if t == "array":
            nums = _nums(v)
            if len(nums) == 3:
                color = tuple(nums)
            elif len(nums) == 1:
                color = (nums[0],) * 3
        t, v = f.xref_get_key(xref, "H")
        hl = {"/N": "none", "/O": "outline", "/P": "push"}.get(v, "invert") if t == "name" else "invert"
    st["visible"] = width > 0 and color is not None
    st["width"] = max(1, min(10, round(width))) if width > 0 else LINK_STYLE["width"]
    st["color"] = color or LINK_STYLE["color"]
    st["line"] = line
    st["highlight"] = hl
    return st


def _apply_link_style(doc: Document, xref: int, style: dict | None):
    st = {**LINK_STYLE, **(style or {})}
    f = doc.fitz
    w = max(1, min(10, int(st["width"] or 1))) if st["visible"] else 0
    line = _LINE_BS.get(st["line"], "S")
    r, g, b = st["color"]
    if w and line == "D":
        f.xref_set_key(xref, "Border", f"[0 0 {w} [3 2]]")
        f.xref_set_key(xref, "BS", f"<</W {w}/S/D/D[3 2]>>")
    else:
        f.xref_set_key(xref, "Border", f"[0 0 {w}]")
        f.xref_set_key(xref, "BS", f"<</W {w}/S/{line}>>")
    f.xref_set_key(xref, "C", f"[{r:g} {g:g} {b:g}]")
    f.xref_set_key(xref, "H", "/" + _HL_H.get(st["highlight"], "I"))


def link_info(doc: Document, page: int, xref: int) -> dict | None:
    """링크 하나: rect · kind(goto·uri·file·bad) · to_page · to_point · zoom · uri · file · style."""
    with doc.lock:
        for l in doc.fitz[page].get_links():
            if l.get("xref") != xref:
                continue
            out = {"rect": fitz.Rect(l["from"]), "kind": "bad", "to_page": None, "to_point": None, "zoom": 0.0,
                   "uri": "", "file": ""}
            k = l["kind"]
            if k == fitz.LINK_GOTO and 0 <= l.get("page", -1) < doc.page_count and _dest_ok(doc, xref):
                out.update(kind="goto", to_page=l["page"], to_point=fitz.Point(l.get("to") or (0, 0)))
                # PyMuPDF 는 XYZ 의 배율을 읽지 않는다(늘 0): 목적지 배열에서 직접 읽는다
                for key in ("A/D", "Dest"):
                    t, v = doc.fitz.xref_get_key(xref, key)
                    m = re.search(r"/XYZ\s+(\S+)\s+(\S+)\s+([-\d.]+)", v) if t == "array" else None
                    if m:
                        out["zoom"] = (_nums(m.group(3)) or [0.0])[0]
                        break
            elif k == fitz.LINK_NAMED and str(l.get("page", "")).isdigit() and 1 <= int(l["page"]) <= doc.page_count:
                out.update(kind="goto", to_page=int(l["page"]) - 1, to_point=fitz.Point(0, 0))
            elif k == fitz.LINK_URI and l.get("uri"):
                out.update(kind="uri", uri=l["uri"])
            elif k in (fitz.LINK_LAUNCH, fitz.LINK_GOTOR) and _launch_file(doc, xref):   # 파일 경로가 있으면 GOTOR 로 읽힌다
                out.update(kind="file", file=_launch_file(doc, xref))
            break
        else:
            return None
    out["style"] = link_style(doc, xref)
    return out


def _dest_ok(doc: Document, xref: int) -> bool:
    """쪽 이동 목적지가 실제 쪽을 가리키나(없는 객체를 가리키면 MuPDF 는 첫 쪽으로 읽는다)."""
    for key in ("A/D", "Dest"):
        t, v = doc.fitz.xref_get_key(xref, key)
        if t == "array":
            m = re.match(r"\[\s*(\d+)\s+0\s+R", v)
            if not m:
                return True
            n = int(m.group(1))
            return 0 < n < doc.fitz.xref_length() and doc.fitz.xref_get_key(n, "Type")[1] == "/Page"
    return True


def _launch_file(doc: Document, xref: int) -> str:
    """파일 링크 경로(get_links 는 한글을 %EC… 로 바꿔 주므로 사전에서 직접 읽는다)."""
    for key in ("A/F/UF", "A/F/F", "A/F"):
        t, v = doc.fitz.xref_get_key(xref, key)
        if t == "string" and v:
            return v
    return ""


def _check_page(doc: Document, to_page: int | None):
    if to_page is not None and not 0 <= to_page < doc.page_count:
        raise ValueError(f"쪽 번호는 1~{doc.page_count} 사이여야 합니다")


def _insert_link(doc: Document, page: int, r: fitz.Rect, to_page, to_point, zoom, uri, file, style) -> int:
    if to_page is not None:
        d = {"kind": fitz.LINK_GOTO, "from": r, "page": to_page, "to": fitz.Point(to_point or (0, 0))}
        if zoom:
            d["zoom"] = float(zoom)
    elif uri:
        d = {"kind": fitz.LINK_URI, "from": r, "uri": uri}
    elif file:
        d = {"kind": fitz.LINK_LAUNCH, "from": r, "file": file.replace("\\", "/")}
    else:
        raise ValueError("링크 대상이 없습니다")
    before = set(_link_xrefs(doc, page))
    doc.fitz[page].insert_link(d)
    xref = next((x for x in _link_xrefs(doc, page) if x not in before), 0)
    if xref:
        if file:   # 한글 경로는 UTF-16 글자열로(PyMuPDF 는 UTF-8 바이트로 넣는다)
            s = fitz.get_pdf_str(d["file"])
            doc.fitz.xref_set_key(xref, "A/F/F", s)
            doc.fitz.xref_set_key(xref, "A/F/UF", s)
        _apply_link_style(doc, xref, style)
    return xref


def add_link(doc: Document, page: int, r: fitz.Rect, *, to_page: int | None = None, to_point=None, zoom: float = 0,
             uri: str | None = None, file: str | None = None, style: dict | None = None) -> int:
    """링크를 넣고 xref 를 돌려준다. 쪽 이동은 to_point(쪽 좌표 왼쪽 위)·zoom(0 이면 보던 배율 그대로)까지 담는다."""
    if to_page is None and not uri and not file:
        raise ValueError("링크 대상이 없습니다")
    _check_page(doc, to_page)
    doc.snapshot()
    with doc.lock:
        return _insert_link(doc, page, fitz.Rect(r), to_page, to_point, zoom, uri, file, style)


def update_link(doc: Document, page: int, xref: int, *, rect: fitz.Rect | None = None, target: dict | None = None,
                style: dict | None = None) -> int:
    """링크의 자리·대상·외관을 바꾼다(되돌리기 한 번). target 은 add_link 의 대상 인자(to_page·to_point·zoom·uri·file).
    지우고 새로 넣는 것이라 xref 가 바뀔 수 있어 새 xref 를 돌려준다."""
    info = link_info(doc, page, xref)
    if info is None:
        return 0
    if target is None:
        if info["kind"] == "goto":
            target = {"to_page": info["to_page"], "to_point": info["to_point"], "zoom": info["zoom"]}
        elif info["kind"] == "uri":
            target = {"uri": info["uri"]}
        elif info["kind"] == "file":
            target = {"file": info["file"]}
        else:
            raise ValueError("링크가 잘못되었습니다.")
    if target.get("to_page") is None and not target.get("uri") and not target.get("file"):
        raise ValueError("링크 대상이 없습니다")
    _check_page(doc, target.get("to_page"))
    doc.snapshot()
    with doc.lock:
        _delete_link_xref(doc, page, xref)
        return _insert_link(doc, page, fitz.Rect(rect if rect is not None else info["rect"]), target.get("to_page"),
                            target.get("to_point"), target.get("zoom", 0), target.get("uri"), target.get("file"),
                            style if style is not None else info["style"])


def _delete_link_xref(doc: Document, page: int, xref: int):
    p = doc.fitz[page]
    for l in p.get_links():
        if l.get("xref") == xref:
            p.delete_link(l)
            return


def delete_link(doc: Document, page: int, link: dict | int):
    """링크 하나를 지운다(get_links 의 dict 또는 xref)."""
    doc.snapshot()
    with doc.lock:
        if isinstance(link, int):
            _delete_link_xref(doc, page, link)
        else:
            doc.fitz[page].delete_link(link)


# ── 교정(수정) 표시 ───────────────────────────────────────────────
def mark_redact(doc: Document, page: int, r: fitz.Rect, fill: Color = (0, 0, 0), overlay_text: str = "") -> int:
    doc.snapshot()
    with doc.lock:
        a = doc.fitz[page].add_redact_annot(r, text=overlay_text or None, fill=fill,
                                            fontname="malgun" if overlay_text else None)
        a.update()
        return a.xref


def apply_redactions(doc: Document, pages: Sequence[int] | None = None, images: bool = True):
    doc.snapshot()
    with doc.lock:
        for i in (pages if pages is not None else range(doc.page_count)):
            doc.fitz[i].apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_PIXELS if images else fitz.PDF_REDACT_IMAGE_NONE)


# ── 텍스트·이미지 편집(원문 내용) ─────────────────────────────────
ITALIC_SLANT = 0.2   # 기울임: 글 방향으로 기울이는 정도(맑은 고딕에는 기울임 글꼴 파일이 없다)
TEXT_PAD = 2         # 쪽에 넣는 글 상자의 여유(화면 글 상자와 줄바꿈이 어긋나지 않게)


def _font_res(p: fitz.Page, family: str, text: str, bold: bool, italic: bool) -> tuple[str, str, bool, bool]:
    """글꼴 이름 → 쪽에 넣은 (글꼴 자원 이름, 글꼴 파일, 굵게를 흉내 낼지, 기울임을 흉내 낼지). 빈 이름이거나 글자를 못 그리는 글꼴이면 맑은 고딕."""
    if family and family != F.DEFAULT_FAMILY:
        path, fake_bold, fake_italic = F.font_file(family, bold, italic)
        if F.covers(path, text):
            base = re.sub(r"[^A-Za-z0-9]", "", os.path.splitext(os.path.basename(path))[0]) or "F"
            fonts = {f[4]: f[3] for f in p.get_fonts()}
            name, k = base, 1
            while name in fonts and re.match(r"[A-Z]{6}\+", fonts[name] or ""):  # 저장 때 줄인 글꼴은 피한다
                k += 1
                name = f"{base}{k}"
            p.insert_font(fontname=name, fontfile=path)
            return name, path, fake_bold, fake_italic
    name = korean_font_name(p, bold)  # 굵기마다 이름을 나누고, 저장 때 줄인 글꼴은 피한다
    p.insert_font(fontname=name, fontfile=korean_fontfile(bold))
    return name, korean_fontfile(bold), False, italic


def _insert_box(p: fitz.Page, r: fitz.Rect, text: str, fontsize: float, color: Color, bold: bool, align: int,
                rotate: int | None = None, italic: bool = False, family: str = "", grow: bool = False):
    """상자에 글을 넣는다(글꼴 이름이 비면 맑은 고딕). 방향은 기본이 쪽 회전(화면에 바로 섬), 넘치면 글 방향의 아래로 늘린다.

    grow=True 면 줄이 상자보다 길 때 줄바꿈하지 않고 상자를 글 방향으로 넓힌다(정렬 쪽을 붙박고)."""
    rot = p.rotation if rotate is None else rotate
    name, path, fake_bold, italic = _font_res(p, family, text, bold, italic)
    extra = {"render_mode": 2, "border_width": 0.04, "fill": color} if fake_bold else {}
    if grow:
        fo = F.font_object(path)
        need = max(fo.text_length(ln, fontsize) for ln in text.split("\n")) + TEXT_PAD + 1
        lr = fitz.Rect(r) * fitz.Matrix(rot)
        lr.normalize()
        if lr.width < need:
            if align == 1:
                lr.x0 -= (need - lr.width) / 2
                lr.x1 = lr.x0 + need
            elif align == 2:
                lr.x0 = lr.x1 - need
            else:
                lr.x1 = lr.x0 + need
            r = lr * fitz.Matrix(-rot)
            r.normalize()

    def put(box: fitz.Rect) -> float:
        morph = None
        if italic:  # 글 방향 기준 가로 기울이기를 쪽 좌표로 옮긴다. 상자 가운데를 축으로 해서 위아래가 고르게 기운다
            m = fitz.Matrix(rot) * fitz.Matrix(1, 0, ITALIC_SLANT, 1, 0, 0) * fitz.Matrix(-rot)
            morph = (fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2), m)
        return p.insert_textbox(box, text, fontsize=fontsize, fontname=name, color=color, align=align, rotate=rot,
                                morph=morph, **extra)

    rc = put(r)
    if rc < 0:  # 상자가 작으면 아래로 늘려 다시
        lr = fitz.Rect(r) * fitz.Matrix(rot)
        lr.normalize()
        lr.y1 += -rc + 4
        r2 = lr * fitz.Matrix(-rot)
        r2.normalize()
        put(r2)


def text_box_size(text: str, fontsize: float, bold: bool = False, min_width: float = 0) -> tuple[float, float]:
    """글을 줄바꿈 없이 넣을 상자 크기(pt, 글 방향 기준). 너비는 가장 긴 줄, 높이는 줄 수만큼."""
    f = fitz.Font(fontfile=korean_fontfile(bold))
    lines = text.split("\n")
    w = max(f.text_length(ln, fontsize) for ln in lines) + TEXT_PAD
    lh = fontsize * (f.ascender - f.descender)
    return max(w, min_width), lh * len(lines) + TEXT_PAD


def add_text(doc: Document, page: int, r: fitz.Rect, text: str, fontsize: float = 10, color: Color = (0, 0, 0),
             bold: bool = False, align: int = 0, italic: bool = False, rotate: int | None = None) -> bool:
    """쪽 내용에 글자를 직접 넣는다(주석 아님). 빈 글이면 아무것도 안 하고 False(실행 취소 자리도 안 만듦).
    rotate: 글 방향(없으면 쪽 회전)."""
    if not text.strip():
        return False
    doc.snapshot()
    with doc.lock:
        _insert_box(doc.fitz[page], r, text, fontsize, color, bold, align, italic=italic,
                    rotate=None if rotate is None else rotate % 360)
    return True


def add_image(doc: Document, page: int, r: fitz.Rect, path: str, keep_proportion: bool = True):
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        p.insert_image(r, filename=path, keep_proportion=keep_proportion, overlay=True, rotate=p.rotation)


def block_style(doc: Document, page: int, block_rect: fitz.Rect) -> dict:
    """글자 덩어리 첫 글자의 모양: 크기·색·굵게·기울임·글꼴 이름(고를 수 있는 글꼴 가운데 맞는 것, 없으면 빈 이름)."""
    with doc.lock:
        spans = [s for b in doc.fitz[page].get_text("dict", clip=block_rect)["blocks"] if b.get("type") == 0
                 for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    if not spans:
        return {"fontsize": 11.0, "color": (0, 0, 0), "bold": False, "italic": False, "family": ""}
    s = spans[0]
    c = s["color"]
    raw = s["font"].split("+", 1)[-1]
    key = re.sub(r"[^a-z0-9]", "", re.sub(r"[-,](bold|italic|oblique|regular|bolditalic)$", "", raw, flags=re.I).lower())
    family = next((f for f in F.font_families() if re.sub(r"[^a-z0-9]", "", f.lower()) == key), "")
    if not family and key.startswith("malgun"):
        family = F.DEFAULT_FAMILY
    return {"fontsize": round(s["size"], 1), "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
            "bold": bool(s["flags"] & 16) or "bold" in raw.lower(), "italic": bool(s["flags"] & 2),
            "family": family}


def edit_block(doc: Document, page: int, block_rect: fitz.Rect, new_text: str, fontsize: float | None = None,
               color: Color = (0, 0, 0), bold: bool = False, align: int = 0, *, italic: bool = False,
               family: str = "", new_rect: fitz.Rect | None = None):
    """원문 글자 덩어리를 고친다: 자리를 교정(흰 칠)하고 새 글자를 같은 자리(new_rect 면 그 자리)에 넣는다."""
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        if fontsize is None:
            spans = [s for b in p.get_text("dict", clip=block_rect)["blocks"] if b.get("type") == 0
                     for l in b["lines"] for s in l["spans"]]
            fontsize = spans[0]["size"] if spans else 11
            if spans and not color:
                c = spans[0]["color"]
                color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
        rot = _text_rotation(p, block_rect)  # 원래 글 방향(지우기 전에)
        redact_only(p, [block_rect], fill=(1, 1, 1), images=fitz.PDF_REDACT_IMAGE_NONE)
        if new_text.strip():
            _insert_box(p, fitz.Rect(new_rect or block_rect), new_text, fontsize, color, bold, align, rotate=rot,
                        italic=italic, family=family, grow=True)


def delete_image(doc: Document, page: int, r: fitz.Rect):
    """그 자리의 그림을 지운다(교정으로)."""
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        redact_only(p, [r], fill=None, images=fitz.PDF_REDACT_IMAGE_REMOVE, text=fitz.PDF_REDACT_TEXT_NONE)


# 그림 돌리기·뒤집기·불투명도·옮기기. 그림을 그리는 줄(/이름 Do)을 그 자리에서 「q 행렬 cm /상태 gs /이름 Do Q」 로 감싼다.
# 지웠다 다시 넣지 않으니 겹친 다른 그림·글이 다치지 않고 앞뒤 차례도 그대로다. 다시 바꾸면 감싼 것을 통째로 새로 쓴다.
_OP_GS = "RPDFA"  # 그림 불투명도 그래픽 상태 이름 머리(뒤에 백분율)
_NUM = rb"[-+]?(?:\d+\.?\d*|\.\d+)"


def _pdf_num(v: float) -> str:
    t = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


def _image_spot(p: fitz.Page, xref: int, rect: fitz.Rect) -> tuple[dict, tuple, list]:
    """rect 자리에 그린 그림 xref 의 (그림 정보, 그리는 줄 자리, 그 쪽의 같은 그림 줄 전부).

    줄 자리는 (내용 스트림 xref, 시작, 끝, 감싼 행렬 또는 None, 불투명도 백분율 또는 None)."""
    infos = [i for i in p.get_image_info(xrefs=True) if i.get("xref") == xref]
    names = [im[7] for im in p.get_images(full=True) if im[0] == xref and im[9] == 0]
    if not infos or not names:
        raise ValueError("이 그림은 쪽에 바로 놓인 그림이 아니라서 바꿀 수 없습니다")
    alt = b"|".join(re.escape(n.encode("latin-1")) for n in names)
    wrap = re.compile(rb"q\s+(" + _NUM + rb"(?:\s+" + _NUM + rb"){5})\s+cm\s+/" + _OP_GS.encode()
                      + rb"(\d+)\s+gs\s+/(?:" + alt + rb")\s+Do\s+Q")
    plain = re.compile(rb"/(?:" + alt + rb")\s+Do\b")
    spots = []
    for cx in p.get_contents():
        data = p.parent.xref_stream(cx) or b""
        taken = []
        for m in wrap.finditer(data):
            spots.append((cx, m.start(), m.end(), fitz.Matrix(*map(float, m.group(1).split())), int(m.group(2))))
            taken.append((m.start(), m.end()))
        for m in plain.finditer(data):
            if not any(a <= m.start() < b for a, b in taken):
                spots.append((cx, m.start(), m.end(), None, None))
    order = {cx: n for n, cx in enumerate(p.get_contents())}
    spots.sort(key=lambda t: (order[t[0]], t[1]))
    if len(spots) != len(infos):
        raise ValueError("이 그림은 다른 묶음 안에서도 그려져 있어 바꿀 수 없습니다")
    k = min(range(len(infos)), key=lambda n: sum(abs(a - b) for a, b in zip(infos[n]["bbox"], rect)))
    return infos[k], spots[k], spots


def image_opacity(doc: Document, page: int, xref: int, rect: fitz.Rect) -> float:
    """그림의 불투명도(0~1). 이 앱으로 바꾼 적 없으면 1."""
    with doc.lock:
        try:
            spot = _image_spot(doc.fitz[page], xref, rect)[1]
        except ValueError:
            return 1.0
    return 1.0 if spot[4] is None else spot[4] / 100


def transform_image(doc: Document, page: int, xref: int, rect: fitz.Rect, *, rotate: int = 0, flip_h: bool = False,
                    flip_v: bool = False, opacity: float | None = None, new_rect: fitz.Rect | None = None) -> fitz.Rect:
    """rect 자리의 그림을 돌리고(rotate 도, 보이는 화면에서 시계 방향) · 뒤집고(보이는 화면의 좌우·상하) ·
    불투명도를 바꾸고 · new_rect 로 옮기거나 늘린다(돌린 뒤의 바깥 상자 기준). 새 자리(회전 전 쪽 좌표)를 돌려준다."""
    with doc.lock:
        p = doc.fitz[page]
        info, (cx, start, end, prev, prev_op), _ = _image_spot(p, xref, rect)
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        p = f[page]
        m_old = fitz.Matrix(info["transform"])         # 그림 단위 네모 → 쪽(회전 전, 아래로 y)
        box = fitz.Rect(info["bbox"])
        c = (box.tl + box.br) / 2
        lin = fitz.Matrix(rotate) if rotate % 360 else fitz.Matrix(1, 1)
        if flip_h or flip_v:
            rm = p.rotation_matrix
            rl = fitz.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
            lin = lin * rl * fitz.Matrix(-1 if flip_h else 1, -1 if flip_v else 1) * ~rl
        m_new = m_old * fitz.Matrix(1, 0, 0, 1, -c.x, -c.y) * lin * fitz.Matrix(1, 0, 0, 1, c.x, c.y)
        if new_rect is not None:
            b = fitz.Rect(0, 0, 1, 1) * m_new
            nr = fitz.Rect(new_rect)
            nr.normalize()
            sx, sy = nr.width / max(b.width, 1e-6), nr.height / max(b.height, 1e-6)
            m_new = m_new * fitz.Matrix(1, 0, 0, 1, -b.x0, -b.y0) * fitz.Matrix(sx, sy) * fitz.Matrix(1, 0, 0, 1, nr.x0, nr.y0)
        tm = p.transformation_matrix                  # PDF 좌표 → 쪽 좌표
        flip = fitz.Matrix(1, 0, 0, -1, 0, 1)         # MuPDF 는 그림 단위 네모를 위아래 뒤집어 쓴다
        old_total, new_total = flip * m_old * ~tm, flip * m_new * ~tm
        outer = old_total if prev is None else ~prev * old_total   # 그림 줄 바깥의 좌표계
        x = new_total * ~outer
        op = (1.0 if prev_op is None else prev_op / 100) if opacity is None else opacity
        pct = max(0, min(100, int(round(op * 100))))
        gs = f"{_OP_GS}{pct}"
        _own_xobjects(f, p.xref, inherit=True)        # 쪽 자원을 이 쪽 것으로(다른 쪽과 같이 쓰던 사전이면 사본)
        t, v = f.xref_get_key(p.xref, "Resources/ExtGState")
        if t == "xref":
            f.xref_set_key(p.xref, "Resources/ExtGState", f.xref_object(int(v.split()[0]), compressed=True))
        elif t != "dict":
            f.xref_set_key(p.xref, "Resources/ExtGState", "<<>>")
        # 불투명도는 /ca 대신 고른 밝기의 소프트 마스크로 건다. MuPDF 는 /ca 가 0.5 아래인 그림을 글 추출에서 빼서
        # (워터마크로 보고) 편집에서 다시 고를 수 없게 된다. 마스크 좌표는 gs 를 거는 때의 그림 단위 네모다
        if pct >= 100:
            f.xref_set_key(p.xref, f"Resources/ExtGState/{gs}", "<</Type/ExtGState>>")
        else:
            mask = f.get_new_xref()
            f.update_object(mask, "<</Type/XObject/Subtype/Form/BBox[-1 -1 2 2]"
                                  "/Group<</Type/Group/S/Transparency/CS/DeviceGray>>>>")
            f.update_stream(mask, f"{pct / 100:g} g -1 -1 3 3 re f".encode())
            f.xref_set_key(p.xref, f"Resources/ExtGState/{gs}",
                           f"<</Type/ExtGState/SMask<</Type/Mask/S/Luminosity/G {mask} 0 R>>>>")
        data = f.xref_stream(cx)
        name = re.search(rb"/(\S+?)\s+Do", data[start:end]).group(1)
        new = (f"q {' '.join(_pdf_num(n) for n in tuple(x))} cm /{gs} gs /".encode() + name + b" Do Q")
        f.update_stream(cx, data[:start] + new + data[end:])
        p._image_info = None
        return fitz.Rect(0, 0, 1, 1) * m_new


def images_on_page(doc: Document, page: int) -> list[dict]:
    """쪽의 그림들과 자리."""
    out = []
    with doc.lock:
        p = doc.fitz[page]
        for info in p.get_image_info(xrefs=True):
            out.append({"xref": info.get("xref", 0), "rect": fitz.Rect(info["bbox"]),
                        "width": info.get("width"), "height": info.get("height")})
    return out


def replace_image(doc: Document, page: int, xref: int, path: str):
    """그 쪽의 그림만 바꾼다. 다른 쪽도 같은 그림 객체를 쓰면 새 그림 객체를 만들어 이 쪽 자원만 그것을 가리키게 한다.

    PyMuPDF 의 replace_image 는 그림 객체 자체를 덮어써서, 로고처럼 여러 쪽이 같이 쓰는 그림은 모든 쪽이 바뀐다."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        p = f[page]
        uses = [(im[7], im[9]) for im in p.get_images(full=True) if im[0] == xref]  # (자원 이름, 그 이름을 가진 객체·0=쪽)
        shared = any(im[0] == xref for j in range(f.page_count) if j != page for im in f[j].get_images(full=True))
        if not shared or not uses:
            p.replace_image(xref, filename=path)
            return
        new = p.insert_image(p.rect, filename=path)  # 새 그림 객체를 얻으려고 넣은 것이라 그리는 줄은 바로 비운다
        f.update_stream(p.get_contents()[-1], b" ")
        forms = {x[0]: (x[1], x[2]) for x in p.get_xobjects()}  # 폼 xref → (자원 이름, 부르는 객체·0=쪽)
        own: dict[int, int] = {}

        def holder(owner: int) -> int:
            """owner(쪽이면 0) 대신 이 쪽만 쓰는 객체. 자원 사전도 이 쪽 것으로 떼어 낸다."""
            if owner in own:
                return own[owner]
            if owner == 0:
                _own_xobjects(f, p.xref, inherit=True)
                own[0] = p.xref
                return p.xref
            nf = f.get_new_xref()  # 폼은 다른 쪽도 부를 수 있으니 사본을 떠서 이 쪽만 사본을 부르게
            f.update_object(nf, f.xref_object(owner, compressed=True))
            f.update_stream(nf, f.xref_stream(owner))
            _own_xobjects(f, nf)
            own[owner] = nf
            name, up = forms.get(owner, (None, 0))
            if name:
                f.xref_set_key(holder(up), f"Resources/XObject/{name}", f"{nf} 0 R")
            return nf

        for name, ref in uses:
            f.xref_set_key(holder(ref), f"Resources/XObject/{name}", f"{new} 0 R")
        p._image_info = None


def _own_xobjects(f: fitz.Document, xref: int, inherit: bool = False):
    """객체의 /Resources 와 그 안 /XObject 를 이 객체만의 사전으로 만든다(같이 쓰던 사전은 사본을 떠서 붙임).

    inherit=True(쪽)면 /Parent 에서 물려받은 /Resources 도 사본으로 붙인다."""
    t, v = f.xref_get_key(xref, "Resources")
    if t == "null" and inherit:
        node, seen = xref, set()
        while t == "null" and node not in seen:
            seen.add(node)
            pt, pv = f.xref_get_key(node, "Parent")
            if pt != "xref":
                break
            node = int(pv.split()[0])
            t, v = f.xref_get_key(node, "Resources")
        if t == "dict":
            f.xref_set_key(xref, "Resources", v)
    if t == "xref":
        f.xref_set_key(xref, "Resources", f.xref_object(int(v.split()[0]), compressed=True))
    elif t not in ("dict",):
        f.xref_set_key(xref, "Resources", "<<>>")
    t, v = f.xref_get_key(xref, "Resources/XObject")
    if t == "xref":
        f.xref_set_key(xref, "Resources/XObject", f.xref_object(int(v.split()[0]), compressed=True))
    elif t != "dict":
        f.xref_set_key(xref, "Resources/XObject", "<<>>")


# ── 양식 칸(위젯) ─────────────────────────────────────────────────
FIELD_TYPES = {
    "text": fitz.PDF_WIDGET_TYPE_TEXT, "checkbox": fitz.PDF_WIDGET_TYPE_CHECKBOX,
    "radio": fitz.PDF_WIDGET_TYPE_RADIOBUTTON, "combo": fitz.PDF_WIDGET_TYPE_COMBOBOX,
    "list": fitz.PDF_WIDGET_TYPE_LISTBOX, "button": fitz.PDF_WIDGET_TYPE_BUTTON,
    "signature": fitz.PDF_WIDGET_TYPE_SIGNATURE,
}
FIELD_TYPE_KO = {"text": "텍스트", "checkbox": "체크 박스", "radio": "라디오 버튼", "combo": "콤보 박스",
                 "list": "목록 박스", "button": "누름 버튼", "signature": "디지털 서명", "image": "이미지", "date": "날짜"}


def _unique_name(doc: Document, base: str, taken: set[str] | None = None) -> str:
    """기본 이름 + 번호(텍스트1, 텍스트2 …), 문서 안에서 겹치지 않게."""
    names = {w.field_name for i in range(doc.page_count) for w in doc.fitz[i].widgets()} | (taken or set())
    n = 1
    while f"{base}{n}" in names:
        n += 1
    return f"{base}{n}"


def _copy_name(doc: Document, name: str, taken: set[str] | None = None) -> str:
    """복사한 칸 이름: 끝 번호를 떼고 겹치지 않는 다음 번호(텍스트1 → 텍스트2)."""
    base = re.sub(r"\d+$", "", name) or name
    names = {w.field_name for i in range(doc.page_count) for w in doc.fitz[i].widgets()} | (taken or set())
    n = 2 if base == name else 1
    while f"{base}{n}" in names:
        n += 1
    return f"{base}{n}"


def add_field(doc: Document, page: int, r: fitz.Rect, ftype: str, name: str | None = None, value=None,
              options: list[str] | None = None, label: str = "", fontsize: float = 10,
              border: Color | None = (0.6, 0.6, 0.6), fill: Color | None = (1, 1, 1), date: bool = False,
              snapshot: bool = True) -> int:
    if snapshot:
        doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        base_ko = FIELD_TYPE_KO.get("date" if date else ftype, ftype)
        w = fitz.Widget()
        radio = ftype == "radio"
        # PyMuPDF 1.27 은 라디오를 바로 만들면 bad xref 로 실패하고 망가진 칸을 남긴다. 체크 박스로 만든 뒤 라디오 표시를 단다.
        w.field_type = FIELD_TYPES["checkbox" if radio else "text" if ftype in ("date", "image") else ftype]
        w.field_name = name or _unique_name(doc, base_ko)
        w.rect = r
        w.text_fontsize = fontsize
        w.text_font = "Helv"
        if border is not None:
            w.border_color = border
            w.border_width = 1
        if fill is not None:
            w.fill_color = fill
        if ftype in ("combo", "list"):
            w.choice_values = options or ["항목 1", "항목 2"]
        if ftype == "button":
            w.button_caption = label or "버튼"
        if ftype in ("checkbox", "radio"):
            w.field_value = bool(value)
        elif value is not None:
            w.field_value = value
        if date:
            w.script_format = 'AFDate_FormatEx("yyyy-mm-dd");'
            w.script_stroke = 'AFDate_KeystrokeEx("yyyy-mm-dd");'
        annot = p.add_widget(w)
        if radio:
            x = annot.xref
            doc.fitz.xref_set_key(x, "Ff", str(_RADIO_FLAGS))
            # 이름을 안 주면 문서에서 마지막에 만든 라디오 묶음에 붙인다(다른 쪽에 찍어도 한 묶음)
            group = name or getattr(doc, "_last_radio", "")
            names = {r.field_name for i in range(doc.page_count) for r in doc.fitz[i].widgets()
                     if r.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON and r.xref != x}
            if not group or (not name and group not in names):
                prev = [(r.xref, r.field_name) for i in range(doc.page_count) for r in doc.fitz[i].widgets()
                        if r.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON and r.xref != x]
                group = max(prev)[1] if prev else w.field_name
            doc._last_radio = group
            _radio_move(doc.fitz, x, group)
            _refresh_widget(p, x)
        return annot.xref


# ── 라디오 묶음 ─────────────────────────────────────────────────
# PDF 의 라디오 묶음 = 이름(/T)·고른 값(/V)을 가진 부모 칸 하나 + 동그라미 칸들(/Kids).
# 동그라미마다 켜짐 이름(Choice1, Choice2 …)이 달라야 다른 뷰어에서도 하나만 켜지고, 자료 내보내기에 무엇을 골랐는지 남는다.
_RADIO_FLAGS = (1 << 15) | (1 << 14)  # Radio · NoToggleToOff


def _xrefs(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"(\d+)\s+0\s+R", text or "")]


def _get_refs(f: fitz.Document, xref: int, key: str) -> list[int]:
    t, v = f.xref_get_key(xref, key)
    if t == "xref":
        return _xrefs(f.xref_object(int(v.split()[0])))
    return _xrefs(v) if t == "array" else []


def _set_refs(f: fitz.Document, xref: int, key: str, xs: list[int]) -> None:
    arr = "[" + " ".join(f"{x} 0 R" for x in xs) + "]"
    t, v = f.xref_get_key(xref, key)
    if t == "xref":
        f.update_object(int(v.split()[0]), arr)
    else:
        f.xref_set_key(xref, key, arr)


def _acro_holder(f: fitz.Document) -> tuple[int, str]:
    """AcroForm/Fields 를 가진 (xref, 열쇠)."""
    cat = f.pdf_catalog()
    t, v = f.xref_get_key(cat, "AcroForm")
    return (int(v.split()[0]), "Fields") if t == "xref" else (cat, "AcroForm/Fields")


def _on_name(f: fitz.Document, x: int) -> str:
    """동그라미 칸의 켜짐 이름(AP/N 의 Off 아닌 열쇠)."""
    for ap in ("AP/N", "AP/D"):
        t, v = f.xref_get_key(x, ap)
        if t == "xref":
            v = f.xref_object(int(v.split()[0]))
        for n in re.findall(r"/([^\s/<>\[\]()]+)\s*(?:\d+\s+0\s+R|<<)", v or ""):
            if n != "Off":
                return n
    return ""


def _sync_radio(f: fitz.Document, x: int) -> None:
    """묶음의 고른 값(/V)을 켜진 동그라미의 켜짐 이름(이름 객체)으로. PyMuPDF 는 글자열로 넣는다."""
    t, v = f.xref_get_key(x, "Parent")
    if t != "xref":
        return
    par = int(v.split()[0])
    on = "/Off"
    for k in _get_refs(f, par, "Kids"):
        a = f.xref_get_key(k, "AS")[1]
        if a.startswith("/") and a != "/Off":
            on = a
    f.xref_set_key(par, "V", on)
    if f.xref_get_key(x, "V")[0] != "null" or f.xref_get_key(x, "Ff")[0] != "null":
        del_keys(f, x, ("V", "Ff"))  # 동그라미 칸에는 칸 값·깃발을 두지 않는다(부모 것을 물려받음)


def save_widget(w: fitz.Widget) -> None:
    """w.update() 대신 쓴다. PyMuPDF 는 꺼진 라디오(값 'Off')를 update 하면 켜 버리고, 묶음 값을 글자열로 넣는다."""
    radio = w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    if radio and w.field_value in ("Off", "", None):
        w.field_value = False
    w.update()
    if radio:
        _sync_radio(w.parent.parent, w.xref)


def reset_widget(w: fitz.Widget) -> None:
    """칸 값 비우기. w.reset() 은 라디오 동그라미를 끄지 못한다."""
    if w.field_type in (fitz.PDF_WIDGET_TYPE_RADIOBUTTON, fitz.PDF_WIDGET_TYPE_CHECKBOX):
        w.field_value = False
        save_widget(w)
    else:
        w.reset()  # field_value = "" 는 텍스트·콤보·목록에서 무시된다


def _rename_on(f: fitz.Document, x: int, old: str, new: str) -> None:
    gone = []
    for ap in ("AP/N", "AP/D"):
        t, v = f.xref_get_key(x, f"{ap}/{old}")
        if t != "null":
            f.xref_set_key(x, f"{ap}/{new}", v)
            gone.append(f"{ap}/{old}")
    if f.xref_get_key(x, "AS")[1] == f"/{old}":
        f.xref_set_key(x, "AS", f"/{new}")
    del_keys(f, x, gone)


def _radio_parent(f: fitz.Document, x: int) -> int:
    """동그라미 칸 x 의 묶음(부모) xref. 혼자 서 있는 라디오(다른 프로그램이 만든 것)면 부모를 만들어 그 밑에 넣는다."""
    t, v = f.xref_get_key(x, "Parent")
    if t == "xref":
        return int(v.split()[0])
    name = f.xref_get_key(x, "T")[1]
    ff = f.xref_get_key(x, "Ff")[1]
    if _on_name(f, x) == "Yes":  # 체크 박스로 만든 칸
        _rename_on(f, x, "Yes", "Choice1")
    on = f.xref_get_key(x, "AS")[1]
    par = f.get_new_xref()
    f.update_object(par, f"<</FT/Btn/Ff {ff if ff.isdigit() else _RADIO_FLAGS}/T {fitz.get_pdf_str(name)}"
                         f"/V {on if on.startswith('/') else '/Off'}/Kids[{x} 0 R]>>")
    del_keys(f, x, ("T", "FT", "Ff", "V"))
    f.xref_set_key(x, "Parent", f"{par} 0 R")
    holder, key = _acro_holder(f)
    xs = _get_refs(f, holder, key)
    _set_refs(f, holder, key, [par if k == x else k for k in xs] if x in xs else xs + [par])
    return par


def _radio_move(f: fitz.Document, x: int, group: str) -> None:
    """동그라미 칸 x 를 이름이 group 인 묶음으로 옮긴다(없으면 새 묶음). 켜짐 이름은 묶음 안에서 겹치지 않게."""
    holder, key = _acro_holder(f)
    t, v = f.xref_get_key(x, "Parent")
    if t == "xref":  # 옛 묶음에서 빼기
        old = int(v.split()[0])
        if f.xref_get_key(old, "T")[1] == group:
            return
        kids = [k for k in _get_refs(f, old, "Kids") if k != x]
        _set_refs(f, old, "Kids", kids)
        if not kids:
            _set_refs(f, holder, key, [k for k in _get_refs(f, holder, key) if k != old])
        elif f.xref_get_key(old, "V")[1] == f"/{_on_name(f, x)}":
            f.xref_set_key(old, "V", "/Off")
    par = None
    for pno in range(f.page_count):
        pg = f[pno]
        for w in pg.widgets():
            if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON and w.xref != x and w.field_name == group:
                par = _radio_parent(f, w.xref)
                break
        if par:
            break
    if par is None:
        par = f.get_new_xref()
        f.update_object(par, f"<</FT/Btn/Ff {_RADIO_FLAGS}/T {fitz.get_pdf_str(group)}/V/Off/Kids[]>>")
        _set_refs(f, holder, key, _get_refs(f, holder, key) + [par])
    kids = _get_refs(f, par, "Kids")
    used = {_on_name(f, k) for k in kids}
    _set_refs(f, holder, key, [k for k in _get_refs(f, holder, key) if k != x])
    del_keys(f, x, ("T", "FT", "Ff", "V"))
    f.xref_set_key(x, "Parent", f"{par} 0 R")
    _set_refs(f, par, "Kids", kids + [x])
    on = _on_name(f, x)
    if on in used or on in ("", "Yes"):
        n = 1
        while f"Choice{n}" in used:
            n += 1
        _rename_on(f, x, on or "Yes", f"Choice{n}")
    f.xref_set_key(x, "AS", "/Off")


def _refresh_widget(page: fitz.Page, x: int) -> None:
    """구조를 바꾼 칸의 모양을 새로 그린다(라디오는 동그라미로)."""
    for w in page.widgets():
        if w.xref == x:
            save_widget(w)
            return


def rename_field(doc: Document, page: int, xref: int, name: str) -> None:
    """칸 이름 바꾸기. 라디오는 이름이 곧 묶음이라, 같은 이름의 묶음으로 옮겨 간다."""
    with doc.lock:
        p = doc.fitz[page]
        w = next((w for w in p.widgets() if w.xref == xref), None)
        if w is None or not name or name == w.field_name:
            return
        if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            _radio_move(doc.fitz, xref, name)
            _refresh_widget(p, xref)
        else:
            w.field_name = name
            save_widget(w)


def fields(doc: Document) -> list[dict]:
    out = []
    with doc.lock:
        for i in range(doc.page_count):
            for w in doc.fitz[i].widgets():
                out.append({"page": i, "xref": w.xref, "name": w.field_name, "type": w.field_type_string,
                            "value": w.field_value, "rect": fitz.Rect(w.rect), "options": w.choice_values or []})
    return out


def set_field_value(doc: Document, page: int, xref: int, value):
    doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        for w in p.widgets():
            if w.xref == xref:
                w.field_value = value
                save_widget(w)
                return


def delete_field(doc: Document, page: int, xref: int, snapshot: bool = True):
    if snapshot:
        doc.snapshot()
    with doc.lock:
        p = doc.fitz[page]
        for w in p.widgets():
            if w.xref == xref:
                f = doc.fitz
                t, v = f.xref_get_key(xref, "Parent")
                p.delete_widget(w)
                if t == "xref":  # 라디오 묶음에 남은 빈자리도 걷는다
                    par = int(v.split()[0])
                    kids = [k for k in _get_refs(f, par, "Kids") if k != xref]
                    _set_refs(f, par, "Kids", kids)
                    if not kids:
                        holder, key = _acro_holder(f)
                        _set_refs(f, holder, key, [k for k in _get_refs(f, holder, key) if k != par])
                return


def reset_fields(doc: Document):
    doc.snapshot()
    with doc.lock:
        for i in range(doc.page_count):
            for w in doc.fitz[i].widgets():
                reset_widget(w)


def align_fields(doc: Document, items: list[tuple[int, int]], how: str):
    """items=(page,xref) 들을 맞춘다. how: left right top bottom hcenter vcenter page_h page_v page_both"""
    if not items:
        return
    doc.snapshot()
    with doc.lock:
        ws = []
        held = {}  # 쪽 객체를 붙잡아 둔다(놓치면 칸이 풀려 update 가 「Annot is not bound to a page」 로 멈춘다)
        for pg, xref in items:
            p = held.setdefault(pg, doc.fitz[pg])
            for w in p.widgets():
                if w.xref == xref:
                    ws.append((pg, w))
        if not ws:
            return
        rects = [fitz.Rect(w.rect) for _, w in ws]
        for (pg, w), r in zip(ws, rects):
            page_r = doc.fitz[pg].rect
            nr = fitz.Rect(r)
            if how == "left":
                nr.x0, nr.x1 = min(x.x0 for x in rects), min(x.x0 for x in rects) + r.width
            elif how == "right":
                nr.x1 = max(x.x1 for x in rects); nr.x0 = nr.x1 - r.width
            elif how == "top":
                nr.y0 = min(x.y0 for x in rects); nr.y1 = nr.y0 + r.height
            elif how == "bottom":
                nr.y1 = max(x.y1 for x in rects); nr.y0 = nr.y1 - r.height
            elif how == "hcenter":
                c = sum((x.x0 + x.x1) / 2 for x in rects) / len(rects)
                nr.x0, nr.x1 = c - r.width / 2, c + r.width / 2
            elif how == "vcenter":
                c = sum((x.y0 + x.y1) / 2 for x in rects) / len(rects)
                nr.y0, nr.y1 = c - r.height / 2, c + r.height / 2
            elif how in ("page_h", "page_both"):
                c = page_r.width / 2
                nr.x0, nr.x1 = c - r.width / 2, c + r.width / 2
            if how in ("page_v", "page_both"):
                c = page_r.height / 2
                nr.y0, nr.y1 = c - r.height / 2, c + r.height / 2
            w.rect = nr
            save_widget(w)


def recognize_fields(doc: Document, pages: Sequence[int] | None = None, progress=None) -> int:
    """양식 인식(간단판): 글자 뒤에 오는 긴 밑줄·빈 네모는 텍스트 칸, 작은 빈 정사각형은 체크 박스로.
    progress(i, n) 를 주면 쪽마다 부른다. 만든 칸이 없으면 되돌리기 기록도 남기지 않는다."""
    made = 0
    was = doc.modified
    doc.snapshot()
    with doc.lock:
        todo = list(pages if pages is not None else range(doc.page_count))
        for k, i in enumerate(todo):
            if progress:
                progress(k, len(todo))
            p = doc.fitz[i]
            existing = [fitz.Rect(w.rect) for w in p.widgets()]
            for d in p.get_drawings():
                r = fitz.Rect(d["rect"])
                empty = d.get("fill") in (None, (1, 1, 1))
                is_check = (8 <= r.width <= 20 and 8 <= r.height <= 20 and abs(r.width - r.height) <= 2
                            and empty and d.get("color") is not None)
                is_line = r.height <= 2 and r.width >= 40
                is_box = 8 <= r.height <= 40 and r.width >= 40 and empty
                if not (is_check or is_line or is_box):
                    continue
                fr = fitz.Rect(r.x0, r.y0 - 14, r.x1, r.y1) if is_line else r
                if any(fr.intersects(e) for e in existing):
                    continue
                if p.get_text("text", clip=fr).strip():
                    continue
                w = fitz.Widget()
                if is_check:
                    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
                    w.field_name = _unique_name(doc, FIELD_TYPE_KO["checkbox"])
                    w.field_value = False
                else:
                    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                    w.field_name = _unique_name(doc, FIELD_TYPE_KO["text"])
                    w.text_fontsize = 10
                w.rect = fr
                w.border_width = 0
                p.add_widget(w)
                existing.append(fr)
                made += 1
    if not made:
        doc._undo.pop()   # 바뀐 것이 없으니 Ctrl+Z 한 번이 헛돌지 않게
        doc.modified = was
    return made


# ── 칸 복사 · 여러 복사본 · 탭 순서 ─────────────────────────────────
_KIND_OF = {fitz.PDF_WIDGET_TYPE_TEXT: "text", fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
            fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio", fitz.PDF_WIDGET_TYPE_COMBOBOX: "combo",
            fitz.PDF_WIDGET_TYPE_LISTBOX: "list", fitz.PDF_WIDGET_TYPE_BUTTON: "button",
            fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature"}
# 새 칸이 스스로 갖는 열쇠(복사하지 않음)
_OWN_KEYS = {"Type", "Subtype", "P", "Parent", "T", "Rect", "AP", "Kids", "AS", "V", "DV", "FT", "Ff",
             "StructParent", "NM", "M", "Opt"}


def field_spec(doc: Document, page: int, xref: int) -> dict | None:
    """칸 하나를 다시 만들 수 있게 적어 둔다(앱 안 클립보드·복사본에 씀). 원본을 지워도 이것으로 붙일 수 있다."""
    with doc.lock:
        f = doc.fitz
        w = next((w for w in f[page].widgets() if w.xref == xref), None)
        if w is None:
            return None
        keys = {}
        for k in f.xref_get_keys(xref):
            if k not in _OWN_KEYS:
                keys[k] = f.xref_get_key(xref, k)
        kind = _KIND_OF.get(w.field_type, "text")
        val = w.field_value
        if kind in ("checkbox", "radio"):
            val = val not in ("Off", "", None, False)
        return {"doc": id(doc), "page": page, "kind": kind, "rect": tuple(w.rect), "name": w.field_name,
                "value": val, "options": list(w.choice_values or []), "caption": w.button_caption or "",
                "flags": w.field_flags, "keys": keys}


def paste_fields(doc: Document, page: int, specs: list[dict], dx: float = 0, dy: float = 0,
                 snapshot: bool = True) -> list[int]:
    """적어 둔 칸들을 page 쪽에 (dx, dy) 만큼 옮겨 만든다. 이름은 겹치면 다음 번호. 만든 xref 들."""
    if not specs:
        return []
    if snapshot:
        doc.snapshot()
    out = []
    with doc.lock:
        f = doc.fitz
        p = f[page]
        taken: set[str] = set()
        for s in specs:
            kind = s["kind"]
            name = _copy_name(doc, s["name"], taken)
            taken.add(name)
            r = fitz.Rect(s["rect"]) + (dx, dy, dx, dy)
            val = s["value"] if kind not in ("text", "combo", "list") or s["value"] not in (None, "") else None
            x = add_field(doc, page, r, kind, name=name, value=val, options=s["options"] or None,
                          label=s["caption"], snapshot=False)
            same = s["doc"] == id(doc)
            for k, (t, v) in s["keys"].items():
                if t == "null" or (not same and (t == "xref" or re.search(r"\d+\s+0\s+R", v or ""))):
                    continue   # 다른 문서의 객체 번호는 이 문서에서 뜻이 없다
                f.xref_set_key(x, k, fitz.get_pdf_str(v) if t == "string" else v)
            if kind != "radio" and s["flags"]:
                f.xref_set_key(x, "Ff", str(s["flags"]))
            p = f[page]
            _refresh_widget(p, x)
            out.append(x)
    return out


def duplicate_fields(doc: Document, items: list[tuple[int, int]], down: int, across: int,
                     dx: float, dy: float) -> list[int]:
    """고른 칸들을 아래로 down 줄 · 옆으로 across 칸 격자로 복사한다(원본이 왼쪽 위 한 칸, 1 이면 그 방향은 안 늘림).
    dx·dy 는 한 칸 건너는 거리(pt). 만든 xref 들."""
    down, across = max(1, int(down)), max(1, int(across))
    if not items or down * across == 1:
        return []
    specs = [sp for pg, x in items if (sp := field_spec(doc, pg, x))]
    doc.snapshot()
    out = []
    for r in range(down):
        for c in range(across):
            if r == 0 and c == 0:
                continue
            for sp in specs:
                out += paste_fields(doc, sp["page"], [sp], c * dx, r * dy, snapshot=False)
    return out


def set_tab_order(doc: Document, page: int, xrefs: list[int]) -> None:
    """page 쪽 Annots 배열에서 칸들이 xrefs 차례로 오게 한다(다른 주석 자리는 그대로). /Tabs 는 지워 배열 차례를 따른다."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        px = f[page].xref
        cur = _get_refs(f, px, "Annots")
        want = [x for x in xrefs if x in cur]
        it = iter(want)
        new = [next(it) if x in want else x for x in cur]
        _set_refs(f, px, "Annots", new)
        if f.xref_get_key(px, "Tabs")[0] != "null":
            del_keys(f, px, ("Tabs",))


def tab_order(doc: Document, page: int) -> list[int]:
    """page 쪽 칸 xref 들, Annots 배열 차례."""
    with doc.lock:
        f = doc.fitz
        ws = {w.xref for w in f[page].widgets()}
        return [x for x in _get_refs(f, f[page].xref, "Annots") if x in ws]
