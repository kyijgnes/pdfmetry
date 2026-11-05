"""양식 칸 속성 읽기·쓰기(칸 깃발·보이기 상태·MK·DA·BS·Opt·동작·CO).

read 로 칸 하나의 속성을 사전으로 읽고, write 로 여러 칸에 같은 값을 넣는다. 되돌리기 기록은 부르는 쪽이 남긴다.
"""
from __future__ import annotations

import re
from contextlib import nullcontext

import fitz

from . import form_calc as C
from .document import del_keys

# 칸 깃발(Ff)
FF_READONLY, FF_REQUIRED = 1, 2
FF_MULTILINE, FF_PASSWORD, FF_FILESELECT = 1 << 12, 1 << 13, 1 << 20
FF_NOSPELL, FF_NOSCROLL, FF_COMB = 1 << 22, 1 << 23, 1 << 24
FF_UNISON = 1 << 25
FF_EDIT, FF_SORT, FF_MULTISELECT, FF_COMMIT = 1 << 18, 1 << 19, 1 << 21, 1 << 26
# 주석 깃발(F)
F_HIDDEN, F_PRINT, F_NOVIEW, F_LOCKED = 2, 4, 32, 128

VISIBILITY = (("visible", "표시"), ("hidden", "숨기기"), ("noprint", "보이지만 출력되지 않음"), ("noview", "숨겨졌지만 출력 가능"))
BORDER_WIDTHS = ((1, "가늘게"), (2, "보통"), (3, "굵게"))
BORDER_STYLES = (("S", "실선"), ("D", "대시"), ("B", "사선"), ("I", "삽입"), ("U", "밑줄"))
FONTS = (("Helv", "Helvetica"), ("TiRo", "Times Roman"), ("Cour", "Courier"))
FONT_SIZES = (0, 5, 5.5, 6.5, 7.5, 8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72)
CHECK_STYLES = (("check", "확인 표시", "4"), ("circle", "원", "l"), ("cross", "분기(십자)", "8"),
                ("diamond", "다이아몬드", "u"), ("square", "사각형", "n"), ("star", "별모양", "H"))
BUTTON_LAYOUTS = ("라벨만", "아이콘만", "아이콘 상단, 라벨 하단", "라벨 상단, 아이콘 하단", "아이콘 왼쪽, 라벨 오른쪽",
                  "라벨 왼쪽, 아이콘 오른쪽", "아이콘 위에 라벨")
HIGHLIGHTS = (("N", "없음"), ("P", "밀어넣기"), ("O", "윤곽선"), ("I", "거꾸로"))
TRIGGERS = (("U", "마우스 놓기"), ("D", "마우스 누르기"), ("E", "마우스 들어옴"), ("X", "마우스 나감"),
            ("Fo", "포커스 얻음"), ("Bl", "포커스 잃음"))
ACTIONS = (("goto", "페이지 보기로 이동"), ("hide", "필드 표시/숨기기"), ("js", "자바 스크립트 실행"),
           ("launch", "파일 열기"), ("reset", "양식 재설정"), ("uri", "웹 링크 열기"))
SIG_LOCKS = (("none", "동작 없음"), ("All", "모든 필드"), ("Exclude", "선택한 항목을 제외한 모든 필드"), ("Include", "이 필드만"))

