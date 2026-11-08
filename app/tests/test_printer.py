"""「PDF메트리」 프린터 앱 쪽 시험: 인쇄 폴더 파일은 이름 없는 새 문서로 열리고 인쇄 파일은 지워진다.
(프린터 설치·포트 모니터 DLL 은 관리자 권한이 들어 시험에서 돌리지 않는다. DLL 은 gcc 로 지어지는지만 본다)

python -m pytest app/tests/test_printer.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_printer_test_")

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app import printer  # noqa: E402
from app.ui import fileops as F  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


@pytest.mark.parametrize("name, title", [
    ("Microsoft Excel - 수학반 진도표.xlsx.pdf", "수학반 진도표.pdf"),
    ("수학반 진도표.pdf", "수학반 진도표.pdf"),
    ("Microsoft Word - 보고서.docx.pdf", "보고서.pdf"),
    ("v1.2 정리.pdf", "v1.2 정리.pdf"),
    (" .pdf", "인쇄 문서.pdf"),
])
def test_printed_title(name, title):
    assert printer.printed_title(os.path.join(printer.PRINT_DIR, "7-1", name)) == title


def test_is_printed():
    assert printer.is_printed(os.path.join(printer.PRINT_DIR, "7-1", "a.pdf"))
    assert not printer.is_printed(os.path.join(os.path.dirname(printer.PRINT_DIR), "a.pdf"))
    assert not printer.is_printed(r"C:\temp\a.pdf")


def test_open_printed_as_new_document(tmp_path, monkeypatch):
    monkeypatch.setattr(printer, "PRINT_DIR", str(tmp_path / "Print"))
    job = tmp_path / "Print" / "12-345"
    job.mkdir(parents=True)
    path = job / "Microsoft Excel - 수학반 진도표.pdf"
    d = fitz.open(); d.new_page(); d.new_page(); d.save(str(path)); d.close()
    w = MainWindow()
    w.open_path(str(path))
    t = w.cur()
    assert t is not None and t.doc.page_count == 2
    assert t.doc.path is None and t.doc.name == "수학반 진도표.pdf"
    assert t.doc.ask_save and t.doc.modified
    assert not job.exists()   # 인쇄 파일은 작업 폴더째 지운다
    assert not w.settings.get("recent") or all(r["path"] != str(path) for r in w.settings["recent"])
    # 저장 창은 새 PDF 저장 폴더의 「수학반 진도표.pdf」
    assert os.path.basename(F.save_suggestion(w, t.doc)) == "수학반 진도표.pdf"


@pytest.mark.skipif(not shutil.which("gcc"), reason="gcc 없음")
def test_monitor_dll_builds(tmp_path):
    out = printer.build_dll(str(tmp_path / printer.DLL_NAME))
    data = open(out, "rb").read()
    assert data[:2] == b"MZ" and b"InitializePrintMonitor2" in data
