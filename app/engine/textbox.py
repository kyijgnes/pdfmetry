"""글 객체 엔진: 서식 묶음 → insert_htmlbox 로 그린 Form XObject.

편집 글(쪽 내용)과 글 주석의 외관이 같이 쓴다. 글은 언제나 **임시 문서**에 그리고 거기서 글꼴을 줄인 뒤
Form 만 옮겨 온다(graft). 열린 문서에서 `subset_fonts()` 를 부르면 MuPDF 가 글꼴 파일 단위로 기억해 둔
자원이 줄인 글꼴을 가리키게 되어, 그 뒤 `insert_font` 로 같은 파일을 넣는 꾸미기·스탬프·교정 글자가
빈 네모로 나온다(이름을 바꿔도 같은 자원을 다시 씀, 11-05 실험). 임시 문서에서 줄이면 열린 문서의 기억은
건드리지 않고, 실행 취소 사본에도 줄인 글꼴만 실린다.

쪽에 놓는 글 객체 줄(설계 4.1):
    /RPDFObj <</Id n /K /T>> BDC q a b c d e f cm /이름 Do Q EMC
이름이 가리키는 Form 사전에 `/RPDFText`(JSON 서식 묶음)를 적어 다시 고칠 때 읽는다.
"""
from __future__ import annotations

import json
import re

import fitz
import pymupdf

from . import fonts as F

Color = tuple[float, float, float]

TEXT_PAD = 2          # 글 상자 여유(화면 글 상자와 줄바꿈이 어긋나지 않게)
ITALIC_SLANT = 0.2    # 기울임 파일이 없는 글꼴: 상자째 글 방향으로 기울이는 정도
_TALL = 14000.0       # 높이를 잴 때 쓰는 긴 임시 쪽(PDF 쪽 크기 한도 14400 안)

DEFAULT = {"text": "", "family": "", "fontsize": 10.0, "color": (0.0, 0.0, 0.0), "bold": False, "italic": False,
           "underline": False, "strike": False, "align": 0, "line_height": 0.0, "letter_spacing": 0.0,
           "rect": None, "rotate": 0}

MARK = b"RPDFObj"
_LINE = re.compile(rb"/RPDFObj\s*<<\s*/Id\s+(\d+)\s*/K\s*/([A-Za-z]+)\s*>>\s*BDC(.*?)EMC", re.S)
_DO = re.compile(rb"/([^\s/<>\[\]()]+)\s+Do\b")


# ── 서식 묶음 ───────────────────────────────────────────────────
def clean(style: dict | None) -> dict:
    """서식 묶음을 믿을 수 있는 값으로(모르는 열쇠는 버림)."""
    out = dict(DEFAULT)
    for k, v in (style or {}).items():
        if k not in DEFAULT or v is None:
            continue
        if k in ("fontsize", "line_height", "letter_spacing"):
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                pass
        elif k == "color":
            if isinstance(v, (list, tuple)) and len(v) == 3:
                out[k] = tuple(min(1.0, max(0.0, float(c))) for c in v)
        elif k in ("bold", "italic", "underline", "strike"):
            out[k] = bool(v)
        elif k == "align":
            out[k] = int(v) if v in (0, 1, 2, 3) else 0
        elif k == "rotate":
            out[k] = int(v) % 360
        elif k == "rect":
            out[k] = [float(x) for x in v][:4]
        else:
            out[k] = str(v) if k in ("text", "family") else v
    out["fontsize"] = max(1.0, min(400.0, out["fontsize"]))
    out["line_height"] = max(0.0, min(10.0, out["line_height"]))
    out["letter_spacing"] = max(-20.0, min(100.0, out["letter_spacing"]))
    if out["family"] == F.DEFAULT_FAMILY:
        out["family"] = ""
    return out


def dumps(style: dict) -> str:
    s = clean(style)
    s["color"] = list(s["color"])
    return json.dumps(s, ensure_ascii=True, separators=(",", ":"))


