"""쪽 범위 칸(PageRangeEdit)·엄격한 범위 파서·페이지 내보내기."""
import os
import tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_range_test_")   # 사용자 설정(최근 파일)을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402

from app.engine import pages as P  # noqa: E402
from app.engine.document import Document  # noqa: E402


def ten(path) -> Document:
    f = fitz.open()
    for i in range(10):
        f.new_page().insert_text((72, 72), f"p{i + 1}")
    f.save(str(path))
    return Document(str(path))


@pytest.mark.parametrize("spec,want", [
    ("1-3,5", [0, 1, 2, 4]),
    (" 1 - 3 , 5 ", [0, 1, 2, 4]),
    ("5,1,3,3", [0, 2, 4]),
    ("10", [9]),
    ("", []),
])
def test_strict_ok(spec, want):
    assert P.parse_range(spec, 10, strict=True) == want


@pytest.mark.parametrize("spec", ["5-1", "3-", "-3", "0", "11", "1,,3", "1,", "1-2-3", "1-3,99", "1 3", "a"])
def test_strict_bad(spec):
    with pytest.raises(ValueError):
        P.parse_range(spec, 10, strict=True)


def test_loose_unchanged():
    # 인쇄 창·바꾸기 등 옛 호출부의 동작은 그대로: 빈 글 = 전부, 열린 범위·공백 나누기·입력 차례
    assert P.parse_range("", 4) == [0, 1, 2, 3]
    assert P.parse_range("3-", 5) == [2, 3, 4]
    assert P.parse_range("3 1", 5) == [2, 0]


def test_labels():
    labels = ["i", "ii", "iii", "1", "2"]
    assert P.parse_range("ii-1", 5, strict=True, labels=labels) == [1, 2, 3]
    assert P.parse_range("iii,5", 5, strict=True, labels=labels) == [2, 4]   # 레이블이 없으면 쪽 번호
    assert P.format_range([0, 1, 2, 4], labels) == "i-iii,2"


def test_extract_name(tmp_path):
    assert os.path.basename(P.extract_name(str(tmp_path), "보고서.pdf")) == "페이지 보고서.pdf"
    (tmp_path / "페이지 보고서.pdf").write_bytes(b"")
    assert os.path.basename(P.extract_name(str(tmp_path), "보고서.pdf")) == "페이지 보고서-1.pdf"
    assert len(os.path.basename(P.extract_name(str(tmp_path), "가" * 300 + ".pdf"))) == len("페이지 ") + 150 + 4


