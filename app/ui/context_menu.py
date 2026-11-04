"""문서 화면 오른쪽 클릭 메뉴 짓기와 Tab · Shift+Tab 초점 이동.

뷰어(PageView)가 `build(view, i, sp)` 로 메뉴를 받아 띄운다. 하는 일은 `requestDialog` 요청으로 창(on_request)에 부탁한다.
"""
from __future__ import annotations

import fitz
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu

from ..engine import annots as A


def _union(rects) -> fitz.Rect:
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= fitz.Rect(x)
    return r


def build(v, i: int, sp: QPointF) -> QMenu:
    """오른쪽 클릭 자리(쪽 i, 장면 좌표 sp)에 맞는 메뉴. 고른 것 → 편집 모드 빈 곳 → 빈 곳 순으로 가른다."""
    m = QMenu(v)
    s = v.sel
    if s and s.kind == "words":
        _words(v, m, s)
    elif s and s.kind == "annot":
        _annot(v, m, s)
    elif s and s.kind == "link":
        _link(v, m, s)
    elif s and s.kind == "widget":
        m.addAction("속성", lambda: v.requestDialog.emit("field_props", s))
        m.addAction("삭제", v.delete_selection)
    elif s and s.kind == "block":
        _block(v, m, s)
    elif s and s.kind == "image":
        _image(v, m, s)
    elif v.edit_mode:
        _edit_blank(v, m, i)
    else:
        _blank(v, m, i, sp)
    return m


def _act(v, key: str):
    v.requestDialog.emit("action", key)


def _words(v, m: QMenu, s):
    m.addAction("복사", v.copy_selection)
    m.addAction("모두 선택", lambda: _select_all(v))
    m.addAction("선택 취소", v.clear_selection)
    m.addSeparator()
    m.addAction("강조", lambda: v._markup_sel("Highlight"))
    m.addAction("밑줄", lambda: v._markup_sel("Underline"))
    m.addAction("취소선", lambda: v._markup_sel("StrikeOut"))
    m.addAction("구불구불한 선", lambda: v._markup_sel("Squiggly"))
    if s.words:
        last = s.words[-1]
        m.addAction("삽입 기호", lambda: v.requestDialog.emit("caret", {"page": s.page, "point": fitz.Point(last[2], last[3])}))
    m.addSeparator()
    if s.words:
        first = s.words[0]
        m.addAction("노트를 텍스트에", lambda: v.requestDialog.emit(
            "note_text", {"page": s.page, "point": fitz.Point(first[0], first[1])}))
        m.addAction("링크 만들기", lambda: v.requestDialog.emit(
            "link_text", {"page": s.page, "rect": _union([w[:4] for w in s.words])}))
    m.addAction("북마크 추가", lambda: v.requestDialog.emit("bookmark_text", {"page": s.page, "text": s.text}))
    m.addAction("이 글자 검색", lambda: v.requestDialog.emit("search_text", " ".join(s.text.split())[:200]))
    if s.words:
        m.addAction("영역 OCR", lambda: v.requestDialog.emit("ocr_words", {"page": s.page, "rect": _union([w[:4] for w in s.words])}))
    m.addSeparator()
    m.addAction("편집", lambda: _act(v, "edit_all_toggle")).setEnabled(not v.edit_mode)
    m.addAction("교정 표시", v._redact_sel)


def _select_all(v):
    v.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier))


def _annot(v, m: QMenu, s):
    reply = A.is_reply(v.doc, s.xref)
    if not reply:
        m.addAction("답글", lambda: v.requestDialog.emit("reply", s))
    if s.text:
        m.addAction("글 복사", lambda: _copy_text(v, s.text))
    m.addSeparator()
    m.addAction("기본값으로", lambda: v.requestDialog.emit("set_default", s)).setEnabled(not reply)
    m.addAction("속성", lambda: v.requestDialog.emit("annot_props", s))
    m.addAction("삭제", v.delete_selection)


def _copy_text(v, text: str):
    QApplication.clipboard().setText(text)
    v.status.emit("글을 복사했습니다")