def loads(raw: str) -> dict | None:
    try:
        return clean(json.loads(raw))
    except (ValueError, TypeError):
        return None


# ── 글꼴 ───────────────────────────────────────────────────────
def face(style: dict) -> tuple[str, bool, bool]:
    """(글꼴 파일, 굵게 흉내, 기울임 흉내). 글자를 못 그리는 글꼴이면 맑은 고딕."""
    s = style
    if s["family"]:
        path, fb, fi = F.font_file(s["family"], s["bold"], s["italic"])
        try:
            if F.covers(path, s["text"]):
                return path, fb, fi
        except Exception:  # noqa: BLE001  읽지 못하는 글꼴 파일
            pass
    path, fb, fi = F.font_file(F.DEFAULT_FAMILY, s["bold"], s["italic"])
    return path, fb, fi


def line_factor(style: dict, path: str | None = None) -> float:
    """한 줄 높이(글자 크기 배). 0 이면 글꼴의 위아래 높이(맑은 고딕 1.33, insert_textbox 와 같음)."""
    if style.get("line_height"):
        return float(style["line_height"])
    fo = F.font_object(path or face(clean(style))[0])
    return fo.ascender - fo.descender


def text_width(style: dict, line: str, path: str | None = None) -> float:
    s = clean(style)
    fo = F.font_object(path or face(s)[0])
    return fo.text_length(line, s["fontsize"]) + s["letter_spacing"] * len(line)


def box_size(style: dict, min_width: float = 0) -> tuple[float, float]:
    """글을 줄바꿈 없이 넣을 상자 크기(pt, 글 방향 기준)."""
    s = clean(style)
    path = face(s)[0]
    lines = s["text"].split("\n")
    w = max(text_width(s, ln, path) for ln in lines) + TEXT_PAD
    return max(w, min_width), s["fontsize"] * line_factor(s, path) * len(lines) + TEXT_PAD


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def html_css(style: dict) -> tuple[str, str]:
    """서식 묶음 → (HTML, CSS). 줄마다 문단 하나, 빈 줄은 폭 없는 글자로 높이를 지킨다."""
    s = clean(style)
    path, _fb, _fi = face(s)
    paras = "".join(f"<p>{_esc(ln) if ln else '&#8203;'}</p>" for ln in s["text"].split("\n"))
    deco = " ".join(n for n, on in (("underline", s["underline"]), ("line-through", s["strike"])) if on) or "none"
    hexc = "#" + "".join(f"{round(c * 255):02x}" for c in s["color"])
    url = path.replace("\\", "/")
    css = (f'@font-face {{font-family: rpdf0; src: url("{url}");}}\n'
           f"p {{font-family: rpdf0; font-size: {s['fontsize']:g}pt; color: {hexc}; margin: 0; padding: 0;"
           f" white-space: pre-wrap; text-align: {('left', 'center', 'right', 'justify')[s['align']]};"
           f" line-height: {line_factor(s, path):.4f}; letter-spacing: {s['letter_spacing']:g}pt;"
           f" text-decoration: {deco}; font-weight: normal; font-style: normal;}}")
    return f"<body>{paras}</body>", css


