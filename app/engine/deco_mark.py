# -*- coding: utf-8 -*-
"""워터마크·배경 그리기(글 · 그림 · PDF 한 쪽, 배율, 타일, 오프셋, 회전, 불투명도).

설정 꼴은 deco.upgrade_settings 가 정한 새 꼴(v 2)이고, 옛 인자(image_path·position="tile"·scale 배 등)로
불러도 먼저 새 꼴로 바꿔 그린다(일괄 처리의 옛 watermark(...) 호출이 그대로 돈다).

배율: scale_mode 「page」 는 돌린 상자가 쪽에 꼭 맞는 크기의 scale_pct %, 「max」 는 원래 크기의 scale_pct %.
글 워터마크는 「page」 일 때만 글꼴 크기 대신 쪽에 맞춘다(타일이면 늘 글꼴 크기).
타일: tile_gap = (빈칸 수, 빈 줄 수). 글꼴 크기에 맞는 거리로 바꿔 돌린 방향의 격자로 쪽을 덮는다.
offset: (오른쪽 +, 아래 +) pt.
"""
from __future__ import annotations

import io
import math
import os
from typing import Sequence

import fitz

from . import fonts as F
from .deco import anchor_point, draw_text_box, norm_font, text_box_size, upgrade_settings
from .tools import EngineError, _color, _pages

DAMAGED = "파일을 로드하지 못했습니다. 파일이 손상되었는지 확인하세요."
# 타일 간격 고르기 칸의 값(가로는 빈칸 수, 세로는 빈 줄 수)
TILE_GAPS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 30, 50, 80)

# PDF 워터마크·배경의 암호. 문서 안 설정(/RPDFSet)에는 적지 않고 이 실행 동안만 들고 있는다
_PASSWORDS: dict[str, str] = {}


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def remember_password(path: str, password: str) -> None:
    _PASSWORDS[_key(path)] = password


def pdf_locked(path: str) -> bool:
    """암호가 걸렸고 아직 암호를 모르는 PDF 인지."""
    try:
        with fitz.open(path) as d:
            return d.needs_pass and not d.authenticate(_PASSWORDS.get(_key(path), ""))
    except Exception:  # noqa: BLE001
        return False


def try_password(path: str, password: str) -> bool:
    """암호가 맞으면 기억해 두고 True."""
    with fitz.open(path) as d:
        ok = bool(d.authenticate(password))
    if ok:
        remember_password(path, password)
    return ok


def tile_step(font: dict | None, gap: Sequence[int]) -> tuple[float, float]:
    """타일 간격(빈칸 수, 빈 줄 수) → 거리(pt). 글꼴 크기에 맞춘 빈칸 너비·줄 높이로 센다."""
    f = norm_font(font)
    fo = F.font_object(F.font_file(f["name"], f["bold"], f["italic"])[0])
    gx, gy = (list(gap) + [2, 2])[:2]
    return (fo.text_length(" ", fontsize=f["size"]) * int(gx),
            f["size"] * (fo.ascender - fo.descender) * int(gy))


def _rot_box(w: float, h: float, angle: float) -> tuple[float, float]:
    t = math.radians(angle)
    return abs(w * math.cos(t)) + abs(h * math.sin(t)), abs(w * math.sin(t)) + abs(h * math.cos(t))