def _link(v, m: QMenu, s):
    m.addAction("링크 열기", lambda: v.requestDialog.emit("open_link", s))
    info = A.link_info(v.doc, s.page, s.xref)
    m.addAction("주소 복사", lambda: v.requestDialog.emit("copy_link", s)).setEnabled(
        bool(info) and info["kind"] in ("uri", "file"))
    m.addAction("링크 고치기", lambda: v.requestDialog.emit("edit_link", s))
    m.addSeparator()
    m.addAction("삭제", v.delete_selection)


def _block(v, m: QMenu, s):
    m.addAction("글자 고치기", lambda: v.requestDialog.emit("edit_block", s))
    m.addSeparator()
    m.addAction("링크 만들기", lambda: v.requestDialog.emit("link_text", {"page": s.page, "rect": fitz.Rect(s.rect)}))
    m.addAction("북마크 추가", lambda: v.requestDialog.emit("bookmark_text", {"page": s.page, "text": s.text}))
    m.addSeparator()
    m.addAction("삭제", v.delete_selection)


def _image(v, m: QMenu, s):
    m.addAction("이미지 바꾸기", lambda: v.requestDialog.emit("replace_image", s))
    m.addAction("이미지 저장", lambda: v.requestDialog.emit("save_image", s))
    m.addSeparator()
    m.addAction("왼쪽 회전", lambda: v.requestDialog.emit("rotate_obj", -90))
    m.addAction("오른쪽 회전", lambda: v.requestDialog.emit("rotate_obj", 90))
    m.addAction("좌우 뒤집기", lambda: v.requestDialog.emit("flip_obj", "h"))
    m.addAction("상하 뒤집기", lambda: v.requestDialog.emit("flip_obj", "v"))
    m.addSeparator()
    m.addAction("링크 만들기", lambda: v.requestDialog.emit("link_text", {"page": s.page, "rect": fitz.Rect(s.rect)}))
    m.addAction("북마크 추가", lambda: v.requestDialog.emit("bookmark_text", {"page": s.page, "text": ""}))
    m.addSeparator()
    m.addAction("삭제", v.delete_selection)


def _edit_blank(v, m: QMenu, i: int):
    m.addAction("모두 선택", lambda: _select_all(v))
    m.addSeparator()
    m.addAction("텍스트 추가", lambda: v.requestDialog.emit("tool", "addtext"))
    m.addAction("이미지 추가", lambda: v.requestDialog.emit("tool", "addimage"))
    m.addAction("쪽 삽입", lambda: _act(v, "insert_blank"))
    m.addSeparator()
    m.addAction("편집 끝내기", lambda: _act(v, "edit_all_toggle"))


def _blank(v, m: QMenu, i: int, sp: QPointF):
    act = lambda key: _act(v, key)  # noqa: E731
    m.addAction("모두 선택", lambda: _select_all(v))
    m.addAction("편집", lambda: act("edit_all_toggle"))
    if i >= 0:
        m.addAction("이 쪽에 메모", lambda: v.requestDialog.emit("note", {"page": i, "point": v.to_page(i, sp)}))
        m.addAction("북마크 추가", lambda: v.requestDialog.emit("bookmark_text", {"page": i, "text": ""}))
    m.addSeparator()
    m.addAction("확대", lambda: act("zoomin")); m.addAction("축소", lambda: act("zoomout"))
    m.addSeparator()
    m.addAction("첫 페이지", lambda: act("first")).setEnabled(v.current > 0)
    m.addAction("마지막 페이지", lambda: act("last")).setEnabled(v.current < v.doc.page_count - 1)
    m.addAction("이전 보기", v.back).setEnabled(bool(v._back))
    m.addAction("다음 보기", v.forward).setEnabled(bool(v._fwd))
    if i >= 0:
        m.addSeparator()
        m.addAction("왼쪽 회전", lambda: v.requestDialog.emit("rotate", {"pages": [i], "deg": -90}))
        m.addAction("오른쪽 회전", lambda: v.requestDialog.emit("rotate", {"pages": [i], "deg": 90}))
        m.addAction("이 쪽 삭제", lambda: v.requestDialog.emit("delete_pages", {"pages": [i]}))
    m.addSeparator()
    m.addAction("인쇄", lambda: act("print")); m.addAction("문서 속성", lambda: act("properties"))
    if v.window().isFullScreen():
        m.addAction("전체 화면 끝내기", lambda: act("fullscreen"))


