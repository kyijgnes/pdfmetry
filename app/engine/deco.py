# -*- coding: utf-8 -*-
"""꾸미기 다섯 가지(워터마크·배경·머리글/바닥글·페이지 번호·베이츠)의 공용 엔진.

꾸미기 하나를 쪽마다 폼 객체(XObject) 하나로 넣고, 그 객체에 /RPDFDeco(종류)·/RPDFSet(설정 JSON)을 단다.
그냥 글·그림으로 그리면 본문과 섞여 다시 찾을 수 없다(추가·업데이트·삭제를 하려고 표시해 둔다).
그리는 함수는 deco_mark(워터마크·배경)·deco_text(머리글/바닥글·페이지 번호·베이츠)에 있고, 여기에는
표시·찾기·지우기, 미리보기, 글 상자 그리기, 아홉 칸 자리 계산, 번호 매크로, 옛 설정 바꾸기를 둔다.

설정(dict)은 「v: 2」 꼴로 저장한다. 그 표시가 없는 설정은 옛 꼴이라 upgrade_settings 가 새 꼴로 바꿔 받는다.
길이는 모두 pt, 불투명도는 0~1, 회전은 도(°).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Sequence

import fitz

from . import fonts as F
from .tools import MM, EngineError, _color, _file_stem, _pages

SETTINGS_VERSION = 2
# 종류 열쇠(문서에 /RPDFDeco 로 적히는 값) → 화면 이름. 열쇠는 바꾸지 않는다(옛 문서가 이 이름으로 적혀 있다)
DECO_KINDS = {"watermark": "워터마크", "background": "배경화면", "header_footer": "머리글 & 바닥글",
              "page_number": "페이지 번호", "bates": "베이츠 번호"}

# ── 아홉 칸 자리 ────────────────────────────────────────────────────────
CELLS9 = ("top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom", "bottom_right")
CELLS6 = ("top_left", "top", "top_right", "bottom_left", "bottom", "bottom_right")   # 머리글/바닥글·페이지 번호
ZERO_MARGINS = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}


def cell_parts(cell: str) -> tuple[str, str]:
    """자리 이름 → (세로 top·middle·bottom, 가로 left·center·right)."""
    c = (cell or "center").lower()
    v = "top" if c.startswith("top") else "bottom" if c.startswith("bottom") else "middle"
    h = "left" if c.endswith("left") else "right" if c.endswith("right") else "center"
    return v, h


def anchor_point(rect: fitz.Rect, w: float, h: float, cell: str = "center", margins: dict | None = None,
                 offset: Sequence[float] = (0, 0)) -> fitz.Point:
    """w×h 상자를 rect 의 아홉 칸 자리에 둘 때 왼쪽 위 점. 여백은 쪽 가장자리에서 안으로 든 거리,
    가운데 줄·칸은 여백을 뺀 안쪽의 가운데. offset 은 (오른쪽 +, 아래 +) 로 더 옮긴다."""
    m = {**ZERO_MARGINS, **(margins or {})}
    v, hz = cell_parts(cell)
    x0, x1 = rect.x0 + m["left"], rect.x1 - m["right"]
    y0, y1 = rect.y0 + m["top"], rect.y1 - m["bottom"]
    x = {"left": x0, "right": x1 - w}.get(hz, x0 + (x1 - x0 - w) / 2)
    y = {"top": y0, "bottom": y1 - h}.get(v, y0 + (y1 - y0 - h) / 2)
    return fitz.Point(x + float(offset[0]), y + float(offset[1]))


# ── 글꼴 · 글 상자 ──────────────────────────────────────────────────────
FONT_DEFAULT = {"name": "", "size": 11.0, "color": (0, 0, 0), "bold": False, "italic": False, "underline": False}
_ITALIC = fitz.Matrix(1, 0, -0.21, 1, 0, 0)   # 기울인 글꼴 파일이 없을 때 기울여 그리는 정도(약 12°)


def norm_font(font: dict | None) -> dict:
    f = {**FONT_DEFAULT, **(font or {})}
    f["color"] = _color(f["color"]) or (0, 0, 0)
    f["size"] = float(f["size"])
    return f


def _lines(text: str) -> list[str]:
    return (text or "").replace("\r\n", "\n").replace(" ", "\n").split("\n")


def _pick_file(f: dict, text: str) -> tuple[str, bool, bool]:
    """글꼴 칸 값 → (파일, 굵게 흉내, 기울임 흉내). 고른 글꼴에 없는 글자(한글 등)가 있으면 맑은 고딕으로."""
    got = F.font_file(f["name"], f["bold"], f["italic"])
    if f["name"] and not F.covers(got[0], text):
        got = F.font_file("", f["bold"], f["italic"])
    return got


def text_box_size(text: str, font: dict | None = None) -> tuple[float, float]:
    """글 상자 크기(가로, 세로 pt). 줄마다 가장 넓은 줄 × 줄 수."""
    f = norm_font(font)
    path = _pick_file(f, text)[0]
    fo = F.font_object(path)
    lines = _lines(text)
    return (max(fo.text_length(s, fontsize=f["size"]) for s in lines),
            f["size"] * (fo.ascender - fo.descender) * len(lines))


def _res_name(page: fitz.Page, path: str) -> str:
    """쪽에 넣을 글꼴 자원 이름. 저장 때 쓴 글자만 남긴(subset) 같은 이름 글꼴이 이미 있으면 새 이름을 쓴다
    (그 이름을 쓰면 PyMuPDF 가 옛 글꼴을 다시 써서 새 글자가 빈 네모로 나온다)."""
    import os
    base = re.sub(r"[^A-Za-z0-9]", "", os.path.splitext(os.path.basename(path))[0]) or "F"
    fonts = {f[4]: f[3] for f in page.get_fonts()}
    name, k = base, 1
    while name in fonts and re.match(r"[A-Z]{6}\+", fonts[name] or ""):
        k += 1
        name = f"{base}{k}"
    return name


def draw_text_box(page: fitz.Page, text: str, font: dict | None = None, anchor="center", rotate: float = 0,
                  opacity: float = 1.0, margins: dict | None = None, offset: Sequence[float] = (0, 0),
                  shape: fitz.Shape | None = None) -> fitz.Rect:
    """글 상자 하나를 그린다(여러 줄 · 글꼴 · 굵게 · 기울임 · 밑줄). 보이는 화면 좌표의 상자(돌리기 전)를 돌려준다.

    anchor: 아홉 칸 자리 이름(margins·offset 과 함께) 또는 상자 가운데 점(fitz.Point).
    줄은 자리의 가로 칸을 따라 왼쪽·가운데·오른쪽으로 맞춘다(점이면 가운데). rotate 는 상자 가운데를 축으로 돈다.
    shape 를 주면 그 위에 그리고 commit 은 부른 쪽이 한다(바둑판처럼 같은 글을 여러 번)."""
    lines = _lines(text)
    if not any(s.strip() for s in lines):
        return fitz.Rect()
    f = norm_font(font)
    path, fake_bold, fake_italic = _pick_file(f, text)
    fo = F.font_object(path)
    size = f["size"]
    lh = size * (fo.ascender - fo.descender)
    widths = [fo.text_length(s, fontsize=size) for s in lines]
    w, h = max(widths), lh * len(lines)
    if isinstance(anchor, str):
        tl = anchor_point(page.rect, w, h, anchor, margins, offset)
        align = cell_parts(anchor)[1]
    else:
        tl = fitz.Point(anchor) - (w / 2, h / 2)
        align = "center"
    box = fitz.Rect(tl, tl.x + w, tl.y + h)
    c = (box.tl + box.br) / 2
    # 보이는 화면(y 아래로)에서 시계 반대 방향으로 rotate 도. morph 는 PDF 좌표(y 위로)·회전 전 쪽에서 걸리므로
    # 보이는 화면의 기울이기·돌리기를 그 좌표로 옮겨 준다(fy: 위아래 뒤집기, lin: 쪽 회전)
    rot = fitz.Matrix(-rotate) if rotate else fitz.Identity
    vis = (_ITALIC if fake_italic else fitz.Identity) * rot
    dm = page.derotation_matrix
    lin, fy = fitz.Matrix(dm.a, dm.b, dm.c, dm.d, 0, 0), fitz.Matrix(1, 0, 0, -1, 0, 0)
    morph_m = fy * ~lin * vis * lin * fy

    def place(p: fitz.Point) -> fitz.Point:   # 상자 가운데를 축으로 돌린 보이는 점
        return (p - c) * rot + c if rotate else p

    own = shape is None
    sh = shape or page.new_shape()
    done = getattr(sh, "_rpdf_fonts", None)
    if done is None:
        done = sh._rpdf_fonts = {}
    name = done.get(path)
    if name is None:
        name = done[path] = _res_name(page, path)
        page.insert_font(fontname=name, fontfile=path)
    col = f["color"]
    for i, (s, lw) in enumerate(zip(lines, widths)):
        if not s:
            continue
        x = {"left": box.x0, "right": box.x1 - lw}.get(align, box.x0 + (w - lw) / 2)
        base = fitz.Point(x, box.y0 + size * fo.ascender + i * lh)
        q = place(base) * dm
        kw = {"morph": (q, morph_m)} if rotate or fake_italic else {}
        if fake_bold:
            kw.update(render_mode=2, border_width=0.04, stroke_opacity=opacity)
        sh.insert_text(q, s, fontsize=size, fontname=name, color=col, fill=col if fake_bold else None,
                       fill_opacity=opacity, rotate=page.rotation, **kw)
        if f["underline"]:
            uy = base.y + size * 0.12
            sh.draw_line(place(fitz.Point(x, uy)) * dm, place(fitz.Point(x + lw, uy)) * dm)
            sh.finish(color=col, width=max(size * 0.05, 0.5), stroke_opacity=opacity)
    if own:
        sh.commit()
    return box


# ── 번호 매크로 ─────────────────────────────────────────────────────────
# 글 안의 <<…>> 를 넣을 때 쪽마다 푼다. 목록에 없는 <<…>> 는 글자 그대로 둔다
_MACRO = re.compile(r"<<(.*?)>>")


def expand_macros(s: str, page_no: int, total: int) -> str:
    """번호 매크로를 푼다. ##1## 은 지금 번호, ##n## 은 마지막 번호. 옛 자리표 {page}·{total} 도 푼다
    (새 꼴 설정에 옛 자리표를 그대로 써 넣은 것)."""
    a, n = str(page_no), str(total)
    s = (s or "").replace("{page}", a).replace("{total}", n)
    table = {"##1##": a, "##n##": n, "##1##/##n##": f"{a}/{n}", "##1##of##n##": f"{n} 페이지 중 {a}",
             "Page##1##": f"페이지 {a}", "Page##1##of##n##": f"{n} 페이지 중 {a}"}
    return _MACRO.sub(lambda m: table.get(m.group(1), m.group(0)), s)


# ── 옛 설정 → 새 꼴 ─────────────────────────────────────────────────────
_OLD_SLOTS = {"left": "top_left", "center": "top", "right": "top_right",
              "footer_left": "bottom_left", "footer_center": "bottom", "footer_right": "bottom_right"}
_OLD_POS = {"header_left": "top_left", "header_center": "top", "header_right": "top_right",
            "footer_left": "bottom_left", "footer_center": "bottom", "footer_right": "bottom_right",
            "header": "top", "footer": "bottom"}
_OLD_WM_MARGIN = 36.0   # 옛 워터마크는 가장자리에서 늘 0.5in 안에 섰다


def old_placeholders(text: str, file_name: str = "", today: _dt.date | None = None) -> str:
    """옛 자리표({page}·{total}·{date}·{file})를 새 꼴로. 날짜·파일 이름은 지금 값의 글자로 굳는다."""
    return _old_text(text, (today or _dt.date.today()).strftime("%Y-%m-%d"), file_name)


def _old_text(s: str, date_s: str, fname: str) -> str:
    return ((s or "").replace("{page}", "<<##1##>>").replace("{total}", "<<##n##>>")
            .replace("{date}", date_s).replace("{file}", fname))


def _old_font(s: dict, size: float, color) -> dict:
    return {**FONT_DEFAULT, "size": float(s.get("font_size", size)),
            "color": tuple(_color(s.get("color") if s.get("color") is not None else color) or (0, 0, 0))}


def _old_margins(s: dict, mm: float) -> dict:
    m = float(s.get("margin_mm", mm)) * MM
    return {"top": m, "bottom": m, "left": m, "right": m}


def upgrade_settings(kind: str, settings: dict, file_name: str | None = None,
                     today: _dt.date | None = None) -> dict:
    """설정을 새 꼴(v 2)로. 이미 새 꼴이면 사본 그대로. 「pages」 는 건드리지 않는다.

    옛 꼴: 머리글의 {page}·{total}·{date}·{file}, margin_mm(여백 하나), 워터마크 position 「tile」·scale(배)·
    image_path, font_size·color 따로. {date} 는 지금 날짜(옛 date_format)로, {file} 은 file_name(없으면 설정에
    적힌 file_name)으로 글자가 굳는다."""
    s = dict(settings)
    if s.get("v") == SETTINGS_VERSION:
        return s
    pages = s.pop("pages", None)
    date_s = (today or _dt.date.today()).strftime(s.pop("date_format", None) or "%Y-%m-%d")
    fname = s.pop("file_name", None) or file_name or ""   # 옛 머리글에 적힌, 넣을 때의 파일 이름이 먼저
    out: dict = {"v": SETTINGS_VERSION}
    if kind == "watermark":
        pos = (s.get("position") or "center").lower()
        tile = pos == "tile"
        cell = "center" if tile else pos if pos in CELLS9 else "center"
        v, hz = cell_parts(cell)
        off = (_OLD_WM_MARGIN if hz == "left" else -_OLD_WM_MARGIN if hz == "right" else 0.0,
               _OLD_WM_MARGIN if v == "top" else -_OLD_WM_MARGIN if v == "bottom" else 0.0)
        out.update(text=s.get("text") or None, file=s.get("image_path") or s.get("file") or None,
                   file_page=int(s.get("file_page", 0)), font=_old_font(s, 48, (0.5, 0.5, 0.5)),
                   position=cell, offset=list(off), tile=tile, tile_gap=[2, 2],
                   scale_mode="max", scale_pct=float(s.get("scale", 0.5)) * 100,
                   rotate=float(s.get("rotate", 45)), opacity=float(s.get("opacity", 0.3)))
    elif kind == "background":
        out.update(color=tuple(_color(s["color"])) if s.get("color") is not None else None,
                   file=s.get("image_path") or s.get("file") or None, file_page=int(s.get("file_page", 0)),
                   scale_mode="page", scale_pct=100.0, rotate=0.0, opacity=float(s.get("opacity", 1.0)))
    elif kind == "header_footer":
        slots = {cell: _old_text(s.get(old) or "", date_s, fname) for old, cell in _OLD_SLOTS.items()}
        out.update(slots={k: v for k, v in slots.items() if v}, start_number=int(s.get("start_number", 1)),
                   font=_old_font(s, 10, (0, 0, 0)), margins=_old_margins(s, 12))
    elif kind == "page_number":
        pos = s.get("position") or "footer_center"
        out.update(style=_old_text(s.get("style") or "{page}", date_s, fname), position=_OLD_SLOTS.get(pos, pos),
                   start=int(s.get("start", 1)), font=_old_font(s, 11, (0, 0, 0)), margins=_old_margins(s, 12.7))
    elif kind == "bates":
        pos = s.get("position") or "footer_right"
        out.update(prefix=s.get("prefix", ""), suffix=s.get("suffix", ""), start=int(s.get("start", 1)),
                   digits=int(s.get("digits", 6)), reverse=bool(s.get("reverse", False)),
                   position=_OLD_POS.get(pos, pos), font=_old_font(s, 10, (0, 0, 0)), margins=_old_margins(s, 12),
                   rotate=0.0, opacity=1.0)
    else:
        raise EngineError(f"모르는 꾸미기입니다: {kind}")
    if pages is not None:
        out["pages"] = pages
    return out


# ── 표시 붙여 넣기 · 찾기 · 지우기 ───────────────────────────────────────
def _deco_draw(kind: str, s: dict):
    """종류·설정 → (그리는 함수(임시 문서, 쪽, 차례 k, 쪽 수 n), 모든 쪽이 같은 그림인지)."""
    from .deco_mark import background, watermark
    from .deco_text import bates_numbers, header_footer, page_numbers

    s = {k: v for k, v in s.items() if k != "pages"}
    if kind == "watermark":
        return (lambda d, i, k, n: watermark(d, pages=[i], **s)), True
    if kind == "background":
        return (lambda d, i, k, n: background(d, pages=[i], **s)), True
    if kind == "header_footer":
        st = int(s.pop("start_number", 1))
        return (lambda d, i, k, n: header_footer(d, pages=[i], start_number=st + k, total=n + st - 1, **s)), False
    if kind == "page_number":
        st = int(s.pop("start", 1))
        return (lambda d, i, k, n: page_numbers(d, pages=[i], start=st + k, total=n + st - 1, **s)), False
    if kind == "bates":
        st, rev = int(s.pop("start", 1)), bool(s.pop("reverse", False))
        return (lambda d, i, k, n: bates_numbers(d, pages=[i], start=st + (n - 1 - k if rev else k), **s)), False
    raise EngineError(f"모르는 꾸미기입니다: {kind}")


def _number_order(kind: str, settings: dict, targets: list[int]) -> tuple[list[int], int]:
    """번호를 셀 차례와 쪽 수. 보통은 범위 안 차례, 베이츠는 deco_text.bates_order(범위 밖 쪽도 셀 수 있음)."""
    if kind == "bates":
        from .deco_text import bates_order
        return bates_order(settings, targets)
    return list(range(len(targets))), len(targets)


def _stamp(doc: fitz.Document, kind: str, settings: dict, targets: list[int], order: list[int] | None = None,
           count: int | None = None) -> None:
    """targets 쪽에 꾸미기를 폼 객체로 붙이고 표시를 단다. order·count 는 번호를 셀 때의 차례와 쪽 수
    (미리보기는 범위 가운데 한 쪽만 그리므로 따로 준다)."""
    draw, same = _deco_draw(kind, settings)
    if order is None:
        order, count = _number_order(kind, settings, targets)
    n = count if count is not None else len(targets)
    tmp = fitz.open()
    try:
        # 1) 임시 문서에 쪽마다 그린다. 쪽과 같은 크기·회전으로 그린 뒤 회전만 0 으로 되돌리면
        #    그 쪽에 바로 그렸을 때와 같은 회전 전 좌표의 그림이 된다
        src_of: list[int] = []
        seen: dict[tuple, int] = {}
        fonts: dict[str, int] = {}
        for k, pno in zip(order, targets):
            page = doc[pno]
            cb = page.cropbox
            key = (round(cb.width, 2), round(cb.height, 2), page.rotation)
            if same and key in seen:
                src_of.append(seen[key]); continue
            t = tmp.new_page(width=cb.width, height=cb.height)
            # 앞 쪽에 넣은 글꼴을 미리 걸어 둔다. 안 그러면 쪽마다 글꼴을 새로 실어 300쪽에 10초 가까이 걸렸다
            rt, rv = tmp.xref_get_key(t.xref, "Resources")
            res = int(rv.split()[0]) if rt == "xref" else t.xref
            for name, fx in fonts.items():
                tmp.xref_set_key(res, f"Font/{name}" if rt == "xref" else f"Resources/Font/{name}", f"{fx} 0 R")
            t.set_rotation(page.rotation)
            draw(tmp, t.number, k, n)
            t.set_rotation(0)
            for f in t.get_fonts():
                fonts.setdefault(f[4], f[0])
            seen[key] = t.number; src_of.append(t.number)
        # 2) 다 그린 뒤에 붙인다(붙이는 중에 임시 문서가 바뀌면 PyMuPDF 의 옮김 표가 어긋난다)
        meta = fitz.get_pdf_str(json.dumps({k: v for k, v in settings.items() if k != "pages"}, ensure_ascii=False))
        for pno, src in zip(targets, src_of):
            page = doc[pno]
            rot = page.rotation
            before = {x[1] for x in page.get_xobjects()}
            page.set_rotation(0)   # 돌린 쪽에 그대로 붙이면 그림이 옆으로 눕는다
            try:
                page.show_pdf_page(page.rect, tmp, src, overlay=kind != "background", keep_proportion=False)
            finally:
                page.set_rotation(rot)
            for x in page.get_xobjects():
                if x[2] == 0 and x[1] not in before:
                    doc.xref_set_key(x[0], "RPDFDeco", f"/{kind}")
                    doc.xref_set_key(x[0], "RPDFSet", meta)
    finally:
        tmp.close()


def add_decoration(doc: fitz.Document, kind: str, pages=None, file_name: str | None = None, **settings) -> int:
    """꾸미기를 넣는다(in place). 넣은 쪽 수. settings 는 그 종류의 설정(옛 꼴도 받음)이고, 새 꼴로 문서 안에
    같이 적어 두어 「업데이트」 때 대화상자에 다시 채운다. file_name 은 옛 {file} 자리표를 풀 때 쓴다."""
    s = upgrade_settings(kind, settings, file_name if file_name is not None else _file_stem(doc))
    s.pop("pages", None)
    targets = _pages(doc, pages)
    _stamp(doc, kind, s, targets)
    return len(targets)


def _deco_items(doc: fitz.Document, pno: int):
    """쪽 pno 에 붙은 꾸미기: [(xref, 이름, 종류, 설정 JSON 글)]."""
    out = []
    for xref, name, ref, _bbox in doc[pno].get_xobjects():
        if ref != 0:
            continue
        t, v = doc.xref_get_key(xref, "RPDFDeco")
        if t == "name":
            out.append((xref, name, v.lstrip("/"), doc.xref_get_key(xref, "RPDFSet")[1]))
    return out


def decorations(doc: fitz.Document, kind: str | None = None) -> list[dict]:
    """문서 안의 꾸미기. 같은 설정끼리 묶어 [{"kind", "settings", "pages"}] (넣은 차례). 설정은 적힌 그대로
    (옛 꼴일 수 있다. 창에 채우기 전에 upgrade_settings)."""
    groups: dict[tuple, dict] = {}
    for pno in range(doc.page_count):
        for _x, _n, k, raw in _deco_items(doc, pno):
            if kind and k != kind:
                continue
            g = groups.get((k, raw))
            if g is None:
                try:
                    s = json.loads(raw)
                except ValueError:
                    s = {}
                g = groups[(k, raw)] = {"kind": k, "settings": s, "pages": []}
            if pno not in g["pages"]:
                g["pages"].append(pno)
    return list(groups.values())


def remove_decorations(doc: fitz.Document, kind: str) -> int:
    """kind 꾸미기를 모두 지운다(in place). 지운 수. 그린 내용 줄(「q /이름 Do Q」)과 쪽 자원의 이름을 같이 뺀다."""
    gone = 0
    for pno in range(doc.page_count):
        items = [it for it in _deco_items(doc, pno) if it[2] == kind]
        if not items:
            continue
        page = doc[pno]
        names = [re.escape(name) for _x, name, _k, _r in items]
        pat = re.compile(rb"q\s*/(?:" + "|".join(names).encode() + rb")\s+Do\s*Q")
        for cx in page.get_contents():
            data = doc.xref_stream(cx)
            new = pat.sub(b"", data)
            if new != data:
                doc.update_stream(cx, new)
        for _x, name, _k, _r in items:
            doc.xref_set_key(page.xref, f"Resources/XObject/{name}", "null")
            gone += 1
    return gone


def replace_decorations(doc: fitz.Document, kind: str, groups: Sequence[dict]) -> None:
    """kind 꾸미기를 모두 지우고 groups([{"settings", "pages"}])로 다시 넣는다(업데이트). in place."""
    remove_decorations(doc, kind)
    for g in groups:
        add_decoration(doc, kind, pages=g["pages"], **g["settings"])


# ── 미리보기 ────────────────────────────────────────────────────────────
def render_preview(doc: fitz.Document, pno: int, kind: str, settings: dict, pages: Sequence[int],
                   size_px: int = 560, hide: Sequence[str] = ()) -> fitz.Pixmap:
    """쪽 pno 를 임시 문서에 복사해 꾸미기를 넣고 그린 그림. 번호는 pages 안의 차례로 센다(실제로 넣을 때와 같음).
    pno 가 pages 밖이면 꾸미기 없이 그 쪽만. hide 의 종류는 복사본에서 먼저 지운다(업데이트 때 고칠 옛것).
    설정이 틀려 못 그리면(파일 없음 등) 쪽만 그린다."""
    tmp = fitz.open()
    try:
        tmp.insert_pdf(doc, from_page=pno, to_page=pno)
        for k in hide:
            remove_decorations(tmp, k)
        order = sorted(set(int(p) for p in pages))
        if pno in order:
            s = upgrade_settings(kind, settings, _file_stem(doc))
            s.pop("pages", None)
            try:
                ks, n = _number_order(kind, s, order)
                _stamp(tmp, kind, s, [0], order=[ks[order.index(pno)]], count=n)
            except Exception:  # noqa: BLE001  칸을 채우는 중의 틀린 값은 미리보기에서 그냥 넘긴다
                pass
        page = tmp[0]
        z = size_px / max(page.rect.width, page.rect.height, 1)
        return page.get_pixmap(matrix=fitz.Matrix(z, z), annots=True)
    finally:
        tmp.close()
