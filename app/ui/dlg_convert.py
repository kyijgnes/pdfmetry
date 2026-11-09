"""내보내기 창: 형식 + [설정] 단추 + 파일 이름 + 저장 위치(「원본 파일 위치」/「폴더 지정」, 기억).
형식마다 설정 창(쪽 범위와 형식 칸)이 따로 있고, 쪽 범위를 뺀 설정은 형식별로 기억한다(settings["convert"][형식]).
형식 칸은 FormatFields 한 벌이라 「설정 변환」 모음 창(dlg_convert_settings)도 같은 칸·같은 저장을 쓴다.

이름이 겹치면 기본 이름을 `이름_1`, `이름_2` 로 비켜 간다(unique_stem). 다 끝나면 convert_done_box 로 알린다."""
from __future__ import annotations

import glob
import os
import subprocess

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from .dialogs import ColorButton, FormDialog, PageRangeEdit, from_rgb01, to_rgb01

# (이름, 키, 확장자). 이미지 키는 확장자와 같고 pdfa·imgpdf 는 .pdf. 한글이 맨 위
FORMATS = [("한글 (.hwpx)", "hwpx", ".hwpx"), ("Word (.docx)", "docx", ".docx"), ("Excel (.xlsx)", "xlsx", ".xlsx"),
           ("PowerPoint (.pptx)", "pptx", ".pptx"), ("Epub (.epub)", "epub", ".epub"), ("JPG (.jpg)", "jpg", ".jpg"),
           ("JPEG (.jpeg)", "jpeg", ".jpeg"), ("PNG (.png)", "png", ".png"), ("BMP (.bmp)", "bmp", ".bmp"),
           ("GIF (.gif)", "gif", ".gif"), ("TIFF (.tiff)", "tiff", ".tiff"), ("TXT (.txt)", "txt", ".txt"),
           ("RTF (.rtf)", "rtf", ".rtf"), ("HTML (.html)", "html", ".html"), ("XML (.xml)", "xml", ".xml"),
           ("이미지 기반 PDF (.pdf)", "imgpdf", ".pdf"), ("PDF/A-2b (.pdf)", "pdfa", ".pdf")]
IMAGE_KEYS = ("jpg", "jpeg", "png", "bmp", "gif", "tiff")
OCR_KEYS = ("docx", "xlsx", "pptx", "txt", "html", "rtf", "epub")   # 「OCR 인식」·「언어 인식」 칸이 있는 형식
DPI_CHOICES = [("원본 DPI 유지", 0), ("72 dpi", 72), ("96 dpi", 96), ("120 dpi", 120), ("150 dpi", 150),
               ("300 dpi", 300), ("450 dpi", 450), ("600 dpi", 600)]
PPTX_DPIS = [100, 150, 200, 300, 600]
PPTX_MODES = [("text", "글 상자로"), ("image", "쪽 그림으로")]
IMAGE_MODES = [("pages", "쪽마다 한 장"), ("merge", "한 장으로 합치기"), ("extract", "PDF 속 이미지 내보내기")]
OCR_MODES = [("all", "모든 파일"), ("scan", "스캔된 PDF"), ("off", "OCR 인식 비활성화")]
DEFAULTS = {"dpi": 0, "mode": "pages", "ocr": "scan", "pptx_dpi": 150, "pptx_mode": "text", "one_sheet": False,
            "zoom": 100, "frame_w": 1, "frame_color": "#FFFFFF", "cover": ""}
OUT_SUB = os.path.join("PDFmetry", "변환")
MAX_PATH = 259

# 저장 위치 오류 문구
ERR_INVALID = "유효하지 않은 경로"
ERR_FOLDER = "폴더 경로가 잘못되었습니다. 다시 시도해주세요."
ERR_PATH = "경로가 잘못되었습니다. 확인하십시오."
ERR_READONLY = "폴더 경로가 읽기 전용입니다. 확인하십시오."
ERR_LONG = "오류! 경로가 너무 깁니다."
LANG_WARN = "세개 이상의 언어는 인식 속도를 늦출 수 있습니다."
LANG_NONE = "OCR을 수행할 언어를 선택하십시오."


def ext_of(key: str) -> str:
    return next((e for _, k, e in FORMATS if k == key), "." + key)


def label_of(key: str) -> str:
    return next((n for n, k, _ in FORMATS if k == key), key)


def default_folder(path: str | None) -> str:
    """원본이 있는 폴더, 새 문서면 내 문서."""
    if path and os.path.isdir(os.path.dirname(path)):
        return os.path.normpath(os.path.dirname(path))
    return os.path.normpath(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or os.path.expanduser("~"))


