"""데이터 추출(지금 문서): 양식 칸 값을 CSV 한 장으로.

고를 모드가 「PDF 양식 필드에서」 하나뿐이라 모드 창은 띄우지 않고 저장 위치만 묻는다(화면 원칙: 고를 것이 하나면 창 없이).
양식 칸이 없으면 알PDF 문구로 알린다. 여러 PDF 를 모으는 것은 일괄 편집의 「데이터 내보내기」.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..engine import tools as T
from .file_list import open_in_explorer

TITLE = "데이터 추출"
CSV_FILTER = "CSV 파일 (*.csv)"


def default_path(doc) -> str:
    """원본 폴더(새 문서면 저장 힌트·내 문서)의 「이름_데이터.csv」."""
    from .fileops import documents_dir
    hint = getattr(doc, "save_hint", None)
    folder = os.path.dirname(doc.path) if doc.path else (os.path.dirname(hint) if hint else documents_dir())
    return os.path.join(folder, os.path.splitext(doc.name)[0] + "_데이터.csv")


def run(win, t, ask_path=None) -> str | None:
    """지금 탭 문서의 양식 값을 CSV 로. ask_path(기본 경로) 는 시험용(저장 창 대신). 만든 경로나 None."""
    doc = t.doc
    if not T.form_row(doc.fitz):
        QMessageBox.information(win, TITLE, T.FORM_NONE)
        return None
    start = default_path(doc)
    if ask_path is not None:
        out = ask_path(start)
    else:
        out, _ = QFileDialog.getSaveFileName(win, TITLE, start, CSV_FILTER)
    if not out:
        return None
    if not out.lower().endswith(".csv"):
        out += ".csv"
    try:
        T.extract_data_doc(doc.fitz, out, doc.name)
    except (OSError, T.EngineError) as e:
        QMessageBox.warning(win, TITLE, str(e))
        return None
    win.say(f"양식 데이터를 저장했습니다: {out}")
    if ask_path is None:
        done_box(win, out)
    return out


def done_box(win, out: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Information, TITLE, f"저장했습니다.\n{out}", QMessageBox.StandardButton.Ok, win)
    box.button(QMessageBox.StandardButton.Ok).setText("확인")
    folder = box.addButton("폴더 열기", QMessageBox.ButtonRole.ActionRole)
    box.exec()
    if box.clickedButton() is folder:
        open_in_explorer(out)
