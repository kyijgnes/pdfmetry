"""왼쪽 패널 엔진 도우미: 북마크 모양 · 북마크로 목차 쪽 만들기 · 레이어 이름/삭제/속성/처음 상태 · 쪽 복사 붙여넣기."""
from __future__ import annotations

import math
import re

import fitz

from . import deco as DC
from .document import Document

# ── 북마크 모양 ──────────────────────────────────────────────────────
STYLE_DEFAULT = {"bold": False, "italic": False, "color": "#000000"}


def hex_color(c) -> str:
    """(r, g, b) 0~1 또는 "#RRGGBB" → "#RRGGBB"."""
    if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        return c.upper()
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return "#" + "".join(f"{max(0, min(255, round(float(v) * 255))):02X}" for v in c[:3])
    return "#000000"


def rgb(c: str) -> tuple[float, float, float]:
    c = hex_color(c)
    return tuple(int(c[k:k + 2], 16) / 255 for k in (1, 3, 5))


def style_of(dest: dict | None) -> dict:
    """북마크 dest(get_toc 넷째 칸) → {bold, italic, color "#RRGGBB"}."""
    d = dest or {}
    return {"bold": bool(d.get("bold")), "italic": bool(d.get("italic")),
            "color": hex_color(d.get("color")) if d.get("color") is not None else "#000000"}


def norm_style(st: dict | None) -> dict:
    s = {**STYLE_DEFAULT, **(st or {})}
    return {"bold": bool(s["bold"]), "italic": bool(s["italic"]), "color": hex_color(s["color"])}


def with_style(dest: dict | None, st: dict) -> dict | None:
    """dest 에 모양을 얹는다. 보통 모양·검정이면 모양 칸을 빼고, 남는 것이 없으면 None(예전처럼 쪽 맨 위)."""
    st = norm_style(st)
    d = {k: v for k, v in (dest or {}).items() if k not in ("bold", "italic", "color")}
    if st["bold"]:
        d["bold"] = True
    if st["italic"]:
        d["italic"] = True
    if st["color"] != "#000000":
        d["color"] = rgb(st["color"])
    if not d:
        return None
    d.setdefault("kind", fitz.LINK_GOTO)   # 모양만 있고 자리가 없으면 쪽 맨 위(set_toc 이 kind 를 봐야 dest 를 씀)
    if "to" not in d:
        d["to"] = fitz.Point(0, 0)
    return d


# ── 북마크로 목차 쪽 만들기 ─────────────────────────────────────────────
TOC_DEFAULT = {"name": "", "size": 12.0, "bold": False, "italic": False, "color": "#000000", "title": "목차"}


