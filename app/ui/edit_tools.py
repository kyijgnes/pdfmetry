"""편집 객체·주석 화면 명령: 자르기·복사·붙여넣기 · 쌓는 순서 · 맞춤·분배 · 주석 여러 쪽에 적용(설계 17, 4.2~4.6).

창(window.on_action)은 `is_key(key)` 인 명령을 `run(win, tab, key)` 로 넘기고, 누른 자리에 붙이기는
`paste(win, tab, at=(쪽, 쪽 좌표))` 로 부른다. 여러 엔진 함수를 묶는 명령은 `doc.batch()` 안에서 돌아 실행 취소 한 걸음이다.

모드마다 칸이 다르다(clipboard.mode_of): 손·선택 = 주석, 편집 모드 = 편집 객체, 양식 편집 = 양식 칸(form_tools).
편집 객체 여럿 고르기는 보기의 `sel_objs`(objects.list_objects 항목 + page, 고른 차례)이고 마지막 것이 `sel` 이다.
"""
from __future__ import annotations

import fitz
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from ..engine import annot_clip as AC
from ..engine import objects as O
from . import clipboard as CB

ORDER_KEYS = {f"obj_{h}": h for h in O.HOWS}
ALIGN_KEYS = {f"obj_align_{h}": h for h in O.ALIGN_HOWS}
KEYS = {"clip_copy", "clip_cut", "clip_paste", "obj_select_all", "annot_apply_pages"} | set(ORDER_KEYS) | set(ALIGN_KEYS)

ORDER_ITEMS = (("front", "맨 앞으로 가져오기"), ("forward", "앞으로 가져오기"), ("backward", "뒤로 보내기"),
               ("back", "맨 뒤로 보내기"))
ALIGN_ITEMS = (("left", "왼쪽 맞춤"), ("right", "오른쪽 맞춤"), ("top", "위쪽 맞춤"), ("bottom", "아래쪽 맞춤"),
               ("hcenter", "가로 가운데 맞춤"), ("vcenter", "세로 가운데 맞춤"), None,
               ("page_h", "페이지 가로 가운데"), ("page_v", "페이지 세로 가운데"), ("page_both", "페이지 한가운데"))
DIST_ITEMS = (("dist_h", "가로 분배"), ("dist_v", "세로 분배"))
NOTHING = "붙여 넣을 것이 없습니다"


def is_key(key: str) -> bool:
    return key in KEYS


def need(how: str) -> int:
    """그 맞춤·분배에 필요한 고른 수."""
    return 3 if how.startswith("dist") else 1 if how.startswith("page") else 2


# ── 고른 것 ─────────────────────────────────────────────────────
def picked_objects(v) -> tuple[int, list[dict]]:
    """편집 모드에서 고른 객체들 (쪽, 항목들). 여러 개 목록이 비었으면 지금 고른 덩어리·그림 하나."""
    if v.sel_objs:
        return v.sel_objs[0]["page"], list(v.sel_objs)
    s = v.sel
    if s and s.kind in ("block", "image") and s.rect is not None:
        it = O.find(v.doc, s.page, fitz.Rect(s.rect), "text" if s.kind == "block" else "image")
        if it is not None:
            return s.page, [dict(it, page=s.page)]
    return -1, []


def _ref(it: dict):
    return it["id"] if it.get("ours") and it.get("id") is not None else it


def select_ids(v, page: int, refs) -> None:
    """Id(또는 원문 항목)들로 다시 고른다(그리는 차례가 아니라 준 차례로)."""
    now = O.list_objects(v.doc, page)
    ours = {o["id"]: o for o in now if o["ours"]}
    out = []
    for x in refs:
        if isinstance(x, dict):
            k = _key(x)
            hit = next((o for o in now if _key(o) == k), None)
            if hit is not None:
                out.append(hit)
        elif x is not None and x in ours:
            out.append(ours[x])
    v.select_objects(page, out)


