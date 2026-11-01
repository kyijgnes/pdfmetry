# -*- coding: utf-8 -*-
"""머리글/바닥글 · 페이지 번호 · 베이츠 번호 그리기.

페이지 번호는 머리글/바닥글 한 칸에 번호 매크로 하나만 든 것이라 같은 그리기(header_footer)로 그린다.
설정 꼴은 deco.upgrade_settings 의 새 꼴(v 2)이고, 옛 인자({page} 자리표·margin_mm·font_size 등)도 받는다.

글 안의 매크로는 <<…>> 로 감싼다. 번호(##1## 지금 번호, ##n## 마지막 번호)는 쪽마다 풀고, 날짜(<<m/d>> 등
DATE_FORMATS)는 설정의 「date」(처음 넣은 날, ISO 글)로 풀어 업데이트해도 날짜가 바뀌지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import re

import fitz

from .deco import CELLS6, draw_text_box, expand_macros, upgrade_settings
from .tools import EngineError, _pages

HF_SLOTS = CELLS6   # 머리글/바닥글 여섯 자리(위 셋 · 아래 셋)
# 페이지 번호 스타일(화면 이름, 글에 넣는 매크로). 처음 고른 값은 첫 항목
PAGE_NUMBER_STYLES = [("1", "<<##1##>>"), ("n", "<<##n##>>"), ("1 of n", "<<##1##of##n##>>"),
                      ("1/n", "<<##1##/##n##>>"), ("Page 1", "<<Page##1##>>"), ("Page 1 of n", "<<Page##1##of##n##>>")]
# 날짜 형식 26가지. m·d 는 앞 0 없음, mm·dd 는 두 자리, y 는 글자 수만큼의 연도(yy 두 자리, yyy 세 자리)
DATE_FORMATS = ("m/d", "m/d/yy", "m/d/yyyy", "mm/dd/yy", "mm/dd/yyyy", "d/m/yy", "d/m/yyyy", "dd/mm/yy",
                "dd/mm/yyyy", "mm/yy", "mm/yyyy",
                "m.d.yy", "m.d.yyyy", "mm.dd.yy", "mm.dd.yyyy", "mm.yy", "mm.yyy", "d.m.yy", "d.m.yyyy",
                "dd.mm.yy", "dd.mm.yyyy",
                "yy-mm-dd", "yyyy-mm-dd", "d", "m", "yyyy")
# 머리글/바닥글·페이지 번호 여백 기본값(pt): 위아래 0.5in, 좌우 1in
HF_MARGINS = {"top": 36.0, "bottom": 36.0, "left": 72.0, "right": 72.0}
MAX_SLOT_CHARS = 512


def format_date(fmt: str, day: _dt.date) -> str:
    """날짜 형식(m/d 등) → 글."""
    def one(m: re.Match) -> str:
        t = m.group(0)
        if t[0] == "y":
            return f"{day.year % 10 ** len(t):0{len(t)}d}" if len(t) < 4 else str(day.year)
        v = day.month if t[0] == "m" else day.day
        return f"{v:02d}" if len(t) > 1 else str(v)
    return re.sub(r"y+|m+|d+", one, fmt)


_DATE_MACRO = re.compile(r"<<(" + "|".join(re.escape(f) for f in sorted(DATE_FORMATS, key=len, reverse=True)) + r")>>")


def expand_dates(s: str, day: _dt.date) -> str:
    """글 안의 날짜 매크로(<<m/d>> 등)를 day 로 푼다. 목록에 없는 <<…>> 는 그대로 둔다."""
    return _DATE_MACRO.sub(lambda m: format_date(m.group(1), day), s or "")


def _day(s: dict) -> _dt.date:
    try:
        return _dt.date.fromisoformat(s.get("date") or "")
    except ValueError:
        return _dt.date.today()


def insert_macro(text: str, pos: int, macro: str) -> tuple[str, int]:
    """글 text 의 pos 자리에 매크로를 끼운다. 바로 앞이 >> 로 끝나면 빈칸 하나를 둔다. (새 글, 새 커서 자리)."""
    add = (" " if text[:pos].endswith(">>") else "") + macro
    return text[:pos] + add + text[pos:], pos + len(add)


def header_footer(doc: fitz.Document, pages=None, total: int | None = None, **settings) -> None:
    """머리글·바닥글 여섯 자리(slots: 자리 → 글). 글 안의 번호·날짜 매크로를 쪽마다 푼다. in place.

    n(마지막 번호)은 (적용할 쪽 수 + 첫 번호 - 1)이다. total 로 따로 줄 수 있다."""
    s = upgrade_settings("header_footer", settings)
    st = int(s.get("start_number", 1))
    targets = _pages(doc, pages)
    if total is None:
        total = len(targets) + st - 1
    day = _day(s)
    slots = {k: expand_dates(v, day) for k, v in (s.get("slots") or {}).items() if v}
    margins = s.get("margins") or HF_MARGINS
    for k, pno in enumerate(targets):
        page = doc[pno]
        for cell, raw in slots.items():
            draw_text_box(page, expand_macros(raw, st + k, total), s.get("font"), cell, margins=margins)


def page_numbers(doc: fitz.Document, pages=None, total: int | None = None, **settings) -> None:
    """페이지 번호. style 은 번호 매크로 글, position 은 HF_SLOTS 가운데 하나. in place."""
    s = upgrade_settings("page_number", settings)
    if s["position"] not in HF_SLOTS:
        raise EngineError(f"페이지 번호 자리가 잘못됐습니다: {s['position']}")
    header_footer(doc, pages=pages, total=total, **page_number_as_header(s))


def page_number_as_header(settings: dict) -> dict:
    """페이지 번호 설정 → 같은 그림의 머리글/바닥글 설정(머리글 창에서 고칠 때)."""
    s = upgrade_settings("page_number", settings)
    out = {"v": s["v"], "slots": {s["position"]: s["style"]}, "start_number": s.get("start", 1),
           "font": s.get("font"), "margins": s.get("margins") or HF_MARGINS}
    if "pages" in s:
        out["pages"] = s["pages"]
    return out


def bates_order(settings: dict, targets: list[int]) -> tuple[list[int], int]:
    """베이츠 번호를 셀 차례와 센 쪽 수. 기본은 범위 밖 쪽도 번호를 하나씩 먹는다(1·3쪽이면 0·2 차례, 셋).
    「marked_only」면 넣는 쪽끼리만 센다(0·1 차례, 둘). 차례는 targets 와 같은 줄 순서로 돌려준다."""
    if not targets:
        return [], 0
    if settings.get("marked_only"):
        rank = {p: k for k, p in enumerate(sorted(set(targets)))}
        return [rank[p] for p in targets], len(rank)
    first = min(targets)
    return [p - first for p in targets], max(targets) - first + 1


def bates_text(settings: dict, number: int) -> str:
    """접두사 + 0 채운 번호 + 접미사."""
    return f"{settings.get('prefix', '')}{number:0{int(settings.get('digits', 1))}d}{settings.get('suffix', '')}"


def bates_numbers(doc: fitz.Document, pages=None, **settings) -> None:
    """베이츠 번호(접두사 + 0 채운 번호 + 접미사). in place.

    reverse: 마지막 쪽이 start 이고 앞으로 갈수록 커진다(「베이츠 번호 거꾸로」).
    marked_only: 꺼져 있으면 범위 안 사이에 끼인 쪽(넣지 않는 쪽)도 번호를 하나씩 먹는다."""
    s = upgrade_settings("bates", settings)
    targets = sorted(set(_pages(doc, pages)))
    st, rev = int(s["start"]), bool(s.get("reverse"))
    order, n = bates_order(s, targets)
    for o, pno in zip(order, targets):
        no = st + (n - 1 - o if rev else o)
        draw_text_box(doc[pno], bates_text(s, no), s.get("font"), s["position"],
                      rotate=float(s.get("rotate", 0)), opacity=float(s.get("opacity", 1)), margins=s.get("margins"))
