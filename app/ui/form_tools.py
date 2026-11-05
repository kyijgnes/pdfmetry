"""양식 편집 도우미: 여러 칸 고르기 명령 · 앱 안 칸 클립보드 · 여러 필드 복사본 창 · 탭 순서 모드 · 양식 인식.

창(window.on_action)은 `KEYS` 에 든 명령을 `run(win, tab, key)` 로 넘긴다.
여러 칸 선택은 보기의 `sel_fields: list[(쪽, xref)]`(고른 차례). 다른 덩어리는 이 목록을 읽기만 한다.
"""
from __future__ import annotations

import fitz
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QGroupBox, QMessageBox, QPushButton, QSpinBox

from ..engine import annots as A
from . import clipboard as CB

# 앱 안 칸 클립보드(탭 사이에서도 같은 것). A.field_spec 목록. 공용 클립보드의 양식 칸 칸을 그대로 가리킨다
CLIP: list[dict] = CB.FIELDS

ALIGNS = ("left", "right", "top", "bottom", "hcenter", "vcenter", "page_h", "page_v", "page_both")
KEYS = {"field_cut", "field_copy", "field_paste", "field_select_all", "field_set_default", "copies", "tab_order",
        "recognize", "form_keep"}

# 클릭 한 번으로 놓는 칸 기본 크기(pt)
DEFAULT_SIZE = {"ftext": (150, 30), "fcombo": (150, 30), "fbutton": (150, 35), "fcheck": (25, 25), "fradio": (25, 25),
                "flist": (150, 100), "fimage": (150, 100), "fsign": (150, 60), "fdate": (150, 32)}

NO_FIELDS = "새 양식 필드가 감지되지 않았습니다."
TAB_DONE = "모든 칸의 순서를 정했습니다. 끝낼까요?"


def default_rect(tool: str, pt: fitz.Point) -> fitz.Rect:
    w, h = DEFAULT_SIZE.get(tool, (150, 30))
    return fitz.Rect(pt.x, pt.y, pt.x + w, pt.y + h)


def keep_on(prefs: dict) -> bool:
    return bool(prefs.get("form_keep_tool", False))


def _info(win, title: str, text: str):
    m = QMessageBox(QMessageBox.Icon.Information, title, text, QMessageBox.StandardButton.Ok, win)
    m.setDefaultButton(QMessageBox.StandardButton.Ok)
    m.exec()


def _ask(win, title: str, text: str) -> bool:
    m = QMessageBox(QMessageBox.Icon.Question, title, text,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, win)
    m.setDefaultButton(QMessageBox.StandardButton.Yes)
    return m.exec() == QMessageBox.StandardButton.Yes


def picked(v) -> list[tuple[int, int]]:
    """고른 칸들. 여러 칸 목록이 비었으면 지금 고른 칸 하나."""
    if v.sel_fields:
        return list(v.sel_fields)
    s = v.sel
    return [(s.page, s.xref)] if s and s.kind == "widget" else []


# ── 명령 ────────────────────────────────────────────────────────
def run(win, t, key: str) -> None:
    v, d = t.viewer, t.doc
    items = picked(v)
    if key == "form_keep":   # 리본 체크(토글)가 부른다
        return
    if key == "recognize":
        recognize(win, t); return
    if key == "field_select_all":
        v.select_all_fields(v.sel.page if v.sel and v.sel.kind == "widget" else v.current); return
    if key == "field_paste":
        paste(win, t); return
    if key == "tab_order":
        TabOrderMode.get(t).start(items[0][0] if items else v.current); return
    if not items:
        win.say("양식 칸을 고르세요"); return
    if key in ("field_copy", "field_cut"):
        CB.put("fields", [s for pg, x in items if (s := A.field_spec(d, pg, x))], id(d), items[0][0])
        if key == "field_cut":
            v.delete_selection()
        win.say(f"칸 {len(CLIP)}개를 {'잘라 냈' if key == 'field_cut' else '복사했'}습니다")
    elif key == "field_set_default":
        from ..engine import tools as T
        d.snapshot(); n = T.set_form_defaults(d.fitz, items); t.changed(None)
        win.say(f"{n}칸의 지금 값을 기본값으로 정했습니다")
    elif key == "copies":
        dlg = CopiesDialog(win, t, items)
        if dlg.exec():
            xs = dlg.made
            t.changed(None)
            win.say(f"칸 {len(xs)}개를 더 만들었습니다")


