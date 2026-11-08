"""일괄 편집 창: 왼쪽 작업 목록(설명 한 줄씩) · 오른쪽 작업 칸 + 공용 파일 목록 + 저장 위치 · [적용]/[취소].

작업은 열두 가지(TASKS). 파일마다 상태 칸에 「처리 중」 · 「완료」 · 실패 까닭 · 「취소됨」 이 남는다.
도는 동안 [적용] 이 [취소] 가 되고, 누르면 지금 파일까지만 하고 남은 파일은 「취소됨」.
저장 위치는 변환이 convert_out, 압축이 compress_out, 나머지가 batch_out(「원본 파일 위치」 면 파일마다 자기 폴더).
결과 이름이 겹치면 「_1」 로 비킨다(엔진 tools._out_name). 마지막 작업·변환 형식은 prefs 의 batch_task · batch_fmt.
꾸미기 넷(베이츠 · 워터마크 · 배경 · 머리글)은 기본 제공·저장한 템플릿 가운데 고르고, [새 템플릿] 으로 그 자리에서 만든다.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QEventLoop, QThread, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..engine import tools as T
from . import fitwidth, theme
from .file_list import FileListPanel, OutputPanel, open_in_explorer

# (열쇠, 이름, 설명 한 줄)
TASKS = [
    ("convert", "변환", "PDF를 Word·Excel·이미지 등으로 변환"),
    ("create", "생성", "이미지·오피스·한글 파일을 PDF로 생성"),
    ("compress", "압축", "PDF 파일 크기 줄이기"),
    ("ocr", "OCR", "스캔 PDF에 찾기·복사되는 글자층 넣기"),
    ("bates", "베이츠", "파일마다 베이츠 번호 넣기"),
    ("watermark", "워터마크", "파일마다 워터마크 넣기"),
    ("encrypt", "보안", "파일마다 열기·권한 암호 걸기"),
    ("export_data", "데이터 내보내기", "양식 값을 CSV 한 장으로 모으기"),
    ("print", "인쇄", "파일을 차례로 인쇄"),
    ("blank", "공백 페이지 삭제", "글자·그림이 없는 빈 쪽 지우기"),
    ("background", "배경", "파일마다 배경 넣기"),
    ("header_footer", "머리글 & 바닥글", "파일마다 머리글·바닥글 넣기"),
]
TASK_NAMES = {k: n for k, n, _ in TASKS}
DECO_TASKS = ("bates", "watermark", "background", "header_footer")
PDF_FILTER = "PDF 파일 (*.pdf)"
DATA_NAME = "양식 데이터.csv"
OUT_STORES = {"convert": ("convert_out", "변환"), "compress": ("compress_out", "압축")}
BATCH_OUT = ("batch_out", "일괄")
NO_SECURITY = "보안 설정을 먼저 하세요"
NO_TEMPLATE = "넣을 템플릿을 고르세요"
NO_NAME = "파일 이름을 입력하세요"


class _Job(QThread):
    """엔진 batch 를 따로 돌린다. rowStarted(줄) · rowDone(줄, 성공, 상태 글, 결과 경로)."""
    rowStarted = Signal(int)
    rowDone = Signal(int, bool, str, str)

    def __init__(self, job: dict):
        super().__init__()
        self.job = job
        self.stop = threading.Event()
        self.results: list[dict] = []

    def _row(self, k: int, rec: dict | None):
        if rec is None:
            self.rowStarted.emit(k)
        elif rec.get("cancelled"):
            self.rowDone.emit(k, False, T.CANCELLED, "")
        else:
            self.rowDone.emit(k, bool(rec.get("ok")), "완료" if rec.get("ok") else (rec.get("error") or "실패"),
                              rec.get("output") or "")

    def run(self):
        try:
            self.results = T.batch([self.job], stop=self.stop, on_row=self._row)
        except Exception as e:  # noqa: BLE001
            self.results = [{"ok": False, "error": str(e)}]


class BatchDialog(QDialog):
    """win: 앱 창(설정·결과 열기·인쇄). task: 처음 고를 작업(None 이면 지난번). paths/passwords: 처음 넣을 파일."""

    def __init__(self, win, task: str | None = None, paths: list[str] | None = None,
                 passwords: list[str | None] | None = None, langs_fn=None):
        super().__init__(win)
        self.win = win
        self.prefs = win.prefs if win is not None else {}
        self.settings = win.settings if win is not None else {}
        self._langs_fn = langs_fn
        self.running: _Job | None = None
        self.results: list[dict] = []
        self.security: dict | None = None
        self.setWindowTitle("일괄 편집"); self.resize(980, 600)
        root = QHBoxLayout(self)
        self.tasks = QListWidget()
        for k, name, desc in TASKS:
            it = QListWidgetItem(f"{name}\n{desc}"); it.setData(Qt.ItemDataRole.UserRole, k)
            self.tasks.addItem(it)
        self.tasks.setSpacing(2)
        fitwidth.fit_list(self.tasks, [t for _k, n, d in TASKS for t in (n, d)], floor=230)
        root.addWidget(self.tasks)
        right = QVBoxLayout(); root.addLayout(right, 1)
        self.title = QLabel(); self.title.setStyleSheet("font-weight:600;")
        right.addWidget(self.title)
        self.desc = QLabel(); self.desc.setStyleSheet(f"color:{theme.TEXT_DIM};")
        right.addWidget(self.desc)
        self.stack = QStackedWidget(); right.addWidget(self.stack)
        self.pages: dict[str, QWidget] = {}
        self.deco_combos: dict[str, QComboBox] = {}
        for k, _n, _d in TASKS:
            w = QWidget(); f = QFormLayout(w); f.setContentsMargins(0, 0, 0, 0)
            self.pages[k] = w; self.stack.addWidget(w)
            if k in DECO_TASKS:
                self._build_deco(f, k)
            elif hasattr(self, f"_build_{k}"):
                getattr(self, f"_build_{k}")(f)
        self.files = FileListPanel(self, "일괄 편집", PDF_FILTER, paths=paths, passwords=passwords)
        right.addWidget(self.files, 1)
        self.outs: dict[str, OutputPanel] = {}
        for key, sub in (*OUT_STORES.values(), BATCH_OUT):
            p = OutputPanel(self, self.settings.setdefault(key, {}), sub)
            self.outs[key] = p; right.addWidget(p)
        bottom = QHBoxLayout(); bottom.addStretch()
        self.apply_btn = QPushButton("적용"); self.apply_btn.setDefault(True); self.apply_btn.clicked.connect(self._apply_or_cancel)
        bottom.addWidget(self.apply_btn)
        right.addLayout(bottom)
        self.files.changed.connect(self._sync_apply)
        self.tasks.currentRowChanged.connect(self._task_changed)
        start = task if task in TASK_NAMES else self.prefs.get("batch_task", "convert")
        keys = [k for k, _, _ in TASKS]
        self.tasks.setCurrentRow(keys.index(start) if start in keys else 0)

    # ── 작업 칸 ──
    def _build_convert(self, f: QFormLayout):
        from .dlg_convert import FORMATS
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        self.fmt = QComboBox()
        for lb, k, _ in FORMATS:
            self.fmt.addItem(lb, k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(self.prefs.get("batch_fmt", "docx"))))
        self.fmt_btn = QPushButton("설정"); self.fmt_btn.setAutoDefault(False); self.fmt_btn.clicked.connect(self.convert_settings)
        hl.addWidget(self.fmt, 1); hl.addWidget(self.fmt_btn)
        f.addRow("추출 형식", row)
        self.fmt.currentIndexChanged.connect(self._sync_fmt)
        self._sync_fmt()

    def _settings_key(self, fmt: str) -> str | None:
        from .dlg_convert import IMAGE_KEYS
        from .dlg_convert_settings import PAGES
        if fmt in IMAGE_KEYS:
            return "png"
        return fmt if fmt in {k for _, k in PAGES} else None

    def _sync_fmt(self, *_a):
        self.fmt_btn.setEnabled(self._settings_key(self.fmt.currentData()) is not None)

    def convert_settings(self):
        from .dlg_convert_settings import ConvertSettingsDialog
        from .dlg_convert import ocr_languages
        key = self._settings_key(self.fmt.currentData())
        dlg = ConvertSettingsDialog(self, self.settings.setdefault("convert", {}), self.prefs, start=key,
                                    langs_fn=self._langs_fn or ocr_languages)
        if dlg.exec():
            self._save()

    def _build_compress(self, f: QFormLayout):
        from .dlg_compress import LEVEL_NAMES
        box = QWidget(); hl = QHBoxLayout(box); hl.setContentsMargins(0, 0, 0, 0)
        self.level_group = QButtonGroup(self); self.levels: dict[str, QRadioButton] = {}
        for lv in T.COMPRESS_LEVELS:
            rb = QRadioButton(LEVEL_NAMES[lv]); self.levels[lv] = rb; self.level_group.addButton(rb); hl.addWidget(rb)
        self.levels.get(self.prefs.get("compress_level"), self.levels["high"]).setChecked(True)
        self.custom_btn = QPushButton("사용자 지정 설정"); self.custom_btn.setAutoDefault(False)
        self.custom_btn.clicked.connect(self.edit_custom)
        hl.addWidget(self.custom_btn); hl.addStretch()
        f.addRow("압축 옵션", box)

    def level(self) -> str:
        return next(k for k, rb in self.levels.items() if rb.isChecked())

    def edit_custom(self):
        from .dlg_compress import CustomDialog, custom_for
        lv = self.level()
        dlg = CustomDialog(self, custom_for(self.prefs, lv))
        if dlg.exec():
            self.prefs.setdefault("compress_custom", {})[lv] = dlg.values()
            self._save()

    def _build_ocr(self, f: QFormLayout):
        self.ocr_form = f
        self.ocr_langs = None   # 처음 볼 때 짓는다(언어 목록을 읽는 데 PowerShell 을 부른다)
        self.ocr_force = QCheckBox("글자가 이미 있는 쪽도 다시 인식")
        f.addRow("", self.ocr_force)

    def _ensure_ocr(self):
        if self.ocr_langs is not None:
            return
        from .dlg_convert import LangList, current_langs, ocr_languages
        avail = (self._langs_fn or ocr_languages)()
        self.ocr_langs = LangList(avail, current_langs(self.prefs, avail))
        self.ocr_form.insertRow(0, "언어 인식", self.ocr_langs)

    def _build_deco(self, f: QFormLayout, kind: str):
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox(); combo.setMinimumWidth(240)
        new = QPushButton("새 템플릿"); new.setAutoDefault(False); new.clicked.connect(lambda _=False, k=kind: self.new_template(k))
        hl.addWidget(combo, 1); hl.addWidget(new)
        f.addRow("템플릿", row)
        self.deco_combos[kind] = combo
        self._fill_templates(kind)

    def _fill_templates(self, kind: str, pick: str | None = None):
        from ..engine import deco_store as S
        combo = self.deco_combos[kind]
        combo.clear()
        for t in S.builtins(kind) + S.templates(kind):
            combo.addItem(S.display_name(t), t)
        if pick:
            for i in range(combo.count()):
                if combo.itemData(i)["id"] == pick:
                    combo.setCurrentIndex(i)

    def new_template(self, kind: str):
        """꾸미기 창을 템플릿 저장 꼴로 연다. 미리보기는 목록 첫 PDF(없으면 빈 A4)."""
        from ..engine.document import Document
        from .dlg_deco import dialog_class
        doc = None
        first = next((p for p in self.files.paths() if p.lower().endswith(".pdf")), None)
        pw = self.files.passwords()[0] if first and self.files.passwords() else None
        try:
            doc = Document(first, pw) if first else Document()
        except Exception:  # noqa: BLE001
            doc = Document()
        try:
            unit = self.prefs.get("unit", "cm")
            dlg = dialog_class(kind)(self, doc, 0, unit=unit)
            dlg.as_template()
            if dlg.exec() and dlg.saved:
                self._fill_templates(kind, dlg.saved["id"])
        finally:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    def _build_encrypt(self, f: QFormLayout):
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0)
        self.sec_label = QLabel("설정 안 함")
        self.sec_btn = QPushButton("보안 설정"); self.sec_btn.setAutoDefault(False); self.sec_btn.clicked.connect(self.edit_security)
        hl.addWidget(self.sec_label, 1); hl.addWidget(self.sec_btn)
        f.addRow("암호", row)

    def edit_security(self):
        from .dlg_security import EncryptDialog
        dlg = EncryptDialog(self, (self.security or {}).get("method"))
        dlg.form.setRowVisible(dlg.here.parentWidget(), False)   # 일괄은 늘 새 파일로
        if dlg.exec():
            self.security = dlg.values()
            how = dict(EncryptDialog.METHODS)[self.security["method"]].split(" · ")[0]
            parts = [p for p, on in (("열기 암호", self.security["user_pw"]), ("권한 암호", self.security["owner_pw"])) if on]
            self.sec_label.setText(" · ".join(parts + [how]))
        self._sync_apply()

    def _build_export_data(self, f: QFormLayout):
        self.data_name = QLineEdit(DATA_NAME)
        f.addRow("파일 이름", self.data_name)
        self.data_name.textChanged.connect(self._sync_apply)

    def _build_print(self, f: QFormLayout):
        from PySide6.QtPrintSupport import QPrinterInfo
        self.printer = QComboBox(); self.printer.addItems(QPrinterInfo.availablePrinterNames())
        self.printer.setCurrentText(QPrinterInfo.defaultPrinterName())
        f.addRow("프린터", self.printer)

    # ── 상태 ──
    def task(self) -> str:
        it = self.tasks.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else "convert"

    def out_panel(self) -> OutputPanel | None:
        k = self.task()
        if k == "print":
            return None
        return self.outs[OUT_STORES.get(k, BATCH_OUT)[0]]

    def _task_changed(self, _row: int):
        k = self.task()
        self.title.setText(TASK_NAMES[k])
        self.desc.setText(next(d for kk, _n, d in TASKS if kk == k))
        self.stack.setCurrentWidget(self.pages[k])
        if k == "ocr":
            self._ensure_ocr()
        create = k == "create"
        if create:
            from .fileops import CREATE_FILTER
            self.files.filters = CREATE_FILTER
        else:
            self.files.filters = PDF_FILTER
        self.files.only_pdf = not create
        cur = self.out_panel()
        for p in self.outs.values():
            p.setVisible(p is cur)
        self.files.clear_status()
        self._sync_apply()

    def _sync_apply(self, *_a):
        if self.running is not None:
            return
        self.apply_btn.setEnabled(self.files.count() > 0)

    def _save(self):
        if self.win is not None:
            from .window import save_settings
            save_settings(self.settings)

    def options(self) -> dict | str:
        """지금 작업의 엔진 options. 막을 것이 있으면 그 문구(글자)."""
        k = self.task()
        if k == "convert":
            from .dlg_convert import DEFAULTS, IMAGE_KEYS
            fmt = self.fmt.currentData()
            saved = (self.settings.get("convert") or {}).get(fmt) or {}
            if fmt in IMAGE_KEYS and not saved:
                saved = (self.settings.get("convert") or {}).get("png") or {}
            from .dlg_convert import current_langs
            return {"fmt": fmt, **DEFAULTS, **saved, "langs": current_langs(self.prefs, [])}
        if k == "compress":
            from .dlg_compress import custom_for
            lv = self.level()
            return {"level": lv, "custom": custom_for(self.prefs, lv)}
        if k == "ocr":
            self._ensure_ocr()
            langs = self.ocr_langs.langs() or (["ko"] if not self.ocr_langs.avail else [])
            if not langs:
                from .dlg_convert import LANG_NONE
                return LANG_NONE
            return {"lang": langs, "dpi": 300, "force": self.ocr_force.isChecked()}
        if k in DECO_TASKS:
            t = self.deco_combos[k].currentData()
            if not t:
                return NO_TEMPLATE
            return {"settings": t["settings"], "range": t.get("range")}
        if k == "encrypt":
            return dict(self.security) if self.security else NO_SECURITY
        if k == "export_data":
            name = self.data_name.text().strip()
            if not name or any(c in name for c in '\\/:*?"<>|'):
                return NO_NAME
            return {"name": name if name.lower().endswith(".csv") else name + ".csv"}
        if k == "print":
            return {"printer": self.printer.currentText()}
        return {}

    # ── 실행 ──
    def _apply_or_cancel(self):
        if self.running is not None:
            self.cancel()
        else:
            self.apply()

    def cancel(self):
        """지금 파일까지만 하고 남은 파일은 「취소됨」."""
        if self.running is not None:
            self.running.stop.set()
            self.apply_btn.setEnabled(False)

    def apply(self) -> list[dict]:
        """작업을 돌린다(창은 살아 있음). 줄마다 {"ok", "text", "output"}."""
        if not self.files.count():
            return []
        k = self.task()
        opts = self.options()
        if isinstance(opts, str):
            QMessageBox.warning(self, "일괄 편집", opts); return []
        out = self.out_panel()
        if out is not None:
            why = out.check()
            if why:
                QMessageBox.warning(self, "일괄 편집", why); return []
            out.commit()
        self.prefs["batch_task"] = k
        if k == "convert":
            self.prefs["batch_fmt"] = self.fmt.currentData()
        if k == "compress":
            self.prefs["compress_level"] = self.level()
        if k == "ocr":
            self.prefs["ocr_langs"] = opts["lang"]
        self._save()
        self.files.clear_status()
        paths, pws = self.files.paths(), self.files.passwords()
        self.results = [{"ok": False, "text": "", "output": ""} for _ in paths]
        if k == "print":
            return self._print(paths, pws, opts["printer"])
        job = {"op": k, "inputs": paths, "passwords": pws, "options": opts,
               "out_dir": out.folder() if out is not None and out.custom() else ""}
        run = _Job(job)
        loop = QEventLoop()
        run.rowStarted.connect(lambda r: self.files.set_status(r, "처리 중", True))
        run.rowDone.connect(self._row_done)
        run.finished.connect(loop.quit)
        self.running = run
        self._busy(True)
        run.start()
        if run.isRunning():
            loop.exec()
        run.wait()
        self.running = None
        self._busy(False)
        self._done(k)
        return self.results

    def _print(self, paths, pws, printer) -> list[dict]:
        def status(r: int, text: str, ok: bool):
            self.results[r] = {"ok": ok, "text": text, "output": ""}
            self.files.set_status(r, text, ok)
        if self.win is not None:
            self.win._batch_print(paths, printer, pws, status=status, summary=False)
        self._done("print")
        return self.results

    def _row_done(self, r: int, ok: bool, text: str, output: str):
        if 0 <= r < len(self.results):
            self.results[r] = {"ok": ok, "text": text, "output": output}
        self.files.set_status(r, text, ok)

    def _busy(self, on: bool):
        for w in (self.tasks, self.stack, self.files, *self.outs.values()):
            w.setEnabled(not on)
        self.apply_btn.setText("취소" if on else "적용")
        self.apply_btn.setEnabled(True)
        if not on:
            self._sync_apply()

    def _done(self, k: str):
        ok = [r for r in self.results if r["ok"]]
        bad = len(self.results) - len(ok)
        if not self.results:
            return
        text = f"{len(ok)}개 완료" + (f", {bad}개 실패 또는 취소" if bad else "")
        self.done_box(TASK_NAMES[k], text, next((r["output"] for r in ok if r["output"]), ""))

    def done_box(self, title: str, text: str, first_output: str):
        """끝 알림: [폴더 열기](결과가 있을 때) · [확인]."""
        box = QMessageBox(QMessageBox.Icon.Information, title, text, QMessageBox.StandardButton.Ok, self)
        box.button(QMessageBox.StandardButton.Ok).setText("확인")
        folder = box.addButton("폴더 열기", QMessageBox.ButtonRole.ActionRole) if first_output else None
        box.exec()
        if folder is not None and box.clickedButton() is folder:
            open_in_explorer(first_output)

    def reject(self):
        if self.running is not None:   # 도는 중 닫기 = 취소
            self.cancel(); return
        super().reject()
