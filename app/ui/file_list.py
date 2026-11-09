"""공용 파일 목록 · 저장 위치 묶음(압축 · 병합 · 일괄 편집이 같이 쓴다).

FileListPanel: 번호 · 이름 · 크기 · 상태 · 동작 열, [파일 추가] 메뉴 다섯, 끌어서 차례 바꾸기, 모두 지우기.
암호 걸린 PDF 는 넣을 때 암호를 묻고 그 줄에 같이 둔다(passwords()).
OutputPanel: 「원본 파일 위치」/「폴더 지정」 + 경로. 고른 값은 store(설정 dict) 에 {"custom", "folder"} 로 남긴다.
"""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .dialogs import settle_default

EMPTY_TEXT = "PDF 파일을 여기로 끌어다 놓습니다."
CLEAR_TIPS = "모든 파일을 지우시겠습니까?"
COLS = ("번호", "이름", "크기", "상태", "동작")
PATH_ROLE = Qt.ItemDataRole.UserRole
PW_ROLE = Qt.ItemDataRole.UserRole + 1
FOLDER_ERROR = "폴더 경로가 잘못되었습니다. 다시 시도해주세요."
FOLDER_READONLY = "폴더 경로가 읽기 전용입니다. 확인하십시오."
FAIL_COLOR = "#D23C3C"
OK_COLOR = "#2E7D32"


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def main_window(w: QWidget | None):
    """위로 올라가며 열린 탭 목록이 있는 앱 창을 찾는다(없으면 None)."""
    while w is not None:
        if hasattr(w, "tabs") and hasattr(w, "cur"):
            return w
        w = w.parentWidget()
    return None


def ask_pdf_password(parent, path: str) -> tuple[bool, str | None]:
    """암호 걸린 PDF 면 맞는 암호를 물어 (넣을지, 암호). 암호 없는 파일은 (True, None), 그만두면 (False, None)."""
    try:
        d = fitz.open(path)
    except Exception:  # noqa: BLE001
        return True, None   # PDF 가 아닌 파일(그림 등)은 그대로
    try:
        if not d.needs_pass:
            return True, None
        def check(pw: str) -> bool:
            return bool(d.authenticate(pw))
        win = main_window(parent)
        prefs = getattr(win, "prefs", None) if win is not None else None
        if prefs is not None:
            from . import dlg_password as PW
            pw = PW.ask(parent, os.path.basename(path), prefs, check)
            return (pw is not None), pw
        from .dialogs import ask_password
        while True:
            pw = ask_password(parent, os.path.basename(path))
            if pw is None:
                return False, None
            if check(pw):
                return True, pw
            QMessageBox.warning(parent, "암호", "비밀번호가 정확하지 않습니다. 기타 비밀번호로 재시도하십시오.")
    finally:
        d.close()


class _Tree(QTreeWidget):
    """끌어서 차례 바꾸기만(항목 안에 떨구지 않음). 밖에서 끌어 온 파일은 패널이 받는다."""
    filesDropped = Signal(list)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); return
        super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.filesDropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction(); return
        super().dropEvent(e)
        p = self.parentWidget()
        while p is not None and not isinstance(p, FileListPanel):
            p = p.parentWidget()
        if p is not None:
            p._renumber()