def default_custom_folder() -> str:
    """「폴더 지정」 처음 값: 바탕 화면\\PDFmetry\\변환."""
    desk = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or os.path.expanduser("~")
    return os.path.normpath(os.path.join(desk, OUT_SUB))


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


def check_folder(folder: str, create: bool = False) -> str | None:
    """저장 위치 검사. 괜찮으면 None, 아니면 오류 문구. create 면 없는 폴더를 만든다(「폴더 지정」 기본 폴더)."""
    folder = (folder or "").strip()
    if not folder:
        return ERR_INVALID
    if len(folder) > MAX_PATH - 12:
        return ERR_LONG
    drive, rest = os.path.splitdrive(folder)
    if not drive or any(c in rest for c in '<>:"|?*'):
        return ERR_PATH
    if not os.path.isdir(folder):
        if not create:
            return ERR_FOLDER
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return ERR_FOLDER
    if not os.access(folder, os.W_OK):
        return ERR_READONLY
    return None


# ── OCR 언어 ──
_LANGS: list[str] | None = None


def ocr_languages() -> list[str]:
    """이 PC 윈도우 OCR 이 되는 언어 태그(한 번만 묻는다). 못 물으면 빈 목록."""
    global _LANGS
    if _LANGS is None:
        try:
            from ..engine import ocr as O
            _LANGS = O.available_languages()
        except Exception:  # noqa: BLE001
            _LANGS = []
    return list(_LANGS)


def lang_label(tag: str) -> str:
    from PySide6.QtCore import QLocale
    name = QLocale(tag).nativeLanguageName()
    return f"{name} ({tag})" if name else tag


def default_langs(avail: list[str]) -> list[str]:
    """설치된 것 가운데 영어·한국어."""
    pick = [t for t in avail if t.lower().split("-")[0] in ("en", "ko")]
    return pick or avail[:1]


def current_langs(prefs: dict | None, avail: list[str]) -> list[str]:
    """OCR 공용 값(ocr_langs). 아직 없으면 옛 ocr_lang 하나, 그것도 없으면 영어·한국어."""
    prefs = prefs or {}
    v = prefs.get("ocr_langs")
    if isinstance(v, list) and v:
        return [str(x) for x in v]
    if prefs.get("ocr_lang"):
        return [str(prefs["ocr_lang"])]
    return default_langs(avail)


class LangList(QWidget):
    """「언어 인식」 체크 목록 + 셋 넘게 고르면 경고 한 줄."""

    def __init__(self, avail: list[str], chosen: list[str]):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        self.avail = list(avail)
        for t in chosen:   # 이 PC 에 없는 값도 보이게(지우지 않는다)
            if t not in self.avail:
                self.avail.append(t)
        self.list = QListWidget(); self.list.setMaximumHeight(96)
        for t in self.avail:
            it = QListWidgetItem(lang_label(t)); it.setData(Qt.ItemDataRole.UserRole, t)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if t in chosen else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        self.warn = QLabel(LANG_WARN); self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        if not self.avail:
            self.list.setVisible(False)
            lay.addWidget(QLabel("설치된 OCR 언어 없음"))
        self.list.itemChanged.connect(lambda _i: self._sync())
        self._sync()

    def langs(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def set_langs(self, chosen: list[str]):
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.CheckState.Checked if it.data(Qt.ItemDataRole.UserRole) in chosen else Qt.CheckState.Unchecked)

    def _sync(self):
        self.warn.setVisible(len(self.langs()) > 3)


def _hex(rgb01) -> str:
    return from_rgb01(rgb01).name().upper()


def _rgb01(hexs: str):
    from PySide6.QtGui import QColor
    c = QColor(hexs or "#FFFFFF")
    return to_rgb01(c if c.isValid() else QColor("#FFFFFF"))


