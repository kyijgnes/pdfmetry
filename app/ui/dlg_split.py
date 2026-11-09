"""PDF 분할 창 · 페이지 레이블 창. run_split · run_page_label 이 창부터 결과 안내까지 맡고, window.py 는 부르기만 한다."""
from __future__ import annotations

import os
import subprocess
import tempfile

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QSpinBox, QVBoxLayout)

from ..engine import pages as P
from . import dialogs as D

SAVE_FIRST = "작업을 실행하기 전에 '{0}'의 변경 사항을 저장하세요."
SIZE_TOO_LARGE = "올바른 분할 크기를 입력하세요. 분할 크기가 현재 파일 크기보다 큽니다."
BAD_PATH = "유효하지 않은 경로"
BAD_FOLDER = "폴더 경로가 잘못되었습니다. 다시 시도해주세요."
READ_ONLY = "폴더 경로가 읽기 전용입니다. 확인하십시오."
SPLIT_DONE = "페이지가 {0} 문서로 성공적으로 분할되었습니다."
OVER_LIMIT = "파일 크기가 분할 크기 설정을 초과하는 파일이 {0}개 있습니다. 파일 크기를 줄이기 위해 일괄 압축을 시도할 수 있습니다."
LABEL_START_ERR = "1 이상의 시작 값을 입력하십시오."
LABEL_PAGE_ERR = "문서에 '{0}'페이지가 존재하지 않습니다."
LABEL_RANGE_ERR = "시작 페이지는 끝 페이지 번호보다 작거나 같아야 합니다"
RANGE_EMPTY = "유효한 페이지 범위를 입력하십시오."


def default_split_folder() -> str:
    desk = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or os.path.expanduser("~")
    return os.path.normpath(os.path.join(desk, "PDF메트리", "Split"))


def check_folder(path: str) -> str | None:
    """쓸 수 있는 폴더면 None, 아니면 오류 문구. 없는 폴더는 만들어 본다."""
    path = (path or "").strip()
    if not path or not os.path.isabs(path):
        return BAD_FOLDER
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return BAD_FOLDER
    if not os.path.isdir(path):
        return BAD_FOLDER
    try:
        fd, tmp = tempfile.mkstemp(dir=path, prefix=".pdfm")
        os.close(fd); os.remove(tmp)
    except OSError:
        return READ_ONLY
    return None


def open_folder(path: str):
    if os.path.isdir(path):
        os.startfile(path)
    elif os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


