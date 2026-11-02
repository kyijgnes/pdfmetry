"""스탬프·서명: 진짜 Stamp 주석(/Subtype /Stamp)으로 찍는다.

외관은 우리가 그린 그림(투명 PNG)을 Stamp 주석의 외관 그림(/AP 의 /I)으로 넣는다. MuPDF 는 주석을 다시 그릴 때
(색·불투명도·자리 바꾸기) 이 그림을 그대로 두므로 옮기거나 크기를 바꿔도 외관이 살아 있다. 벡터 외관을 직접 넣으면
다시 그릴 때 MuPDF 가 자기 영어 스탬프로 덮어써서 쓰지 않는다.

옛 버전이 찍은 글자 스탬프(테두리 글상자, 주제 「Stamp」)는 그대로 읽고 고친다(annots.stamp_text).

스탬프 하나를 가리키는 값(spec): {"group": "standard"|"sign"|"dynamic"|"digital", "id": 이름}
                               {"group": "custom", "id": 사용자 스탬프 id} · {"group": "signature", "file": 그림 경로}
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time

import fitz

from .document import Document
from . import fonts as F

Color = tuple[float, float, float]

GREEN = (0.13, 0.5, 0.13)
RED = (0.8, 0.0, 0.0)
BLUE = (0.1, 0.3, 0.75)
GRAY = (0.4, 0.4, 0.4)
COLORS = {"red": ("빨강", RED), "green": ("초록", GREEN), "blue": ("파랑", BLUE), "gray": ("회색", GRAY)}

# 묶음(메뉴 차례). 사용자 지정은 저장한 스탬프에서 채운다.
GROUPS = [("custom", "사용자 지정"), ("standard", "표준 비즈니스"), ("sign", "서명"), ("dynamic", "동적"),
          ("digital", "디지털 스탬프")]

# (PDF /Name, 메뉴 이름, 스탬프 글자, 색, 꼴)
STANDARD = [
    ("Approved", "승인됨", "APPROVED", GREEN, "rect"),
    ("NotApproved", "승인되지 않음", "NOT APPROVED", RED, "rect"),
    ("Draft", "초안", "DRAFT", BLUE, "rect"),
    ("Final", "최종", "FINAL", GREEN, "rect"),
    ("Completed", "완료", "COMPLETED", GREEN, "rect"),
    ("Confidential", "기밀", "CONFIDENTIAL", RED, "rect"),
    ("ForPublicRelease", "공개 자료", "FOR PUBLIC RELEASE", GREEN, "rect"),
    ("NotForPublicRelease", "비공개 자료", "NOT FOR PUBLIC RELEASE", RED, "rect"),
    ("ForComment", "검토용", "FOR COMMENT", BLUE, "rect"),
    ("Void", "비어있음", "VOID", RED, "rect"),
    ("PreliminaryResults", "예비 결과", "PRELIMINARY RESULTS", BLUE, "rect"),
    ("InformationOnly", "정보만", "INFORMATION ONLY", BLUE, "rect"),
]
SIGN = [
    ("Witness", "증인", "WITNESS", RED, "right_arrow"),
    ("InitialHere", "이름의 앞글자", "INITIAL HERE", RED, "right_arrow"),
    ("SignHere", "여기에 서명", "SIGN HERE", RED, "right_arrow"),
    ("Accepted", "승인됨", "Accepted", GREEN, "rect"),
    ("Rejected", "거절됨", "Rejected", RED, "rect"),
]
DYNAMIC = [
    ("Revised", "변경됨", "REVISED", BLUE, "rect"),
    ("Reviewed", "검토됨", "REVIEWED", BLUE, "rect"),
    ("Received", "수신됨", "RECEIVED", BLUE, "rect"),
    ("Approved", "승인됨", "APPROVED", GREEN, "rect"),
    ("Confidential", "기밀", "CONFIDENTIAL", RED, "rect"),
]
# 디지털 스탬프: ID 편집의 이름·회사·부서가 들어간다
DIGITAL = [
    ("NameSeal", "이름 도장", "", RED, "circle"),
    ("ConfirmSeal", "확인 도장", "확인", RED, "circle"),
    ("Identity", "이름·소속·날짜", "", BLUE, "rect"),
]
BUILTIN = {"standard": STANDARD, "sign": SIGN, "dynamic": DYNAMIC, "digital": DIGITAL}

STYLES = [("text", "글자만"), ("rect", "사각형"), ("left_arrow", "왼쪽 화살표"), ("right_arrow", "오른쪽 화살표"),
          ("circle", "원")]
# (보이는 꼴, strftime). 첫 칸은 날짜 없음
DATE_FORMATS = [("없음", ""), ("yyyy.MM.dd HH:mm", "%Y.%m.%d %H:%M"), ("yyyy/MM/dd HH:mm", "%Y/%m/%d %H:%M"),
                ("yyyy/MM/dd", "%Y/%m/%d"), ("MM/dd/yyyy", "%m/%d/%Y"), ("MM/dd", "%m/%d"),
                ("dd.MM.yyyy", "%d.%m.%Y"), ("dd/MM/yy", "%d/%m/%y")]
IDENTITIES = [("none", "없음"), ("name", "이름"), ("company", "회사"), ("department", "부서/직위")]
DYN_DATE = "%Y.%m.%d %H:%M"   # 표준 동적 스탬프의 날짜 꼴

LINE_H = 1.25        # 줄 높이(글자 크기의 배)
BASE_FS = 18.0       # 처음 크기의 첫 줄 글자 크기(pt)
PNG_PX = 1400        # 외관 그림의 긴 변 최대 픽셀


def builtin(group: str, sid: str) -> tuple | None:
    return next((s for s in BUILTIN.get(group, []) if s[0] == sid), None)


# ── 사용자 스탬프·서명 저장 ─────────────────────────────────────────
class StampStore:
    """설정 폴더의 stamps.json + stamps\\ 그림 폴더.

    custom: [{"id","name","category","type": "image"|"dynamic", "file"(그림 파일 이름), "date_fmt",
              "width","height", 동적이면 "style","color","text","identity","font","fontsize"(0=자동),"vertical"}]
    signatures: [{"id","file","how": "draw"|"type"|"photo"}] · scale: 찍는 크기 배율 · identity: {company, department}
    """

    def __init__(self, folder: str | None):
        self.folder = folder
        self.data: dict = {"custom": [], "signatures": [], "scale": 1.0, "identity": {}}
        if not folder:
            return
        try:
            with open(os.path.join(folder, "stamps.json"), encoding="utf-8") as fp:
                raw = json.load(fp)
            if isinstance(raw, dict):
                for k in self.data:
                    if isinstance(raw.get(k), type(self.data[k])):
                        self.data[k] = raw[k]
        except (OSError, ValueError, TypeError):
            pass

    # 그림 폴더
    def image_dir(self) -> str:
        return os.path.join(self.folder or "", "stamps")

    def image_path(self, name: str) -> str:
        return os.path.join(self.image_dir(), name)

    def _new_id(self, head: str) -> str:
        n = int(time.time() * 1000)
        ids = {e["id"] for e in self.data["custom"] + self.data["signatures"]}
        while f"{head}{n}" in ids:
            n += 1
        return f"{head}{n}"

    def save(self) -> None:
        if not self.folder:
            return
        try:
            os.makedirs(self.folder, exist_ok=True)
            path = os.path.join(self.folder, "stamps.json")
            with open(path + ".tmp", "w", encoding="utf-8") as fp:
                json.dump(self.data, fp, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    # 사용자 스탬프
    def custom(self) -> list[dict]:
        return list(self.data["custom"])

    def get_custom(self, sid: str) -> dict | None:
        return next((e for e in self.data["custom"] if e.get("id") == sid), None)

    def categories(self) -> list[str]:
        return sorted({e.get("category", "") for e in self.data["custom"] if e.get("category")})

    def put_custom(self, entry: dict, png: bytes | None = None) -> str:
        """새로 넣거나(id 없음) 고친다. png 를 주면 그 스탬프의 그림으로 저장한다. id 를 돌려준다."""
        e = dict(entry)
        if not e.get("id"):
            e["id"] = self._new_id("c")
        if png is not None:
            os.makedirs(self.image_dir(), exist_ok=True)
            e["file"] = e["id"] + ".png"
            with open(self.image_path(e["file"]), "wb") as fp:
                fp.write(png)
        lst = self.data["custom"]
        k = next((i for i, x in enumerate(lst) if x.get("id") == e["id"]), None)
        if k is None:
            lst.append(e)
        else:
            lst[k] = e
        self.save()
        return e["id"]

    def delete_custom(self, sid: str) -> None:
        e = self.get_custom(sid)
        if not e:
            return
        self.data["custom"].remove(e)
        if e.get("file"):
            try:
                os.remove(self.image_path(e["file"]))
            except OSError:
                pass
        self.save()

    # 서명
    def signatures(self) -> list[dict]:
        return [e for e in self.data["signatures"] if os.path.exists(self.image_path(e.get("file", "")))]

    def add_signature(self, png: bytes, how: str) -> str:
        """서명 그림을 저장하고 그 파일 경로를 돌려준다."""
        sid = self._new_id("s")
        os.makedirs(self.image_dir(), exist_ok=True)
        name = sid + ".png"
        with open(self.image_path(name), "wb") as fp:
            fp.write(png)
        self.data["signatures"].append({"id": sid, "file": name, "how": how})
        self.save()
        return self.image_path(name)

    def delete_signature(self, sid: str) -> None:
        e = next((x for x in self.data["signatures"] if x.get("id") == sid), None)
        if not e:
            return
        self.data["signatures"].remove(e)
        try:
            os.remove(self.image_path(e["file"]))
        except OSError:
            pass
        self.save()

    # 찍는 크기·신원
    @property
    def scale(self) -> float:
        try:
            return max(0.2, min(10.0, float(self.data.get("scale", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def remember_scale(self, s: float) -> None:
        self.data["scale"] = round(max(0.2, min(10.0, s)), 3)
        self.save()

    def identity(self) -> dict:
        i = self.data.get("identity") or {}
        return {"company": i.get("company", ""), "department": i.get("department", "")}

    def set_identity(self, company: str, department: str) -> None:
        self.data["identity"] = {"company": company, "department": department}
        self.save()


def default_folder() -> str:
    from ..appinfo import data_dir
    return data_dir()


_store: StampStore | None = None


def store() -> StampStore:
    global _store
    if _store is None:
        _store = StampStore(default_folder())
    return _store


def reset_store() -> None:
    """설정 폴더가 바뀐 뒤(시험) 다시 읽게."""
    global _store
    _store = None


# ── 외관 정하기 ────────────────────────────────────────────────────
def _date(fmt: str, now: _dt.datetime | None) -> str:
    return (now or _dt.datetime.now()).strftime(fmt) if fmt else ""


def face(spec: dict, *, author: str = "", identity: dict | None = None, now: _dt.datetime | None = None,
         st: StampStore | None = None) -> dict:
    """스탬프 하나를 그릴 값: shape · color · lines[(글자, 크기 배)] · image · font · fontsize · vertical ·
    name(PDF /Name) · title(주석 내용) · subject. 모르는 스탬프면 ValueError."""
    st = st or store()
    ident = {"name": author, **(identity or st.identity())}
    g = spec.get("group")
    base = {"shape": "rect", "color": RED, "lines": [], "image": "", "font": "", "fontsize": 0, "vertical": False,
            "subject": "스탬프"}
    if g == "signature":
        path = spec.get("file", "")
        if not path or not os.path.exists(path):
            raise ValueError("서명 그림을 찾지 못했습니다")
        return dict(base, shape="none", image=path, name="Signature", title="서명", subject="서명")
    if g in BUILTIN:
        s = builtin(g, spec.get("id", ""))
        if s is None:
            raise ValueError("없는 스탬프입니다")
        name, title, text, color, shape = s
        f = dict(base, shape=shape, color=color, name=name, title=title)
        if g == "dynamic":
            who = ", ".join(x for x in (ident["name"], _date(DYN_DATE, now)) if x)
            f["lines"] = [(text, 1.0), (who, 0.5)]
        elif g == "digital":
            org = " ".join(x for x in (ident["company"], ident["department"]) if x)
            if name == "NameSeal":
                f["lines"] = [(ident["name"] or "이름", 1.0)]
            elif name == "ConfirmSeal":
                f["lines"] = [(text, 0.8), (ident["name"] or "이름", 0.6)]
            else:
                f["lines"] = [(ident["name"] or "이름", 1.0)] + ([(org, 0.55)] if org else []) + [(_date(DYN_DATE, now), 0.55)]
        else:
            f["lines"] = [(text, 1.0)]
        return f
    if g == "custom":
        e = st.get_custom(spec.get("id", ""))
        if e is None:
            raise ValueError("지운 스탬프입니다")
        date = _date(e.get("date_fmt", ""), now)
        f = dict(base, name="Custom", title=e.get("name") or "스탬프", vertical=bool(e.get("vertical")),
                 width=float(e.get("width") or 0), height=float(e.get("height") or 0))
        if e.get("type") == "image":
            path = st.image_path(e.get("file", ""))
            if not os.path.exists(path):
                raise ValueError("스탬프 그림을 찾지 못했습니다")
            f.update(shape="none", image=path, lines=[(date, 0.5)] if date else [], color=(0, 0, 0))
            return f
        lines = [(e.get("text") or "", 1.0)]
        who = {"name": ident["name"], "company": ident["company"], "department": ident["department"]}.get(e.get("identity", "none"), "")
        sub = ", ".join(x for x in (who, date) if x)
        if sub:
            lines.append((sub, 0.5))
        f.update(shape=e.get("style", "rect"), color=COLORS.get(e.get("color", "red"), COLORS["red"])[1], lines=lines,
                 font=e.get("font", ""), fontsize=float(e.get("fontsize") or 0))
        return f
    raise ValueError("없는 스탬프입니다")


def _font_path(fc: dict, text: str) -> str:
    if fc.get("font"):
        path = F.font_file(fc["font"], bold=True)[0]
        if path and F.covers(path, text):
            return path
    return F.korean_fontfile(bold=True)


def _text_w(path: str, text: str) -> float:
    """글자 크기 1 일 때 너비."""
    try:
        return F.font_object(path).text_length(text, fontsize=1)
    except Exception:  # noqa: BLE001
        return len(text) * 0.6


def _image_aspect(path: str) -> float:
    try:
        pix = fitz.Pixmap(path)
        return pix.width / max(1, pix.height)
    except Exception:  # noqa: BLE001
        return 1.0


def natural_size(fc: dict) -> tuple[float, float]:
    """보이는 방향의 처음 크기(pt, 가로·세로). 사용자 스탬프에 정한 크기가 있으면 그 크기."""
    if fc.get("width") and fc.get("height"):
        return fc["width"], fc["height"]
    if fc["image"]:
        h = 60.0
        w = h * _image_aspect(fc["image"])
        if fc["lines"]:
            h *= 1.25
        if fc["shape"] == "none" and fc["name"] == "Signature":
            w, h = 150.0, 150.0 / _image_aspect(fc["image"])
    else:
        fs = fc["fontsize"] or BASE_FS
        path = _font_path(fc, "".join(t for t, _ in fc["lines"]))
        tw = max((_text_w(path, t) * fs * r for t, r in fc["lines"] if t), default=fs * 3)
        th = sum(fs * r * LINE_H for t, r in fc["lines"] if t) or fs * LINE_H
        pad = fs * 0.6
        w, h = tw + pad * 2, th + pad
        if fc["shape"] in ("left_arrow", "right_arrow"):
            w += h * 0.5
        elif fc["shape"] == "circle":
            w = h = max(w, h) * 1.15
    return (h, w) if fc["vertical"] else (w, h)


def _draw(page: fitz.Page, fc: dict) -> None:
    """빈 쪽(보이는 방향, 세로면 눕힌 크기) 전체에 스탬프를 그린다."""
    r = page.rect
    w, h = r.width, r.height
    col = fc["color"]
    lw = max(1.0, min(6.0, min(w, h) / 18))     # 처음 크기(높이 36pt)에서 굵기 2
    inner = fitz.Rect(r.x0 + lw, r.y0 + lw, r.x1 - lw, r.y1 - lw)
    text_box = fitz.Rect(inner)
    shape = fc["shape"]
    if shape == "rect":
        page.draw_rect(inner, color=col, width=lw, radius=min(0.2, 6 / max(1, min(w, h))))
    elif shape in ("left_arrow", "right_arrow"):
        hw = inner.height / 2
        x0, y0, x1, y1, ym = inner.x0, inner.y0, inner.x1, inner.y1, (inner.y0 + inner.y1) / 2
        if shape == "right_arrow":
            pts = [(x0, y0), (x1 - hw, y0), (x1, ym), (x1 - hw, y1), (x0, y1), (x0, y0)]
            text_box.x1 -= hw * 0.8
        else:
            pts = [(x1, y0), (x0 + hw, y0), (x0, ym), (x0 + hw, y1), (x1, y1), (x1, y0)]
            text_box.x0 += hw * 0.8
        page.draw_polyline(pts, color=col, width=lw, closePath=True)
    elif shape == "circle":
        page.draw_oval(inner, color=col, width=lw)
        s = min(inner.width, inner.height) * 0.68
        c = (inner.tl + inner.br) / 2
        text_box = fitz.Rect(c.x - inner.width * 0.34, c.y - s / 2, c.x + inner.width * 0.34, c.y + s / 2)
    lines = [(t, k) for t, k in fc["lines"] if t]
    if fc["image"]:
        img_box = fitz.Rect(r)
        if lines:
            img_box.y1 = r.y0 + r.height * 0.8
            text_box = fitz.Rect(r.x0, img_box.y1, r.x1, r.y1)
        page.insert_image(img_box, filename=fc["image"], keep_proportion=False)
    if not lines:
        return
    path = _font_path(fc, "".join(t for t, _ in lines))
    pad = 0 if fc["image"] else min(text_box.height, text_box.width) * 0.08
    box = fitz.Rect(text_box.x0 + pad, text_box.y0 + pad / 2, text_box.x1 - pad, text_box.y1 - pad / 2)
    # 줄마다 너비에 맞는 크기, 전체 높이에 맞는 크기 가운데 작은 쪽(정한 글자 크기가 있으면 그보다 크지 않게)
    fs = box.height / sum(k * LINE_H for _, k in lines)
    for t, k in lines:
        fs = min(fs, box.width / max(0.01, _text_w(path, t) * k))
    if fc["fontsize"]:   # 정한 글자 크기는 처음 크기 기준(크게 찍으면 같이 커진다)
        fs = min(fs, fc["fontsize"] * h / max(1.0, natural_size(dict(fc, vertical=False, width=0, height=0))[1]))
    total = sum(fs * k * LINE_H for _, k in lines)
    y = box.y0 + (box.height - total) / 2
    font = F.font_object(path)
    for t, k in lines:
        size = fs * k
        tw = _text_w(path, t) * size
        x = box.x0 + (box.width - tw) / 2
        base = y + size * LINE_H * 0.5 + (font.ascender + font.descender) / 2 * size
        page.insert_text((x, base), t, fontsize=size, fontfile=path, fontname="stampfont", color=col)
        y += size * LINE_H


def render_png(fc: dict, w: float, h: float, rotation: int = 0, px: int = PNG_PX) -> bytes:
    """보이는 방향 w×h(pt) 크기의 외관 그림. rotation 은 찍을 쪽의 회전(그 쪽에서 똑바로 보이게 돌려 둔다)."""
    vertical = fc["vertical"]
    dw, dh = (h, w) if vertical else (w, h)       # 세로 스탬프는 눕혀 그린 뒤 돌린다
    tmp = fitz.open()
    try:
        page = tmp.new_page(width=max(1.0, dw), height=max(1.0, dh))
        _draw(page, fc)
        s = max(1.0, min(8.0, px / max(dw, dh)))
        rot = (-rotation - (90 if vertical else 0)) % 360
        pix = page.get_pixmap(matrix=fitz.Matrix(s, s).prerotate(rot), alpha=True)
        return pix.tobytes("png")
    finally:
        tmp.close()


def preview_png(spec: dict, height_px: int = 48, **kw) -> bytes:
    """메뉴·관리 창 미리보기."""
    fc = face(spec, **kw)
    w, h = natural_size(fc)
    return render_png(fc, w, h, px=int(max(w, h) / h * height_px))


def face_preview_png(fc: dict, height_px: int = 96) -> bytes:
    w, h = natural_size(fc)
    return render_png(fc, w, h, px=int(max(w, h) / h * height_px))


# ── 찍기 ─────────────────────────────────────────────────────────
def _display_size(p: fitz.Page, r: fitz.Rect) -> tuple[float, float]:
    dr = fitz.Rect(r) * p.rotation_matrix
    return abs(dr.width), abs(dr.height)


def default_rect(doc: Document, page: int, point: fitz.Point, spec: dict, *, author: str = "",
                 st: StampStore | None = None) -> fitz.Rect:
    """한 번 눌러 찍을 자리: 누른 자리를 가운데로 기억한 크기만큼(쪽 밖으로 나가지 않게)."""
    st = st or store()
    p = doc.fitz[page]
    w, h = natural_size(face(spec, author=author, st=st))
    k = st.scale
    w, h = w * k, h * k
    dp = fitz.Point(point) * p.rotation_matrix
    pr = p.rect * p.rotation_matrix              # 보이는 쪽
    pw, ph = abs(pr.width), abs(pr.height)
    if w > pw or h > ph:
        f = min(pw / w, ph / h)
        w, h = w * f, h * f
    x0 = min(max(0.0, dp.x - w / 2), pw - w)
    y0 = min(max(0.0, dp.y - h / 2), ph - h)
    r = fitz.Rect(x0, y0, x0 + w, y0 + h) * p.derotation_matrix
    return r.normalize()


def remember_drag(doc: Document, page: int, rect: fitz.Rect, spec: dict, *, author: str = "",
                  st: StampStore | None = None) -> None:
    """끌어서 찍은 크기를 다음에 한 번 눌러 찍을 크기로(처음 크기의 배율로) 기억한다."""
    st = st or store()
    w, h = _display_size(doc.fitz[page], rect)
    try:
        nw, nh = natural_size(face(spec, author=author, st=st))
    except ValueError:
        return
    st.remember_scale(max(w / nw, h / nh))


def add(doc: Document, page: int, rect: fitz.Rect, spec: dict, *, author: str = "", now: _dt.datetime | None = None,
        st: StampStore | None = None) -> int:
    """Stamp 주석 하나를 찍고 xref 를 돌려준다. 동적 스탬프의 이름·날짜는 찍는 때의 값으로 굽는다."""
    fc = face(spec, author=author, now=now, st=st)
    p = doc.fitz[page]
    w, h = _display_size(p, rect)
    if w < 2 or h < 2:
        raise ValueError("스탬프 자리가 너무 작습니다")
    png = render_png(fc, w, h, p.rotation)
    doc.snapshot()
    with doc.lock:
        a = p.add_stamp_annot(fitz.Rect(rect).normalize(), stamp=png)
        doc.fitz.xref_set_key(a.xref, "Name", "/" + fc["name"])
        a = p.load_annot(a.xref)
        a.set_info(content=fc["title"], title=author, subject=fc["subject"])
        a.update()
        return a.xref


def is_stamp(doc: Document, xref: int) -> bool:
    """Stamp 주석이거나 옛 글자 스탬프(주제 Stamp 인 글상자)."""
    f = doc.fitz
    st = f.xref_get_key(xref, "Subtype")[1]
    return st == "/Stamp" or (st == "/FreeText" and f.xref_get_key(xref, "Subj")[1] in ("(Stamp)", "Stamp"))


def pdf_page_png(path: str, page: int = 0, px: int = PNG_PX) -> bytes:
    """사용자 지정 스탬프를 PDF 의 한 쪽으로 만들 때 그 쪽 그림(흰 바탕)."""
    with fitz.open(path) as src:
        if not 0 <= page < src.page_count:
            raise ValueError("없는 페이지입니다")
        pg = src[page]
        s = max(1.0, min(6.0, px / max(pg.rect.width, pg.rect.height)))
        return pg.get_pixmap(matrix=fitz.Matrix(s, s)).tobytes("png")


def image_png(path: str) -> bytes:
    """사용자 지정 스탬프를 그림 파일로 만들 때(읽을 수 없으면 ValueError)."""
    try:
        pix = fitz.Pixmap(path)
    except Exception as e:  # noqa: BLE001
        raise ValueError("이미지 형식이 지원되지 않습니다.") from e
    if pix.colorspace and pix.colorspace.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("png")
