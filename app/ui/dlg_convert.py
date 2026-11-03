"""내보내기 창: 형식 + [설정] 단추 + 파일 이름 + 저장 위치. 형식마다 설정 창(쪽 범위와 형식 칸)이 따로 있고,
쪽 범위를 뺀 설정은 형식별로 기억한다(settings["convert"][형식]).

이름이 겹치면 기본 이름을 `이름_1`, `이름_2` 로 비켜 간다(unique_stem). 다 끝나면 success_box 로 알린다."""
from __future__ import annotations

import glob
import os
import subprocess

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QRadioButton, QWidget)

from .dialogs import FormDialog, PageRangeEdit

# (이름, 키, 확장자). 이미지 키는 확장자와 같고 pdfa 만 .pdf
FORMATS = [("Word (.docx)", "docx", ".docx"), ("Excel (.xlsx)", "xlsx", ".xlsx"), ("PowerPoint (.pptx)", "pptx", ".pptx"),
           ("Epub (.epub)", "epub", ".epub"), ("JPG (.jpg)", "jpg", ".jpg"), ("JPEG (.jpeg)", "jpeg", ".jpeg"),
           ("PNG (.png)", "png", ".png"), ("BMP (.bmp)", "bmp", ".bmp"), ("GIF (.gif)", "gif", ".gif"),
           ("TIFF (.tiff)", "tiff", ".tiff"), ("TXT (.txt)", "txt", ".txt"), ("RTF (.rtf)", "rtf", ".rtf"),
           ("HTML (.html)", "html", ".html"), ("XML (.xml)", "xml", ".xml"), ("PDF/A (.pdf)", "pdfa", ".pdf"),
           ("한글 (.hwpx)", "hwpx", ".hwpx")]
IMAGE_KEYS = ("jpg", "jpeg", "png", "bmp", "gif", "tiff")
DPI_CHOICES = [("원본 DPI 유지", 0), ("72 dpi", 72), ("96 dpi", 96), ("150 dpi", 150), ("200 dpi", 200),
               ("300 dpi", 300), ("400 dpi", 400), ("600 dpi", 600)]
PPTX_DPIS = [100, 150, 200, 300, 600]
IMAGE_MODES = [("pages", "쪽마다 한 장"), ("merge", "한 장으로 합치기"), ("extract", "PDF 속 이미지 내보내기")]
OCR_MODES = [("scan", "스캔된 쪽만"), ("all", "모든 쪽"), ("off", "하지 않음")]
PDFA_STANDARDS = ["PDF/A-2b"]
DEFAULTS = {"dpi": 0, "mode": "pages", "ocr": "scan", "pptx_dpi": 150, "pdfa": "PDF/A-2b"}


def ext_of(key: str) -> str:
    return next((e for _, k, e in FORMATS if k == key), "." + key)


def default_folder(path: str | None) -> str:
    """원본이 있는 폴더, 새 문서면 내 문서."""
    if path and os.path.isdir(os.path.dirname(path)):
        return os.path.dirname(path)
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or os.path.expanduser("~")


def unique_stem(folder: str, stem: str, ext: str, multi: bool = False) -> str:
    """folder 안에서 겹치지 않는 이름. multi 는 쪽마다 파일을 만드는 이미지(<이름>_p0001.png 꼴이 하나라도 있으면 겹침)."""
    def taken(s: str) -> bool:
        if multi:
            return bool(glob.glob(os.path.join(glob.escape(folder), glob.escape(s) + "_p[0-9]*" + ext)))
        return os.path.exists(os.path.join(folder, s + ext))
    if not taken(stem):
        return stem
    n = 1
    while taken(f"{stem}_{n}"):
        n += 1
    return f"{stem}_{n}"


class ConvertSettings(FormDialog):
    """형식별 설정. 쪽 범위는 어느 형식에나 있고, 나머지는 형식마다 다르다."""

    def __init__(self, parent, fmt: str, total: int, current: int, pages: list[int] | None, saved: dict | None):
        label = next((n for n, k, _ in FORMATS if k == fmt), fmt)
        super().__init__(f"{label} 설정", parent, "확인")
        sv = {**DEFAULTS, **(saved or {})}
        self.fmt = fmt
        self.pages_edit = self.add("페이지 범위 변환", PageRangeEdit(total, current))
        if pages is not None:
            self.pages_edit.set_pages(pages)
        self.dpi = self.mode_group = self.ocr = self.pptx_dpi = self.std = None
        self.modes: dict[str, QRadioButton] = {}
        if fmt in IMAGE_KEYS:
            self.dpi = self.add("품질", QComboBox())
            for lb, v in DPI_CHOICES:
                self.dpi.addItem(lb, v)
            self.dpi.setCurrentIndex(max(0, self.dpi.findData(sv["dpi"])))
            box = QWidget(); hl = QHBoxLayout(box); hl.setContentsMargins(0, 0, 0, 0)
            self.mode_group = QButtonGroup(self)
            for k, lb in IMAGE_MODES:
                rb = QRadioButton(lb); self.modes[k] = rb; self.mode_group.addButton(rb); hl.addWidget(rb)
            self.modes.get(sv["mode"], self.modes["pages"]).setChecked(True)
            self.add("설정", box)
            for rb in self.modes.values():
                rb.toggled.connect(self._sync)
            self._sync()
        elif fmt == "pptx":
            self.pptx_dpi = self.add("이미지 해상도", QComboBox())
            for d in PPTX_DPIS:
                self.pptx_dpi.addItem(f"{d} dpi", d)
            self.pptx_dpi.setCurrentIndex(max(0, self.pptx_dpi.findData(sv["pptx_dpi"])))
        elif fmt == "docx":
            self.ocr = self.add("OCR 모드", QComboBox())
            for k, lb in OCR_MODES:
                self.ocr.addItem(lb, k)
            self.ocr.setCurrentIndex(max(0, self.ocr.findData(sv["ocr"])))
        elif fmt == "pdfa":
            self.std = self.add("PDF/A 표준", QComboBox()); self.std.addItems(PDFA_STANDARDS)

    def _sync(self):
        if self.dpi is not None:
            self.dpi.setEnabled(not self.modes["extract"].isChecked())

    def accept(self):
        try:
            self.pages_edit.pages()
        except ValueError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e)); self.pages_edit.focus_error(); return
        super().accept()

    def pages(self) -> list[int]:
        return self.pages_edit.pages()

    def saved(self) -> dict:
        """쪽 범위를 뺀, 다시 열 때 기억할 값."""
        d: dict = {}
        if self.dpi is not None:
            d["dpi"] = self.dpi.currentData(); d["mode"] = next(k for k, rb in self.modes.items() if rb.isChecked())
        if self.pptx_dpi is not None:
            d["pptx_dpi"] = self.pptx_dpi.currentData()
        if self.ocr is not None:
            d["ocr"] = self.ocr.currentData()
        if self.std is not None:
            d["pdfa"] = self.std.currentText()
        return d