def test_extract_keeps_security_and_layers(tmp_path):
    f = fitz.open()
    for i in range(4):
        f.new_page().insert_text((72, 72), f"p{i + 1}")
    f.add_ocg("층")
    f.set_metadata({"title": "제목"})
    src = tmp_path / "enc.pdf"
    f.save(str(src), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    d = Document(str(src), "u")
    out = P.extract(d, [3, 0], str(tmp_path / "out.pdf"), creator="PDF메트리")
    r = fitz.open(out)
    assert r.needs_pass and r.authenticate("u")
    assert [p.get_text().strip() for p in r] == ["p1", "p4"]
    assert r.metadata["title"] == "제목" and r.metadata["creator"] == "PDF메트리"
    assert [o["name"] for o in r.get_ocgs().values()] == ["층"]
    assert d.page_count == 4   # 원본은 그대로


# ── 화면 ──────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_range_edit(qapp):
    from app.ui.dialogs import PageRangeEdit
    w = PageRangeEdit(10, 0)
    assert w.all.isChecked() and w.edit.text() == "1" and not w.edit.isEnabled()
    assert w.pages() == list(range(10))
    w.which.setCurrentIndex(1)   # 짝수 페이지
    assert w.pages() == [1, 3, 5, 7, 9]
    w.rng.setChecked(True); w.edit.setText("1-10")   # 짝수 + 사용자 지정 1-10
    assert w.edit.isEnabled() and w.pages() == [1, 3, 5, 7, 9]
    w.which.setCurrentIndex(0); w.edit.setText("1-3,5")
    assert w.pages() == [0, 1, 2, 4]
    for bad in ("1-3,99", "", "3-"):
        w.edit.setText(bad)
        with pytest.raises(ValueError, match="유효한 페이지 범위"):
            w.pages()
    w.which.setCurrentIndex(1); w.edit.setText("1")   # 거르고 나서 0쪽
    with pytest.raises(ValueError):
        w.pages()


def test_range_edit_typing(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from app.ui.dialogs import PageRangeEdit
    w = PageRangeEdit(10, 0); w.rng.setChecked(True); w.edit.clear()
    QTest.keyClicks(w.edit, "1-3, 5ab")
    assert w.edit.text() == "1-3,5"   # 글자·공백은 타자로 안 들어간다
    w.edit.clear(); w.edit.insert("1 - 3")   # 붙여넣기로는 공백이 들어간다
    assert w.edit.text() == "1 - 3" and w.edit.maxLength() == 200
    assert not w.edit.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)


def test_set_pages(qapp):
    from app.ui.dialogs import PageRangeEdit
    w = PageRangeEdit(10, 0)
    w.set_pages([1, 3, 5, 7, 9]); assert w.all.isChecked() and w.which.currentData() == "even"
    w.set_pages([0, 2, 4, 6, 8]); assert w.which.currentData() == "odd"
    w.set_pages(list(range(10))); assert w.which.currentData() == "all"
    w.set_pages([4, 0, 1, 2]); assert w.rng.isChecked() and w.edit.text() == "1-3,5" and w.which.currentData() == "all"
    w.cur.setChecked(True); assert w.rng.isChecked() and w.edit.text() == "1"   # 옛 「지금 쪽」 호환


def test_extract_dialog(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.ui.dlg_extract import ExtractDialog
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dlg = ExtractDialog(None, 10, 0, [0, 1, 2, 4])
    assert dlg.windowTitle() == "페이지 내보내기" and dlg.range.edit.text() == "1-3,5"
    assert not dlg.delete_after.isChecked() and dlg.delete_after.isHidden()
    dlg.range.edit.setText("1-3,99"); dlg.accept()
    assert dlg.result() == 0 and warned[-1] == P.RANGE_ERROR
    dlg.range.rng.setChecked(False); dlg.range.all.setChecked(True); dlg.delete_after.setChecked(True); dlg.accept()
    assert dlg.result() == 0 and "모든 페이지" in warned[-1]   # 전부 지우기는 막고 창을 닫지 않는다
    dlg.range.rng.setChecked(True); dlg.range.edit.setText("2,4"); dlg.accept()
    assert dlg.result() == 1 and dlg.values() == {"pages": [1, 3], "delete": True}


def test_extract_in_window(qapp, tmp_path, monkeypatch):
    """창에서: 고른 쪽을 새 탭으로 열고, 추출 후 삭제가 원본에서 지우며 되돌릴 수 있다."""
    from app.ui import window as W
    from app.ui.dlg_extract import ExtractDialog
    monkeypatch.setattr(W, "data_dir", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(ExtractDialog, "exec", lambda self: (self.range.rng.setChecked(True), self.range.edit.setText("2-4,7"),
                                                             self.delete_after.setChecked(True), self.accept(), 1)[-1])
    src = tmp_path / "보고서.pdf"
    ten(src).close()
    win = W.MainWindow()
    try:
        win.open_path(str(src))
        t = win.tabs[-1]
        win.pages_action("extract", [])
        new = win.tabs[-1]
        assert new is not t and new.doc.name == "페이지 보고서.pdf"
        assert [new.doc.fitz[i].get_text().strip() for i in range(new.doc.page_count)] == ["p2", "p3", "p4", "p7"]
        assert new.doc.ask_save and new.doc.save_hint == os.path.join(str(tmp_path), "페이지 보고서.pdf")
        assert t.doc.page_count == 6 and t.doc.can_undo()
        t.doc.undo(); assert t.doc.page_count == 10
    finally:
        for x in list(win.tabs):
            x.doc.modified = False
        win.close()