def paste(win, t) -> None:
    v, d = t.viewer, t.doc
    if not CLIP:
        win.say("붙여 넣을 칸이 없습니다"); return
    pg = v.current
    # 같은 문서 같은 쪽이면 원본 위에 겹치지 않게 조금 비켜 놓는다
    off = 10 if all(s["doc"] == id(d) and s["page"] == pg for s in CLIP) else 0
    xs = A.paste_fields(d, pg, CLIP, off, off)
    t.changed([pg])
    v.select_fields([(pg, x) for x in xs])
    win.say(f"칸 {len(xs)}개를 붙여 넣었습니다")


def align(win, t, how: str) -> bool:
    """여러 칸이 골라져 있으면 창 없이 바로 맞춘다. 했으면 True."""
    v, d = t.viewer, t.doc
    items = v.sel_fields
    if len(items) < 2:
        return False
    from ..engine import form_props as FP
    sel, items = items, FP.unlocked(d, items)   # 잠긴 칸은 그 자리 그대로
    if len(items) < len(sel):
        win.say("잠긴 칸은 옮기지 않았습니다")
    if not items:
        return True
    from ..engine import tools as T
    if how in ("dist_h", "dist_v") and len(items) < 3:
        win.say("같은 간격으로 벌리려면 칸을 셋 이상 고르세요"); return True
    d.snapshot()
    T.align_widgets(d.fitz, items, how)
    t.changed(sorted({pg for pg, _ in items}))
    v.select_fields(sel)
    return True


def recognize(win, t) -> None:
    from . import dialogs as D
    d = t.doc
    if d.page_count > 20:
        n = D.ProgressDialog(win, "양식 인식", A.recognize_fields, d).run()
        if n is None:
            return
    else:
        n = A.recognize_fields(d)
    if not n:
        _info(win, "양식 인식", NO_FIELDS); return
    t.changed(None)
    if not t.viewer.form_mode:
        win.on_toggle("form_edit", True)
    win.say(f"양식 칸 {n}개를 만들었습니다")


# ── 여러 필드 복사본 ─────────────────────────────────────────────
_CopiesCls = None


def CopiesDialog(parent, tab, items):
    """창을 열 때 만든다(dialogs 를 늦게 읽으려고)."""
    global _CopiesCls
    if _CopiesCls is None:
        _CopiesCls = _copies_dialog_class()
    return _CopiesCls(parent, tab, items)


def _copies_dialog_class():
    from .dialogs import FormDialog

    class _Copies(FormDialog):
        """아래로·옆으로 몇 번, 칸 사이 거리, 「전체 위치」 옮기기. [확인] 으로 만들고 되돌리기는 하나로 묶는다."""
        STEP = 1.0

        def __init__(self, parent, tab, items):
            super().__init__("여러 필드 복사본 만들기", parent, "확인")
            self.tab, self.doc, self.items, self.made = tab, tab.doc, items, []
            rects = [fitz.Rect(w.rect) for pg, x in items for w in self.doc.fitz[pg].widgets() if w.xref == x]
            u = fitz.Rect(rects[0])
            for r in rects[1:]:
                u |= r
            self.down = self.add("선택한 필드를 아래로 복사(번)", QSpinBox()); self.down.setRange(1, 40); self.down.setValue(2)
            self.across = self.add("선택한 필드를 수평 복사(번)", QSpinBox()); self.across.setRange(1, 40); self.across.setValue(1)
            self.dx = self.add("수평 간격(pt)", QDoubleSpinBox()); self.dx.setRange(0, 2000); self.dx.setValue(round(u.width, 1))
            self.dy = self.add("수직 간격(pt)", QDoubleSpinBox()); self.dy.setRange(0, 2000); self.dy.setValue(round(u.height, 1))
            box = QGroupBox("전체 위치(모든 필드)")
            g = QGridLayout(box)
            for (lbl, sx, sy, r_, c_) in (("위로", 0, -1, 0, 1), ("왼쪽", -1, 0, 1, 0), ("오른쪽", 1, 0, 1, 2), ("아래로", 0, 1, 2, 1)):
                b = QPushButton(lbl); b.setAutoDefault(False)
                b.clicked.connect(lambda _=False, a=sx, c=sy: self.nudge(a, c))
                g.addWidget(b, r_, c_)
            self.form.addRow(box)
            self.moved = False

        def nudge(self, sx: int, sy: int):
            """고른 칸 전체를 1pt 옮긴다."""
            if not self.moved:
                self.doc.snapshot()
                self.moved = True
            d = (sx * self.STEP, sy * self.STEP, sx * self.STEP, sy * self.STEP)
            from ..engine import form_props as FP
            with self.doc.lock:
                for pg, x in FP.unlocked(self.doc, self.items):
                    for w in self.doc.fitz[pg].widgets():
                        if w.xref == x:
                            w.rect = fitz.Rect(w.rect) + d
                            A.save_widget(w)
            self.tab.changed(sorted({pg for pg, _ in self.items}))

        def values(self) -> dict:
            return {"down": self.down.value(), "across": self.across.value(), "dx": self.dx.value(), "dy": self.dy.value()}

        def accept(self):
            v = self.values()
            if v["down"] * v["across"] > 1:
                self.made = A.duplicate_fields(self.doc, self.items, v["down"], v["across"], v["dx"], v["dy"])
                if self.moved:
                    self.doc._undo.pop()   # 옮기기와 복사본을 되돌리기 한 번으로
            super().accept()

        def reject(self):
            if self.moved:
                self.doc.undo()
                self.tab.changed(None)
            super().reject()

    return _Copies