class FileListPanel(QWidget):
    """파일 목록. task_name 은 작업 이름(「압축」 · 「PDF 병합」), filters 는 [파일 추가] 열기 창 거르개."""
    changed = Signal()

    def __init__(self, parent, task_name: str, filters: str, paths: list[str] | None = None,
                 passwords: list[str | None] | None = None, only_pdf: bool = True):
        super().__init__(parent)
        self.task_name, self.filters, self.only_pdf = task_name, filters, only_pdf
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.add_btn = QToolButton(); self.add_btn.setText("파일 추가")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.add_btn.clicked.connect(self.add_files)
        m = QMenu(self.add_btn)
        m.addAction("파일 추가", self.add_files)
        self.act_cur = m.addAction("열려있는 파일 추가", self.add_current)
        self.act_all = m.addAction("모든 열린 파일", self.add_all_open)
        self.act_pick = m.addAction("열린 파일 선택", self.pick_open)
        m.addAction("폴더 추가", self.add_folder)
        m.aboutToShow.connect(self._sync_menu)
        self.add_btn.setMenu(m)
        top.addWidget(self.add_btn)
        top.addStretch()
        self.clear_btn = QPushButton("모두 지우기"); self.clear_btn.clicked.connect(self.clear_all)
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.tree = _Tree()
        self.tree.setColumnCount(len(COLS)); self.tree.setHeaderLabels(list(COLS))
        self.tree.setRootIsDecorated(False); self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setAcceptDrops(True)
        h = self.tree.header(); h.setStretchLastSection(False)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3, 4):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.filesDropped.connect(self.add_paths)
        lay.addWidget(self.tree, 1)
        self.empty = QLabel(EMPTY_TEXT, self.tree.viewport()); self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{theme.TEXT_DIM};")
        self.setAcceptDrops(True)
        pws = list(passwords or [])
        for k, p in enumerate(paths or []):
            self._add(p, pws[k] if k < len(pws) else None, ask=False)
        self._renumber()

    # ── 화면 ──
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.empty.setGeometry(self.tree.viewport().rect())

    def showEvent(self, e):
        super().showEvent(e)
        self.empty.setGeometry(self.tree.viewport().rect())

    def _renumber(self):
        for k in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(k).setText(0, str(k + 1))
        self.empty.setVisible(self.tree.topLevelItemCount() == 0)
        self.empty.setGeometry(self.tree.viewport().rect())
        self.clear_btn.setEnabled(self.tree.topLevelItemCount() > 0)
        self.changed.emit()

    def _sync_menu(self):
        docs = self._open_docs()
        win = main_window(self)
        cur = win.cur() if win is not None else None
        self.act_cur.setEnabled(bool(cur and cur.doc.path))
        self.act_all.setEnabled(bool(docs)); self.act_pick.setEnabled(bool(docs))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ── 넣기 ──
    def _has(self, path: str) -> bool:
        n = os.path.normcase(os.path.abspath(path))
        return any(os.path.normcase(os.path.abspath(p)) == n for p in self.paths())

    def _add(self, path: str, password: str | None = None, ask: bool = True) -> bool:
        if not path or not os.path.isfile(path) or self._has(path):
            return False
        if self.only_pdf and not path.lower().endswith(".pdf"):
            return False
        if ask and password is None:
            ok, password = ask_pdf_password(self, path)
            if not ok:
                return False
        it = QTreeWidgetItem(["", os.path.basename(path), size_text(os.path.getsize(path)), "", "제거"])
        it.setData(0, PATH_ROLE, path); it.setData(0, PW_ROLE, password)
        it.setFlags((it.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
        it.setForeground(4, QColor(theme.ACCENT))
        self.tree.addTopLevelItem(it)
        return True

    def add_paths(self, paths: list[str], passwords: list[str | None] | None = None):
        pws = list(passwords or [])
        for k, p in enumerate(paths):
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    self._add(os.path.join(p, f))
            else:
                self._add(p, pws[k] if k < len(pws) else None)
        self._renumber()

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "파일 추가", "", self.filters)
        self.add_paths(paths)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "폴더 추가")
        if d:
            self.add_paths([d])

    def _open_docs(self) -> list:
        """열린 문서 가운데 디스크에 있는 것: [(이름, 경로, 암호)]."""
        win = main_window(self)
        if win is None:
            return []
        from . import fileops as F
        out = []
        for w in F.windows(win):
            for t in getattr(w, "tabs", []):
                if t.doc.path and os.path.isfile(t.doc.path) and (not self.only_pdf or t.doc.path.lower().endswith(".pdf")):
                    out.append((t.doc.name, t.doc.path, t.doc.password))
        return out

    def add_current(self):
        win = main_window(self)
        t = win.cur() if win is not None else None
        if t and t.doc.path:
            self._add(t.doc.path, t.doc.password, ask=False); self._renumber()

    def add_all_open(self):
        for _, p, pw in self._open_docs():
            self._add(p, pw, ask=False)
        self._renumber()

    def pick_open(self):
        docs = self._open_docs()
        dlg = QDialog(self); dlg.setWindowTitle("열린 파일 선택")
        vl = QVBoxLayout(dlg)
        boxes = []
        for name, p, pw in docs:
            cb = QCheckBox(name); cb.setChecked(True); vl.addWidget(cb); boxes.append((cb, p, pw))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("확인"); bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); vl.addWidget(bb)
        settle_default(dlg, bb.button(QDialogButtonBox.StandardButton.Ok))
        if dlg.exec():
            for cb, p, pw in boxes:
                if cb.isChecked():
                    self._add(p, pw, ask=False)
            self._renumber()

    # ── 빼기 ──
    def _clicked(self, it: QTreeWidgetItem, col: int):
        if col == 4 and self.isEnabled():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it)); self._renumber()

    def remove_row(self, row: int):
        self.tree.takeTopLevelItem(row); self._renumber()

    def clear_all(self, confirm: bool = True):
        if not self.tree.topLevelItemCount():
            return
        if confirm and QMessageBox.question(self, self.task_name, CLEAR_TIPS) != QMessageBox.StandardButton.Yes:
            return
        self.tree.clear(); self._renumber()

    # ── 읽기 · 상태 ──
    def paths(self) -> list[str]:
        return [self.tree.topLevelItem(k).data(0, PATH_ROLE) for k in range(self.tree.topLevelItemCount())]

    def passwords(self) -> list[str | None]:
        return [self.tree.topLevelItem(k).data(0, PW_ROLE) for k in range(self.tree.topLevelItemCount())]

    def count(self) -> int:
        return self.tree.topLevelItemCount()

    def set_status(self, row: int, text: str, ok: bool):
        it = self.tree.topLevelItem(row)
        if it is None:
            return
        it.setText(3, text)
        it.setForeground(3, QColor(OK_COLOR if ok else FAIL_COLOR))
        if not ok:
            it.setToolTip(3, text)   # 상태 칸이 좁아 긴 실패 문구가 잘릴 때 원문

    def status(self, row: int) -> str:
        it = self.tree.topLevelItem(row)
        return it.text(3) if it is not None else ""

    def clear_status(self):
        for k in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(k); it.setText(3, ""); it.setToolTip(3, "")