def make_toc_pages(doc: Document, entries: list[list], font: dict | None = None) -> int:
    """북마크 목록(평평한 [단계, 제목, 1부터 쪽, …])으로 맨 앞에 목차 쪽을 끼우고 줄마다 그 쪽 링크를 단다.
    쪽이 넘치면 여러 쪽. 끼운 쪽 수를 돌려준다(되돌리기 한 걸음)."""
    f = {**TOC_DEFAULT, **(font or {})}
    rows = [(int(e[0]), str(e[1]), int(e[2]) - 1) for e in entries if len(e) > 2 and int(e[2]) > 0]
    if not rows:
        raise ValueError("목차로 만들 북마크가 없습니다")
    size = float(f["size"])
    body = {"name": f["name"], "size": size, "bold": f["bold"], "italic": f["italic"], "color": rgb(f["color"])}
    head = {**body, "size": size * 1.6, "bold": True}
    first = doc.fitz[0].rect if doc.page_count else fitz.Rect(0, 0, 595, 842)
    W, H = first.width, first.height
    margin = 56.0
    lh = size * 1.7
    top = margin + size * 1.6 * 2
    per = max(1, int((H - top - margin) // lh))
    n = math.ceil(len(rows) / per)
    doc.snapshot()
    with doc.lock:
        for k in range(n):
            doc.fitz.new_page(pno=k, width=W, height=H)
        for k in range(n):
            page = doc.fitz[k]
            if k == 0 and f["title"]:
                tw, th = DC.text_box_size(f["title"], head)
                DC.draw_text_box(page, f["title"], head, anchor=fitz.Point(margin + tw / 2, margin + th / 2))
            for j, (lvl, title, pg) in enumerate(rows[k * per:(k + 1) * per]):
                target = pg + n
                y = top + j * lh
                num = str(target + 1)
                nw, nh = DC.text_box_size(num, body)
                x0 = margin + (max(1, lvl) - 1) * size * 1.5
                room = W - margin - nw - size - x0
                text = title
                tw, th = DC.text_box_size(text, body)
                while tw > room and len(text) > 1:   # 쪽 번호와 겹치지 않게 끝을 … 로 줄임
                    text = text[:-2] + "…"
                    tw, th = DC.text_box_size(text, body)
                DC.draw_text_box(page, text, body, anchor=fitz.Point(x0 + tw / 2, y + th / 2))
                DC.draw_text_box(page, num, body, anchor=fitz.Point(W - margin - nw / 2, y + nh / 2))
                page.insert_link({"kind": fitz.LINK_GOTO, "page": target, "to": fitz.Point(0, 0),
                                  "from": fitz.Rect(x0, y, W - margin, y + max(th, nh))})
    return n


# ── 레이어 ──────────────────────────────────────────────────────────
_REF = re.compile(r"(\d+) 0 R")


def _ocprops(f: fitz.Document) -> tuple[str, int | None, str]:
    """(종류, 간접 개체 번호, 글자) — OCProperties 가 카탈로그 안 사전이면 번호 None."""
    kind, val = f.xref_get_key(f.pdf_catalog(), "OCProperties")
    if kind == "xref":
        x = int(val.split()[0])
        return "xref", x, f.xref_object(x, compressed=True)
    return kind, None, val


def ocg_order(f: fitz.Document) -> list[int]:
    """화면 목록 차례(/D /Order 를 따라 간 것, 없으면 /OCGs 차례)의 레이어 개체 번호."""
    try:
        _, _, txt = _ocprops(f)
    except Exception:  # noqa: BLE001
        return []
    m = re.search(r"/Order\s*\[", txt)
    if m:
        depth, k = 1, m.end()
        while k < len(txt) and depth:
            depth += {"[": 1, "]": -1}.get(txt[k], 0)
            k += 1
        refs = [int(x) for x in _REF.findall(txt[m.end():k])]
    else:
        refs = []
    if not refs:
        refs = list(f.get_ocgs().keys())
    seen, out = set(), []
    for x in refs:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def layer_rows(doc: Document) -> list[dict]:
    """doc.layers() 에 개체 번호(xref)·상위 꺼짐(parent_off) 을 더한 것."""
    rows = doc.layers()
    with doc.lock:
        order = ocg_order(doc.fitz)
        ocgs = doc.fitz.get_ocgs()
    by_name: dict[str, list[int]] = {}
    for x, v in ocgs.items():
        by_name.setdefault(v["name"], []).append(x)
    aligned = len(order) == len(rows) and all(ocgs.get(x, {}).get("name") == r["name"] for x, r in zip(order, rows))
    used: set[int] = set()
    stack: list[dict] = []
    for k, r in enumerate(rows):
        if aligned:
            r["xref"] = order[k]
        else:
            r["xref"] = next((x for x in by_name.get(r["name"], []) if x not in used), None)
        if r["xref"] is not None:
            used.add(r["xref"])
        while stack and stack[-1]["depth"] >= r["depth"]:
            stack.pop()
        r["parent_off"] = any(not s["on"] or s["parent_off"] for s in stack)
        stack.append(r)
    return rows


def page_layers(doc: Document, pno: int) -> set[int]:
    """그 쪽이 쓰는 레이어 개체 번호(쪽 자원의 /Properties 와 주석 /OC)."""
    out: set[int] = set()
    with doc.lock:
        f = doc.fitz
        page = f[pno]
        kind, val = f.xref_get_key(page.xref, "Resources/Properties")
        if kind == "xref":
            val = f.xref_object(int(val.split()[0]), compressed=True)
        out |= {int(x) for x in _REF.findall(val or "")}
        for a in page.annots() or []:
            k, v = f.xref_get_key(a.xref, "OC")
            if k == "xref":
                out.add(int(v.split()[0]))
        ocgs = set(f.get_ocgs().keys())
    return out & ocgs


def _reload(doc: Document):
    """MuPDF 는 레이어 목록을 열 때 한 번 읽어 둔다. 고친 레이어 정의가 목록·그림에 보이게 다시 연다."""
    was = doc.modified
    with doc.lock:
        doc._swap(doc.fitz.tobytes(garbage=0, deflate=False, encryption=fitz.PDF_ENCRYPT_KEEP))
    doc.modified = was


def rename_layer(doc: Document, xref: int, name: str):
    name = name.strip()
    if not name:
        raise ValueError("레이어 이름이 비었습니다")
    doc.snapshot()
    with doc.lock:
        old = doc.fitz.get_ocgs().get(xref, {}).get("name")
        doc.fitz.xref_set_key(xref, "Name", fitz.get_pdf_str(name))
        hidden = getattr(doc, "hidden_layers", None)
        if hidden is not None and old in hidden:
            hidden.discard(old); hidden.add(name)
    _reload(doc); doc.modified = True


def delete_layer(doc: Document, xref: int):
    """레이어 정의를 목록에서 뺀다(그 레이어로 묶였던 내용은 늘 보인다)."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        name = f.get_ocgs().get(xref, {}).get("name")
        kind, x, txt = _ocprops(f)
        new = re.sub(rf"(?<!\d){xref} 0 R", "", txt)
        if x is None:
            f.xref_set_key(f.pdf_catalog(), "OCProperties", new)
        else:
            f.update_object(x, new)
        hidden = getattr(doc, "hidden_layers", None)
        if hidden is not None and name in hidden and name not in {v["name"] for v in f.get_ocgs().values()}:
            hidden.discard(name)
    _reload(doc); doc.modified = True


_USAGE = (("view", "View", "ViewState"), ("print", "Print", "PrintState"), ("export", "Export", "ExportState"))
USAGE_NAMES = (("when_on", "켜져 있을 때"), ("never", "안 함"), ("always", "항상"))


def layer_props(doc: Document, xref: int) -> dict:
    """속성 창 칸: 이름 · 쓰임(View/Design) · 처음 상태 · 잠금 · 보이기/인쇄/내보내기 처음 상태."""
    with doc.lock:
        f = doc.fitz
        info = f.get_ocgs().get(xref, {})
        base = f.get_layer(-1)
        out = {"name": info.get("name", ""), "intent": "design" if "Design" in (info.get("intent") or []) else "view",
               "on": xref not in (base.get("off") or []), "locked": xref in (base.get("locked") or [])}
        for key, sub, state in _USAGE:
            k, v = f.xref_get_key(xref, f"Usage/{sub}/{state}")
            out[key] = {"/ON": "always", "/OFF": "never"}.get(v if k == "name" else "", "when_on")
    return out


def set_layer_props(doc: Document, xref: int, props: dict):
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        if props.get("name", "").strip():
            old = f.get_ocgs().get(xref, {}).get("name")
            f.xref_set_key(xref, "Name", fitz.get_pdf_str(props["name"].strip()))
            hidden = getattr(doc, "hidden_layers", None)
            if hidden is not None and old in hidden:
                hidden.discard(old); hidden.add(props["name"].strip())
        f.xref_set_key(xref, "Intent", "[/Design]" if props.get("intent") == "design" else "[/View]")
        base = f.get_layer(-1)
        on = [x for x in base.get("on") or [] if x != xref]
        off = [x for x in base.get("off") or [] if x != xref]
        locked = [x for x in base.get("locked") or [] if x != xref]
        (on if props.get("on", True) else off).append(xref)
        if props.get("locked"):
            locked.append(xref)
        f.set_layer(-1, on=on, off=off, locked=locked)
        for key, sub, state in _USAGE:
            v = {"always": "/ON", "never": "/OFF"}.get(props.get(key, "when_on"))
            if v:
                f.xref_set_key(xref, f"Usage/{sub}/{state}", v)
            else:
                f.xref_set_key(xref, f"Usage/{sub}", "null")
    _reload(doc); doc.modified = True


def set_layer_locked(doc: Document, xref: int, locked: bool):
    p = layer_props(doc, xref)
    p["locked"] = locked
    set_layer_props(doc, xref, p)


def reset_layers(doc: Document):
    """화면에서 켜고 끈 것을 버리고 파일에 적힌 처음 상태로(문서는 안 바뀜)."""
    hidden = getattr(doc, "hidden_layers", None)
    if hidden is not None:
        hidden.clear()
    _reload(doc)


# ── 쪽 복사 · 붙여넣기 · 다른 문서에서 끌어 놓기 ───────────────────────────
def pages_bytes(doc: Document, pages: list[int]) -> bytes:
    """고른 쪽만 담은 PDF 바이트(쪽 클립보드)."""
    out = fitz.open()
    with doc.lock:
        for i in sorted(set(pages)):
            out.insert_pdf(doc.fitz, from_page=i, to_page=i)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def insert_pages(doc: Document, at: int, src: fitz.Document | bytes, pages: list[int] | None = None) -> int:
    """다른 문서(열린 fitz 문서 또는 PDF 바이트)의 쪽을 at 자리에 끼운다. 끼운 쪽 수(되돌리기 한 걸음)."""
    own = isinstance(src, (bytes, bytearray))
    s = fitz.open("pdf", bytes(src)) if own else src
    try:
        pg = list(range(s.page_count)) if pages is None else sorted(set(pages))
        if not pg:
            return 0
        doc.snapshot()
        doc.dedupe = True
        at = max(0, min(at, doc.page_count))
        with doc.lock:
            for k, i in enumerate(pg):
                doc.fitz.insert_pdf(s, from_page=i, to_page=i, start_at=at + k)
        return len(pg)
    finally:
        if own:
            s.close()