def _tile_centers(rect: fitz.Rect, w: float, h: float, angle: float, gap: Sequence[float]) -> list[fitz.Point]:
    """타일 자리(가운데 점). 돌린 방향을 따라 한 줄에 gap[0] 만큼, 줄과 줄은 gap[1] 만큼 띄운 격자로 쪽을 덮는다.
    격자는 쪽 가운데를 지나고, 가장자리에서 잘리는 것도 넣는다."""
    t = math.radians(angle)
    u = fitz.Point(math.cos(t), -math.sin(t))   # 글 방향(보이는 화면에서 시계 반대로 angle)
    v = fitz.Point(math.sin(t), math.cos(t))    # 다음 줄 방향
    su, sv = max(w + gap[0], 1.0), max(h + gap[1], 1.0)
    c = (rect.tl + rect.br) / 2
    bw, bh = _rot_box(w, h, angle)
    reach = math.hypot(rect.width, rect.height) / 2 + max(w, h)
    nu, nv = int(reach / su) + 1, int(reach / sv) + 1
    out = []
    for j in range(-nv, nv + 1):
        for i in range(-nu, nu + 1):
            p = c + u * (i * su) + v * (j * sv)
            if abs(p.x - c.x) < (rect.width + bw) / 2 and abs(p.y - c.y) < (rect.height + bh) / 2:
                out.append(p)
    return out


def _open_image(image_path: str, opacity: float, rotate: float) -> tuple[bytes, tuple[int, int]]:
    """그림에 불투명도를 섞고 rotate 도(시계 반대) 돌려 PNG 바이트로. (바이트, 돌리기 전 크기)."""
    from PIL import Image

    try:
        im = Image.open(image_path).convert("RGBA")
    except Exception as e:
        raise EngineError(DAMAGED) from e
    size = im.size
    if opacity < 1:
        a = im.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, opacity))))
        im.putalpha(a)
    if rotate % 360:
        im = im.rotate(rotate, resample=Image.Resampling.BICUBIC, expand=True)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), size


def is_pdf(path: str | None) -> bool:
    return bool(path) and os.path.splitext(path)[1].lower() == ".pdf"


def _pdf_source(path: str, pno: int, opacity: float, password: str | None = None) -> fitz.Document:
    """PDF 한 쪽을 한 쪽짜리 문서로(불투명도를 걸어서). 워터마크·배경으로 그 쪽을 그대로 얹는다."""
    try:
        src = fitz.open(path)
    except Exception as e:
        raise EngineError(DAMAGED) from e
    if src.needs_pass and not any(pw and src.authenticate(pw) for pw in (password, _PASSWORDS.get(_key(path)))):
        src.close()
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


def _scale_k(rect: fitz.Rect, w: float, h: float, angle: float, mode: str, pct: float) -> float:
    """원래 크기 w×h 에 곱할 배율. page: 돌린 상자가 쪽에 꼭 맞는 배율의 pct %, max: pct %."""
    k = max(float(pct), 1) / 100
    if mode == "page":
        bw, bh = _rot_box(w, h, angle)
        k *= min(rect.width / bw, rect.height / bh)
    return k


def _spots(page: fitz.Page, w: float, h: float, s: dict) -> list[fitz.Point]:
    """돌리기 전 크기 w×h 인 꾸미기의 가운데 점들. 타일이면 격자, 아니면 아홉 칸 자리 + 오프셋 한 곳
    (돌린 상자가 그 자리에 들어가게)."""
    rotate = float(s.get("rotate", 0))
    if s.get("tile"):
        return _tile_centers(page.rect, w, h, rotate, tile_step(s.get("font"), s.get("tile_gap") or (2, 2)))
    bw, bh = _rot_box(w, h, rotate)
    tl = anchor_point(page.rect, bw, bh, s.get("position", "center"), None, s.get("offset") or (0, 0))
    return [tl + (bw / 2, bh / 2)]