# ── 그리기(임시 문서) ────────────────────────────────────────────
def _scratch(style: dict, w: float, h: float) -> tuple[fitz.Document, int, float]:
    """임시 문서의 w×h 쪽에 글을 그린다. (임시 문서, 글 Form xref, 실제로 쓴 높이). 높이가 모자라면 늘린다."""
    s = clean(style)
    html, css = html_css(s)
    w = max(w, 1.0)
    src = fitz.open()
    pg = src.new_page(width=w, height=max(h, 1.0))
    spare, _ = pg.insert_htmlbox(pg.rect, html, css=css, scale_low=1)
    if spare < 0:   # 모자라면 긴 쪽에서 재고 그 높이로 다시
        src.close()
        src = fitz.open()
        pg = src.new_page(width=w, height=_TALL)
        spare, _ = pg.insert_htmlbox(pg.rect, html, css=css, scale_low=1)
        used = _TALL - max(spare, 0)
        for extra in (0.5, 2.0, 6.0):   # 잰 높이에 딱 맞춘 쪽이 반올림으로 모자라면 조금씩 늘려 다시
            src.close()
            src = fitz.open()
            h = used + extra
            pg = src.new_page(width=w, height=h)
            spare, _ = pg.insert_htmlbox(pg.rect, html, css=css, scale_low=1)
            if spare >= 0:
                break
        spare = 0.0
    used = h - max(spare, 0)
    _path, fake_bold, _fi = face(s)
    if fake_bold:
        _fake_bold(src, s["color"], s["fontsize"])
    try:
        src.subset_fonts()
    except Exception:  # noqa: BLE001  줄이기에 실패하면 통째로(크기만 큼)
        pass
    form = next(x[0] for x in pg.get_xobjects() if x[2] == 0)
    return src, form, used


def _fake_bold(src: fitz.Document, color: Color, fontsize: float) -> None:
    """굵은 글꼴 파일이 없을 때: 글자에 획을 더한다(채우기+선 그리기, 글자 크기에 맞춘 선 두께)."""
    r, g, b = color
    pre = f"{r:g} {g:g} {b:g} RG {max(0.3, fontsize * 0.04):g} w ".encode()
    for x in range(1, src.xref_length()):
        if src.xref_is_stream(x) and src.xref_get_key(x, "Subtype")[1] == "/Form":
            data = src.xref_stream(x) or b""
            if b"BT" in data:
                data = re.sub(rb"\b0 Tr\b", b"2 Tr", data)
                src.update_stream(x, pre + b"2 Tr\n" + data)


def _graft(dst: fitz.Document, src: fitz.Document, xref: int) -> int:
    """src 의 객체 xref 를 dst 로 옮긴다(딸린 글꼴·자원까지). dst 의 새 xref."""
    d = pymupdf._as_pdf_document(dst)
    s = pymupdf._as_pdf_document(src)
    obj = pymupdf.mupdf.pdf_new_indirect(s, xref, 0)
    ref = pymupdf.mupdf.pdf_graft_object(d, obj)
    return pymupdf.mupdf.pdf_to_num(ref)


def build(dst: fitz.Document, style: dict, w: float, h: float) -> tuple[int, float, float]:
    """dst 에 글 Form 을 만든다. (Form xref, 너비, 높이) · 높이는 h 보다 글이 길면 늘어난다. Form 공간은 0,0~너비,높이(위로 y)."""
    src, form, used = _scratch(style, w, h)
    try:
        hh = max(h, used)
        x = _graft(dst, src, form)
    finally:
        src.close()
    dst.xref_set_key(x, "BBox", f"[0 0 {_num(max(w, 1.0))} {_num(hh)}]")
    dst.xref_set_key(x, "Matrix", "[1 0 0 1 0 0]")
    return x, max(w, 1.0), hh


def _num(v: float) -> str:
    t = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


def matrix_for(page_rect: fitz.Rect, rotate: int, w: float, h: float, to_pdf: fitz.Matrix,
               italic: bool = False) -> fitz.Matrix:
    """Form 공간(0,0~w,h, 위로 y) → 그릴 자리 좌표(PDF 쪽 또는 주석 외관 공간).

    page_rect: 회전 전 쪽 좌표의 바깥 네모(글 방향으로 돌린 뒤). rotate: 글 방향(반시계, insert_textbox 와 같은 뜻).
    to_pdf: 쪽 좌표 → 그릴 자리 좌표. italic: 상자째 글 방향으로 기울인다(가운데를 축으로)."""
    rot = fitz.Matrix(rotate % 360)
    b = fitz.Rect(0, 0, w, h) * rot
    t = fitz.Rect(page_rect) * to_pdf
    t.normalize()
    m = rot * fitz.Matrix(1, 0, 0, 1, t.x0 - b.x0, t.y0 - b.y0)
    if italic:
        c = (t.tl + t.br) / 2
        m = (m * fitz.Matrix(1, 0, 0, 1, -c.x, -c.y) * ~rot * fitz.Matrix(1, 0, ITALIC_SLANT, 1, 0, 0) * rot
             * fitz.Matrix(1, 0, 0, 1, c.x, c.y))
    return m