class ConvertDialog(FormDialog):
    """내보내기: 추출 형식 + [설정], 파일 이름, 저장 위치."""

    def __init__(self, parent, total: int, current: int, default_fmt: str, doc_name: str, doc_path: str | None,
                 store: dict | None = None):
        super().__init__("변환", parent, "변환")
        self.total, self.current = total, current
        self.store = store if store is not None else {}   # settings["convert"]
        self.stem = os.path.splitext(doc_name or "문서")[0] or "문서"
        self.pages: list[int] | None = None               # None = 모든 쪽
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        self.fmt = QComboBox()
        for lb, k, _ in FORMATS:
            self.fmt.addItem(lb, k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(default_fmt)))
        self.settings_btn = QPushButton("설정"); self.settings_btn.setAutoDefault(False)
        hl.addWidget(self.fmt, 1); hl.addWidget(self.settings_btn)
        self.add("추출 형식", row)
        self.name = self.add("파일 이름", QLineEdit())
        frow = QWidget(); fl = QHBoxLayout(frow); fl.setContentsMargins(0, 0, 0, 0)
        self.folder = QLineEdit(default_folder(doc_path)); b = QPushButton("찾기"); b.setAutoDefault(False)
        b.clicked.connect(self._pick); fl.addWidget(self.folder, 1); fl.addWidget(b)
        self.add("저장 위치", frow)
        self._name_touched = False
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.folder.textChanged.connect(lambda _t: self._default_name())
        self.settings_btn.clicked.connect(self.open_settings)
        self._default_name()

    # ── 상태 ──
    def key(self) -> str:
        return self.fmt.currentData()

    def saved(self) -> dict:
        return {**DEFAULTS, **self.store.get(self.key(), {})}

    def mode(self) -> str:
        """이미지 형식에서만 의미 있다: pages · merge · extract."""
        return self.saved()["mode"] if self.key() in IMAGE_KEYS else "pages"

    def _multi(self) -> bool:
        return self.key() in IMAGE_KEYS and self.mode() == "pages"

    def _unique(self, folder: str, stem: str) -> str:
        extract = self.mode() == "extract"
        return unique_stem(folder, stem, "" if extract else ext_of(self.key()), multi=self._multi() or extract)

    def _default_name(self):
        if self._name_touched:
            return
        self.name.setText(self._unique(self.folder.text(), self.stem))

    def _fmt_changed(self):
        self.pages = None
        self._name_touched = False
        self._default_name()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "저장 위치", self.folder.text())
        if d:
            self.folder.setText(d)

    def open_settings(self):
        key = self.key()
        dlg = ConvertSettings(self, key, self.total, self.current, self.pages, self.store.get(key))
        if dlg.exec():
            pages = dlg.pages()
            self.pages = None if pages == list(range(self.total)) else pages
            self.store[key] = dlg.saved()
            if not self._name_touched:
                self._default_name()

    def accept(self):
        folder, name = self.folder.text().strip(), self.name.text().strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            QMessageBox.warning(self, "변환", "파일 이름을 확인해 주세요"); self.name.setFocus(); return
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "변환", "저장 위치를 확인해 주세요"); self.folder.setFocus(); return
        super().accept()

    def values(self) -> dict:
        """fmt · folder · stem(겹치면 _숫자) · ext · out(파일 하나일 때의 경로) · pages(None=전체) · 형식 설정."""
        key, folder = self.key(), self.folder.text().strip()
        ext = ext_of(key)
        stem = self.name.text().strip()
        if stem.lower().endswith(ext):
            stem = stem[:-len(ext)]
        stem = self._unique(folder, stem)
        return {"fmt": key, "folder": folder, "stem": stem, "ext": ext, "out": os.path.join(folder, stem + ext),
                "pages": self.pages, **self.saved()}


def success_box(parent, path: str, title: str = "변환 성공") -> None:
    """끝났다는 알림: [확인] 과 [폴더 열기]. path 가 폴더면 그 폴더를, 파일이면 탐색기에서 그 파일을 고른다."""
    box = QMessageBox(QMessageBox.Icon.Information, title, f"저장했습니다.\n{path}", QMessageBox.StandardButton.Ok, parent)
    box.button(QMessageBox.StandardButton.Ok).setText("확인")
    opener = box.addButton("폴더 열기", QMessageBox.ButtonRole.ActionRole)
    box.exec()
    if box.clickedButton() is opener:
        open_in_explorer(path)


def open_in_explorer(path: str) -> None:
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.startfile(path if os.path.isdir(path) else os.path.dirname(path))
