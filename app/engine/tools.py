# -*- coding: utf-8 -*-
"""PDF 도구 엔진: 압축·워터마크·머리글·보안·가리기·비교·양식·일괄.

화면(Qt) 코드는 들여오지 않는다. 사용자에게 보일 실패는 EngineError(한국어) 로 올린다.
「in place」 로 적힌 함수는 넘겨받은 Document 를 고치고 None 을 돌려준다. 그 밖의 함수는
넘겨받은 Document 를 건드리지 않는다.
"""
from __future__ import annotations

import csv
import difflib
import json
import os
import re
from typing import Callable, Iterable, Sequence

import fitz

from ..appinfo import APP_NAME
from .annots import reset_widget, save_widget

Progress = Callable[[int, int, str], None] | None


class EngineError(Exception):
    """사용자에게 보여 줄 실패(메시지는 한국어)."""


def _subset(doc: fitz.Document):
    """저장 직전에 글꼴을 쓴 글자만 남긴다. 글자 하나만 넣어도 맑은 고딕 통째(7MB 넘게)가 실린다."""
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001
        pass


# ── 공용 ────────────────────────────────────────────────────────────────
PAGE_SIZES: dict[str, tuple[float, float]] = {       # mm (세로 방향 기준)
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "B4": (257.0, 364.0),      # JIS B4 (한국 인쇄 관행)
    "B5": (182.0, 257.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}
MM = 72.0 / 25.4

_KOREAN_FONT_FILES = ("malgun.ttf", "NanumGothic.ttf", "gulim.ttc", "batang.ttc")
_FONT_CACHE: dict[str, str] = {}


def korean_font_path() -> str:
    """한글이 되는 트루타입 글꼴 파일 경로(맑은 고딕 우선)."""
    if "path" in _FONT_CACHE:
        return _FONT_CACHE["path"]
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in _KOREAN_FONT_FILES:
        p = os.path.join(fonts_dir, name)
        if os.path.exists(p):
            _FONT_CACHE["path"] = p
            return p
    raise EngineError("한글 글꼴(맑은 고딕)을 찾지 못했습니다")


def korean_font() -> fitz.Font:
    """글자를 그릴 때 늘 쓰는 한글 글꼴(fitz.Font). 내장 글꼴은 한글이 없다."""
    return fitz.Font(fontfile=korean_font_path())


def _say(progress: Progress, done: int, total: int, msg: str) -> None:
    if progress:
        try:
            progress(done, total, msg)
        except Exception:
            pass


def _pages(doc: fitz.Document, pages: Iterable[int] | None) -> list[int]:
    if pages is None:
        return list(range(doc.page_count))
    out = []
    for p in pages:
        if 0 <= int(p) < doc.page_count:
            out.append(int(p))
    return out


def _color(c) -> tuple[float, float, float] | None:
    """'#rrggbb' · (r,g,b) 0~1 · (r,g,b) 0~255 → (r,g,b) 0~1."""
    if c is None:
        return None
    if isinstance(c, str):
        s = c.strip().lstrip("#")
        if len(s) != 6:
            raise EngineError(f"색 값이 잘못됐습니다: {c}")
        return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    t = tuple(float(v) for v in c)[:3]
    if any(v > 1 for v in t):
        t = tuple(v / 255 for v in t)
    return t  # type: ignore[return-value]


def page_size_points(size="A4", orientation: str | None = "portrait") -> tuple[float, float]:
    """용지 이름 또는 (가로mm, 세로mm) → (가로pt, 세로pt)."""
    if isinstance(size, str):
        key = size.strip()
        hit = next((k for k in PAGE_SIZES if k.lower() == key.lower()), None)
        if hit is None:
            raise EngineError(f"모르는 용지 크기입니다: {size}")
        w_mm, h_mm = PAGE_SIZES[hit]
    else:
        w_mm, h_mm = float(size[0]), float(size[1])
    w, h = w_mm * MM, h_mm * MM
    if orientation == "landscape" and w < h:
        w, h = h, w
    elif orientation == "portrait" and w > h:
        w, h = h, w
    return w, h


def _file_stem(doc: fitz.Document) -> str:
    name = getattr(doc, "name", "") or ""
    return os.path.splitext(os.path.basename(name))[0] if name else ""


# ── 압축 ────────────────────────────────────────────────────────────────
# 단계값(높음 72dpi·45 / 중간 120dpi·70 / 낮음 150dpi·85). 목표 해상도보다 10% 넘게 고운 그림만 줄인다
# (조금 넘는 것까지 다시 줄이면 화질만 깎인다. 문턱을 1/4 위로 두었더니 흔한 150dpi 스캔이 중간에서 안 줄었다)
_LEVELS = {
    "high": dict(dpi_threshold=80, dpi_target=72, quality=45),
    "medium": dict(dpi_threshold=132, dpi_target=120, quality=70),
    "low": dict(dpi_threshold=165, dpi_target=150, quality=85),
}
COMPRESS_LEVELS = ("high", "medium", "low")   # 화면 차례(기본은 첫째 「높음」)


class NotSmaller(EngineError):
    """압축해도 원본보다 작아지지 않았다(결과 파일은 만들지 않음)."""


NOT_SMALLER = "이 PDF의 크기가 최대한으로 축소되었으며 더 이상 축소할 수 없습니다."
# 사용자 지정 창에서 고르는 해상도(dpi). 중간 단계 기본 120 도 목록에 둔다(없으면 그대로 확인해도 값이 바뀐다)
CUSTOM_DPIS = (72, 96, 120, 144, 150, 300, 400, 600)
# 해상도 → JPEG 품질: 「해상도 ≥ 지정값」 인 첫 줄의 품질, 없으면 100
_QUALITY_BY_DPI = ((72, 45), (96, 55), (120, 70), (150, 85), (300, 95), (600, 100), (1200, 100))
CUSTOM_KEYS = ("dedupe", "streams", "bookmarks", "attach", "meta", "annots")


def quality_for_dpi(dpi: int) -> int:
    return next((q for d, q in _QUALITY_BY_DPI if d >= dpi), 100)


def compress_defaults(level: str) -> dict:
    """단계별 사용자 지정 기본값: 그 단계 해상도 · 중복 제거·스트림 압축·메타데이터 삭제 켬, 나머지 끔."""
    return {"dpi": _LEVELS.get(level, _LEVELS["high"])["dpi_target"], "dedupe": True, "streams": True,
            "bookmarks": False, "attach": False, "meta": True, "annots": False}


def _level_options(level: str, custom: dict | None) -> tuple[dict, dict]:
    """(그림 줄이기 값, 사용자 지정 값). 해상도를 바꿨으면 문턱·품질도 그 해상도에 맞춘다."""
    c = {**compress_defaults(level), **(custom or {})}
    opt = dict(_LEVELS[level])
    dpi = int(c.get("dpi") or opt["dpi_target"])
    if dpi != opt["dpi_target"]:
        opt = dict(dpi_threshold=int(dpi * 1.1), dpi_target=dpi, quality=quality_for_dpi(dpi))
    return opt, c


def _keep_copy(doc: fitz.Document, password: str | None = None) -> fitz.Document:
    """암호·권한을 그대로 둔 작업 사본. 여기서 고치고 encryption=KEEP 으로 저장하면 결과에도 같은 암호가 남는다.
    (그냥 tobytes 로 뜬 사본은 암호가 풀려, 압축·평면화·일괄 결과가 암호 없이 나갔다)"""
    from .document import DocumentError, keep_copy
    try:
        return keep_copy(doc, password)
    except DocumentError as e:
        raise EngineError(str(e)) from e


def compress(doc: fitz.Document, out_path: str, level: str = "high",
             progress: Progress = None, password: str | None = None, remove_metadata: bool | None = None,
             before: int | None = None, custom: dict | None = None) -> dict:
    """그림을 다시 눌러 담고 쓰레기 객체를 걷어 저장. {"before": 바이트, "after": 바이트}. 암호는 그대로.

    흑백(1비트) 그림도 같은 해상도로 줄인다. 원본보다 작아지지 않으면 out_path 를 만들지 않고 NotSmaller.
    before: 원본 파일 크기(사본을 넘길 때. 없으면 doc.name 파일이나 지금 바이트 길이로 잰다).
    custom: 그 단계의 사용자 지정 값(compress_defaults 꼴: dpi · dedupe · streams · bookmarks · attach · meta · annots).
    remove_metadata 는 옛 인자(주면 custom 의 meta 보다 앞선다)."""
    if level not in _LEVELS:
        raise EngineError(f"압축 단계가 잘못됐습니다: {level}")
    opt, c = _level_options(level, custom)
    if remove_metadata is not None:
        c["meta"] = bool(remove_metadata)
    work = _keep_copy(doc, password)
    # 다시 쓴 바이트 길이가 아니라 실제 파일 크기와 대조한다(원본보다 커진 것도 줄었다고 보였다)
    # 사본을 encryption 없이 tobytes 하면 사본의 암호가 떨어져 KEEP 저장이 깨진다(길이만 잴 때도 KEEP)
    if before is None:
        before = os.path.getsize(doc.name) if doc.name and os.path.exists(doc.name) else len(doc.tobytes(encryption=fitz.PDF_ENCRYPT_KEEP))
    tmp = out_path + ".rpdf-tmp"
    try:
        _say(progress, 0, 2, "그림 다시 담는 중")
        if hasattr(work, "rewrite_images"):
            try:
                work.rewrite_images(options=_rewrite_options(opt))
            except Exception:
                _manual_recompress(work, opt, progress)
        else:
            _manual_recompress(work, opt, progress)
        _strip_content(work, c)
        _say(progress, 1, 2, "저장하는 중")
        _subset(work)
        streams = bool(c["streams"])
        work.save(tmp, garbage=4 if c["dedupe"] else 1, deflate=streams, clean=streams, deflate_images=True,
                  deflate_fonts=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        work.close()
    after = os.path.getsize(tmp)
    if after >= before:  # 커진 파일을 「압축」 이라고 남기지 않는다
        os.remove(tmp)
        raise NotSmaller(NOT_SMALLER)
    os.replace(tmp, out_path)
    _say(progress, 2, 2, "압축 끝")
    return {"before": before, "after": after}


def _strip_content(work: fitz.Document, c: dict) -> None:
    """사용자 지정 「삭제된 콘텐츠 압축」: 북마크 · 첨부 파일 · 문서 정보와 메타데이터 · 모든 메모와 양식."""
    if c.get("bookmarks"):
        work.set_toc([])
    if c.get("attach"):
        for name in list(work.embfile_names()):
            work.embfile_del(name)
    if c.get("annots") or c.get("attach"):
        for page in work:
            for a in list(page.annots() or []):
                if c.get("annots") or a.type[0] == fitz.PDF_ANNOT_FILE_ATTACHMENT:
                    page.delete_annot(a)
            if c.get("annots"):
                for wd in list(page.widgets() or []):
                    page.delete_widget(wd)
    if c.get("meta"):
        work.set_metadata({})
        try:
            work.del_xml_metadata()
        except Exception:  # noqa: BLE001
            pass


def _rewrite_options(opt: dict):
    """rewrite_images 기본(평균 솎기)은 정수배로만 줄여 212dpi 스캔이 120dpi 로 안 줄었다(중간·낮음이 같은 크기).
    컬러·회색은 bicubic 으로 목표 해상도에 맞춰 줄인다. 흑백(1비트)은 팩스 압축이라 평균 솎기 그대로."""
    m = fitz.mupdf
    o = m.PdfImageRewriterOptions()
    q = str(opt["quality"])
    for kind in ("color_lossless", "color_lossy", "gray_lossless", "gray_lossy"):
        setattr(o, f"{kind}_image_recompress_method", m.FZ_RECOMPRESS_JPEG)
        setattr(o, f"{kind}_image_subsample_method", m.FZ_SUBSAMPLE_BICUBIC)
        setattr(o, f"{kind}_image_subsample_threshold", opt["dpi_threshold"])
        setattr(o, f"{kind}_image_subsample_to", opt["dpi_target"])
        setattr(o, f"{kind}_image_recompress_quality", q)
    o.bitonal_image_recompress_method = m.FZ_RECOMPRESS_FAX
    o.bitonal_image_subsample_method = m.FZ_SUBSAMPLE_AVERAGE
    o.bitonal_image_subsample_threshold = opt["dpi_threshold"]
    o.bitonal_image_subsample_to = opt["dpi_target"]
    o.bitonal_image_recompress_quality = q
    return o


def _manual_recompress(work: fitz.Document, opt: dict, progress: Progress) -> None:
    """rewrite_images 가 없을 때: 그림마다 Pillow 로 다시 눌러 담는다."""
    import io

    from PIL import Image

    done_xrefs: set[int] = set()
    n = work.page_count
    for i, page in enumerate(work):
        _say(progress, i, n, f"그림 다시 담는 중 {i + 1}/{n}쪽")
        for info in page.get_images(full=True):
            xref = info[0]
            if xref in done_xrefs:
                continue
            done_xrefs.add(xref)
            try:
                raw = work.extract_image(xref)
                im = Image.open(io.BytesIO(raw["image"]))
                if im.mode in ("RGBA", "P", "LA"):
                    im = im.convert("RGB")
                elif im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                rects = page.get_image_rects(xref)
                if rects:
                    r = rects[0]
                    want = max(1, int(r.width / 72 * opt["dpi_target"]))
                    if im.width > want * 1.1:
                        im = im.resize((want, max(1, int(im.height * want / im.width))), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=opt["quality"], optimize=True)
                if buf.tell() < len(raw["image"]):
                    page.replace_image(xref, stream=buf.getvalue())
            except Exception:
                continue


# ── 평탄화 ──────────────────────────────────────────────────────────────
def flatten(doc: fitz.Document, out_path: str, password: str | None = None) -> str:
    """주석·입력칸을 본문 그림으로 굳혀 저장. 암호는 그대로."""
    work = _keep_copy(doc, password)
    try:
        if not hasattr(work, "bake"):
            raise EngineError("이 PyMuPDF 에는 평탄화(bake)가 없습니다")
        work.bake(annots=True, widgets=True)
        _subset(work)
        work.save(out_path, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    finally:
        work.close()
    return out_path


# ── 꾸미기(워터마크 · 배경 · 머리글/바닥글 · 페이지 번호 · 베이츠) ──────────
# deco · deco_mark · deco_text 로 옮겼다. 옛 이름(tools.watermark 등)은 여기서 그대로 꺼내 쓴다.
# 그 모듈들이 이 파일의 공용 함수를 가져다 쓰므로 맨 위에서 들이지 않고 처음 부를 때 가져온다(돌림 들이기 방지).
_DECO_NAMES = {
    "deco": ("DECO_KINDS", "add_decoration", "decorations", "remove_decorations", "replace_decorations",
             "render_preview", "upgrade_settings", "draw_text_box", "expand_macros"),
    "deco_mark": ("watermark", "background"),
    "deco_text": ("header_footer", "page_numbers", "bates_numbers", "PAGE_NUMBER_STYLES", "HF_SLOTS"),
}


def __getattr__(name: str):
    import importlib
    for mod, names in _DECO_NAMES.items():
        if name in names:
            return getattr(importlib.import_module(f".{mod}", __package__), name)
    raise AttributeError(name)


# ── 빈 쪽 삭제 · 기울기 보정 ────────────────────────────────────────────
def _white_fraction(page: fitz.Page, dpi: int = 36, threshold_gray: int = 245) -> float:
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    arr = np.frombuffer(pix.samples, np.uint8)
    if arr.size == 0:
        return 1.0
    return float((arr >= threshold_gray).mean())


def _ink_pixels(page: fitz.Page, dpi: int = 50) -> int:
    """종이 바탕(중앙값)보다 눈에 띄게 어두운 점 수. 낮은 해상도로 떠서 잡티는 흐려지고, 가장자리 그림자는 빼고 센다."""
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].astype(np.int16)
    if a.size == 0:
        return 0
    dark = a < int(np.median(a)) - 60
    h, w = dark.shape
    mh, mw = max(1, h * 4 // 100), max(1, w * 4 // 100)
    return int(dark[mh:h - mh, mw:w - mw].sum())


def is_blank_page(page: fitz.Page, threshold: float = 0.995) -> bool:
    """글자층·벡터 그림·주석이 없고, 그림으로 봐도 잉크가 거의 없는 쪽.

    전에는 36dpi 흰 비율 99.5% 만 봐서 벡터 그래프만 있는 쪽, 「정답 ⑤」 한 줄 스캔 쪽, 서명 한 획 쪽까지 지웠다.
    threshold 는 옛 호출과 맞추려고 남겨 둔다.
    """
    if page.get_text("text").strip():
        return False
    if page.get_drawings() or page.first_annot or page.first_widget:
        return False
    return _ink_pixels(page) < 8


def delete_blank_pages(doc: fitz.Document, threshold: float = 0.995) -> list[int]:
    """글자가 없고 거의 흰 쪽을 지운다. 지운 쪽 번호(원래 번호, 0부터) 목록. in place."""
    blanks = [i for i in range(doc.page_count) if is_blank_page(doc[i], threshold)]
    if blanks and len(blanks) < doc.page_count:
        doc.delete_pages(blanks)
    elif blanks:
        # 전부 빈 쪽이면 첫 쪽은 남긴다(쪽 없는 PDF 는 못 만든다)
        doc.delete_pages(blanks[1:])
        blanks = blanks[1:]
    return blanks


def _estimate_skew(gray, max_angle: float = 10.0, step: float = 0.25) -> float:
    """가로 투영의 분산이 가장 큰 각도(도). 양수 = 시계 반대 방향으로 돌려야 바로 선다."""
    import numpy as np
    from PIL import Image

    im = Image.fromarray(gray)
    if im.width > 800:
        im = im.resize((800, max(1, int(im.height * 800 / im.width))))
    g = np.asarray(im, dtype=np.int16)
    # 종이 바탕(중앙값)보다 어두운 것만 잉크로 본다. 전에는 회색 195 미만을 다 잉크로 봐서
    # 색 바탕 표지 전체가 잉크가 되고 반듯한 쪽을 찾는 범위 끝(10°)까지 돌렸다.
    ink = (g < int(np.median(g)) - 60).astype(np.uint8) * 255
    base = Image.fromarray(ink)
    best, best_score = 0.0, -1.0
    angles = np.arange(-max_angle, max_angle + step / 2, step)
    for a in angles:
        rot = base.rotate(float(a), resample=Image.NEAREST, expand=False, fillcolor=0)
        prof = np.asarray(rot, dtype=np.float32).sum(axis=1)
        score = float(prof.var())
        if score > best_score:
            best_score, best = score, float(a)
    return best


def deskew(doc: fitz.Document, pages=None, max_angle: float = 10.0,
           progress: Progress = None) -> list[float]:
    """글자층이 없는 쪽(스캔본)의 기울기를 바로잡는다. 쪽마다 돌린 각도 목록(안 돌린 쪽은 0). in place.

    쪽을 그림으로 다시 뜨지 않고 쪽 내용 앞뒤에 회전 행렬(q … cm … Q)을 끼운다. 그래서 화질·크기가 그대로이고
    쪽 객체가 남아 북마크·링크·주석도 그대로다.
    """
    import numpy as np

    targets = _pages(doc, pages)
    angles: list[float] = []
    step = 0.25
    for k, pno in enumerate(targets):
        _say(progress, k, len(targets), f"기울기 재는 중 {pno + 1}쪽")
        page = doc[pno]
        if page.get_text("text").strip():
            angles.append(0.0)
            continue
        pix = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY)
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].copy()
        ang = _estimate_skew(gray, max_angle, step)
        # 0.5° 미만은 눈에 안 띄고, 찾는 범위 끝에 걸린 값은 잘못 잰 것이다(반듯한 표지가 10° 돌던 일)
        if abs(ang) < 0.5 or abs(ang) >= max_angle - step:
            angles.append(0.0)
            continue
        _rotate_content(doc, page, ang)
        angles.append(ang)
    _say(progress, len(targets), len(targets), "기울기 보정 끝")
    return angles


def _rotate_content(doc: fitz.Document, page: fitz.Page, deg: float):
    """쪽 내용을 보이는 영역 가운데를 축으로 deg 만큼(화면에서 시계 반대) 돌린다."""
    import math

    cb = page.cropbox
    c = fitz.Point(cb.width / 2, cb.height / 2) * ~page.transformation_matrix  # 쪽 좌표 → PDF 좌표
    t = math.radians(deg)
    cs, sn = math.cos(t), math.sin(t)
    e = c.x - c.x * cs + c.y * sn
    f = c.y - c.x * sn - c.y * cs
    page.wrap_contents()
    head = doc.get_new_xref()
    doc.update_object(head, "<<>>")
    doc.update_stream(head, f"q {cs:.6f} {sn:.6f} {-sn:.6f} {cs:.6f} {e:.4f} {f:.4f} cm\n".encode())
    tail = doc.get_new_xref()
    doc.update_object(tail, "<<>>")
    doc.update_stream(tail, b"\nQ\n")
    xrefs = [head] + list(page.get_contents()) + [tail]
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")


# ── 쪽 상자 · 크기 ─────────────────────────────────────────────────────
def crop_pages(doc: fitz.Document, rect, pages=None) -> None:
    """자르기(CropBox, 포인트). in place."""
    r = fitz.Rect(rect)
    for pno in _pages(doc, pages):
        page = doc[pno]
        page.set_cropbox(r & page.mediabox if (r & page.mediabox).is_valid else r)


def set_page_boxes(doc: fitz.Document, box: str, rect, pages=None) -> None:
    """media/crop/trim/bleed/art 상자를 정한다. in place."""
    r = fitz.Rect(rect)
    setters = {
        "media": "set_mediabox", "crop": "set_cropbox", "trim": "set_trimbox",
        "bleed": "set_bleedbox", "art": "set_artbox",
    }
    name = setters.get(box.lower().replace("box", ""))
    if not name:
        raise EngineError(f"모르는 쪽 상자입니다: {box}")
    for pno in _pages(doc, pages):
        getattr(doc[pno], name)(r)


def resize_pages(doc: fitz.Document, size="A4", orientation: str | None = None,
                 scale_content: bool = True, pages=None) -> None:
    """쪽 크기를 바꾼다. scale_content 면 내용을 비율 맞춰 채우고, 아니면 가운데 그대로 둔다. in place."""
    targets = _pages(doc, pages)
    from .document import plain_bytes
    src = fitz.open("pdf", plain_bytes(doc))
    try:
        for pno in targets:
            old = doc[pno]
            orient = orientation
            if orient is None:
                orient = "landscape" if old.rect.width > old.rect.height else "portrait"
            w, h = page_size_points(size, orient)
            new = doc.new_page(pno=pno + 1, width=w, height=h)
            if scale_content:
                new.show_pdf_page(new.rect, src, pno, keep_proportion=True)
            else:
                ow, oh = old.rect.width, old.rect.height
                x0 = (w - ow) / 2
                y0 = (h - oh) / 2
                new.show_pdf_page(fitz.Rect(x0, y0, x0 + ow, y0 + oh), src, pno, keep_proportion=True)
            doc.delete_page(pno)
    finally:
        src.close()


# ── 보안 ────────────────────────────────────────────────────────────────
# 암호화 방식(셋, 기본 128 bit AES)
ENCRYPT_METHODS = {"aes128": fitz.PDF_ENCRYPT_AES_128, "aes256": fitz.PDF_ENCRYPT_AES_256, "rc4": fitz.PDF_ENCRYPT_RC4_128}


def encryption_method(doc: fitz.Document) -> str | None:
    """지금 문서의 암호화 방식(ENCRYPT_METHODS 열쇠). 암호가 없으면 None."""
    s = ((doc.metadata or {}).get("encryption") or "").upper()
    if not s:
        return None
    if "AES" in s:
        return "aes256" if "256" in s else "aes128"
    return "rc4"


def encrypt(doc: fitz.Document, out_path: str, user_pw: str = "", owner_pw: str = "",
            allow_print: bool = True, allow_copy: bool = True, allow_modify: bool = True,
            allow_annotate: bool = True, allow_forms: bool = True, allow_accessibility: bool = True,
            allow_assemble: bool = True, allow_print_hq: bool = True, method: str = "aes128") -> str:
    """암호를 걸어 out_path 에 저장. method: aes128(기본) · aes256 · rc4."""
    with open(out_path, "wb") as f:
        f.write(encrypt_bytes(doc, user_pw, owner_pw, method, allow_print=allow_print, allow_copy=allow_copy,
                              allow_modify=allow_modify, allow_annotate=allow_annotate, allow_forms=allow_forms,
                              allow_accessibility=allow_accessibility, allow_assemble=allow_assemble,
                              allow_print_hq=allow_print_hq)[0])
    return out_path


def encrypt_bytes(doc: fitz.Document, user_pw: str = "", owner_pw: str = "", method: str = "aes128",
                  allow_print: bool = True, allow_copy: bool = True, allow_modify: bool = True,
                  allow_annotate: bool = True, allow_forms: bool = True, allow_accessibility: bool = True,
                  allow_assemble: bool = True, allow_print_hq: bool = True) -> tuple[bytes, str]:
    """암호를 건 PDF 바이트와 다시 열 때 쓸 권한 암호. 열린 문서에 걸 때는 Document.security 에 이 인자를 넣어 두면
    다음 저장에서 이것으로 쓴다(「암호화」 와 같은 흐름. 창을 닫는 순간 파일이 바뀌지 않음)."""
    check_encrypt_args(user_pw, owner_pw, method)
    perm = 0
    for flag, allowed in (
        (fitz.PDF_PERM_PRINT, allow_print), (fitz.PDF_PERM_COPY, allow_copy),
        (fitz.PDF_PERM_MODIFY, allow_modify), (fitz.PDF_PERM_ANNOTATE, allow_annotate),
        (fitz.PDF_PERM_FORM, allow_forms), (fitz.PDF_PERM_ACCESSIBILITY, allow_accessibility),
        (fitz.PDF_PERM_ASSEMBLE, allow_assemble), (fitz.PDF_PERM_PRINT_HQ, allow_print_hq),
    ):
        if allowed:
            perm |= flag
    # 권한 암호를 비우면 전에는 열기 암호가 권한 암호로도 쓰여, 열기 암호로 열면 인쇄·복사 막기가 무의미했다
    import secrets
    from .document import plain_bytes
    owner = owner_pw or secrets.token_urlsafe(18)
    work = fitz.open("pdf", plain_bytes(doc))  # 열린 문서의 글꼴을 줄이면 이어서 넣는 글자가 빠진다. 사본에서.
    try:
        _subset(work)
        data = work.tobytes(encryption=ENCRYPT_METHODS[method], owner_pw=owner, user_pw=user_pw,
                            permissions=perm, garbage=3, deflate=True)
    finally:
        work.close()
    chk = fitz.open("pdf", data)   # 암호가 정말 걸렸는지 다시 열어 본다(안 걸린 채 「암호화」 로 넘어가지 않게)
    try:
        if not (chk.metadata or {}).get("encryption") and not chk.needs_pass:
            raise EngineError("암호를 걸지 못했습니다(암호에 쓸 수 없는 글자가 있는지 보세요)")
    finally:
        chk.close()
    return data, owner


def check_encrypt_args(user_pw: str = "", owner_pw: str = "", method: str = "aes128") -> None:
    """암호를 걸기 전에 확인(창을 닫기 전에 부른다). 틀리면 EngineError."""
    if method not in ENCRYPT_METHODS:
        raise EngineError(f"암호화 방식이 잘못됐습니다: {method}")
    if not user_pw and not owner_pw:
        raise EngineError("열기 암호나 권한 암호 가운데 하나는 넣어야 합니다")
    if user_pw and owner_pw and user_pw == owner_pw:
        raise EngineError("열기 암호와 권한 암호는 달라야 합니다(같으면 열기 암호로 권한 제한이 풀립니다)")
    # 128 bit 방식(AES·RC4)은 암호를 영문 글자로만 다룬다. 한글 암호를 넣으면 MuPDF 가 말없이 암호 없는 파일을 만든다
    if method != "aes256" and not (user_pw + owner_pw).isascii():
        raise EngineError("128 bit 방식은 영문·숫자·기호 암호만 됩니다. 한글 암호를 쓰려면 256 bit AES 를 고르세요")


def is_encrypted(path: str) -> bool:
    try:
        d = fitz.open(path)
    except Exception as e:
        raise EngineError(f"파일을 열지 못했습니다: {os.path.basename(path)}") from e
    try:
        return bool(d.needs_pass or d.is_encrypted)
    finally:
        d.close()


def authenticate(doc: fitz.Document, password: str) -> bool:
    """암호를 넣어 연다. 맞으면 True."""
    if not doc.needs_pass:
        return True
    return bool(doc.authenticate(password))


def decrypt(path: str, password: str, out_path: str) -> str:
    """암호를 풀어 저장."""
    d = fitz.open(path)
    try:
        if d.needs_pass and not d.authenticate(password):
            raise EngineError("암호가 맞지 않습니다")
        d.save(out_path, encryption=fitz.PDF_ENCRYPT_NONE, garbage=3, deflate=True)
    finally:
        d.close()
    return out_path


# ── 권한 단계(암호화 창) ───────────────────────────────────────────────
PERM_ALL = (fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ | fitz.PDF_PERM_COPY | fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE
            | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_ASSEMBLE)
PERM_WRITE = fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ASSEMBLE
# 변경 단계마다 허용하는 것(0 불가 · 1 쪽 넣기·지우기·돌리기 · 2 양식 채우기·서명 · 3 주석·양식·서명 · 4 쪽 추출 빼고 모두)
_CHANGE_FLAGS = ((), ("allow_assemble",), ("allow_forms",), ("allow_annotate", "allow_forms"),
                 ("allow_modify", "allow_annotate", "allow_forms", "allow_assemble"))


def perm_flags(print_level: int, change_level: int, copy: bool) -> dict:
    """인쇄 3단계·변경 5단계·복사 → encrypt_bytes 의 allow_* 여덟. 접근성 읽기는 늘 켠다."""
    d = {"allow_print": print_level >= 1, "allow_print_hq": print_level >= 2, "allow_copy": bool(copy),
         "allow_accessibility": True}
    on = _CHANGE_FLAGS[max(0, min(4, change_level))]
    for k in ("allow_modify", "allow_annotate", "allow_forms", "allow_assemble"):
        d[k] = k in on
    return d


def perm_levels(p: int) -> tuple[int, int, bool]:
    """권한 값(/P) → (인쇄 단계, 변경 단계, 복사)."""
    pr = 2 if p & fitz.PDF_PERM_PRINT and p & fitz.PDF_PERM_PRINT_HQ else 1 if p & fitz.PDF_PERM_PRINT else 0
    if p & fitz.PDF_PERM_MODIFY:
        ch = 4
    elif p & fitz.PDF_PERM_ANNOTATE:
        ch = 3
    elif p & fitz.PDF_PERM_FORM:
        ch = 2
    elif p & fitz.PDF_PERM_ASSEMBLE:
        ch = 1
    else:
        ch = 0
    return pr, ch, bool(p & fitz.PDF_PERM_COPY)


def file_permissions(doc: fitz.Document) -> int | None:
    """파일에 적힌 권한 값(/P). 권한 암호로 풀어 연 뒤에도 원래 제한을 읽는다. 암호가 없으면 None."""
    try:
        kind, val = doc.xref_get_key(-1, "Encrypt")
        if kind == "xref":
            kind, val = "dict", doc.xref_object(int(val.split()[0]), compressed=True)
        m = re.search(r"/P\s*(-?\d+)", val) if kind == "dict" else None
    except Exception:  # noqa: BLE001
        return None
    return int(m.group(1)) if m else None


def is_restricted(doc: fitz.Document) -> bool:
    """권한 암호를 몰라 쓰기 권한이 모자란 채 열린 문서(MuPDF permissions 로 판정)."""
    if not (doc.metadata or {}).get("encryption"):
        return False
    return (doc.permissions & PERM_WRITE) != PERM_WRITE


def unlock_owner(doc: fitz.Document, password: str) -> bool:
    """권한 암호를 넣어 제한을 푼다. 권한 암호가 맞으면 True(열기 암호는 False)."""
    try:
        return bool(doc.authenticate(password or "") & 4)
    except Exception:  # noqa: BLE001
        return False


# ── 가리기(리댁션) · 숨은 정보 ───────────────────────────────────────────
def redact_find(doc: fitz.Document, text: str, whole_word: bool = False, case: bool = False, regex: bool = False,
                pages=None) -> list[dict]:
    """찾은 곳 목록(검색 & 교정 패널). 한 건 = {"page", "rects", "text", "ctx"}. 표시는 안 단다."""
    if not text:
        raise EngineError("검색 텍스트는 비워둘 수 없습니다.")
    from .document import find_text_ctx, search_pattern
    try:
        rx = search_pattern(text, regex=regex, case=case, whole_word=whole_word)
    except re.error as e:
        raise EngineError(f"정규식이 맞지 않습니다 ({e})") from e
    hits = []
    for pno in _pages(doc, pages):
        for t, rects, ctx in find_text_ctx(doc[pno], rx):
            hits.append({"page": pno, "rects": [fitz.Rect(r) for r in rects], "text": t, "ctx": ctx})
    return hits


def mark_hits(doc: fitz.Document, hits: Sequence[dict], fill=(0, 0, 0), style: dict | None = None) -> int:
    """찾은 곳에 교정 표시를 단다(아직 적용은 안 함). 단 건수."""
    col = _color(fill)
    n = 0
    for h in hits:
        page = doc[h["page"]]
        for r in h["rects"]:
            a = page.add_redact_annot(r, fill=col)
            if style:
                set_redact_style(doc, h["page"], a.xref, **style)
        n += 1
    return n


def redact_search(doc: fitz.Document, pattern: str, regex: bool = False, pages=None,
                  fill=(0, 0, 0)) -> int:
    """찾은 글자마다 가리기 주석을 붙인다(아직 적용은 안 함). 붙인 수."""
    if not pattern:
        raise EngineError("찾을 글자를 넣어 주세요")
    # 정규식은 낱말 하나가 아니라 줄 글자 전체에 건다(띄어 쓴 전화번호 「010 1234 5678」 도 잡게)
    return mark_hits(doc, redact_find(doc, pattern, regex=regex, case=regex, pages=pages), fill)


REDACT_ALIGN = ("왼쪽", "가운데", "오른쪽")


def redact_style(doc: fitz.Document, xref: int) -> dict:
    """교정 표시의 채우기 색·오버레이 글·글자 크기(0 = 자동)·글자 색·맞춤."""
    def key(k):
        try:
            return doc.xref_get_key(xref, k)
        except Exception:  # noqa: BLE001
            return ("null", "null")
    fill = None
    t, v = key("IC")
    if t == "array":
        nums = [float(x) for x in v.strip("[] ").split()]
        fill = tuple(nums[:3]) if len(nums) >= 3 else None
    t, v = key("OverlayText")
    text = _pdf_text_value(t, v)
    t, v = key("DA")
    size, color = 0.0, (0.0, 0.0, 0.0)
    if t == "string":
        m = re.search(r"(-?[\d.]+)\s+Tf", v)
        size = float(m.group(1)) if m else 0.0
        m = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", v)
        color = tuple(float(x) for x in m.groups()) if m else color
    t, v = key("Q")
    align = int(v) if t == "int" else 0
    return {"fill": fill, "text": text, "fontsize": size, "text_color": color, "align": align}


def _pdf_text_value(t: str, v: str) -> str:
    if t == "string":
        return v
    if t == "unknown" or v.startswith("<"):
        try:
            raw = bytes.fromhex(v.strip("<>"))
            return raw[2:].decode("utf-16-be") if raw[:2] == b"\xfe\xff" else raw.decode("latin-1")
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _pdf_string(s: str) -> str:
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def set_redact_style(doc: fitz.Document, page: int, xref: int, fill=None, text: str | None = None,
                     fontsize: float | None = None, text_color=None, align: int | None = None) -> None:
    """교정 표시의 속성을 바꾼다(None 은 그대로). 적용할 때 apply_redactions 가 이 값으로 글을 얹는다."""
    cur = redact_style(doc, xref)
    if fill is not None:
        c = _color(fill) or (0, 0, 0)
        doc.xref_set_key(xref, "IC", "[%g %g %g]" % c)
    if text is not None:
        doc.xref_set_key(xref, "OverlayText", _pdf_string(text) if text else "null")
    size = cur["fontsize"] if fontsize is None else float(fontsize)
    col = cur["text_color"] if text_color is None else (_color(text_color) or (0, 0, 0))
    doc.xref_set_key(xref, "DA", "(%g %g %g rg /Helv %g Tf)" % (*col, size))
    if align is not None:
        doc.xref_set_key(xref, "Q", str(max(0, min(2, int(align)))))
    try:
        a = doc[page].load_annot(xref)
        if a is not None:
            a.update()
    except Exception:  # noqa: BLE001
        pass


def _overlay(page: fitz.Page, r: fitz.Rect, text: str, size: float, color, align: int) -> None:
    """교정한 자리에 글을 얹는다. 크기 0(자동)이면 칸에 맞는 가장 큰 크기."""
    font = korean_font_path()
    sizes = [size] if size > 0 else [max(4.0, min(r.height * 0.75, 72.0) - k) for k in range(0, 72)]
    for s in sizes:
        if s < 4:
            break
        rc = page.insert_textbox(r, text, fontname="malgunrd", fontfile=font, fontsize=s, color=color, align=align)
        if rc >= 0:
            return
    page.insert_textbox(r, text, fontname="malgunrd", fontfile=font, fontsize=4, color=color, align=align)


def apply_redactions(doc: fitz.Document, pages=None, images: int = fitz.PDF_REDACT_IMAGE_PIXELS,
                     metadata: bool = False) -> int:
    """붙여 둔 가리기 주석을 실제로 적용. in place. 오버레이 글은 한글 글꼴로 다시 얹는다
    (MuPDF 에 맡기면 한글 오버레이가 깨진다). metadata 면 문서 정보·XMP 도 지운다. 적용한 교정 표시 수."""
    n = 0
    for pno in _pages(doc, pages):
        page = doc[pno]
        over = []
        for a in page.annots(types=[fitz.PDF_ANNOT_REDACT]):
            n += 1
            st = redact_style(doc, a.xref)
            if st["text"]:
                over.append((fitz.Rect(a.rect), st))
                doc.xref_set_key(a.xref, "OverlayText", "null")
        if not over and not page.first_annot:
            continue
        page.apply_redactions(images=images)
        for r, st in over:
            _overlay(page, r, st["text"], st["fontsize"], st["text_color"], st["align"])
    if metadata:
        clear_metadata(doc)
    return n


def clear_metadata(doc: fitz.Document) -> None:
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:  # noqa: BLE001
        pass


HIDDEN_KEYS = ("metadata", "attachments", "bookmarks", "annotations", "form_fields", "hidden_text", "hidden_layers",
               "search_index", "cropped", "links_js")


def remove_hidden_data(doc: fitz.Document, metadata: bool = True, attachments: bool = True,
                       javascript: bool | None = None, annotations: bool = False, bookmarks: bool = False,
                       form_fields: bool = False, hidden_text: bool = False, hidden_layers: bool = False,
                       search_index: bool = False, cropped: bool = False, links_js: bool | None = None,
                       progress: Progress = None) -> list[str]:
    """숨은 정보를 걷어낸다. in place. 지운 것 열쇠 목록. javascript 는 옛 이름(links_js 와 같음)."""
    if links_js is None:
        links_js = True if javascript is None else javascript
    # MuPDF scrub 을 쓰는 것을 먼저(주석·칸을 지운 뒤 scrub 하면 끊긴 참조를 만나 멈춘다)
    steps = [(k, on) for k, on in (("metadata", metadata), ("attachments", attachments), ("hidden_text", hidden_text),
                                   ("links_js", links_js), ("search_index", search_index), ("hidden_layers", hidden_layers),
                                   ("cropped", cropped), ("bookmarks", bookmarks), ("form_fields", form_fields),
                                   ("annotations", annotations)) if on]
    done = []
    for i, (k, _) in enumerate(steps):
        _say(progress, i, len(steps), "숨겨진 데이터 삭제...")
        _HIDDEN[k](doc)
        done.append(k)
    _say(progress, len(steps), len(steps), "숨겨진 데이터 삭제...")
    return done


def _scrub(doc: fitz.Document, **on) -> None:
    kw = dict(attached_files=False, clean_pages=False, embedded_files=False, hidden_text=False, javascript=False,
              metadata=False, redactions=False, remove_links=False, reset_fields=False, reset_responses=False,
              thumbnails=False, xml_metadata=False)
    kw.update(on)
    doc.scrub(**kw)


def _rm_annotations(doc: fitz.Document) -> None:
    for page in doc:
        for a in list(page.annots()):
            page.delete_annot(a)


def _rm_fields(doc: fitz.Document) -> None:
    for page in doc:
        for w in list(page.widgets()):
            page.delete_widget(w)
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "AcroForm", "null")