# ── Tab · Shift+Tab: 쪽 차례대로 주석 · 링크 · 양식 칸 ─────────────────────
def page_targets(v, i: int) -> list[tuple[str, int, fitz.Rect]]:
    """i 쪽의 초점 대상 (종류, xref, 쪽 좌표 네모), 화면에서 위→아래·왼→오른쪽 차례. 종류: annot · link · widget."""
    f = v.doc.fitz[i]
    rot = f.rotation_matrix
    out: list[tuple[str, int, fitz.Rect]] = []
    if v.show_annots:
        for a in f.annots():
            if a.type[1] in ("Link", "Widget", "Popup") or A.is_reply(v.doc, a.xref):
                continue
            out.append(("annot", a.xref, fitz.Rect(a.rect)))
        for l in f.get_links():
            if l.get("xref"):
                out.append(("link", l["xref"], fitz.Rect(l["from"])))
    for w in f.widgets():
        out.append(("widget", w.xref, fitz.Rect(w.rect)))

    def key(t):
        r = fitz.Rect(t[2]) * rot
        r.normalize()
        return (round(r.y0, 1), round(r.x0, 1))

    out.sort(key=key)
    return out


def focus_step(v, forward: bool) -> bool:
    """고른 것의 다음(앞이면 이전) 대상을 골라 그 자리로 간다. 더 없으면 False(초점은 Qt 가 옮긴다)."""
    n = v.doc.page_count
    s = v.sel
    cur = (s.kind, s.xref) if s and s.kind in ("annot", "link", "widget") else None
    start = s.page if cur else v.current
    step = 1 if forward else -1
    p = start
    while 0 <= p < n:
        items = page_targets(v, p)
        if cur and p == start:
            idx = next((k for k, t in enumerate(items) if (t[0], t[1]) == cur), None)
            if idx is None:
                cand = items if forward else items[::-1]
            else:
                cand = items[idx + 1:] if forward else items[:idx][::-1]
        else:
            cand = items if forward else items[::-1]
        if cand:
            _select(v, p, cand[0])
            return True
        p += step
    return False


def _select(v, i: int, t: tuple[str, int, fitz.Rect]):
    from .viewer import Selection
    kind, xref, rect = t
    if kind == "annot":
        a = A.get(v.doc, i, xref)
        if a is None:
            return
        v.sel = Selection("annot", i, xref, fitz.Rect(a.rect), text=a.info.get("content", ""),
                          extra={"kind": a.type[1], "kind_ko": A.KIND_NAMES.get(a.type[1], a.type[1]),
                                 "colors": A.annot_colors(v.doc, a), "opacity": a.opacity,
                                 "width": (a.border or {}).get("width", 1)})
    elif kind == "link":
        info = A.link_info(v.doc, i, xref)
        if info is None:
            return
        v.sel = Selection("link", i, xref, fitz.Rect(info["rect"]), text=v.link_hover_text(info),
                          extra={"kind": "Link", "kind_ko": "링크", "rect": fitz.Rect(info["rect"])})
    else:
        w = next((w for w in v.doc.fitz[i].widgets() if w.xref == xref), None)
        if w is None:
            return
        v.sel = Selection("widget", i, xref, fitz.Rect(w.rect), text=w.field_name,
                          extra={"type": w.field_type_string, "value": w.field_value})
    _reveal(v, i, v.sel.rect)
    v.selectionChanged.emit(v.sel)
    v.viewport().update()


def _reveal(v, i: int, rect: fitz.Rect):
    """고른 네모가 화면에 들어오게(보기 회전·쪽 회전을 거친 장면 좌표로)."""
    if v.layout_mode in ("single", "facing") and i not in v.visible_pages_for_layout():
        v.goto(i, rect.y0)
    v.ensureVisible(v.from_page_rect(i, rect), 60, 60)
