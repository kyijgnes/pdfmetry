"""라운드 15 다: PDF 압축 창 · 비교 결과 탭 · 병합 창 · 평면화 새 탭.

python -m pytest app/tests/test_r15_tools.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_r15_tools_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import compare as C  # noqa: E402
from app.engine import pages as P  # noqa: E402
from app.engine import tools as T  # noqa: E402
from app.ui import dlg_compare as DC  # noqa: E402
from app.ui import dlg_compress as DZ  # noqa: E402
from app.ui import dlg_merge as DM  # noqa: E402
from app.ui import file_list as FL  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _text_pdf(path: str, pages: list[list[str]], toc=None, pw: str | None = None) -> str:
    d = fitz.open()
    for lines in pages:
        p = d.new_page()
        for k, ln in enumerate(lines):
            p.insert_text((72, 80 + 20 * k), ln, fontsize=12)
    if toc:
        d.set_toc(toc)
    if pw:
        d.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=pw, owner_pw=pw)
    else:
        d.save(path)
    d.close()
    return path


def _image_pdf(path: str, toc=None) -> str:
    """고운 그림(줄일 거리)이 든 두 쪽."""
    rng = np.random.default_rng(1)
    d = fitz.open()
    for k in range(2):
        p = d.new_page()
        arr = (rng.random((900, 900, 3)) * 60 + np.linspace(0, 190, 900)[None, :, None]).astype(np.uint8)
        pm = fitz.Pixmap(fitz.csRGB, 900, 900, arr.tobytes(), False)
        p.insert_image(fitz.Rect(72, 72, 272, 272), pixmap=pm)
        p.insert_text((72, 320), f"page {k + 1}", fontsize=12)
    if toc:
        d.set_toc(toc)
    d.save(path, deflate=False)
    d.close()
    return path


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


@pytest.fixture()
def folder():
    return tempfile.mkdtemp(prefix="r15_")


# ── 압축 ──────────────────────────────────────────────────────
def _run_compress(monkeypatch, win, t, answer=None):
    """창을 띄우지 않고 [적용] 을 누른 것처럼. 「압축 성공!」 에 넘어간 (개수, %) 를 돌려준다."""
    seen = {}

    def fake_exec(self):
        self.out.combo.setCurrentIndex(self.out.ORIGINAL)
        seen["results"] = self.apply()
        seen["dlg"] = self
        return 0
    monkeypatch.setattr(DZ.CompressDialog, "exec", fake_exec)
    monkeypatch.setattr(DZ.CompressDialog, "success", lambda self, n, pct: (seen.setdefault("success", (n, pct)), answer)[1])
    win.compress(t)
    return seen


def test_compress_dialog_names_and_average(monkeypatch, win, folder):
    src = _image_pdf(os.path.join(folder, "그림.pdf"))
    win.open_path(src); t = win.cur()
    seen = _run_compress(monkeypatch, win, t)
    out = os.path.join(folder, "그림_압축.pdf")
    assert os.path.isfile(out)
    assert seen["dlg"].files.paths() == [src]   # 지금 문서가 들어 있었다
    n, pct = seen["success"]
    assert n == 1 and pct == round((os.path.getsize(src) - os.path.getsize(out)) / os.path.getsize(src) * 100)
    assert seen["dlg"].files.status(0) == "완료"
    seen = _run_compress(monkeypatch, win, t)
    assert os.path.isfile(os.path.join(folder, "그림_압축_1.pdf"))
    assert win.prefs["compress_level"] == "high"


def test_compress_open_answer_opens_results(monkeypatch, win, folder):
    src = _image_pdf(os.path.join(folder, "열기.pdf"))
    win.open_path(src); t = win.cur()
    _run_compress(monkeypatch, win, t, answer="open")
    assert win.cur().doc.path == os.path.join(folder, "열기_압축.pdf")


def test_compress_custom_removes_bookmarks(monkeypatch, win, folder):
    src = _image_pdf(os.path.join(folder, "북마크.pdf"), toc=[[1, "하나", 1], [1, "둘", 2]])
    win.prefs["compress_custom"] = {"high": {"bookmarks": True}}
    win.open_path(src); t = win.cur()
    _run_compress(monkeypatch, win, t)
    d = fitz.open(os.path.join(folder, "북마크_압축.pdf"))
    assert d.get_toc() == []
    d.close()
    # 기본(끔)이면 북마크가 남는다
    win.prefs["compress_custom"] = {}
    _run_compress(monkeypatch, win, t)
    d = fitz.open(os.path.join(folder, "북마크_압축_1.pdf"))
    assert len(d.get_toc()) == 2
    d.close()


def test_compress_not_smaller_marks_row(monkeypatch, win, folder):
    small = os.path.join(folder, "작은.pdf")
    d = fitz.open(); d.new_page().insert_text((72, 72), "x"); d.save(small, garbage=4, deflate=True); d.close()
    win.open_path(small); t = win.cur()
    real = T.compress

    def no_gain(src, out, level="high", **kw):   # 다 줄인 문서: 엔진은 파일을 안 만들고 NotSmaller
        kw["before"] = 1
        return real(src, out, level, **kw)
    monkeypatch.setattr(DZ.T, "compress", no_gain)
    seen = _run_compress(monkeypatch, win, t)
    assert "success" not in seen
    assert seen["results"][0]["ok"] is False
    assert seen["dlg"].files.status(0) == T.NOT_SMALLER
    assert not os.path.exists(os.path.join(folder, "작은_압축.pdf"))


def test_compress_custom_dialog_and_prefs_upgrade():
    prefs = {"compress_meta": False}
    v = DZ.custom_for(prefs, "medium")
    assert "compress_meta" not in prefs and v["meta"] is False and v["dpi"] == 120
    dlg = DZ.CustomDialog(None, v)
    assert dlg.dpi.currentData() == 120   # 중간 기본 120 이 목록에 있다(300 으로 바뀌지 않음)
    dlg.boxes["bookmarks"].setChecked(True)
    assert dlg.values()["bookmarks"] is True and dlg.values()["dpi"] == 120
    assert T.quality_for_dpi(144) == 85 and T.quality_for_dpi(400) == 100


def test_output_panel_folder(folder):
    store = {}
    op = FL.OutputPanel(None, store, "압축")
    assert op.folder_for(os.path.join(folder, "a.pdf")) == folder
    op.combo.setCurrentIndex(op.CUSTOM); op.path.setText(os.path.join(folder, "지정"))
    assert op.check() is None and os.path.isdir(os.path.join(folder, "지정"))
    op.commit()
    assert store == {"custom": True, "folder": os.path.join(folder, "지정")}
    op2 = FL.OutputPanel(None, store, "압축")
    assert op2.custom() and op2.folder_for(os.path.join(folder, "a.pdf")) == os.path.join(folder, "지정")
    assert FL.unique_path(folder, "a") == os.path.join(folder, "a.pdf")


# ── 비교 ──────────────────────────────────────────────────────
def _compare(monkeypatch, win, path_b: str | None = None, same: bool = False):
    def fake_exec(self):
        if same:
            self.pick(1, dict(self.picked[0]))
        else:
            self.pick(1, {"name": os.path.basename(path_b), "path": path_b, "tab": None})
        self.accept()
        return self.result()
    monkeypatch.setattr(DC.CompareSelectDialog, "exec", fake_exec)
    win.compare(win.cur())


def test_compare_result_tab(monkeypatch, win, folder):
    a = _text_pdf(os.path.join(folder, "이전.pdf"), [["hello world", "second line", "gone"], ["page two"]])
    b = _text_pdf(os.path.join(folder, "새.pdf"), [["hello world", "second LINE", "added more"], ["page two", "tail"]])
    win.open_path(a); n0 = len(win.tabs)
    _compare(monkeypatch, win, b)
    assert len(win.tabs) == n0 + 1
    t = win.cur()
    assert t.doc.name.startswith("파일 비교") and t.doc.page_count == 2
    panel = t.compare_panel
    cnt = C.counts(panel.items)
    assert cnt["insert"] == 1 and cnt["modify"] == 1
    assert panel.total.text() == f"총 변경 항목: {len(panel.items)}"
    names = {ly["name"] for ly in t.doc.layers()}
    assert C.layer_name("modify", "text") in names
    # 이전/다음이 상자를 차례로 고른다
    panel.step(1); first = panel.current
    panel.step(1); second = panel.current
    assert first != second
    panel.step(-1); assert panel.current == first
    assert t.viewer.search_hits and t.viewer.search_hits[0][0] == panel.items[first]["page"]
    # 「수정」 숨기기: 레이어가 화면에서 꺼지고 목록에서도 빠진다
    panel.kind_btns["modify"].setChecked(False)
    assert C.layer_name("modify", "text") in t.doc.hidden_layers
    assert all(it["kind"] != "modify" for it in panel.visible())
    panel.set_all(True)
    assert not t.doc.hidden_layers


def test_compare_delete_hidden_removes_red(monkeypatch, win, folder):
    a = _text_pdf(os.path.join(folder, "가.pdf"), [["one", "two", "three"]])
    b = _text_pdf(os.path.join(folder, "나.pdf"), [["one", "three"]])
    win.open_path(a)
    _compare(monkeypatch, win, b)
    t = win.cur(); panel = t.compare_panel
    assert C.counts(panel.items)["delete"] == 1

    def red(doc):
        pix = doc.fitz[0].get_pixmap(dpi=40)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        return int(((arr[..., 0] > 200) & (arr[..., 1] < 160) & (arr[..., 2] < 160)).sum())
    assert red(t.doc) > 0
    panel.kind_btns["delete"].setChecked(False)
    assert red(t.doc) == 0


def test_compare_same_document_and_no_diff(monkeypatch, win, folder):
    a = _text_pdf(os.path.join(folder, "같음.pdf"), [["same"]])
    win.open_path(a)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a_, **k: warned.append(a_[2]))
    calls = []

    def fake_exec(self):
        self.pick(1, dict(self.picked[0]))
        self.accept()                 # 같은 문서: 막힘
        calls.append(self.result())
        return 0
    monkeypatch.setattr(DC.CompareSelectDialog, "exec", fake_exec)
    n0 = len(win.tabs)
    win.compare(win.cur())
    assert warned == [DC.SAME_DOC] and len(win.tabs) == n0
    # 내용이 같은 다른 파일: 차이 없음 알림, 탭 안 열림
    b = _text_pdf(os.path.join(folder, "같음2.pdf"), [["same"]])
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a_, **k: infos.append(a_[2]))
    _compare(monkeypatch, win, b)
    assert infos == [C.NO_DIFF] and len(win.tabs) == n0


def test_compare_engine_images():
    a, b = fitz.open(), fitz.open()
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False); pm.clear_with(90)
    pa = a.new_page(); pa.insert_text((72, 72), "text"); pa.insert_image(fitz.Rect(100, 100, 200, 200), pixmap=pm)
    pb = b.new_page(); pb.insert_text((72, 72), "text")
    res = C.build(a, b, None)
    assert [(it["kind"], it["what"]) for it in res["items"]] == [("delete", "image")]


# ── 병합 ──────────────────────────────────────────────────────
def test_merge_toc_page_and_password(monkeypatch, win, folder):
    a = _text_pdf(os.path.join(folder, "첫.pdf"), [["A1"], ["A2"]], toc=[[1, "처음", 1], [1, "둘째", 2]])
    b = _text_pdf(os.path.join(folder, "잠김.pdf"), [["B1"]], pw="pw")
    asked = []
    monkeypatch.setattr("app.ui.dlg_password.ask", lambda parent, name, prefs, check, on_presets=None:
                        (asked.append(name), "pw" if check("pw") else None)[1])
    win.open_path(a)

    def fake_exec(self):
        assert self.name.text() == "첫_합치기.pdf"
        self.files.add_paths([b])          # 암호 파일: 넣을 때 암호를 묻는다
        self.toc.setChecked(True)
        self.out.combo.setCurrentIndex(self.out.ORIGINAL)
        self.apply()
        return self.result()
    monkeypatch.setattr(DM.MergeDialog, "exec", fake_exec)
    win.merge()
    assert asked == ["잠김.pdf"]
    out = os.path.join(folder, "첫_합치기.pdf")
    assert win.cur().doc.path == out and win.prefs["merge_toc"] is True
    d = fitz.open(out)
    assert d.page_count == 4 and "목차" in d[0].get_text()
    targets = sorted(lk["page"] for lk in d[0].get_links())
    assert targets == [1, 2]     # 누르면 그 쪽(목차 다음 첫 쪽 · 둘째 쪽)으로
    d.close()
    # 같은 이름이 있으면 _1
    win.doctabs.setCurrentIndex(0); pump(0.05)
    win.merge()
    assert os.path.isfile(os.path.join(folder, "첫_합치기_1.pdf"))


def test_merge_engine_password_error(folder):
    b = _text_pdf(os.path.join(folder, "잠김2.pdf"), [["B"]], pw="pw")
    with pytest.raises(ValueError):
        P.merge([b], os.path.join(folder, "m.pdf"))
    P.merge([b], os.path.join(folder, "m.pdf"), passwords=["pw"], toc_page=True)
    d = fitz.open(os.path.join(folder, "m.pdf"))
    assert d.page_count == 2 and "잠김2" in d[0].get_text()   # 북마크가 없으면 파일 이름으로 목차
    d.close()


def test_file_list_panel_rows(folder):
    a = _text_pdf(os.path.join(folder, "1.pdf"), [["x"]])
    b = _text_pdf(os.path.join(folder, "2.pdf"), [["y"]])
    p = FL.FileListPanel(None, "압축", "PDF (*.pdf)", paths=[a])
    p.add_paths([b, a])                  # 겹친 것은 한 번만
    assert p.paths() == [a, b] and p.tree.topLevelItem(1).text(0) == "2"
    p.set_status(1, "실패", False)
    assert p.status(1) == "실패"
    p._clicked(p.tree.topLevelItem(0), 4)   # 「제거」 칸
    assert p.paths() == [b] and p.tree.topLevelItem(0).text(0) == "1"
    p.clear_all(confirm=False)
    assert p.count() == 0 and not p.empty.isHidden()


# ── 평면화 ────────────────────────────────────────────────────
def test_flatten_new_tab(win, folder):
    src = _text_pdf(os.path.join(folder, "양식.pdf"), [["form"]])
    win.open_path(src); t = win.cur()
    from app.engine import annots as A
    A.rect(t.doc, 0, fitz.Rect(50, 50, 150, 150)); t.changed([0])
    n0 = len(win.tabs)
    win.flatten(t)
    assert len(win.tabs) == n0 + 1
    new = win.cur()
    assert new is not t and new.doc.name == "양식_평면화.pdf"
    assert new.doc.ask_save and new.doc.modified
    assert new.doc.save_hint == os.path.join(folder, "양식_평면화.pdf")
    assert not list(new.doc.fitz[0].annots())   # 주석이 본문으로 굳었다
    assert len(list(t.doc.fitz[0].annots())) == 1   # 원본 탭은 그대로