def _rm_search_index(doc: fitz.Document) -> None:
    cat = doc.pdf_catalog()
    for k in ("PieceInfo", "SearchIndex"):
        doc.xref_set_key(cat, k, "null")
    for page in doc:
        doc.xref_set_key(page.xref, "PieceInfo", "null")


def _rm_cropped(doc: fitz.Document) -> None:
    """자르기 상자 밖 내용을 지운다(상자는 그대로)."""
    for page in doc:
        c, m, rot = fitz.Rect(page.cropbox), fitz.Rect(page.mediabox), page.rotation
        if abs(c.x0 - m.x0) < 0.5 and abs(c.y0 - m.y0) < 0.5 and abs(c.x1 - m.x1) < 0.5 and abs(c.y1 - m.y1) < 0.5:
            continue
        page.set_rotation(0); page.set_cropbox(m)
        bands = [fitz.Rect(m.x0, m.y0, m.x1, c.y0), fitz.Rect(m.x0, c.y1, m.x1, m.y1),
                 fitz.Rect(m.x0, c.y0, c.x0, c.y1), fitz.Rect(c.x1, c.y0, m.x1, c.y1)]
        from .document import redact_only
        redact_only(page, [b for b in bands if b.width > 0.1 and b.height > 0.1], fill=None,
                    images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
        page.set_cropbox(c); page.set_rotation(rot)


def _rm_links_js(doc: fitz.Document) -> None:
    for page in doc:
        for ln in list(page.get_links()):
            page.delete_link(ln)
        doc.xref_set_key(page.xref, "AA", "null")
    cat = doc.pdf_catalog()
    for k in ("OpenAction", "AA"):
        doc.xref_set_key(cat, k, "null")
    _scrub(doc, javascript=True)


def _rm_hidden_layers(doc: fitz.Document) -> None:
    """꺼진 레이어의 내용을 지우고 레이어(OCG)를 없앤다."""
    try:
        off = set((doc.get_layer() or {}).get("off") or [])
    except Exception:  # noqa: BLE001
        off = set()
    if off:
        for page in doc:
            _strip_page_oc(doc, page, off)
            for a in list(page.annots()):
                if _oc_hidden(doc, a.xref, off):
                    page.delete_annot(a)
    doc.xref_set_key(doc.pdf_catalog(), "OCProperties", "null")


def _oc_hidden(doc: fitz.Document, xref: int, off: set) -> bool:
    try:
        t, v = doc.xref_get_key(xref, "OC")
    except Exception:  # noqa: BLE001
        return False
    if t != "xref":
        return False
    return _ref_hidden(doc, int(v.split()[0]), off)


def _ref_hidden(doc: fitz.Document, ref: int, off: set) -> bool:
    if ref in off:
        return True
    try:   # 여러 레이어를 묶은 OCMD: 든 레이어가 다 꺼졌으면 숨은 것
        t, v = doc.xref_get_key(ref, "OCGs")
    except Exception:  # noqa: BLE001
        return False
    refs = [int(x) for x in re.findall(r"(\d+)\s+0\s+R", v)] if t in ("array", "xref") else []
    if t == "xref":
        refs = [int(v.split()[0])]
    return bool(refs) and all(r in off for r in refs)


def _res_names(doc: fitz.Document, page: fitz.Page, sub: str) -> dict[str, int]:
    """쪽 자원의 Properties·XObject 이름 → xref."""
    try:
        t, v = doc.xref_get_key(page.xref, "Resources/" + sub)
        if t == "xref":
            v = doc.xref_object(int(v.split()[0]), compressed=True)
        elif t != "dict":
            return {}
    except Exception:  # noqa: BLE001
        return {}
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"/([^\s/<>\[\]()]+)\s*(\d+)\s+0\s+R", v)}


