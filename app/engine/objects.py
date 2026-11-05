"""편집 객체: 쪽 내용에 놓인 그림·글 덩어리를 표식 줄로 다룬다(설계 17, docs/편집 객체 설계.md 4.1·4.2·4.5·4.6).

객체 줄은 쪽 내용 맨 바깥(기본 좌표계)에 놓인 XObject 그리기 한 줄이다.
    /RPDFObj <</Id n /K /I>> BDC q a b c d e f cm /RPDFA숫자 gs /이름 Do Q EMC
K 는 /I(그림)·/T(글). Id 는 그 쪽 안에서만 유일한 정수이고 객체의 정체는 Id 뿐이다
(교정 한 번에 스트림·자원 이름·Form 번호가 다 바뀌므로 그것들로는 다시 찾지 않는다).

표식 없는 그림·글은 원문 객체다. 쌓는 순서를 바꾸거나 옮길 때 「들어올려」 객체 줄로 만든다.
복사 자료(spec)는 문서를 바꾸지 않고 읽기만 한다(원문 그림도 그 자리에서 읽음).

ref 자리에는 우리 객체 Id(정수)나 list_objects·find 가 돌려준 항목(dict)을 준다.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Iterable, Sequence

import fitz

from .document import Document, redact_only

MARK = b"RPDFObj"
GS_HEAD = "RPDFA"          # 불투명도 그래픽 상태 이름 머리(annots.transform_image 와 같음)
TEXT_KEY = "RPDFText"      # 글 객체 Form 사전의 서식 열쇠(JSON 글자열)
NO_ORDER = "이 객체는 순서를 바꿀 수 없습니다"
HOWS = ("front", "back", "forward", "backward")
ALIGN_HOWS = ("left", "right", "top", "bottom", "hcenter", "vcenter", "page_h", "page_v", "page_both", "dist_h", "dist_v")


# ── 내용 스트림 낱말 나누기 ───────────────────────────────────────
_RE_WS = re.compile(rb"(?:[\x00\t\n\x0c\r ]+|%[^\r\n]*)+")
_RE_REG = re.compile(rb"[^\x00\t\n\x0c\r ()<>\[\]{}/%]+")
_RE_NAME = re.compile(rb"/[^\x00\t\n\x0c\r ()<>\[\]{}/%]*")
_RE_HEX = re.compile(rb"<[0-9A-Fa-f\x00\t\n\x0c\r ]*>")
_RE_ID = re.compile(rb"[\x00\t\n\x0c\r ]ID[\x00\t\n\x0c\r ]")
_RE_EI = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r /%]|$)")
_NUM = rb"[-+]?(?:\d+\.?\d*|\.\d+)"
_RE_INNER = re.compile(rb"^\s*(?:q\s+)?(?:(" + _NUM + rb"(?:\s+" + _NUM + rb"){5})\s*cm\s*)?"
                       rb"(?:/([^\s/\[\]()<>{}%]+)\s*gs\s*)?/([^\s/\[\]()<>{}%]+)\s*Do\s*(?:Q\s*)?$")


def _tokens(data: bytes):
    """(시작, 끝, 종류, 낱말) 차례. 종류: reg(수·연산자)·name·str(글자열)·inline(인라인 그림)·<< >> [ ] { }.
    글자열과 인라인 그림 안의 q·Q 는 건너뛴다."""
    i, n = 0, len(data)
    while i < n:
        m = _RE_WS.match(data, i)
        if m:
            i = m.end()
            continue
        c = data[i]
        if c == 0x28:                       # ( 글자열: 괄호 짝과 \ 건너뛰기
            depth, j = 1, i + 1
            while j < n and depth:
                b = data[j]
                if b == 0x5C:
                    j += 2
                    continue
                if b == 0x28:
                    depth += 1
                elif b == 0x29:
                    depth -= 1
                j += 1
            yield i, j, "str", None
            i = j
        elif c == 0x3C:                     # << 또는 <16진 글자열>
            if data[i + 1:i + 2] == b"<":
                yield i, i + 2, "<<", None
                i += 2
            else:
                m = _RE_HEX.match(data, i)
                j = m.end() if m else i + 1
                yield i, j, "str", None
                i = j
        elif c == 0x3E:
            j = i + 2 if data[i + 1:i + 2] == b">" else i + 1
            yield i, j, ">>", None
            i = j
        elif c in b"[]{}":
            yield i, i + 1, chr(c), None
            i += 1
        elif c == 0x2F:
            m = _RE_NAME.match(data, i)
            yield i, m.end(), "name", data[i + 1:m.end()]
            i = m.end()
        elif c == 0x29:                     # 짝 없는 ) 는 버린다
            i += 1
        else:
            m = _RE_REG.match(data, i)
            j = m.end()
            tok = data[i:j]
            if tok == b"BI":                # 인라인 그림: ID 뒤 바이트를 EI 까지 통째로
                mi = _RE_ID.search(data, j)
                me = _RE_EI.search(data, mi.end()) if mi else None
                j = me.end() if me else n
                yield i, j, "inline", None
            else:
                yield i, j, "reg", tok
            i = j


def _parse(data: bytes) -> tuple[list[dict], list[dict]]:
    """쪽 내용을 맨 바깥 덩이로 나눈다.

    덩이: {"t": "obj", "s", "e", "id", "k", "inner": (s, e)} 또는 {"t": "raw", "s", "e", "ok"}.
    raw 의 ok 는 그 덩이 안의 q·Q 가 맞고 맨 바깥에 cm 이 없는지(넘어 옮겨도 되는지).
    Do 목록: {"s", "e", "name", "seg"(덩이 차례), "obj"(객체 줄 안인지)} — 쪽에서 바로 그린 XObject, 그린 차례."""
    segs: list[dict] = []
    dos: list[dict] = []
    qd = md = 0
    obj = None            # 열린 객체 줄
    pend = None           # /RPDFObj 를 보고 << … >> BDC 를 기다리는 중
    raw = {"t": "raw", "s": 0, "base": 0, "min": 0, "cm0": False, "has": False}
    prev = None           # 바로 앞 낱말

    def close_raw(end: int):
        if raw["has"]:
            segs.append({"t": "raw", "s": raw["s"], "e": end,
                         "ok": raw["min"] >= 0 and qd - raw["base"] == 0 and not raw["cm0"]})

    for s, e, kind, tok in _tokens(data):
        if pend is not None:
            st = pend["stage"]
            if st == 0 and kind == "<<":
                pend["stage"] = 1
                prev = (s, e, kind, tok)
                continue
            if st == 1:
                if kind == ">>":
                    pend["stage"] = 2
                prev = (s, e, kind, tok)
                continue
            if st == 2 and kind == "reg" and tok == b"BDC":
                head = data[pend["s"]:e]
                mi = re.search(rb"/Id\s+(\d+)", head)
                mk = re.search(rb"/K\s*/([IT])", head)
                if mi:
                    close_raw(pend["s"])
                    obj = {"t": "obj", "s": pend["s"], "id": int(mi.group(1)),
                           "k": (mk.group(1).decode() if mk else "I"), "is": e}
                pend = None
                md += 1
                prev = (s, e, kind, tok)
                continue
            if obj is None and not raw["has"]:
                raw.update(s=pend["s"], base=qd, min=0, cm0=False, has=True)
            pend = None
        if obj is None and qd == 0 and md == 0 and kind == "name" and tok == MARK:
            pend = {"s": s, "stage": 0}
            prev = (s, e, kind, tok)
            continue
        if obj is None and not raw["has"]:
            raw.update(s=s, base=qd, min=0, cm0=False, has=True)
        if kind == "reg":
            if tok == b"q":
                qd += 1
            elif tok == b"Q":
                qd -= 1
                if obj is None:
                    raw["min"] = min(raw["min"], qd - raw["base"])
            elif tok in (b"BDC", b"BMC"):
                md += 1
            elif tok == b"EMC":
                md = max(0, md - 1)
                if obj is not None and md == 0:
                    obj["e"] = e
                    obj["inner"] = (obj.pop("is"), s)
                    segs.append(obj)
                    obj = None
                    raw = {"t": "raw", "s": e, "base": qd, "min": 0, "cm0": False, "has": False}
            elif tok == b"cm" and obj is None and qd == raw["base"]:
                raw["cm0"] = True
            elif tok == b"Do" and prev is not None and prev[2] == "name":
                dos.append({"s": prev[0], "e": e, "name": prev[3].decode("latin-1"), "seg": len(segs),
                            "obj": obj is not None})
        prev = (s, e, kind, tok)
    if obj is not None:                     # EMC 없이 끝난 줄은 원문으로 본다
        raw.update(s=obj["s"], base=0, has=True)
        obj = None
    close_raw(len(data))
    return segs, dos


# ── 쪽 내용 읽기·쓰기 ──────────────────────────────────────────────
def _joined(f: fitz.Document, p: fitz.Page) -> bytes:
    return b"\n".join(f.xref_stream(cx) or b"" for cx in p.get_contents())


def _consolidate(f: fitz.Document, p: fitz.Page) -> tuple[int, bytes]:
    """쪽 내용을 한 스트림으로 합친다. q·Q 짝이 안 맞거나 맨 바깥에 cm 이 남은 원문 덩이는 q … Q 로 감싼다.
    (그 스트림 xref, 내용)."""
    if not p.get_contents():
        x = f.get_new_xref()
        f.update_object(x, "<<>>")
        f.update_stream(x, b" ")
        f.xref_set_key(p.xref, "Contents", f"{x} 0 R")
    cs = p.get_contents()   # 감싸기는 덩이마다 한다(page.wrap_contents 는 짝만 맞춰 끝에 붙인 객체 줄을 안에 가둘 수 있다)
    data = _joined(f, p)
    segs, _ = _parse(data)
    if any(s["t"] == "raw" and not s["ok"] for s in segs):
        out, last = [], 0
        for s in segs:
            if s["t"] == "raw" and not s["ok"]:
                piece = data[s["s"]:s["e"]]
                d = _balance(piece)
                out.append(data[last:s["s"]] + b"q\n" + b"q\n" * d[0] + piece + b"\n" + b"Q\n" * d[1] + b"Q")
                last = s["e"]
        out.append(data[last:])
        data = b"".join(out)
    _write(f, p, cs[0], data)
    if len(cs) > 1 or f.xref_get_key(p.xref, "Contents")[0] != "xref":
        f.xref_set_key(p.xref, "Contents", f"{cs[0]} 0 R")
    return cs[0], data


def _balance(piece: bytes) -> tuple[int, int]:
    """(앞에 더할 q 수, 뒤에 더할 Q 수)."""
    d = lo = 0
    for _, _, kind, tok in _tokens(piece):
        if kind == "reg" and tok == b"q":
            d += 1
        elif kind == "reg" and tok == b"Q":
            d -= 1
            lo = min(lo, d)
    return -lo, d - lo


def _write(f: fitz.Document, p: fitz.Page, cx: int, data: bytes) -> None:
    f.update_stream(cx, data)
    p._image_info = None


def _tm(p: fitz.Page) -> fitz.Matrix:
    """PDF 좌표 → 회전 전 쪽 좌표. page.transformation_matrix 는 돌린 쪽에서 미디어 상자 시작점을 빠뜨려 직접 짓는다."""
    cb, mb = p.cropbox, p.mediabox
    return fitz.Matrix(1, 0, 0, -1, -cb.x0, mb.y1 - cb.y0)


def _names(p: fitz.Page) -> dict[str, int]:
    """쪽에서 바로 부르는 XObject 이름 → xref(그림·Form)."""
    out = {im[7]: im[0] for im in p.get_images(full=True) if im[9] == 0}
    for x in p.get_xobjects():
        if x[2] == 0:
            out[x[1]] = x[0]
    return out


def _inner(data: bytes, seg: dict) -> tuple[fitz.Matrix, str | None, str] | None:
    """객체 줄 안쪽 → (행렬, gs 이름, XObject 이름)."""
    m = _RE_INNER.match(data[seg["inner"][0]:seg["inner"][1]])
    if not m:
        return None
    mat = fitz.Matrix(*map(float, m.group(1).split())) if m.group(1) else fitz.Matrix(1, 0, 0, 1, 0, 0)
    gs = m.group(2).decode("latin-1") if m.group(2) else None
    return mat, gs, m.group(3).decode("latin-1")


def _num(v: float) -> str:
    t = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


def _line(oid: int, k: str, m: fitz.Matrix, gs: str | None, name: str) -> bytes:
    g = f"/{gs} gs " if gs else ""
    return (f"/RPDFObj <</Id {oid} /K /{k}>> BDC q {' '.join(_num(v) for v in tuple(m))} cm {g}/{name} Do Q EMC"
            .encode("latin-1"))


def _xobj_box(f: fitz.Document, xref: int) -> tuple[fitz.Rect, fitz.Matrix, str]:
    """XObject 의 (단위 네모, 자기 행렬, 종류 I·T)."""
    if f.xref_get_key(xref, "Subtype")[1] == "/Image":
        return fitz.Rect(0, 0, 1, 1), fitz.Matrix(1, 0, 0, 1, 0, 0), "I"
    t, v = f.xref_get_key(xref, "BBox")
    try:
        box = fitz.Rect(*map(float, v.strip("[] ").split())) if t == "array" else fitz.Rect(0, 0, 1, 1)
    except (TypeError, ValueError):
        box = fitz.Rect(0, 0, 1, 1)
    t, v = f.xref_get_key(xref, "Matrix")
    try:
        fm = fitz.Matrix(*map(float, v.strip("[] ").split())) if t == "array" else fitz.Matrix(1, 0, 0, 1, 0, 0)
    except (TypeError, ValueError):
        fm = fitz.Matrix(1, 0, 0, 1, 0, 0)
    return box, fm, "T"


def _ours(f: fitz.Document, p: fitz.Page, data: bytes | None = None) -> tuple[bytes, list[dict], list[dict], list[dict]]:
    """(내용, 덩이, Do 목록, 우리 객체 목록). 우리 객체: {id, kind, rect, xref, ours, seg, matrix, gs, name}."""
    if data is None:
        data = _joined(f, p)
    segs, dos = _parse(data)
    names = _names(p)
    tm = _tm(p)
    out = []
    for n, s in enumerate(segs):
        if s["t"] != "obj":
            continue
        inn = _inner(data, s)
        xref = names.get(inn[2], 0) if inn else 0
        rect, mat = fitz.Rect(), None
        if inn and xref:
            box, fm, _ = _xobj_box(f, xref)
            mat = inn[0]
            rect = (box * fm * mat * tm).normalize()
        out.append({"id": s["id"], "kind": "image" if s["k"] == "I" else "text", "rect": rect, "xref": xref,
                    "ours": True, "seg": n, "matrix": mat, "gs": inn[1] if inn else None,
                    "name": inn[2] if inn else ""})
    return data, segs, dos, out


def _close(a: fitz.Rect, b: fitz.Rect, tol: float = 1.5) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ── 읽기 ─────────────────────────────────────────────────────────
def list_objects(doc: Document, page: int) -> list[dict]:
    """쪽의 편집 객체를 그리는 차례로. 항목: {id, kind(image·text), rect, xref, ours}.
    원문 객체는 id 가 None 이고 ours 가 False(그림은 xref, 글은 xref 0)."""
    with doc.lock:
        f = doc.fitz
        p = f[page]
        data, segs, dos, ours = _ours(f, p)
        items = [{k: o[k] for k in ("id", "kind", "rect", "xref", "ours")} for o in ours]
        mine_img = [o for o in ours if o["kind"] == "image"]
        mine_txt = [o["rect"] for o in ours if o["kind"] == "text"]
        used = set()
        for info in p.get_image_info(xrefs=True):
            r = fitz.Rect(info["bbox"])
            hit = next((n for n, o in enumerate(mine_img) if n not in used and o["xref"] == info.get("xref")
                        and _close(o["rect"], r)), None)
            if hit is not None:
                used.add(hit)
                continue
            items.append({"id": None, "kind": "image", "rect": r, "xref": info.get("xref", 0), "ours": False})
        for b in p.get_text("dict")["blocks"]:
            if b.get("type") != 0 or not any(s["text"].strip() for l in b["lines"] for s in l["spans"]):
                continue
            r = fitz.Rect(b["bbox"])
            c = (r.tl + r.br) / 2
            if any(c in fitz.Rect(t.x0 - 1, t.y0 - 1, t.x1 + 1, t.y1 + 1) for t in mine_txt):
                continue
            items.append({"id": None, "kind": "text", "rect": r, "xref": 0, "ours": False})
        log = p.get_bboxlog()
    return _by_log(items, log)


def _by_log(items: list[dict], log: list) -> list[dict]:
    """bboxlog 에서 처음 그려진 차례로 줄 세운다(우리 객체 먼저 자리를 잡는다)."""
    taken: set[int] = set()
    keys = {}
    for n in sorted(range(len(items)), key=lambda n: not items[n]["ours"]):
        it = items[n]
        r = it["rect"]
        big = fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)
        key = len(log) + n
        for i, (t, bb) in enumerate(log):
            if i in taken:
                continue
            bb = fitz.Rect(bb)
            if it["kind"] == "image":
                ok = t == "fill-image" and _close(bb, r, 2)
            else:
                ok = t.endswith("-text") and ((bb.tl + bb.br) / 2) in big
            if ok:
                key = i
                if it["kind"] == "image":
                    taken.add(i)
                break
        keys[n] = key
    return [items[n] for n in sorted(range(len(items)), key=lambda n: keys[n])]


def find(doc: Document, page: int, rect: fitz.Rect, kind: str | None = None) -> dict | None:
    """rect 자리의 객체 하나(kind: image · text · block, 없으면 둘 다). 자리가 가장 가까운 것."""
    want = {"block": "text"}.get(kind or "", kind)
    r = fitz.Rect(rect)
    cands = [o for o in list_objects(doc, page) if want in (None, "", o["kind"])]
    cands = [o for o in cands if o["rect"].intersects(r) or _close(o["rect"], r, 3)]
    if not cands:
        return None
    return min(cands, key=lambda o: sum(abs(a - b) for a, b in zip(o["rect"], r)))


def _resolve(doc: Document, page: int, ref) -> dict:
    """ref(Id 또는 항목) → 지금 쪽의 항목. 우리 객체는 Id 로만 찾는다."""
    if isinstance(ref, int) or (isinstance(ref, dict) and ref.get("ours") and ref.get("id") is not None):
        oid = ref if isinstance(ref, int) else ref["id"]
        with doc.lock:
            f = doc.fitz
            _, _, _, ours = _ours(f, f[page])
        o = next((o for o in ours if o["id"] == oid), None)
        if o is None:
            raise ValueError(f"객체를 찾을 수 없습니다 (Id {oid})")
        return o
    if isinstance(ref, dict):
        return dict(ref, ours=False, id=None)
    raise TypeError("ref 는 Id(정수)나 list_objects 항목이어야 합니다")


def _next_id(segs: list[dict]) -> int:
    return max((s["id"] for s in segs if s["t"] == "obj"), default=0) + 1


# ── 자원 ─────────────────────────────────────────────────────────
def _put_xobject(f: fitz.Document, p: fitz.Page, xref: int, head: str) -> str:
    """쪽 자원에 xref 를 부르는 이름이 있으면 그것, 없으면 새 이름을 단다."""
    for name, x in _names(p).items():
        if x == xref:
            return name
    from .annots import _own_xobjects
    _own_xobjects(f, p.xref, inherit=True)
    taken = set(_names(p))
    t, v = f.xref_get_key(p.xref, "Resources/XObject")
    if t == "dict":
        taken |= {m.decode("latin-1") for m in re.findall(rb"/([^\s/<>\[\]()]+)\s", v.encode("latin-1"))}
    n = 1
    while f"{head}{n}" in taken:
        n += 1
    name = f"{head}{n}"
    f.xref_set_key(p.xref, f"Resources/XObject/{name}", f"{xref} 0 R")
    return name


def _ensure_gs(f: fitz.Document, p: fitz.Page, opacity: float) -> str:
    """불투명도 그래픽 상태(annots.transform_image 와 같은 모양: 100 아래는 고른 밝기 소프트 마스크)."""
    from .annots import _own_xobjects
    pct = max(0, min(100, int(round(opacity * 100))))
    gs = f"{GS_HEAD}{pct}"
    _own_xobjects(f, p.xref, inherit=True)
    t, v = f.xref_get_key(p.xref, "Resources/ExtGState")
    if t == "xref":
        f.xref_set_key(p.xref, "Resources/ExtGState", f.xref_object(int(v.split()[0]), compressed=True))
    elif t != "dict":
        f.xref_set_key(p.xref, "Resources/ExtGState", "<<>>")
    if f.xref_get_key(p.xref, f"Resources/ExtGState/{gs}")[0] != "null":
        return gs
    if pct >= 100:
        f.xref_set_key(p.xref, f"Resources/ExtGState/{gs}", "<</Type/ExtGState>>")
    else:
        mask = f.get_new_xref()
        f.update_object(mask, "<</Type/XObject/Subtype/Form/BBox[-1 -1 2 2]"
                              "/Group<</Type/Group/S/Transparency/CS/DeviceGray>>>>")
        f.update_stream(mask, f"{pct / 100:g} g -1 -1 3 3 re f".encode())
        f.xref_set_key(p.xref, f"Resources/ExtGState/{gs}",
                       f"<</Type/ExtGState/SMask<</Type/Mask/S/Luminosity/G {mask} 0 R>>>>")
    return gs


def _gs_opacity(gs: str | None) -> float:
    m = re.fullmatch(GS_HEAD + r"(\d+)", gs or "")
    return int(m.group(1)) / 100 if m else 1.0


# ── 실행 취소 한 걸음 ──────────────────────────────────────────────
@contextmanager
def _one_step(doc: Document):
    """안에서 부른 snapshot() 을 한 걸음으로 묶는다. Document.batch() 가 있으면 그것을 쓴다."""
    batch = getattr(doc, "batch", None)
    if batch is not None:
        outer = not getattr(doc, "_batch", 0)
        try:
            with batch():
                yield
        except BaseException:
            if outer:   # 막혔으면 들어가기 전 모습으로 되돌리고 batch 가 뜬 사본도 거둔다
                doc.fitz.close()
                doc.fitz = doc._load(doc._undo.pop())
            raise
        return
    if getattr(doc, "_rpdf_step", False):   # 바깥 걸음 안이면 그대로
        yield
        return
    doc.snapshot()
    doc._rpdf_step = True
    doc.snapshot = lambda: None
    try:
        yield
    except BaseException:
        # 바꾸다 막혔으면 들어가기 전 모습으로 되돌리고 실행 취소 자리도 거둔다
        doc.fitz.close()
        doc.fitz = doc._load(doc._undo.pop())
        raise
    finally:
        del doc.snapshot
        doc._rpdf_step = False


# ── 들어올리기 ────────────────────────────────────────────────────
def _image_occurrence(f: fitz.Document, p: fitz.Page, item: dict, data: bytes, segs, dos) -> tuple[dict, dict]:
    """원문 그림 항목 → (get_image_info 항목, 그것을 그린 쪽 Do). 다른 Form 안에서 그렸으면 ValueError."""
    xref = item["xref"]
    infos = [i for i in p.get_image_info(xrefs=True) if i.get("xref") == xref]
    names = {n for n, x in _names(p).items() if x == xref}
    occ = [d for d in dos if d["name"] in names]
    if not infos or len(occ) != len(infos):
        raise ValueError(NO_ORDER)
    k = min(range(len(infos)), key=lambda n: sum(abs(a - b) for a, b in zip(infos[n]["bbox"], item["rect"])))
    if occ[k]["obj"]:
        raise ValueError(NO_ORDER)
    return infos[k], occ[k]


def _info_matrix(p: fitz.Page, info: dict) -> fitz.Matrix:
    """get_image_info 의 transform → 기본 좌표계 cm 행렬."""
    return fitz.Matrix(1, 0, 0, -1, 0, 1) * fitz.Matrix(info["transform"]) * ~_tm(p)


def _look(p: fitz.Page) -> bytes:
    return p.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), annots=False).samples


def _occ_gs(data: bytes, occ: dict) -> float:
    """원문 그림 바로 앞에 annots.transform_image 가 건 불투명도(없으면 1)."""
    m = re.search(rb"/" + GS_HEAD.encode() + rb"(\d+)\s*gs\s*$", data[max(0, occ["s"] - 40):occ["s"]])
    return int(m.group(1)) / 100 if m else 1.0


def _lift_image(doc: Document, page: int, item: dict, side: str | None) -> int:
    """side 가 None 이면 원문 덩이 뒤·앞에 둘 다 놓아 그려 보고 화면이 같은 쪽(덜 다른 쪽)을 고른다."""
    f = doc.fitz
    p = f[page]
    cx, data = _consolidate(f, p)
    segs, dos = _parse(data)
    info, occ = _image_occurrence(f, p, item, data, segs, dos)
    seg = segs[occ["seg"]]
    if seg["t"] != "raw" or not seg["ok"]:
        raise ValueError(NO_ORDER)
    m = _info_matrix(p, info)
    oid = _next_id(segs)
    gs = _ensure_gs(f, p, _occ_gs(data, occ))
    line = _line(oid, "I", m, gs, occ["name"])
    body = data[:occ["s"]] + b" " + data[occ["e"]:]
    shift = occ["e"] - occ["s"] - 1

    def build(where: str) -> bytes:
        at = seg["e"] - shift if where == "after" else seg["s"]
        return body[:at] + b"\n" + line + b"\n" + body[at:]

    if side is None:
        look = _look(p)
        best = None
        for where in ("after", "before"):
            _write(f, p, cx, build(where))
            d = sum(1 for x, y in zip(_look(f[page]), look) if x != y)
            if best is None or d < best[0]:
                best = (d, where)
            if d == 0:
                break
        side = best[1]
        p = f[page]
    _write(f, p, cx, build(side))
    return oid


def _block_text(p: fitz.Page, rect: fitz.Rect) -> str:
    lines = []
    for b in p.get_text("dict", clip=rect)["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            lines.append("".join(s["text"] for s in ln["spans"]))
    return "\n".join(lines).strip("\n")


def _lift_text(doc: Document, page: int, item: dict, new_rect: fitz.Rect | None = None) -> int:
    """원문 글 → 우리 글 객체(annots.edit_block 길: 그 자리 글만 지우고 다시 씀). 새 Id."""
    from . import annots as A
    p = doc.fitz[page]
    rect = fitz.Rect(item["rect"])
    text = _block_text(p, rect)
    st = A.block_style(doc, page, rect)
    before = {o["id"] for o in _ours(doc.fitz, p)[3]}
    got = A.edit_block(doc, page, rect, text, st["fontsize"], st["color"], st["bold"], 0, italic=st["italic"],
                       family=st["family"], new_rect=new_rect)
    if isinstance(got, int) and not isinstance(got, bool):
        return got
    after = [o["id"] for o in _ours(doc.fitz, doc.fitz[page])[3] if o["id"] not in before]
    if not after:
        raise ValueError("이 글은 아직 글 객체로 바꿀 수 없습니다")
    return after[-1]


def lift(doc: Document, page: int, ref, side: str | None = None) -> int:
    """원문 객체를 객체 줄로 들어올린다(우리 객체면 그대로 Id). side: after(원문 덩이 뒤)·before(앞)·None(겹친 것이
    없는 쪽을 골라 화면이 같게). 그림의 원래 자르기는 사라진다. 실행 취소 한 걸음."""
    item = _resolve(doc, page, ref)
    if item["ours"]:
        return item["id"]
    with doc.lock, _one_step(doc):
        return _lift(doc, page, item, side)


def _lift(doc: Document, page: int, item: dict, side: str | None) -> int:
    if item["kind"] == "image":
        return _lift_image(doc, page, item, side)
    return _lift_text(doc, page, item)


# ── 쌓는 순서 ─────────────────────────────────────────────────────
def order(doc: Document, page: int, ref, how: str) -> int:
    """쌓는 순서: front(맨 앞으로)·back(맨 뒤로)·forward(앞으로)·backward(뒤로). 원문 객체는 먼저 들어올린다
    (원문 덩이 안 객체의 앞으로는 그 덩이 바로 뒤, 뒤로는 바로 앞). 바꿀 수 없으면 ValueError. 객체 Id."""
    if how not in HOWS:
        raise ValueError(f"how: {how}")
    item = _resolve(doc, page, ref)
    with doc.lock:
        if item["ours"]:
            _check_order(doc, page, item["id"], how)
        with _one_step(doc):
            if not item["ours"]:
                if item["kind"] == "image":
                    oid = _lift_image(doc, page, item, "after" if how in ("front", "forward") else "before")
                    if how in ("forward", "backward"):
                        return oid
                else:
                    oid = _lift_text(doc, page, item)
                    if how in ("forward", "backward"):   # 새 글 객체는 맨 끝에 놓인다: 원문 덩이 바로 뒤·앞으로
                        _place_next_to_text(doc, page, oid, item["rect"], how)
                        return oid
            else:
                oid = item["id"]
            _move_seg(doc, page, oid, how)
            return oid


def _plan(segs: list[dict], idx: int, how: str) -> tuple[list[dict], list[dict]]:
    """(새 덩이 차례, 넘어가는 덩이들)."""
    rest = segs[:idx] + segs[idx + 1:]
    piece = segs[idx]
    if how == "front":
        at = len(rest)
    elif how == "back":
        at = 0
    elif how == "forward":
        at = min(idx + 1, len(rest))
    else:
        at = max(idx - 1, 0)
    crossed = segs[idx + 1:at + 1] if at >= idx else segs[at:idx]
    return rest[:at] + [piece] + rest[at:], crossed


def _check_order(doc: Document, page: int, oid: int, how: str) -> None:
    f = doc.fitz
    p = f[page]
    data = _joined(f, p)
    segs, _ = _parse(data)
    idx = next((n for n, s in enumerate(segs) if s["t"] == "obj" and s["id"] == oid), None)
    if idx is None:
        raise ValueError(f"객체를 찾을 수 없습니다 (Id {oid})")
    _, crossed = _plan(segs, idx, how)
    if any(s["t"] == "raw" and not s["ok"] and _real(data, s) for s in crossed):
        # 맨 바깥 cm 만 있는 덩이는 합칠 때 감싸서 고치므로, 짝이 안 맞는 q·Q 만 막는다
        if any(_balance(data[s["s"]:s["e"]]) != (0, 0) for s in crossed if s["t"] == "raw"):
            raise ValueError(NO_ORDER)


def _real(data: bytes, s: dict) -> bool:
    return bool(data[s["s"]:s["e"]].strip())


def _move_seg(doc: Document, page: int, oid: int, how: str) -> None:
    f = doc.fitz
    p = f[page]
    cx, data = _consolidate(f, p)
    segs, _ = _parse(data)
    segs = [s for s in segs if s["t"] == "obj" or _real(data, s)]
    idx = next(n for n, s in enumerate(segs) if s["t"] == "obj" and s["id"] == oid)
    new, crossed = _plan(segs, idx, how)
    if any(s["t"] == "raw" and not s["ok"] for s in crossed):
        raise ValueError(NO_ORDER)
    _write(f, p, cx, b"\n".join(data[s["s"]:s["e"]] for s in new) + b"\n")


def _place_next_to_text(doc: Document, page: int, oid: int, rect: fitz.Rect, how: str) -> None:
    """들어올린 글 객체(맨 끝)를 원래 글이 있던 원문 덩이 바로 뒤(forward)·앞(backward)으로."""
    f = doc.fitz
    p = f[page]
    cx, data = _consolidate(f, p)
    segs, _ = _parse(data)
    segs = [s for s in segs if s["t"] == "obj" or _real(data, s)]
    idx = next(n for n, s in enumerate(segs) if s["t"] == "obj" and s["id"] == oid)
    piece = segs.pop(idx)
    raws = [n for n, s in enumerate(segs) if s["t"] == "raw" and _shows_text(data[s["s"]:s["e"]])]
    host = raws[0] if len(raws) == 1 else (raws[-1] if raws else len(segs) - 1)
    at = host + 1 if how == "forward" else host
    segs.insert(max(0, at), piece)
    _write(f, p, cx, b"\n".join(data[s["s"]:s["e"]] for s in segs) + b"\n")


def _shows_text(piece: bytes) -> bool:
    return any(kind == "reg" and tok in (b"Tj", b"TJ", b"'", b'"') for _, _, kind, tok in _tokens(piece))


# ── 복사 자료 · 붙여넣기 ──────────────────────────────────────────
def _image_bytes(f: fitz.Document, xref: int) -> tuple[bytes, bytes | None]:
    """(그림 바이트, 알파 마스크 바이트). 원래 꼴(jpeg 등)을 살리고, 못 읽으면 PNG 로."""
    try:
        e = f.extract_image(xref)
        mask = None
        if e.get("smask"):
            mask = f.extract_image(e["smask"])["image"]
        if e.get("image") and (e.get("colorspace", 3) in (1, 3) or e.get("ext") in ("jpeg", "jpx")):
            return e["image"], mask
    except Exception:  # noqa: BLE001
        pass
    pix = fitz.Pixmap(f, xref)
    if pix.colorspace and pix.colorspace.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    sm = f.xref_get_key(xref, "SMask")
    if sm[0] == "xref":
        try:
            pix = fitz.Pixmap(pix, fitz.Pixmap(f, int(sm[1].split()[0])))
        except Exception:  # noqa: BLE001
            pass
    return pix.tobytes("png"), None


def _text_style(f: fitz.Document, xref: int) -> dict | None:
    t, v = f.xref_get_key(xref, TEXT_KEY)
    if t != "string":
        return None
    try:
        return json.loads(v)
    except ValueError:
        return None


def spec(doc: Document, page: int, ref) -> dict:
    """다시 만들 수 있게 적은 복사 자료(문서는 바꾸지 않음). 객체 번호에 기대지 않고, 같은 문서면 xref 를 다시 쓴다.

    그림: {kind: image, image, mask, matrix(단위 네모 → 쪽 좌표), opacity, rect, doc, xref}
    글:   {kind: text, text, style(/RPDFText 또는 block_style), matrix, rect, doc, form(같은 문서에서 다시 쓸 Form xref)}"""
    item = _resolve(doc, page, ref)
    with doc.lock:
        f = doc.fitz
        p = f[page]
        tm = _tm(p)
        base = {"kind": item["kind"], "rect": tuple(item["rect"]), "doc": id(doc)}
        if item["kind"] == "image":
            if item["ours"]:
                m, op = item["matrix"], _gs_opacity(item["gs"])
            else:
                data = _joined(f, p)
                segs, dos = _parse(data)
                info, occ = _image_occurrence(f, p, item, data, segs, dos)
                m, op = _info_matrix(p, info), _occ_gs(data, occ)
            img, mask = _image_bytes(f, item["xref"])
            return dict(base, image=img, mask=mask, matrix=tuple(m * tm), opacity=op, xref=item["xref"])
        text = _block_text(p, fitz.Rect(item["rect"]))
        if item["ours"]:
            st = _text_style(f, item["xref"])
            return dict(base, text=(st or {}).get("text", text), style=st, matrix=tuple(item["matrix"] * tm),
                        form=item["xref"])
        from . import annots as A
        st = dict(A.block_style(doc, page, fitz.Rect(item["rect"])), text=text)
        return dict(base, text=text, style=st, matrix=None, form=0)


def _page_box(p: fitz.Page) -> fitz.Rect:
    """회전 전 쪽 좌표의 쪽 네모."""
    return fitz.Rect(0, 0, p.cropbox.width, p.cropbox.height)


def _clamp(box: fitz.Rect, page_box: fitz.Rect) -> tuple[float, float]:
    """box 를 쪽 안으로 당길 (dx, dy)."""
    dx = dy = 0.0
    if box.x1 > page_box.x1:
        dx = page_box.x1 - box.x1
    if box.x0 + dx < page_box.x0:
        dx = page_box.x0 - box.x0
    if box.y1 > page_box.y1:
        dy = page_box.y1 - box.y1
    if box.y0 + dy < page_box.y0:
        dy = page_box.y0 - box.y0
    return dx, dy


def _new_image_xref(f: fitz.Document, p: fitz.Page, s: dict) -> int:
    """그림 바이트로 새 그림 객체를 만든다(insert_image 가 붙인 그리는 줄은 바로 비움)."""
    before = set(p.get_contents())
    kw = {"mask": s["mask"]} if s.get("mask") else {}
    xref = p.insert_image(fitz.Rect(0, 0, 1, 1), stream=s["image"], overlay=True, keep_proportion=False, **kw)
    for cx in p.get_contents():
        if cx not in before:
            f.update_stream(cx, b" ")
    return xref


def paste(doc: Document, page: int, specs: Sequence[dict], dx: float = 0, dy: float = 0) -> list[int]:
    """복사 자료를 쪽에 붙인다(dx·dy 는 쪽 좌표로 비킬 만큼, 묶음이 쪽 밖이면 안으로 당김). 새 객체 Id 들
    (글 객체를 쓰지 못한 자리는 None). 실행 취소 한 걸음."""
    specs = list(specs)
    if not specs:
        return []
    with doc.lock, _one_step(doc):
        f = doc.fitz
        p = f[page]
        box = None
        for s in specs:
            r = fitz.Rect(s["rect"]) + (dx, dy, dx, dy)
            box = r if box is None else box | r
        cx_, cy_ = _clamp(box, _page_box(p))
        dx, dy = dx + cx_, dy + cy_
        out: list[int | None] = []
        for s in specs:
            p = f[page]
            if s["kind"] == "image" or (s["kind"] == "text" and s.get("form") and s.get("doc") == id(doc)
                                        and _form_ok(f, s["form"])):
                out.append(_paste_line(doc, page, s, dx, dy))
            else:
                out.append(_paste_text(doc, page, s, dx, dy))
        return out


def _form_ok(f: fitz.Document, xref: int) -> bool:
    return 0 < xref < f.xref_length() and f.xref_get_key(xref, "Subtype")[1] == "/Form"


def _paste_line(doc: Document, page: int, s: dict, dx: float, dy: float) -> int:
    f = doc.fitz
    p = f[page]
    cx, _ = _consolidate(f, p)
    if s["kind"] == "image":
        xref = s.get("xref", 0)
        same = s.get("doc") == id(doc) and 0 < xref < f.xref_length() and \
            f.xref_get_key(xref, "Subtype")[1] == "/Image"
        if not same:
            xref = _new_image_xref(f, p, s)
            cx, _ = _consolidate(f, p)
        name = _put_xobject(f, p, xref, "RPDFIm")
        gs = _ensure_gs(f, p, s.get("opacity", 1.0))
        k = "I"
    else:
        name = _put_xobject(f, p, s["form"], "RPDFFrm")
        gs, k = None, "T"
    tm = _tm(p)
    m = fitz.Matrix(s["matrix"]) * fitz.Matrix(1, 0, 0, 1, dx, dy) * ~tm
    data = f.xref_stream(cx) or b""
    segs, _ = _parse(data)
    oid = _next_id(segs)
    _write(f, p, cx, data.rstrip() + b"\n" + _line(oid, k, m, gs, name) + b"\n")
    return oid


# 글 자료를 새 글 객체로 쓰는 길. 2단계에서 textbox 로 잇는다(없으면 annots.add_text).
TEXT_WRITER = None


def _paste_text(doc: Document, page: int, s: dict, dx: float, dy: float) -> int | None:
    st = dict(s.get("style") or {})
    r = fitz.Rect(s["rect"]) + (dx, dy, dx, dy)
    text = s.get("text") or st.get("text", "")
    if TEXT_WRITER is not None:
        return TEXT_WRITER(doc, page, r, text, st)
    from . import annots as A
    p = doc.fitz[page]
    before = {o["id"] for o in _ours(doc.fitz, p)[3]}
    kw = {}
    if "italic" in st:
        kw["italic"] = bool(st["italic"])
    got = A.add_text(doc, page, r, text, st.get("fontsize", 10), tuple(st.get("color") or (0, 0, 0)),
                     bool(st.get("bold")), int(st.get("align", 0) or 0), **kw)
    if isinstance(got, int) and not isinstance(got, bool):
        return got
    new = [o["id"] for o in _ours(doc.fitz, doc.fitz[page])[3] if o["id"] not in before]
    return new[-1] if new else None


# ── 옮기기 · 지우기 ──────────────────────────────────────────────
def _set_matrix(doc: Document, page: int, oid: int, fn) -> None:
    """우리 객체 줄의 행렬을 그 자리에서 바꾼다(fn: 쪽 좌표 행렬 → 새 쪽 좌표 행렬)."""
    f = doc.fitz
    p = f[page]
    cx, data = _consolidate(f, p)
    data, segs, _, ours = _ours(f, p, data)
    o = next((o for o in ours if o["id"] == oid), None)
    if o is None or o["matrix"] is None:
        raise ValueError(f"객체를 찾을 수 없습니다 (Id {oid})")
    seg = segs[o["seg"]]
    tm = _tm(p)
    m = fn(o["matrix"] * tm) * ~tm
    line = _line(oid, "I" if o["kind"] == "image" else "T", m, o["gs"], o["name"])
    _write(f, p, cx, data[:seg["s"]] + line + data[seg["e"]:])


def move(doc: Document, page: int, refs: Iterable, dx: float, dy: float) -> list[int]:
    """객체들을 쪽 좌표로 dx·dy 만큼 옮긴다(원문은 들어올린 뒤). 쌓는 순서는 그대로. 새 Id 들. 한 걸음."""
    items = [_resolve(doc, page, r) for r in refs]
    out = []
    with doc.lock, _one_step(doc):
        for it in items:
            if it["ours"]:
                oid = it["id"]
            elif it["kind"] == "text":
                out.append(_lift_text(doc, page, it, fitz.Rect(it["rect"]) + (dx, dy, dx, dy)))
                continue
            else:
                oid = _lift_image(doc, page, it, None)
            _set_matrix(doc, page, oid, lambda m: m * fitz.Matrix(1, 0, 0, 1, dx, dy))
            out.append(oid)
    return out


def delete(doc: Document, page: int, refs: Iterable) -> int:
    """객체들을 지운다. 우리 객체는 줄을 통째로 들어내고(교정하지 않음), 원문 그림은 그 그리기 한 줄만,
    원문 글은 그 자리 글만 교정으로 지운다. 지운 수. 한 걸음."""
    items = [_resolve(doc, page, r) for r in refs]
    if not items:
        return 0
    n = 0
    with doc.lock, _one_step(doc):
        f = doc.fitz
        ids = {it["id"] for it in items if it["ours"]}
        imgs = [it for it in items if not it["ours"] and it["kind"] == "image"]
        texts = [it for it in items if not it["ours"] and it["kind"] == "text"]
        p = f[page]
        cx, data = _consolidate(f, p)
        segs, dos = _parse(data)
        cut = [(s["s"], s["e"]) for s in segs if s["t"] == "obj" and s["id"] in ids]
        for it in imgs:
            _, occ = _image_occurrence(f, p, it, data, segs, dos)
            cut.append((occ["s"], occ["e"]))
        for a, b in sorted(cut, reverse=True):
            data = data[:a] + b" " + data[b:]
        n += len(cut)
        _write(f, p, cx, data)
        if texts:
            redact_only(f[page], [fitz.Rect(t["rect"]) for t in texts], fill=None,
                        images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            n += len(texts)
    return n


# ── 맞춤 · 분배(순수 계산) ─────────────────────────────────────────
def arrange(rects: Sequence[fitz.Rect], how: str, page_rect: fitz.Rect | None = None) -> list[fitz.Rect]:
    """네모 목록을 맞추거나 분배한 새 네모 목록(같은 차례). 규칙은 양식 칸 맞춤(tools.align_widgets)과 같다.

    how: left·right·top·bottom·hcenter·vcenter(고른 것을 다 합친 네모 기준) · page_h·page_v·page_both(page_rect 가운데)
    · dist_h·dist_v(같은 간격, 셋 이상). 좌표계는 부르는 쪽 것을 그대로(보이는 화면 좌표를 주면 그 기준)."""
    if how not in ALIGN_HOWS:
        raise ValueError(f"how: {how}")
    rects = [fitz.Rect(r).normalize() for r in rects]
    if not rects:
        return []
    u = fitz.Rect(rects[0])
    for r in rects[1:]:
        u |= r
    new = [fitz.Rect(r) for r in rects]
    if how in ("dist_h", "dist_v"):
        if len(rects) < 3:
            raise ValueError("같은 간격으로 벌리려면 셋 이상 고르세요")
        a = 0 if how == "dist_h" else 1
        seq = sorted(range(len(rects)), key=lambda k: (rects[k].x0, rects[k].y0)[a])
        size = sum((rects[k].width, rects[k].height)[a] for k in seq)
        gap = ((u.width, u.height)[a] - size) / (len(seq) - 1)
        pos = (u.x0, u.y0)[a]
        for k in seq:
            r = new[k]
            if a == 0:
                r.x1, r.x0 = pos + r.width, pos
            else:
                r.y1, r.y0 = pos + r.height, pos
            pos += (r.width, r.height)[a] + gap
        return new
    if how.startswith("page") and page_rect is None:
        raise ValueError("쪽 가운데 맞춤에는 page_rect 가 있어야 합니다")
    pr = fitz.Rect(page_rect) if page_rect is not None else None
    for r in new:
        w_, h_ = r.width, r.height
        if how == "left":
            r.x0 = u.x0
        elif how == "right":
            r.x0 = u.x1 - w_
        elif how == "top":
            r.y0 = u.y0
        elif how == "bottom":
            r.y0 = u.y1 - h_
        elif how == "hcenter":
            r.x0 = (u.x0 + u.x1 - w_) / 2
        elif how == "vcenter":
            r.y0 = (u.y0 + u.y1 - h_) / 2
        if how in ("page_h", "page_both"):
            r.x0 = pr.x0 + (pr.width - w_) / 2
        if how in ("page_v", "page_both"):
            r.y0 = pr.y0 + (pr.height - h_) / 2
        r.x1, r.y1 = r.x0 + w_, r.y0 + h_
    return new