# ── 시스템 클립보드에 올릴 그림·글자 ─────────────────────────────────
def _qimage(spec: dict) -> QImage | None:
    try:
        pix = fitz.Pixmap(spec["image"])
        if spec.get("mask"):
            pix = fitz.Pixmap(pix, fitz.Pixmap(spec["mask"]))
        img = QImage.fromData(pix.tobytes("png"), "PNG")
        return None if img.isNull() else img
    except Exception:  # noqa: BLE001  그림을 못 풀면 글자만 올린다
        return None


def _png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


# ── 명령 ────────────────────────────────────────────────────────
def run(win, t, key: str) -> None:
    v = t.viewer
    mode = CB.mode_of(v)
    if key in ("clip_copy", "clip_cut", "clip_paste") and mode == "fields":
        from . import form_tools as FT
        FT.run(win, t, {"clip_copy": "field_copy", "clip_cut": "field_cut", "clip_paste": "field_paste"}[key])
        return
    if key == "clip_copy":
        copy(win, t, cut=False)
    elif key == "clip_cut":
        copy(win, t, cut=True)
    elif key == "clip_paste":
        paste(win, t)
    elif key == "obj_select_all":
        select_all(v)
    elif key == "annot_apply_pages":
        apply_pages(win, t)
    elif key in ORDER_KEYS:
        order(win, t, ORDER_KEYS[key])
    elif key in ALIGN_KEYS:
        align(win, t, ALIGN_KEYS[key])


def copy(win, t, cut: bool = False) -> bool:
    """지금 모드의 고른 것을 그 칸에 담는다(자르기는 담은 뒤 지우기 한 걸음). 담았으면 참."""
    v, d = t.viewer, t.doc
    word = "잘라 냈" if cut else "복사했"
    if CB.mode_of(v) == "objects":
        page, items = picked_objects(v)
        if not items:
            win.say("자르거나 복사할 객체 선택 필요"); return False
        specs = [O.spec(d, page, _ref(it)) for it in items]
        imgs = [s for s in specs if s["kind"] == "image"]
        text = "\n".join(s.get("text") or "" for s in specs if s["kind"] == "text").strip()
        CB.put("objects", specs, id(d), page, image=_qimage(imgs[0]) if len(imgs) == 1 else None, text=text)
        if cut:
            O.delete(d, page, [_ref(it) for it in items])
            v.clear_selection()
            t.changed([page])
        win.say(f"객체 {len(specs)}개를 {word}습니다")
        return True
    s = v.sel
    if not (s and s.kind == "annot"):
        win.say("자르거나 복사할 주석 선택 필요"); return False
    spec = AC.annot_spec(d, s.page, s.xref)
    if spec is None:
        win.say("이 주석은 복사할 수 없습니다"); return False
    CB.put("annots", [spec], id(d), s.page, text=(spec.get("info") or {}).get("content", ""))
    if cut:
        v.delete_selection()
    win.say(f"주석을 {word}습니다")
    return True


def _system_specs(v, page: int) -> list[dict]:
    """다른 프로그램에서 복사한 그림·글자를 쪽 가운데 놓을 편집 객체 자료로."""
    img, text = CB.system_content()
    p = v.doc.fitz[page]
    vis = fitz.Rect(p.rect)   # 보이는 쪽(회전 반영)
    if img is not None:
        w, h = img.width() * 72 / 96, img.height() * 72 / 96
        k = min(1.0, vis.width * 0.8 / max(w, 1), vis.height * 0.8 / max(h, 1))
        w, h = w * k, h * k
        r = fitz.Rect(vis.x0 + (vis.width - w) / 2, vis.y0 + (vis.height - h) / 2, 0, 0)
        r.x1, r.y1 = r.x0 + w, r.y0 + h
        m = fitz.Matrix(w, 0, 0, -h, r.x0, r.y1) * p.derotation_matrix   # 보이는 화면에서 바로 선 그림
        box = (r * p.derotation_matrix).normalize()
        return [{"kind": "image", "image": _png_bytes(img), "mask": None, "matrix": tuple(m), "opacity": 1.0,
                 "rect": tuple(box), "doc": None, "xref": 0}]
    text = text.strip()
    if not text:
        return []
    from ..engine import textbox as T
    fs = float(v.props.get("fontsize", 11) or 11)
    style = {"fontsize": fs, "color": (0, 0, 0), "bold": False, "italic": False, "align": 0}
    w, h = T.box_size(dict(style, text=text))
    w = min(vis.width * 0.8, w)
    r = fitz.Rect(vis.x0 + (vis.width - w) / 2, vis.y0 + (vis.height - h) / 2, 0, 0)
    r.x1, r.y1 = r.x0 + w, r.y0 + h
    box = (r * p.derotation_matrix).normalize()
    return [{"kind": "text", "text": text, "style": style, "rect": tuple(box), "matrix": None, "form": 0, "doc": None}]