_TOK = re.compile(rb"\s+|%[^\r\n]*|/[^\s/<>\[\]()%{}]*|<<|>>|<[0-9A-Fa-f\s]*>|\[|\]|\(|[^\s/<>\[\]()%{}]+")


def _content_ops(data: bytes):
    """내용 흐름을 (연산자, 피연산자 [(시작, 끝, 글자)], 연산 끝) 으로 쪼갠다. 글자열·인라인 그림을 건너뛴다."""
    i, n, ops = 0, len(data), []
    while i < n:
        m = _TOK.match(data, i)
        if not m:
            i += 1; continue
        tok = m.group()
        if tok[:1].isspace() or tok[:1] == b"%":
            i = m.end(); continue
        if tok == b"(":
            depth, j = 1, i + 1
            while j < n and depth:
                c = data[j:j + 1]
                if c == b"\\":
                    j += 2; continue
                depth += 1 if c == b"(" else -1 if c == b")" else 0
                j += 1
            ops.append((None, i, j, data[i:j])); i = j; continue
        if tok in (b"<<", b">>", b"[", b"]") or tok[:1] in (b"/", b"<") or re.fullmatch(rb"[-+.\d]+|true|false|null", tok):
            ops.append((None, i, m.end(), tok)); i = m.end(); continue
        if tok == b"BI":   # 인라인 그림: ID 뒤 날 바이트 … EI
            j = data.find(b"ID", m.end())
            k = j + 3 if j >= 0 else n
            e = re.compile(rb"\sEI(?=\s|$)").search(data, k)
            end = e.end() if e else n
            ops.append((b"BI", i, end, data[i:end])); i = end; continue
        ops.append((tok, i, m.end(), tok)); i = m.end()
    return ops


