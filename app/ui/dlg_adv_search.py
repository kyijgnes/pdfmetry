"""고급 검색(Ctrl+Shift+F): 현재 문서 · 모든 열린 문서 · 폴더(하위 폴더까지)의 PDF 에서 찾고, 바꾼 결과는 새 파일로만.

창 하나가 조건 → 진행·결과 → 바꾸기 화면을 갈아 끼운다. 찾기는 일꾼 스레드, 진행 글은 0.5초마다 고친다.
체크 상태·바꾸기 저장 폴더는 prefs["adv_search"] 에 기억하고 검색어는 기억하지 않는다.
"""
from __future__ import annotations

import html
import os
import subprocess
import tempfile

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QProgressDialog, QPushButton, QRadioButton,
                               QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..engine import search_multi as SM
from . import icons
from .search_panel import PREFIX_ROLE, FoundDelegate

KEY = "adv_search"
DEFAULTS = {"case": False, "whole": False, "comments": False, "bookmarks": False, "attach": False,
            "never_fail": False, "never_repl_fail": False, "open_folder": False, "repl_dir": ""}
CURRENT, OPEN, FOLDER = 0, 1, 2
KIND_ICON = {"text": "text", "annot": "comments", "bookmark": "bookmark"}
KIND_NAME = {"text": "", "annot": "주석", "bookmark": "북마크"}
FOUND_ROLE = Qt.ItemDataRole.UserRole


def prefs_of(win) -> dict:
    d = win.prefs.get(KEY)
    if not isinstance(d, dict):
        d = win.prefs[KEY] = {}
    for k, v in DEFAULTS.items():
        d.setdefault(k, v)
    return d


def reveal(paths: list[str]):
    """바꾼 결과: 하나면 탐색기에서 골라 보이고, 여럿이면 폴더를 연다."""
    if len(paths) == 1:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(paths[0])])
    elif paths:
        os.startfile(os.path.dirname(paths[0]))  # noqa: S606


class _Worker(QThread):
    fileDone = Signal(object)    # FileResult
    total = Signal(int)          # 찾을 파일 수(폴더는 다 모은 뒤 알림)

    def __init__(self, targets: list, folder: str, opts: SM.Options):
        super().__init__()
        self.targets, self.folder, self.opts = targets, folder, opts   # targets: [(경로, 열린 Document 또는 None)]
        self.stopped = False

    def run(self):
        targets = self.targets
        if self.folder:
            targets = [(p, self.opened.get(os.path.normcase(os.path.abspath(p)))) for p in SM.pdf_files(self.folder)]
        self.total.emit(len(targets))
        for path, doc in targets:
            if self.stopped:
                return
            r = SM.search_file(path, self.opts, opened=doc, stop=lambda: self.stopped)
            if r.status != SM.MISSING:   # 없는 파일은 조용히 건너뛴다
                self.fileDone.emit(r)