def layout(style: dict, box: fitz.Rect, grow: bool = False) -> tuple[fitz.Rect, float, float]:
    """글 방향 상자(dims) 계산: (글 방향 네모 lr, 너비, 최소 높이). grow=True 면 가장 긴 줄만큼, 아니면 가장 긴 낱말만큼은
    너비를 늘린다(정렬 쪽 붙박음, 낱말이 상자 밖으로 삐져 잘리지 않게)."""
    s = clean(style)
    rot = s["rotate"]
    lr = fitz.Rect(box) * fitz.Matrix(rot)
    lr.normalize()
    if s["text"].strip():
        path = face(s)[0]
        parts = s["text"].split("\n") if grow else s["text"].split()
        need = max(text_width(s, ln, path) for ln in parts) + TEXT_PAD + 1
        if lr.width < need:
            if s["align"] == 1:
                lr.x0 -= (need - lr.width) / 2
                lr.x1 = lr.x0 + need
            elif s["align"] == 2:
                lr.x0 = lr.x1 - need
            else:
                lr.x1 = lr.x0 + need
    return lr, lr.width, lr.height


# ── 쪽의 글 객체 줄 ──────────────────────────────────────────────
def lines(page: fitz.Page) -> list[dict]:
    """쪽 내용의 표식 줄 전부(그리는 차례). {id, kind, cx, start, end, name, xref}."""
    f = page.parent
    out = []
    for cx in page.get_contents():
        data = f.xref_stream(cx) or b""
        if MARK not in data:
            continue
        for m in _LINE.finditer(data):
            dm = _DO.search(m.group(3))
            name = dm.group(1).decode("latin-1") if dm else ""
            out.append({"id": int(m.group(1)), "kind": m.group(2).decode("latin-1"), "cx": cx,
                        "start": m.start(), "end": m.end(), "name": name, "xref": _res_xref(page, name)})
    return out


def _res_xref(page: fitz.Page, name: str) -> int:
    if not name:
        return 0
    t, v = page.parent.xref_get_key(page.xref, f"Resources/XObject/{name}")
    if t == "xref":
        return int(v.split()[0])
    for x in page.get_xobjects():   # 물려받은 자원
        if x[1] == name and x[2] == 0:
            return x[0]
    return 0


def text_objects(page: fitz.Page) -> list[dict]:
    """쪽의 글 객체(K=T)와 서식 묶음. {id, xref, style, rect}."""
    out = []
    for ln in lines(page):
        if ln["kind"] != "T" or not ln["xref"]:
            continue
        t, v = page.parent.xref_get_key(ln["xref"], "RPDFText")
        st = loads(v) if t == "string" else None
        if st is None:
            continue
        out.append({**ln, "style": st, "rect": fitz.Rect(st["rect"]) if st["rect"] else fitz.Rect()})
    return out


def find_text(page: fitz.Page, rect: fitz.Rect) -> dict | None:
    """rect 와 가장 많이 겹치는 글 객체(겹친 넓이가 둘 중 작은 것의 절반 넘을 때)."""
    best, score = None, 0.0
    r = fitz.Rect(rect)
    for o in text_objects(page):
        i = o["rect"] & r
        if i.is_empty:
            continue
        small = max(1e-6, min(abs(o["rect"]), abs(r)))
        sc = abs(i) / small
        if sc > score:
            best, score = o, sc
    return best if score > 0.5 else None


