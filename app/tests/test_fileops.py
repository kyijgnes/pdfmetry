"""열기·저장·생성 시험(창을 offscreen 으로 띄움): 고친 것 없는 저장 · 읽기 전용 → 다른 이름으로 · 다른 창에 열린 파일 ·
사전 설정 비밀번호 · 자동 저장 복구 · 빈 PDF · 파일에서 생성 · 클립보드 글자.

python -m pytest app/tests/test_fileops.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_fileops_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import convert as C  # noqa: E402
from app.engine.document import Document, FileLocked, read_only  # noqa: E402
from app.ui import dlg_password as PW  # noqa: E402
from app.ui import fileops as F  # noqa: E402
from app.ui import window as W  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.1):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 2, pw: str | None = None):
    d = fitz.open()
    for i in range(n):
        d.new_page().insert_text((72, 100), f"page {i + 1}")
    if pw:
        d.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=pw, owner_pw=pw)
    else:
        d.save(path)
    d.close()
    return path


@pytest.fixture()
def win():
    # 설정 파일을 읽지도 남기지도 않는다(같은 APPDATA 를 쓰는 다른 시험에 「같은 창」 끔 같은 값이 새지 않게)
    w = MainWindow(settings={}); w.show(); pump(0.05)
    yield w
    for x in F.windows(w):
        for t in x.tabs:
            t.doc.modified = False
    for x in F.windows(w):
        x.close()
    pump(0.05)
    if os.path.exists(W.SETTINGS):
        os.remove(W.SETTINGS)


@pytest.fixture()
def folder():
    return tempfile.mkdtemp(prefix="fileops_")


# ── 이름·경로 ─────────────────────────────────────────────────
def test_names_and_paths():
    assert F.copy_name(r"C:\a\보고서.pdf") == r"C:\a\보고서-복사.pdf"
    assert F.unique_title(set()) == "제목 없음.pdf"
    assert F.unique_title({"제목 없음.pdf", "제목 없음-2.pdf"}) == "제목 없음-3.pdf"
    assert F.path_problem("C:\\" + "가" * 300 + ".pdf") == "오류! 경로가 너무 깁니다."
    assert F.path_problem(r"C:\a\b?c.pdf") == "경로가 잘못되었습니다."
    assert F.path_problem(r"C:\a\보통.pdf") is None
    assert F.created_name([r"C:\x\a.docx"]) == "a.pdf"
    assert F.created_name([r"C:\x\a.docx", r"C:\x\b.png"]) == "a 합치기.pdf"


def test_blank_spec():
    assert F.blank_spec({}) == ("제목 없음", 1, 595, 842)
    assert F.blank_spec({"blank": {"name": "빈문서", "count": 3, "size": "A4", "landscape": True}}) == ("빈문서", 3, 842, 595)
    assert F.blank_spec({"blank": {"size": "custom", "w": 300, "h": 400}})[2:] == (300, 400)


# ── 저장 ──────────────────────────────────────────────────────
def test_unmodified_save_keeps_mtime(win, folder):
    p = make_pdf(os.path.join(folder, "그대로.pdf"))
    os.utime(p, (1_600_000_000, 1_600_000_000))
    win.open_path(p); pump()
    assert win.save(win.cur()) is True
    assert os.path.getmtime(p) == 1_600_000_000


def test_read_only_goes_to_save_as(win, folder, monkeypatch):
    p = make_pdf(os.path.join(folder, "읽기전용.pdf"))
    win.open_path(p); pump()
    t = win.cur(); t.doc.modified = True
    os.chmod(p, stat.S_IREAD)
    try:
        assert read_only(p)
        with pytest.raises(FileLocked):
            t.doc.save(p)
        asked = []
        monkeypatch.setattr(F, "ask_save_as_instead", lambda parent, why: asked.append(why) or True)
        out = os.path.join(folder, "새이름.pdf")
        offered = []
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (offered.append(a[2]), (out, ""))[1])
        assert win.save(t) is True
        assert asked and "읽기 전용" in asked[0]
        assert offered == [F.copy_name(p)]   # 기본 이름 「-복사」
        assert os.path.isfile(out) and t.doc.path == out
    finally:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


def test_save_as_cancel_on_locked(win, folder, monkeypatch):
    p = make_pdf(os.path.join(folder, "잠김.pdf"))
    win.open_path(p); pump()
    t = win.cur(); t.doc.modified = True
    os.chmod(p, stat.S_IREAD)
    try:
        monkeypatch.setattr(F, "ask_save_as_instead", lambda parent, why: False)
        assert win.save(t) is False and t.doc.modified
    finally:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


# ── 열기 ──────────────────────────────────────────────────────
def test_open_in_other_window_brings_it(win, folder):
    p = make_pdf(os.path.join(folder, "다른창.pdf"))
    w2 = MainWindow(settings=win.settings); w2.show(); w2.open_path(p); pump()
    win.open_path(p); pump()
    assert not win.tabs and len(w2.tabs) == 1


def test_same_window_off_opens_new_window(win, folder):
    a = make_pdf(os.path.join(folder, "a.pdf")); b = make_pdf(os.path.join(folder, "b.pdf"))
    win.prefs["same_window"] = False
    win.open_path(a); win.open_path(b); pump()
    assert len(win.tabs) == 1
    assert any(len(w.tabs) == 1 and w.tabs[0].doc.path == os.path.abspath(b) for w in F.windows(win) if w is not win)


def test_preset_password_opens_without_asking(win, folder, monkeypatch):
    p = make_pdf(os.path.join(folder, "암호.pdf"), pw="s3cret")
    PW.set_presets(win.prefs, ["wrong", "s3cret"]); win.prefs["preset_pw_auto"] = True
    monkeypatch.setattr(PW.PasswordDialog, "exec", lambda self: pytest.fail("창을 띄우면 안 됨"))
    win.open_path(p); pump()
    assert len(win.tabs) == 1
    W.save_settings(win.settings)
    with open(W.SETTINGS, encoding="utf-8") as f:
        assert "s3cret" not in f.read()   # 설정 파일에 평문 없음
    assert PW.presets(win.prefs) == ["wrong", "s3cret"]


def test_password_dialog_stays_on_wrong():
    dlg = PW.PasswordDialog(None, "x.pdf", {}, lambda pw: pw == "ok")
    dlg.edit.setText("no")
    assert dlg.try_open() is False and dlg.err.text() == PW.PasswordDialog.WRONG and dlg.password is None
    dlg.edit.setText("ok")
    assert dlg.try_open() is True and dlg.password == "ok"


def test_open_fdf_imports_into_current(win, folder):
    p = make_pdf(os.path.join(folder, "주석.pdf"))
    d = fitz.open(p); d[0].add_text_annot((100, 100), "메모"); d.save(os.path.join(folder, "src.pdf")); d.close()
    from app.engine import annot_xfdf as X
    src = Document(os.path.join(folder, "src.pdf"))
    raw, n, _ = X.to_xfdf(src, "src.pdf"); src.close()
    xf = os.path.join(folder, "notes.xfdf"); open(xf, "wb").write(raw)
    win.open_path(p); pump()
    win.open_path(xf); pump()
    assert len(win.tabs) == 1 and len(list(win.cur().doc.page(0).annots())) == n


# ── 자동 저장 복구 ────────────────────────────────────────────
def test_autosave_and_restore(win, folder):
    p = make_pdf(os.path.join(folder, "복구.pdf"))
    win.open_path(p); pump()
    t = win.cur(); t.doc.modified = True
    assert win.autosave.write_all() == 1
    items = F.pending()
    assert len(items) == 1 and items[0][0]["path"] == os.path.abspath(p)
    # 앱이 튕겨 다시 켠 것처럼: 다른 프로그램이 남긴 사본으로 바꿔 두고 새 창이 연다
    meta_path = os.path.splitext(items[0][1])[0] + ".json"
    meta = json.load(open(meta_path, encoding="utf-8")); meta["pid"] = -1
    json.dump(meta, open(meta_path, "w", encoding="utf-8"))
    F.sweep(set())   # 지금 프로그램 것만 지우므로 남는다
    assert len(F.pending()) == 1
    t.doc.modified = False
    win.close_tabs(list(win.tabs)); pump()
    w2 = MainWindow(settings={}); pump()
    assert w2.autosave.offer_restore(ask=lambda n: True) == 1
    assert w2.cur().doc.modified and w2.cur().doc.save_hint == os.path.abspath(p)
    assert not F.pending()
    w2.cur().doc.modified = False; w2.close()


def test_save_drops_recovery_copy(win, folder):
    p = make_pdf(os.path.join(folder, "지움.pdf"))
    win.open_path(p); pump()
    t = win.cur(); t.doc.modified = True
    win.autosave.write_all()
    assert F.pending()
    win.save(t)
    assert not F.pending()


# ── 생성 ──────────────────────────────────────────────────────
def test_blank_without_dialog(win):
    win.prefs["blank"] = {"count": 2, "size": "A4"}
    win.create_blank(); win.create_blank(); pump()
    assert [t.doc.name for t in win.tabs] == ["제목 없음.pdf", "제목 없음-2.pdf"]
    assert win.tabs[0].doc.page_count == 2 and not win.tabs[0].doc.path


def test_from_files_text_like(folder):
    paths = []
    for name, body in (("a.log", "로그 한 줄\n" * 200), ("b.xml", "<a>값</a>"), ("c.ini", "[x]\nk=v")):
        p = os.path.join(folder, name); open(p, "w", encoding="utf-8").write(body); paths.append(p)
    out = C.from_files(paths, os.path.join(folder, "out.pdf"))
    d = fitz.open(out)
    assert d.page_count >= 3
    d.close()


def test_create_from_paths_opens_unsaved(win, folder):
    a = make_pdf(os.path.join(folder, "첫.pdf")); b = make_pdf(os.path.join(folder, "둘.pdf"))
    win.create_from_paths([a, b]); pump(0.3)
    t = win.cur()
    assert t.doc.name == "첫 합치기.pdf" and t.doc.page_count == 4 and t.doc.modified
    assert t.doc.save_hint == os.path.join(folder, "첫 합치기.pdf")


def test_clipboard_long_text_spans_pages(win):
    md = QMimeData(); md.setText("긴 글자 한 줄입니다\n" * 400)
    QApplication.clipboard().setMimeData(md)
    assert F.clipboard_kind(QApplication.clipboard().mimeData()) == "text"
    win.create_from_clipboard(); pump()
    assert win.cur().doc.page_count > 1


def test_clipboard_kind_files(folder):
    p = make_pdf(os.path.join(folder, "f.pdf"))
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(p)])
    assert F.clipboard_kind(md) == "files"
    assert F.clipboard_kind(QMimeData()) is None


def test_close_many_asks_from_menu(win, folder, monkeypatch):
    from app.ui import window as W
    for n in ("x.pdf", "y.pdf", "z.pdf", "w.pdf"):
        win.open_path(make_pdf(os.path.join(folder, n)))
    pump()
    answer = [None]
    monkeypatch.setattr(W, "ask_close_many", lambda *a, **k: answer[0])
    assert win.close_tabs(list(win.tabs), confirm=True) is False and len(win.tabs) == 4   # 그만둠
    answer[0] = "current"   # 현재 탭 닫기: 지금 탭 하나만
    cur = win.cur()
    assert win.close_tabs(list(win.tabs), confirm=True) is False and len(win.tabs) == 3 and cur not in win.tabs
    answer[0] = "always"    # 항상 모든 탭 닫기: 다 닫고 다음부터 묻지 않음
    assert win.close_tabs(list(win.tabs), confirm=True) and not win.tabs
    assert win.prefs["confirm_close_many"] is False