class FormatFields:
    """한 형식의 설정 칸(쪽 범위 빼고)을 form 에 넣는다. 변환 창 설정과 「설정 변환」 모음 창이 같이 쓴다."""

    def __init__(self, form: QFormLayout, owner: QWidget, fmt: str, saved: dict | None, prefs: dict | None = None,
                 langs_fn=ocr_languages):
        self.form, self.owner, self.fmt, self.prefs = form, owner, fmt, prefs
        sv = {**DEFAULTS, **(saved or {})}
        self.dpi = self.mode_group = self.ocr = self.pptx_dpi = self.pptx_mode = self.one_sheet = None
        self.zoom = self.frame_w = self.frame_color = self.cover_on = self.cover_path = self.lang = None
        self.modes: dict[str, QRadioButton] = {}
        if fmt in IMAGE_KEYS:
            self.dpi = self._add("품질", QComboBox())
            for lb, v in DPI_CHOICES:
                self.dpi.addItem(lb, v)
            box = QWidget(); vl = QVBoxLayout(box); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(2)
            self.mode_group = QButtonGroup(owner)
            for k, lb in IMAGE_MODES:
                rb = QRadioButton(lb); self.modes[k] = rb; self.mode_group.addButton(rb); vl.addWidget(rb)
            self._add("이미지 설정", box)
            self.zoom = self._add("확대/축소", QSpinBox()); self.zoom.setRange(1, 200); self.zoom.setSuffix(" %")
            self.frame_w = self._add("테두리 굵기", QSpinBox()); self.frame_w.setRange(0, 20); self.frame_w.setSuffix(" px")
            self.frame_color = self._add("테두리 색", ColorButton((1, 1, 1)))
            for rb in self.modes.values():
                rb.toggled.connect(self._sync)
        elif fmt == "pptx":
            self.pptx_mode = self._add("변환 방식", QComboBox())
            for k, lb in PPTX_MODES:
                self.pptx_mode.addItem(lb, k)
            self.pptx_dpi = self._add("이미지 해상도", QComboBox())
            for d in PPTX_DPIS:
                self.pptx_dpi.addItem(f"{d} dpi", d)
        elif fmt == "xlsx":
            self.one_sheet = self._add("설정 변환", QCheckBox("모든 페이지를 하나의 Excel 시트로 합치기"))
        elif fmt == "epub":
            box = QWidget(); vl = QVBoxLayout(box); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(2)
            g = QButtonGroup(owner)
            self.cover_off = QRadioButton("책 표지 없음"); self.cover_on = QRadioButton("책 표지 추가:")
            g.addButton(self.cover_off); g.addButton(self.cover_on)
            row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
            self.cover_path = QLineEdit(); self.cover_btn = QPushButton("찾기"); self.cover_btn.setAutoDefault(False)
            self.cover_btn.clicked.connect(self._pick_cover)
            hl.addWidget(self.cover_on); hl.addWidget(self.cover_path, 1); hl.addWidget(self.cover_btn)
            vl.addWidget(self.cover_off); vl.addWidget(row)
            self._add("책 표지", box)
            self.cover_on.toggled.connect(self._sync)
        if fmt in OCR_KEYS:
            self.ocr = self._add("OCR 인식", QComboBox())
            for k, lb in OCR_MODES:
                self.ocr.addItem(lb, k)
            avail = langs_fn() if langs_fn else []
            self.lang = self._add("언어 인식", LangList(avail, current_langs(prefs, avail)))
            self.ocr.currentIndexChanged.connect(self._sync)
        self.load(sv)

    def _add(self, label: str, w):
        self.form.addRow(label, w)
        return w

    def load(self, sv: dict):
        sv = {**DEFAULTS, **sv}
        if self.dpi is not None:
            self.dpi.setCurrentIndex(max(0, self.dpi.findData(sv["dpi"])))
            self.modes.get(sv["mode"], self.modes["pages"]).setChecked(True)
            self.zoom.setValue(int(sv["zoom"])); self.frame_w.setValue(int(sv["frame_w"]))
            self.frame_color.color = _rgb01(sv["frame_color"]); self.frame_color._paint()
        if self.pptx_dpi is not None:
            self.pptx_dpi.setCurrentIndex(max(0, self.pptx_dpi.findData(sv["pptx_dpi"])))
            self.pptx_mode.setCurrentIndex(max(0, self.pptx_mode.findData(sv["pptx_mode"])))
        if self.one_sheet is not None:
            self.one_sheet.setChecked(bool(sv["one_sheet"]))
        if self.cover_on is not None:
            self.cover_path.setText(sv["cover"] or "")
            (self.cover_on if sv["cover"] else self.cover_off).setChecked(True)
        if self.ocr is not None:
            self.ocr.setCurrentIndex(max(0, self.ocr.findData(sv["ocr"])))
        self._sync()

    def reset(self):
        """[기본]: 형식 칸을 처음 값으로(언어는 영어·한국어)."""
        self.load(DEFAULTS)
        if self.lang is not None:
            self.lang.set_langs(default_langs(self.lang.avail))

    def _sync(self, *_a):
        if self.dpi is not None:
            merge = self.modes["merge"].isChecked()
            self.dpi.setEnabled(not self.modes["extract"].isChecked())
            for w in (self.zoom, self.frame_w, self.frame_color):
                self.form.setRowVisible(w, merge)
        if self.cover_on is not None:
            on = self.cover_on.isChecked()
            self.cover_path.setEnabled(on); self.cover_btn.setEnabled(on)
        if self.lang is not None:
            self.lang.setEnabled(self.ocr.currentData() != "off")

    def _pick_cover(self):
        p, _ = QFileDialog.getOpenFileName(self.owner, "책 표지", os.path.dirname(self.cover_path.text()),
                                           "그림 (*.jpg *.jpeg *.png *.gif *.bmp)")
        if p:
            self.cover_path.setText(os.path.normpath(p)); self.cover_on.setChecked(True)

    def error(self) -> tuple[str, QWidget] | None:
        """확인 전에 막을 것: (문구, 초점 둘 칸)."""
        if self.cover_on is not None and self.cover_on.isChecked() and not os.path.isfile(self.cover_path.text().strip()):
            return "표지 그림 파일을 골라 주세요", self.cover_path
        if self.lang is not None and self.ocr.currentData() != "off" and self.lang.avail and not self.lang.langs():
            return LANG_NONE, self.lang.list
        return None

    def values(self) -> dict:
        d: dict = {}
        if self.dpi is not None:
            d["dpi"] = self.dpi.currentData(); d["mode"] = next(k for k, rb in self.modes.items() if rb.isChecked())
            d["zoom"] = self.zoom.value(); d["frame_w"] = self.frame_w.value(); d["frame_color"] = _hex(self.frame_color.color)
        if self.pptx_dpi is not None:
            d["pptx_dpi"] = self.pptx_dpi.currentData()
            d["pptx_mode"] = self.pptx_mode.currentData()
        if self.one_sheet is not None:
            d["one_sheet"] = self.one_sheet.isChecked()
        if self.cover_on is not None:
            d["cover"] = self.cover_path.text().strip() if self.cover_on.isChecked() else ""
        if self.ocr is not None:
            d["ocr"] = self.ocr.currentData()
        return d

    def commit_langs(self):
        """언어 칸은 OCR 공용 값이라 prefs["ocr_langs"] 에 바로 쓴다."""
        if self.lang is not None and self.prefs is not None and self.lang.avail and self.lang.langs():
            self.prefs["ocr_langs"] = self.lang.langs()