def _place_file(page: fitz.Page, s: dict, overlay: bool) -> None:
    """파일(그림 또는 PDF 한 쪽)을 쪽에 얹는다. 타일이면 쪽을 덮도록 되풀이한다."""
    path, rotate, opacity = s["file"], float(s.get("rotate", 0)), float(s.get("opacity", 1))
    if not os.path.exists(path):
        raise EngineError("이 파일이 존재하지 않습니다.")
    rect, dm = page.rect, page.derotation_matrix
    mode, pct = s.get("scale_mode", "page"), float(s.get("scale_pct", 100))
    if is_pdf(path):
        src = _pdf_source(path, s.get("file_page", 0), opacity, s.get("file_password"))
        try:
            sw, sh = src[0].rect.width, src[0].rect.height
            k = _scale_k(rect, sw, sh, rotate, mode, pct)
            w, h = sw * k, sh * k
            bw, bh = _rot_box(w, h, rotate)
            for c in _spots(page, w, h, s):
                r = fitz.Rect(c.x - bw / 2, c.y - bh / 2, c.x + bw / 2, c.y + bh / 2) * dm
                r.normalize()
                page.show_pdf_page(r, src, 0, overlay=overlay, rotate=-(rotate + page.rotation))
        finally:
            src.close()
        return
    img, (iw, ih) = _open_image(path, opacity, rotate)
    k = _scale_k(rect, iw, ih, rotate, mode, pct)
    w, h = iw * k, ih * k
    bw, bh = _rot_box(w, h, rotate)
    xref = 0
    for c in _spots(page, w, h, s):
        r = fitz.Rect(c.x - bw / 2, c.y - bh / 2, c.x + bw / 2, c.y + bh / 2) * dm  # 보이는 좌표 → 회전 전
        r.normalize()
        # 타일은 같은 그림 객체를 다시 쓴다(xref)
        xref = page.insert_image(r, stream=None if xref else img, xref=xref, overlay=overlay,
                                 rotate=page.rotation % 360)


def _text_font(page: fitz.Page, s: dict) -> dict:
    """글 워터마크의 글꼴. 「페이지 비율」 이면 돌린 글 상자가 쪽의 scale_pct % 에 맞는 크기로."""
    f = norm_font(s.get("font"))
    if s.get("scale_mode") == "page" and not s.get("tile"):
        w, h = text_box_size(s["text"], f)
        if w > 0 and h > 0:
            f["size"] *= _scale_k(page.rect, w, h, float(s.get("rotate", 0)), "page", float(s.get("scale_pct", 50)))
    return f


def watermark(doc: fitz.Document, pages=None, **settings) -> None:
    """워터마크(글 또는 파일)를 쪽 위에 얹는다. in place."""
    s = upgrade_settings("watermark", settings)
    text, path = s.get("text") or "", s.get("file")
    if not text.strip() and not path:
        raise EngineError("워터마크 글자나 파일 가운데 하나는 있어야 합니다")
    rotate, opacity = float(s.get("rotate", 0)), float(s.get("opacity", 1))
    for pno in _pages(doc, pages):
        page = doc[pno]
        if text.strip():
            font = _text_font(page, s)
            w, h = text_box_size(text, font)
            sh = page.new_shape()
            for c in _spots(page, w, h, {**s, "font": font}):
                draw_text_box(page, text, font, c, rotate=rotate, opacity=opacity, shape=sh)
            sh.commit()
        if path:
            _place_file(page, s, overlay=True)


def background(doc: fitz.Document, pages=None, **settings) -> None:
    """바탕색 또는 바탕 파일(그림·PDF 한 쪽)을 본문 아래에 깐다. 파일은 쪽 가운데에 배율·회전대로. in place."""
    s = upgrade_settings("background", settings)
    col, path = _color(s.get("color")), s.get("file")
    if col is None and not path:
        raise EngineError("바탕색이나 바탕 파일 가운데 하나는 있어야 합니다")
    opacity = float(s.get("opacity", 1))
    for pno in _pages(doc, pages):
        page = doc[pno]
        if path:
            _place_file(page, {**s, "tile": False, "position": "center", "offset": (0, 0)}, overlay=False)
            continue
        full = page.rect * page.derotation_matrix  # 회전 전 좌표로 쪽 전체(page.rect 로는 90·270° 쪽의 70% 만 덮었다)
        full.normalize()
        shape = page.new_shape()
        shape.draw_rect(full)
        shape.finish(color=None, fill=col, fill_opacity=opacity)
        shape.commit(overlay=False)
