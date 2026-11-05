"""라운드 16 라: 디지털 서명(pyHanko) · 디지털 ID · 검증 · 서명 지우기 · 서명된 문서 띠 · 서명 칸 우클릭.
python -m pytest app/tests/test_dsign_r16.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_dsign_test_")   # 사용자 설정·Security 폴더를 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import dsign as G  # noqa: E402
from app.ui import context_menu as CM  # noqa: E402
from app.ui import dlg_dsign as DG  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

PW = "secret1"


def pump(sec: float = 0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _pdf(path: str, sig_fields: int = 2, text_field: bool = True) -> str:
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 72), "Contract body", fontsize=12)
    for k in range(sig_fields):
        w = fitz.Widget(); w.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE; w.field_name = f"Sig{k + 1}"
        w.rect = fitz.Rect(72, 100 + 90 * k, 272, 160 + 90 * k); p.add_widget(w)
    if text_field:
        w = fitz.Widget(); w.field_type = fitz.PDF_WIDGET_TYPE_TEXT; w.field_name = "Name"
        w.rect = fitz.Rect(300, 100, 500, 120); p.add_widget(w)
    d.save(path); d.close()
    return path


@pytest.fixture()
def folder():
    return tempfile.mkdtemp(prefix="r16_dsign_")


@pytest.fixture()
def msgs(monkeypatch):
    got = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, k=kind, **kw: (got.append((k, a[2] if len(a) > 2 else "")),
                                                                                      QMessageBox.StandardButton.Ok)[1]))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **kw: (got.append(("question", a[2])),
                                                                                QMessageBox.StandardButton.Yes)[1]))
    return got


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump()
    w.settings["digital_ids"] = []; w.settings["trusted_certs"] = []
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump()
    QApplication.clipboard().clear()


@pytest.fixture()
def ident(win):
    e = G.create_id("홍길동", "hong@example.com", PW)
    G.add_entry(DG.ids(win), e)
    return e


def _drive(monkeypatch, out_path: str, *, pw: str = PW, lock: bool = False, seen: list | None = None):
    """서명 창을 비밀번호만 넣어 누르고, 다른 이름 저장은 out_path 로."""
    def ex(dlg):
        if seen is not None:
            seen.append(type(dlg).__name__)
        if isinstance(dlg, DG.SignDialog):
            dlg.pw.setText(pw); dlg.lock.setChecked(lock)
            dlg.try_accept()
            return dlg.result() == QDialog.DialogCode.Accepted
        return True
    monkeypatch.setattr(DG, "_exec", ex)
    monkeypatch.setattr(DG, "_save_path", lambda w, default: out_path)


def _click_field(win, t, name: str):
    w = next(w for w in t.doc.fitz[0].widgets() if w.field_name == name)
    win.on_request(t, "fill_field", {"page": 0, "xref": w.xref, "widget": w})
    pump()


# ── 엔진 ─────────────────────────────────────────────────────────
def test_new_id_goes_to_security_folder_and_short_password_blocked():
    e = G.create_id("김서명", "kim@example.com", "abcdef")
    assert os.path.dirname(e["path"]) == G.security_dir() and os.path.exists(e["path"])
    assert e["path"].endswith("김서명.pfx") and e["name"] == "김서명"
    with pytest.raises(G.DsignError) as x:
        G.create_id("짧은", "s@example.com", "12345")
    assert str(x.value) == G.MSG_PW_SHORT
    with pytest.raises(G.DsignError) as x:
        G.create_id("메일", "not-an-email", "abcdef")
    assert str(x.value) == G.MSG_EMAIL
    info = G.cert_info(G.entry_cert(e))
    assert info["self_signed"] and "전자 서명" in info["usages"] and info["email"] == "kim@example.com"


def test_wrong_password_and_expired_messages(folder):
    src = open(_pdf(os.path.join(folder, "a.pdf")), "rb").read()
    e = G.create_id("만료", "old@example.com", PW, path=os.path.join(folder, "old.pfx"),
                    not_before=dt.datetime(2015, 1, 1, tzinfo=dt.timezone.utc), years=1)
    with pytest.raises(G.DsignError) as x:
        G.sign_bytes(src, G.SignRequest(e["path"], "wrong!!", field="Sig1"))
    assert str(x.value) == G.MSG_BAD_PW == "비밀번호가 일치하지 않습니다."
    with pytest.raises(G.DsignError) as x:
        G.sign_bytes(src, G.SignRequest(e["path"], PW, field="Sig1"))
    assert str(x.value) == G.MSG_EXPIRED == "선택한 인증서는 만료되었습니다!"
    with pytest.raises(G.DsignError) as x:
        G.load_pfx(e["path"], "")
    assert str(x.value) == G.MSG_NEED_PW


def test_import_export_and_trust(folder):
    e = G.create_id("가져올", "imp@example.com", PW, path=os.path.join(folder, "imp.p12"))
    got = G.import_pfx(e["path"], PW)
    assert got["cert"] == e["cert"]
    with pytest.raises(G.DsignError):
        G.import_pfx(e["path"], "nope!!")
    cer = os.path.join(folder, "pub.cer")
    G.export_cert(e, cer)
    assert G.cert_b64(G.read_cert_file(cer)) == e["cert"]
    lst = []
    assert G.add_entry(lst, e) is False and G.add_entry(lst, dict(e)) is True and len(lst) == 1


def test_second_signature_keeps_first_valid(folder):
    src = open(_pdf(os.path.join(folder, "b.pdf")), "rb").read()
    e = G.create_id("둘", "two@example.com", PW, path=os.path.join(folder, "two.pfx"))
    one = G.sign_bytes(src, G.SignRequest(e["path"], PW, field="Sig1"))
    two = G.sign_bytes(one, G.SignRequest(e["path"], PW, field="Sig2"))
    st = {s.field: s for s in G.validate(two)}
    assert st["Sig1"].valid and st["Sig1"].coverage == G.COV_LATER
    assert st["Sig2"].valid and st["Sig2"].coverage == G.COV_OK
    assert G.summary(list(st.values())) == G.MSG_ALL_OK


def test_appearance_has_korean_lines():
    lines = G.appearance_lines(G.DEFAULT_STYLE, {"name": "홍길동", "dn": "CN=홍길동"}, "검토함", "서울",
                               dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc))
    assert lines[0] == "홍길동" and "DN: CN=홍길동" in lines and "이유: 검토함" in lines and "위치: 서울" in lines
    d = fitz.open("pdf", G.appearance_pdf(200, 60, lines))
    assert "홍길동" in d[0].get_text()


# ── 화면 흐름 ─────────────────────────────────────────────────────
def test_two_empty_fields_guide_then_click_signs_and_reopens_valid(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "계약.pdf"))
    win.open_path(src); pump()
    t = win.cur()
    out = os.path.join(folder, G.signed_name(src))
    assert out.endswith("계약_서명.pdf")
    seen = []
    _drive(monkeypatch, out, seen=seen)
    win.on_action("digital_sign"); pump()
    assert ("information", DG.MSG_MULTI) in msgs and not os.path.exists(out)
    _click_field(win, t, "Sig1")
    assert "SignDialog" in seen and os.path.exists(out)
    t2 = win.cur()
    assert t2 is not t and os.path.normcase(t2.doc.path) == os.path.normcase(os.path.abspath(out))
    pump(0.2)
    assert t2.sign_bar.isVisible() and t2.sign_bar.label.text() == G.MSG_ALL_OK
    states = DG.validate_all(win, t2)
    assert states and all(s.valid for s in states) and "ValidateAllDialog" in seen
    assert G.summary(states) == "서명되었으며 모든 서명이 유효합니다."
    # 서명 칸을 다시 누르면 속성(서명 창이 아님)
    seen.clear()
    _click_field(win, t2, "Sig1")
    assert seen == ["SigPropsDialog"]


def test_edited_copy_reports_changed(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "c.pdf"))
    out = os.path.join(folder, "c_서명.pdf")
    data = G.sign_bytes(open(src, "rb").read(), G.SignRequest(ident["path"], PW, field="Sig1"))
    open(out, "wb").write(data)
    d = fitz.open(out); d[0].insert_text((72, 400), "X", fontsize=12)
    bad = os.path.join(folder, "c_고침.pdf"); d.save(bad); d.close()
    st = G.validate(open(bad, "rb").read())
    assert st and st[0].coverage == G.COV_BAD and not st[0].valid
    assert G.summary(st) == "하나 이상의 서명이 잘못되었습니다."
    # 덧붙여(증분) 고친 사본은 「그 뒤 변경」
    d = fitz.open(out); d[0].insert_text((72, 420), "Y", fontsize=12); d.saveIncr(); d.close()
    st = G.validate(open(out, "rb").read())
    assert st[0].coverage in (G.COV_LATER, G.COV_BAD)


def test_lock_makes_fields_read_only(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "d.pdf"))
    win.open_path(src); pump()
    out = os.path.join(folder, "d_서명.pdf")
    _drive(monkeypatch, out, lock=True)
    _click_field(win, win.cur(), "Sig1")
    assert win.prefs["dsign_lock"] is True
    f = fitz.open(out)
    flags = {w.field_name: w.field_flags for w in f[0].widgets()}
    assert flags["Name"] & 1
    f.close()


def test_clear_all_leaves_empty_fields(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "e.pdf"))
    out = os.path.join(folder, "e_서명.pdf")
    open(out, "wb").write(G.sign_bytes(open(src, "rb").read(), G.SignRequest(ident["path"], PW, field="Sig1")))
    win.open_path(out); pump(0.2)
    t = win.cur()
    assert G.has_signatures(t.doc.fitz) and t.sign_bar.isVisible()
    monkeypatch.setattr(DG, "_exec", lambda d: True)
    assert DG.clear_all(win, t) == 1
    sw = G.sig_widgets(t.doc.fitz)
    assert [s["name"] for s in sw] == ["Sig1", "Sig2"] and not any(s["signed"] for s in sw)
    assert not t.sign_bar.isVisible() and t.doc.modified


def test_clear_refuses_without_local_certificate(win, folder, msgs):
    src = _pdf(os.path.join(folder, "f.pdf"))
    other = G.create_id("남", "other@example.com", PW, path=os.path.join(folder, "other.pfx"))   # 목록에 없음
    out = os.path.join(folder, "f_서명.pdf")
    open(out, "wb").write(G.sign_bytes(open(src, "rb").read(), G.SignRequest(other["path"], PW, field="Sig1")))
    win.open_path(out); pump()
    assert DG.clear_all(win, win.cur()) == 0
    assert ("warning", DG.MSG_CLEAR_FAIL.format(1)) in msgs
    assert G.has_signatures(win.cur().doc.fitz)


def test_trusted_cert_makes_identity_valid(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "g.pdf"))
    out = os.path.join(folder, "g_서명.pdf")
    open(out, "wb").write(G.sign_bytes(open(src, "rb").read(), G.SignRequest(ident["path"], PW, field="Sig1")))
    win.open_path(out); pump()
    t = win.cur()
    st = DG.validate_tab(win, t)[0]
    assert st.identity_text == G.ID_UNKNOWN
    DG.add_trusted(win, ident["cert"])
    st = DG.validate_tab(win, t)[0]
    assert st.identity_text == "서명자의 ID가 유효합니다." and st.status_text == "서명이 유효합니다."
    dlg = DG.SigPropsDialog(win, t, st)
    assert dlg.ident.text() == G.ID_OK and dlg.cov.text() == G.COV_OK and dlg.f_signer.text() == "홍길동"


def test_wrong_password_in_dialog_shows_message(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "h.pdf"))
    win.open_path(src); pump()
    out = os.path.join(folder, "h_서명.pdf")
    _drive(monkeypatch, out, pw="틀린비번123")
    _click_field(win, win.cur(), "Sig1")
    assert ("warning", G.MSG_BAD_PW) in msgs and not os.path.exists(out)


def test_no_field_drag_makes_new_signature(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "i.pdf"), sig_fields=0, text_field=False)
    win.open_path(src); pump()
    t = win.cur()
    out = os.path.join(folder, "i_서명.pdf")
    _drive(monkeypatch, out)
    win.on_action("digital_sign"); pump()
    assert ("information", DG.MSG_DRAG) in msgs and t.viewer.tool == "fsign"
    t.viewer._make_field(0, fitz.Rect(300, 500, 500, 560)); pump()
    assert os.path.exists(out)
    assert not t.doc.modified and not G.sig_widgets(t.doc.fitz)   # 원래 문서는 그대로
    st = G.validate(open(out, "rb").read())
    assert len(st) == 1 and st[0].valid and abs(st[0].rect.x0 - 300) < 1 and abs(st[0].rect.y1 - 560) < 1


def test_one_empty_field_opens_dialog_directly(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "j.pdf"), sig_fields=1)
    win.open_path(src); pump()
    out = os.path.join(folder, "j_서명.pdf")
    _drive(monkeypatch, out)
    win.on_action("digital_sign"); pump()
    assert os.path.exists(out) and not msgs


def test_context_menu_signature_items(win, ident, folder, msgs, monkeypatch):
    src = _pdf(os.path.join(folder, "k.pdf"))
    out = os.path.join(folder, "k_서명.pdf")
    open(out, "wb").write(G.sign_bytes(open(src, "rb").read(), G.SignRequest(ident["path"], PW, field="Sig1")))
    win.open_path(out); pump()
    t = win.cur(); v = t.viewer
    for name, on in (("Sig1", True), ("Sig2", False)):
        w = next(w for w in t.doc.fitz[0].widgets() if w.field_name == name)
        v.select_fields([(0, w.xref)])
        m = QMenu(); CM.signature_items(v, m, v.sel)
        acts = {a.text(): a for a in m.actions() if a.text()}
        assert set(acts) == {"서명 지우기", "서명 인증", "서명 속성 보기"}
        assert all(a.isEnabled() == on for a in acts.values())
    seen = []
    monkeypatch.setattr(DG, "_exec", lambda d: seen.append(type(d).__name__) or True)
    win.on_request(t, "dsign", {"do": "props", "name": "Sig1"})
    win.on_request(t, "dsign", {"do": "validate", "name": "Sig1"})
    assert seen == ["SigPropsDialog"] and any(k == "information" and "서명이" in s for k, s in msgs)
    win.on_request(t, "dsign", {"do": "clear", "name": "Sig1"})
    assert not G.has_signatures(t.doc.fitz)


def test_ribbon_menu_and_cert_manager(win, ident, folder, msgs, monkeypatch):
    seen = []
    monkeypatch.setattr(DG, "_exec", lambda d: seen.append(type(d).__name__) or True)
    win.on_action("dsign_ids")   # 문서 없이도 열림
    assert seen == ["CertManagerDialog"]
    from app.ui import ribbon as R
    menu = next(it[4] for _, _, items in R.TABS for it in items if it != R.SEP and it[0] == "sign_manage")
    keys = [k for k, *_ in menu]
    assert keys[:3] == ["dsign_validate", "dsign_clear", "dsign_ids"] and {"sign_new", "sign_apply", "sign_clear"} <= set(keys)
    dlg = DG.CertManagerDialog(win)
    assert dlg.id_list.count() == 1 and "홍길동" in dlg.id_list.item(0).text()
    cer = os.path.join(folder, "t.cer"); G.export_cert(ident, cer)
    monkeypatch.setattr(DG.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (cer, "")))
    dlg.add_trust()
    assert DG.trusted(win) == [ident["cert"]] and dlg.tr_list.count() == 1
    dlg.tr_list.setCurrentRow(0); dlg.delete_trust()
    assert DG.trusted(win) == []
    dlg.id_list.setCurrentRow(0); dlg.delete_id()
    assert DG.ids(win) == [] and not os.path.exists(ident["path"])


def test_add_id_wizard_steps(win, msgs):
    w = DG.AddIdWizard(win)
    w.go_next()                       # 새로 만들기 → 식별 정보
    assert w.stack.currentIndex() == 2
    w.name.setText("마법사"); w.email.setText("bad")
    assert w.go_next() is False and ("warning", G.MSG_EMAIL) in msgs
    w.email.setText("wiz@example.com"); assert w.go_next()
    assert w.path.text() == G.default_id_path("마법사")
    w.pw.setText("12345"); w.pw2.setText("12345")
    assert w.go_next() is False and ("warning", G.MSG_PW_SHORT) in msgs
    w.pw.setText("123456"); w.pw2.setText("123456")
    assert w.go_next() and w.stack.currentIndex() == 4 and os.path.exists(w.entry["path"])
    assert w.summary.count() == 1 and "마법사" in w.summary.item(0).text()


def test_appearance_dialog_saves_style(win, ident, msgs, monkeypatch):
    d = DG.AppearanceDialog(win, DG.style(win))
    d.w.checks["dn"].setChecked(False); d.w.checks["time"].setChecked(True); d.w.r_name.setChecked(True)
    v = d.value()
    assert "dn" not in v["items"] and "time" in v["items"] and v["shape"] == "name"
    assert not d.w.preview.pixmap().isNull()
