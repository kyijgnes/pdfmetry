"""PDF 비교: 두 문서를 줄 단위로 대조해 결과 PDF 를 짓는다.

결과 쪽 하나에 왼쪽 이전 쪽 · 오른쪽 새 쪽을 나란히 담고, 달라진 곳을 삭제(빨강) · 삽입(파랑) · 수정(초록) 상자로 칠한다.
상자는 종류·대상(텍스트 · 이미지 · 페이지)마다 레이어에 그려, 화면에서 레이어를 끄면 그 상자만 숨는다(파일은 그대로).
"""
from __future__ import annotations

import difflib
import os
from typing import Callable

import fitz

from . import tools as T

Progress = Callable[[int, int, str], None] | None

KINDS = ("delete", "insert", "modify")
KIND_NAMES = {"delete": "삭제", "insert": "삽입", "modify": "수정"}
WHATS = ("text", "image", "page")
WHAT_NAMES = {"text": "텍스트 줄", "image": "이미지", "page": "페이지"}
# 선 색 · 채움 색(0~1) · 채움 진하기
LINE = {"delete": (236, 119, 119), "insert": (102, 116, 222), "modify": (140, 203, 134)}
FILL = {"delete": (255, 0, 0), "insert": (0, 30, 255), "modify": (22, 255, 0)}
FILL_OPACITY = 38 / 255
GAP = 24.0      # 두 쪽 사이
HEAD = 26.0     # 쪽 위 이름 줄
NO_DIFF = "두 문서에는 차이가 없습니다."


def rgb01(c: tuple[int, int, int]) -> tuple[float, float, float]:
    return tuple(v / 255 for v in c)


def layer_name(kind: str, what: str) -> str:
    return f"비교 {KIND_NAMES[kind]} {WHAT_NAMES[what]}"


def _lines(page: fitz.Page) -> list[tuple[str, fitz.Rect]]:
    """쪽의 글자 줄(빈칸을 하나로 줄인 글, 줄 상자). 위에서 아래 차례."""
    out = []
    for b in page.get_text("dict", sort=True).get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            text = " ".join("".join(sp.get("text", "") for sp in ln.get("spans", [])).split())
            if text:
                out.append((text, fitz.Rect(ln["bbox"])))
    return out


def _images(page: fitz.Page) -> list[tuple[bytes, fitz.Rect]]:
    out = []
    try:
        infos = page.get_image_info(hashes=True)
    except Exception:  # noqa: BLE001
        return out
    for info in infos:
        r = fitz.Rect(info.get("bbox"))
        if r.is_empty or r.is_infinite:
            continue
        out.append((info.get("digest") or b"", r))
    return out


def _overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    i = fitz.Rect(a) & b
    if i.is_empty:
        return 0.0
    return i.get_area() / max(1e-6, min(a.get_area(), b.get_area()))


