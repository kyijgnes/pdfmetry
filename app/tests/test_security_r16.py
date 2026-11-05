"""라운드 16 다: 암호화 창 · 권한 암호 띠 · 교정(안내·적용·검색 & 교정) · 숨은 데이터 열 가지.
python -m pytest app/tests/test_security_r16.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_security_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import tools as T  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui import dlg_redact as DR  # noqa: E402
from app.ui import dlg_security as SEC  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

P8 = T.PERM_ALL


def pump(sec: float = 0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _pdf(path: str, lines=("Hello secret 010-1234-5678",), **save) -> str:
    d = fitz.open()
    p = d.new_page()
    for k, ln in enumerate(lines):
        p.insert_text((72, 80 + 20 * k), ln, fontsize=12)
    d.set_metadata({"title": "제목", "author": "글쓴이"})
    d.save(path, **save); d.close()
    return path


@pytest.fixture()
def folder():
    return tempfile.mkdtemp(prefix="r16_sec_")


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump()
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump()


def _save_with(doc: Document, dlg: SEC.EncryptDialog, out: str):
    assert dlg.save_btn.isEnabled()
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    doc.security = {} if dlg.removes() else dlg.values()
    doc.save(out)


# ── 권한 단계 ───────────────────────────────────────────────
def test_perm_args_levels():
    a = SEC.perm_args(1, 2, False)
    assert a["allow_print"] and not a["allow_print_hq"] and a["allow_forms"] and not a["allow_annotate"]
    assert not a["allow_modify"] and not a["allow_assemble"] and not a["allow_copy"] and a["allow_accessibility"]
    full = SEC.perm_args(2, 4, True)
    assert all(full.values())
    for pr in range(3):
        for ch in range(5):
            for cp in (False, True):
                p = sum(f for f, k in ((4, "allow_print"), (2048, "allow_print_hq"), (16, "allow_copy"), (8, "allow_modify"),
                                       (32, "allow_annotate"), (256, "allow_forms"), (1024, "allow_assemble"))
                        if SEC.perm_args(pr, ch, cp)[k])
                assert T.perm_levels(p) == (pr, ch, cp)


# ── 암호화 창 ───────────────────────────────────────────────
def test_open_password_only_gives_all_permissions(folder):
    src = _pdf(os.path.join(folder, "a.pdf"))
    d = Document(src)
    dlg = SEC.EncryptDialog(None, None, SEC.current(d))
    assert not dlg.use_open.isChecked() and not dlg.use_owner.isChecked()
    assert not dlg.method_box.isEnabled() and not dlg.owner_pw.isEnabled() and not dlg.print_level.isEnabled()
    dlg.use_open.setChecked(True)
    assert not dlg.save_btn.isEnabled()        # 체크한 칸에 암호가 없으면 저장 못 함
    dlg.open_pw.setText("open1")
    out = os.path.join(folder, "o.pdf")
    _save_with(d, dlg, out)
    g = fitz.open(out)
    assert g.needs_pass and g.authenticate("open1") and (g.permissions & P8) == P8


def test_permission_levels_saved(folder):
    src = _pdf(os.path.join(folder, "a.pdf"))
    d = Document(src)
    dlg = SEC.EncryptDialog(None, None, SEC.current(d))
    assert dlg.print_level.currentIndex() == 0 and dlg.change_level.currentIndex() == 0   # 처음은 인쇄 제한·변경 불가
    dlg.use_owner.setChecked(True); dlg.owner_pw.setText("owner1")
    dlg.print_level.setCurrentIndex(1)                    # 저해상도
    dlg.change_level.setCurrentIndex(2)                   # 양식 채우기와 서명
    dlg.copy.setChecked(False)
    out = os.path.join(folder, "p.pdf")
    _save_with(d, dlg, out)
    g = fitz.open(out)
    assert not g.needs_pass
    want = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY
    assert g.permissions & P8 == want
    assert T.is_restricted(g)


def test_both_off_removes_password(folder):
    src = _pdf(os.path.join(folder, "a.pdf"), encryption=fitz.PDF_ENCRYPT_AES_128, user_pw="u1", owner_pw="o1")
    d = Document(src, password="u1")
    dlg = SEC.EncryptDialog(None, T.encryption_method(d.fitz), SEC.current(d))
    assert dlg.use_open.isChecked()
    dlg.use_open.setChecked(False); dlg.use_owner.setChecked(False)
    assert dlg.removes() and dlg.save_btn.isEnabled() and not dlg.method_box.isEnabled()
    out = os.path.join(folder, "n.pdf")
    _save_with(d, dlg, out)
    g = fitz.open(out)
    assert not g.needs_pass and not (g.metadata or {}).get("encryption")


def test_fake_dots_keep_same_password(folder):
    src = _pdf(os.path.join(folder, "a.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="열기암호", owner_pw="own2",
               permissions=fitz.PDF_PERM_PRINT)
    d = Document(src, password="열기암호")
    dlg = SEC.EncryptDialog(None, T.encryption_method(d.fitz), SEC.current(d))
    assert dlg.use_open.isChecked() and dlg.open_pw.text() == SEC.FAKE
    assert dlg.methods["aes256"].isChecked()
    dlg.use_owner.setChecked(False)   # 권한 암호는 모르는 채(열기 암호로 열었다)
    out = os.path.join(folder, "f.pdf")
    _save_with(d, dlg, out)
    g = fitz.open(out)
    assert g.needs_pass and g.authenticate("열기암호")
    # 다시 열어도 가짜 글자, 손대지 않으면 그대로
    d2 = Document(out, password="열기암호")
    dlg2 = SEC.EncryptDialog(None, None, SEC.current(d2))
    assert dlg2.open_pw.text() == SEC.FAKE
    assert dlg2.values()["user_pw"] == "열기암호"
    dlg2.open_pw.setText(""); dlg2._touch("open"); dlg2.open_pw.setText("new")
    assert dlg2.values()["user_pw"] == "new"


def test_same_passwords_rejected(folder, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen.append(a[2]))
    dlg = SEC.EncryptDialog(None)
    dlg.use_open.setChecked(True); dlg.open_pw.setText("same")
    dlg.use_owner.setChecked(True); dlg.owner_pw.setText("same")
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted and "부동" in seen[0]


# ── 권한 암호 띠 ─────────────────────────────────────────────
def test_owner_only_doc_bar_blocks_and_unlocks(win, folder, monkeypatch):
    src = _pdf(os.path.join(folder, "r.pdf"), encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="ownpw",
               permissions=fitz.PDF_PERM_PRINT)
    win.open_path(src); pump(); t = win.cur()
    assert t.protect_bar.isVisibleTo(t) and t.protect_bar.label.text() == SEC.BAR_TEXT
    win.on_tool("rect"); pump()
    assert t.viewer.tool != "rect"                         # 주석 도구가 막힌다
    n = t.doc.page_count
    win.on_action("insert_blank"); pump()
    assert t.doc.page_count == n
    # 틀린 암호는 문구, 맞는 암호면 띠가 사라지고 풀린다
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    answers = iter(["wrong", "ownpw"])

    def fake_exec(self):
        while True:
            self.pw.setText(next(answers)); self.accept()
            if self.result() == QDialog.DialogCode.Accepted:
                return True
    monkeypatch.setattr(SEC.OwnerPasswordDialog, "exec", fake_exec)
    t.protect_bar.run.click(); pump()
    assert warned == [SEC.WRONG_OWNER]
    assert not t.protect_bar.isVisibleTo(t) and not T.is_restricted(t.doc.fitz)
    win.on_tool("rect"); pump()
    assert t.viewer.tool == "rect"
    # 저장·되돌리기로 다시 불러도 풀린 채
    t.doc.snapshot(); t.doc.undo()
    assert not T.is_restricted(t.doc.fitz)


def test_encrypt_on_restricted_doc_asks_owner_first(win, folder, monkeypatch):
    src = _pdf(os.path.join(folder, "r.pdf"), encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="ownpw",
               permissions=fitz.PDF_PERM_PRINT)
    win.open_path(src); pump(); t = win.cur()
    asked = []
    monkeypatch.setattr(SEC.OwnerPasswordDialog, "exec", lambda self: asked.append(1) or False)
    opened = []
    monkeypatch.setattr(SEC.EncryptDialog, "exec", lambda self: opened.append(1) or False)
    win.on_action("encrypt")
    assert asked and not opened


def test_ribbon_has_no_decrypt_button():
    from app.ui import ribbon as R
    keys = [it[0] for n, _l, items in R.TABS if n == "protect" for it in items if it != R.SEP]
    assert "decrypt" not in keys and "redact_apply" in keys and "sign_manage" in keys


# ── 교정 ───────────────────────────────────────────────────
def test_redact_tip_never_again(win, folder, monkeypatch):
    win.open_path(_pdf(os.path.join(folder, "a.pdf"))); pump(); t = win.cur()
    shown = []

    def fake(self):
        shown.append(1); self.never.setChecked(True); return True
    monkeypatch.setattr(DR.TipDialog, "exec", fake)
    win.on_tool("redact"); pump()
    assert shown == [1] and win.prefs.get("redact_tip_off") is True
    assert t.redact_bar.isVisibleTo(t)
    win.on_tool("hand"); win.on_tool("redact"); pump()
    assert shown == [1]
    from app.ui.window import load_settings
    assert load_settings()["prefs"]["redact_tip_off"] is True


def test_redact_apply_dialog_clears_metadata(win, folder, monkeypatch):
    win.open_path(_pdf(os.path.join(folder, "a.pdf"))); pump(); t = win.cur()
    d = t.doc
    d.fitz[0].add_redact_annot(fitz.Rect(70, 66, 200, 84))

    def fake(self):
        assert self.meta.text() == "파일에서 메타데이터 삭제" and not self.meta.isChecked()
        self.meta.setChecked(True); return True
    monkeypatch.setattr(DR.RedactApplyDialog, "exec", fake)
    win.on_action("redact_apply"); pump()
    assert not d.fitz.metadata.get("title") and not d.fitz.metadata.get("author")
    assert "Hello" not in d.fitz[0].get_text()
    assert win.prefs.get("redact_meta") is True


def test_overlay_text_korean_and_auto_size(folder):
    d = fitz.open(_pdf(os.path.join(folder, "a.pdf")))
    a = d[0].add_redact_annot(fitz.Rect(70, 66, 300, 86))
    T.set_redact_style(d, 0, a.xref, fill=(0, 0, 1), text="기밀 사항", fontsize=0, text_color=(1, 1, 1), align=1)
    st = T.redact_style(d, a.xref)
    assert st["text"] == "기밀 사항" and st["fontsize"] == 0 and st["align"] == 1 and st["fill"] == (0, 0, 1)
    assert T.apply_redactions(d) == 1
    txt = d[0].get_text()
    assert "기밀 사항" in txt and "Hello" not in txt


def test_search_panel_five_hits_mark_two(win, folder):
    win.open_path(_pdf(os.path.join(folder, "a.pdf"), lines=["apple one", "apple two", "Apple three", "apple four", "pineapple apple"]))
    pump(); t = win.cur()
    win.on_action("redact_search"); pump()
    assert t.side.isVisible() and t.side_key == "redact"
    p = t.redact_panel
    p.text.setText("apple"); p.find()
    assert p.count.text() == "6개"
    p.whole.setChecked(True); p.find()
    assert p.count.text() == "5개"
    p.case.setChecked(True); p.find()
    assert p.count.text() == "4개"
    p.case.setChecked(False); p.find()
    for i in (0, 2):
        p.list.item(i).setCheckState(Qt.CheckState.Checked)
    assert p.mark(only_checked=True) == 2
    rs = list(t.doc.fitz[0].annots(types=[fitz.PDF_ANNOT_REDACT]))
    assert len(rs) == 2
    assert t.redact_bar.isVisibleTo(t) and p.count.text() == "결과 없음"


def test_search_panel_asks_again_after_change(win, folder, monkeypatch):
    win.open_path(_pdf(os.path.join(folder, "a.pdf"))); pump(); t = win.cur()
    p = t.redact_panel
    p.text.setText("Hello"); p.find()
    t.doc.snapshot()   # 문서가 바뀌었다
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    assert p.mark() == 0 and asked and "검색을 반복" in asked[0]


def test_search_empty_message(win, folder, monkeypatch):
    win.open_path(_pdf(os.path.join(folder, "a.pdf"))); pump(); t = win.cur()
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen.append(a[2]))
    t.redact_panel.find()
    assert seen == ["검색 텍스트는 비워둘 수 없습니다."]


# ── 숨은 데이터 ──────────────────────────────────────────────
def _rich(folder) -> fitz.Document:
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 80), "Visible text", fontsize=12)
    p.insert_text((72, 120), "Invisible ocr", fontsize=12, render_mode=3)
    ocg = d.add_ocg("Secret", on=False)
    p.insert_text((72, 160), "Layer secret", fontsize=12, oc=ocg)
    p.insert_text((72, 700), "Cropped away", fontsize=12)
    p.set_cropbox(fitz.Rect(0, 0, 595, 400))
    p.add_highlight_annot(fitz.Rect(72, 66, 150, 84))
    p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 66, 150, 84), "uri": "https://example.com"})
    w = fitz.Widget(); w.field_name = "f1"; w.field_type = fitz.PDF_WIDGET_TYPE_TEXT; w.rect = fitz.Rect(300, 60, 400, 80)
    p.add_widget(w)
    d.embfile_add("a.txt", b"hello")
    d.set_toc([[1, "첫째", 1]])
    d.set_metadata({"title": "제목", "author": "글쓴이"})
    d.xref_set_key(d.pdf_catalog(), "PieceInfo", "<</X <</LastModified (D:2020)>>>>")
    d.xref_set_key(p.xref, "PieceInfo", "<</X <</LastModified (D:2020)>>>>")
    d.xref_set_key(d.pdf_catalog(), "OpenAction", "<</S /JavaScript /JS (app.alert\\(1\\))>>")
    return fitz.open("pdf", d.tobytes())


def _all_text(d: fitz.Document) -> str:
    p = d[0]
    c = fitz.Rect(p.cropbox)
    p.set_cropbox(p.mediabox)
    t = p.get_text()
    p.set_cropbox(c)
    return t


CHECKS = {
    "metadata": lambda d: not d.metadata.get("title") and not d.metadata.get("author"),
    "attachments": lambda d: d.embfile_count() == 0,
    "bookmarks": lambda d: d.get_toc() == [],
    "annotations": lambda d: len(list(d[0].annots())) == 0,
    "form_fields": lambda d: len(list(d[0].widgets())) == 0,
    "hidden_text": lambda d: "Invisible ocr" not in _all_text(d) and "Visible text" in _all_text(d),
    "hidden_layers": lambda d: not any(x in d[0].read_contents().lower() for x in (b"4c6179657220736563726574", b"layer secret")),
    "search_index": lambda d: d.xref_get_key(d.pdf_catalog(), "PieceInfo")[0] == "null"
                              and d.xref_get_key(d[0].xref, "PieceInfo")[0] == "null",
    "cropped": lambda d: "Cropped away" not in _all_text(d) and "Visible text" in _all_text(d),
    "links_js": lambda d: d[0].get_links() == [] and d.xref_get_key(d.pdf_catalog(), "OpenAction")[0] == "null",
}


@pytest.mark.parametrize("key", T.HIDDEN_KEYS)
def test_hidden_data_each(folder, key):
    d = _rich(folder)
    assert not CHECKS[key](d)                 # 처음엔 있다
    done = T.remove_hidden_data(d, **{k: k == key for k in T.HIDDEN_KEYS})
    assert done == [key] and CHECKS[key](d)
    others = [k for k in T.HIDDEN_KEYS if k != key and k not in ("hidden_text",)]
    for k in others:   # 다른 것은 그대로
        if k == "metadata" and key == "metadata":
            continue
        assert not CHECKS[k](d), k
    fitz.open("pdf", d.tobytes(garbage=3))    # 저장해도 깨지지 않는다


def test_hidden_layer_content_gone_ocg_removed(folder):
    d = _rich(folder)
    T.remove_hidden_data(d, **{k: k == "hidden_layers" for k in T.HIDDEN_KEYS})
    assert d.xref_get_key(d.pdf_catalog(), "OCProperties")[0] == "null"
    assert CHECKS["hidden_layers"](d) and "Visible text" in _all_text(d)


def test_hidden_dialog_remembers_and_confirms(win, folder, monkeypatch):
    path = os.path.join(folder, "rich.pdf"); _rich(folder).save(path)
    win.open_path(path); pump(); t = win.cur()
    tips = []
    monkeypatch.setattr(DR.TipDialog, "exec", lambda self: tips.append(self.windowTitle()) or self.never.setChecked(True) or True)
    seen = {}

    def fake(self):
        seen["n"] = len(self.cbs); seen["overlap"] = self.overlap.isEnabled()
        seen["first"] = {k: cb.isChecked() for k, cb in self.cbs.items()}
        for k, cb in self.cbs.items():
            cb.setChecked(k in ("metadata", "bookmarks"))
        return True
    monkeypatch.setattr(DR.HiddenDataDialog, "exec", fake)
    texts = []

    def box_exec(self):
        texts.append(self.text())
        for b in self.buttons():
            if b.text() == "제거":
                b.click()
    monkeypatch.setattr(QMessageBox, "exec", box_exec)
    win.on_action("hidden"); pump()
    assert seen["n"] == 10 and seen["overlap"] is False and all(seen["first"].values())
    assert texts == [DR.HIDDEN_CONFIRM] and tips == ["숨겨진 데이터 삭제"]
    d = t.doc.fitz
    assert d.get_toc() == [] and not d.metadata.get("title") and d.embfile_count() == 1
    assert win.prefs["hidden_data"]["bookmarks"] is True and win.prefs["hidden_data"]["attachments"] is False
    # 다음에는 지난 값으로, 안내 창은 다시 안 뜬다
    monkeypatch.setattr(DR.HiddenDataDialog, "exec", lambda self: seen.update(again={k: cb.isChecked() for k, cb in self.cbs.items()}) or False)
    win.on_action("hidden")
    assert seen["again"]["attachments"] is False and seen["again"]["metadata"] is True and len(tips) == 1


# ── 일괄 편집 보안 ────────────────────────────────────────────
def test_batch_security_uses_new_dialog(win, folder, monkeypatch):
    from app.ui import dlg_batch as DB
    src = _pdf(os.path.join(folder, "b.pdf"))
    dlg = DB.BatchDialog(win, "encrypt", [src])

    def fake(self):
        assert not self.new_file.isVisibleTo(self)
        self.use_open.setChecked(True); self.open_pw.setText("bpw")
        return True
    monkeypatch.setattr(SEC.EncryptDialog, "exec", fake)
    dlg.edit_security()
    assert dlg.security["user_pw"] == "bpw" and "128 bit AES" in dlg.sec_label.text()
    data, _ = T.encrypt_bytes(fitz.open(src), **dlg.security)
    g = fitz.open("pdf", data)
    assert g.needs_pass and g.authenticate("bpw")


def test_protected_doc_blocks_dsign_clear():
    # 보호 문서에서 「모든 서명 제거」도 쓰기 명령으로 막는다(라운드 16 합치기).
    assert "dsign_clear" in SEC._write_keys()