_TYPE = {fitz.PDF_WIDGET_TYPE_TEXT: "text", fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
         fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio", fitz.PDF_WIDGET_TYPE_COMBOBOX: "combo",
         fitz.PDF_WIDGET_TYPE_LISTBOX: "list", fitz.PDF_WIDGET_TYPE_BUTTON: "button",
         fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature"}


def _lock(doc):
    return getattr(doc, "lock", None) or nullcontext()


def _f(doc) -> fitz.Document:
    return getattr(doc, "fitz", doc)


def _widget(f: fitz.Document, page: int, xref: int):
    p = f[page]
    return p, next((w for w in p.widgets() if w.xref == xref), None)


def _holder(f: fitz.Document, x: int) -> int:
    """칸 값·깃발이 사는 곳: 묶음(부모)이 있으면 부모."""
    t, v = f.xref_get_key(x, "Parent")
    return int(v.split()[0]) if t == "xref" else x


def _int(f, x, key, default=0) -> int:
    t, v = f.xref_get_key(x, key)
    try:
        return int(float(v)) if t in ("int", "float") else default
    except ValueError:
        return default


def _str(f, x, key) -> str:
    t, v = f.xref_get_key(x, key)
    if t == "string":
        return v
    if t == "name":
        return v.lstrip("/")
    return ""


def _pdf_str(s: str) -> str:
    return fitz.get_pdf_str(s)


# ── 동작 ────────────────────────────────────────────────────────
def _parse_action(f: fitz.Document, obj: str) -> dict | None:
    """동작 사전 글 → {"type": …}. 모르는 것은 None."""
    if re.fullmatch(r"\s*\d+\s+0\s+R\s*", obj or ""):
        obj = f.xref_object(int(obj.split()[0]), compressed=True)
    s = re.search(r"/S\s*/(\w+)", obj or "")
    if not s:
        return None
    kind = s.group(1)

    def strings(key):
        m = re.search(rf"/{key}\s*\[(.*?)\]", obj, re.S)
        return [_unpdf(t) for t in re.findall(r"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f]*>", m.group(1))] if m else []

    if kind == "GoTo":
        m = re.search(r"/D\s*\[\s*(\d+)\s+0\s+R", obj)
        page = 0
        if m:
            x = int(m.group(1))
            page = next((i for i in range(f.page_count) if f[i].xref == x), 0)
        return {"type": "goto", "page": page}
    if kind == "Hide":
        hide = not re.search(r"/H\s*false", obj)
        return {"type": "hide", "fields": strings("T") or ([_unpdf(m.group(0))] if (m := re.search(r"/T\s*(\((?:\\.|[^\\)])*\))", obj)) else []), "hide": hide}
    if kind == "ResetForm":
        return {"type": "reset", "fields": strings("Fields")}
    if kind == "URI":
        m = re.search(r"/URI\s*(\((?:\\.|[^\\)])*\))", obj)
        return {"type": "uri", "uri": _unpdf(m.group(1)) if m else ""}
    if kind == "Launch":
        m = re.search(r"/F\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f]*>)", obj)
        return {"type": "launch", "file": _unpdf(m.group(1)) if m else ""}
    if kind == "JavaScript":
        m = re.search(r"/JS\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f]*>|\d+\s+0\s+R)", obj)
        js = ""
        if m:
            g = m.group(1)
            js = f.xref_stream(int(g.split()[0])).decode("utf-8", "replace") if g.endswith("R") else _unpdf(g)
        return {"type": "js", "script": js}
    return None


def _unpdf(s: str) -> str:
    """PDF 글자 토큰 → 파이썬 글."""
    s = s.strip()
    if s.startswith("<"):
        b = bytes.fromhex(s[1:-1] or "")
        if b.startswith(b"\xfe\xff"):
            return b[2:].decode("utf-16-be", "replace")
        return b.decode("latin-1")
    body = s[1:-1]
    out, i = bytearray(), 0
    esc = {"n": 10, "r": 13, "t": 9, "b": 8, "f": 12, "(": 40, ")": 41, "\\": 92}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            n = body[i + 1]
            if n in esc:
                out.append(esc[n]); i += 2; continue
            m = re.match(r"[0-7]{1,3}", body[i + 1:])
            if m:
                out.append(int(m.group(0), 8) & 255); i += 1 + len(m.group(0)); continue
            i += 1; continue
        out += ch.encode("latin-1", "replace") if ord(ch) < 256 else ch.encode("utf-8")
        i += 1
    b = bytes(out)
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _action_obj(f: fitz.Document, a: dict, nxt: str = "") -> str:
    t = a.get("type")
    tail = f"/Next {nxt}" if nxt else ""
    if t == "goto":
        pg = max(0, min(f.page_count - 1, int(a.get("page", 0))))
        return f"<</S/GoTo/D[{f[pg].xref} 0 R/Fit]{tail}>>"
    if t == "hide":
        names = "".join(_pdf_str(n) for n in a.get("fields", []))
        return f"<</S/Hide/T[{names}]/H {'true' if a.get('hide', True) else 'false'}{tail}>>"
    if t == "reset":
        names = "".join(_pdf_str(n) for n in a.get("fields", []))
        return f"<</S/ResetForm{'/Fields[' + names + ']' if names else ''}/Flags 0{tail}>>"
    if t == "uri":
        return f"<</S/URI/URI{_pdf_str(a.get('uri', ''))}{tail}>>"
    if t == "launch":
        return f"<</S/Launch/F{_pdf_str(a.get('file', ''))}{tail}>>"
    return f"<</S/JavaScript/JS{_pdf_str(a.get('script', ''))}{tail}>>"


def _chain(f: fitz.Document, obj: str) -> list[dict]:
    out, seen = [], 0
    while obj and seen < 50:
        seen += 1
        if re.fullmatch(r"\s*\d+\s+0\s+R\s*", obj):
            obj = f.xref_object(int(obj.split()[0]), compressed=True)
        a = _parse_action(f, obj)
        if a:
            out.append(a)
        m = re.search(r"/Next\s*(\d+\s+0\s+R|<<.*>>)", obj, re.S)
        obj = m.group(1) if m else ""
    return out


def read_actions(f: fitz.Document, x: int) -> dict[str, list[dict]]:
    out = {}
    for key, _ in TRIGGERS:
        t, v = f.xref_get_key(x, "A" if key == "U" else f"AA/{key}")
        if key == "U" and t == "null":
            t, v = f.xref_get_key(x, "AA/U")
        if t in ("dict", "xref"):
            acts = _chain(f, v)
            if acts:
                out[key] = acts
    return out


def write_actions(f: fitz.Document, x: int, acts: dict[str, list[dict]]) -> None:
    for key, _ in TRIGGERS:
        lst = acts.get(key) or []
        path = "A" if key == "U" else f"AA/{key}"
        if key == "U":
            if f.xref_get_key(x, "AA/U")[0] != "null":
                del_keys(f, x, ["AA/U"])
        if not lst:
            if f.xref_get_key(x, path)[0] != "null":
                del_keys(f, x, [path])
            continue
        nxt = ""
        for a in reversed(lst[1:]):
            nx = f.get_new_xref()
            f.update_object(nx, _action_obj(f, a, nxt))
            nxt = f"{nx} 0 R"
        if f.xref_get_key(x, "AA")[0] == "null" and key != "U":
            f.xref_set_key(x, "AA", "<<>>")
        f.xref_set_key(x, path, _action_obj(f, lst[0], nxt))


def keep_actions(f: fitz.Document, x: int) -> list[tuple[str, str]]:
    """w.update() 가 지우는 동작(/A·/AA/Fo·/AA/Bl 의 자바스크립트 아닌 것) 사본."""
    out = []
    for key in ("A", "AA/Fo", "AA/Bl"):
        t, v = f.xref_get_key(x, key)
        if t not in ("dict", "xref"):
            continue
        body = f.xref_object(int(v.split()[0]), compressed=True) if t == "xref" else v
        if "/JavaScript" not in body or "/Next" in body:
            out.append((key, v))
    return out


def restore_actions(f: fitz.Document, x: int, kept: list[tuple[str, str]]) -> None:
    for key, v in kept:
        if f.xref_get_key(x, key)[1] != v:
            if key.startswith("AA/") and f.xref_get_key(x, "AA")[0] == "null":
                f.xref_set_key(x, "AA", "<<>>")
            f.xref_set_key(x, key, v)


# ── 모양 덧그리기 ──────────────────────────────────────────────
def _on_name(f: fitz.Document, x: int) -> str:
    t, v = f.xref_get_key(x, "AP/N")
    names = re.findall(r"/([^\s/<>\[\]()]+)\s+\d+\s+0\s+R", v) if t == "dict" else []
    return next((n for n in names if n != "Off"), "")


def check_char(f: fitz.Document, x: int) -> str:
    return _str(f, x, "MK/CA")


def redraw_check(w: fitz.Widget) -> None:
    """체크·라디오의 켜짐 모양을 MK/CA 글자(ZapfDingbats)로 다시 그린다. 기본 모양이면 건드리지 않는다."""
    f = w.parent.parent
    x = w.xref
    ch = check_char(f, x)
    radio = w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    if not ch or (not radio and ch in ("4", "3")) or (radio and ch == "l"):
        return
    t, v = f.xref_get_key(x, "AP/N")
    refs = dict(re.findall(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R", v)) if t == "dict" else {}
    on = next((n for n in refs if n != "Off"), "")
    if not on:
        return
    onx = int(refs[on])
    base = f.xref_stream(int(refs["Off"])) if "Off" in refs else b""
    r = fitz.Rect(w.rect)
    wd, ht = r.width, r.height
    fs = min(wd, ht) * 0.75
    col = w.text_color or (0, 0, 0)
    tx, ty = (wd - fs * 0.78) / 2, (ht - fs * 0.7) / 2
    esc = ch.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    draw = f"\nq BT {col[0]:g} {col[1]:g} {col[2]:g} rg /ZaDb {fs:.2f} Tf {tx:.2f} {ty:.2f} Td ({esc}) Tj ET Q\n"
    font = f.get_new_xref()
    f.update_object(font, "<</Type/Font/Subtype/Type1/BaseFont/ZapfDingbats>>")
    f.update_stream(onx, base + draw.encode("latin-1"))
    f.xref_set_key(onx, "Resources", f"<</Font<</ZaDb {font} 0 R>>>>")


def apply_shown_text(w: fitz.Widget) -> None:
    """서식을 건 칸은 값은 그대로 두고 보이는 글만 서식대로 다시 그린다."""
    shown = C.shown_text(w)
    if shown is None and w.field_type == fitz.PDF_WIDGET_TYPE_COMBOBOX:   # 내보낼 값 대신 보일 이름
        cur = "" if w.field_value is None else str(w.field_value)
        shown = next((str(v[1]) for v in (w.choice_values or [])
                      if isinstance(v, (tuple, list)) and len(v) >= 2 and str(v[0]) == cur and str(v[1]) != cur), None)
    if shown is None:
        return
    f = w.parent.parent
    holder = _holder(f, w.xref)
    raw = "" if w.field_value is None else str(w.field_value)
    w.field_value = shown
    w.update()
    f.xref_set_key(holder, "V", _pdf_str(raw))
    w.field_value = raw


BOM16 = bytes((0xFE, 0xFF))


def _fix_js(f: fitz.Document, x: int) -> None:
    """PyMuPDF 는 스크립트를 UTF-8 바이트 그대로 넣어 한글 칸 이름이 깨진다. UTF-16 글자열로 바꿔 둔다."""
    for k in ("K", "F", "V", "C"):
        t, v = f.xref_get_key(x, f"AA/{k}/JS")
        if t == "xref":
            b = f.xref_stream(int(v.split()[0])) or b""
            text = None if b.isascii() else C.js_text(b)
        elif t == "string" and not v.isascii():
            text = C.js_text(v)
            text = None if text == v else text
        else:
            text = None
        if text is None:
            continue
        ta, va = f.xref_get_key(x, f"AA/{k}")
        if ta == "xref":
            f.xref_set_key(int(va.split()[0]), "JS", _pdf_str(text))
        else:
            f.xref_set_key(x, f"AA/{k}", f"<</S/JavaScript/JS{_pdf_str(text)}>>")


def after_save(w: fitz.Widget, kept: list[tuple[str, str]]) -> None:
    """annots.save_widget 이 w.update() 뒤에 부른다."""
    f = w.parent.parent
    _fix_js(f, w.xref)
    restore_actions(f, w.xref, kept)
    if w.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
        redraw_check(w)
    else:
        apply_shown_text(w)
        restore_actions(f, w.xref, kept)
        _fix_js(f, w.xref)


# ── 읽기 ────────────────────────────────────────────────────────
def field_type(w: fitz.Widget) -> str:
    return _TYPE.get(w.field_type, "text")


def _visibility(fl: int) -> str:
    if fl & F_HIDDEN:
        return "hidden"
    if fl & F_NOVIEW:
        return "noview" if fl & F_PRINT else "hidden"
    return "visible" if fl & F_PRINT else "noprint"


def _vis_flags(fl: int, vis: str) -> int:
    fl &= ~(F_HIDDEN | F_PRINT | F_NOVIEW)
    return fl | {"visible": F_PRINT, "hidden": F_HIDDEN, "noprint": 0, "noview": F_NOVIEW | F_PRINT}[vis]


def _options(f: fitz.Document, x: int, w: fitz.Widget) -> list[tuple[str, str]]:
    out = []
    for v in w.choice_values or []:
        if isinstance(v, (tuple, list)) and len(v) >= 2:
            out.append((str(v[0]), str(v[1])))
        else:
            out.append((str(v), str(v)))
    return out


def read(doc, page: int, xref: int) -> dict:
    f = _f(doc)
    with _lock(doc):
        p, w = _widget(f, page, xref)
        if w is None:
            return {}
        h = _holder(f, xref)
        ff = _int(f, h, "Ff")
        fl = _int(f, xref, "F", F_PRINT)
        kind = field_type(w)
        d = {"type": kind, "page": page, "xref": xref, "name": w.field_name or "", "tooltip": w.field_label or "",
             "visibility": _visibility(fl), "readonly": bool(ff & FF_READONLY), "required": bool(ff & FF_REQUIRED),
             "locked": bool(fl & F_LOCKED),
             "border_color": tuple(w.border_color) if w.border_color else None,
             "fill_color": tuple(w.fill_color) if w.fill_color else None,
             "border_width": int(round(w.border_width or 1)) if w.border_width else 1,
             "border_style": (w.border_style or "S")[:1].upper(),
             "font": w.text_font if w.text_font in dict(FONTS) else "Helv",
             "fontsize": float(w.text_fontsize or 0), "text_color": tuple(w.text_color or (0, 0, 0)),
             "actions": read_actions(f, xref)}
        if kind == "text":
            d.update(align=_int(f, xref, "Q", _int(f, h, "Q")), default=_str(f, h, "DV"),
                     multiline=bool(ff & FF_MULTILINE), noscroll=bool(ff & FF_NOSCROLL), password=bool(ff & FF_PASSWORD),
                     nospell=bool(ff & FF_NOSPELL), fileselect=bool(ff & FF_FILESELECT), comb=bool(ff & FF_COMB),
                     maxlen=int(w.text_maxlen or 0))
        if kind in ("checkbox", "radio"):
            ch = check_char(f, xref) or ("l" if kind == "radio" else "4")
            d.update(style=next((k for k, _, c in CHECK_STYLES if c == ch), "circle" if kind == "radio" else "check"),
                     export=_name_text(_on_name(f, xref)) or "Yes", default_on=bool(_str(f, h, "DV")) and _str(f, h, "DV") != "Off",
                     unison=bool(ff & FF_UNISON))
        if kind in ("combo", "list"):
            d.update(options=_options(f, xref, w), default=_str(f, h, "DV"),
                     edit=bool(ff & FF_EDIT), sort=bool(ff & FF_SORT), commit=bool(ff & FF_COMMIT),
                     multi=bool(ff & FF_MULTISELECT))
        if kind == "button":
            d.update(layout=_int(f, xref, "MK/TP"), highlight=_str(f, xref, "H") or "I",
                     captions={"N": _str(f, xref, "MK/CA") or (w.button_caption or ""), "D": _str(f, xref, "MK/RC"),
                               "R": _str(f, xref, "MK/AC")})
        if kind in ("text", "combo"):
            d["format"] = C.parse_format(C.js_text(w.script_format), C.js_text(w.script_stroke))
            d["validate"] = C.parse_validate(C.js_text(w.script_change))
            d["calc"] = C.parse_calc(C.js_text(w.script_calc))
        if kind == "list":
            d["selchange"] = C.js_text(w.script_stroke).strip()
        if kind == "signature":
            act = _str(f, xref, "Lock/Action")
            t, v = f.xref_get_key(xref, "Lock/Fields")
            d["sig_lock"] = {"action": act or "none",
                             "fields": [_unpdf(s) for s in re.findall(r"\((?:\\.|[^\\)])*\)", v)] if t == "array" else []}
        return d


# ── 쓰기 ────────────────────────────────────────────────────────
_WIDGET_KEYS = {"tooltip", "border_color", "fill_color", "border_width", "border_style", "font", "fontsize",
                "text_color", "maxlen", "format", "validate", "calc", "selchange", "captions"}


def _set_ff(f: fitz.Document, h: int, bit: int, on: bool) -> None:
    ff = _int(f, h, "Ff")
    nf = (ff | bit) if on else (ff & ~bit)
    if nf != ff:
        f.xref_set_key(h, "Ff", str(nf))


def _name_tok(s: str) -> str:
    """파이썬 글 → PDF 이름 토큰 글(앞 / 없이). 영숫자 밖은 #xx."""
    return "".join(chr(b) if (chr(b).isalnum() and b < 128) or chr(b) in "-_." else f"#{b:02X}"
                   for b in s.encode("utf-8")) or "Yes"


def _name_text(tok: str) -> str:
    """PDF 이름 토큰 글(#xx 포함) → 파이썬 글."""
    try:
        return re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]),
                      tok.encode("latin-1", "replace")).decode("utf-8")
    except UnicodeDecodeError:
        return tok


