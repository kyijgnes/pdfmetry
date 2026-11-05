"""PDF → PPT. 쪽마다 슬라이드 하나.

mode="text"(기본): 배경은 글자를 뺀 쪽 그림(그림·선·도형), 그 위에 글 덩어리마다 고칠 수 있는 글 상자.
mode="image": 쪽 전체 그림 한 장(옛 동작). 두 방식 다 쪽 글자를 슬라이드 노트에도 넣는다.

글자층이 OCR 로 넣은 보이지 않는 글자(render_mode=3)뿐인 쪽은 배경 그림에 글자가 그대로 남아 있으므로,
글 상자 바탕을 그 자리 배경색으로 칠해 그림 속 글자를 덮는다.
"""
from __future__ import annotations

import io
import os
import re
from typing import Callable, Optional

import fitz

Progress = Optional[Callable[[int, int, str], None]]
PT = 12700   # EMU / pt

# ── 글꼴 ───────────────────────────────────────────────────────────────
_installed: dict[str, tuple[str, str]] | None = None
_font_objs: dict[str, object] = {}


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_]", "", s).lower()


def installed_fonts() -> dict[str, tuple[str, str]]:
    """윈도우에 깔린 글꼴 {정규화한 이름: (화면 이름, 파일 경로)}. 레지스트리에서 한 번만 읽는다. 못 읽으면 빈 것."""
    global _installed
    if _installed is not None:
        return _installed
    names: dict[str, tuple[str, str]] = {}
    try:
        import winreg
        windir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                k = winreg.OpenKey(root, key_path)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    v, data, _t = winreg.EnumValue(k, i)
                except OSError:
                    break
                i += 1
                v = re.sub(r"\s*\(.*?\)\s*$", "", v)
                path = data if os.path.isabs(str(data)) else os.path.join(windir, str(data))
                for part in v.split("&"):
                    part = part.strip()
                    if part:
                        names.setdefault(_norm(part), (part, path))
    except Exception:  # noqa: BLE001
        pass
    _installed = names
    return names


def _font_obj(name: str):
    """글자 너비를 잴 fitz.Font. 파일을 못 찾으면 None."""
    if name in _font_objs:
        return _font_objs[name]
    f = None
    hit = installed_fonts().get(_norm(name))
    if hit and os.path.isfile(hit[1]):
        try:
            f = fitz.Font(fontfile=hit[1])
        except Exception:  # noqa: BLE001
            f = None
    _font_objs[name] = f
    return f


def text_width(text: str, size: float, latin: str, ea: str) -> float | None:
    """대신 쓸 글꼴로 찍었을 때 글 너비(pt). 잴 수 없으면 None."""
    fl, fe = _font_obj(latin), _font_obj(ea)
    if fl is None and fe is None:
        return None
    w = 0.0
    for ch in text:
        f = fe if (_HANGUL.match(ch) or _CJK.match(ch)) else fl
        f = f or fl or fe
        w += f.text_length(ch, fontsize=size)
    return w


_STYLE = re.compile(r"[,\-]?(bold|italic|oblique|regular|medium|light|semibold|demibold|black|heavy|"
                    r"extrabold|ultrabold|thin|book|roman|mt|psmt|ps)$", re.I)
_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힯]")
_CJK = re.compile(r"[぀-ヿ一-鿿]")
# PDF 에 자주 나오는 이름 → 윈도우 글꼴
_ALIAS = {
    "helvetica": "Arial", "arial": "Arial", "arialmt": "Arial", "times": "Times New Roman",
    "timesnewroman": "Times New Roman", "timesroman": "Times New Roman", "courier": "Courier New",
    "couriernew": "Courier New", "symbol": "Symbol", "zapfdingbats": "Wingdings",
    "malgungothic": "Malgun Gothic", "맑은고딕": "Malgun Gothic", "batang": "Batang", "바탕": "Batang",
    "gulim": "Gulim", "굴림": "Gulim", "dotum": "Dotum", "돋움": "Dotum", "gungsuh": "Gungsuh", "궁서": "Gungsuh",
}


