"""PDF 병합 창: 파일 목록 · 북마크로 목차 쪽 추가 · 파일 이름(「첫 파일_합치기.pdf」) · 저장 위치 · [미리 보기] · [적용].

암호 걸린 PDF 는 목록에 넣을 때 암호를 묻는다. [미리 보기] 는 임시 파일로 합쳐 쪽 그림 격자로 보인다.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import fitz
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from ..engine import pages as P
from .file_list import FileListPanel, OutputPanel, unique_path
from .dialogs import settle_default

SUFFIX = "_합치기"
CLOSE_TIPS = "파일을 종료하시겠습니까?"
MERGE_FILTER = ("PDF·이미지·문서 (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp *.xps *.epub *.txt *.svg *.cbz)"
                ";;PDF 파일 (*.pdf);;모든 파일 (*)")
MERGE_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp", ".xps", ".epub", ".txt", ".svg", ".cbz")


def default_name(paths: list[str]) -> str:
    if not paths:
        return "합치기.pdf"
    return os.path.splitext(os.path.basename(paths[0]))[0] + SUFFIX + ".pdf"


class _MergeList(FileListPanel):
    """병합은 PDF 밖에 그림·글 파일도 받는다."""

    def _add(self, path: str, password: str | None = None, ask: bool = True) -> bool:
        if path and not path.lower().endswith(MERGE_EXTS):
            return False
        return super()._add(path, password, ask)


class PreviewDialog(QDialog):
    """「미리보기 결합」: 합친 쪽 그림 격자, [수정으로 돌아가기] · [적용]."""

    def __init__(self, parent, pdf_path: str):
        super().__init__(parent)
        self.setWindowTitle("미리보기 결합"); self.resize(760, 560)
        lay = QVBoxLayout(self)
        self.grid = QListWidget(); self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(140, 190)); self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static); self.grid.setSpacing(8)
        lay.addWidget(self.grid, 1)
        d = fitz.open(pdf_path)
        try:
            for i, page in enumerate(d):
                z = 190 / max(1.0, page.rect.height)
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                self.grid.addItem(QListWidgetItem(QIcon(QPixmap.fromImage(img)), str(i + 1)))
        finally:
            d.close()
        bb = QDialogButtonBox()
        back = bb.addButton("수정으로 돌아가기", QDialogButtonBox.ButtonRole.RejectRole)
        ok = bb.addButton("적용", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setDefault(True); back.setAutoDefault(False)
        settle_default(self, ok)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class MergeDialog(QDialog):
    """[적용] 이 끝나면 accept 하고 out_path 에 결과 경로를 둔다(여는 일은 부른 쪽)."""

    def __init__(self, win, paths: list[str] | None = None, passwords: list[str | None] | None = None):
        super().__init__(win)
        self.win = win
        self.prefs = win.prefs if win is not None else {}
        self.settings = win.settings if win is not None else {}
        self.out_path: str | None = None
        self.done_ok = False
        self._auto_name = True
        self.setWindowTitle("PDF 병합"); self.resize(820, 540)
        lay = QVBoxLayout(self)
        self.files = _MergeList(self, "PDF 병합", MERGE_FILTER, paths=paths, passwords=passwords, only_pdf=False)
        lay.addWidget(self.files, 1)
        self.toc = QCheckBox("북마크로 목차 쪽 추가"); self.toc.setChecked(bool(self.prefs.get("merge_toc", False)))
        lay.addWidget(self.toc)
        row = QHBoxLayout(); row.addWidget(QLabel("파일 이름"))
        self.name = QLineEdit(default_name(self.files.paths()))
        self.name.textEdited.connect(lambda _t: setattr(self, "_auto_name", False))
        row.addWidget(self.name, 1); lay.addLayout(row)
        self.out = OutputPanel(self, self.settings.setdefault("merge_out", {}), "병합")
        lay.addWidget(self.out)
        bb = QHBoxLayout(); bb.addStretch()
        self.preview_btn = QPushButton("미리 보기"); self.preview_btn.clicked.connect(self.preview)
        self.apply_btn = QPushButton("적용"); self.apply_btn.setDefault(True); self.apply_btn.clicked.connect(self.apply)
        settle_default(self, self.apply_btn)
        bb.addWidget(self.preview_btn); bb.addWidget(self.apply_btn); lay.addLayout(bb)
        self.files.changed.connect(self._sync)
        self._sync()

    def _sync(self):
        has = self.files.count() > 0
        self.apply_btn.setEnabled(has); self.preview_btn.setEnabled(has)
        if self._auto_name:
            self.name.setText(default_name(self.files.paths()))

    def _save(self):
        if self.win is not None:
            from .window import save_settings
            save_settings(self.settings)

    def target(self) -> str | None:
        """저장할 경로(같은 이름이 있으면 _번호). 이름·폴더가 잘못되면 알리고 None."""
        why = self.out.check()
        if why:
            QMessageBox.warning(self, "PDF 병합", why); return None
        name = self.name.text().strip()
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        if not name or any(c in name for c in '\\/:*?"<>|'):
            QMessageBox.warning(self, "PDF 병합", "유효하지 않은 경로"); self.name.setFocus(); return None
        paths = self.files.paths()
        return unique_path(self.out.folder_for(paths[0] if paths else None), name, ".pdf")

    def _merge(self, out: str) -> bool:
        from .dialogs import ProgressDialog
        dlg = ProgressDialog(self, "PDF 합치기", P.merge, self.files.paths(), out,
                             passwords=self.files.passwords(), toc_page=self.toc.isChecked())
        return dlg.run() is not None

    def preview(self):
        if not self.files.count():
            return
        folder = tempfile.mkdtemp(prefix="pdfmetry_merge_")
        tmp = os.path.join(folder, "미리보기.pdf")
        try:
            if not self._merge(tmp) or not os.path.isfile(tmp):
                QMessageBox.warning(self, "미리보기 결합", "미리보기 생성 실패"); return
            if PreviewDialog(self, tmp).exec():
                self.apply(ready=tmp)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def apply(self, ready: str | None = None):
        """합쳐 저장하고 닫는다. ready: 미리 보기로 이미 합친 임시 파일(그 파일을 옮겨 쓴다)."""
        if not self.files.count():
            return
        out = self.target()
        if not out:
            return
        self.prefs["merge_toc"] = self.toc.isChecked()
        self.out.commit(); self._save()
        if ready and os.path.isfile(ready):
            shutil.copyfile(ready, out)
        elif not self._merge(out):
            return
        self.out_path = out
        self.done_ok = True
        self.accept()

    def reject(self):
        if not self.done_ok and self.files.count() and \
                QMessageBox.question(self, "PDF 병합", CLOSE_TIPS) != QMessageBox.StandardButton.Yes:
            return
        super().reject()
