"""라운드 24 남은 대화상자: 고급 찾기 기본 위치 · 압축 창 배치 · 병합·압축 단추 · 환경 설정 글꼴 이름 · 표지 문구.

python -m pytest app/tests/test_dialogs_r24.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_dlg_r24_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.engine import fonts as F  # noqa: E402
from app.engine import tools as T  # noqa: E402
from app.ui import dlg_adv_search as DA  # noqa: E402
from app.ui import dlg_compress as DZ  # noqa: E402
from app.ui import dlg_convert as DC  # noqa: E402
from app.ui import dlg_merge as DM  # noqa: E402
from app.ui import dlg_prefs as DP  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402


def pump(sec: float = 0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _pdf(path: str) -> str:
    d = fitz.open(); d.new_page().insert_text((72, 100), "r24"); d.save(path); d.close()
    return path


@pytest.fixture()
def win():
    w = MainWindow(); w.show(); w.resize(1200, 800); pump()
    yield w
    for t in list(w.tabs):
        t.doc.modified = False
    w.close(); pump()


@pytest.fixture()
def folder():
    d = tempfile.mkdtemp(prefix="r24_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _buttons(dlg) -> list[str]:
    return [b.text() for b in dlg.findChildren(QPushButton) if b.isVisibleTo(dlg) and not b.text().startswith("파일")]


# ① D6 고급 찾기: 문서가 있으면 「현재 문서」, 없으면 「폴더 위치 찾기」 를 골라 뜬다
def test_adv_search_default_where(win, folder):
    empty = DA.AdvancedSearchDialog(win)
    assert empty.r_dir.isChecked() and empty.folder.isEnabled()
    win.open_path(_pdf(os.path.join(folder, "찾기.pdf"))); pump(0.1)
    opened = DA.AdvancedSearchDialog(win)
    assert opened.r_cur.isChecked() and not opened.folder.isEnabled()


# ② D12 압축 창: 저장 위치 줄이 창 전체 폭 아래, 첫 높이는 줄였다
def test_compress_output_row_full_width(win, folder):
    dlg = DZ.CompressDialog(win, paths=[_pdf(os.path.join(folder, "줄이기.pdf"))])
    dlg.show(); pump(0.1)
    assert dlg.height() < 520
    files, out = dlg.files.geometry(), dlg.out.geometry()
    assert out.top() >= files.bottom()                    # 목록·옵션 아래
    assert out.left() <= files.left() + 2 and out.width() > dlg.width() * 0.9
    dlg.close()


# ③ D13 병합·압축: 「취소」 단추 없이 「적용」 하나(알PDF 와 같게), 압축은 도는 동안 「적용」 이 「취소」
def test_merge_compress_buttons_and_cancel(monkeypatch, win, folder):
    m = DM.MergeDialog(win, paths=[_pdf(os.path.join(folder, "가.pdf"))]); m.show(); pump()
    assert "취소" not in _buttons(m) and _buttons(m)[-2:] == ["미리 보기", "적용"]
    m.done_ok = True; m.close()
    paths = [_pdf(os.path.join(folder, f"{k}.pdf")) for k in range(3)]
    z = DZ.CompressDialog(win, paths=paths); z.show(); pump()
    assert "취소" not in _buttons(z) and z.apply_btn.text() == "적용"
    z.out.combo.setCurrentIndex(z.out.ORIGINAL)
    real = T.compress

    def slow(*a, **k):
        time.sleep(0.3); return real(*a, **k)
    monkeypatch.setattr(T, "compress", slow)
    monkeypatch.setattr(DZ.CompressDialog, "success", lambda self, n, pct: None)
    seen = {}

    def press():
        seen["running"] = z.apply_btn.text(); z.apply_btn.click()
        seen["after"] = (z.apply_btn.text(), z.apply_btn.isEnabled())
    QTimer.singleShot(100, press)
    res = z.apply()
    assert seen["running"] == "취소" and seen["after"] == ("취소 중…", False)
    assert res[0]["ok"] and not any(r["ok"] for r in res[1:])   # 지금 파일까지 하고 멈춤
    assert z.apply_btn.text() == "적용" and z.apply_btn.isEnabled() and z.isVisible()
    z.close()


# ④ D19 환경 설정: 두 글꼴 칸이 같은 한국어 표시 이름, 저장 값은 글꼴 이름
def test_prefs_font_names_same(win):
    dlg = DP.PreferencesDialog(win, win.settings)
    dlg.show_page(next(n for n, p in enumerate(DP.PAGES) if p[0] == "font"))
    items = lambda c: [c.itemText(i) for i in range(c.count())]  # noqa: E731
    assert items(dlg.ef_font) == items(dlg.cf_font)
    shown = F.display_name(F.DEFAULT_FAMILY)
    assert dlg.ef_font.currentText() == dlg.cf_font.currentText() == shown
    if os.path.exists(r"C:\Windows\Fonts\malgun.ttf"):
        assert shown == "맑은 고딕"
    dlg.set_font(dlg.ef_font, "Malgun Gothic"); dlg.cf_font.setEditText(shown)
    dlg._save_font()
    assert win.prefs["edit_font"]["font"] == win.prefs["comment_font"]["font"] == F.DEFAULT_FAMILY
    dlg.cf_font.setEditText("없는 글꼴"); dlg._save_font()
    assert win.prefs["comment_font"]["font"] == DP.COMMENT_FONT["font"]
    dlg.close()


# ⑤ W10 표지 그림 문구는 명사형
def test_cover_error_noun():
    ep = DC.ConvertSettings(None, "epub", 3, 0, None, None, {}, lambda: ["kor"])
    ep.fields.cover_on.setChecked(True)
    assert ep.fields.error()[0] == "표지 그림 파일 선택 필요"