def diff_pages(pa: fitz.Page | None, pb: fitz.Page | None) -> list[dict]:
    """한 쌍의 쪽 차이. 항목 {kind, what, rects_a, rects_b, text_a, text_b}(상자는 각 쪽 좌표)."""
    if pa is None and pb is None:
        return []
    if pb is None:
        return [{"kind": "delete", "what": "page", "rects_a": [pa.rect], "rects_b": [], "text_a": "", "text_b": ""}]
    if pa is None:
        return [{"kind": "insert", "what": "page", "rects_a": [], "rects_b": [pb.rect], "text_a": "", "text_b": ""}]
    items: list[dict] = []
    la, lb = _lines(pa), _lines(pb)
    if la or lb:
        sm = difflib.SequenceMatcher(a=[t for t, _ in la], b=[t for t, _ in lb], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            kind = {"delete": "delete", "insert": "insert"}.get(tag, "modify")
            items.append({"kind": kind, "what": "text", "rects_a": [r for _, r in la[i1:i2]],
                          "rects_b": [r for _, r in lb[j1:j2]],
                          "text_a": "\n".join(t for t, _ in la[i1:i2]), "text_b": "\n".join(t for t, _ in lb[j1:j2])})
    ia, ib = _images(pa), _images(pb)
    if not la and not lb:
        # 글자층 없는 쪽(스캔): 그림을 떠서 다른 칸을 수정으로
        if ia or ib:
            ra, rb = T._image_diff_rects(pa, pb)
            for x, y in zip(ra, rb):
                items.append({"kind": "modify", "what": "image", "rects_a": [fitz.Rect(x)], "rects_b": [fitz.Rect(y)],
                              "text_a": "", "text_b": ""})
        return items
    left = list(ia)
    rest_b = []
    for dg, r in ib:   # 같은 그림(지문)이 있으면 짝
        k = next((n for n, (d, _) in enumerate(left) if d == dg), None)
        if k is None:
            rest_b.append((dg, r))
            continue
        _, ra = left.pop(k)
        if abs(ra.x0 - r.x0) > 2 or abs(ra.y0 - r.y0) > 2 or abs(ra.width - r.width) > 2 or abs(ra.height - r.height) > 2:
            items.append({"kind": "modify", "what": "image", "rects_a": [ra], "rects_b": [r], "text_a": "", "text_b": ""})
    for dg, r in rest_b:   # 지문은 다르고 자리가 겹치면 바뀐 그림
        k = next((n for n, (_, ra) in enumerate(left) if _overlap(ra, r) > 0.5), None)
        if k is not None:
            _, ra = left.pop(k)
            items.append({"kind": "modify", "what": "image", "rects_a": [ra], "rects_b": [r], "text_a": "", "text_b": ""})
        else:
            items.append({"kind": "insert", "what": "image", "rects_a": [], "rects_b": [r], "text_a": "", "text_b": ""})
    for _, ra in left:
        items.append({"kind": "delete", "what": "image", "rects_a": [ra], "rects_b": [], "text_a": "", "text_b": ""})
    return items


def _label(page: fitz.Page, at: fitz.Point, text: str, font: str | None):
    try:
        if font:
            page.insert_text(at, text, fontsize=10, fontname="kr", fontfile=font, color=(0.35, 0.35, 0.35))
        else:
            page.insert_text(at, text, fontsize=10, color=(0.35, 0.35, 0.35))
    except Exception:  # noqa: BLE001
        pass


def build(doc_a: fitz.Document, doc_b: fitz.Document, out_path: str | None, name_a: str = "이전 파일",
          name_b: str = "새 파일", progress: Progress = None) -> dict:
    """비교 결과 PDF 를 out_path 에 쓰고 {"items": [...], "pages": 결과 쪽 수} 를 돌려준다.
    항목: {"id", "kind", "what", "page"(결과 쪽), "rects"(결과 쪽 좌표 [x0,y0,x1,y1] 목록), "text_a", "text_b",
    "page_a", "page_b"(원본 쪽, 0부터, 없으면 None)}. 차이가 없으면 items 가 비고 파일은 만들지 않는다."""
    pairs = T.match_pages(doc_a, doc_b, progress)
    n = len(pairs)
    found: list[tuple[int, int | None, int | None, list[dict]]] = []
    for k, (ia, ib) in enumerate(pairs):
        T._say(progress, k, n, f"비교하는 중 {k + 1}/{n}")
        pa = doc_a[ia] if ia is not None else None
        pb = doc_b[ib] if ib is not None else None
        found.append((k, ia, ib, diff_pages(pa, pb)))
    if not any(d for *_, d in found):
        T._say(progress, n, n, "비교 끝")
        return {"items": [], "pages": 0}
    out = fitz.open()
    try:
        font = T.korean_font_path()
    except Exception:  # noqa: BLE001
        font = None
    ocgs: dict[tuple[str, str], int] = {}

    def ocg(kind: str, what: str) -> int:
        if (kind, what) not in ocgs:
            ocgs[(kind, what)] = out.add_ocg(layer_name(kind, what), on=True)
        return ocgs[(kind, what)]

    items: list[dict] = []
    for k, ia, ib, diffs in found:
        pa = doc_a[ia] if ia is not None else None
        pb = doc_b[ib] if ib is not None else None
        ra = pa.rect if pa is not None else (pb.rect if pb is not None else fitz.Rect(0, 0, 595, 842))
        rb = pb.rect if pb is not None else ra
        W, H = ra.width + GAP + rb.width, HEAD + max(ra.height, rb.height)
        page = out.new_page(width=W, height=H)
        off_a, off_b = fitz.Point(0, HEAD), fitz.Point(ra.width + GAP, HEAD)
        if pa is not None:
            page.show_pdf_page(fitz.Rect(0, HEAD, ra.width, HEAD + ra.height), doc_a, ia)
            _label(page, fitz.Point(4, 17), f"{name_a} · {ia + 1}쪽", font)
        if pb is not None:
            page.show_pdf_page(fitz.Rect(off_b.x, HEAD, off_b.x + rb.width, HEAD + rb.height), doc_b, ib)
            _label(page, fitz.Point(off_b.x + 4, 17), f"{name_b} · {ib + 1}쪽", font)
        for d in diffs:
            boxes = [fitz.Rect(r) + (off_a.x, off_a.y, off_a.x, off_a.y) for r in d["rects_a"]]
            boxes += [fitz.Rect(r) + (off_b.x, off_b.y, off_b.x, off_b.y) for r in d["rects_b"]]
            x = ocg(d["kind"], d["what"])
            for r in boxes:
                page.draw_rect(r + (-1, -1, 1, 1), color=rgb01(LINE[d["kind"]]), fill=rgb01(FILL[d["kind"]]),
                               fill_opacity=FILL_OPACITY, width=1, oc=x)
            items.append({"id": len(items), "kind": d["kind"], "what": d["what"], "page": k,
                          "rects": [[r.x0, r.y0, r.x1, r.y1] for r in boxes], "text_a": d["text_a"], "text_b": d["text_b"],
                          "page_a": ia, "page_b": ib})
    if font:
        T._subset(out)
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        out.save(out_path, garbage=4, deflate=True)
    pages = out.page_count
    out.close()
    T._say(progress, n, n, "비교 끝")
    return {"items": items, "pages": pages}


def counts(items: list[dict]) -> dict[str, int]:
    return {k: sum(1 for it in items if it["kind"] == k) for k in KINDS}


def item_title(it: dict) -> str:
    """목록 한 줄: 「삭제텍스트 줄 · 3쪽」 꼴 대신 「삭제 · 텍스트 줄 · 3쪽」 과 바뀐 글 앞부분."""
    head = f"{KIND_NAMES[it['kind']]} · {WHAT_NAMES[it['what']]} · {it['page'] + 1}쪽"
    text = it.get("text_b") if it["kind"] == "insert" else it.get("text_a") or it.get("text_b")
    text = " ".join((text or "").split())
    if len(text) > 40:
        text = text[:39] + "…"
    return head + (f": {text}" if text else "")