def _strip_page_oc(doc: fitz.Document, page: fitz.Page, off: set) -> None:
    props = {k: v for k, v in _res_names(doc, page, "Properties").items() if _ref_hidden(doc, v, off)}
    xobjs = {k for k, v in _res_names(doc, page, "XObject").items() if _oc_hidden(doc, v, off)}
    if not props and not xobjs:
        return
    page.clean_contents(sanitize=False)
    xs = page.get_contents()
    if not xs:
        return
    data = doc.xref_stream(xs[0])
    cuts, stack, operands = [], [], []
    for op, s, e, tok in _content_ops(data):
        if op is None:
            operands.append((s, tok)); continue
        start = operands[0][0] if operands else s
        if op in (b"BDC", b"BMC"):
            hide = op == b"BDC" and len(operands) >= 2 and operands[0][1] == b"/OC" and operands[1][1][1:].decode("latin-1") in props
            stack.append((start, hide))
        elif op == b"EMC" and stack:
            st, hide = stack.pop()
            if hide and not any(h for _, h in stack):
                cuts.append((st, e))
        elif op == b"Do" and operands and operands[-1][1][1:].decode("latin-1") in xobjs and not any(h for _, h in stack):
            cuts.append((start, e))
        operands = []
    if not cuts:
        return
    out, last = [], 0
    for s, e in sorted(cuts):
        if s >= last:
            out.append(data[last:s]); last = e
    out.append(data[last:])
    doc.update_stream(xs[0], b"\n".join(out))