class ConvertSettings(FormDialog):
    """형식별 설정. 쪽 범위는 어느 형식에나 있고, 나머지는 형식마다 다르다(FormatFields)."""

    def __init__(self, parent, fmt: str, total: int, current: int, pages: list[int] | None, saved: dict | None,
                 prefs: dict | None = None, langs_fn=ocr_languages):
        super().__init__(f"{label_of(fmt)} 설정", parent, "확인")
        self.fmt = fmt
        self.pages_edit = self.add("페이지 범위 변환", PageRangeEdit(total, current))
        if pages is not None:
            self.pages_edit.set_pages(pages)
        self.fields = FormatFields(self.form, self, fmt, saved, prefs, langs_fn)
        reset = self.bb.addButton("기본", QDialogButtonBox.ButtonRole.ResetRole)
        reset.setAutoDefault(False)
        reset.clicked.connect(self._reset)
        # 옛 이름(시험·호출부)
        self.dpi, self.modes, self.ocr, self.pptx_dpi = self.fields.dpi, self.fields.modes, self.fields.ocr, self.fields.pptx_dpi

    def _reset(self):
        self.fields.reset()
        self.pages_edit.set_pages(list(range(self.pages_edit.total)))

    def accept(self):
        try:
            self.pages_edit.pages()
        except ValueError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e)); self.pages_edit.focus_error(); return
        err = self.fields.error()
        if err:
            QMessageBox.warning(self, self.windowTitle(), err[0]); err[1].setFocus(); return
        self.fields.commit_langs()
        super().accept()

    def pages(self) -> list[int]:
        return self.pages_edit.pages()

    def saved(self) -> dict:
        """쪽 범위를 뺀, 다시 열 때 기억할 값."""
        return self.fields.values()