def _union(rects) -> fitz.Rect:
    u = None
    for r in rects:
        r = fitz.Rect(r)
        u = r if u is None else u | r
    return u


def paste(win, t, at: tuple[int, fitz.Point] | None = None) -> list:
    """지금 모드의 칸을 붙인다. at 이 없으면(Ctrl+V) 지금 쪽 같은 자리(같은 문서 같은 쪽이면 10pt 비켜서),
    있으면 그 점에 묶음의 왼쪽 위. 붙인 것을 고른 상태로 둔다. 새 Id·xref 들."""
    v, d = t.viewer, t.doc
    mode = CB.mode_of(v)
    if mode == "fields":
        from . import form_tools as FT
        FT.paste(win, t); return []
    page = at[0] if at else v.current
    if page < 0:
        return []
    if mode == "objects":
        own = bool(CB.OBJECTS) and CB.ours_on_system()
        specs = list(CB.OBJECTS) if own else _system_specs(v, page)
        if not specs and CB.OBJECTS:
            specs, own = list(CB.OBJECTS), True
        if not specs:
            win.say(NOTHING); return []
        dx, dy = _offset(specs, at, own and CB.same_place("objects", id(d), page))
        with d.batch():
            ids = O.paste(d, page, specs, dx, dy)
        t.changed([page])
        select_ids(v, page, ids)
        win.say(f"객체 {sum(1 for x in ids if x is not None)}개를 붙여 넣었습니다")
        return ids
    specs = list(CB.ANNOTS)
    if not specs:
        win.say(NOTHING); return []
    dx, dy = _offset(specs, at, CB.same_place("annots", id(d), page))
    box = _union(fitz.Rect(s["rect"]) + (dx, dy, dx, dy) for s in specs)
    cx, cy = O._clamp(box, O._page_box(d.fitz[page]))
    with d.batch():
        xs = AC.paste_annots(d, page, specs, dx + cx, dy + cy)
    t.changed([page])
    if xs:
        from . import context_menu as CM
        pg = d.fitz[page]   # 쪽을 붙잡아 둔다(주석보다 먼저 풀리면 죽음)
        a = pg.load_annot(xs[-1])
        r = fitz.Rect(a.rect) if a else fitz.Rect()
        CM._select(v, page, ("annot", xs[-1], r))
    win.say(f"주석 {len(xs)}개를 붙여 넣었습니다")
    return xs


def _offset(specs, at, same: bool) -> tuple[float, float]:
    if at is not None:
        box = _union(s["rect"] for s in specs)
        return at[1].x - box.x0, at[1].y - box.y0
    return (10.0, 10.0) if same else (0.0, 0.0)


def select_all(v) -> None:
    """편집 모드에서 지금 쪽의 객체 전부(편집 대상에 맞는 것만)."""
    page = v.current
    want = {"text": ("text",), "image": ("image",)}.get(v.edit_target, ("text", "image"))
    v.select_objects(page, [o for o in O.list_objects(v.doc, page) if o["kind"] in want])