def _rm_hidden_text(doc: fitz.Document) -> None:
    """보이지 않게 쓴 글자(글자 그리기 모드 3, OCR 글자층 등)를 지운다."""
    for page in doc:
        page.clean_contents(sanitize=False)
        xs = page.get_contents()
        if not xs:
            continue
        data = doc.xref_stream(xs[0])
        if b"Tr" not in data:
            continue
        cuts, operands, tr, stack = [], [], 0, []
        for op, s_, e, tok in _content_ops(data):
            if op is None:
                operands.append((s_, tok)); continue
            start = operands[0][0] if operands else s_
            if op == b"q":
                stack.append(tr)
            elif op == b"Q" and stack:
                tr = stack.pop()
            elif op == b"Tr" and operands:
                try:
                    tr = int(float(operands[-1][1]))
                except ValueError:
                    pass
            elif op in (b"Tj", b"TJ", b"'", b'"') and tr == 3:
                cuts.append((start, e))
            operands = []
        if cuts:
            out, last = [], 0
            for a, b in cuts:
                out.append(data[last:a]); last = b
            out.append(data[last:])
            doc.update_stream(xs[0], b" ".join(out))
    _scrub(doc, hidden_text=True)


_HIDDEN = {
    "metadata": lambda d: (clear_metadata(d), _scrub(d, metadata=True, xml_metadata=True, thumbnails=True)),
    "attachments": lambda d: _scrub(d, attached_files=True, embedded_files=True),
    "bookmarks": lambda d: d.set_toc([]),
    "annotations": _rm_annotations,
    "form_fields": _rm_fields,
    "hidden_text": lambda d: _rm_hidden_text(d),
    "hidden_layers": _rm_hidden_layers,
    "search_index": _rm_search_index,
    "cropped": _rm_cropped,
    "links_js": _rm_links_js,
}


def _words(page: fitz.Page) -> list[tuple]:
    return page.get_text("words", sort=True)


def _image_diff_rects(pa: fitz.Page | None, pb: fitz.Page | None, dpi: int = 50, cell: int = 16):
    """두 쪽을 그려 다른 칸(16px 격자)을 뭉쳐 포인트 네모로."""
    import numpy as np

    def render(p):
        pix = p.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()

    a = render(pa) if pa is not None else None
    b = render(pb) if pb is not None else None
    if a is None and b is None:
        return [], []
    h = max(x.shape[0] for x in (a, b) if x is not None)
    w = max(x.shape[1] for x in (a, b) if x is not None)

    def pad(x):
        out = np.full((h, w), 255, np.uint8)
        if x is not None:
            out[:x.shape[0], :x.shape[1]] = x
        return out

    a, b = pad(a), pad(b)
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)) > 40
    rows, cols = (h + cell - 1) // cell, (w + cell - 1) // cell
    grid = np.zeros((rows, cols), bool)
    for r in range(rows):
        for c in range(cols):
            if diff[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell].any():
                grid[r, c] = True
    scale = 72.0 / dpi
    rects = []
    seen = np.zeros_like(grid)
    for r in range(rows):
        for c in range(cols):
            if not grid[r, c] or seen[r, c]:
                continue
            stack = [(r, c)]
            seen[r, c] = True
            r0 = r1 = r
            c0 = c1 = c
            while stack:
                y, x = stack.pop()
                r0, r1, c0, c1 = min(r0, y), max(r1, y), min(c0, x), max(c1, x)
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < rows and 0 <= nx < cols and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            rects.append([c0 * cell * scale, r0 * cell * scale, (c1 + 1) * cell * scale, (r1 + 1) * cell * scale])
    return rects, [list(x) for x in rects]


def _page_key(page: fitz.Page):
    """쪽 짝 맞추기용 지문: 글자가 있으면 낱말 묶음, 없으면(스캔) 작은 회색 그림(평균을 뺀 값)."""
    import numpy as np

    words = [w[4] for w in page.get_text("words")]
    if words:
        return ("t", set(words))
    pix = page.get_pixmap(matrix=fitz.Matrix(96 / max(1, page.rect.width), 128 / max(1, page.rect.height)),
                          colorspace=fitz.csGRAY, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].astype(float)
    a = a[:128, :96]
    a = a - a.mean()
    return ("i", a)


def _page_sim(a, b) -> float:
    """0~1. 글자 쪽은 낱말 겹침, 그림 쪽은 상관계수(글자가 빽빽한 스캔 쪽끼리는 회색 평균이 다 비슷해 차이로는 못 가른다)."""
    import numpy as np

    if a[0] != b[0]:
        return 0.0
    if a[0] == "t":
        return len(a[1] & b[1]) / max(1, len(a[1] | b[1]))
    x, y = a[1], b[1]
    if x.shape != y.shape:
        h, w = min(x.shape[0], y.shape[0]), min(x.shape[1], y.shape[1])
        x, y = x[:h, :w], y[:h, :w]
    den = float(np.sqrt((x * x).sum() * (y * y).sum()))
    if den == 0:
        return 1.0 if not x.any() and not y.any() else 0.0  # 둘 다 빈 쪽이면 같다
    return max(0.0, float((x * y).sum()) / den)


_MATCH_MIN = {"t": 0.35, "i": 0.8}  # 이만큼 비슷해야 같은 쪽으로 본다(그림 쪽은 빽빽한 글자 쪽끼리도 꽤 닮는다)


def match_pages(doc_a: fitz.Document, doc_b: fitz.Document, progress: Progress = None) -> list[tuple[int | None, int | None]]:
    """두 문서의 쪽 짝(차례 유지). 끼우거나 뺀 쪽이 있어도 뒤쪽이 한 칸씩 밀려 전부 다르다고 나오지 않게.
    비슷한 정도(낱말 겹침·작은 그림)에서 문턱을 뺀 만큼을 점수로, 순서를 지키는 짝 맞추기(동적 계획법)를 한다.
    문턱을 빼야 「조금 닮은 짝 여럿」 이 「꼭 같은 짝 몇」 을 이기지 못한다. 짝 없는 쪽은 (i, None)·(None, j)."""
    na, nb = doc_a.page_count, doc_b.page_count
    ka, kb = [], []
    for i in range(na):
        _say(progress, i, na + nb, f"쪽 지문 뜨는 중 {i + 1}/{na}")
        ka.append(_page_key(doc_a[i]))
    for j in range(nb):
        _say(progress, na + j, na + nb, f"쪽 지문 뜨는 중 {j + 1}/{nb}")
        kb.append(_page_key(doc_b[j]))
    band = 40 + abs(na - nb)  # 아주 긴 문서는 대각선 둘레만
    score = [[0.0] * (nb + 1) for _ in range(na + 1)]
    move = [[0] * (nb + 1) for _ in range(na + 1)]  # 1 짝 · 2 A만 · 3 B만
    for i in range(na + 1):
        for j in range(nb + 1):
            if i == 0 and j == 0:
                continue
            best, mv = -1.0, 0
            if i and j and abs(i - j) <= band:
                gain = _page_sim(ka[i - 1], kb[j - 1]) - _MATCH_MIN[ka[i - 1][0]]
                if gain >= 0 and score[i - 1][j - 1] + gain + 1e-9 > best:
                    best, mv = score[i - 1][j - 1] + gain + 1e-9, 1
            if i and score[i - 1][j] > best:
                best, mv = score[i - 1][j], 2
            if j and score[i][j - 1] > best:
                best, mv = score[i][j - 1], 3
            score[i][j], move[i][j] = best, mv
    pairs = []
    i, j = na, nb
    while i or j:
        mv = move[i][j]
        if mv == 1:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif mv == 2:
            pairs.append((i - 1, None)); i -= 1
        else:
            pairs.append((None, j - 1)); j -= 1
    # 같은 틈(앞뒤 짝 사이)에 양쪽 다 짝 없는 쪽이 남으면 「뺀 쪽+끼운 쪽」 이 아니라 「많이 바뀐 쪽」 으로 차례대로 짝짓는다
    out, gap_a, gap_b = [], [], []

    def flush():
        n = min(len(gap_a), len(gap_b))
        out.extend(zip(gap_a[:n], gap_b[:n]))
        out.extend((x, None) for x in gap_a[n:])
        out.extend((None, y) for y in gap_b[n:])
        gap_a.clear(); gap_b.clear()
    for a, b in pairs[::-1]:
        if a is not None and b is not None:
            flush(); out.append((a, b))
        elif a is not None:
            gap_a.append(a)
        else:
            gap_b.append(b)
    flush()
    return out


def compare(doc_a: fitz.Document, doc_b: fitz.Document, progress: Progress = None) -> list[dict]:
    """쪽마다 글자 차이(difflib)와 달라진 자리(네모)를 돌려준다. 쪽 수가 달라도 되고, 끼우거나 뺀 쪽은 짝을 맞춰
    한쪽에만 있는 쪽(only_in)으로 낸다. page_a·page_b 는 각 문서의 쪽(없으면 None)."""
    pairs = match_pages(doc_a, doc_b, progress)
    n = len(pairs)
    out = []
    for k, (ia, ib) in enumerate(pairs):
        _say(progress, k, n, f"비교하는 중 {k + 1}/{n}")
        pa = doc_a[ia] if ia is not None else None
        pb = doc_b[ib] if ib is not None else None
        wa = _words(pa) if pa is not None else []
        wb = _words(pb) if pb is not None else []
        ta = [w[4] for w in wa]
        tb = [w[4] for w in wb]
        text_diff = []
        rects_a: list[list[float]] = []
        rects_b: list[list[float]] = []
        if ta or tb:
            sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                text_diff.append((tag, " ".join(ta[i1:i2]), " ".join(tb[j1:j2])))
                rects_a.extend(list(w[:4]) for w in wa[i1:i2])
                rects_b.extend(list(w[:4]) for w in wb[j1:j2])
        else:
            rects_a, rects_b = _image_diff_rects(pa, pb)
        out.append({"page": ia if ia is not None else ib, "page_a": ia, "page_b": ib, "text_diff": text_diff,
                    "changed_rects_a": rects_a, "changed_rects_b": rects_b,
                    "only_in": "a" if pb is None else "b" if pa is None else None})
    _say(progress, n, n, "비교 끝")
    return out


# ── 속성 ────────────────────────────────────────────────────────────────
def pdf_version(doc: fitz.Document) -> str:
    if hasattr(doc, "pdf_version"):
        try:
            return str(doc.pdf_version())
        except Exception:
            pass
    fmt = (doc.metadata or {}).get("format", "")
    m = re.search(r"(\d+\.\d+)", fmt or "")
    if m:
        return m.group(1)
    try:
        head = doc.tobytes(encryption=fitz.PDF_ENCRYPT_KEEP)[:12].decode("latin-1")
        m = re.search(r"%PDF-(\d+\.\d+)", head)
        return m.group(1) if m else ""
    except Exception:
        return ""


