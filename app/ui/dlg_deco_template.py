"""꾸미기 템플릿 창: 「{0} 추가」(템플릿에서 추가) · 「{0} 편집」(업데이트할 것이 둘 이상).

추가: 「템플릿」(기본 제공 둘)과 「사용자 지정」(저장한 것) 카드를 미리보기로 보이고, 여러 장을 골라 한 번에 넣는다.
사용자 지정 칸에는 「+」(새로 만들기)와 카드마다 고치기·지우기가 있다. 넣기 전 템플릿마다 담긴 쪽 범위를
이 문서의 쪽 수로 검사한다. 편집: 기본 제공 칸을 숨기고 문서 안의 것들을 카드로 보이며 카드마다 고치기·빼기.
넣기·바꾸기는 dlg_deco.run_decorate 가 한다(chosen() · groups 를 읽음).
"""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

from ..engine import deco as E
from ..engine import deco_store as S
from ..engine.document import Document
from .dialogs import _buttons
from .dlg_deco import PREVIEW_PX, DecoPreview, _warn, describe, dialog_class, related_kinds
from .dlg_pagebox import BAD_RANGE

THUMB = 128   # 카드 그림의 긴 변
COLS = 3


def _pixmap(pix: fitz.Pixmap) -> QPixmap:
    img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                 QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(img)


def render_many(doc: fitz.Document, pno: int, items: list[tuple[str, dict, list[int]]], size_px: int,
                hide=()) -> fitz.Pixmap:
    """쪽 pno 에 여러 꾸미기((종류, 설정, 쪽들))를 차례로 얹은 그림. 번호는 각자의 쪽들 안 차례로 센다."""
    tmp = fitz.open()
    try:
        tmp.insert_pdf(doc, from_page=pno, to_page=pno)
        for k in hide:
            E.remove_decorations(tmp, k)
        for kind, s, pages in items:
            order = sorted(set(pages))
            if pno not in order:
                continue
            s = {k: v for k, v in E.upgrade_settings(kind, s).items() if k != "pages"}
            try:
                E._stamp(tmp, kind, s, [0], order=[order.index(pno)], count=len(order))
            except Exception:  # noqa: BLE001  파일이 사라진 템플릿 등은 미리보기에서 넘긴다
                pass
        page = tmp[0]
        z = size_px / max(page.rect.width, page.rect.height, 1)
        return page.get_pixmap(matrix=fitz.Matrix(z, z), annots=True)
    finally:
        tmp.close()