def _next_id(page: fitz.Page) -> int:
    return max([ln["id"] for ln in lines(page)] + [0]) + 1


def _ensure_wrapped(page: fitz.Page) -> None:
    """원문 내용을 q … Q 로 감싸 둔다(원문이 맨 바깥에서 좌표계를 바꿔도 우리 줄은 기본 좌표계에)."""
    f = page.parent
    cs = page.get_contents()
    first = (f.xref_stream(cs[0]) or b"").lstrip() if cs else b""
    if not page.is_wrapped or not first.startswith(b"q"):
        if page.is_wrapped:
            pymupdf.TOOLS._insert_contents(page, b"q\n", False)
            pymupdf.TOOLS._insert_contents(page, b"\nQ\n", True)
        else:
            page.wrap_contents()


def _free_name(page: fitz.Page) -> str:
    taken = {x[1] for x in page.get_xobjects()}
    k = 0
    while f"RPDFTx{k}" in taken:
        k += 1
    return f"RPDFTx{k}"


def _line_bytes(oid: int, name: str, m: fitz.Matrix, kind: str = "T") -> bytes:
    cm = " ".join(_num(v) for v in tuple(m))
    return f"/RPDFObj <</Id {oid} /K /{kind}>> BDC q {cm} cm /{name} Do Q EMC".encode()


def write(page: fitz.Page, style: dict, box: fitz.Rect, *, grow: bool = False, oid: int | None = None) -> int:
    """글 객체를 쓴다. oid 가 쪽에 있으면 그 줄 자리에서 바꿔 쓴다(쌓는 순서 그대로), 없으면 맨 앞에 새로.
    빈 글이면 그 객체를 지우고 0. 그 밖에는 객체 Id."""
    from .annots import _own_xobjects
    f = page.parent
    s = clean(style)
    old = next((ln for ln in lines(page) if ln["id"] == oid), None) if oid else None
    if not s["text"].strip():
        if old:
            remove(page, oid)
        return 0
    lr, w, h = layout(s, box, grow)
    path, _fb, fake_italic = face(s)
    x, w, hh = build(f, s, w, h)
    lr.y1 = lr.y0 + hh
    lr.x1 = lr.x0 + w
    r2 = lr * fitz.Matrix(-s["rotate"])
    r2.normalize()
    s["rect"] = [r2.x0, r2.y0, r2.x1, r2.y1]
    f.xref_set_key(x, "RPDFText", fitz.get_pdf_str(dumps(s)))
    m = matrix_for(r2, s["rotate"], w, hh, ~page.transformation_matrix, italic=s["italic"] and fake_italic)
    _own_xobjects(f, page.xref, inherit=True)
    name = _free_name(page)
    f.xref_set_key(page.xref, f"Resources/XObject/{name}", f"{x} 0 R")
    if old:
        data = f.xref_stream(old["cx"]) or b""
        f.update_stream(old["cx"], data[:old["start"]] + _line_bytes(oid, name, m) + data[old["end"]:])
        if _drop_name(page, old["name"]):
            free_forms(f, [old["xref"]])
        new_id = oid
    else:
        _ensure_wrapped(page)
        new_id = oid or _next_id(page)
        cx = page.get_contents()[-1]
        data = f.xref_stream(cx) or b""
        f.update_stream(cx, data + b"\n" + _line_bytes(new_id, name, m) + b"\n")
    _refresh(page)
    return new_id


def remove(page: fitz.Page, oid: int) -> bool:
    """객체 줄을 통째로 들어낸다(교정하지 않음: 밑줄·취소선 선까지 같이 사라진다)."""
    f = page.parent
    old = next((ln for ln in lines(page) if ln["id"] == oid), None)
    if not old:
        return False
    data = f.xref_stream(old["cx"]) or b""
    f.update_stream(old["cx"], data[:old["start"]] + data[old["end"]:])
    if _drop_name(page, old["name"]):
        free_forms(f, [old["xref"]])
    _refresh(page)
    return True


