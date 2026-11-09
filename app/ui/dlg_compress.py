"""PDF 압축 창: 파일 목록(지금 문서가 들어감) · 높음/중간/낮음 · 사용자 지정 설정 · 저장 위치 · [적용].

결과는 「원래이름_압축.pdf」(있으면 「_압축_1」), 원본은 덮어쓰지 않는다. 못 줄인 파일은 그 줄 상태 칸에 실패로 남고,
하나라도 줄였으면 「압축 성공!」 창에 평균 감소율을 보인다. 단계 · 사용자 지정 값 · 저장 위치는 기억한다.
"""
from __future__ import annotations

import os

import fitz
from PySide6.QtCore import QEventLoop, QThread, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QMessageBox, QPushButton, QRadioButton, QVBoxLayout)

from ..engine import tools as T
from . import icons
from .file_list import FileListPanel, OutputPanel, open_in_explorer, unique_path
from .dialogs import settle_default

LEVEL_NAMES = {"high": "높음", "medium": "중간", "low": "낮음"}
SUFFIX = "_압축"
SUCCESS_TITLE = "압축 성공!"
PDF_FILTER = "PDF 파일 (*.pdf)"
CHECKS = (("dedupe", "중복 리소스 제거"), ("streams", "스트림 콘텐츠 압축"))
REMOVES = (("bookmarks", "북마크 삭제"), ("attach", "첨부 파일 삭제"), ("meta", "문서 정보 및 메타데이터 삭제"),
           ("annots", "모든 메모 및 양식 삭제"))


def upgrade_prefs(prefs: dict) -> None:
    """옛 「compress_meta」(체크 하나) 를 세 단계 meta 로 옮기고 지운다."""
    if "compress_meta" in prefs:
        meta = bool(prefs.pop("compress_meta"))
        cc = prefs.setdefault("compress_custom", {})
        for lv in T.COMPRESS_LEVELS:
            cc.setdefault(lv, {})["meta"] = meta


def custom_for(prefs: dict, level: str) -> dict:
    """그 단계의 사용자 지정 값(빠진 키는 기본값)."""
    upgrade_prefs(prefs)
    saved = (prefs.get("compress_custom") or {}).get(level) or {}
    base = T.compress_defaults(level)
    out = {k: (saved[k] if k in saved else v) for k, v in base.items()}
    if int(out["dpi"]) not in T.CUSTOM_DPIS:
        out["dpi"] = base["dpi"]
    return out


class CustomDialog(QDialog):
    """「사용자 지정 설정」: 지금 고른 단계의 값을 고친다."""

    def __init__(self, parent, values: dict):
        super().__init__(parent)
        self.setWindowTitle("사용자 지정 설정")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.dpi = QComboBox()
        for d in T.CUSTOM_DPIS:
            self.dpi.addItem(f"{d}dpi", d)
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(int(values["dpi"]))))
        form.addRow("이미지 압축 해상도:", self.dpi)
        lay.addLayout(form)
        self.boxes: dict[str, QCheckBox] = {}
        for title, rows in (("콘텐츠 압축", CHECKS), ("삭제된 콘텐츠 압축", REMOVES)):
            g = QGroupBox(title); vl = QVBoxLayout(g)
            for key, label in rows:
                cb = QCheckBox(label); cb.setChecked(bool(values.get(key))); vl.addWidget(cb); self.boxes[key] = cb
            lay.addWidget(g)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        settle_default(self, bb.button(QDialogButtonBox.StandardButton.Ok))
        bb.accepted.connect(self.accept); lay.addWidget(bb)

    def values(self) -> dict:
        return {"dpi": int(self.dpi.currentData()), **{k: cb.isChecked() for k, cb in self.boxes.items()}}