def unique_path(folder: str, stem: str, ext: str = ".pdf", taken: set[str] | None = None) -> str:
    """folder 안에서 겹치지 않는 「stem.ext」, 있으면 「stem_1.ext」 · 「stem_2.ext」. taken 은 이번에 이미 쓰기로 한 경로."""
    taken = {os.path.normcase(p) for p in (taken or ())}

    def free(p: str) -> bool:
        return not os.path.exists(p) and os.path.normcase(p) not in taken
    p = os.path.join(folder, stem + ext)
    n = 1
    while not free(p):
        p = os.path.join(folder, f"{stem}_{n}{ext}"); n += 1
    return p


def open_in_explorer(path: str) -> None:
    """탐색기에서 그 파일을 골라 보인다(폴더면 그 폴더)."""
    import subprocess
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isdir(path):
        os.startfile(path)


def default_folder(sub: str) -> str:
    """지정 폴더 기본: 바탕화면\\PDFmetry\\<sub>."""
    desk = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or os.path.join(os.path.expanduser("~"), "Desktop")
    return os.path.normpath(os.path.join(desk, "PDFmetry", sub))


class OutputPanel(QWidget):
    """저장 위치: 「원본 파일 위치」(기본) · 「폴더 지정」 + 경로 + [찾아보기]. store 는 {"custom": bool, "folder": 글자}."""
    ORIGINAL, CUSTOM = 0, 1

    def __init__(self, parent, store: dict, default_sub: str, origin: str | None = None, vertical: bool = False):
        """origin: 「원본 파일 위치」 일 때 경로 칸에 보일 폴더(파일 하나짜리 창). None 이면 지정 폴더를 흐리게 둔다.
        vertical: 콤보 아래에 경로 줄(좁은 창)."""
        super().__init__(parent)
        self.store = store
        self.default = default_folder(default_sub)
        self.origin = os.path.normpath(origin) if origin else origin
        self._custom_text = os.path.normpath(store.get("folder") or self.default)
        lay = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("저장 위치")
        lay.addWidget(self.label)
        self.combo = QComboBox(); self.combo.addItems(["원본 파일 위치", "폴더 지정"])
        lay.addWidget(self.combo)
        row = QHBoxLayout() if vertical else lay
        self.path = QLineEdit(self._custom_text)
        row.addWidget(self.path, 1)
        self.browse = QPushButton("찾아보기"); self.browse.setAutoDefault(False); self.browse.clicked.connect(self._browse)
        row.addWidget(self.browse)
        if vertical:
            lay.addLayout(row)
        self.combo.setCurrentIndex(self.CUSTOM if store.get("custom") else self.ORIGINAL)
        self.combo.currentIndexChanged.connect(self._sync)
        self.path.textChanged.connect(self._edited)
        self._sync()

    def _edited(self, text: str):
        if self.custom() or self.origin is None:   # origin 이 없으면 경로 칸은 늘 지정 폴더
            self._custom_text = text

    def set_origin(self, origin: str | None):
        self.origin = os.path.normpath(origin) if origin else origin
        self._sync()

    def _sync(self):
        on = self.custom()
        if self.origin is not None:
            self.path.blockSignals(True)
            self.path.setText(self._custom_text if on else self.origin)
            self.path.blockSignals(False)
        self.path.setEnabled(on); self.browse.setEnabled(on)

    def _browse(self):
        start = self.path.text().strip()
        d = QFileDialog.getExistingDirectory(self, "폴더 지정", start if os.path.isdir(start) else "")
        if d:
            self.path.setText(os.path.normpath(d))

    def custom(self) -> bool:
        return self.combo.currentIndex() == self.CUSTOM

    def folder(self) -> str:
        return self.path.text().strip()

    def check(self) -> str | None:
        """[적용] 앞 검사. 지정 폴더가 없으면 만들고, 못 만들거나 쓸 수 없으면 알릴 문구."""
        if not self.custom():
            return None
        f = self.folder()
        from .fileops import path_problem
        if not f or path_problem(os.path.join(f, "a.pdf")):
            return FOLDER_ERROR
        try:
            os.makedirs(f, exist_ok=True)
        except OSError:
            return FOLDER_ERROR
        if not os.access(f, os.W_OK):
            return FOLDER_READONLY
        return None

    def commit(self):
        """고른 값을 store 에 남긴다(설정 파일 쓰기는 부른 쪽)."""
        self.store["custom"] = self.custom()
        self.store["folder"] = self._custom_text.strip() or self.default

    def folder_for(self, input_path: str | None) -> str:
        """그 입력 파일의 결과를 둘 폴더. 원본 파일 위치인데 원본 폴더가 없으면(새 문서) 내 문서."""
        if self.custom():
            return self.folder()
        d = os.path.dirname(input_path) if input_path else ""
        if d and os.path.isdir(d):
            return d
        from .fileops import documents_dir
        return documents_dir()
