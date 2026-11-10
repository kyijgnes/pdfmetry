"""속성 패널·리본 잘림 시험(라운드 23): 배율 1.0·1.5 에서 단추 글자가 다 보이는지, 리본 이름 표시.

배율은 프로세스마다 하나라(QT_SCALE_FACTOR) 1.5 는 따로 띄운 파이썬에서 잰다.
python -m pytest app/tests/test_props_ribbon_fit.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_fit_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QIcon, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QToolButton, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from app.ui import icons, ribbon, theme  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pump(sec: float = 0.15):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents(); time.sleep(0.005)


def _make() -> str:
    p = os.path.join(tempfile.mkdtemp(), "폭시험.pdf")
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((72, 100), "hello block", fontsize=14)
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 50), False)
    pg.insert_image(fitz.Rect(300, 300, 400, 350), pixmap=pm)
    pg.add_rect_annot(fitz.Rect(100, 500, 200, 560))
    d.save(p); d.close()
    return p


def _click(v, pt: fitz.Point):
    for kind in ("press", "release"):
        vp = QPointF(v.mapFromScene(v.from_page_point(0, pt)))
        t = QEvent.Type.MouseButtonPress if kind == "press" else QEvent.Type.MouseButtonRelease
        held = Qt.MouseButton.LeftButton if kind == "press" else Qt.MouseButton.NoButton
        e = QMouseEvent(t, vp, QPointF(v.viewport().mapToGlobal(vp.toPoint())), Qt.MouseButton.LeftButton, held,
                        Qt.KeyboardModifier.NoModifier)
        (v.mousePressEvent if kind == "press" else v.mouseReleaseEvent)(e)
        pump(0.03)


def clipped(panel: QWidget) -> list:
    """패널 안 보이는 단추 가운데 글자가 다 안 들어가거나 보이는 자리(스크롤 창) 밖으로 나간 것."""
    view = panel._scroll.viewport()
    right = view.mapTo(panel, QPoint(view.width(), 0)).x()
    bad = []
    for b in panel.findChildren(QAbstractButton):
        if not b.isVisibleTo(panel) or not b.text():
            continue
        x1 = b.mapTo(panel, QPoint(b.width(), 0)).x()
        if b.sizeHint().width() > b.width() or x1 > right:
            bad.append((b.text(), b.sizeHint().width(), b.width(), x1, right))
    return bad


def run_probe() -> dict:
    """창을 1456 폭으로 띄우고 블록·그림·주석을 차례로 골라 잘린 단추와 리본 이름 상태를 모은다."""
    w = MainWindow(); w.show(); w.resize(1456, 860); pump(0.3)
    w.open_path(_make()); pump(0.4)
    t = w.cur(); v = t.viewer
    if not t.props.isVisible():
        w.right_action("props"); pump(0.2)
    out: dict = {"clipped": {}, "panel_w": QWidget.width(t.props)}
    _click(v, fitz.Point(100.5, 530)); pump(0.2)    # 주석(직사각형 테두리)
    out["clipped"]["annot"] = clipped(t.props)
    out["del_annot"] = t.props.del_btn.text(); out["kind_annot"] = getattr(v.sel, "kind", None)
    w.on_toggle("edit_all", True); pump(0.1)
    for name, pt in (("block", fitz.Point(80, 95)), ("image", fitz.Point(350, 330))):
        _click(v, pt); pump(0.2)
        out["clipped"][name] = clipped(t.props)
        out[f"kind_{name}"] = getattr(v.sel, "kind", None)
        out[f"del_{name}"] = t.props.del_btn.text()
    rb = w.ribbon
    rb.set_tab("comment"); pump(0.1)
    page = rb.pages["comment"]
    btns = [b for b in page.findChildren(QToolButton) if b.text()]
    out["comment_named"] = [b.text() for b in btns if b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon]
    vis = [b for b in page.findChildren(QToolButton) if b.isVisible()]
    out["comment_left"] = min(b.geometry().left() for b in vis)
    out["comment_right"] = page.width() - max(b.geometry().right() for b in vis)
    for tt in list(w.tabs):
        tt.doc.modified = False
    w.close(); pump(0.05)
    return out


def _check(res: dict):
    for name, bad in res["clipped"].items():
        assert bad == [], (name, bad)
    assert res["kind_annot"] == "annot" and res["kind_block"] == "block" and res["kind_image"] == "image"
    assert res["del_annot"] == "고른 주석 삭제"
    assert res["del_block"] == res["del_image"] == "고른 개체 삭제"


def test_props_not_clipped_at_1x():
    _check(run_probe())


def test_props_not_clipped_at_1_5x():
    env = dict(os.environ, QT_SCALE_FACTOR="1.5", QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               APPDATA=tempfile.mkdtemp(prefix="rpdf_fit_15_"))
    code = ("import json, app.tests.test_props_ribbon_fit as T; print('RESULT' + json.dumps(T.run_probe(), ensure_ascii=False))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                       timeout=180)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line is not None, r.stdout[-2000:] + r.stderr[-2000:]
    _check(json.loads(line[len("RESULT"):]))


def test_comment_ribbon_names_by_group_and_centered():
    res = run_probe()
    named = res["comment_named"]
    # 왼쪽 첫 묶음(글자 표시)은 이름이 다 보이고, 묶음 안에서 이름이 일부만 보이는 일은 없다
    for b in ("강조", "영역 강조", "밑줄", "취소선", "구불구불한 선", "색"):
        assert b in named
    groups: list[list[str]] = [[]]
    for it in next(items for key, _, items in ribbon.TABS if key == "comment"):
        if it == ribbon.SEP:
            groups.append([])
        elif it[2]:
            groups[-1].append(it[2])
    for labels in groups:
        shown = [x for x in labels if x in named]
        assert shown in ([], labels), (labels, shown)
    assert abs(res["comment_left"] - res["comment_right"]) <= 12   # 넘쳐도 가운데


def test_ampersand_shows_as_is():
    rb = ribbon.Ribbon(); rb.show(); pump(0.05)
    b = rb.buttons["grid"][0]
    assert b.text() == "눈금자 && 격자"   # 화면에는 「눈금자 & 격자」
    assert ribbon.shown("A & B") == "A && B"
    rb.close()


def test_view_and_form_labels():
    assert ribbon.LABELS["layout_menu"] == "페이지 표시"
    items = {it[0]: it for _, _, its in ribbon.TABS for it in its if it != ribbon.SEP}
    assert ("layout_facing", "양 옆으로 두 페이지 보기") in items["layout_menu"][4]
    more = [m[0] for m in items["form_more"][4] if m != ribbon.MENU_SEP]
    for k in ("recognize", "extract_data", "form_reset"):   # 단추로 나온 것은 더보기에 없다
        assert k not in more
    assert ("form_set_default", "지금 값을 기본값으로 저장") in items["form_more"][4]


def test_disabled_icon_is_fainter():
    ic = icons.icon("save", 16)
    on = ic.pixmap(16, 16, QIcon.Mode.Normal).toImage()
    off = ic.pixmap(16, 16, QIcon.Mode.Disabled).toImage()
    ink = lambda im: max(im.pixelColor(x, y).alpha() for x in range(16) for y in range(16))  # noqa: E731
    assert ink(off) < ink(on) * 0.6


def test_squiggly_icon_has_wave():
    im = icons.pixmap("squiggly", 32).toImage()
    rows = [sum(im.pixelColor(x, y).alpha() > 60 for x in range(32)) for y in range(32)]
    assert sum(rows[:20]) > 0 and sum(rows[24:]) > 0   # 위에 글자, 아래에 물결


# 라운드 24: 단추 여백을 줄여 주석 탭 이름을 더 편다. 창 너비별 이름 보이는 단추 수(배율 1.0, 맑은 고딕).
# 줄이기 전(0.2.26)에는 1456 에서 10개, 다 펴려면 2264px 였다. 지금은 1456 에서 16개, 다 펴려면 1926px.
COMMENT_NAMED_AT = {1100: 7, 1280: 12, 1456: 16, 1700: 20, 1920: 22}


def ribbon_counts(widths) -> dict:
    """리본만 띄워 너비마다 탭별 (이름 보이는 단추 수, 묶음별 펴짐) 을 잰다. 잘린 이름도 모은다."""
    app.setStyleSheet(theme.qss(False))   # 창이 거는 라이트 스킨(여백 규칙이 여기 있다)
    rb = ribbon.Ribbon(); rb.resize(1456, 120); rb.show(); pump(0.2)
    out: dict = {"named": {}, "groups": {}, "clipped": [], "full": {}}
    for key, (compact, extra, btns, groups) in rb._widths.items():
        out["full"][key] = compact + sum(extra)
    for wd in widths:
        rb.resize(wd, 120); pump(0.08)
        for key, (compact, extra, btns, groups) in rb._widths.items():
            on = [b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon for b in btns]
            out["named"].setdefault(key, {})[wd] = sum(on)
            out["groups"].setdefault(key, {})[wd] = [on[g[0]] for g in groups]
            if wd != 1456:
                continue
            rb.set_tab(key); pump(0.05)
            for b, o in zip(btns, on):
                if o and b.width() < b.sizeHint().width():
                    out["clipped"].append((key, b.text(), b.width(), b.sizeHint().width()))
                # 이름 칸 = 단추 폭 - 아이콘만일 때 폭 - Qt 의 아이콘·글자 사이 4px. 이름 글자 폭보다 좁으면 잘린다
                if o:
                    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly); icon_only = b.sizeHint().width()
                    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                    room = b.width() - icon_only - 4
                    need = b.fontMetrics().horizontalAdvance(b.text().replace("&&", "&"))
                    if room < need:
                        out["clipped"].append((key, b.text(), room, need))
    rb.close()
    return out


def test_ribbon_names_by_width():
    res = ribbon_counts(sorted(COMMENT_NAMED_AT))
    assert res["clipped"] == [], res["clipped"]
    got = res["named"]["comment"]
    print("comment named by width", got, "full", res["full"])
    assert got[1456] > 10, got   # 줄이기 전 10개
    for wd, n in COMMENT_NAMED_AT.items():   # 글꼴 차이로 한 묶음 어긋나는 것까지는 넘어간다
        assert abs(got[wd] - n) <= 6, (wd, got)
    assert res["full"]["comment"] < 2264
    ws = sorted(got)
    assert all(got[a] <= got[b] for a, b in zip(ws, ws[1:])), got   # 넓을수록 이름이 줄지 않는다
    # 1456 에서 주석 탭은 앞 묶음부터 펴진다(글자 표시·그리기·그림 묶음), 다른 탭(양식 빼고)은 다 편다
    g = res["groups"]["comment"][1456]
    assert g[:3] == [True, True, True], g
    for key, gs in res["groups"].items():
        if key not in ("comment", "form"):
            assert all(gs[1456]), (key, gs[1456])