class _Job(QThread):
    """파일마다 차례로 압축. rowDone(줄, 성공, 상태 글, 결과 {"out", "before", "after"})."""
    rowStarted = Signal(int)
    rowDone = Signal(int, bool, str, dict)

    def __init__(self, tasks: list[dict], level: str, custom: dict):
        super().__init__()
        self.tasks, self.level, self.custom = tasks, level, custom

    def run(self):
        for k, t in enumerate(self.tasks):
            self.rowStarted.emit(k)
            src = t.get("doc")
            own = src is None
            try:
                if own:
                    src = fitz.open(t["path"])
                    if src.needs_pass and not src.authenticate(t.get("password") or ""):
                        raise T.EngineError("비밀번호가 정확하지 않습니다. 기타 비밀번호로 재시도하십시오.")
                res = T.compress(src, t["out"], self.level, password=t.get("password"), before=t["before"],
                                 custom=self.custom)
                self.rowDone.emit(k, True, "완료", {"out": t["out"], **res})
            except Exception as e:  # noqa: BLE001
                self.rowDone.emit(k, False, str(e) or "실패", {})
            finally:
                if src is not None:
                    try:
                        src.close()
                    except Exception:  # noqa: BLE001
                        pass


def average_cut(results: list[dict]) -> int:
    """줄어든 파일만으로 (원본 합 - 결과 합) / 원본 합, 소수점 없는 %(최소 1)."""
    rs = [r for r in results if r.get("after") is not None and r["after"] < r["before"]]
    before = sum(r["before"] for r in rs)
    if not before:
        return 1
    return max(1, round((before - sum(r["after"] for r in rs)) / before * 100))