# ── 탭 순서 모드 ─────────────────────────────────────────────────
class TabOrderMode:
    """한 쪽의 칸을 차례로 누르면 번호가 붙고, 다 누르면 끝낼지 묻는다. Esc 는 그만두기.
    보기(PageView.hooks)에 얹혀 누르기·키·그리기를 먼저 받는다."""

    @classmethod
    def get(cls, tab) -> "TabOrderMode":
        m = getattr(tab, "_tab_order", None)
        if m is None:
            m = tab._tab_order = cls(tab)
        return m

    def __init__(self, tab):
        self.tab = tab
        self.view = tab.viewer
        self.active = False
        self.page = -1
        self.order: list[int] = []
        self.view.hooks.append(self)

    def start(self, page: int):
        xs = A.tab_order(self.tab.doc, page)
        if not xs:
            self.tab.win.say("이 쪽에는 양식 칸이 없습니다"); return
        self.page, self.order, self.active = page, [], True
        self.view.clear_selection()
        self.tab.win.say("칸을 차례로 누르세요 · Esc 로 그만두기")
        self.view.viewport().update()

    def stop(self):
        self.active = False
        self.order = []
        self.view.viewport().update()

    def finish(self):
        xs = list(self.order)
        rest = [x for x in A.tab_order(self.tab.doc, self.page) if x not in xs]
        A.set_tab_order(self.tab.doc, self.page, xs + rest)
        pg = self.page
        self.stop()
        self.tab.changed([pg])
        self.tab.win.say("탭 순서를 정했습니다")

    def tool_changed(self, tool: str):
        if self.active and tool != "form":
            self.stop()

    def press(self, e: QMouseEvent, i: int, sp: QPointF) -> bool:
        if not self.active:
            return False
        if e.button() != Qt.MouseButton.LeftButton or i != self.page:
            return True
        pt = self.view.to_page(i, sp)
        for w in self.tab.doc.fitz[i].widgets():
            if fitz.Rect(w.rect).contains(pt) and w.xref not in self.order:
                self.order.append(w.xref)
                self.view.viewport().update()
                if len(self.order) == len(A.tab_order(self.tab.doc, i)):
                    if _ask(self.tab.win, "탭 순서 설정", TAB_DONE):
                        self.finish()
                break
        return True

    def key(self, e: QKeyEvent) -> bool:
        if self.active and e.key() == Qt.Key.Key_Escape:
            self.stop(); self.tab.win.say(""); return True
        if self.active and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.order:
            self.finish(); return True
        return False

    def paint(self, p: QPainter, i: int):
        if not self.active or i != self.page:
            return
        f = QFont(p.font()); f.setBold(True); f.setPointSizeF(9); p.setFont(f)
        num = {x: k + 1 for k, x in enumerate(self.order)}
        for w in self.tab.doc.fitz[i].widgets():
            r = self.view.from_page_rect(i, fitz.Rect(w.rect))
            n = num.get(w.xref)
            box = QRectF(r.left(), r.top(), 20, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(240, 126, 35) if n else QColor(150, 150, 150, 160))
            p.drawRect(box)
            p.setPen(QPen(Qt.GlobalColor.white))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(n) if n else "")
