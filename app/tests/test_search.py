"""검색 시험(offscreen): 검색 패널 손질 · Ctrl+T 떠 있는 판 · 고급 검색(여러 파일 찾기·「_바꾸기.pdf」).

python -m pytest app/tests/test_search.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_search_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import search_multi as SM  # noqa: E402
from app.ui import dlg_adv_search as ADV  # noqa: E402
from app.ui import search_panel as SP  # noqa: E402
from app.ui import shortcuts as K  # noqa: E402
from app.ui import tab_detach  # noqa: E402
from app.ui import window as Wmod  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def make_pdf(path: str, n: int = 6, word: str = "apple", note: str | None = None, toc: str | None = None, **save):
    """쪽마다 「page N」, 1·3·5쪽에 word, note 를 주면 2쪽에 그 내용의 노트 주석."""
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"page {i + 1}", fontsize=14)
        if i % 2 == 0:
            p.insert_text((72, 200), f"{word} pie and {word} juice", fontsize=12)
    if note:
        d[1].add_text_annot((300, 300), note)
    if toc:
        d.set_toc([[1, "처음", 1], [1, toc, 3]])
    d.save(path, **save); d.close()
    return path


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump(0.05)
    yield w
    for x in list(tab_detach.windows):
        for t in list(x.tabs):
            t.doc.modified = False
        if x is not w:
            x.close()
    d = getattr(w, "_adv_search", None)
    if d is not None:
        d.close()
    w.close(); pump(0.05)


@pytest.fixture()
def boxes(monkeypatch):
    """알림 창을 띄우지 않고 (제목, 글) 을 모은다."""
    got = []

    def rec(kind):
        return staticmethod(lambda parent, title, text, *a, **k: got.append((kind, title, text)) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", rec("info"))
    monkeypatch.setattr(QMessageBox, "warning", rec("warn"))
    monkeypatch.setattr(QMessageBox, "critical", rec("crit"))   # 오류 창에서 멈추지 않게

    def exec_(self):
        got.append(("box", self.windowTitle(), self.text()))
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return got


@pytest.fixture()
def pdf():
    return make_pdf(os.path.join(tempfile.mkdtemp(), "검색.pdf"), note="kiwi 메모 글", toc="kiwi 장")


# ── 검색 패널 ───────────────────────────────────────────────
def test_panel_empty_query_warns(win, pdf, boxes):
    win.open_path(pdf); pump(0.2)
    s = win.cur().search
    s.edit.setText("   ")
    s.edit.returnPressed.emit()
    assert boxes == [("warn", "검색", "검색 텍스트는 비워둘 수 없습니다.")]
    assert s.body.currentWidget() is s.guide and s.guide.text() == SP.GUIDE


def test_panel_comments_only_when_checked(win, pdf, boxes):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); s = t.search
    s.edit.setText("kiwi"); s.run()
    assert s.hits == [] and s.count.text() == "결과 없음"
    s.comments.setChecked(True); s.run()
    assert len(s.results) == 1 and isinstance(s.results[0], SM.AnnotHit) and s.count.text() == "1/1"
    assert "주석" in s.list.item(0).text()
    s._pick(s.list.item(0)); pump(0.05)
    a = next(t.doc.fitz[1].annots())
    assert t.viewer.sel is not None and t.viewer.sel.kind == "annot" and t.viewer.sel.xref == a.xref
    # 칸을 비우면 결과와 강조를 지운다
    s.edit.setText("")
    assert s.hits == [] and t.viewer.search_hits == [] and s.body.currentWidget() is s.guide and boxes == []


def test_panel_names_and_found_color(win, pdf):
    win.open_path(pdf); pump(0.2)
    s = win.cur().search
    assert (s.case.text(), s.whole.text(), s.comments.text()) == ("대/소문자 구분", "단어 단위로", "주석 포함")
    h = SP.found_html("1쪽   red apple  pie", "apple pie", False, start=5)
    assert f"<span style='color:{SP.FOUND_COLOR}'>apple  pie</span>" in h and SP.FOUND_COLOR == "#FF8450"
    assert "<span" not in SP.found_html("1쪽   x", "쪽", False, start=5)   # 앞머리 「1쪽」 은 칠하지 않는다
    s.edit.setText("apple"); s.run()
    assert s.count.text() == "1/6" and s.list.item(0).data(SP.PREFIX_ROLE) == len("1쪽   ")
    s.list.viewport().grab()   # 대리자가 그리다 터지지 않는지


def test_panel_replace_all_message(win, pdf, boxes):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); s = t.search
    s.edit.setText("apple"); s.run(); s.repl.setText("pear"); s.replace_all()
    assert ("info", "바꾸기", "모두 완료되었습니다. 6개를 교체했습니다.") in boxes
    assert not t.doc.search("apple")


# ── 떠 있는 판 ──────────────────────────────────────────────
def test_float_count_prev_next_and_esc(win, boxes):
    p = make_pdf(os.path.join(tempfile.mkdtemp(), "판.pdf"), n=5, word="melon")   # 1·3·5쪽에 두 번씩 = 6
    win.open_path(p); pump(0.2)
    t = win.cur(); v = t.viewer
    win.on_action("search_replace"); pump(0.05)
    f = t.find_float
    assert f is not None and f.isVisible() and f.parentWidget() is t.view_stack
    assert not f.next.isEnabled() and not f.prev.isEnabled()
    f.edit.setText("melon")
    assert f.next.isEnabled() and f.prev.isEnabled()
    v.goto(2, remember=False); pump(0.05)
    f.edit.returnPressed.emit()   # 다음 = 지금 쪽(3쪽)부터
    assert f.where.text() == "3/6" and v.search_hits and v.search_current == 2 and v.search_hits[2][0] == 2
    f.find(1); assert f.where.text() == "4/6"
    f.find(-1); f.find(-1); assert f.where.text() == "2/6"
    win.on_action("search_next"); assert f.where.text() == "3/6"   # F3 도 판이 보이면 판으로
    # 패널과 결과를 나눠 쓰지 않는다
    assert t.search.hits == []
    f.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not f.isVisible() and v.search_hits == [] and v.search_current == -1
    assert boxes == []


def test_float_no_result_message(win, pdf, boxes):
    win.open_path(pdf); pump(0.2)
    t = win.cur()
    win.on_action("search_replace"); f = t.find_float
    f.edit.setText("없는말"); f.find(1)
    assert boxes == [("info", "검색", "검색이 완료되었습니다. 찾고 있는 것과 일치한 항목이 없습니다.")]


def test_float_replace_one_and_all(win, pdf, boxes):
    win.open_path(pdf); pump(0.2)
    t = win.cur(); d = t.doc
    win.on_action("search_replace"); f = t.find_float
    f.mode.setCurrentIndex(1)
    assert f.repl_box.isVisible() and f.where.text() == ""
    f.edit.setText("apple")
    assert not f.rep1.isEnabled()          # 바꿀 글자가 없으면 꺼짐
    f.repl.setText("pear")
    assert f.rep1.isEnabled() and f.repall.isEnabled()
    f.replace_one()                         # 지금 결과가 없으면 이번에는 찾기만
    assert len(d.search("apple")) == 6 and f.current == 0
    f.repl.returnPressed.emit()             # 바꿀 칸 Enter = 바꾸기
    assert len(d.search("apple")) == 5 and len(d.search("pear")) == 1 and d.modified
    assert f.results[f.current].page == 0   # 바로 다음(1쪽 둘째)
    d.undo(); t.changed(None, structural=True)
    assert len(d.search("apple")) == 6 and f.results is None   # 문서가 바뀌면 센 것을 버린다
    assert f.replace_all() == 6
    assert ("info", "바꾸기", "모두 완료되었습니다. 6개를 교체했습니다.") in boxes and not d.search("apple")


def test_float_replace_needs_modify_permission(win, boxes):
    p = make_pdf(os.path.join(tempfile.mkdtemp(), "권한.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="own", user_pw="",
                 permissions=fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY)
    win.open_path(p); pump(0.2)
    t = win.cur()
    win.on_action("search_replace"); f = t.find_float
    f.mode.setCurrentIndex(1); f.edit.setText("apple"); f.repl.setText("pear")
    assert f.next.isEnabled() and not f.rep1.isEnabled() and not f.repall.isEnabled()


# ── 고급 검색 ───────────────────────────────────────────────
@pytest.fixture()
def folder():
    d = tempfile.mkdtemp(prefix="고급검색_")
    make_pdf(os.path.join(d, "하나.pdf"), word="grape", note="grape 메모", toc="grape 장")
    os.makedirs(os.path.join(d, "속"))
    make_pdf(os.path.join(d, "속", "둘.pdf"), n=3, word="grape")
    make_pdf(os.path.join(d, "잠김.pdf"), word="grape", encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with open(os.path.join(d, "그냥.txt"), "w") as fh:
        fh.write("grape")
    return d


def run_adv(win, folder: str, q: str = "grape", **checks):
    win.on_action("search_advanced"); pump(0.05)
    dlg = win._adv_search
    assert dlg.isVisible()
    dlg.r_dir.setChecked(True); dlg.folder.setText(folder); dlg.query.setText(q)
    for k, on in checks.items():
        getattr(dlg, k).setChecked(on)
    assert dlg.go.isEnabled()
    dlg.start(); dlg.wait(); pump(0.1)
    return dlg


def test_adv_folder_tree_and_encrypted_notice(win, folder, boxes):
    dlg = run_adv(win, folder, c_comments=True, c_bm=True)
    tops = [dlg.tree.topLevelItem(k) for k in range(dlg.tree.topLevelItemCount())]
    assert sorted(os.path.basename(it.text(0)) for it in tops) == ["둘.pdf", "하나.pdf"]
    one = next(it for it in tops if it.text(0).endswith("하나.pdf"))
    kinds = [one.child(k).data(0, ADV.FOUND_ROLE).kind for k in range(one.childCount())]
    assert kinds.count("text") == 6 and kinds.count("annot") == 1 and kinds.count("bookmark") == 1
    assert dlg.prog_text.text() == "3개 문서, 12개 결과" and dlg.stop_btn.text() == "새 검색"
    box = [b for b in boxes if b[0] == "box"]
    assert len(box) == 1 and box[0][1] == "검색" and "파일이 암호화 되었습니다" in box[0][2] and "잠김.pdf" in box[0][2]
    assert "파일 1개를" in box[0][2]   # 묶음 수가 아니라 파일 수


def test_adv_result_click_opens_and_highlights(win, folder, boxes):
    dlg = run_adv(win, folder, c_comments=True, c_bm=True)
    top = next(dlg.tree.topLevelItem(k) for k in range(dlg.tree.topLevelItemCount()) if dlg.tree.topLevelItem(k).text(0).endswith("하나.pdf"))
    items = [top.child(k) for k in range(top.childCount())]
    third = [it for it in items if it.data(0, ADV.FOUND_ROLE).kind == "text"][2]
    dlg._clicked(third); pump(0.2)
    t = win.cur()
    assert os.path.basename(t.doc.path) == "하나.pdf"
    assert len(t.viewer.search_hits) == 6 and t.viewer.search_current == 2 and t.viewer.search_hits[2][0] == 2
    dlg._clicked(next(it for it in items if it.data(0, ADV.FOUND_ROLE).kind == "annot")); pump(0.05)
    assert t.viewer.sel is not None and t.viewer.sel.kind == "annot"
    dlg._clicked(next(it for it in items if it.data(0, ADV.FOUND_ROLE).kind == "bookmark")); pump(0.05)
    assert t.side_key == "bookmark" and t.bookmarks.tree.currentItem().text(0) == "grape 장"
    assert len(win.tabs) == 1   # 이미 연 파일은 다시 안 연다
    dlg._clicked(third); assert len(win.tabs) == 1


def test_adv_replace_makes_new_file_only(win, folder, boxes, monkeypatch):
    shown = []
    monkeypatch.setattr(ADV, "reveal", lambda paths: shown.append(paths))
    src = os.path.join(folder, "하나.pdf")
    before = os.path.getmtime(src)
    dlg = run_adv(win, folder)
    assert dlg.to_replace.isEnabled()
    dlg.show_replace()
    assert dlg.files.item(0).text() == "모든 문서" and len(dlg.checked_paths()) == 2
    dlg.files.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dlg.checked_paths() == [] and not dlg.apply_btn.isEnabled()
    one = next(k for k in range(dlg.files.count()) if (dlg.files.item(k).data(Qt.ItemDataRole.UserRole) or "").endswith("하나.pdf"))
    dlg.files.item(one).setCheckState(Qt.CheckState.Checked)
    out_dir = tempfile.mkdtemp(prefix="바꾼결과_")
    dlg.r_to.setText("lemon"); dlg.rep_dir.setText(out_dir); dlg.rep_open.setChecked(True)
    made = dlg.apply_replace()
    assert [os.path.basename(m) for m in made] == ["하나_바꾸기.pdf"] and shown == [made]
    assert ("info", "바꾸기", "1 파일이 교체되었습니다") in boxes
    assert os.path.getmtime(src) == before and "grape" in fitz.open(src)[0].get_text()
    g = fitz.open(made[0]); assert "lemon" in g[0].get_text() and "grape" not in g[0].get_text(); g.close()
    # 겹치면 _번호
    made2 = dlg.apply_replace()
    assert os.path.basename(made2[0]) == "하나_바꾸기_1.pdf"


def test_adv_checks_remembered(win, folder, boxes):
    run_adv(win, folder, c_case=True, c_attach=True)
    saved = Wmod.load_settings()["prefs"]["adv_search"]
    assert saved["case"] and saved["attach"] and not saved["whole"] and saved["never_fail"] is False
    win._adv_search.close(); win._adv_search = None
    w2 = MainWindow(); w2.show(); pump(0.05)
    try:
        w2.on_action("search_advanced"); d2 = w2._adv_search
        assert d2.c_case.isChecked() and d2.c_attach.isChecked() and not d2.c_whole.isChecked()
        assert d2.query.text() == ""   # 검색어는 기억하지 않는다
        assert d2.r_dir.isChecked() and not d2.r_cur.isEnabled()   # 문서가 없으면 폴더만
        d2.close()
    finally:
        w2.close()


def test_adv_attachment_pdf_searched(win, boxes):
    d = tempfile.mkdtemp(prefix="첨부검색_")
    f = fitz.open(); f.new_page().insert_text((72, 72), "겉 글")
    inner = fitz.open(); inner.new_page().insert_text((72, 72), "inside papaya"); f.embfile_add("안.pdf", inner.tobytes())
    f.save(os.path.join(d, "겉.pdf")); f.close()
    dlg = run_adv(win, d, q="papaya", c_attach=False)
    assert dlg.tree.topLevelItemCount() == 0
    dlg.show_cond(); dlg.c_attach.setChecked(True); dlg.start(); dlg.wait(); pump(0.1)
    top = dlg.tree.topLevelItem(0)
    f0 = top.child(0).data(0, ADV.FOUND_ROLE)
    assert f0.attach == "안.pdf" and top.child(0).text(0).startswith("[안.pdf]")
    dlg._clicked(top.child(0)); pump(0.2)
    assert os.path.basename(win.cur().doc.path) == "안.pdf" and win.cur().viewer.search_hits


def test_adv_current_and_open_docs(win, pdf, folder, boxes):
    win.open_path(pdf); win.open_path(os.path.join(folder, "하나.pdf")); pump(0.2)
    win.on_action("search_advanced"); dlg = win._adv_search
    dlg.r_open.setChecked(True); dlg.query.setText("apple"); dlg.start(); dlg.wait(); pump(0.1)
    assert dlg.tree.topLevelItemCount() == 1 and dlg.tree.topLevelItem(0).text(0).endswith("검색.pdf")
    assert dlg.scope.text() == "모든 열린 문서"
    dlg.show_cond(); dlg.r_cur.setChecked(True); dlg.start(); dlg.wait(); pump(0.1)
    assert dlg.tree.topLevelItemCount() == 0   # 지금 문서(하나.pdf)에는 apple 이 없다


# ── 단축키 ─────────────────────────────────────────────────
def test_shortcuts_and_about(win, monkeypatch):
    assert K.DEFAULTS["search_advanced"] == ["Ctrl+Shift+F"] and K.DEFAULTS["about"] == ["Shift+Alt+F11"]
    assert K.clashes(K.current({})) == {}
    called = []
    monkeypatch.setattr(win, "show_help", lambda: called.append(1))
    win.on_action("about")
    assert called == [1]


def test_replace_file_inside_pdf_attachment():
    d = tempfile.mkdtemp(prefix="첨부바꾸기_")
    f = fitz.open(); f.new_page().insert_text((72, 72), "outside papaya")
    inner = fitz.open(); inner.new_page().insert_text((72, 72), "inside papaya"); f.embfile_add("안.pdf", inner.tobytes())
    src = os.path.join(d, "겉.pdf"); f.save(src); f.close()
    out, n, st = SM.replace_file(src, "papaya", "mango", d, attach=True)
    assert st == SM.OK and n == 2 and os.path.basename(out) == "겉_바꾸기.pdf"
    g = fitz.open(out)
    (name, data), = SM.pdf_attachments(g)
    assert name == "안.pdf" and "mango" in fitz.open("pdf", data)[0].get_text() and "mango" in g[0].get_text()
    assert "papaya" in fitz.open(src)[0].get_text()