# ── PDF 분할 ──────────────────────────────────────────────────
class SplitDialog(QDialog):
    MODES = [("count", "파일 당 페이지 수로 분할하기"), ("size", "파일 크기로 분할"), ("bookmarks", "북마크 별로 분할하기"),
             ("parts", "파일 개수로 분할하기"), ("ranges", "페이지 범위마다 분할하기")]

    def __init__(self, parent, total: int, file_size: int, has_bookmarks: bool, src_dir: str,
                 custom: bool = False, custom_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("분할"); self.setMinimumWidth(460)
        self.total, self.file_size, self.has_bookmarks, self.src_dir = total, file_size, has_bookmarks, src_dir
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)

        box = QGroupBox("분할 유형"); g = QGridLayout(box); g.setColumnStretch(2, 1); g.setHorizontalSpacing(14)
        self.group = QButtonGroup(self); self.radios = {}
        for row, (key, text) in enumerate(self.MODES):
            r = QRadioButton(text); self.group.addButton(r, row); self.radios[key] = r
            g.addWidget(r, row, 0)
        self.pages_n = QSpinBox(); self.pages_n.setRange(1, max(1, total)); self.pages_n.setValue(1)
        self.pages_unit = QLabel()
        self.mb = QDoubleSpinBox(); self.mb.setRange(0.01, 100); self.mb.setDecimals(2); self.mb.setSingleStep(0.1); self.mb.setValue(2)
        self.mb_unit = QLabel()
        self.parts_n = QSpinBox(); self.parts_n.setRange(1, max(1, total)); self.parts_n.setValue(min(2, max(1, total)))
        self.parts_unit = QLabel()
        for row, (w, unit) in enumerate(((self.pages_n, self.pages_unit), (self.mb, self.mb_unit))):
            g.addWidget(w, row, 1); g.addWidget(unit, row, 2)
        g.addWidget(self.parts_n, 3, 1); g.addWidget(self.parts_unit, 3, 2)
        self.ranges = QPlainTextEdit(); self.ranges.setPlaceholderText("한 줄에 범위 하나\n1-3\n4-10"); self.ranges.setMaximumHeight(70)
        g.addWidget(self.ranges, 5, 0, 1, 3)
        lay.addWidget(box)

        out = QGroupBox("저장"); hl = QHBoxLayout(out)
        self.where = QComboBox(); self.where.addItems(["원본 파일 위치", "폴더 지정"])
        self.dir = QLineEdit(); self.pick = QPushButton("폴더"); self.pick.clicked.connect(self._pick)
        hl.addWidget(self.where); hl.addWidget(self.dir, 1); hl.addWidget(self.pick)
        lay.addWidget(out)
        self.custom_dir = os.path.normpath(custom_dir or default_split_folder()); self._showing_custom = False
        self.where.setCurrentIndex(1 if custom else 0)

        self.bb = D._buttons(self, "확인"); self.bb.accepted.disconnect(); self.bb.accepted.connect(self._ok)
        lay.addWidget(self.bb)
        self.radios["count"].setChecked(True)
        self.group.idToggled.connect(lambda *_: self._sync())
        for sb in (self.pages_n, self.mb, self.parts_n):
            sb.valueChanged.connect(self._sync)
        self.where.currentIndexChanged.connect(self._sync_where)
        self._sync(); self._sync_where()

    def mode(self) -> str:
        return self.MODES[self.group.checkedId()][0]

    def _sync(self):
        m = self.mode()
        self.pages_n.setEnabled(m == "count"); self.mb.setEnabled(m == "size")
        self.parts_n.setEnabled(m == "parts"); self.ranges.setEnabled(m == "ranges")
        self.pages_unit.setText(f"파일 당 {self.pages_n.value()} 페이지")
        self.mb_unit.setText(f"파일당 {self.mb.value():g}MB")
        self.parts_unit.setText(f"파일 {self.parts_n.value()}개")

    def _sync_where(self):
        custom = self.where.currentIndex() == 1
        if self._showing_custom and not custom:
            self.custom_dir = self.dir.text()
        self._showing_custom = custom
        self.dir.setText(self.custom_dir if custom else self.src_dir)
        self.dir.setEnabled(custom); self.pick.setEnabled(custom)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더", self.dir.text())
        if d:
            self.dir.setText(os.path.normpath(d))

    def _ok(self):
        m = self.mode()
        if m == "size" and self.mb.value() * 1024 * 1024 > self.file_size:
            QMessageBox.warning(self, "분할", SIZE_TOO_LARGE); self.mb.setFocus(); return
        if m == "bookmarks" and not self.has_bookmarks:
            QMessageBox.warning(self, "분할", P.NO_TOP_BOOKMARK); return
        if m == "ranges":
            try:
                if not [r for r in self.ranges.toPlainText().splitlines() if r.strip() and P.parse_range(r, self.total)]:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "분할", RANGE_EMPTY); self.ranges.setFocus(); return
        err = check_folder(self.dir.text())
        if err:
            QMessageBox.warning(self, BAD_PATH, err); self.dir.setFocus(); return
        self.accept()

    def values(self) -> dict:
        m = self.mode()
        value = {"count": self.pages_n.value(), "size": self.mb.value(), "parts": self.parts_n.value()}.get(m, 1)
        return {"mode": m, "value": value, "ranges": self.ranges.toPlainText().splitlines(),
                "out_dir": os.path.normpath(self.dir.text().strip()), "custom": self.where.currentIndex() == 1,
                "custom_dir": os.path.normpath(self.dir.text().strip()) if self.where.currentIndex() == 1 else self.custom_dir}


class SplitResultDialog(QDialog):
    """끝 안내. 크기 방식에서 한도를 넘긴 파일이 있으면 그 안내와 「압축」 단추를 같이 보인다."""
    def __init__(self, parent, count: int, folder: str, over: list[str], on_compress=None):
        super().__init__(parent)
        self.setWindowTitle("분할"); self.setMinimumWidth(420)
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)
        msg = QLabel(OVER_LIMIT.format(len(over)) if over else SPLIT_DONE.format(count)); msg.setWordWrap(True)
        lay.addWidget(msg)
        row = QHBoxLayout(); row.addStretch()
        if over and on_compress:
            b = QPushButton("압축"); b.clicked.connect(lambda: (self.accept(), on_compress(over))); row.addWidget(b)
        b = QPushButton("폴더 열기"); b.setDefault(True); b.clicked.connect(lambda: (self.accept(), open_folder(folder))); row.addWidget(b)
        D.settle_default(self, b)
        c = QPushButton("닫기"); c.clicked.connect(self.reject); row.addWidget(c)
        lay.addLayout(row)