def _drop_name(page: fitz.Page, name: str) -> bool:
    """더 안 쓰는 Form 이름을 쪽 자원에서 뺀다. 다른 줄이 아직 쓰면 두고 False."""
    if not name or any(ln["name"] == name for ln in lines(page)):
        return False
    f = page.parent
    if f.xref_get_key(page.xref, f"Resources/XObject/{name}")[0] != "null":
        f.xref_set_key(page.xref, f"Resources/XObject/{name}", "null")
        t, v = f.xref_get_key(page.xref, "Resources/XObject")
        if t == "dict":
            f.xref_set_key(page.xref, "Resources/XObject", re.sub(r"/[^\s/<>\[\]()]+\s+null(?=[\s/>\]])", "", v))
    return True


_REF = re.compile(r"(\d+) 0 R")
_STR = re.compile(r"\((?:\\.|[^\\()])*\)|<(?!<)[0-9A-Fa-f\s]*>")   # 글자열(안의 「5 0 R」 같은 글은 참조가 아님)
_STOP = re.compile(r"/Type\s*/(?:Page|Pages|Annot|Catalog)\b")


def _reach(f: fitz.Document, roots, seen: set[int] | None = None) -> set[int]:
    """roots 에서 닿는 객체 번호(쪽·주석·문서 뿌리에서 멈춤)."""
    seen = set() if seen is None else seen
    todo = [x for x in roots if x]
    n = f.xref_length()
    while todo:
        x = todo.pop()
        if x in seen or not 0 < x < n:
            continue
        try:
            obj = f.xref_object(x, compressed=True)
        except Exception:  # noqa: BLE001
            continue
        obj = _STR.sub("()", obj)
        if _STOP.search(obj):
            continue
        seen.add(x)
        todo.extend(int(m) for m in _REF.findall(obj))
    return seen


def our_forms(f: fitz.Document, skip: set[int] = frozenset()) -> list[int]:
    """문서 안 우리 글 Form 전부(쪽의 글 객체 줄 + 글 주석 외관). skip 은 뺀다."""
    out = []
    for i in range(f.page_count):
        pg = f[i]
        if any(MARK in (f.xref_stream(cx) or b"") for cx in pg.get_contents()):
            out += [ln["xref"] for ln in lines(pg) if ln["xref"] and ln["xref"] not in skip]
        for ax in pg.annot_xrefs():
            if ax[1] == fitz.PDF_ANNOT_FREE_TEXT:
                fx = annot_form(f, ax[0])
                if fx and fx not in skip:
                    out.append(fx)
    return out


def free_forms(f: fitz.Document, xrefs) -> int:
    """더 안 쓰는 우리 글 Form 과 거기 딸린 글꼴을 비운다(실행 취소 사본이 고칠 때마다 커지지 않게).
    다른 글 Form 이 같이 쓰는 객체(저장 때 같은 객체 합치기로 묶인 글꼴 등)는 남긴다. 비운 수."""
    xs = {x for x in xrefs if x}
    if not xs:
        return 0
    cand = _reach(f, xs)
    if not cand:
        return 0
    keep = _reach(f, our_forms(f, skip=xs))
    n = 0
    for x in cand - keep:
        try:
            if f.xref_is_stream(x):
                f.update_stream(x, b"")
            f.update_object(x, "null")
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def annot_form(f: fitz.Document, xref: int) -> int:
    """글 주석 외관에 입힌 우리 글 Form 의 xref(없으면 0)."""
    t, v = f.xref_get_key(xref, "AP/N")
    if t != "xref":
        return 0
    t2, v2 = f.xref_get_key(int(v.split()[0]), "Resources/XObject/RPDFTx")
    return int(v2.split()[0]) if t2 == "xref" else 0


def _refresh(page: fitz.Page) -> None:
    try:
        page._image_info = None
    except AttributeError:
        pass