def _rename_state(f: fitz.Document, x: int, old_tok: str, new: str) -> None:
    new_tok = _name_tok(new)
    for ap in ("AP/N", "AP/D"):
        t, v = f.xref_get_key(x, ap)
        if t == "dict":
            f.xref_set_key(x, ap, re.sub(rf"/{re.escape(old_tok)}(?=[\s/<>\[\]()])", f"/{new_tok}", v))
    old = _name_text(old_tok)
    if _str(f, x, "AS") in (old, old_tok):
        f.xref_set_key(x, "AS", f"/{new_tok}")
    h = _holder(f, x)
    if _str(f, h, "V") in (old, old_tok):
        f.xref_set_key(h, "V", f"/{new_tok}")


def _write_one(f: fitz.Document, page: int, xref: int, ch: dict) -> None:
    from .annots import save_widget
    p, w = _widget(f, page, xref)
    if w is None:
        return
    kind = field_type(w)
    btn = kind in ("checkbox", "radio", "button")
    if _WIDGET_KEYS & ch.keys():
        if "tooltip" in ch:
            w.field_label = ch["tooltip"] or None
        if "border_color" in ch:
            w.border_color = ch["border_color"]
            if ch["border_color"] and not w.border_width:
                w.border_width = 1
        if "fill_color" in ch:
            w.fill_color = ch["fill_color"]
        if "border_width" in ch:
            w.border_width = int(ch["border_width"])
        if "border_style" in ch:
            w.border_style = ch["border_style"]
            w.border_dashes = [3] if ch["border_style"] == "D" else None
        if "font" in ch and not btn:
            w.text_font = ch["font"]
        if "fontsize" in ch:
            w.text_fontsize = float(ch["fontsize"])
        if "text_color" in ch:
            w.text_color = ch["text_color"]
        if "maxlen" in ch and kind == "text":
            w.text_maxlen = int(ch["maxlen"] or 0)
        if "format" in ch and kind in ("text", "combo"):
            w.script_format, w.script_stroke = C.build_format(ch["format"])
        if "validate" in ch and kind in ("text", "combo"):
            w.script_change = C.build_validate(ch["validate"])
        if "calc" in ch and kind in ("text", "combo"):
            w.script_calc = C.build_calc(ch["calc"])
        if "selchange" in ch and kind == "list":
            w.script_stroke = ch["selchange"] or None
        if "captions" in ch and kind == "button":
            w.button_caption = ch["captions"].get("N", "") or " "
        save_widget(w)
    h = _holder(f, xref)
    raw_ap = False
    for key, mk in (("border_color", "MK/BC"), ("fill_color", "MK/BG")):   # 「없음」 은 update 가 지우지 않는다
        if key in ch and ch[key] is None and f.xref_get_key(xref, mk)[0] != "null":
            del_keys(f, xref, [mk])
            raw_ap = True
    if "visibility" in ch or "locked" in ch:
        fl = _int(f, xref, "F", F_PRINT)
        if "visibility" in ch:
            fl = _vis_flags(fl, ch["visibility"])
        if "locked" in ch:
            fl = (fl | F_LOCKED) if ch["locked"] else (fl & ~F_LOCKED)
        f.xref_set_key(xref, "F", str(fl))
    for key, bit in (("readonly", FF_READONLY), ("required", FF_REQUIRED), ("multiline", FF_MULTILINE),
                     ("noscroll", FF_NOSCROLL), ("password", FF_PASSWORD), ("nospell", FF_NOSPELL),
                     ("fileselect", FF_FILESELECT), ("comb", FF_COMB), ("unison", FF_UNISON), ("edit", FF_EDIT),
                     ("sort", FF_SORT), ("commit", FF_COMMIT), ("multi", FF_MULTISELECT)):
        if key in ch:
            if key == "required" and kind in ("button", "list"):
                continue
            _set_ff(f, h, bit, bool(ch[key]))
            raw_ap = raw_ap or key in ("multiline", "password", "comb")
    if "align" in ch and kind in ("text", "combo", "list"):
        f.xref_set_key(xref, "Q", str(int(ch["align"])))
        raw_ap = True
    if "default" in ch and kind in ("text", "combo", "list"):
        f.xref_set_key(h, "DV", _pdf_str(ch["default"])) if ch["default"] else del_keys(f, h, ["DV"])
    if "style" in ch and kind in ("checkbox", "radio"):
        c = next((c for k, _, c in CHECK_STYLES if k == ch["style"]), "4")
        if f.xref_get_key(xref, "MK")[0] == "null":
            f.xref_set_key(xref, "MK", "<<>>")
        f.xref_set_key(xref, "MK/CA", _pdf_str(c))
        raw_ap = True
    if "export" in ch and kind in ("checkbox", "radio") and ch["export"]:
        old = _on_name(f, xref)
        if old and _name_text(old) != ch["export"]:
            _rename_state(f, xref, old, ch["export"])
            raw_ap = True
    if "default_on" in ch and kind in ("checkbox", "radio"):
        f.xref_set_key(h, "DV", f"/{_on_name(f, xref) or 'Yes'}") if ch["default_on"] else del_keys(f, h, ["DV"])
    if "options" in ch and kind in ("combo", "list"):
        opts = ch["options"]
        arr = "".join(f"[{_pdf_str(e)}{_pdf_str(n)}]" if e != n else _pdf_str(n) for e, n in opts)
        f.xref_set_key(h if h != xref else xref, "Opt", f"[{arr}]")
        raw_ap = True
    if "layout" in ch and kind == "button":
        if f.xref_get_key(xref, "MK")[0] == "null":
            f.xref_set_key(xref, "MK", "<<>>")
        f.xref_set_key(xref, "MK/TP", str(int(ch["layout"])))
    if "highlight" in ch and kind == "button":
        f.xref_set_key(xref, "H", f"/{ch['highlight']}")
    if "captions" in ch and kind == "button":
        if f.xref_get_key(xref, "MK")[0] == "null":
            f.xref_set_key(xref, "MK", "<<>>")
        for k, key in (("D", "MK/RC"), ("R", "MK/AC")):
            v = ch["captions"].get(k, "")
            f.xref_set_key(xref, key, _pdf_str(v)) if v else del_keys(f, xref, [key])
    if "actions" in ch:
        write_actions(f, xref, ch["actions"])
    if "sig_lock" in ch and kind == "signature":
        sl = ch["sig_lock"]
        if sl.get("action", "none") == "none":
            del_keys(f, xref, ["Lock"])
        else:
            names = "".join(_pdf_str(n) for n in sl.get("fields", []))
            extra = f"/Fields[{names}]" if sl["action"] != "All" else ""
            f.xref_set_key(xref, "Lock", f"<</Type/SigFieldLock/Action/{sl['action']}{extra}>>")
    if "calc" in ch and kind in ("text", "combo"):
        order = C.calc_order(f)
        if ch["calc"].get("kind", "none") == "none":
            if xref in order:
                set_calc_order(f, [x for x in order if x != xref])
        elif xref not in order:
            set_calc_order(f, order + [xref])
    if raw_ap:
        p, w = _widget(f, page, xref)
        if w is not None:
            save_widget(w)