def run_split(win, t):
    """분할 창 → 저장 요구 → 나누기 → 결과 안내. 설정(저장 폴더 방식·폴더)은 앱 설정에 남긴다."""
    from .window import save_settings
    d = t.doc
    if d.modified or not d.path or not os.path.exists(d.path):
        if QMessageBox.question(win, "분할", SAVE_FIRST.format(d.name), QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) \
                != QMessageBox.StandardButton.Ok or not win.save(t):
            return
    prefs = win.prefs
    has_bm = any(e[0] == 1 and e[2] > 0 for e in d.flat_toc())
    dlg = SplitDialog(win, d.page_count, os.path.getsize(d.path), has_bm, os.path.dirname(os.path.abspath(d.path)),
                      bool(prefs.get("split_custom")), prefs.get("split_folder", ""))
    if not dlg.exec():
        return
    vals = dlg.values()
    prefs["split_custom"] = vals["custom"]; prefs["split_folder"] = vals["custom_dir"]; save_settings(win.settings)
    outs = D.ProgressDialog(win, "분할", P.split, d, vals["out_dir"], vals["mode"], vals["value"], vals["ranges"]).run()
    if not outs:
        return
    folder = os.path.dirname(outs[0])
    over = [o for o in outs if os.path.getsize(o) > vals["value"] * 1024 * 1024] if vals["mode"] == "size" else []
    win.say(SPLIT_DONE.format(len(outs)))
    SplitResultDialog(win, len(outs), folder, over, lambda files: compress_files(win, files, folder)).exec()


def compress_files(win, files: list[str], folder: str):
    """넘친 파일들을 「PDF 압축」 창에 담아 연다(결과는 「_압축」, 저장 위치는 그 창 기억대로)."""
    from .dlg_compress import CompressDialog
    CompressDialog(win, list(files)).exec()