def properties(doc: fitz.Document) -> dict:
    """메타데이터 + 쪽 수 + 쪽 크기(mm) + 암호 여부 + PDF 버전 + 파일 크기."""
    meta = dict(doc.metadata or {})
    size_mm = None
    if doc.page_count:
        r = doc[0].rect
        size_mm = (round(r.width / MM, 1), round(r.height / MM, 1))
    name = getattr(doc, "name", "") or ""
    fsize = os.path.getsize(name) if name and os.path.exists(name) else None
    return {
        **meta,
        "page_count": doc.page_count,
        "file_size": fsize,
        "file_path": name,
        "page_size_mm": size_mm,
        "encrypted": bool((doc.metadata or {}).get("encryption")),  # is_encrypted 는 푼 뒤·권한 암호만이면 False
        "pdf_version": pdf_version(doc),
        "has_form": bool(doc.is_form_pdf),
        "has_toc": bool(doc.get_toc(simple=True)),
    }


_META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate", "trapped")


def set_properties(doc: fitz.Document, **metadata) -> None:
    """메타데이터 바꾸기(title author subject keywords creator producer creationDate modDate). in place."""
    cur = dict(doc.metadata or {})
    cur.pop("format", None)
    cur.pop("encryption", None)
    for k, v in metadata.items():
        if k not in _META_KEYS:
            raise EngineError(f"모르는 속성입니다: {k}")
        cur[k] = "" if v is None else str(v)
    doc.set_metadata(cur)


# ── 양식 ────────────────────────────────────────────────────────────────
def form_fields(doc: fitz.Document) -> list[dict]:
    out = []
    for pno, page in enumerate(doc):
        for w in page.widgets():
            out.append({
                "name": w.field_name, "type": w.field_type_string, "type_id": w.field_type,
                "value": w.field_value, "page": pno, "rect": list(w.rect),
                "choices": list(w.choice_values or []) if w.field_type in (
                    fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX) else None,
                "xref": w.xref,
            })
    return out


def _form_values(fields: list[dict]) -> dict[str, object]:
    """칸 이름 → 값. 라디오 묶음은 동그라미마다 줄이 있으니 켜진 것의 켜짐 이름(없으면 Off) 하나로."""
    data: dict[str, object] = {}
    for fd in fields:
        if fd["type_id"] == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            if fd["value"] not in (None, "", "Off", False) or fd["name"] not in data:
                data[fd["name"]] = fd["value"] if fd["value"] not in (None, "", False) else "Off"
        else:
            data[fd["name"]] = fd["value"]
    return data


def export_form_data(doc: fitz.Document, out_path: str, pdf_name: str | None = None) -> str:
    """양식 값을 FDF(.fdf)·XFDF(.xfdf)·CSV(.csv)·JSON(그 밖) 으로. pdf_name: FDF 에 적을 원본 PDF 이름."""
    fields = form_fields(doc)
    data = _form_values(fields)
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".fdf", ".xfdf"):
        kinds = {fd["name"]: fd["type_id"] for fd in fields}
        if pdf_name is None:   # 바이트로 연 문서는 이름이 「pdf」 다
            pdf_name = os.path.basename(getattr(doc, "name", "") or "")
            pdf_name = pdf_name if pdf_name.lower().endswith(".pdf") else ""
        text = (_fdf_text if ext == ".fdf" else _xfdf_text)(data, kinds, pdf_name)
        with open(out_path, "wb") as f:
            f.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        return out_path
    if ext == ".csv":
        first = {}
        for fd in fields:
            first.setdefault(fd["name"], fd)
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(["name", "type", "value", "page"])
            for name, v in data.items():
                wr.writerow([name, first[name]["type"], v, first[name]["page"] + 1])
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"fields": data, "detail": fields}, f, ensure_ascii=False, indent=2)
    return out_path


_ON = ("1", "true", "yes", "on", "예")


def import_form_data(doc: fitz.Document, path: str) -> int:
    """FDF · XFDF · JSON({name: value} 또는 {"fields": {...}}) · CSV(name,value 열) 를 읽어 채운다. 채운 칸 이름 수. in place."""
    values: dict[str, object] = {}
    ext = os.path.splitext(path)[1].lower()
    if ext in (".fdf", ".xfdf"):
        with open(path, "rb") as f:
            raw = f.read()
        try:
            values = _read_fdf(raw) if ext == ".fdf" else _read_xfdf(raw)
        except Exception as e:  # noqa: BLE001
            raise EngineError(f"양식 자료 파일을 읽지 못했습니다: {os.path.basename(path)}") from e
        return _fill_values(doc, values)
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            for row in rd:
                if row.get("name") is not None:
                    values[row["name"]] = row.get("value", "")
    else:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("fields"), dict):
            data = data["fields"]
        if not isinstance(data, dict):
            raise EngineError("양식 자료 파일 모양이 맞지 않습니다")
        values = data
    return _fill_values(doc, values)


def _fill_values(doc: fitz.Document, values: dict[str, object]) -> int:
    done: set[str] = set()
    for page in doc:
        radios = []
        for w in page.widgets():
            if w.field_name not in values:
                continue
            v = values[w.field_name]
            if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                radios.append(w)  # 끄기를 먼저 하고 켜기를 나중에(켤 때 같은 묶음 나머지를 끈다)
                continue
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                on = (w.on_state() or "").lower()
                w.field_value = bool(v) if not isinstance(v, str) else v.strip().lower() in _ON + (on,)
            elif isinstance(v, (list, tuple)) and w.field_type == fitz.PDF_WIDGET_TYPE_LISTBOX:
                w.field_value = [str(x) for x in v]
            else:
                w.field_value = "" if v is None else (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v))
            save_widget(w)
            done.add(w.field_name)
        radios.sort(key=lambda w: str(values[w.field_name]) == (w.on_state() or ""))
        for w in radios:
            w.field_value = str(values[w.field_name]) == (w.on_state() or "")
            save_widget(w)
            done.add(w.field_name)
    return len(done)


# FDF·XFDF: 다른 PDF 프로그램(다른 PDF 편집기·아크로뱃)과 양식 값을 주고받는 표준 꼴
def _fdf_value(v, kind: int) -> str:
    if kind in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
        name = "Off" if v in (None, "", False, "Off") else ("Yes" if v is True else str(v))
        return "/" + re.sub(r"[^!-~]|[()<>\[\]{}/%#]", lambda m: "#%02X" % ord(m.group()) if ord(m.group()) < 256 else "", name)
    if isinstance(v, (list, tuple)):
        return "[" + " ".join(fitz.get_pdf_str(str(x)) for x in v) + "]"
    return fitz.get_pdf_str("" if v is None else str(v))


def _fdf_text(data: dict, kinds: dict, pdf_name: str) -> bytes:
    rows = "\n".join(f"<</T {fitz.get_pdf_str(name)} /V {_fdf_value(v, kinds.get(name, 0))}>>" for name, v in data.items())
    body = (f"%FDF-1.2\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<</FDF <</Fields [\n{rows}\n]"
            + (f" /F {fitz.get_pdf_str(pdf_name)}" if pdf_name else "") + ">> >>\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF\n")
    return body.encode("latin-1", errors="replace")


def _xfdf_text(data: dict, kinds: dict, pdf_name: str) -> str:
    from xml.sax.saxutils import escape, quoteattr
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">']
    if pdf_name:
        out.append(f"<f href={quoteattr(pdf_name)}/>")
    out.append("<fields>")
    for name, v in data.items():
        if kinds.get(name) in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
            v = "Off" if v in (None, "", False, "Off") else ("Yes" if v is True else v)
        vals = v if isinstance(v, (list, tuple)) else [v]
        out.append(f"<field name={quoteattr(name)}>" + "".join(f"<value>{escape('' if x is None else str(x))}</value>" for x in vals) + "</field>")
    out += ["</fields>", "</xfdf>", ""]
    return "\n".join(out)


def _read_xfdf(raw: bytes) -> dict[str, object]:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)
    out: dict[str, object] = {}

    def walk(el, prefix: str):
        for f in el:
            if f.tag.rsplit("}", 1)[-1] != "field":
                continue
            name = f"{prefix}.{f.get('name')}" if prefix else (f.get("name") or "")
            vals = [v.text or "" for v in f if v.tag.rsplit("}", 1)[-1] in ("value", "value-richtext")]
            if vals:
                out[name] = vals[0] if len(vals) == 1 else vals
            walk(f, name)   # 점으로 이은 이름은 칸 안의 칸으로 들어 있다

    for fields in root.iter():
        if fields.tag.rsplit("}", 1)[-1] == "fields":
            walk(fields, "")
    return out


_PDF_TOKEN = re.compile(rb"\s*(<<|>>|\[|\]|\(|<[0-9A-Fa-f\s]*>|/[^\s/<>\[\]()%]*|[^\s/<>\[\]()%]+|%[^\r\n]*)")


def _pdf_parse(raw: bytes, pos: int = 0):
    """FDF 안의 PDF 값 하나를 읽는다 → (값, 다음 자리). 사전은 dict, 배열은 list, 이름은 '/이름', 글은 str."""
    while True:
        m = _PDF_TOKEN.match(raw, pos)
        if not m:
            raise ValueError("FDF 를 읽지 못했습니다")
        tok, pos = m.group(1), m.end()
        if not tok.startswith(b"%"):
            break
    if tok == b"<<":
        d = {}
        while True:
            m = re.compile(rb"\s*(>>|/[^\s/<>\[\]()%]*)").match(raw, pos)
            if m.group(1) == b">>":
                return d, m.end()
            key, pos = m.group(1)[1:].decode("latin-1"), m.end()
            d[key], pos = _pdf_parse(raw, pos)
    if tok == b"[":
        arr = []
        while True:
            m = re.compile(rb"\s*\]").match(raw, pos)
            if m:
                return arr, m.end()
            v, pos = _pdf_parse(raw, pos)
            arr.append(v)
    if tok == b"(":
        buf, depth, k = bytearray(), 1, pos
        esc = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}
        while k < len(raw):
            c = raw[k]
            if c == 0x5C:  # \
                n = raw[k + 1]
                if n in esc:
                    buf += esc[n]; k += 2
                elif 0x30 <= n <= 0x37:
                    oct_ = re.match(rb"[0-7]{1,3}", raw[k + 1:k + 4]).group()
                    buf.append(int(oct_, 8) & 255); k += 1 + len(oct_)
                elif n in (0x0D, 0x0A):
                    k += 2 + (n == 0x0D and raw[k + 2:k + 3] == b"\n")
                else:
                    buf.append(n); k += 2
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    return _pdf_text(bytes(buf)), k + 1
            buf.append(c); k += 1
        raise ValueError("글이 끝나지 않았습니다")
    if tok.startswith(b"<"):
        h = re.sub(rb"\s", b"", tok[1:-1])
        return _pdf_text(bytes.fromhex((h + b"0" * (len(h) % 2)).decode())), pos
    if tok.startswith(b"/"):
        return "/" + re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), tok[1:].decode("latin-1")), pos
    m = re.compile(rb"\s+(\d+)\s+R(?![^\s/<>\[\]()%])").match(raw, pos) if tok.isdigit() else None
    if m:   # 「12 0 R」: 따로 적힌 객체를 가리킨다
        return ("ref", int(tok)), m.end()
    return tok.decode("latin-1"), pos   # 숫자·true·false


def _pdf_text(b: bytes) -> str:
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", errors="replace")
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", errors="replace")
    return b.decode("latin-1")


def _read_fdf(raw: bytes) -> dict[str, object]:
    k = raw.find(b"/Fields")
    if k < 0:
        return {}
    fields, _ = _pdf_parse(raw, k + len(b"/Fields"))
    out: dict[str, object] = {}

    def deref(v, depth=0):
        if isinstance(v, tuple) and depth < 4:
            m = re.search(rb"(?<!\d)%d\s+0\s+obj" % v[1], raw)
            return deref(_pdf_parse(raw, m.end())[0], depth + 1) if m else None
        return v

    def walk(items, prefix: str):
        items = deref(items)
        for f in items if isinstance(items, list) else []:
            f = deref(f)
            if not isinstance(f, dict):
                continue
            f = {key: deref(val) for key, val in f.items()}
            name = f"{prefix}.{f.get('T', '')}" if prefix else str(f.get("T", ""))
            if "V" in f:
                v = f["V"]
                out[name] = v[1:] if isinstance(v, str) and v.startswith("/") else v
            walk(f.get("Kids"), name)

    walk(fields, "")
    return out