class CompressDialog(QDialog):
    """win: 앱 창(설정 · 결과 열기). paths/passwords: 처음 넣을 파일.
    sources: 경로 → 그 파일 대신 압축할 지금 상태 사본(fitz.Document, 저장 안 한 편집 반영). 창이 닫히면 닫는다.
    origins: 경로 → 「원본 파일 위치」 로 쓸 폴더(임시로 떠 둔 새 문서)."""

    def __init__(self, win, paths: list[str] | None = None, passwords: list[str | None] | None = None,
                 sources: dict | None = None, origins: dict | None = None):
        super().__init__(win)
        self.win = win
        self.prefs = win.prefs if win is not None else {}
        self.settings = win.settings if win is not None else {}
        upgrade_prefs(self.prefs)
        self.sources = {os.path.normcase(k): v for k, v in (sources or {}).items()}
        self.origins = {os.path.normcase(k): v for k, v in (origins or {}).items()}
        self.results: list[dict] = []
        self.setWindowTitle("PDF 압축"); self.resize(900, 520)
        root = QHBoxLayout(self)
        self.files = FileListPanel(self, "압축", PDF_FILTER, paths=paths, passwords=passwords)
        root.addWidget(self.files, 1)
        side = QVBoxLayout()
        g = QGroupBox("압축 옵션"); gl = QVBoxLayout(g)
        self.group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        row = QHBoxLayout()
        for lv in T.COMPRESS_LEVELS:
            rb = QRadioButton(LEVEL_NAMES[lv]); self.radios[lv] = rb; self.group.addButton(rb); row.addWidget(rb)
        self.radios.get(self.prefs.get("compress_level"), self.radios["high"]).setChecked(True)
        gl.addLayout(row)
        self.gear = QPushButton(icons.icon("settings", 16), "사용자 지정 설정"); self.gear.clicked.connect(self.edit_custom)
        gl.addWidget(self.gear)
        side.addWidget(g)
        side.addStretch()
        self.out = OutputPanel(self, self.settings.setdefault("compress_out", {}), "압축")
        side.addWidget(self.out)
        self.apply_btn = QPushButton("적용"); self.apply_btn.setDefault(True); self.apply_btn.clicked.connect(self.apply)
        settle_default(self, self.apply_btn)
        hl = QHBoxLayout(); hl.addStretch(); hl.addWidget(self.apply_btn)
        side.addLayout(hl)
        root.addLayout(side, 1)
        self.files.changed.connect(lambda: self.apply_btn.setEnabled(self.files.count() > 0))
        self.apply_btn.setEnabled(self.files.count() > 0)

    def level(self) -> str:
        return next(k for k, rb in self.radios.items() if rb.isChecked())

    def _save(self):
        if self.win is not None:
            from .window import save_settings
            save_settings(self.settings)

    def edit_custom(self):
        lv = self.level()
        dlg = CustomDialog(self, custom_for(self.prefs, lv))
        if dlg.exec():
            self.prefs.setdefault("compress_custom", {})[lv] = dlg.values()
            self._save()

    def _folder(self, path: str) -> str:
        if not self.out.custom() and os.path.normcase(path) in self.origins:
            return self.origins[os.path.normcase(path)]
        return self.out.folder_for(path)

    def apply(self) -> list[dict]:
        """파일마다 압축하고(창은 살아 있음) 끝나면 결과 창. 돌려주는 값: 줄마다 {"ok", "out", "before", "after", "text"}."""
        if not self.files.count():
            return []
        why = self.out.check()
        if why:
            QMessageBox.warning(self, "PDF 압축", why); return []
        lv = self.level()
        self.prefs["compress_level"] = lv
        self.out.commit(); self._save()
        tasks, taken = [], set()
        for p, pw in zip(self.files.paths(), self.files.passwords()):
            stem = os.path.splitext(os.path.basename(p))[0] + SUFFIX
            out = unique_path(self._folder(p), stem, ".pdf", taken); taken.add(out)
            src = self.sources.get(os.path.normcase(p))
            tasks.append({"path": p, "password": pw, "out": out, "before": os.path.getsize(p),
                          "doc": fitz.open("pdf", src.tobytes(encryption=fitz.PDF_ENCRYPT_KEEP)) if src is not None else None})
            if src is not None and pw:
                tasks[-1]["doc"].authenticate(pw)
        self.files.clear_status()
        self.results = [{"ok": False, "text": ""} for _ in tasks]
        job = _Job(tasks, lv, custom_for(self.prefs, lv))
        loop = QEventLoop()
        job.rowStarted.connect(lambda k: self.files.set_status(k, "압축 중", True))
        job.rowDone.connect(self._row_done)
        job.finished.connect(loop.quit)
        self._busy(True)
        job.start()
        if job.isRunning():
            loop.exec()
        job.wait()
        self._busy(False)
        ok = [r for r in self.results if r["ok"]]
        if ok:
            how = self.success(len(ok), average_cut(ok))
            if how == "open":
                self.accept()
                for r in ok:
                    self.win.open_path(r["out"])
            elif how == "folder":
                open_in_explorer(ok[0]["out"])
        return self.results

    def _row_done(self, k: int, good: bool, text: str, res: dict):
        self.results[k] = {"ok": good, "text": text, **res}
        self.files.set_status(k, text, good)

    def _busy(self, on: bool):
        for w in (self.files, self.gear, self.out, self.apply_btn, *self.radios.values()):
            w.setEnabled(not on)
        if not on:
            self.apply_btn.setEnabled(self.files.count() > 0)

    def success(self, n: int, pct: int) -> str | None:
        """「압축 성공!」 창. "folder"(폴더 열기, 기본) · "open"(파일 열기) · None(닫음)."""
        box = QMessageBox(QMessageBox.Icon.Information, SUCCESS_TITLE,
                          f"{n}개의 문서를 압축하여 파일 크기를 평균 {pct}% 줄였습니다!", QMessageBox.StandardButton.NoButton, self)
        folder = box.addButton("폴더 열기", QMessageBox.ButtonRole.AcceptRole)
        opener = box.addButton("파일 열기", QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(folder)
        box.exec()
        c = box.clickedButton()
        return "folder" if c is folder else "open" if c is opener else None

    def done(self, r):
        for d in self.sources.values():
            try:
                d.close()
            except Exception:  # noqa: BLE001
                pass
        self.sources = {}
        super().done(r)