def write(doc, items: list[tuple[int, int]], changes: dict) -> list[int]:
    """칸 여럿에 같은 바꿈을 넣는다. 바뀐 쪽 번호들."""
    f = _f(doc)
    pages = set()
    with _lock(doc):
        for page, xref in items:
            _write_one(f, page, xref, changes)
            pages.add(page)
    return sorted(pages)


# ── 계산 순서 ──────────────────────────────────────────────────
def set_calc_order(doc, xrefs: list[int]) -> None:
    f = _f(doc)
    cat = f.pdf_catalog()
    if f.xref_get_key(cat, "AcroForm")[0] == "null":
        return
    if not xrefs:
        del_keys(f, cat, ["AcroForm/CO"])
        return
    f.xref_set_key(cat, "AcroForm/CO", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")


def calc_fields(doc) -> list[dict]:
    """계산이 걸린 칸을 CO 차례대로: [{"xref", "page", "name"}]. CO 에 없는 계산 칸은 뒤에 붙는다."""
    f = _f(doc)
    with _lock(doc):
        found = {}
        for i in range(f.page_count):
            for w in f[i].widgets():
                if (w.script_calc or "").strip():
                    found[w.xref] = {"xref": w.xref, "page": i, "name": w.field_name or ""}
        order = [x for x in C.calc_order(f) if x in found]
        return [found[x] for x in order] + [v for x, v in found.items() if x not in order]


def is_locked(doc, xref: int) -> bool:
    f = _f(doc)
    return bool(_int(f, xref, "F") & F_LOCKED)


def name_conflict(doc, items: list[tuple[int, int]], name: str) -> bool:
    """다른 종류 칸이 이미 그 이름을 쓰는지."""
    f = _f(doc)
    mine = {x for _, x in items}
    kinds = set()
    with _lock(doc):
        for pg, x in items:
            _, w = _widget(f, pg, x)
            if w is not None:
                kinds.add(w.field_type)
        for i in range(f.page_count):
            for w in f[i].widgets():
                if w.xref not in mine and w.field_name == name and w.field_type not in kinds:
                    return True
    return False


def hidden_from_print(f: fitz.Document) -> bool:
    """출력에서 빼거나 출력만 할 칸이 있는지(인쇄 사본을 따로 만들지 정할 때)."""
    for i in range(f.page_count):
        for w in f[i].widgets():
            fl = _int(f, w.xref, "F", F_PRINT)
            if not fl & F_PRINT or fl & (F_NOVIEW | F_HIDDEN):
                return True
    return False


def prepare_print(f: fitz.Document, pages) -> None:
    """인쇄 사본에서: 출력하지 않을 칸은 지우고, 숨겨졌지만 출력 가능한 칸은 보이게."""
    for i in sorted(set(pages)):
        if not 0 <= i < f.page_count:
            continue
        p = f[i]
        for w in list(p.widgets()):
            fl = _int(f, w.xref, "F", F_PRINT)
            if not fl & F_PRINT or fl & F_HIDDEN:
                p.delete_widget(w)
            elif fl & F_NOVIEW:
                f.xref_set_key(w.xref, "F", str(fl & ~F_NOVIEW))