def _field_key(doc: fitz.Document, xref: int, key: str) -> tuple[str, str]:
    """칸의 열쇠 값. 없으면 부모(라디오 묶음 등)에서 물려받은 것."""
    for _ in range(8):
        t, v = doc.xref_get_key(xref, key)
        if t != "null":
            return t, v
        pt, pv = doc.xref_get_key(xref, "Parent")
        if pt != "xref":
            break
        xref = int(pv.split()[0])
    return "null", ""


def reset_form(doc: fitz.Document) -> None:
    """양식 값을 칸마다 저장된 기본값(DV)으로 되돌린다. 기본값이 없는 칸은 비운다. in place."""
    for page in doc:
        radios = []
        for w in page.widgets():
            t, dv = _field_key(doc, w.xref, "DV")
            if w.field_type in (fitz.PDF_WIDGET_TYPE_RADIOBUTTON, fitz.PDF_WIDGET_TYPE_CHECKBOX):
                on = t == "name" and dv == "/" + (w.on_state() or "")
                if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                    radios.append((on, w)); continue
                w.field_value = on
                save_widget(w)
            elif t == "string":
                w.field_value = dv   # xref_get_key 는 글을 풀어서 준다
                save_widget(w)
            elif t == "array" and w.field_type == fitz.PDF_WIDGET_TYPE_LISTBOX:
                w.field_value = [x for x in re.findall(r"\((.*?)\)", dv)]
                save_widget(w)
            else:
                reset_widget(w)
        for on, w in sorted(radios, key=lambda r: r[0]):   # 끌 것 먼저, 켤 것 나중
            w.field_value = on
            save_widget(w)


def set_form_defaults(doc: fitz.Document) -> int:
    """지금 값을 칸마다 기본값(DV)으로 적는다(「재설정」 이 이 값으로 돌아감). 적은 칸 수. in place."""
    n = 0
    seen: set[int] = set()
    for page in doc:
        for w in page.widgets():
            holder = w.xref
            pt, pv = doc.xref_get_key(w.xref, "Parent")
            if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON and pt == "xref":
                holder = int(pv.split()[0])   # 라디오는 묶음(부모)에 값이 있다
            if holder in seen:
                continue
            seen.add(holder)
            t, v = _field_key(doc, holder, "V")
            doc.xref_set_key(holder, "DV", fitz.get_pdf_str(v) if t == "string" else v if t != "null" else "null")
            n += 1
    return n


def align_widgets(doc: fitz.Document, items: Sequence[tuple[int, int]], how: str) -> int:
    """양식 칸 여럿을 맞춘다. items=(쪽, xref). 맞춘 칸 수. in place.

    how: left·right·top·bottom·hcenter·vcenter 는 고른 칸을 다 합친 사각형 기준,
         page_h·page_v·page_both 는 쪽 가운데, dist_h·dist_v 는 같은 간격으로 벌리기(셋 이상)."""
    held = {}
    ws = []
    for pg, xref in items:
        p = held.setdefault(pg, doc[pg])   # 쪽을 붙잡아 둔다(놓치면 update 가 「Annot is not bound to a page」)
        ws += [(pg, w) for w in p.widgets() if w.xref == xref]
    if not ws:
        return 0
    rects = [fitz.Rect(w.rect) for _, w in ws]
    u = fitz.Rect(rects[0])
    for r in rects[1:]:
        u |= r
    new = [fitz.Rect(r) for r in rects]
    if how in ("dist_h", "dist_v"):
        if len(ws) < 3:
            raise EngineError("같은 간격으로 벌리려면 칸을 셋 이상 고르세요")
        a = 0 if how == "dist_h" else 1
        order = sorted(range(len(rects)), key=lambda k: (rects[k].x0, rects[k].y0)[a])
        size = sum((rects[k].width, rects[k].height)[a] for k in order)
        span = (u.width, u.height)[a]
        gap = (span - size) / (len(order) - 1)
        pos = (u.x0, u.y0)[a]
        for k in order:
            r = new[k]
            if a == 0:
                r.x1, r.x0 = pos + r.width, pos
            else:
                r.y1, r.y0 = pos + r.height, pos
            pos += (r.width, r.height)[a] + gap
    for (pg, _), r in zip(ws, new):
        page_r = held[pg].rect
        w_, h_ = r.width, r.height
        if how == "left": r.x0 = u.x0
        elif how == "right": r.x0 = u.x1 - w_
        elif how == "top": r.y0 = u.y0
        elif how == "bottom": r.y0 = u.y1 - h_
        elif how == "hcenter": r.x0 = (u.x0 + u.x1 - w_) / 2
        elif how == "vcenter": r.y0 = (u.y0 + u.y1 - h_) / 2
        if how in ("page_h", "page_both"): r.x0 = (page_r.width - w_) / 2
        if how in ("page_v", "page_both"): r.y0 = (page_r.height - h_) / 2
        r.x1, r.y1 = r.x0 + w_, r.y0 + h_
    for (_, w), r in zip(ws, new):
        w.rect = r
        save_widget(w)
    return len(ws)


FORM_NONE = "검색된 양식 필드가 없습니다."


def form_row(doc: fitz.Document) -> dict[str, object]:
    """문서 하나의 양식 값 {칸 이름: 값}(라디오 묶음은 고른 값 하나). 칸이 없으면 빈 dict."""
    return _form_values(form_fields(doc))


def write_data_table(rows: Sequence[tuple[str, dict, str]], out_path: str) -> str:
    """[(파일 이름, {칸: 값}, 비고)] 를 표 한 장으로. 열 = 「파일」 · 칸 이름들(처음 나온 차례) · 「비고」.
    .csv 면 엑셀이 한글을 바로 읽는 UTF-8(BOM), 그 밖(.xlsx)이면 엑셀 파일."""
    names: list[str] = []
    for _, vals, _ in rows:
        for k in vals:
            if k not in names:
                names.append(k)
    head = ["파일", *names, "비고"]
    body = [[name, *[_cell(vals.get(k, "")) for k in names], note] for name, vals, note in rows]
    if out_path.lower().endswith(".csv"):
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(head)
            wr.writerows(body)
        return out_path
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "양식 자료"
    ws.append(head)
    for r in body:
        ws.append(r)
    wb.save(out_path)
    return out_path


def extract_data_doc(doc: fitz.Document, out_path: str, name: str) -> str:
    """지금 문서 하나의 양식 값을 CSV 한 장(머리 줄 + 값 한 줄)으로. 칸이 없으면 EngineError(FORM_NONE)."""
    vals = form_row(doc)
    if not vals:
        raise EngineError(FORM_NONE)
    return write_data_table([(name, vals, "")], out_path)


def extract_data(docs_paths: Sequence[str], out_path: str, progress: Progress = None,
                 passwords: Sequence[str | None] | None = None) -> str:
    """여러 PDF 의 양식 값을 표 한 장으로(PDF 하나 = 한 줄, 칸 이름 = 열). out_path 가 .csv 면 CSV, 그 밖은 .xlsx."""
    rows = []
    pws = list(passwords or [])
    n = len(docs_paths)
    for i, p in enumerate(docs_paths):
        _say(progress, i, n, f"양식 읽는 중 {os.path.basename(p)}")
        rows.append((os.path.basename(p), *_read_form(p, pws[i] if i < len(pws) else None)))
    write_data_table(rows, out_path)
    _say(progress, n, n, "저장 끝")
    return out_path


def _read_form(path: str, password: str | None = None) -> tuple[dict, str]:
    """(양식 값, 비고). 못 열면 ({}, 까닭)."""
    try:
        d = fitz.open(path)
    except Exception:  # noqa: BLE001
        return {}, "열지 못함"
    try:
        if d.needs_pass and not d.authenticate(password or ""):
            return {}, "암호가 걸린 파일"
        vals = form_row(d)
        return vals, "" if vals else FORM_NONE
    finally:
        d.close()


def _cell(v):
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return v


# ── 인쇄 ────────────────────────────────────────────────────────────────
def list_printers() -> list[str]:
    try:
        import win32print
    except ImportError as e:
        raise EngineError("pywin32 가 없어 프린터 목록을 못 읽습니다") from e
    try:
        return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    except Exception as e:
        raise EngineError(f"프린터 목록을 못 읽었습니다 ({e})") from e


def default_printer() -> str | None:
    try:
        import win32print
        return win32print.GetDefaultPrinter()
    except Exception:
        return None


def print_pdf(path: str, printer: str | None = None, pages: Iterable[int] | None = None,
              copies: int = 1) -> str:
    """윈도우 기본 PDF 처리기로 인쇄한다. pages 가 있으면 그 쪽만 임시 PDF 로 만들어 보낸다."""
    if not os.path.exists(path):
        raise EngineError(f"파일이 없습니다: {path}")
    send = path
    if pages is not None:
        import tempfile

        d = fitz.open(path)
        try:
            sel = _pages(d, pages)
            if not sel:
                raise EngineError("인쇄할 쪽이 없습니다")
            d.select(sel)
            fd, send = tempfile.mkstemp(prefix="pdfapp_print_", suffix=".pdf")
            os.close(fd)
            d.save(send, garbage=3)
        finally:
            d.close()
    try:
        import win32api
    except ImportError as e:
        raise EngineError("pywin32 가 없어 인쇄를 못 합니다") from e
    for _ in range(max(1, int(copies))):
        try:
            if printer:
                win32api.ShellExecute(0, "printto", send, f'"{printer}"', ".", 0)
            else:
                win32api.ShellExecute(0, "print", send, None, ".", 0)
        except Exception as e:
            if getattr(e, "winerror", None) == 31 or (e.args and e.args[0] == 31):
                # SE_ERR_NOASSOC: PDF 기본 앱에 「인쇄」 동작이 없다(다른 PDF 편집기·이 앱 둘 다). 윈도우 문구는 엉뚱하다
                raise EngineError(f"PDF 기본 앱에 인쇄 기능이 없어 보내지 못했습니다({APP_NAME}의 인쇄·일괄 인쇄를 쓰세요)") from e
            raise EngineError(f"인쇄를 보내지 못했습니다 ({e})") from e
    return send


# ── 일괄 처리 ───────────────────────────────────────────────────────────
CANCELLED = "취소됨"
# 결과 이름 뒤에 붙는 말(변환·인쇄·생성·데이터 내보내기는 붙이지 않음)
BATCH_SUFFIX = {"compress": "_압축", "watermark": "_워터마크", "background": "_배경", "header_footer": "_머리글",
                "page_number": "_페이지번호", "bates": "_베이츠", "encrypt": "_보안", "ocr": "_OCR", "blank": "_공백삭제"}
DECO_OPS = ("watermark", "background", "header_footer", "page_number", "bates")
# 옛 변환 이름(target) → 변환 창 형식 키
_OLD_TARGET = {"text": "txt", "word": "docx", "excel": "xlsx", "images": "png"}
_IMAGE_FMTS = ("jpg", "jpeg", "png", "bmp", "gif", "tiff")
_CONVERT_EXT = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "txt": ".txt", "html": ".html", "xml": ".xml",
                "rtf": ".rtf", "epub": ".epub", "pdfa": ".pdf", "imgpdf": ".pdf", "hwpx": ".hwpx"}
_CONVERT_OCR = ("docx", "xlsx", "pptx", "txt", "html", "rtf", "epub")


def _free(p: str, taken: set | None) -> bool:
    return not os.path.exists(p) and (taken is None or os.path.normcase(p) not in taken)


