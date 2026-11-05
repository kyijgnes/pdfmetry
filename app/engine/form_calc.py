"""양식 칸 서식·유효성 검사·계산을 파이썬으로 따라 한다.

파일에는 표준 AF 스크립트(AFNumber_Format 같은 것)를 넣어 다른 PDF 프로그램에서도 같게 돌고,
우리 화면은 자바스크립트 없이 그 스크립트를 읽어 같은 결과를 흉내 낸다.
사용자 스크립트·단순 표기법은 파일에 저장만 하고 여기서는 돌리지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import re

import fitz

# ── 서식 ────────────────────────────────────────────────────────
SEP_STYLES = ("1,234.56", "1234.56", "1.234,56", "1234,56", "1'234.56")
CURRENCIES = ("", "$", "€", "￥", "DM", "ƒ", "₣", "kr", "TL", "Pes.", "￡")
_CUR_AFTER = {"DM", "kr", "TL", "Pes."}
DATE_FORMATS = ("m/d", "m/d/yy", "m/d/yyyy", "mm/dd/yy", "mm/dd/yyyy", "mm/yyyy", "d-mmm", "d-mmm-yy", "d-mmm-yyyy",
                "dd-mmm-yy", "dd-mmm-yyyy", "yy-mm-dd", "yyyy-mm-dd", "mmm-yy", "mmm-yyyy", "mmmm-yy", "mmmm-yyyy",
                "mmm d,yyyy", "mmmm d,yyyy", "m/d/yy h:MM tt", "m/d/yyyy h:MM tt", "m/d/yy HH:MM", "m/d/yyyy HH:MM")
TIME_FORMATS = ("HH:MM", "h:MM tt", "HH:MM:ss", "h:MM:ss tt")
SPECIALS = ("우편번호", "우편번호 +4", "전화 번호", "주민등록번호")
_SPECIAL_MASKS = ("99999", "99999-9999", "(999) 999-9999", "999-99-9999")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")

MSG_RANGE = "잘못된 값: {0} 보다 크거나 같고 {1}보다 작거나 같아야 합니다."
MSG_RANGE_MIN = "잘못된 값: {0} 보다 크거나 같아야 합니다."
MSG_RANGE_MAX = "잘못된 값: {0}보다 작거나 같아야 합니다."
MSG_DATE = "유효하지 않은 날짜/시간: 필드[{0}] 은 {1} 형식과 일치해야 합니다."
MSG_NUMBER = "입력한 값이 필드 [{0}] 의 형식과 일치하지 않습니다."


def js_text(s) -> str:
    """스크립트 글을 바로잡는다: UTF-16 머리표가 붙은 바이트·글, UTF-8 바이트를 latin-1 로 읽은 글."""
    if s is None:
        return ""
    if isinstance(s, bytes):
        b = s
    else:
        try:
            b = s.encode("latin-1")
        except UnicodeEncodeError:
            return s
    if b[:2] == bytes((0xFE, 0xFF)):
        return b[2:].decode("utf-16-be", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return s if isinstance(s, str) else b.decode("latin-1")


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _args(s: str) -> list[str]:
    """AF 함수 인자 글 → 낱낱(따옴표 글은 풀고, 숫자·true/false 는 글 그대로)."""
    out, cur, q, esc = [], "", None, False
    for ch in s:
        if q:
            if esc:
                cur += ch; esc = False
            elif ch == "\\":
                esc = True
            elif ch == q:
                q = None
            else:
                cur += ch
        elif ch in "\"'":
            q = ch
        elif ch == ",":
            out.append(cur.strip()); cur = ""
        else:
            cur += ch
    if cur.strip() or out:
        out.append(cur.strip())
    return out


def _call(script: str | None, name: str) -> list[str] | None:
    m = re.search(rf"\b{name}\s*\((.*)\)\s*;?\s*$", (script or "").strip(), re.S)
    return _args(m.group(1)) if m else None


def parse_format(fmt: str | None, stroke: str | None = None) -> dict:
    """서식 스크립트 → {"kind": none|number|percent|date|time|special|custom, ...}."""
    fmt = (fmt or "").strip()
    if not fmt and not (stroke or "").strip():
        return {"kind": "none"}
    a = _call(fmt, "AFNumber_Format")
    if a and len(a) >= 6:
        return {"kind": "number", "dec": int(float(a[0])), "sep": int(float(a[1])), "neg": int(float(a[2])),
                "currency": a[4], "prepend": a[5].lower() == "true"}
    a = _call(fmt, "AFPercent_Format")
    if a and len(a) >= 2:
        return {"kind": "percent", "dec": int(float(a[0])), "sep": int(float(a[1]))}
    a = _call(fmt, "AFDate_FormatEx")
    if a:
        return {"kind": "date", "fmt": a[0]}
    a = _call(fmt, "AFDate_Format")
    if a:
        k = int(float(a[0]))
        return {"kind": "date", "fmt": DATE_FORMATS[k] if 0 <= k < len(DATE_FORMATS) else "yyyy-mm-dd"}
    a = _call(fmt, "AFTime_FormatEx")
    if a:
        return {"kind": "time", "fmt": a[0]}
    a = _call(fmt, "AFTime_Format")
    if a:
        k = int(float(a[0]))
        return {"kind": "time", "fmt": TIME_FORMATS[k] if 0 <= k < len(TIME_FORMATS) else "HH:MM"}
    a = _call(fmt, "AFSpecial_Format")
    if a:
        return {"kind": "special", "which": int(float(a[0])), "mask": ""}
    a = _call(fmt, "AFSpecial_KeystrokeEx") or _call(stroke, "AFSpecial_KeystrokeEx")
    if a:
        return {"kind": "special", "which": 4, "mask": a[0]}
    return {"kind": "custom", "format": fmt, "keystroke": (stroke or "").strip()}


def build_format(d: dict) -> tuple[str | None, str | None]:
    """{"kind": …} → (서식 스크립트, 키 입력 스크립트). 없음이면 (None, None)."""
    k = d.get("kind", "none")
    if k == "number":
        dec, sep, neg = int(d.get("dec", 2)), int(d.get("sep", 0)), int(d.get("neg", 0))
        cur = d.get("currency", "")
        pre = "true" if d.get("prepend", cur not in _CUR_AFTER) else "false"
        return (f"AFNumber_Format({dec}, {sep}, {neg}, 0, {_q(cur)}, {pre});",
                f"AFNumber_Keystroke({dec}, {sep}, {neg}, 0, {_q(cur)}, {pre});")
    if k == "percent":
        dec, sep = int(d.get("dec", 2)), int(d.get("sep", 0))
        return f"AFPercent_Format({dec}, {sep});", f"AFPercent_Keystroke({dec}, {sep});"
    if k == "date":
        f = d.get("fmt") or "yyyy-mm-dd"
        return f"AFDate_FormatEx({_q(f)});", f"AFDate_KeystrokeEx({_q(f)});"
    if k == "time":
        f = d.get("fmt") or "HH:MM"
        if f in TIME_FORMATS:
            n = TIME_FORMATS.index(f)
            return f"AFTime_Format({n});", f"AFTime_Keystroke({n});"
        return f"AFTime_FormatEx({_q(f)});", f"AFTime_KeystrokeEx({_q(f)});"
    if k == "special":
        w = int(d.get("which", 0))
        if w == 4:
            m = _q(d.get("mask", ""))
            return f"AFSpecial_KeystrokeEx({m});", f"AFSpecial_KeystrokeEx({m});"
        return f"AFSpecial_Format({w});", f"AFSpecial_Keystroke({w});"
    if k == "custom":
        return d.get("format") or None, d.get("keystroke") or None
    return None, None


def to_number(s, sep: int = 0) -> float | None:
    """칸 글 → 숫자. 빈 글은 None. 통화 기호·자릿점·괄호(음수)를 걷는다."""
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    t = str(s or "").strip()
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"[^0-9.,'\-]", "", t)
    if sep in (2, 3):
        t = t.replace(".", "").replace("'", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace("'", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -abs(v) if neg else v


def _group(n: float, dec: int, sep: int) -> str:
    s = f"{abs(n):.{dec}f}"
    ip, _, fp = s.partition(".")
    th = {0: ",", 1: "", 2: ".", 3: "", 4: "'"}.get(sep, ",")
    if th:
        ip = f"{int(ip):,}".replace(",", th)
    point = "," if sep in (2, 3) else "."
    return ip + (point + fp if fp else "")


def _fmt_date(v: _dt.datetime, f: str) -> str:
    out, i = [], 0
    toks = ("yyyy", "yy", "mmmm", "mmm", "mm", "m", "dd", "d", "HH", "H", "hh", "h", "MM", "M", "ss", "s", "tt")
    while i < len(f):
        for t in toks:
            if f.startswith(t, i):
                h12 = v.hour % 12 or 12
                out.append({"yyyy": f"{v.year:04d}", "yy": f"{v.year % 100:02d}", "mmmm": _MONTHS[v.month - 1],
                            "mmm": _MONTHS[v.month - 1][:3], "mm": f"{v.month:02d}", "m": str(v.month),
                            "dd": f"{v.day:02d}", "d": str(v.day), "HH": f"{v.hour:02d}", "H": str(v.hour),
                            "hh": f"{h12:02d}", "h": str(h12), "MM": f"{v.minute:02d}", "M": str(v.minute),
                            "ss": f"{v.second:02d}", "s": str(v.second), "tt": "am" if v.hour < 12 else "pm"}[t])
                i += len(t)
                break
        else:
            out.append(f[i]); i += 1
    return "".join(out)


def parse_date(s: str, f: str = "", time_only: bool = False) -> _dt.datetime | None:
    """칸 글 → 날짜·시각. 서식 순서(년·월·일 자리)를 먼저 보고, 흔한 꼴을 이어서 본다."""
    t = str(s or "").strip()
    if not t:
        return None
    low = t.lower()
    mon = next((k + 1 for k, n in enumerate(_MONTHS) if n.lower()[:3] in low), None)
    nums = [int(x) for x in re.findall(r"\d+", t)]
    if not nums:
        return None
    hh = mm = ss = 0
    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm|오전|오후)?", low)
    if tm:
        hh, mm, ss = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
        if tm.group(4) in ("pm", "오후") and hh < 12:
            hh += 12
        if tm.group(4) in ("am", "오전") and hh == 12:
            hh = 0
        head = low[:tm.start()]
        nums = [int(x) for x in re.findall(r"\d+", head)]
    today = _dt.date.today()
    if time_only:
        if not tm:
            return None
        try:
            return _dt.datetime(today.year, today.month, today.day, hh, mm, ss)
        except ValueError:
            return None
    order = [c for c in re.findall(r"y+|m+|d+", f.replace("MM", "").replace("HH", "")) if c[0] in "ymd"]
    if mon:
        order = [c for c in order if c[0] != "m"]
    if len(nums) >= 1 and nums[0] > 31:
        order = ["y", "m", "d"] if not mon else ["y", "d"]
    if not order:
        order = ["y", "m", "d"] if not mon else ["d", "y"]
    vals = {"y": today.year, "m": mon or 1, "d": 1}
    for c, n in zip([o[0] for o in order], nums):
        vals[c] = n
    if len(nums) < len(order) and not any(o[0] == "y" for o in order[:len(nums)]):
        vals["y"] = today.year
    y = vals["y"]
    if y < 100:
        y += 2000 if y < 50 else 1900
    try:
        return _dt.datetime(y, vals["m"], vals["d"], hh, mm, ss)
    except ValueError:
        return None


def _mask(digits: str, mask: str) -> str:
    out, k = [], 0
    for ch in mask:
        if ch in "9AOX":
            if k >= len(digits):
                break
            out.append(digits[k]); k += 1
        else:
            out.append(ch)
    return "".join(out)


def format_value(fmt: dict, raw) -> str:
    """서식을 건 칸에 보일 글. 서식에 안 맞는 값은 그대로 보인다."""
    k = fmt.get("kind", "none")
    s = "" if raw is None else str(raw)
    if not s.strip() or k in ("none", "custom"):
        return s
    if k in ("number", "percent"):
        v = to_number(s, fmt.get("sep", 0))
        if v is None:
            return s
        dec, sep = int(fmt.get("dec", 2)), int(fmt.get("sep", 0))
        if k == "percent":
            return ("-" if v < 0 else "") + _group(v * 100, dec, sep) + "%"
        body = _group(v, dec, sep)
        cur = fmt.get("currency", "")
        if cur:
            body = (cur + body) if fmt.get("prepend", cur not in _CUR_AFTER) else (body + cur)
        if v < 0 and round(abs(v), dec) != 0:
            body = f"({body})" if int(fmt.get("neg", 0)) in (2, 3) else "-" + body
        return body
    if k in ("date", "time"):
        v = parse_date(s, fmt.get("fmt", ""), time_only=k == "time")
        return _fmt_date(v, fmt.get("fmt", "")) if v else s
    if k == "special":
        w = int(fmt.get("which", 0))
        mask = fmt.get("mask", "") if w == 4 else _SPECIAL_MASKS[w] if 0 <= w < 4 else ""
        chars = re.sub(r"[^0-9A-Za-z]", "", s) if w == 4 else re.sub(r"\D", "", s)
        return _mask(chars, mask) if mask else s
    return s


def check_input(fmt: dict, raw, name: str) -> str | None:
    """서식과 안 맞는 입력이면 알림 글."""
    k = fmt.get("kind", "none")
    s = str(raw or "").strip()
    if not s:
        return None
    if k in ("number", "percent") and to_number(s, fmt.get("sep", 0)) is None:
        return MSG_NUMBER.format(name)
    if k in ("date", "time") and parse_date(s, fmt.get("fmt", ""), time_only=k == "time") is None:
        return MSG_DATE.format(name, fmt.get("fmt", ""))
    return None


# ── 유효성 검사 ─────────────────────────────────────────────────
def _num_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def parse_validate(script: str | None) -> dict:
    s = (script or "").strip()
    if not s:
        return {"kind": "none"}
    a = _call(s, "AFRange_Validate")
    if a and len(a) >= 4:
        lo = float(a[1]) if a[0].lower() == "true" else None
        hi = float(a[3]) if a[2].lower() == "true" else None
        return {"kind": "range", "min": lo, "max": hi}
    return {"kind": "custom", "script": s}


def build_validate(d: dict) -> str | None:
    k = d.get("kind", "none")
    if k == "range":
        lo, hi = d.get("min"), d.get("max")
        if lo is None and hi is None:
            return None
        return (f"AFRange_Validate({'true' if lo is not None else 'false'}, {_num_text(lo or 0)}, "
                f"{'true' if hi is not None else 'false'}, {_num_text(hi or 0)});")
    if k == "custom":
        return d.get("script") or None
    return None


def check_range(val: dict, raw, sep: int = 0) -> str | None:
    """범위 밖이면 알림 글, 괜찮으면 None."""
    if val.get("kind") != "range":
        return None
    v = to_number(raw, sep)
    if v is None:
        return None
    lo, hi = val.get("min"), val.get("max")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        if lo is not None and hi is not None:
            return MSG_RANGE.format(_num_text(lo), _num_text(hi))
        return MSG_RANGE_MIN.format(_num_text(lo)) if lo is not None else MSG_RANGE_MAX.format(_num_text(hi))
    return None


# ── 계산 ────────────────────────────────────────────────────────
CALC_OPS = (("SUM", "합계(+)"), ("PRD", "곱(x)"), ("AVG", "평균"), ("MIN", "최소"), ("MAX", "최대"))


def parse_calc(script: str | None) -> dict:
    s = (script or "").strip()
    if not s:
        return {"kind": "none"}
    m = re.search(r"AFSimple_Calculate\s*\(\s*[\"'](\w+)[\"']\s*,\s*new\s+Array\s*\((.*)\)\s*\)", s, re.S)
    if m:
        return {"kind": "simple", "op": m.group(1).upper(), "fields": _args(m.group(2))}
    m = re.search(r"/\*\*\s*BVCALC(.*?)EVCALC\s*\*\*/", s, re.S)
    if m:
        return {"kind": "notation", "expr": m.group(1).strip()}
    return {"kind": "custom", "script": s}


def build_calc(d: dict) -> str | None:
    k = d.get("kind", "none")
    if k == "simple" and d.get("fields"):
        names = ", ".join(_q(n) for n in d["fields"])
        return f'AFSimple_Calculate("{d.get("op", "SUM")}", new Array ({names}));'
    if k == "notation" and d.get("expr", "").strip():
        e = d["expr"].strip()
        js = re.sub(r"[A-Za-z_가-힣][\w가-힣.]*", lambda m: f"AFMakeNumber(getField({_q(m.group(0))}).value)", e)
        return f"/** BVCALC {e} EVCALC **/ event.value = {js};"
    if k == "custom":
        return d.get("script") or None
    return None


def compute(op: str, values: list[float]) -> float:
    if not values:
        return 0.0
    if op == "PRD":
        r = 1.0
        for v in values:
            r *= v
        return r
    if op == "AVG":
        return sum(values) / len(values)
    if op == "MIN":
        return min(values)
    if op == "MAX":
        return max(values)
    return sum(values)


def _widgets(f: fitz.Document):
    pages = [f[i] for i in range(f.page_count)]   # 쪽을 붙잡아 둔다(놓치면 칸이 풀린다)
    out = [(i, w) for i, p in enumerate(pages) for w in p.widgets()]
    return out, pages


def _field_number(w: fitz.Widget) -> float:
    v = w.field_value
    if w.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
        if v in (False, None, "", "Off"):
            return 0.0
        n = to_number(v if isinstance(v, str) else w.on_state())
        return n if n is not None else 0.0
    n = to_number(v)
    return n if n is not None else 0.0


def calc_order(f: fitz.Document) -> list[int]:
    """AcroForm CO 배열의 xref 차례."""
    cat = f.pdf_catalog()
    t, v = f.xref_get_key(cat, "AcroForm/CO")
    if t != "array":
        return []
    return [int(x) for x in re.findall(r"(\d+)\s+0\s+R", v)]


def recalc(doc) -> list[int]:
    """계산 칸을 CO 차례대로 다시 셈한다. 값이 바뀐 쪽 번호들. doc 은 우리 Document 또는 fitz 문서."""
    from contextlib import nullcontext
    f = getattr(doc, "fitz", doc)
    lock = getattr(doc, "lock", None)
    changed: set[int] = set()
    with lock if lock is not None else nullcontext():
        order = calc_order(f)
        if not order:
            return []
        from .annots import save_widget
        for x in order:
            spec = parse_calc(_script(f, x, "C"))
            if spec["kind"] != "simple":
                continue
            names = set(spec["fields"])
            vals, target = [], None
            ws, _pages = _widgets(f)
            for i, w in ws:
                if w.field_name in names:
                    vals.append(_field_number(w))
                if w.xref == x:
                    target = (i, w)
            if target is None:
                continue
            i, w = target
            res = compute(spec["op"], vals)
            txt = _num_text(round(res, 10))
            if str(w.field_value or "") != txt:
                w.field_value = txt
                save_widget(w)
                changed.add(i)
    return sorted(changed)


def _script(f: fitz.Document, x: int, key: str) -> str:
    t, v = f.xref_get_key(x, f"AA/{key}/JS")
    if t == "string":
        return js_text(v)
    if t == "xref":
        try:
            return js_text(f.xref_stream(int(v.split()[0])) or b"")
        except Exception:
            return ""
    return ""


def shown_text(w: fitz.Widget) -> str | None:
    """서식을 건 텍스트·콤보 칸이 보일 글. 서식이 없거나 같은 글이면 None."""
    if w.field_type not in (fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX):
        return None
    fmt = parse_format(js_text(w.script_format), js_text(w.script_stroke))
    if fmt["kind"] in ("none", "custom"):
        return None
    raw = "" if w.field_value is None else str(w.field_value)
    out = format_value(fmt, raw)
    return out if out != raw else None