# ── 페이지 레이블 ─────────────────────────────────────────────
class PageLabelDialog(QDialog):
    def __init__(self, parent, f, selected: list[int]):
        super().__init__(parent)
        self.setWindowTitle("페이지 레이블"); self.setMinimumWidth(440)
        self.f, self.total = f, f.page_count
        sel = sorted(set(selected or []))
        self.sel = (sel[0], sel[-1]) if sel else (0, self.total - 1)
        labs = [s or str(i + 1) for i, s in enumerate(P.page_label_strings(f))]
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 12); lay.setSpacing(10)

        rng = QGroupBox("페이지 범위"); g = QGridLayout(rng)
        self.all = QRadioButton("모든 페이지"); self.chosen = QRadioButton("선택한 페이지"); self.custom = QRadioButton("범위")
        self.r_from = QLineEdit(labs[self.sel[0]]); self.r_to = QLineEdit(labs[self.sel[1]])
        for e in (self.r_from, self.r_to):
            e.setMaximumWidth(80)
        g.addWidget(self.all, 0, 0, 1, 5); g.addWidget(self.chosen, 1, 0, 1, 5)
        g.addWidget(self.custom, 2, 0); g.addWidget(self.r_from, 2, 1); g.addWidget(QLabel("부터"), 2, 2)
        g.addWidget(self.r_to, 2, 3); g.addWidget(QLabel(f"/ {self.total}"), 2, 4); g.setColumnStretch(5, 1)
        self.range_group = QButtonGroup(self)
        for b in (self.all, self.chosen, self.custom):
            self.range_group.addButton(b)
        whole = not sel or (sel[0] == 0 and sel[-1] == self.total - 1)
        (self.all if whole else self.chosen).setChecked(True)
        self.chosen.setEnabled(bool(sel))
        lay.addWidget(rng)

        num = QGroupBox("페이지 번호 매기기"); v = QVBoxLayout(num)
        self.new = QRadioButton("새 섹션 시작"); self.extend = QRadioButton("이전 섹션에서 사용된 번호 매기기를 선택한 페이지로 확장")
        self.new.setChecked(True); v.addWidget(self.new); v.addWidget(self.extend)
        grid = QGridLayout(); grid.setColumnStretch(1, 1)
        self.style = QComboBox(); self.style.addItems([text for _, text in P.LABEL_STYLES])
        self.prefix = QLineEdit(); self.start = QLineEdit("1")
        self.preview = QLabel()
        self.style_l, self.prefix_l, self.start_l = QLabel("스타일"), QLabel("접두사"), QLabel("시작")
        for row, (l, w) in enumerate(((self.style_l, self.style), (self.prefix_l, self.prefix), (self.start_l, self.start))):
            grid.addWidget(l, row, 0); grid.addWidget(w, row, 1)
        self.preview_l = QLabel("미리보기:")
        grid.addWidget(self.preview_l, 3, 0); grid.addWidget(self.preview, 3, 1)
        v.addLayout(grid)
        lay.addWidget(num)

        d = P.label_at(f, self.sel[0])
        self.style.setCurrentIndex([k for k, _ in P.LABEL_STYLES].index(d["style"]) if d["style"] in [k for k, _ in P.LABEL_STYLES] else 0)
        self.prefix.setText(d["prefix"]); self.start.setText(str(d["start"]))

        self.bb = D._buttons(self, "적용"); self.bb.accepted.disconnect(); self.bb.accepted.connect(self._ok)
        lay.addWidget(self.bb)
        for b in (self.all, self.chosen, self.custom, self.new, self.extend):
            b.toggled.connect(self._sync)
        self.style.currentIndexChanged.connect(self._sync)
        self.prefix.textChanged.connect(self._sync); self.start.textChanged.connect(self._sync)
        self._sync()

    def style_key(self) -> str:
        return P.LABEL_STYLES[self.style.currentIndex()][0]

    def _sync(self):
        self.r_from.setEnabled(self.custom.isChecked()); self.r_to.setEnabled(self.custom.isChecked())
        new = self.new.isChecked()
        for w in (self.style, self.prefix, self.style_l, self.prefix_l, self.preview, self.preview_l):
            w.setEnabled(new)
        has_num = bool(self.style_key())
        self.start.setEnabled(new and has_num); self.start_l.setEnabled(new and has_num)
        n = P.label_number(self.start.text(), self.style_key()) if has_num else 1
        self.preview.setText(P.label_preview(self.style_key(), self.prefix.text(), n) if n and n >= 1 else "")

    def page_range(self) -> tuple[int, int]:
        """(첫 쪽, 끝 쪽) 0부터. 틀리면 ValueError(문구)."""
        if self.all.isChecked():
            return 0, self.total - 1
        if self.chosen.isChecked():
            return self.sel
        a_t, b_t = self.r_from.text().strip(), self.r_to.text().strip()
        if not a_t or not b_t:
            raise ValueError(RANGE_EMPTY)
        a = P.page_of_label(self.f, a_t)
        if a is None:
            raise ValueError(LABEL_PAGE_ERR.format(a_t))
        b = P.page_of_label(self.f, b_t, after=a)
        if b is None:
            raise ValueError(LABEL_PAGE_ERR.format(b_t))
        if a > b:
            raise ValueError(LABEL_RANGE_ERR)
        return a, b

    def _ok(self):
        try:
            self.page_range()
        except ValueError as e:
            QMessageBox.warning(self, "페이지 레이블", str(e)); self.r_from.setFocus(); return
        if self.new.isChecked() and self.style_key():
            n = P.label_number(self.start.text(), self.style_key())
            if not n or n < 1:
                QMessageBox.warning(self, "페이지 레이블", LABEL_START_ERR); self.start.setFocus(); self.start.selectAll(); return
        self.accept()

    def values(self) -> dict:
        a, b = self.page_range()
        n = P.label_number(self.start.text(), self.style_key()) if self.style_key() else 1
        return {"first": a, "last": b, "style": self.style_key(), "prefix": self.prefix.text(), "start": n or 1,
                "extend": self.extend.isChecked()}


def run_page_label(win, t, selected: list[int]):
    dlg = PageLabelDialog(win, t.doc.fitz, selected)
    if dlg.exec():
        P.apply_labels(t.doc, **dlg.values())
        t.changed(None, structural=True)
