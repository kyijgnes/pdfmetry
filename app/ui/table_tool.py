"""표 인식 모드: 문서 전체에서 표를 찾아 테두리를 그리고, 고른 표 위 [표 복사] 로 클립보드에 넣는다.

- 켜면 엔진 `ocr.find_tables` 를 진행 창으로 돌리고 개수를 알린다. 다시 누르거나 Esc 면 끈다.
- 표를 누르면 고르고, 그 위에 [표 복사] · [▾] 단추가 뜬다. [표 복사] 는 HTML 표 + 탭 구분 글자(워드·엑셀 붙여넣기),
  [▾] 메뉴 「Excel로 저장」 은 .xlsx 로 저장한다.
보기(PageView.hooks)에 얹혀 그리기·누르기·키를 먼저 받는다.
"""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import QMimeData, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from . import theme

TITLE = "표 인식"
EDGE = QColor(113, 128, 230)
PICKED = QColor(240, 126, 35)
BTN_W, MENU_W, BTN_H = 72, 22, 22


def found_text(n: int) -> str:
    if n:
        return f"인식 완료. 현재 문서에 {n}개의 표가 있습니다. Word 또는 Excel에 복사하여 붙여넣을 수 있습니다."
    return "인식 완료. 현재 파일에 표가 없습니다."


COPY_FAIL = "죄송합니다! 표 복사 실패. 선택한 표의 구조가 지원되지 않거나 인식되지 않습니다."


def copy_rows(rows: list[list[str]]) -> bool:
    """클립보드에 HTML 표 + 탭 구분 글자. 칸이 하나도 없으면 False."""
    from ..engine import ocr as O
    if not rows or not any(any(c for c in r) for r in rows):
        return False
    md = QMimeData()
    md.setHtml(O.table_html(rows))
    md.setText(O.table_tsv(rows))
    QApplication.clipboard().setMimeData(md)
    return True


def save_xlsx(rows: list[list[str]], path: str) -> None:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


class TableTool(QObject):
    """문서 탭 하나의 표 인식 결과."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.view = tab.viewer
        self.tables: list[dict] = []
        self.picked = -1
        self.active = False
        self.view.hooks.append(self)

    # ── 켜고 끄기 ─────────────────────────────────────────
    def start(self) -> bool:
        """표를 찾아 모드를 켠다. 그만두거나 실패하면 False."""
        from . import dialogs as D
        from . import dlg_ocr as DO
        from ..engine import ocr as O
        win = self.tab.win
        work = self.tab.doc.copy()   # 그리는 일꾼과 같은 문서를 만지지 않게 사본에서 찾는다
        try:
            res = D.ProgressDialog(win, TITLE, O.find_tables, work, lang=DO.current_langs(win.prefs)).run()
        finally:
            work.close()
        if res is None:
            return False
        self.tables = res
        self.picked = 0 if res else -1
        self.active = True
        self.view.viewport().update()
        QMessageBox.information(win, TITLE, found_text(len(res)))
        if res:
            self.view.goto(res[0]["page"])
        return True

    def stop(self):
        self.active = False
        self.tables = []
        self.picked = -1
        self.view.viewport().update()

    def tool_changed(self, tool: str):
        pass   # 표 인식은 도구와 따로 켜 두고, 다시 누르거나 Esc 로 끈다

    # ── 고른 표 ───────────────────────────────────────────
    def copy_picked(self) -> bool:
        if not 0 <= self.picked < len(self.tables):
            return False
        ok = copy_rows(self.tables[self.picked]["rows"])
        win = self.tab.win
        if ok:
            win.say("클립보드에 복사됨")
        else:
            QMessageBox.warning(win, TITLE, COPY_FAIL)
        return ok

    def save_picked(self, path: str | None = None) -> str | None:
        if not 0 <= self.picked < len(self.tables):
            return None
        win = self.tab.win
        tb = self.tables[self.picked]
        if path is None:
            hint = win._suggest(self.tab, f"_{tb['page'] + 1}쪽_표.xlsx")
            path, _ = QFileDialog.getSaveFileName(win, "Excel로 저장", hint, "Excel (*.xlsx)")
            if not path:
                return None
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
        try:
            save_xlsx(tb["rows"], path)
        except OSError as e:
            QMessageBox.warning(win, TITLE, f"저장하지 못했습니다.\n{e}"); return None
        if hasattr(win, "_offer_open"):
            win._offer_open(path)
        return path

    # ── 보기 도우미 ───────────────────────────────────────
    def _scene_rect(self, k: int) -> QRectF:
        t = self.tables[k]
        return self.view.from_page_rect(t["page"], fitz.Rect(t["bbox"]))

    def _buttons(self) -> tuple[QRectF, QRectF] | None:
        if not (self.active and 0 <= self.picked < len(self.tables)):
            return None
        r = self._scene_rect(self.picked)
        y = r.top() - BTN_H - 4
        if y < self.view.items[self.tables[self.picked]["page"]].pos().y():
            y = r.top() + 4   # 쪽 위 끝에 붙은 표는 안쪽에
        x = r.right() - BTN_W - MENU_W
        return QRectF(x, y, BTN_W, BTN_H), QRectF(x + BTN_W, y, MENU_W, BTN_H)

    def press(self, e: QMouseEvent, i: int, sp: QPointF) -> bool:
        if not self.active or e.button() != Qt.MouseButton.LeftButton:
            return False
        b = self._buttons()
        if b is not None:
            if b[0].contains(sp):
                self.copy_picked(); return True
            if b[1].contains(sp):
                m = QMenu(self.view)
                m.addAction("표 복사", self.copy_picked)
                m.addAction("Excel로 저장", self.save_picked)
                m.exec(e.globalPosition().toPoint())
                return True
        for k in range(len(self.tables)):
            if self.tables[k]["page"] == i and self._scene_rect(k).contains(sp):
                self.picked = k
                self.view.viewport().update()
                return True
        return False

    def key(self, e: QKeyEvent) -> bool:
        if self.active and e.key() == Qt.Key.Key_Escape:
            self.stop()
            self.tab.win.say("")
            return True
        return False

    def paint(self, p: QPainter, i: int):
        if not self.active:
            return
        for k, t in enumerate(self.tables):
            if t["page"] != i:
                continue
            c = PICKED if k == self.picked else EDGE
            fill = QColor(c); fill.setAlpha(18)
            p.setPen(QPen(c, 2)); p.setBrush(fill)
            p.drawRect(self._scene_rect(k))
        b = self._buttons()
        if b is not None and self.tables[self.picked]["page"] == i:
            f = QFont(p.font()); f.setPointSizeF(9); p.setFont(f)
            for rect, text in ((b[0], "표 복사"), (b[1], "▾")):
                p.setPen(QPen(QColor(theme.ACCENT), 1)); p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(QColor("white"))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def toggle(win, tab) -> None:
    """리본 「표 인식」: 꺼져 있으면 찾아 켜고, 켜져 있으면 끈다."""
    tool = tab.table_tool
    if tool is None:
        tool = tab.table_tool = TableTool(tab)
    if tool.active:
        tool.stop(); win.say(""); return
    if tool.start() and tool.tables:
        win.say("표를 누르고 [표 복사] 를 누르세요. 다시 「표 인식」 을 누르거나 Esc 로 끕니다.")
