"""라운드 15 라: 일괄 편집 창 · 일괄 엔진(원본 폴더 · 이름 비키기 · 취소 · 새 작업) · 데이터 추출 CSV · 옛 창 정리.

python -m pytest app/tests/test_batch_r15.py
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_batch_r15_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import deco_store as S  # noqa: E402
from app.engine import tools as T  # noqa: E402
from app.ui import dialogs as D  # noqa: E402
from app.ui import dlg_batch as DB  # noqa: E402
from app.ui import dlg_convert as DC  # noqa: E402
from app.ui import dlg_data_extract as DX  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _pdf(path: str, pages: int = 2, blank_at: tuple[int, ...] = (), field: str | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    d = fitz.open()
    for k in range(pages):
        p = d.new_page()
        if k not in blank_at:
            p.insert_text((72, 80), f"page {k + 1}", fontsize=12)
    if field:
        w = fitz.Widget()
        w.field_name = "이름"; w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 500, 300, 530); w.field_value = field
        d[0].add_widget(w)
    d.save(path)
    d.close()
    return path


@pytest.fixture()
def folder():
    return tempfile.mkdtemp(prefix="batch_r15_")


@pytest.fixture(autouse=True)
def _no_settings_file(monkeypatch):
    """창 시험이 저장 위치 값을 설정 파일에 남겨 다른 시험 파일(변환 창 기억)을 흔들지 않게."""
    from app.ui import window as W
    monkeypatch.setattr(W, "save_settings", lambda *_a, **_k: None)


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump(0.05)


# ── 엔진 ──────────────────────────────────────────────────────
def test_original_folder_per_file_and_unique_names(folder):
    a = _pdf(os.path.join(folder, "가", "문서.pdf"))
    b = _pdf(os.path.join(folder, "나", "문서.pdf"))
    open(os.path.join(folder, "가", "문서.txt"), "w").close()   # 같은 이름이 이미 있음
    res = T.batch([{"op": "convert", "inputs": [a, b], "out_dir": "", "options": {"fmt": "txt"}}])
    assert [r["ok"] for r in res] == [True, True], res
    assert res[0]["output"] == os.path.join(folder, "가", "문서_1.txt")
    assert res[1]["output"] == os.path.join(folder, "나", "문서.txt")


def test_same_name_twice_in_one_folder(folder):
    a = _pdf(os.path.join(folder, "가", "같음.pdf"))
    b = _pdf(os.path.join(folder, "나", "같음.pdf"))
    out = os.path.join(folder, "결과")
    res = T.batch([{"op": "convert", "inputs": [a, b], "out_dir": out, "options": {"fmt": "txt"}}])
    assert [os.path.basename(r["output"]) for r in res] == ["같음.txt", "같음_1.txt"]


def test_out_name_taken_set(folder):
    taken: set = set()
    p1 = T._out_name("x/a.pdf", folder, "_압축", ".pdf", taken)
    p2 = T._out_name("y/a.pdf", folder, "_압축", ".pdf", taken)
    assert os.path.basename(p1) == "a_압축.pdf" and os.path.basename(p2) == "a_압축_1.pdf"


def test_cancel_marks_rest(folder):
    paths = [_pdf(os.path.join(folder, f"{k}.pdf")) for k in range(4)]
    stop = threading.Event()
    seen = []

    def on_row(k, rec):
        if rec is not None:
            seen.append(k)
            if k == 0:
                stop.set()
    res = T.batch([{"op": "blank", "inputs": paths, "out_dir": "", "options": {}}], stop=stop, on_row=on_row)
    assert seen == [0, 1, 2, 3]
    assert [r["cancelled"] for r in res] == [False, True, True, True]
    assert all(r["error"] == T.CANCELLED for r in res[1:])


def test_blank_pages_deleted(folder):
    p = _pdf(os.path.join(folder, "빈쪽.pdf"), pages=4, blank_at=(1, 3))
    res = T.batch([{"op": "blank", "inputs": [p], "out_dir": "", "options": {}}])
    assert res[0]["ok"], res
    assert res[0]["output"].endswith("빈쪽_공백삭제.pdf") and fitz.open(res[0]["output"]).page_count == 2
    q = _pdf(os.path.join(folder, "꽉참.pdf"))
    r2 = T.batch([{"op": "blank", "inputs": [q], "out_dir": "", "options": {}}])
    assert not r2[0]["ok"] and r2[0]["error"] == "공백 페이지가 없습니다"


@pytest.mark.parametrize("kind", ["bates", "header_footer", "background", "watermark"])
def test_deco_templates_three_files(folder, kind):
    from app.engine import deco as E
    tpl = S.builtins(kind)[1]
    paths = [_pdf(os.path.join(folder, f"{kind}{k}.pdf")) for k in range(3)]
    res = T.batch([{"op": kind, "inputs": paths, "out_dir": "",
                    "options": {"settings": tpl["settings"], "range": tpl["range"]}}])
    assert all(r["ok"] for r in res), res
    for r in res:
        d = fitz.open(r["output"])
        assert E.decorations(d, kind), r["output"]
        d.close()


def test_saved_template_used(folder):
    s = dict(S.builtins("watermark")[0]["settings"]); s["text"] = "일괄시험"
    t = S.save("watermark", "일괄 시험", s)
    try:
        p = _pdf(os.path.join(folder, "w.pdf"))
        res = T.batch([{"op": "watermark", "inputs": [p], "out_dir": "", "options": {"settings": t["settings"], "range": t["range"]}}])
        assert res[0]["ok"] and "일괄시험" in fitz.open(res[0]["output"])[0].get_text()
    finally:
        S.remove("watermark", t["id"])


def test_export_data_one_csv(folder):
    a = _pdf(os.path.join(folder, "가", "a.pdf"), field="김영")
    b = _pdf(os.path.join(folder, "나", "b.pdf"), field="박영")
    c = _pdf(os.path.join(folder, "나", "c.pdf"))   # 양식 없음
    res = T.batch([{"op": "export_data", "inputs": [a, b, c], "out_dir": "", "options": {"name": "모음.csv"}}])
    assert [r["ok"] for r in res] == [True, True, False]
    assert res[2]["error"] == T.FORM_NONE
    out = res[0]["output"]
    assert out == os.path.join(folder, "가", "모음.csv") and all(r["output"] == out for r in res)
    rows = list(csv.reader(open(out, encoding="utf-8-sig")))
    assert rows[0] == ["파일", "이름", "비고"]
    assert rows[1][:2] == ["a.pdf", "김영"] and rows[2][:2] == ["b.pdf", "박영"] and len(rows) == 4


def test_extract_data_doc_csv(folder):
    p = _pdf(os.path.join(folder, "f.pdf"), field="김영")
    d = fitz.open(p)
    out = T.extract_data_doc(d, os.path.join(folder, "f.csv"), "f.pdf")
    assert list(csv.reader(open(out, encoding="utf-8-sig"))) == [["파일", "이름", "비고"], ["f.pdf", "김영", ""]]
    with pytest.raises(T.EngineError):
        T.extract_data_doc(fitz.open(_pdf(os.path.join(folder, "g.pdf"))), os.path.join(folder, "g.csv"), "g.pdf")


def test_create_per_file(folder):
    from PIL import Image
    img = os.path.join(folder, "그림.png")
    Image.new("RGB", (40, 30), "red").save(img)
    res = T.batch([{"op": "create", "inputs": [img], "out_dir": "", "options": {}}])
    assert res[0]["ok"] and res[0]["output"] == os.path.join(folder, "그림.pdf")


# ── 옛 창 정리 ────────────────────────────────────────────────
def test_old_dialogs_gone():
    for name in ("OcrDialog", "CompressDialog", "CompareDialog", "BatchDialog", "BATCH_FORMATS", "COMPRESS_CHOICES"):
        assert not hasattr(D, name), name
    assert hasattr(D, "FileListDialog")   # 「파일에서 생성」 이 쓴다
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    for f in os.listdir(root):
        if f.endswith(".py"):
            src = open(os.path.join(root, f), encoding="utf-8").read()
            for name in ("D.OcrDialog", "D.CompressDialog", "D.CompareDialog", "D.BatchDialog", "BATCH_FORMATS", "COMPRESS_CHOICES"):
                assert name not in src, (f, name)


# ── 창 ────────────────────────────────────────────────────────
def test_convert_tab_batch_opens_convert_with_current(monkeypatch, win, folder):
    src = _pdf(os.path.join(folder, "지금.pdf"))
    win.open_path(src); pump(0.1)
    seen = {}
    monkeypatch.setattr(DB.BatchDialog, "exec", lambda self: (seen.update(task=self.task(), paths=self.files.paths()), 0)[1])
    win.ribbon.set_tab("convert")
    win.on_action("batch")
    assert seen == {"task": "convert", "paths": [src]}
    win.ribbon.set_tab("tools"); seen.clear()
    win.prefs["batch_task"] = "blank"
    win.on_action("batch")
    assert seen == {"task": "blank", "paths": []}


def test_dialog_apply_convert_original_folder(monkeypatch, win, folder):
    a = _pdf(os.path.join(folder, "가", "하나.pdf"))
    b = _pdf(os.path.join(folder, "나", "둘.pdf"))
    monkeypatch.setattr(DB.BatchDialog, "done_box", lambda self, *a: None)
    dlg = DB.BatchDialog(win, "convert", [a, b], langs_fn=lambda: [])
    dlg.fmt.setCurrentIndex(dlg.fmt.findData("txt"))
    dlg.out_panel().combo.setCurrentIndex(0)
    res = dlg.apply()
    assert [r["ok"] for r in res] == [True, True]
    assert os.path.isfile(os.path.join(folder, "가", "하나.txt")) and os.path.isfile(os.path.join(folder, "나", "둘.txt"))
    assert dlg.files.status(0) == "완료" and win.prefs["batch_fmt"] == "txt" and win.prefs["batch_task"] == "convert"
    assert win.settings["convert_out"]["custom"] is False
    dlg.close()


def test_dialog_cancel_marks_rest(monkeypatch, win, folder):
    paths = [_pdf(os.path.join(folder, f"{k}.pdf")) for k in range(4)]
    monkeypatch.setattr(DB.BatchDialog, "done_box", lambda self, *a: None)
    slow = T._batch_one

    def one(*a, **k):
        time.sleep(0.3)
        return slow(*a, **k)
    monkeypatch.setattr(T, "_batch_one", one)
    dlg = DB.BatchDialog(win, "blank", paths)
    dlg.outs["batch_out"].combo.setCurrentIndex(0)
    first = {"done": False}

    def started(r):
        if r == 0 and not first["done"]:
            first["done"] = True
            assert dlg.apply_btn.text() == "취소"
            dlg.apply_btn.click()
    orig = dlg.files.set_status

    def spy(r, text, ok):
        orig(r, text, ok)
        if text == "처리 중":
            started(r)
    dlg.files.set_status = spy
    dlg.apply()
    assert [dlg.files.status(k) for k in range(4)][1:] == ["취소됨"] * 3
    assert dlg.apply_btn.text() == "적용"
    dlg.close()


def test_dialog_deco_and_security_and_export(monkeypatch, win, folder):
    monkeypatch.setattr(DB.BatchDialog, "done_box", lambda self, *a: None)
    paths = [_pdf(os.path.join(folder, f"f{k}.pdf"), field=f"값{k}") for k in range(3)]
    dlg = DB.BatchDialog(win, "bates", paths)
    dlg.outs["batch_out"].combo.setCurrentIndex(0)
    assert dlg.deco_combos["bates"].count() >= 2
    assert all(r["ok"] for r in dlg.apply())
    # 보안: 설정 전에는 막힘
    dlg.tasks.setCurrentRow([k for k, _, _ in DB.TASKS].index("encrypt"))
    assert dlg.options() == DB.NO_SECURITY
    dlg.security = {"user_pw": "a", "owner_pw": "", "method": "aes128"}
    res = dlg.apply()
    assert all(r["ok"] for r in res) and fitz.open(res[0]["output"]).needs_pass
    # 데이터 내보내기: CSV 한 장
    dlg.tasks.setCurrentRow([k for k, _, _ in DB.TASKS].index("export_data"))
    dlg.data_name.setText("모음")
    res = dlg.apply()
    out = os.path.join(folder, "모음.csv")
    assert all(r["ok"] for r in res) and res[0]["output"] == out
    assert len(list(csv.reader(open(out, encoding="utf-8-sig")))) == 4
    dlg.close()


def test_window_extract_data_current(monkeypatch, win, folder):
    src = _pdf(os.path.join(folder, "양식.pdf"), field="김영")
    win.open_path(src); pump(0.1)
    got = {}
    monkeypatch.setattr(DX, "done_box", lambda w, out: got.setdefault("out", out))
    monkeypatch.setattr(DX.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (a[2], "")))
    win.on_action("extract_data")
    assert got["out"] == os.path.join(folder, "양식_데이터.csv") and os.path.isfile(got["out"])
    # 양식 없는 문서는 알PDF 문구
    plain = _pdf(os.path.join(folder, "그냥.pdf"))
    win.open_path(plain); pump(0.1)
    said = {}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, m, *a: said.setdefault("m", m)))
    win.on_action("extract_data")
    assert said["m"] == T.FORM_NONE


def test_convert_dialog_uses_output_panel(folder):
    from app.ui.file_list import OutputPanel
    doc_path = _pdf(os.path.join(folder, "c.pdf"))
    store = {}
    dlg = DC.ConvertDialog(None, 2, 0, "docx", "c.pdf", doc_path, {}, store, {}, lambda: [])
    assert isinstance(dlg.out, OutputPanel) and dlg.folder.text() == folder
    custom = os.path.join(folder, "지정")
    dlg.where.setCurrentIndex(1); dlg.folder.setText(custom)
    dlg.where.setCurrentIndex(0); assert dlg.folder.text() == folder
    dlg.where.setCurrentIndex(1); assert dlg.folder.text() == custom


def test_every_task_page_builds(win, folder):
    dlg = DB.BatchDialog(win, "convert", [_pdf(os.path.join(folder, "a.pdf"))], langs_fn=lambda: ["ko"])
    keys = [k for k, _, _ in DB.TASKS]
    assert len(keys) == 12
    for i, k in enumerate(keys):
        dlg.tasks.setCurrentRow(i)
        assert dlg.task() == k and dlg.title.text() == DB.TASK_NAMES[k]
        assert (dlg.out_panel() is None) == (k == "print")
        opts = dlg.options()
        assert isinstance(opts, dict) or (k == "encrypt" and opts == DB.NO_SECURITY), (k, opts)
    dlg.tasks.setCurrentRow(keys.index("create"))
    assert not dlg.files.only_pdf
    dlg.tasks.setCurrentRow(keys.index("ocr"))
    assert dlg.files.only_pdf and dlg.options()["lang"] == ["ko"]
    dlg.close()
