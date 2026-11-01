# -*- coding: utf-8 -*-
"""워터마크·배경 그리기(글 · 그림 · PDF 한 쪽, 배율, 바둑판, 회전, 불투명도).

설정 꼴은 deco.upgrade_settings 가 정한 새 꼴(v 2)이고, 옛 인자(image_path·position="tile"·scale 배 등)로
불러도 먼저 새 꼴로 바꿔 그린다(일괄 처리의 옛 watermark(...) 호출이 그대로 돈다).
"""
from __future__ import annotations

import io
import math
import os

import fitz

from .deco import anchor_point, draw_text_box, text_box_size, upgrade_settings
from .tools import EngineError, _color, _pages


def _tile_centers(rect: fitz.Rect, w: float, h: float, angle: float, gap: float) -> list[fitz.Point]:
    """바둑판 워터마크 자리(가운데 점). 돌린 상자 크기만큼 띄우고 줄마다 반 칸씩 엇갈린다. 가장자리는 잘려도 넣는다."""
    t = math.radians(angle)
    bw = abs(w * math.cos(t)) + abs(h * math.sin(t))
    bh = abs(w * math.sin(t)) + abs(h * math.cos(t))
    sx, sy = bw + gap, bh + gap
    out = []
    row = 0
    y = rect.y0 + sy / 2
    while y - bh / 2 < rect.y1:
        x = rect.x0 + (sx / 2 if row % 2 else 0)
        while x - bw / 2 < rect.x1:
            out.append(fitz.Point(x, y))
            x += sx
        y += sy
        row += 1
    return out


def _blend_image(image_path: str, opacity: float) -> tuple[bytes, tuple[int, int]]:
    """그림을 주어진 불투명도로 미리 섞어 PNG 바이트로."""
    from PIL import Image

    try:
        im = Image.open(image_path).convert("RGBA")
    except Exception as e:
        raise EngineError(f"그림을 열지 못했습니다: {image_path}") from e
    if opacity < 1:
        a = im.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, opacity))))
        im.putalpha(a)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), im.size


def is_pdf(path: str | None) -> bool:
    return bool(path) and os.path.splitext(path)[1].lower() == ".pdf"


def _pdf_source(path: str, pno: int, opacity: float, password: str | None = None) -> fitz.Document:
    """PDF 한 쪽을 한 쪽짜리 문서로(불투명도를 걸어서). 워터마크·배경으로 그 쪽을 그대로 얹는다."""
    try:
        src = fitz.open(path)
    except Exception as e:
        raise EngineError(f"파일을 열지 못했습니다: {path}") from e
    if src.needs_pass and not (password and src.authenticate(password)):
        raise EngineError(f"암호가 걸린 파일입니다: {os.path.basename(path)}")
    pno = min(max(int(pno), 0), src.page_count - 1)
    one = fitz.open()
    r = src[pno].rect
    page = one.new_page(width=r.width, height=r.height)
    page.show_pdf_page(page.rect, src, pno)
    if opacity < 1:
        gs = one.get_new_xref()
        one.update_object(gs, f"<</Type/ExtGState/ca {opacity:.3f}/CA {opacity:.3f}>>")
        rt, rv = one.xref_get_key(page.xref, "Resources")
        if rt == "xref":
            one.xref_set_key(int(rv.split()[0]), "ExtGState/RPDFop", f"{gs} 0 R")
        else:
            one.xref_set_key(page.xref, "Resources/ExtGState/RPDFop", f"{gs} 0 R")
        cx = page.get_contents()
        data = b"".join(one.xref_stream(x) for x in cx)
        one.update_stream(cx[0], b"q /RPDFop gs\n" + data + b"\nQ")
        page.set_contents(cx[0])
    src.close()
    return one


def _mark_size(rect: fitz.Rect, sw: float, sh: float, mode: str, pct: float) -> tuple[float, float]:
    """원래 크기 sw×sh 의 그림·쪽을 배율 칸대로. page: 쪽에 맞춘 크기의 pct %, max: 원래 크기의 pct %."""
    k = (min(rect.width / sw, rect.height / sh) if mode == "page" else 1.0) * max(pct, 1) / 100
    return sw * k, sh * k


