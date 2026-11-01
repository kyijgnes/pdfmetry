# -*- coding: utf-8 -*-
"""구성 탭 회전·삭제·삽입·바꾸기: 엔진 동작과 대화상자 기본값·검사. 작은 PDF 를 그 자리에서 만들어 쓴다."""
from __future__ import annotations

import os
import sys

import fitz
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.engine import pages as P  # noqa: E402
from app.engine.document import Document  # noqa: E402


def make(n: int, tag: str = "T", size=(595, 842)) -> Document:
    f = fitz.open()
    for i in range(n):
        pg = f.new_page(width=size[0], height=size[1])
        pg.insert_text((72, 100), f"{tag}{i + 1}")
    return Document.from_bytes(f.tobytes(), f"{tag}.pdf")


def save_src(tmp_path, n: int, tag: str = "S", password: str | None = None, size=(400, 500)) -> str:
    f = fitz.open()
    for i in range(n):
        f.new_page(width=size[0], height=size[1]).insert_text((50, 80), f"{tag}{i + 1}")
    p = str(tmp_path / f"{tag}.pdf")
    if password:
        f.save(p, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    else:
        f.save(p)
    return p


def texts(d: Document) -> list[str]:
    return [d.fitz[i].get_text().strip() for i in range(d.page_count)]


# ── 장소 계산 ─────────────────────────────────────────────────
@pytest.mark.parametrize("place,page,before,want", [
    ("first", 4, True, 0), ("first", 4, False, 1),      # 「첫 페이지 뒤」 는 2쪽 자리
    ("last", 0, True, 4), ("last", 0, False, 5),
    ("page", 2, True, 2), ("page", 2, False, 3),
    ("page", 99, False, 5),
])
def test_insert_index(place, page, before, want):
    assert P.insert_index(place, page, before, 5) == want


def test_insert_blank_follows_boxes_and_rotation():
    d = make(3)
    pg = d.fitz[1]
    pg.set_mediabox(fitz.Rect(0, 0, 500, 700))
    pg.set_cropbox(fitz.Rect(10, 20, 400, 600))
    pg.set_trimbox(fitz.Rect(20, 30, 390, 590))
    pg.set_rotation(90)
    P.insert_blank(d, 2)                 # 2쪽 뒤 자리 → 앞 쪽은 2쪽
    new = d.fitz[2]
    assert d.page_count == 4 and new.get_text().strip() == ""
    assert new.mediabox == fitz.Rect(0, 0, 500, 700)
    assert new.cropbox == fitz.Rect(10, 20, 400, 600)
    assert new.trimbox == fitz.Rect(20, 30, 390, 590)
    assert new.rotation == 90
    P.insert_blank(d, 0)                 # 맨 앞이면 1쪽을 따른다
    assert d.fitz[0].mediabox == fitz.Rect(0, 0, 595, 842) and d.fitz[0].rotation == 0
    assert d.undo() and d.undo() and d.page_count == 3


def test_insert_blank_given_size_still_works():
    d = make(1)
    P.insert_blank(d, 1, 300, 200, 2)
    assert d.page_count == 3 and d.fitz[2].rect == fitz.Rect(0, 0, 300, 200)


# ── PDF에서 삽입 ──────────────────────────────────────────────
def test_insert_pdfs_many_files_in_order_one_undo(tmp_path):
    d = make(2)
    a = save_src(tmp_path, 2, "A")
    b = save_src(tmp_path, 1, "B", password="pw")
    n = P.insert_pdfs(d, 1, [(a, None, None), (b, "pw", None)])
    assert n == 3 and texts(d) == ["T1", "A1", "A2", "B1", "T2"]
    assert d.undo() and texts(d) == ["T1", "T2"]


def test_insert_pdfs_wrong_password_leaves_doc(tmp_path):
    d = make(2)
    b = save_src(tmp_path, 1, "B", password="pw")
    with pytest.raises(ValueError):
        P.insert_pdfs(d, 0, [(b, "틀림", None)])
    assert d.page_count == 2 and not d.undo()


def test_insert_pdfs_page_subset(tmp_path):
    d = make(1)
    a = save_src(tmp_path, 3, "A")
    P.insert_pdfs(d, 1, [(a, None, [2, 0])])
    assert texts(d) == ["T1", "A3", "A1"]


def test_is_xfa():
    f = fitz.open(); f.new_page()
    assert not P.is_xfa(f)
    x = f.get_new_xref(); f.update_object(x, "<<>>"); f.update_stream(x, b"<xdp/>")
    f.xref_set_key(f.pdf_catalog(), "AcroForm", f"<</Fields[]/XFA {x} 0 R>>")
    assert P.is_xfa(f)


# ── 바꾸기 ────────────────────────────────────────────────────
def _annot(d: Document, i: int, text: str):
    d.fitz[i].add_text_annot((300, 300), text)


def _notes(d: Document, i: int) -> list[str]:
    return [a.info["content"] for a in d.fitz[i].annots()]


def test_replace_without_keeping_annots(tmp_path):
    d = make(4)
    _annot(d, 1, "메모")
    s = save_src(tmp_path, 3, "S")
    P.replace(d, [1, 2], s, [0, 1, 2])
    assert texts(d) == ["T1", "S1", "S2", "S3", "T4"]
    assert not any(_notes(d, i) for i in range(d.page_count))


def test_replace_keep_annots_same_count(tmp_path):
    d = make(3)
    _annot(d, 1, "메모")
    s = save_src(tmp_path, 2, "S", size=(400, 500))
    P.replace(d, [1], s, [1], keep_annots=True)
    assert texts(d) == ["T1", "S2", "T3"]
    assert _notes(d, 1) == ["메모"]
    assert d.fitz[1].rect == fitz.Rect(0, 0, 400, 500)   # 상자는 원본 쪽 것
    assert d.undo() and texts(d) == ["T1", "T2", "T3"]


def test_replace_keep_annots_more_source(tmp_path):
    d = make(3)
    _annot(d, 1, "메모")
    s = save_src(tmp_path, 3, "S")
    P.replace(d, [1], s, [0, 1, 2], keep_annots=True)
    assert texts(d) == ["T1", "S1", "S2", "S3", "T3"]
    assert _notes(d, 1) == ["메모"] and not _notes(d, 2)


def test_replace_keep_annots_more_target(tmp_path):
    d = make(4)
    for i in range(4):
        _annot(d, i, f"메모{i + 1}")
    s = save_src(tmp_path, 1, "S")
    P.replace(d, [1, 2], s, [0], keep_annots=True)
    assert texts(d) == ["T1", "S1", "T4"]
    assert [_notes(d, i) for i in range(3)] == [["메모1"], ["메모2"], ["메모4"]]
    # 저장했다 다시 열어도 같다(옮긴 내용 객체가 걷히지 않음)
    f = fitz.open("pdf", d.to_bytes())
    assert [f[i].get_text().strip() for i in range(3)] == ["T1", "S1", "T4"]


def test_replace_keep_annots_clears_rotation(tmp_path):
    d = make(2)
    d.fitz[0].set_rotation(90)
    s = save_src(tmp_path, 1, "S")
    P.replace(d, [0], s, [0], keep_annots=True)
    assert d.fitz[0].rotation == 0


def test_replace_password_source(tmp_path):
    d = make(2)
    s = save_src(tmp_path, 1, "S", password="pw")
    with pytest.raises(ValueError):
        P.replace(d, [0], s, [0])
    P.replace(d, [0], s, [0], password="pw")
    assert texts(d) == ["S1", "T2"]


# ── 대화상자 ──────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_page_from_text():
    from app.ui.dlg_organize import page_from_text
    labels = ["i", "ii", "1", "2", "3"]
    assert page_from_text("ii", labels) == 1
    assert page_from_text("3", labels) == 4          # 레이블이 먼저
    assert page_from_text("5", labels) == 4          # 맞는 레이블이 없으면 쪽 번호
    assert page_from_text("6", labels) is None
    assert page_from_text("", labels) is None and page_from_text("x", labels) is None
    assert page_from_text("2", ["1", "2", "3"]) == 1


def test_place_dialog_defaults_and_memory(qapp):
    from app.ui.dlg_organize import PlaceDialog
    prefs: dict = {}
    dlg = PlaceDialog(None, "빈 페이지 삽입", ["1", "2", "3", "4"], 2, prefs)
    assert dlg.r_page.isChecked() and dlg.r_after.isChecked() and dlg.page.text() == "3"
    assert dlg.total_lbl.text() == "/ 4"
    dlg.r_first.setChecked(True); dlg.r_before.setChecked(True)
    assert not dlg.page.isEnabled()
    assert dlg.check() and dlg.at() == 0
    dlg.remember()
    assert prefs["insert_place"] == {"at": "first", "before": True}
    again = PlaceDialog(None, "빈 페이지 삽입", ["1", "2", "3", "4"], 2, prefs)
    assert again.r_first.isChecked() and again.r_before.isChecked() and again.page.text() == "3"


def test_place_dialog_bad_page_keeps_open(qapp, monkeypatch):
    from app.ui import dlg_organize as O
    said = []
    monkeypatch.setattr(O, "_warn", lambda parent, text: said.append(text))
    dlg = O.PlaceDialog(None, "빈 페이지 삽입", ["1", "2"], 0, {})
    dlg.page.setText("9")
    dlg.accept()
    assert said == ["유효한 페이지 범위를 입력하십시오."] and dlg.result() == 0


def test_rotate_dialog_default_zero(qapp):
    from app.ui.dlg_organize import RotateDialog
    d = make(3)
    dlg = RotateDialog(None, d, 0, [1, 2])
    assert dlg.angle() == 0
    dlg.buttons[2].setChecked(True)
    vals = dlg.values()
    assert vals["degrees"] == 180 and vals["pages"] == [1, 2]
    assert not dlg.preview.pixmap().isNull()


def test_replace_dialog_defaults(qapp, tmp_path):
    from app.ui.dlg_organize import ReplaceDialog
    d = make(5)
    dlg = ReplaceDialog(None, d, [1, 2, 3])
    assert (dlg.t_from.value(), dlg.t_to.value()) == (2, 4)
    assert not dlg.s_from.isEnabled() and not dlg.keep.isChecked()
    s = save_src(tmp_path, 3, "S")
    assert dlg.set_source(s)
    assert dlg.s_from.isEnabled() and (dlg.s_from.value(), dlg.s_to.value()) == (1, 1)
    dlg.s_to.setValue(3)
    vals = dlg.values()
    assert vals["pages"] == [1, 2, 3] and vals["src_pages"] == [0, 1, 2] and vals["path"] == s


def test_replace_dialog_checks(qapp, monkeypatch, tmp_path):
    from app.ui import dlg_organize as O
    said = []
    monkeypatch.setattr(O, "_warn", lambda parent, text: said.append(text))
    d = make(5)
    dlg = O.ReplaceDialog(None, d, [0])
    assert not dlg.check() and said[-1] == "파일이 선택되지 않았습니다."
    dlg.set_source(save_src(tmp_path, 2, "S"))
    dlg.t_from.setValue(4); dlg.t_to.setValue(2)
    assert not dlg.check() and said[-1] == "유효한 페이지 범위를 입력하십시오."
    # 자기 자신
    own = str(tmp_path / "own.pdf"); d.fitz.save(own); d.path = own
    assert not dlg.set_source(own) and said[-1] == "문서는 자신으로 대체 할 수 없습니다. 다른 문서를 선택하십시오."