def base_font_name(pdf_name: str) -> str:
    """「ABCDEF+NanumGothic-Bold」 → 「NanumGothic」."""
    n = pdf_name.split("+", 1)[-1]
    for _ in range(3):
        m = _STYLE.search(n)
        if not m or m.start() == 0:
            break
        n = n[:m.start()]
    return n.strip(",- ") or pdf_name


def font_for(pdf_name: str, text: str) -> tuple[str, str]:
    """(라틴 글꼴, 동아시아 글꼴). 이 PC 에 없는 글꼴은 비슷한 윈도우 글꼴로 대신한다."""
    base = base_font_name(pdf_name)
    nb = _norm(base)
    have = installed_fonts()
    serif = bool(re.search(r"batang|myeongjo|myungjo|ming|song|serif|times|명조|바탕", base, re.I))
    if nb in _ALIAS:
        latin = _ALIAS[nb]
    elif nb in have:
        latin = have[nb][0]
    elif re.search(r"courier|mono|consol", base, re.I):
        latin = "Courier New"
    else:
        latin = "Times New Roman" if serif else "Arial"
    ea_default = "Batang" if serif else "Malgun Gothic"
    if nb in have and (_HANGUL.search(text) or _CJK.search(text)):
        ea = have[nb][0]
    elif nb in _ALIAS and _ALIAS[nb] in ("Malgun Gothic", "Batang", "Gulim", "Dotum", "Gungsuh"):
        ea = _ALIAS[nb]
    else:
        ea = ea_default
    if _HANGUL.search(text) and latin in ("Arial", "Times New Roman", "Courier New") and nb not in _ALIAS:
        latin = ea   # 한글이 든 글 상자는 라틴 글자도 같은 글꼴로(섞이면 줄 높이가 흔들림)
    return latin, ea


# ── 쪽 읽기 ────────────────────────────────────────────────────────────
def _invisible_only(page: fitz.Page) -> bool:
    """글자가 있고 전부 보이지 않는 글자(OCR 층)인가."""
    try:
        tr = page.get_texttrace()
    except Exception:  # noqa: BLE001
        return False
    return bool(tr) and all(t.get("type") == 3 for t in tr)


def text_blocks(page: fitz.Page) -> list[dict]:
    """가로 글 덩어리만: [{"bbox", "lines": [{"bbox", "spans": [...]}]}]. 빈 줄·세로·기운 줄은 뺀다(그림에 남김)."""
    out = []
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines = []
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if abs(dy) > 0.01 or dx <= 0:
                continue
            spans = [s for s in ln.get("spans", []) if s.get("text")]
            if not "".join(s["text"] for s in spans).strip():
                continue
            lines.append({"bbox": fitz.Rect(ln["bbox"]), "spans": _merge_spans(spans)})
        # 한 덩어리 안에서도 아래로 쌓이는 줄만 한 상자에 둔다(표의 한 행처럼 옆으로 놓인 줄은 따로)
        group: list[dict] = []
        for ln in lines:
            if group:
                prev = group[-1]["bbox"]
                if ln["bbox"].y0 < prev.y0 + 0.5 * prev.height:
                    out.append(_box(group)); group = []
            group.append(ln)
        if group:
            out.append(_box(group))
    return out


def _box(lines: list[dict]) -> dict:
    r = fitz.Rect(lines[0]["bbox"])
    for ln in lines[1:]:
        r |= ln["bbox"]
    return {"bbox": r, "lines": lines}


def _style(s: dict) -> tuple:
    f = s.get("font") or ""
    f = "Type3" if f.startswith("Type3") else f   # Type3 는 글자마다 글꼴 번호가 달라 이름으로는 못 묶는다
    return (f, round(s.get("size", 0), 1), s.get("flags", 0) & (16 | 2), s.get("color"))


def _merge_spans(spans: list[dict]) -> list[dict]:
    """모양이 같은 이웃 조각을 하나로(Type3 글꼴은 글자마다 조각이라 그대로면 run 이 수백 개)."""
    out: list[dict] = []
    for s in spans:
        if out and _style(out[-1]) == _style(s):
            m = out[-1]
            m["text"] += s["text"]
            m["bbox"] = tuple(fitz.Rect(m["bbox"]) | fitz.Rect(s["bbox"]))
        else:
            out.append(dict(s))
    return out