class ConvertDialog(FormDialog):
    """내보내기: 추출 형식 + [설정], 파일 이름, 저장 위치(콤보 + 경로, 기억)."""

    def __init__(self, parent, total: int, current: int, default_fmt: str, doc_name: str, doc_path: str | None,
                 store: dict | None = None, out_store: dict | None = None, prefs: dict | None = None,
                 langs_fn=ocr_languages):
        super().__init__("변환", parent, "변환")
        self.total, self.current = total, current
        self.store = store if store is not None else {}           # settings["convert"]
        self.out_store = out_store if out_store is not None else {}   # settings["convert_out"]
        self.prefs, self.langs_fn = prefs, langs_fn
        self.doc_path = doc_path
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
        # 저장 위치: 압축·병합·일괄과 같은 OutputPanel(같은 {"custom", "folder"} 꼴로 convert_out 에 기억)
        from .file_list import OutputPanel
        self.out = OutputPanel(self, self.out_store, "변환", origin=default_folder(doc_path), vertical=True)
        self.out.label.hide()
        self.add("저장 위치", self.out)
        self.where, self.folder, self.browse = self.out.combo, self.out.path, self.out.browse
        self._name_touched = False
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        self.name.textChanged.connect(self._sync_ok)
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.where.currentIndexChanged.connect(self._where_changed)
        self.folder.textChanged.connect(self._folder_edited)
        self.settings_btn.clicked.connect(self.open_settings)
        self._default_name()
        self._sync_ok()

    # ── 상태 ──
    def key(self) -> str:
        return self.fmt.currentData()

    def custom(self) -> bool:
        return self.where.currentIndex() == 1

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

    def _sync_ok(self, *_a):
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.name.text().strip()))

    def _fmt_changed(self):
        self.pages = None
        self._name_touched = False
        self._default_name()

    def _where_changed(self, *_a):
        self._default_name()   # 경로 칸 바꾸기는 OutputPanel 이 먼저 했다

    def _folder_edited(self, _text: str):
        self._default_name()

    def open_settings(self):
        key = self.key()
        dlg = ConvertSettings(self, key, self.total, self.current, self.pages, self.store.get(key), self.prefs, self.langs_fn)
        if dlg.exec():
            pages = dlg.pages()
            self.pages = None if pages == list(range(self.total)) else pages
            self.store[key] = {**self.store.get(key, {}), **dlg.saved()}
            if not self._name_touched:
                self._default_name()

    def accept(self):
        folder, name = self.folder.text().strip(), self.name.text().strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            QMessageBox.warning(self, "변환", ERR_PATH); self.name.setFocus(); return
        err = check_folder(folder, create=self.custom() and os.path.normcase(folder) == os.path.normcase(default_custom_folder()))
        if not err and len(os.path.join(folder, name + ext_of(self.key()))) > MAX_PATH:
            err = ERR_LONG
        if err:
            QMessageBox.warning(self, "변환", err); self.folder.setFocus(); return
        self.out_store["custom"] = self.custom()
        if self.custom():   # 지정 폴더는 「폴더 지정」 으로 쓴 때만 기억
            self.out_store["folder"] = folder
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


def done_text(label: str, files: list[str], folder: str) -> str:
    if len(files) == 1:
        return f"{label}(으)로 변환되었습니다.\n{files[0]}"
    return f"{label}(으)로 변환되었습니다. 파일은 '{folder}' 폴더에 있습니다."


def convert_done_box(parent, label: str, files: list[str], folder: str, open_file=None, exec_: bool = True) -> QMessageBox:
    """변환 성공 창: [파일 열기](파일 하나일 때) · [폴더 열기] · [확인]. 결과를 저절로 열지는 않는다.
    open_file(path) 를 주면 [파일 열기] 가 그것을 부른다(PDF 결과는 앱 탭으로)."""
    box = QMessageBox(QMessageBox.Icon.Information, "변환 성공", done_text(label, files, folder),
                      QMessageBox.StandardButton.Ok, parent)
    box.button(QMessageBox.StandardButton.Ok).setText("확인")
    file_btn = box.addButton("파일 열기", QMessageBox.ButtonRole.ActionRole) if len(files) == 1 else None
    folder_btn = box.addButton("폴더 열기", QMessageBox.ButtonRole.ActionRole)
    box.file_btn, box.folder_btn = file_btn, folder_btn
    if not exec_:
        return box
    box.exec()
    if file_btn is not None and box.clickedButton() is file_btn:
        (open_file or os.startfile)(files[0])
    elif box.clickedButton() is folder_btn:
        open_in_explorer(files[0] if len(files) == 1 else folder)
    return box


def open_in_explorer(path: str) -> None:
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.startfile(path if os.path.isdir(path) else os.path.dirname(path))