def order(win, t, how: str) -> None:
    v, d = t.viewer, t.doc
    page, items = picked_objects(v)
    if not items:
        win.say("순서를 바꿀 객체 선택 필요"); return
    # 맨 앞으로는 그리는 차례대로, 맨 뒤로는 거꾸로 보내야 고른 것끼리의 차례가 그대로다
    seq = [o for o in O.list_objects(d, page)]
    pos = {(_key(o)): n for n, o in enumerate(seq)}
    items = sorted(items, key=lambda o: pos.get(_key(o), 0), reverse=how in ("back", "forward"))
    ids = []
    try:
        with d.batch():
            for it in items:
                ids.append(O.order(d, page, _ref(it), how))
    except ValueError as e:
        if not ids:   # 하나도 못 바꿨으면 빈 실행 취소 자리를 거둔다
            d.undo(); d._redo.clear()
        win.say(str(e) or O.NO_ORDER)
        t.changed([page])
        return
    t.changed([page])
    select_ids(v, page, ids)
    win.say(dict(ORDER_ITEMS)[how] + " 했습니다")


def _key(o: dict):
    return ("o", o["id"]) if o.get("ours") else ("r", o["kind"], tuple(round(x, 1) for x in o["rect"]))


def align(win, t, how: str) -> None:
    """고른 객체들을 보이는 화면(쪽 회전·보기 회전 반영) 기준으로 맞추거나 분배한다. 한 걸음."""
    v, d = t.viewer, t.doc
    page, items = picked_objects(v)
    if len(items) < need(how):
        win.say("같은 간격으로 벌리려면 셋 이상 선택 필요" if how.startswith("dist") else "맞출 객체 둘 이상 선택 필요")
        return
    p = d.fitz[page]
    rot, bounds = p.rotation_matrix, fitz.Rect(p.rect)
    if getattr(v, "view_rot", 0):   # 보기 회전까지 거친 화면에서 왼쪽·위
        rot, bounds = rot * v.view_matrix(page), fitz.Rect(v.page_rect_pt(page))
    back = ~rot
    olds = [fitz.Rect(it["rect"]) for it in items]
    vis = [(r * rot).normalize() for r in olds]
    news = O.arrange(vis, how, bounds)
    moves = []
    for it, old, nv in zip(items, olds, news):
        nb = (nv * back).normalize()
        dx, dy = nb.x0 - old.x0, nb.y0 - old.y0
        moves.append((it, dx, dy))
    ids = []
    with d.batch():
        for it, dx, dy in moves:
            if abs(dx) < 0.01 and abs(dy) < 0.01:
                ids.append(it["id"] if it.get("ours") else it)
                continue
            got = O.move(d, page, [_ref(it)], dx, dy)
            ids.append(got[0] if got else None)
    t.changed([page])
    select_ids(v, page, ids)
    win.say(dict(x for x in ALIGN_ITEMS + DIST_ITEMS if x)[how] + " 했습니다")


def apply_pages(win, t) -> None:
    v, d = t.viewer, t.doc
    s = v.sel
    if not (s and s.kind == "annot"):
        win.say("여러 쪽에 적용할 주석 선택 필요"); return
    if not AC.can_apply(s.extra.get("kind", "")):
        win.say("이 주석은 여러 쪽에 적용할 수 없습니다"); return
    from .dlg_apply_pages import ApplyPagesDialog
    dlg = ApplyPagesDialog(win, d.page_count, s.page, s.extra.get("kind_ko", "주석"))
    if not dlg.exec():
        return
    apply_to(win, t, dlg.pages())


def apply_to(win, t, pages: list[int]) -> tuple[int, int]:
    v, d = t.viewer, t.doc
    s = v.sel
    try:
        n, m = AC.apply_to_pages(d, s.page, s.xref, pages)
    except ValueError as e:
        win.say(str(e)); return 0, 0
    t.changed(None)
    win.say(f"{n}쪽에 적용, {m}쪽 건너뜀")
    return n, m
