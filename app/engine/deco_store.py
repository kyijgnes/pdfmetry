# -*- coding: utf-8 -*-
"""꾸미기 템플릿 저장·읽기.

종류마다 data_dir()\\decorations\\<종류>.json 하나에 목록으로 둔다. 템플릿 하나는
{"id", "name", "settings"(엔진 새 꼴 설정, 쪽 범위 빼고), "range"({"how": all·even·odd, "custom": 범위 글 또는 None})}.
파일(그림·PDF)로 만든 템플릿은 원본을 decorations\\files\\<id>.<확장자> 로 복사해 두고 그것을 쓴다(원본이
사라져도 그대로 넣을 수 있게). 복사본 이름은 "copy" 에 적고 읽을 때 settings["file"] 을 그 자리로 채운다.
템플릿을 지우면 복사본도 지운다. 기본 제공 템플릿(종류마다 둘)은 파일에 적지 않고 여기 표에서 낸다.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid

from . import pages as P
from .deco import DECO_KINDS, SETTINGS_VERSION, upgrade_settings

CM = 72 / 2.54
ALL_PAGES = {"how": "all", "custom": None}
_HF_MARGINS = {"top": 36.0, "bottom": 36.0, "left": 72.0, "right": 72.0}   # 위아래 0.5in · 좌우 1in
_BATES_MARGINS = {"top": 1.25 * CM, "bottom": 1.25 * CM, "left": 1.25 * CM, "right": 2.54 * CM}


def _font(size: float) -> dict:
    return {"name": "", "size": float(size), "color": (0, 0, 0), "bold": False, "italic": False, "underline": False}


def _watermark(text: str) -> dict:
    return {"v": SETTINGS_VERSION, "text": text, "file": None, "file_page": 0, "font": _font(80), "position": "center",
            "offset": [0.0, 0.0], "tile": False, "tile_gap": [2, 2], "scale_mode": "max", "scale_pct": 100.0,
            "rotate": 45.0, "opacity": 0.3}


def _background(rgb: tuple[int, int, int]) -> dict:
    return {"v": SETTINGS_VERSION, "color": tuple(c / 255 for c in rgb), "file": None, "file_page": 0,
            "scale_mode": "max", "scale_pct": 100.0, "rotate": 0.0, "opacity": 1.0}


def _header(macro: str) -> dict:
    return {"v": SETTINGS_VERSION, "slots": {"bottom": macro}, "start_number": 1, "font": _font(14),
            "margins": dict(_HF_MARGINS)}


def _page_number(macro: str) -> dict:
    return {"v": SETTINGS_VERSION, "style": macro, "position": "bottom", "start": 1, "font": _font(14),
            "margins": dict(_HF_MARGINS)}


def _bates(prefix: str) -> dict:
    return {"v": SETTINGS_VERSION, "prefix": prefix, "suffix": "", "start": 1, "digits": 4, "reverse": False,
            "position": "top_right", "font": _font(14), "margins": dict(_BATES_MARGINS), "rotate": 0.0, "opacity": 1.0}


_BUILTIN = {
    "watermark": (("비밀", _watermark("비밀")), ("복사 금지", _watermark("복사 금지"))),
    "background": (("연두빛 회색", _background((214, 226, 202))), ("양피지", _background((227, 220, 199)))),
    "header_footer": (("페이지 1", _header("<<Page##1##>>")), ("1/n", _header("<<##1##/##n##>>"))),
    "page_number": (("페이지 1", _page_number("<<Page##1##>>")), ("1/n", _page_number("<<##1##/##n##>>"))),
    "bates": (("0001", _bates("")), ("AAA0001", _bates("AAA"))),
}


def builtins(kind: str) -> list[dict]:
    """기본 제공 템플릿 둘(고치거나 지울 수 없음)."""
    return [{"id": f"builtin-{k + 1}", "name": name, "settings": json.loads(json.dumps(s)), "range": dict(ALL_PAGES),
             "builtin": True} for k, (name, s) in enumerate(_BUILTIN[kind])]


# ── 저장 자리 ───────────────────────────────────────────────────────────
def folder() -> str:
    from ..appinfo import data_dir
    return os.path.join(data_dir(), "decorations")


def _path(kind: str) -> str:
    if kind not in DECO_KINDS:
        raise ValueError(f"모르는 꾸미기입니다: {kind}")
    return os.path.join(folder(), f"{kind}.json")


def _files() -> str:
    return os.path.join(folder(), "files")


def _read(kind: str) -> list[dict]:
    try:
        with open(_path(kind), encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("id")] if isinstance(data, list) else []


def _write(kind: str, items: list[dict]) -> None:
    os.makedirs(folder(), exist_ok=True)
    keep = []
    for t in items:
        t = {**t, "settings": dict(t["settings"])}
        if t.get("copy"):
            t["settings"]["file"] = None   # 복사본 자리는 읽을 때 채운다(폴더가 옮겨져도 맞게)
        t.pop("builtin", None)
        keep.append(t)
    tmp = _path(kind) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(keep, fp, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(kind))


def templates(kind: str) -> list[dict]:
    """사용자 지정(저장한) 템플릿, 저장한 차례. 파일 템플릿은 settings["file"] 이 복사본을 가리킨다."""
    out = []
    for t in _read(kind):
        t["settings"] = upgrade_settings(kind, t.get("settings") or {})
        t["range"] = norm_range(t.get("range"))
        if t.get("copy"):
            t["settings"]["file"] = os.path.join(_files(), t["copy"])
        out.append(t)
    return out


def find_name(kind: str, name: str) -> dict | None:
    """같은 이름(앞뒤 공백 무시)의 사용자 지정 템플릿."""
    name = (name or "").strip()
    return next((t for t in templates(kind) if display_name(t) == name), None)


def display_name(t: dict) -> str:
    """보이는 이름. 비어 있으면 id."""
    return (t.get("name") or "").strip() or t["id"]


# ── 고치기 ──────────────────────────────────────────────────────────────
def _copy_file(tid: str, settings: dict, old_copy: str | None) -> str | None:
    """설정의 파일을 복사본 자리로. 이미 그 복사본이면 그대로. 새 복사본 이름(파일이 없으면 None)."""
    src = settings.get("file")
    if not src:
        return None
    if old_copy and os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.join(_files(), old_copy)):
        return old_copy
    ext = os.path.splitext(src)[1].lower()
    name = f"{tid}{ext}"
    os.makedirs(_files(), exist_ok=True)
    dst = os.path.join(_files(), name)
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    return name


def _drop_copy(name: str | None) -> None:
    if name:
        try:
            os.remove(os.path.join(_files(), name))
        except OSError:
            pass


def save(kind: str, name: str, settings: dict, rng: dict | None = None, tid: str | None = None) -> dict:
    """템플릿을 저장한다. tid 가 있으면 그것을 바꾸고, 없으면 새로 넣는다. 저장한 템플릿을 돌려준다.
    파일 템플릿이면 원본을 복사해 둔다(원본이 없으면 OSError)."""
    items = _read(kind)
    old = next((t for t in items if t["id"] == tid), None) if tid else None
    tid = old["id"] if old else uuid.uuid4().hex
    s = {k: v for k, v in upgrade_settings(kind, settings).items() if k != "pages"}
    old_copy = (old or {}).get("copy")
    copy = _copy_file(tid, s, old_copy)
    if old_copy and old_copy != copy:
        _drop_copy(old_copy)
    t = {"id": tid, "name": (name or "").strip(), "settings": s, "range": norm_range(rng)}
    if copy:
        t["copy"] = copy
        t["source"] = (old or {}).get("source") if copy == old_copy else settings.get("file")
    if old:
        items[items.index(old)] = t
    else:
        items.append(t)
    _write(kind, items)
    if copy:
        t = {**t, "settings": {**s, "file": os.path.join(_files(), copy)}}
    return t


def remove(kind: str, tid: str) -> bool:
    """템플릿 하나와 그 파일 복사본을 지운다. 지웠으면 True."""
    items = _read(kind)
    old = next((t for t in items if t["id"] == tid), None)
    if old is None:
        return False
    items.remove(old)
    _write(kind, items)
    _drop_copy(old.get("copy"))
    return True


# ── 쪽 범위 ─────────────────────────────────────────────────────────────
def norm_range(rng: dict | None) -> dict:
    rng = rng or {}
    how = rng.get("how") if rng.get("how") in ("all", "even", "odd") else "all"
    custom = (rng.get("custom") or "").strip() or None
    return {"how": how, "custom": custom}


def range_pages(rng: dict | None, total: int) -> list[int]:
    """템플릿 범위 → 이 문서의 쪽(0부터). 범위가 쪽 수를 넘거나 0쪽이면 ValueError(범위 오류 문구)."""
    r = norm_range(rng)
    try:
        got = P.subset(P.parse_range(r["custom"], total, strict=True) if r["custom"] else range(total), r["how"])
    except ValueError:
        got = []
    if not got:
        raise ValueError(P.RANGE_ERROR)
    return got


# ── 이름 ────────────────────────────────────────────────────────────────
def summary(kind: str, settings: dict) -> str:
    """설정을 한 줄로(템플릿 이름 기본값 · 업데이트 목록): 글, 파일 이름, 번호 모양, 색."""
    s = upgrade_settings(kind, settings)
    slots = s.get("slots") or {}
    what = (s.get("text") or os.path.basename(s.get("file") or "") or s.get("style")
            or next((v for v in slots.values() if v), "")
            or (f"{s.get('prefix', '')}{int(s.get('start', 1)):0{int(s.get('digits', 1))}d}{s.get('suffix', '')}"
                if "digits" in s else "")
            or ("색" if s.get("color") else ""))
    return " ".join(str(what).split())