# ── 글 주석 외관 ────────────────────────────────────────────────
def dress_annot(f: fitz.Document, xref: int, style: dict, area: fitz.Rect, page: fitz.Page | None = None) -> bool:
    """MuPDF 가 지은 글 주석 외관(/AP /N)에서 글 부분만 걷어 내고 우리 글 Form 으로 바꾼다.

    테두리·바탕·말풍선 지시선은 MuPDF 가 그린 것을 그대로 쓴다. area: 글이 들어갈 네모(회전 전 쪽 좌표).
    style["rotate"] 는 주석의 글 방향. 외관이 없거나 모양을 모르면 False(MuPDF 외관 그대로)."""
    t, v = f.xref_get_key(xref, "AP/N")
    if t != "xref":
        return False
    ap = int(v.split()[0])
    data = f.xref_stream(ap) or b""
    bt = data.find(b"BT")
    if bt >= 0:
        cut = data.rfind(b"W\nn\n", 0, bt)
        cut = cut + 4 if cut >= 0 else max(0, data.rfind(b"\nq\n", 0, bt))
        head = data[:cut]
    else:
        head = data
    s = clean(style)
    lr, w, h = layout(s, area)
    _path, _fb, fake_italic = face(s)
    fx, w, hh = build(f, s, w, h)
    pg = page
    if pg is None:
        t2, pv = f.xref_get_key(xref, "P")
        if t2 == "xref":
            px = int(pv.split()[0])
            n = next((i for i in range(f.page_count) if f.page_xref(i) == px), None)
            pg = f[n] if n is not None else None
    to_pdf = ~pg.transformation_matrix if pg is not None else fitz.Matrix(1, 0, 0, -1, 0, 0)
    # 외관 공간 = PDF 쪽 공간(외관 /Matrix 가 단위 행렬일 때). 글이 아래로 넘치면 위를 붙박고 잘린다(상자 안으로 클립)
    lr.y1 = lr.y0 + hh
    r2 = lr * fitz.Matrix(-s["rotate"])
    r2.normalize()
    m = matrix_for(r2, s["rotate"], w, hh, to_pdf, italic=s["italic"] and fake_italic)
    amat = f.xref_get_key(ap, "Matrix")
    if amat[0] == "array":
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", amat[1])]
        if len(nums) == 6 and nums != [1, 0, 0, 1, 0, 0]:
            m = m * ~fitz.Matrix(*nums)
    cm = " ".join(_num(q) for q in tuple(m))
    t3, res = f.xref_get_key(ap, "Resources")
    if t3 == "xref":
        res = f.xref_object(int(res.split()[0]), compressed=True)
    if t3 in ("null", ""):
        res = "<<>>"
    f.xref_set_key(ap, "Resources", res if res.strip().startswith("<<") else "<<>>")
    f.xref_set_key(ap, "Resources/Font", "null")   # MuPDF 가 글에 쓰던 내장 글꼴은 더 안 씀
    f.xref_set_key(ap, "Resources/XObject", "<<>>")
    f.xref_set_key(ap, "Resources/XObject/RPDFTx", f"{fx} 0 R")
    f.xref_set_key(fx, "RPDFText", fitz.get_pdf_str(dumps({**s, "rect": [r2.x0, r2.y0, r2.x1, r2.y1]})))
    f.update_stream(ap, head + f"\nq {cm} cm /RPDFTx Do Q\n".encode())
    return True


def annot_font(f: fitz.Document, xref: int) -> str:
    """글 주석에 우리가 입힌 글꼴 이름(외관 Form 의 /RPDFText). 없으면 빈 이름."""
    fx = annot_form(f, xref)
    if not fx:
        return ""
    t3, raw = f.xref_get_key(fx, "RPDFText")
    st = loads(raw) if t3 == "string" else None
    return (st or {}).get("family") or (F.DEFAULT_FAMILY if st else "")