def ask(parent, title: str, text: str) -> bool:
    """「삭제」 · 「취소」 확인."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    yes = box.addButton("삭제", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


class Card(QFrame):
    """미리보기 카드: 그림 단추(고르기) · 이름 · 아래 단추들."""

    def __init__(self, title: str, pix: QPixmap | None, checkable: bool, actions=(), parent=None):
        super().__init__(parent)
        self.setObjectName("decocard")
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(4)
        self.pick = QToolButton(); self.pick.setObjectName("decocardpick")
        self.pick.setFixedSize(THUMB + 12, THUMB + 12); self.pick.setCheckable(checkable)
        self.pick.setStyleSheet("QToolButton#decocardpick{background:#E4E6EA; border:2px solid transparent; border-radius:4px;}"
                                "QToolButton#decocardpick:checked{border-color:#2F6FEB;}")
        if pix is not None:
            self.pick.setIcon(pix); self.pick.setIconSize(QSize(THUMB, THUMB))
        else:
            self.pick.setText("+"); f = self.pick.font(); f.setPointSize(28); self.pick.setFont(f)
        lay.addWidget(self.pick, 0, Qt.AlignmentFlag.AlignHCenter)
        name = QLabel(title); name.setAlignment(Qt.AlignmentFlag.AlignCenter); name.setWordWrap(True)
        name.setFixedWidth(THUMB + 12)
        lay.addWidget(name)
        self.name = name
        if actions:
            row = QHBoxLayout(); row.setSpacing(4); row.addStretch(1)
            for label, fn in actions:
                b = QToolButton(); b.setText(label); b.clicked.connect(fn); row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
        lay.addStretch(1)


class TemplateDialog(QDialog):
    def __init__(self, parent, doc: Document, kind: str, current: int = 0, unit: str = "cm",
                 groups: list[dict] | None = None):
        super().__init__(parent)
        self.doc, self.kind, self.current, self.unit = doc, kind, current, unit
        self.name = E.DECO_KINDS[kind]
        self.editing = groups is not None
        self.groups = [dict(g) for g in groups] if groups is not None else []
        self.hide_kinds = related_kinds(kind) if self.editing else ()
        self.picked: list[str] = []   # 고른 템플릿 id(고른 차례)
        self.setWindowTitle(f"{self.name} 편집" if self.editing else f"{self.name} 추가")
        outer = QVBoxLayout(self); outer.setContentsMargins(16, 14, 16, 12); outer.setSpacing(10)
        body = QHBoxLayout(); body.setSpacing(16); outer.addLayout(body)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMinimumWidth(COLS * (THUMB + 26) + 24); self.scroll.setMinimumHeight(PREVIEW_PX + 40)
        body.addWidget(self.scroll, 1)
        self.preview = DecoPreview(doc.page_count); body.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        self.bb = _buttons(self, "적용"); self.ok = self.bb.button(QDialogButtonBox.StandardButton.Ok)
        outer.addWidget(self.bb)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        self.preview.pageChanged.connect(lambda _: self.refresh())
        self.preview.set_pages(list(range(doc.page_count)), current)
        self.rebuild()

    # ── 카드 ───────────────────────────────────────────────────────
    def _thumb(self, items, pages: list[int]) -> QPixmap:
        pno = self.current if self.current in pages or not pages else pages[0]
        with self.doc.lock:
            return _pixmap(render_many(self.doc.fitz, pno, items, THUMB, self.hide_kinds))

    def _tpl_pages(self, t: dict) -> list[int]:
        try:
            return S.range_pages(t["range"], self.doc.page_count)
        except ValueError:
            return [self.current]

    def _section(self, lay: QVBoxLayout, title: str, cards: list[QWidget]):
        head = QLabel(title); f = head.font(); f.setBold(True); head.setFont(f)
        lay.addWidget(head)
        g = QGridLayout(); g.setSpacing(8)
        for k, c in enumerate(cards):
            g.addWidget(c, k // COLS, k % COLS, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        g.setColumnStretch(COLS, 1)
        lay.addLayout(g)

    def _tpl_card(self, t: dict, actions=()) -> Card:
        pages = self._tpl_pages(t)
        c = Card(S.display_name(t), self._thumb([(self.kind, t["settings"], pages)], pages), True, actions)
        c.pick.setChecked(t["id"] in self.picked)
        c.pick.toggled.connect(lambda on, tid=t["id"]: self._toggle(tid, on))
        return c

    def rebuild(self):
        """카드를 다시 늘어놓는다(저장·고치기·지우기 뒤)."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
        if self.editing:
            cards = []
            for k, g in enumerate(self.groups):
                c = Card(describe(g), self._thumb([(g["kind"], g["settings"], g["pages"])], g["pages"]), False,
                         (("고치기", lambda _=False, i=k: self.edit_group(i)), ("빼기", lambda _=False, i=k: self.drop_group(i))))
                c.pick.clicked.connect(lambda _=False, i=k: self.edit_group(i))
                cards.append(c)
            self._section(lay, "사용자 지정", cards)
        else:
            self.builtin = S.builtins(self.kind)
            self.custom = S.templates(self.kind)
            known = {t["id"] for t in self.builtin + self.custom}
            self.picked = [i for i in self.picked if i in known]
            self._section(lay, "템플릿", [self._tpl_card(t) for t in self.builtin])
            plus = Card("새로 만들기", None, False)
            plus.pick.clicked.connect(self.new_template)
            cards = [plus] + [self._tpl_card(t, (("고치기", lambda _=False, x=t: self.edit_template(x)),
                                                 ("지우기", lambda _=False, x=t: self.delete_template(x))))
                              for t in self.custom]
            self._section(lay, "사용자 지정", cards)
        lay.addStretch(1)
        self.scroll.setWidget(w)
        self.changed()

    def _toggle(self, tid: str, on: bool):
        if on and tid not in self.picked:
            self.picked.append(tid)
        elif not on and tid in self.picked:
            self.picked.remove(tid)
        self.changed()

    # ── 사용자 지정 템플릿 ─────────────────────────────────────────
    def _open(self, kind: str, update: bool = False):
        return dialog_class(kind)(self, self.doc, self.current, update=update, unit=self.unit,
                                  hide=self.hide_kinds)

    def new_template(self):
        dlg = self._open(self.kind)
        dlg.as_template()
        if dlg.exec():
            if dlg.saved and dlg.saved["id"] not in self.picked:
                self.picked.append(dlg.saved["id"])
            self.rebuild()

    def edit_template(self, t: dict):
        dlg = self._open(self.kind)
        dlg.load(t["settings"], self._tpl_pages(t))
        dlg.as_template(t)
        if dlg.exec():
            self.rebuild()

    def delete_template(self, t: dict):
        label = (t.get("name") or "").strip()
        if ask(self, f"{self.name} 삭제", f'"{label}"을(를) 삭제하시겠습니까?' if label else "템플릿을 삭제하시겠습니까?"):
            S.remove(self.kind, t["id"])
            self.rebuild()

    # ── 편집 모드: 문서 안의 것 ────────────────────────────────────
    def edit_group(self, i: int):
        g = self.groups[i]
        s = g["settings"]
        if g["kind"] == "page_number" and self.kind == "header_footer":
            from ..engine.deco_text import page_number_as_header
            s = page_number_as_header(s)
        dlg = self._open(self.kind, update=True)
        dlg.no_template()
        dlg.load(E.upgrade_settings(self.kind, s, os.path.splitext(self.doc.name)[0]), g["pages"])
        if dlg.exec():
            vals = dlg.values()
            pages = vals.pop("pages")
            self.groups[i] = {"kind": self.kind, "settings": vals, "pages": pages}
            self.rebuild()

    def drop_group(self, i: int):
        if ask(self, f"{self.name} 삭제", f"{self.name}을(를) 삭제하시겠습니까?"):
            del self.groups[i]
            self.rebuild()

    # ── 미리보기 · 적용 ────────────────────────────────────────────
    def _items(self) -> list[tuple[str, dict, list[int]]]:
        if self.editing:
            return [(g["kind"], g["settings"], g["pages"]) for g in self.groups]
        out = []
        by_id = {t["id"]: t for t in self.builtin + self.custom}
        for tid in self.picked:
            t = by_id[tid]
            try:
                out.append((self.kind, t["settings"], S.range_pages(t["range"], self.doc.page_count)))
            except ValueError:
                pass
        return out

    def changed(self):
        self.ok.setEnabled(self.editing or bool(self.picked))
        self._timer.start()

    def refresh(self):
        items = self._items()
        pages = sorted({p for _k, _s, ps in items for p in ps})
        self.preview.set_pages(pages or list(range(self.doc.page_count)))
        with self.doc.lock:
            pix = render_many(self.doc.fitz, self.preview.page(), items, PREVIEW_PX, self.hide_kinds)
        self.preview.set_image(pix)

    def chosen(self) -> list[tuple[dict, list[int]]]:
        """고른 템플릿마다 (설정, 넣을 쪽). 범위가 이 문서의 쪽 수를 넘으면 ValueError."""
        by_id = {t["id"]: t for t in self.builtin + self.custom}
        return [(by_id[tid]["settings"], S.range_pages(by_id[tid]["range"], self.doc.page_count)) for tid in self.picked]

    def accept(self):
        if not self.editing:
            if not self.picked:
                return
            try:
                self.chosen()
            except ValueError:
                _warn(self, BAD_RANGE); return
        super().accept()