def _out_name(inp: str, out_dir: str, suffix: str = "", ext: str | None = None, taken: set | None = None) -> str:
    """out_dir 안 「원래이름+suffix+ext」, 있으면 「_1」 · 「_2」 로 비킨다. taken 은 이번 일괄에서 이미 쓰기로 한 경로
    (normcase 한 것)이고, 고른 경로를 거기 더한다. ext 가 "" 면 폴더 이름으로 본다(이미지 여러 장)."""
    stem = os.path.splitext(os.path.basename(inp))[0] + suffix
    if ext is None:
        ext = os.path.splitext(inp)[1]
    p = os.path.join(out_dir, stem + ext)
    n = 1
    while not _free(p, taken):
        p = os.path.join(out_dir, f"{stem}_{n}{ext}")
        n += 1
    if taken is not None:
        taken.add(os.path.normcase(p))
    return p


def _stopped(stop) -> bool:
    if stop is None:
        return False
    is_set = getattr(stop, "is_set", None)
    return bool(is_set() if is_set else stop())


def batch(jobs: Sequence[dict], progress: Progress = None, stop=None, on_row=None) -> list[dict]:
    """여러 일을 차례로. jobs = [{"op", "inputs", "out_dir", "options", "passwords"?}].

    out_dir 이 비면(None·"") 파일마다 자기 폴더(원본 파일 위치)에 저장한다. 결과 이름이 겹치면 「_1」 로 비킨다.
    stop: 멈춤 깃발(threading.Event 나 bool 을 돌려주는 함수). 서면 남은 파일은 「취소됨」.
    on_row(줄 번호, rec): 파일 하나를 시작할 때(rec None)와 끝날 때(rec) 부른다. 줄 번호는 모든 일에 걸쳐 0부터.

    op: convert(options.fmt = 변환 창 형식 키, 옛 target 도 받음, 그 밖은 형식 설정과 ocr·langs)
        compress(level · custom) · watermark/background/header_footer/page_number/bates(settings · range,
        옛 워터마크 kw 도 받음) · encrypt(user_pw/owner_pw/method/allow_*) · ocr(lang/dpi/force) · blank
        create(파일마다 PDF 로, options.name 을 주면 옛 동작대로 하나로 합침) · export_data(options.name 표 한 장)
        print(printer/copies)
    결과: 줄마다 {"input", "output", "ok", "error", "cancelled"}(create 합치기는 일 하나에 한 줄)
    """
    from . import convert as _cv
    from . import ocr as _ocr

    results: list[dict] = []
    total = sum(1 if (j.get("op") == "create" and "name" in (j.get("options") or {})) else len(j.get("inputs", []))
                for j in jobs) or 1
    row = 0
    taken: set[str] = set()

    def start(k: int):
        if on_row:
            on_row(k, None)

    def finish(k: int, rec: dict):
        results.append(rec)
        if on_row:
            on_row(k, rec)

    for job in jobs:
        op = job.get("op")
        inputs = list(job.get("inputs", []))
        pws = list(job.get("passwords") or [])
        fixed = job.get("out_dir") or ""
        opts = dict(job.get("options") or {})

        def folder(inp: str, fixed=fixed) -> str:
            d = fixed or os.path.dirname(os.path.abspath(inp)) or os.getcwd()
            os.makedirs(d, exist_ok=True)
            return d

        if op == "create" and "name" in opts:
            rec = {"input": inputs, "output": "", "ok": False, "error": "", "cancelled": False}
            start(row)
            if _stopped(stop):
                rec.update(error=CANCELLED, cancelled=True)
            else:
                out = os.path.join(folder(inputs[0]) if inputs else (fixed or os.getcwd()), opts["name"])
                rec["output"] = out
                try:
                    _cv.from_files(inputs, out)
                    rec["ok"] = True
                except Exception as e:  # noqa: BLE001
                    rec["error"] = str(e)
            finish(row, rec)
            row += 1
            _say(progress, row, total, opts["name"])
            continue
        if op == "export_data":
            row = _export_rows(inputs, pws, opts, folder, taken, stop, start, finish, row, progress, total)
            continue
        for i, inp in enumerate(inputs):
            _say(progress, row, total, os.path.basename(inp))
            start(row)
            rec = {"input": inp, "output": "", "ok": False, "error": "", "cancelled": False}
            if _stopped(stop):
                rec.update(error=CANCELLED, cancelled=True)
            else:
                try:
                    rec["output"] = _batch_one(op, inp, folder(inp), dict(opts), _cv, _ocr,
                                               password=pws[i] if i < len(pws) else None, taken=taken)
                    rec["ok"] = True
                except Exception as e:  # noqa: BLE001
                    rec["error"] = str(e) or "실패"
            finish(row, rec)
            row += 1
    _say(progress, total, total, "일괄 처리 끝")
    return results


def _export_rows(inputs, pws, opts, folder, taken, stop, start, finish, row, progress, total) -> int:
    """데이터 내보내기: 파일마다 양식 값을 읽어 줄 상태를 남기고, 끝에 표 한 장(CSV)을 첫 파일 폴더(또는 지정 폴더)에."""
    name = opts.get("name") or "양식 데이터.csv"
    stem, ext = os.path.splitext(name)
    ext = ext or ".csv"
    table, recs = [], []
    for i, inp in enumerate(inputs):
        _say(progress, row, total, os.path.basename(inp))
        start(row)
        rec = {"input": inp, "output": "", "ok": False, "error": "", "cancelled": False}
        if _stopped(stop):
            rec.update(error=CANCELLED, cancelled=True)
        else:
            vals, note = _read_form(inp, pws[i] if i < len(pws) else None)
            table.append((os.path.basename(inp), vals, note))
            rec["ok"], rec["error"] = bool(vals), note
        recs.append((row, rec))
        row += 1
    out = ""
    if table:
        out = _out_name(stem + ext, folder(inputs[0]), "", ext, taken)
        write_data_table(table, out)
    for k, rec in recs:
        if not rec["cancelled"] and out:
            rec["output"] = out
        finish(k, rec)
    return row


def _save_keep(doc: fitz.Document, out: str) -> str:
    """결과에도 원본의 암호·권한을 그대로 둔다(encryption=KEEP)."""
    _subset(doc)
    doc.save(out, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    return out


def _convert(doc: fitz.Document, inp: str, out_dir: str, opts: dict, _cv, _ocr, taken: set | None) -> str:
    """변환 창과 같은 형식 키·설정으로 파일 하나를 바꾼다. 쪽 범위는 없음(모든 쪽)."""
    target = opts.get("target", "")
    fmt = opts.get("fmt") if not target else _OLD_TARGET.get(target, target)
    if target == "images" and opts.get("fmt"):
        fmt = opts["fmt"]   # 옛 꼴: target=images, fmt=jpg
    fmt = fmt or "txt"
    stem = os.path.splitext(os.path.basename(inp))[0]
    if fmt in _IMAGE_FMTS:
        mode = opts.get("mode", "pages")
        dpi = opts.get("dpi") or None
        if mode == "merge":
            out = _out_name(inp, out_dir, "", "." + fmt, taken)
            files = _cv.to_images(doc, out_dir, fmt=fmt, dpi=dpi, merge=True,
                                  stem=os.path.splitext(os.path.basename(out))[0], zoom=opts.get("zoom", 100),
                                  frame_w=opts.get("frame_w", 1), frame_color=opts.get("frame_color", "#FFFFFF"))
            return files[0] if files else out
        sub = _out_name(inp, out_dir, "", "", taken)   # 쪽마다 한 장은 이름 폴더 하나에
        os.makedirs(sub, exist_ok=True)
        if mode == "extract":
            _cv.extract_images(doc, sub, stem=stem)
        else:
            _cv.to_images(doc, sub, fmt=fmt, dpi=dpi, stem=stem)
        return sub
    if fmt not in _CONVERT_EXT:
        raise EngineError(f"모르는 변환 종류입니다: {fmt}")
    ocr_mode = opts.get("ocr", "off")
    if ocr_mode in ("all", "scan") and fmt in _CONVERT_OCR:
        try:   # 인식이 안 되는 PC 면 그냥 넘어간다(변환 창과 같음)
            _ocr.ocr_pages(doc, lang=opts.get("langs") or ["ko"], dpi=300, force=ocr_mode == "all")
        except Exception:  # noqa: BLE001
            pass
    suffix = "_pdfa" if fmt == "pdfa" else "_이미지" if fmt == "imgpdf" else ""
    out = _out_name(inp, out_dir, suffix, _CONVERT_EXT[fmt], taken)
    if fmt == "docx":
        return _cv.to_word(doc, out)
    if fmt == "xlsx":
        return _cv.to_excel(doc, out, one_sheet=bool(opts.get("one_sheet")))
    if fmt == "pptx":
        return _cv.to_pptx(doc, out, dpi=int(opts.get("pptx_dpi", 150)))
    if fmt == "txt":
        return _cv.to_text(doc, out)
    if fmt == "html":
        return _cv.to_html(doc, out)
    if fmt == "xml":
        return _cv.to_xml(doc, out)
    if fmt == "rtf":
        return _cv.to_rtf(doc, out)
    if fmt == "epub":
        return _cv.to_epub(doc, out, cover=opts.get("cover", ""))
    if fmt == "pdfa":
        return _cv.to_pdfa(doc, out)
    if fmt == "imgpdf":
        return _cv.to_image_pdf(doc, out)
    return _cv.to_hwpx(doc, out)


def _deco(doc: fitz.Document, kind: str, opts: dict, stem: str) -> None:
    """꾸미기 템플릿(settings · range)을 넣는다. 옛 꼴(워터마크 kw 를 바로)도 받는다."""
    from .deco import add_decoration
    if "settings" not in opts:
        if kind != "watermark":
            raise EngineError("넣을 템플릿을 고르세요")
        from .deco_mark import watermark
        watermark(doc, **opts)
        return
    from .deco_store import range_pages
    try:
        pages = range_pages(opts.get("range"), doc.page_count)
    except ValueError as e:
        raise EngineError(str(e)) from e
    add_decoration(doc, kind, pages=pages, file_name=stem, **dict(opts["settings"]))


def _batch_one(op: str, inp: str, out_dir: str, opts: dict, _cv, _ocr, password: str | None = None,
               taken: set | None = None) -> str:
    """파일 하나. 결과 경로(이미지 쪽마다는 폴더)."""
    if op == "print":
        return print_pdf(inp, printer=opts.get("printer"), pages=opts.get("pages"), copies=opts.get("copies", 1))
    if op == "create":
        out = _out_name(inp, out_dir, "", ".pdf", taken)
        return _cv.from_files([inp], out)
    doc = fitz.open(inp)
    pw = opts.pop("password", None) or password or ""
    doc._pdfapp_pw = pw
    try:
        if doc.needs_pass and not doc.authenticate(pw):
            raise EngineError("암호가 걸린 파일입니다")
        if op == "convert":
            return _convert(doc, inp, out_dir, opts, _cv, _ocr, taken)
        if op not in BATCH_SUFFIX:
            raise EngineError(f"모르는 일괄 작업입니다: {op}")
        out = _out_name(inp, out_dir, BATCH_SUFFIX[op], ".pdf", taken)
        if op == "compress":
            compress(doc, out, level=opts.get("level", "high"), password=pw or None, custom=opts.get("custom"))
            return out
        if op in DECO_OPS:
            _deco(doc, op, opts, os.path.splitext(os.path.basename(inp))[0])
            return _save_keep(doc, out)
        if op == "encrypt":
            return encrypt(doc, out, **opts)
        if op == "ocr":
            _ocr.ocr_pages(doc, **opts)
            return _save_keep(doc, out)
        # blank
        if not delete_blank_pages(doc):
            raise EngineError("공백 페이지가 없습니다")
        return _save_keep(doc, out)
    finally:
        doc.close()