def _place_file(page: fitz.Page, s: dict, overlay: bool, cell: str = "center", offset=(0, 0)) -> None:
    """파일(그림 또는 PDF 한 쪽)을 쪽에 얹는다. 바둑판이면 쪽을 덮도록 되풀이한다."""
    path, rotate, opacity = s["file"], float(s.get("rotate", 0)), float(s.get("opacity", 1))
    if not os.path.exists(path):
        raise EngineError("이 파일이 존재하지 않습니다.")
    rect = page.rect
    dm = page.derotation_matrix
    tile = bool(s.get("tile"))
    if is_pdf(path):
        src = _pdf_source(path, s.get("file_page", 0), opacity, s.get("file_password"))
        try:
            w, h = _mark_size(rect, src[0].rect.width, src[0].rect.height, s.get("scale_mode", "page"),
                              float(s.get("scale_pct", 100)))
            t = math.radians(rotate)
            bw, bh = abs(w * math.cos(t)) + abs(h * math.sin(t)), abs(w * math.sin(t)) + abs(h * math.cos(t))
            spots = (_tile_centers(rect, w, h, rotate, gap=max(w, h) * 0.4) if tile
                     else [anchor_point(rect, bw, bh, cell, None, offset) + (bw / 2, bh / 2)])
            for c in spots:
                r = fitz.Rect(c.x - bw / 2, c.y - bh / 2, c.x + bw / 2, c.y + bh / 2) * dm
                r.normalize()
                page.show_pdf_page(r, src, 0, overlay=overlay, rotate=-(rotate + page.rotation))
        finally:
            src.close()
        return
    img, (iw, ih) = _blend_image(path, opacity)
    w, h = _mark_size(rect, iw, ih, s.get("scale_mode", "page"), float(s.get("scale_pct", 100)))
    rot90 = int(round(rotate / 90)) * 90   # 그림은 90° 단위로만 돌릴 수 있다. 가까운 쪽으로
    if tile:
        tls = [fitz.Point(c.x - w / 2, c.y - h / 2) for c in _tile_centers(rect, w, h, rot90, gap=max(w, h) * 0.4)]
    else:
        tls = [anchor_point(rect, w, h, cell, None, offset)]
    xref = 0
    for tl in tls:
        r = fitz.Rect(tl.x, tl.y, tl.x + w, tl.y + h) * dm  # 보이는 좌표 → 회전 전
        r.normalize()
        # 바둑판은 같은 그림 객체를 다시 쓴다(xref)
        xref = page.insert_image(r, stream=None if xref else img, xref=xref, overlay=overlay,
                                 rotate=(page.rotation + rot90) % 360)


def watermark(doc: fitz.Document, pages=None, **settings) -> None:
    """워터마크(글 또는 파일)를 쪽 위에 얹는다. in place."""
    s = upgrade_settings("watermark", settings)
    text, path = s.get("text"), s.get("file")
    if not text and not path:
        raise EngineError("워터마크 글자나 그림 가운데 하나는 있어야 합니다")
    rotate, opacity = float(s.get("rotate", 0)), float(s.get("opacity", 1))
    cell, offset = s.get("position", "center"), s.get("offset") or (0, 0)
    for pno in _pages(doc, pages):
        page = doc[pno]
        if text:
            if s.get("tile"):
                w, h = text_box_size(text, s.get("font"))
                sh = page.new_shape()
                for c in _tile_centers(page.rect, w, h, rotate, gap=float(s["font"].get("size", 11)) * 1.2):
                    draw_text_box(page, text, s.get("font"), c, rotate=rotate, opacity=opacity, shape=sh)
                sh.commit()
            else:
                draw_text_box(page, text, s.get("font"), cell, rotate=rotate, opacity=opacity, offset=offset)
        if path:
            _place_file(page, s, overlay=True, cell=cell, offset=offset)


def background(doc: fitz.Document, pages=None, **settings) -> None:
    """바탕색 또는 바탕 파일(그림·PDF 한 쪽)을 본문 아래에 깐다. in place."""
    s = upgrade_settings("background", settings)
    col, path = _color(s.get("color")), s.get("file")
    if col is None and not path:
        raise EngineError("바탕색이나 바탕 그림 가운데 하나는 있어야 합니다")
    opacity = float(s.get("opacity", 1))
    for pno in _pages(doc, pages):
        page = doc[pno]
        if path:
            _place_file(page, {**s, "tile": False}, overlay=False)
            continue
        full = page.rect * page.derotation_matrix  # 회전 전 좌표로 쪽 전체(page.rect 로는 90·270° 쪽의 70% 만 덮었다)
        full.normalize()
        shape = page.new_shape()
        shape.draw_rect(full)
        shape.finish(color=None, fill=col, fill_opacity=opacity)
        shape.commit(overlay=False)
