# -*- coding: utf-8 -*-
"""머리글/바닥글 · 페이지 번호 · 베이츠 번호 그리기.

페이지 번호는 머리글/바닥글 한 칸에 번호 매크로 하나만 든 것이라 같은 그리기(header_footer)로 그린다.
설정 꼴은 deco.upgrade_settings 의 새 꼴(v 2)이고, 옛 인자({page} 자리표·margin_mm·font_size 등)도 받는다.
"""
from __future__ import annotations

import fitz

from .deco import CELLS6, draw_text_box, expand_macros, upgrade_settings
from .tools import EngineError, _pages

HF_SLOTS = CELLS6   # 머리글/바닥글 여섯 자리(위 셋 · 아래 셋)
# 페이지 번호 모양(화면 이름, 머리글 글에 넣는 매크로)
PAGE_NUMBER_STYLES = [("1", "<<##1##>>"), ("1 / n", "<<##1##>> / <<##n##>>"), ("- 1 -", "- <<##1##>> -"),
                      ("페이지 1", "<<Page##1##>>"), ("n 페이지 중 1", "<<Page##1##of##n##>>")]


def header_footer(doc: fitz.Document, pages=None, total: int | None = None, **settings) -> None:
    """머리글·바닥글 여섯 자리(slots: 자리 → 글). 글 안의 번호 매크로를 쪽마다 푼다. in place.

    n(마지막 번호)은 (적용할 쪽 수 + 첫 번호 - 1)이다. total 로 따로 줄 수 있다."""
    s = upgrade_settings("header_footer", settings)
    st = int(s.get("start_number", 1))
    targets = _pages(doc, pages)
    if total is None:
        total = len(targets) + st - 1
    slots = {k: v for k, v in (s.get("slots") or {}).items() if v}
    for k, pno in enumerate(targets):
        page = doc[pno]
        for cell, raw in slots.items():
            draw_text_box(page, expand_macros(raw, st + k, total), s.get("font"), cell, margins=s.get("margins"))


def page_numbers(doc: fitz.Document, pages=None, total: int | None = None, **settings) -> None:
    """페이지 번호. style 은 번호 매크로 글, position 은 HF_SLOTS 가운데 하나. in place."""
    s = upgrade_settings("page_number", settings)
    if s["position"] not in HF_SLOTS:
        raise EngineError(f"페이지 번호 자리가 잘못됐습니다: {s['position']}")
    header_footer(doc, pages=pages, total=total, v=s["v"], slots={s["position"]: s["style"]},
                  start_number=s.get("start", 1), font=s.get("font"), margins=s.get("margins"))


def page_number_as_header(settings: dict) -> dict:
    """페이지 번호 설정 → 같은 그림의 머리글/바닥글 설정(머리글 창에서 고칠 때)."""
    s = upgrade_settings("page_number", settings)
    out = {"v": s["v"], "slots": {s["position"]: s["style"]}, "start_number": s.get("start", 1),
           "font": s.get("font"), "margins": s.get("margins")}
    if "pages" in s:
        out["pages"] = s["pages"]
    return out


def bates_numbers(doc: fitz.Document, pages=None, **settings) -> None:
    """베이츠 번호(접두어 + 0 채운 번호 + 접미어). in place.

    reverse: 마지막 쪽이 start 이고 앞으로 갈수록 커진다(「베이츠 번호 거꾸로」)."""
    s = upgrade_settings("bates", settings)
    targets = _pages(doc, pages)
    st, digits, rev = int(s["start"]), int(s["digits"]), bool(s.get("reverse"))
    for k, pno in enumerate(targets):
        no = st + (len(targets) - 1 - k if rev else k)
        draw_text_box(doc[pno], f"{s['prefix']}{no:0{digits}d}{s['suffix']}", s.get("font"), s["position"],
                      rotate=float(s.get("rotate", 0)), opacity=float(s.get("opacity", 1)), margins=s.get("margins"))