def _background(page: fitz.Page, blocks: list[dict], dpi: int, remove_text: bool) -> fitz.Pixmap:
    """글자를 뺀 쪽 그림. 원본은 건드리지 않고 그 쪽만 사본에 옮겨 지운다."""
    if not remove_text or not blocks:
        return page.get_pixmap(dpi=dpi, alpha=False)
    tmp = fitz.open()
    tmp.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    p = tmp[0]
    # 사본 쪽의 좌표계는 원본과 같다. 글 상자로 옮긴 줄 자리만 글자를 지운다.
    for b in blocks:
        for ln in b["lines"]:
            p.add_redact_annot(ln["bbox"])
    p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                       text=fitz.PDF_REDACT_TEXT_REMOVE)
    pix = p.get_pixmap(dpi=dpi, alpha=False)
    tmp.close()
    return pix


def _fill_color(pix: fitz.Pixmap, rect: fitz.Rect, scale: float) -> tuple[int, int, int]:
    """글 상자 테두리 바로 바깥 화소의 가운데값(배경색)."""
    x0, y0 = int(rect.x0 * scale) - 2, int(rect.y0 * scale) - 2
    x1, y1 = int(rect.x1 * scale) + 2, int(rect.y1 * scale) + 2
    pts = []
    for x in range(x0, x1 + 1, max(1, (x1 - x0) // 12 or 1)):
        pts += [(x, y0), (x, y1)]
    for y in range(y0, y1 + 1, max(1, (y1 - y0) // 6 or 1)):
        pts += [(x0, y), (x1, y)]
    vals = []
    for x, y in pts:
        if 0 <= x < pix.width and 0 <= y < pix.height:
            vals.append(pix.pixel(x, y)[:3])
    if not vals:
        return (255, 255, 255)
    return tuple(sorted(v[i] for v in vals)[len(vals) // 2] for i in range(3))


# ── 글 상자 ────────────────────────────────────────────────────────────
def _set_run_fonts(run, latin: str, ea: str):
    """라틴·동아시아 글꼴과 lang=ko-KR(한글 낱말 단위 줄나눔)."""
    from pptx.oxml.ns import qn

    rpr = run._r.get_or_add_rPr()
    rpr.set("lang", "ko-KR")
    rpr.set("altLang", "en-US")
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is not None:
            rpr.remove(el)
    # 차례: (ln, fill …) 뒤에 latin, ea, cs
    for tag, face in (("a:latin", latin), ("a:ea", ea), ("a:cs", latin)):
        el = rpr.makeelement(qn(tag), {"typeface": face})
        rpr.append(el)


def _add_textbox(slide, block: dict, k: float, ox: int, oy: int, fill: tuple | None):
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Emu, Pt

    r = block["bbox"]
    left, top = ox + int(r.x0 * PT * k), oy + int(r.y0 * PT * k)
    width, height = max(int(r.width * PT * k), PT), max(int(r.height * PT * k), PT)
    # 글꼴 너비가 원본과 조금 달라도 줄 끝이 잘리지 않게 오른쪽을 조금 넉넉히
    tb = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(int(width * 1.04) + PT), Emu(height))
    tf = tb.text_frame
    tf.word_wrap = False
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    if fill is not None:
        tb.fill.solid()
        tb.fill.fore_color.rgb = RGBColor(*fill)
    lines = block["lines"]
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        size = max((s["size"] for s in ln["spans"]), default=10)
        # 줄 간격: 다음 줄 윗변까지(마지막 줄은 제 높이). 줄 높이보다 작게는 안 둠
        nxt = lines[i + 1]["bbox"].y0 - ln["bbox"].y0 if i + 1 < len(lines) else ln["bbox"].height
        p.line_spacing = Pt(max(nxt, ln["bbox"].height, size) * k)
        p.space_before = p.space_after = Pt(0)
        # 줄이 덩어리 왼쪽보다 들어가 있으면 들여쓴다
        indent = ln["bbox"].x0 - r.x0
        if indent > 1:
            p.level = 0
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", str(int(indent * PT * k)))
        runs = []
        for s in ln["spans"]:
            text = s["text"]
            run = p.add_run()
            run.text = text
            f = run.font
            f.size = Pt(max(1.0, round(s["size"] * k * 2) / 2))
            flags = s.get("flags", 0)
            fname = s.get("font", "")
            f.bold = bool(flags & 16) or bool(re.search(r"bold|black|heavy", fname, re.I))
            f.italic = bool(flags & 2) or bool(re.search(r"italic|oblique", fname, re.I))
            c = s.get("color", 0) or 0
            f.color.rgb = RGBColor((c >> 16) & 255, (c >> 8) & 255, c & 255)
            latin, ea = font_for(fname, text)
            _set_run_fonts(run, latin, ea)
            runs.append((run, text, s["size"], latin, ea))
        # 대신 쓴 글꼴은 너비가 달라 줄이 넘치거나 모자란다. 줄마다 글자 간격으로 원래 줄 너비에 맞춘다
        _fit_line(runs, ln["bbox"].width, k)
    return tb


def _fit_line(runs: list, target: float, k: float):
    total = 0.0
    n = 0
    for i, (_r, text, size, latin, ea) in enumerate(runs):
        t = text.rstrip() if i == len(runs) - 1 else text
        w = text_width(t, size, latin, ea)
        if w is None:
            return
        total += w
        n += len(t)
    if n < 2 or target <= 0:
        return
    size = max(r[2] for r in runs)
    gap = (target - total) / n   # pt, 글자마다
    gap = max(-0.25 * size, min(0.3 * size, gap))
    if abs(gap) > 0.05:
        for run, *_x in runs:
            run._r.get_or_add_rPr().set("spc", str(int(round(gap * k * 100))))


def _say(progress: Progress, k: int, n: int, msg: str):
    if progress:
        progress(k, n, msg)


def to_pptx(doc: fitz.Document, out_path: str, pages=None, dpi: int = 150, progress: Progress = None,
            mode: str = "text") -> str:
    from pptx import Presentation
    from pptx.util import Emu

    d = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(d, exist_ok=True)
    targets = list(range(doc.page_count)) if pages is None else [p for p in pages if 0 <= p < doc.page_count]
    prs = Presentation()
    if targets:
        r = doc[targets[0]].rect
        prs.slide_width = Emu(int(r.width * PT))
        prs.slide_height = Emu(int(r.height * PT))
    blank = prs.slide_layouts[6]
    for n, pno in enumerate(targets):
        _say(progress, n, len(targets), f"슬라이드 만드는 중 {pno + 1}쪽")
        page = doc[pno]
        slide = prs.slides.add_slide(blank)
        # 슬라이드 크기는 하나뿐이라 판형이 다른 쪽은 비율을 지켜 가운데에 둔다(늘이면 찌그러진다)
        sw, sh = int(prs.slide_width), int(prs.slide_height)
        k = min(sw / (page.rect.width * PT), sh / (page.rect.height * PT))
        w_, h_ = int(page.rect.width * PT * k), int(page.rect.height * PT * k)
        ox, oy = (sw - w_) // 2, (sh - h_) // 2
        blocks = text_blocks(page) if mode == "text" else []
        ocr_page = bool(blocks) and _invisible_only(page)
        pix = _background(page, blocks, dpi, remove_text=not ocr_page)
        slide.shapes.add_picture(io.BytesIO(pix.tobytes("png")), ox, oy, width=w_, height=h_)
        scale = pix.width / page.rect.width
        for b in blocks:
            fill = _fill_color(pix, b["bbox"], scale) if ocr_page else None
            _add_textbox(slide, b, k, ox, oy, fill)
        text = page.get_text("text").strip()
        if text:
            slide.notes_slide.notes_text_frame.text = text[:30000]
    prs.save(out_path)
    _say(progress, len(targets), len(targets), "PPT 변환 끝")
    return out_path
