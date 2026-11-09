"""라운드 23 대화상자 다듬기: 기본 단추(Enter) · 경로 표기 · 작은 화면(배율 200%) · 화면 읽기 이름 · 문구.

python -m pytest app/tests/test_dialog_polish.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_dlg_polish_")   # 사용자 설정을 건드리지 않게

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QAccessible  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QPushButton,  # noqa: E402
                               QSlider, QToolButton)

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import stamps as S  # noqa: E402
from app.engine.document import Document  # noqa: E402
from app.ui import dialogs as D  # noqa: E402

SMALL = QRect(0, 0, 960, 520)   # 1920×1080 · 배율 200% 에서 쓸 수 있는 화면(논리 px)


def pump(sec: float = 0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


@pytest.fixture()
def doc(tmp_path):
    f = fitz.open()
    for i in range(3):
        f.new_page().insert_text((72, 100), f"P{i + 1}")
    p = str(tmp_path / "다듬기.pdf"); f.save(p)
    d = Document(p)
    yield d
    d.close() if hasattr(d, "close") else None


@pytest.fixture()
def st(tmp_path):
    return S.StampStore(str(tmp_path / "stamps"))


@pytest.fixture()
def settings():
    return {"prefs": {}}


# ── 만드는 법(이름 → 대화상자, 확인 단추) ─────────────────────
def _ok_of(dlg):
    for name in ("ok", "apply_btn", "go"):
        b = getattr(dlg, name, None)
        if isinstance(b, QPushButton):
            return b
    bb = getattr(dlg, "bb", None)
    if bb is not None:
        return bb.button(bb.StandardButton.Ok) or bb.buttons()[0]
    return None


def _make(kind, doc, st, settings):
    from app.ui import dlg_deco_mark as DM, dlg_deco_text as DT, dlg_extract as DE, dlg_prefs as DP
    from app.ui import dlg_properties as DPR, dlg_signature as DS, dlg_split as DSP, dlg_stamp as DST
    from app.ui import printing as PR
    if kind == "split":
        d = DSP.SplitDialog(None, 3, 1000, False, tempfile.gettempdir()); return d, d.bb.button(d.bb.StandardButton.Ok)
    if kind == "signature":
        d = DS.SignatureDialog(None, st); return d, _text_btn(d, "서명 넣기")
    if kind == "stamp_custom":
        d = DST.CustomStampDialog(None, st=st); return d, d.bb.button(d.bb.StandardButton.Ok)
    if kind == "stamp_manage":
        d = DST.StampManageDialog(None, "나", st=st); return d, _text_btn(d, "확인")
    if kind == "header":
        d = DT.HeaderFooterDialog(None, doc, 0); return d, d.ok
    if kind == "watermark":
        d = DM.WatermarkDialog(None, doc, 0); return d, d.ok
    if kind == "background":
        d = DM.BackgroundDialog(None, doc, 0); return d, d.ok
    if kind == "pagenumber":
        d = DT.PageNumberDialog(None, doc, 0); return d, d.ok
    if kind == "print":
        d = PR.PrintDialog(None, doc.fitz, 0, None); return d, d.ok
    if kind == "extract":
        d = DE.ExtractDialog(None, 3, 0, [0]); return d, _text_btn(d, "내보내기")
    if kind == "prefs":
        d = DP.PreferencesDialog(None, settings); return d, _text_btn(d, "적용")
    if kind == "properties":
        d = DPR.PropertiesDialog(None, doc, 0, settings); return d, d.apply_btn
    if kind == "filelist":
        d = D.FileListDialog(None, "합치기", "합치기", "PDF (*.pdf)"); return d, _text_btn(d, "합치기")
    raise KeyError(kind)


def _text_btn(dlg, text):
    return next(b for b in dlg.findChildren(QPushButton) if b.text() == text)


DEFAULT_KINDS = ["split", "signature", "stamp_custom", "stamp_manage", "header", "watermark", "background",
                 "pagenumber", "print", "extract", "prefs", "properties", "filelist"]


@pytest.mark.parametrize("kind", DEFAULT_KINDS)
def test_dialog_default_is_confirm(kind, doc, st, settings):
    """확인 단추 하나만 기본 단추(강조색·Enter), 나머지 단추는 초점을 받아도 기본 단추가 되지 않는다."""
    dlg, ok = _make(kind, doc, st, settings)
    dlg.show(); pump()
    buttons = dlg.findChildren(QPushButton)
    assert ok is not None and ok in buttons
    assert [b for b in buttons if b.isDefault()] == [ok]
    assert [b.text() for b in buttons if b is not ok and b.autoDefault()] == []
    dlg.reject()


def test_enter_goes_to_confirm_when_other_button_focused():
    """「추가」에 초점이 있어도 Enter 는 확인 단추로 간다(전에는 파일 고르기 창이 떴다)."""
    dlg = D.FileListDialog(None, "합치기", "합치기", "PDF (*.pdf)")
    hits = []
    _text_btn(dlg, "합치기").clicked.connect(lambda: hits.append("ok"))
    add = _text_btn(dlg, "추가")
    add.clicked.disconnect()   # 진짜 파일 고르기 창은 띄우지 않는다
    add.clicked.connect(lambda: hits.append("add"))
    dlg.show(); pump()
    add.setFocus(); pump()
    assert not add.isDefault()
    QTest.keyClick(add, Qt.Key.Key_Return); pump()
    assert hits == ["ok"]


# ── 경로 표기 ───────────────────────────────────────────────
def test_paths_shown_with_backslash():
    from app.ui import dlg_convert as DC, dlg_split as DSP, file_list as FL
    for p in (DC.default_custom_folder(), DC.default_folder(None), DSP.default_split_folder(), FL.default_folder("병합")):
        assert "/" not in p, p
    panel = FL.OutputPanel(None, {"folder": "C:/작업/결과", "custom": True}, "병합")
    assert panel.path.text() == r"C:\작업\결과"
    panel.set_origin("D:/원본/폴더"); panel.combo.setCurrentIndex(panel.ORIGINAL)
    assert panel.path.text() == r"D:\원본\폴더"
    split = DSP.SplitDialog(None, 3, 1000, False, "C:\\src", True, "C:/나눔/결과")
    assert split.dir.text() == r"C:\나눔\결과"


# ── 작은 화면(배율 2.0 · 960×520) ──────────────────────────
FIT_KINDS = ["print", "watermark", "background", "header", "pagenumber", "properties", "prefs", "adv_search"]


def _bottom_inside(dlg, btn) -> bool:
    r = QRect(btn.mapTo(dlg, QPoint(0, 0)), btn.size())
    return dlg.rect().contains(r) and btn.isVisible()


@pytest.mark.parametrize("kind", FIT_KINDS)
def test_dialog_fits_small_screen(kind, doc, st, settings, monkeypatch):
    monkeypatch.setattr(D, "available_rect", lambda w: QRect(SMALL))
    from app.ui import printing as PR
    monkeypatch.setattr(PR, "available_rect", lambda w: QRect(SMALL))
    if kind == "adv_search":
        from app.ui import dlg_adv_search as DA
        from app.ui.window import MainWindow
        win = MainWindow(); win.show(); pump()
        dlg = DA.AdvancedSearchDialog(win); ok = dlg.go
    else:
        dlg, ok = _make(kind, doc, st, settings)
    dlg.show(); pump(0.1)
    fg = dlg.frameGeometry()   # 창틀(제목줄)까지 화면 안
    assert SMALL.contains(fg), (kind, fg)
    assert _bottom_inside(dlg, ok), (kind, ok.text())
    dlg.reject()


def test_print_folds_advanced_on_small_screen_but_keeps_pref(doc, monkeypatch):
    from app.ui import printing as PR
    monkeypatch.setattr(PR, "available_rect", lambda w: QRect(SMALL))
    dlg = PR.PrintDialog(None, doc.fitz, 0, {"advanced": True})
    assert not dlg._advanced and dlg.remember()["advanced"] is True


def test_normal_screen_unchanged(doc, monkeypatch):
    """넉넉한 화면에서는 몸통 스크롤이 생기지 않고 크기도 그대로다."""
    big = QRect(0, 0, 1920, 1040)
    monkeypatch.setattr(D, "available_rect", lambda w: QRect(big))
    from app.ui import dlg_deco_mark as DM
    dlg = DM.WatermarkDialog(None, doc, 0); dlg.show(); pump(0.1)
    body = dlg.findChild(D.FitScroll)
    assert body is not None and not body.verticalScrollBar().isVisible() and not body.horizontalScrollBar().isVisible()
    dlg.reject()


# ── 화면 읽기 이름 ──────────────────────────────────────────
def _name(w) -> str:
    acc = QAccessible.queryAccessibleInterface(w)
    return acc.text(QAccessible.Text.Name) if acc else ""


def _unnamed(dlg) -> list[str]:
    out = []
    for w in dlg.findChildren(QLineEdit) + dlg.findChildren(QComboBox) + dlg.findChildren(QAbstractSpinBox) + \
            dlg.findChildren(QSlider) + dlg.findChildren(QToolButton):
        if isinstance(w, QLineEdit) and isinstance(w.parent(), (QAbstractSpinBox, QComboBox)):
            continue   # 숫자칸·목록 안쪽 글칸
        if isinstance(w, QToolButton) and len(w.text().strip()) > 1 and any(c.isalnum() for c in w.text()):
            continue   # 글자 단추는 글자가 이름
        if not _name(w).strip():
            out.append(f"{type(w).__name__}:{w.objectName()}:{w.text() if hasattr(w, 'text') else ''}")
    return out


def test_print_dialog_names(doc):
    from app.ui import printing as PR
    dlg = PR.PrintDialog(None, doc.fitz, 0, None)
    assert _name(dlg.portrait_btn) == "세로" and _name(dlg.landscape_btn) == "가로"
    assert _name(dlg.props_btn) == "프린터 속성"
    assert _name(dlg.copies) == "매수"
    assert _unnamed(dlg) == []


def test_watermark_dialog_names(doc):
    from app.ui import dlg_deco_mark as DM
    dlg = DM.WatermarkDialog(None, doc, 0)
    assert _name(dlg.pos.btns["top_left"] if "top_left" in dlg.pos.btns else list(dlg.pos.btns.values())[0]) == "왼쪽 위"
    assert _name(dlg.off_v) == "세로 이동" and _name(dlg.off_h) == "가로 이동"
    assert _unnamed(dlg) == []


# ── 문구·빈 상태 ────────────────────────────────────────────
def test_words(doc, st, settings):
    from app.ui import dlg_deco_mark as DM, dlg_prefs as DP, dlg_stamp as DST, dlg_signature as DS
    from app.ui import printing as PR, shortcuts as K
    assert all(not label.startswith("쪽 ") for _, label in D.FieldAlignDialog.HOWS)
    assert "페이지 가로 가운데" in [label for _, label in D.FieldAlignDialog.HOWS]
    prefs = DP.PreferencesDialog(None, settings)
    texts = [b.text() for b in prefs.findChildren(QPushButton)]
    assert "찾아보기" in texts and "…" not in texts
    assert prefs.autosave_style.text() == "마지막으로 고친 주석 모양을 기본값으로 사용"
    prefs.show_page([k for k, _ in DP.PAGES].index("shortcut"))
    assert any(b.text() == "모두 기본값으로 되돌리기" for b in prefs.findChildren(QPushButton))
    ed = prefs.findChild(K.ShortcutEditor)
    e = ed.edits[next(iter(ed.edits))][0]
    le = e.findChild(QLineEdit)
    assert ed.table.columnWidth(2) >= le.fontMetrics().horizontalAdvance("Ctrl+Shift+Tab") + 20
    # 인쇄: 가운데 머리는 하는 일 이름, 매수 칸은 글자 라벨
    pr = PR.PrintDialog(None, doc.fitz, 0, None)
    heads = [w.text() for w in pr.findChildren(type(pr.err)) if w.objectName() == "printhead"]
    assert heads.count("페이지 범위") == 1 and "홀수·짝수·역순" in heads
    # 워터마크: 회전 단추는 지금 각도를 눌린 모양으로
    wm = DM.WatermarkDialog(None, doc, 0)
    assert wm.look.quick[45].isChecked() and not wm.look.quick[0].isChecked()
    wm.look.rotate.setValue(0)
    assert wm.look.quick[0].isChecked() and not wm.look.quick[45].isChecked()
    wm.look.rotate.setValue(30)
    assert not any(b.isChecked() for b in wm.look.quick.values())
    # 배경: 「색」 라벨 없음
    bg = DM.BackgroundDialog(None, doc, 0)
    assert "색" not in [w.text() for w in bg.findChildren(type(pr.err))]
    # 빈 상태
    sm = DST.StampManageDialog(None, "나", st=st)
    assert sm.list.count() == 1 and sm.list.item(0).text() == DST.EMPTY_STAMPS
    assert sm.list.item(0).flags() == Qt.ItemFlag.NoItemFlags and not sm.del_btn.isEnabled()
    sg = DS.SignatureDialog(None, st)
    assert sg.list.count() == 1 and sg.list.item(0).text() == "저장한 서명 없음" and not sg.del_btn.isEnabled()
    cs = DST.CustomStampDialog(None, st=st)
    assert cs.preview.text() == "고른 파일 없음"


def test_password_and_extract_buttons(doc):
    from app.ui import dlg_extract as DE, dlg_password as DPW
    ex = DE.ExtractDialog(None, 3, 0, [0])
    assert [b.text() for b in ex.findChildren(QPushButton) if b.isDefault()] == ["내보내기"]
    pw = DPW.PasswordDialog(None, "a.pdf", {}, lambda s: False)
    pw.show(); pump()
    assert [b.text() for b in pw.findChildren(QPushButton) if b.isDefault()] == ["열기"]
    pw.reject()


def test_deco_preview_drawn_at_open(doc):
    from app.ui import dlg_deco_mark as DM
    dlg = DM.WatermarkDialog(None, doc, 0)
    pm = dlg.preview.view.pixmap()
    assert pm is not None and not pm.isNull()