class AdvancedSearchDialog(QDialog):
    def __init__(self, win):
        super().__init__(win, Qt.WindowType.Tool)
        self.win = win
        self.setWindowTitle("고급 검색")
        self.resize(300, 560); self.setMinimumSize(288, 500)
        self.worker: _Worker | None = None
        self.results: list[SM.FileResult] = []   # 결과가 있는 파일만
        self.failed: list[SM.FileResult] = []
        self.done_files = 0
        self.total_files = 0
        self.opts = SM.Options("")
        self._shown_q = ("", False)
        self.pages = QStackedWidget()
        v = QVBoxLayout(self); v.setContentsMargins(10, 10, 10, 10); v.addWidget(self.pages)
        self._build_cond(); self._build_result(); self._build_replace()
        self.timer = QTimer(self); self.timer.setInterval(500); self.timer.timeout.connect(self._progress)

    # ── 조건 화면 ─────────────────────────────────────────────
    def _build_cond(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        v.addWidget(QLabel("파일 위치"))
        self.where = QButtonGroup(self)
        self.r_cur = QRadioButton("현재 문서"); self.r_open = QRadioButton("모든 열린 문서"); self.r_dir = QRadioButton("폴더 위치 찾기")
        for k, r in enumerate((self.r_cur, self.r_open, self.r_dir)):
            self.where.addButton(r, k); v.addWidget(r)
        row = QHBoxLayout()
        self.folder = QLineEdit(); self.folder.setPlaceholderText("폴더 경로"); row.addWidget(self.folder, 1)
        self.browse = QToolButton(); self.browse.setIcon(icons.icon("folder", 16)); self.browse.clicked.connect(self._pick_folder)
        row.addWidget(self.browse)
        v.addLayout(row)
        v.addSpacing(4)
        self.query = QLineEdit(); self.query.setPlaceholderText("찾을 단어 또는 구문"); self.query.returnPressed.connect(self.start)
        v.addWidget(self.query)
        self.c_case = QCheckBox("대/소문자 구분"); self.c_whole = QCheckBox("단어 단위로"); self.c_comments = QCheckBox("주석 포함")
        self.c_bm = QCheckBox("북마크 포함"); self.c_attach = QCheckBox("첨부 파일 포함")
        for c in (self.c_case, self.c_whole, self.c_comments, self.c_bm, self.c_attach):
            v.addWidget(c)
        v.addStretch()
        bl = QHBoxLayout(); bl.addStretch()
        self.go = QPushButton("검색"); self.go.setDefault(True); self.go.clicked.connect(self.start); bl.addWidget(self.go)
        cancel = QPushButton("취소"); cancel.clicked.connect(self.close); bl.addWidget(cancel)
        v.addLayout(bl)
        for s in (self.where.idToggled, ):
            s.connect(lambda *_: self._sync_cond())
        self.folder.textChanged.connect(self._sync_cond); self.query.textChanged.connect(self._sync_cond)
        self.cond = w; self.pages.addWidget(w)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "폴더 고르기", self.folder.text())
        if d:
            self.folder.setText(d); self.r_dir.setChecked(True)

    def _sync_cond(self):
        has = bool(self.win.tabs)
        self.r_cur.setEnabled(has); self.r_open.setEnabled(has)
        k = self.where.checkedId()
        self.folder.setEnabled(k == FOLDER); self.browse.setEnabled(k == FOLDER)
        ok_where = (k in (CURRENT, OPEN) and has) or (k == FOLDER and os.path.isdir(self.folder.text().strip()))
        self.go.setEnabled(ok_where and bool(self.query.text().strip()))

    def show_cond(self):
        p = prefs_of(self.win)
        self.c_case.setChecked(p["case"]); self.c_whole.setChecked(p["whole"]); self.c_comments.setChecked(p["comments"])
        self.c_bm.setChecked(p["bookmarks"]); self.c_attach.setChecked(p["attach"])
        if self.where.checkedId() < 0 or (not self.win.tabs and self.where.checkedId() != FOLDER):
            (self.r_cur if self.win.tabs else self.r_dir).setChecked(True)
        self.pages.setCurrentWidget(self.cond)
        self._sync_cond()
        self.query.setFocus(); self.query.selectAll()

    # ── 진행·결과 화면 ────────────────────────────────────────
    def _build_result(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        self.look = QLabel(); self.look.setTextFormat(Qt.TextFormat.RichText); v.addWidget(self.look)
        self.scope = QLabel(); self.scope.setObjectName("dim"); self.scope.setWordWrap(True); v.addWidget(self.scope)
        self.prog_text = QLabel(); v.addWidget(self.prog_text)
        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setFixedHeight(6); v.addWidget(self.bar)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setColumnCount(1)
        self.tree.setItemDelegate(FoundDelegate(self.tree, self))
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.itemClicked.connect(self._clicked)
        v.addWidget(self.tree, 1)
        bl = QHBoxLayout()
        self.to_replace = QPushButton("바꾸기"); self.to_replace.setFlat(True); self.to_replace.clicked.connect(self.show_replace)
        bl.addWidget(self.to_replace); bl.addStretch()
        self.stop_btn = QPushButton("중단"); self.stop_btn.clicked.connect(self._stop_or_new); bl.addWidget(self.stop_btn)
        v.addLayout(bl)
        self.result = w; self.pages.addWidget(w)

    def _targets(self, k: int) -> list:
        if k == CURRENT:
            t = self.win.cur()
            return [(t.doc.path or self.win.doctabs.tabText(self.win.tabs.index(t)), t.doc)] if t else []
        out = []
        from .fileops import windows
        for w in windows(self.win):
            out += [(t.doc.path or w.doctabs.tabText(i), t.doc) for i, t in enumerate(w.tabs)]
        return out

    def _opened(self) -> dict:
        from .fileops import windows
        return {os.path.normcase(os.path.abspath(t.doc.path)): t.doc for w in windows(self.win) for t in w.tabs if t.doc.path}

    def start(self):
        if not self.go.isEnabled():
            return
        p = prefs_of(self.win)
        p.update(case=self.c_case.isChecked(), whole=self.c_whole.isChecked(), comments=self.c_comments.isChecked(),
                 bookmarks=self.c_bm.isChecked(), attach=self.c_attach.isChecked())
        self._save()
        q = self.query.text().strip()
        self.opts = SM.Options(q, p["case"], p["whole"], p["comments"], p["bookmarks"], p["attach"])
        self._shown_q = (q, p["case"])
        k = self.where.checkedId()
        folder = self.folder.text().strip() if k == FOLDER else ""
        targets = [] if folder else self._targets(k)
        self.look.setText(f"<b>{html.escape(q)}</b> 찾기")
        self.scope.setText(folder or (targets[0][0] if k == CURRENT and targets else "모든 열린 문서"))
        self.results, self.failed, self.done_files = [], [], 0
        self.total_files = len(targets)
        self.tree.clear()
        self.bar.setRange(0, 0 if folder else max(1, len(targets))); self.bar.setValue(0)
        self.to_replace.setEnabled(False)
        self.stop_btn.setText("중단")
        self.pages.setCurrentWidget(self.result)
        w = self.worker = _Worker(targets, folder, self.opts)
        w.opened = self._opened()
        w.total.connect(self._got_total)
        w.fileDone.connect(self._file_done)
        w.finished.connect(lambda w=w: self._finished(w))
        self._progress()
        self.timer.start()
        w.start()

    def _got_total(self, n: int):
        self.total_files = n
        self.bar.setRange(0, max(1, n))

    def _file_done(self, r: SM.FileResult):
        self.done_files += 1
        if r.status != SM.OK:
            self.failed.append(r); return
        if not r.items:
            return
        self.results.append(r)
        top = QTreeWidgetItem([r.path]); top.setData(0, PREFIX_ROLE, len(r.path)); top.setIcon(0, icons.icon("open", 16))
        top.setToolTip(0, r.path)   # 긴 경로가 잘릴 때 원문
        for f in r.items:
            head = (f"[{f.attach}] " if f.attach else "") + (f"{KIND_NAME[f.kind]} " if KIND_NAME[f.kind] else "") \
                + (f"{f.page + 1}쪽   " if f.page >= 0 else "   ")
            it = QTreeWidgetItem([head + f.context[:60]])
            it.setData(0, PREFIX_ROLE, len(head)); it.setData(0, FOUND_ROLE, f)
            it.setIcon(0, icons.icon("attach" if f.attach else KIND_ICON[f.kind], 16))
            top.addChild(it)
        self.tree.addTopLevelItem(top); top.setExpanded(True)

    def _count(self) -> int:
        return sum(len(r.items) for r in self.results)

    def _progress(self):
        self.prog_text.setText(f"{self.done_files}개 문서, {self._count()}개 결과")
        if self.bar.maximum():
            self.bar.setValue(self.done_files)

    def _finished(self, w):
        if w is not self.worker:
            return
        self.timer.stop(); self._progress()
        self.bar.setRange(0, 1); self.bar.setValue(1)
        self.stop_btn.setText("새 검색")
        self.to_replace.setEnabled(bool(self.results) and any(os.path.isfile(r.path) for r in self.results))
        self.worker = None
        p = prefs_of(self.win)
        if self.failed and not p["never_fail"]:
            enc = [r.path for r in self.failed if r.status == SM.ENCRYPTED]
            bad = [r.path for r in self.failed if r.status != SM.ENCRYPTED]
            parts = []
            if enc:
                parts.append("파일이 암호화 되었습니다\n" + "\n".join(enc))
            if bad:
                parts.append("파일이 손상되었습니다\n" + "\n".join(bad))
            self._failure_box("검색", f"파일 {len(self.failed)}개를 검색하지 못했습니다.\n\n" + "\n\n".join(parts), "never_fail")

    def _failure_box(self, title: str, text: str, key: str):
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok, self)
        cb = QCheckBox("다시 보지 않기"); box.setCheckBox(cb)
        box.exec()
        if cb.isChecked():
            prefs_of(self.win)[key] = True; self._save()

    def _stop_or_new(self):
        if self.worker is not None:
            self.worker.stopped = True
            self.worker.wait(5000)
            self._finished(self.worker)
        else:
            self.show_cond()

    def wait(self, ms: int = 30000):
        """시험용: 찾기가 끝날 때까지."""
        from PySide6.QtWidgets import QApplication
        if self.worker is not None:
            self.worker.wait(ms)
        QApplication.processEvents(); QApplication.processEvents()

    # 결과 누르기
    def _clicked(self, it: QTreeWidgetItem, _col: int = 0):
        f = it.data(0, FOUND_ROLE)
        if f is None:
            return
        fr = next((r for r in self.results if f in r.items), None)
        if fr is None:
            return
        t = self._open_tab(fr.path, f.attach)
        if t is None:
            return
        if f.kind == "annot":
            t.pick_annot(f.page, f.xref)
        elif f.kind == "bookmark":
            self._pick_bookmark(t, f)
        else:
            same = [x for x in fr.items if x.kind == "text" and x.attach == f.attach]
            t.show_hits([(x.page, x.rects) for x in same], same.index(f), jump=True)

    def _open_tab(self, path: str, attach: str):
        """그 파일을 연 탭(없으면 연다). 첨부 안 결과면 첨부를 임시 폴더에 꺼내 연다."""
        from .fileops import bring, find_open
        src = self._tab_by_label(path)
        if attach:
            data = dict(SM.pdf_attachments(src.doc.fitz, src.doc.lock)).get(attach) if src else None
            if data is None:
                f, _st = SM.open_raw(path)
                if f is None:
                    return None
                data = dict(SM.pdf_attachments(f)).get(attach); f.close()
            if data is None:
                return None
            tmp = os.path.join(tempfile.mkdtemp(prefix="pdfmetry_attach_"), os.path.basename(attach))
            with open(tmp, "wb") as fh:
                fh.write(data)
            path, src = tmp, None
        if src is not None:
            w, k = self._where_tab(src)
            bring(w, k)
            return src
        hit = find_open(self.win, path)
        if hit is None:
            self.win.open_path(path)
            hit = find_open(self.win, path)
        if hit is None:
            return None
        w, k = hit
        bring(w, k)
        return w.tabs[k]

    def _where_tab(self, tab):
        from .fileops import windows
        for w in windows(self.win):
            if tab in w.tabs:
                return w, w.tabs.index(tab)
        return self.win, 0

    def _tab_by_label(self, label: str):
        """결과 파일 이름(경로, 저장 안 된 문서는 탭 이름)으로 열린 탭."""
        from .fileops import find_open, windows
        if os.path.isfile(label):
            hit = find_open(self.win, label)
            return hit[0].tabs[hit[1]] if hit else None
        for w in windows(self.win):
            for k, t in enumerate(w.tabs):
                if not t.doc.path and w.doctabs.tabText(k) == label:
                    return t
        return None

    def _pick_bookmark(self, t, f: SM.Found):
        """북마크 패널을 열고 그 북마크를 고른다. 차례·이름이 안 맞으면 다시 찾으라고 알린다."""
        if not (t.side.isVisible() and t.side_key == "bookmark"):
            t.show_side("bookmark")
        tree = t.bookmarks.tree
        items = []

        def walk(it):
            items.append(it)
            for c in range(it.childCount()):
                walk(it.child(c))
        for k in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(k))
        it = items[f.index] if 0 <= f.index < len(items) else None
        if it is None or " ".join(it.text(0).split()) != " ".join(f.context.split()):
            QMessageBox.information(self, "검색", "문서가 마지막 검색 이후 수정되었으며 검색 결과가 더 이상 유효하지 않습니다. 다시 검색하십시오.")
            return
        tree.setCurrentItem(it); tree.scrollToItem(it)
        if f.page >= 0:
            t.viewer.goto(f.page)

    # ── 바꾸기 화면 ───────────────────────────────────────────
    def _build_replace(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        back = QToolButton(); back.setIcon(icons.icon("back", 16)); back.setText("뒤로"); back.setAutoRaise(True)
        back.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon); back.clicked.connect(lambda: self.pages.setCurrentWidget(self.result))
        v.addWidget(back)
        v.addWidget(QLabel("대상 문서"))
        self.files = QListWidget(); self.files.itemChanged.connect(self._file_checked); v.addWidget(self.files, 1)
        v.addWidget(QLabel("찾을 글자"))
        self.r_from = QLineEdit(); v.addWidget(self.r_from)
        v.addWidget(QLabel("바꿀 내용"))
        self.r_to = QLineEdit(); v.addWidget(self.r_to)
        v.addWidget(QLabel("다른 이름으로 저장할 폴더"))
        row = QHBoxLayout()
        self.rep_dir = QLineEdit(); row.addWidget(self.rep_dir, 1)
        b = QToolButton(); b.setIcon(icons.icon("folder", 16)); b.clicked.connect(self._pick_repl_dir); row.addWidget(b)
        v.addLayout(row)
        self.rep_open = QCheckBox("바꾼 뒤 폴더 열기"); v.addWidget(self.rep_open)
        tip = QLabel("글자만 바꿈 · 원본은 그대로 두고 「_바꾸기」 새 파일로 저장"); tip.setObjectName("dim"); tip.setWordWrap(True)
        v.addWidget(tip)
        bl = QHBoxLayout(); bl.addStretch()
        self.apply_btn = QPushButton("적용"); self.apply_btn.clicked.connect(self.apply_replace); bl.addWidget(self.apply_btn)
        v.addLayout(bl)
        for e in (self.r_from, self.r_to, self.rep_dir):
            e.textChanged.connect(self._sync_replace)
        self.replace_page = w; self.pages.addWidget(w)

    def _pick_repl_dir(self):
        d = QFileDialog.getExistingDirectory(self, "저장할 폴더", self.rep_dir.text())
        if d:
            self.rep_dir.setText(d)

    ALL = "모든 문서"

    def show_replace(self):
        p = prefs_of(self.win)
        self.files.blockSignals(True)
        self.files.clear()
        paths = [r.path for r in self.results if os.path.isfile(r.path)]
        if len(paths) > 1:
            self._add_check(self.ALL, None)
        for pth in paths:
            self._add_check(pth, pth)
        self.files.blockSignals(False)
        self.r_from.setText(self.opts.needle); self.r_to.clear()
        self.rep_dir.setText(p["repl_dir"] or (os.path.dirname(paths[0]) if paths else ""))
        self.rep_open.setChecked(p["open_folder"])
        self.pages.setCurrentWidget(self.replace_page)
        self._sync_replace()
        self.r_to.setFocus()

    def _add_check(self, text: str, path: str | None):
        it = QListWidgetItem(text); it.setData(Qt.ItemDataRole.UserRole, path)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable); it.setCheckState(Qt.CheckState.Checked)
        if path:
            it.setToolTip(path)   # 긴 경로가 잘릴 때 원문
        self.files.addItem(it)

    def _file_checked(self, it: QListWidgetItem):
        self.files.blockSignals(True)
        if it.data(Qt.ItemDataRole.UserRole) is None:   # 「모든 문서」
            for k in range(1, self.files.count()):
                self.files.item(k).setCheckState(it.checkState())
        elif self.files.count() and self.files.item(0).data(Qt.ItemDataRole.UserRole) is None:
            every = all(self.files.item(k).checkState() == Qt.CheckState.Checked for k in range(1, self.files.count()))
            self.files.item(0).setCheckState(Qt.CheckState.Checked if every else Qt.CheckState.Unchecked)
        self.files.blockSignals(False)
        self._sync_replace()

    def checked_paths(self) -> list[str]:
        return [self.files.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.files.count())
                if self.files.item(k).data(Qt.ItemDataRole.UserRole) and self.files.item(k).checkState() == Qt.CheckState.Checked]

    def _sync_replace(self, *_):
        self.apply_btn.setEnabled(bool(self.checked_paths()) and bool(self.r_from.text()) and bool(self.r_to.text())
                                  and bool(self.rep_dir.text().strip()))

    def apply_replace(self) -> list[str]:
        if not self.apply_btn.isEnabled():
            return []
        p = prefs_of(self.win)
        folder = self.rep_dir.text().strip()
        p.update(repl_dir=folder, open_folder=self.rep_open.isChecked()); self._save()
        todo = self.checked_paths()
        made, skipped = [], 0
        dlg = QProgressDialog("바꾸는 중", "취소", 0, len(todo), self)
        dlg.setWindowTitle("바꾸기"); dlg.setMinimumDuration(400); dlg.setWindowModality(Qt.WindowModality.WindowModal)
        for k, path in enumerate(todo):
            if dlg.wasCanceled():
                for m in made:   # 도중에 취소하면 만든 결과 파일을 지운다
                    try:
                        os.remove(m)
                    except OSError:
                        pass
                return []
            dlg.setValue(k)
            out, _n, st = SM.replace_file(path, self.r_from.text(), self.r_to.text(), folder,
                                          case=self.opts.case, whole=self.opts.whole, attach=self.opts.attach)
            if out:
                made.append(out)
            elif st != SM.OK:
                skipped += 1
        dlg.setValue(len(todo)); dlg.close()
        if skipped and not p["never_repl_fail"]:
            self._failure_box("바꾸기", f"파일이 손상되었거나 수정할 권한이 없기 때문에 바꾸기에서 {skipped}개의 파일을 건너뛰었습니다.",
                              "never_repl_fail")
        QMessageBox.information(self, "바꾸기", f"{len(made)} 파일이 교체되었습니다")
        if made and p["open_folder"]:
            reveal(made)
        return made

    # ── 공용 ──────────────────────────────────────────────────
    def _save(self):
        from .window import save_settings
        save_settings(self.win.settings)

    def place(self):
        """문서 화면 오른쪽 위에 붙인다. 떠 있는 바꾸기 판이 보이면 그 왼쪽에."""
        t = self.win.cur()
        if t is None:
            return
        fl = getattr(t, "find_float", None)
        if fl is not None and fl.isVisible():
            g = fl.mapToGlobal(QPoint(-self.width() - 8, 0))
        else:
            vs = t.view_stack
            g = vs.mapToGlobal(QPoint(vs.width() - self.width() - 16, 16))
        self.move(g)

    def closeEvent(self, e):
        if self.worker is not None:
            self.worker.stopped = True; self.worker.wait(5000)
        self.timer.stop()
        super().closeEvent(e)


def open_for(win) -> AdvancedSearchDialog:
    """창 하나에 하나. 다시 부르면 조건 화면으로 앞에 띄운다."""
    d = getattr(win, "_adv_search", None)
    if d is None:
        d = win._adv_search = AdvancedSearchDialog(win)
    if d.worker is None:
        t = win.cur()
        if t is not None and not d.query.text():
            d.query.setText(t.search.edit.text())
        d.show_cond()
    d.show(); d.place(); d.raise_(); d.activateWindow()
    return d
