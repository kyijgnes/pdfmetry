"""양식 칸 속성 창 · 서식 · 계산 · 계산 순서(라운드 16 나).

python -m pytest app/tests/test_form_props.py
"""
from __future__ import annotations

import os

import fitz
import pytest

from app.engine import annots as A
from app.engine import form_calc as C
from app.engine import form_props as P
from app.engine.document import Document

R = fitz.Rect


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _doc(pages: int = 1) -> Document:
    f = fitz.open()
    for _ in range(pages):
        f.new_page()
    return Document.from_bytes(f.tobytes(), "t.pdf")


def _reopen(d: Document, tmp_path, name="x.pdf") -> Document:
    p = str(tmp_path / name)
    d.fitz.save(p)
    return Document(p)


def _shown(d: Document, page: int, r: fitz.Rect) -> str:
    return d.fitz[page].get_text("text", clip=r).strip()


def _value(d: Document, page: int, xref: int):
    pg = d.fitz[page]
    return next(w.field_value for w in pg.widgets() if w.xref == xref)


# ── 서식 ────────────────────────────────────────────────────────
def test_number_format_shows_currency(tmp_path):
    d = _doc()
    r = R(50, 50, 220, 80)
    x = A.add_field(d, 0, r, "text")
    P.write(d, [(0, x)], {"format": {"kind": "number", "dec": 2, "sep": 0, "neg": 0, "currency": "$"}})
    A.set_field_value(d, 0, x, "1234.5")
    assert _shown(d, 0, r) == "$1,234.50"
    assert _value(d, 0, x) == "1234.5"          # 값은 그대로, 보이는 글만 서식대로
    d2 = _reopen(d, tmp_path)
    assert _shown(d2, 0, r) == "$1,234.50"
    assert P.read(d2, 0, x)["format"] == {"kind": "number", "dec": 2, "sep": 0, "neg": 0, "currency": "$", "prepend": True}


@pytest.mark.parametrize("fmt,raw,out", [
    ({"kind": "number", "dec": 0, "sep": 2, "neg": 2, "currency": ""}, "-1234", "(1.234)"),
    ({"kind": "percent", "dec": 1, "sep": 0}, "0.256", "25.6%"),
    ({"kind": "date", "fmt": "yyyy-mm-dd"}, "2020/8/20", "2020-08-20"),
    ({"kind": "date", "fmt": "mmm d,yyyy"}, "2020-08-20", "Aug 20,2020"),
    ({"kind": "time", "fmt": "h:MM tt"}, "13:05", "1:05 pm"),
    ({"kind": "special", "which": 2}, "01012345678", "(010) 123-4567"),
    ({"kind": "special", "which": 4, "mask": "999-9999"}, "1234567", "123-4567"),
])
def test_format_values(fmt, raw, out):
    assert C.format_value(fmt, raw) == out
    f, k = C.build_format(fmt)
    back = C.parse_format(f, k)
    assert C.format_value(back, raw) == out


def test_check_input_messages():
    assert "형식과 일치해야" in C.check_input({"kind": "date", "fmt": "yyyy-mm-dd"}, "어제", "날짜1")
    assert C.check_input({"kind": "number", "dec": 2, "sep": 0}, "abc", "n") is not None
    assert C.check_input({"kind": "number", "dec": 2, "sep": 0}, "12.5", "n") is None


def test_range_message():
    d = _doc()
    x = A.add_field(d, 0, R(50, 50, 200, 80), "text")
    P.write(d, [(0, x)], {"validate": {"kind": "range", "min": 0, "max": 100}})
    val = P.read(d, 0, x)["validate"]
    assert C.check_range(val, "150") == "잘못된 값: 0 보다 크거나 같고 100보다 작거나 같아야 합니다."
    assert C.check_range(val, "55") is None


# ── 계산 ────────────────────────────────────────────────────────
def test_sum_updates_and_calc_order(tmp_path):
    d = _doc()
    xs = [A.add_field(d, 0, R(50, 50 + 40 * k, 200, 80 + 40 * k), "text") for k in range(3)]
    names = [P.read(d, 0, x)["name"] for x in xs]
    tot = A.add_field(d, 0, R(50, 200, 200, 230), "text")
    avg = A.add_field(d, 0, R(50, 240, 200, 270), "text")
    P.write(d, [(0, tot)], {"calc": {"kind": "simple", "op": "SUM", "fields": names}})
    P.write(d, [(0, avg)], {"calc": {"kind": "simple", "op": "AVG", "fields": names}})
    for x, v in zip(xs, ("1", "2", "3.5")):
        A.set_field_value(d, 0, x, v)
        C.recalc(d)
    assert _value(d, 0, tot) == "6.5"
    A.set_field_value(d, 0, xs[0], "10")
    assert C.recalc(d) == [0]
    assert _value(d, 0, tot) == "15.5"
    assert C.calc_order(d.fitz) == [tot, avg]
    P.set_calc_order(d, [avg, tot])
    d2 = _reopen(d, tmp_path)
    assert C.calc_order(d2.fitz) == [avg, tot]
    assert [c["xref"] for c in P.calc_fields(d2)] == [avg, tot]
    assert P.read(d2, 0, tot)["calc"] == {"kind": "simple", "op": "SUM", "fields": names}   # 한글 칸 이름 그대로
    P.write(d2, [(0, avg)], {"calc": {"kind": "none"}})
    assert C.calc_order(d2.fitz) == [tot]


# ── 옵션 · 일반 ─────────────────────────────────────────────────
def test_checkbox_star_survives_save(tmp_path):
    d = _doc()
    x = A.add_field(d, 0, R(50, 50, 80, 80), "checkbox", value=True)
    P.write(d, [(0, x)], {"style": "star", "export": "동의", "default_on": True})
    A.set_field_value(d, 0, x, True)
    d2 = _reopen(d, tmp_path)
    p = P.read(d2, 0, x)
    assert (p["style"], p["export"], p["default_on"]) == ("star", "동의", True)
    t, v = d2.fitz.xref_get_key(x, "AP/N")
    on = [int(n) for n in v.replace("R", "").split()[1::3]]
    assert any(b"(H) Tj" in d2.fitz.xref_stream(k) for k in on)
    A.set_field_value(d2, 0, x, True)      # 다시 저장해도 모양이 그대로
    assert P.read(d2, 0, x)["style"] == "star"


def test_combo_export_value_goes_to_fdf(tmp_path):
    from app.engine import tools as T
    d = _doc()
    r = R(50, 50, 200, 80)
    x = A.add_field(d, 0, r, "combo", name="fruit")
    P.write(d, [(0, x)], {"options": [("A1", "Apple"), ("B2", "Banana")], "default": "A1"})
    A.set_field_value(d, 0, x, "B2")
    assert _shown(d, 0, r) == "Banana"        # 보이는 이름
    out = T.export_form_data(d.fitz, str(tmp_path / "f.fdf"))
    data = open(out, "rb").read()
    assert b"B2" in data and b"Banana" not in data
    d2 = _reopen(d, tmp_path)
    p = P.read(d2, 0, x)
    assert p["options"] == [("A1", "Apple"), ("B2", "Banana")] and p["default"] == "A1"


def test_hidden_and_noprint(tmp_path):
    from app.ui.printing import content_doc
    d = _doc()
    hid = A.add_field(d, 0, R(50, 50, 200, 90), "text", value="HIDDEN", fill=(0, 0, 0))
    nop = A.add_field(d, 0, R(50, 120, 200, 160), "text", value="NOPRINT", fill=(0, 0, 0))
    P.write(d, [(0, hid)], {"visibility": "hidden"})
    P.write(d, [(0, nop)], {"visibility": "noprint"})
    pg = d.fitz[0]
    dark = lambda p, r: min(p.get_pixmap(clip=r).samples)   # noqa: E731
    assert dark(pg, R(50, 50, 200, 90)) == 255              # 숨긴 칸은 화면에 안 그려짐
    assert dark(pg, R(50, 120, 200, 160)) < 50             # 출력 안 함 칸은 화면엔 보임
    c = content_doc(d.fitz, True, True, True, [0])
    assert c is not d.fitz
    names = {w.field_name for w in c[0].widgets()}
    assert P.read(d, 0, nop)["name"] not in names and P.read(d, 0, hid)["name"] not in names
    d2 = _reopen(d, tmp_path)
    assert P.read(d2, 0, hid)["visibility"] == "hidden" and P.read(d2, 0, nop)["visibility"] == "noprint"


def test_actions_survive_value_change(tmp_path):
    d = _doc(2)
    x = A.add_field(d, 0, R(50, 50, 200, 80), "text")
    acts = {"U": [{"type": "goto", "page": 1}, {"type": "uri", "uri": "https://example.com"}],
            "Fo": [{"type": "hide", "fields": ["a", "b"], "hide": True}],
            "Bl": [{"type": "reset", "fields": []}], "E": [{"type": "js", "script": "app.alert(1);"}],
            "X": [{"type": "launch", "file": "C:/a.txt"}]}
    P.write(d, [(0, x)], {"actions": acts})
    A.set_field_value(d, 0, x, "값")         # update 가 /A·/AA/Fo 를 지우던 것
    d2 = _reopen(d, tmp_path)
    assert P.read(d2, 0, x)["actions"] == acts


def test_props_round_trip(tmp_path):
    d = _doc()
    x = A.add_field(d, 0, R(50, 50, 250, 80), "text")
    ch = {"tooltip": "이름을 쓰세요", "readonly": True, "required": True, "border_color": (1, 0, 0),
          "fill_color": None, "border_width": 3, "border_style": "D", "font": "Cour", "fontsize": 12,
          "text_color": (0, 0, 1), "align": 2, "default": "기본", "multiline": True, "noscroll": True,
          "password": False, "nospell": True, "comb": True, "maxlen": 6, "locked": True}
    P.write(d, [(0, x)], ch)
    d2 = _reopen(d, tmp_path)
    p = P.read(d2, 0, x)
    for k, v in ch.items():
        assert p[k] == v, k


def test_multi_items_write():
    d = _doc()
    xs = [A.add_field(d, 0, R(50, 50 + 40 * k, 200, 80 + 40 * k), "text") for k in range(3)]
    P.write(d, [(0, x) for x in xs], {"border_color": (0, 1, 0), "readonly": True})
    assert all(P.read(d, 0, x)["readonly"] and P.read(d, 0, x)["border_color"] == (0, 1, 0) for x in xs)


def test_radio_unison_on_parent():
    d = _doc()
    a = A.add_field(d, 0, R(50, 50, 70, 70), "radio", name="g")
    b = A.add_field(d, 0, R(80, 50, 100, 70), "radio", name="g")
    P.write(d, [(0, a)], {"unison": True, "style": "square"})
    assert P.read(d, 0, b)["unison"] is True      # 묶음 깃발
    assert P.read(d, 0, a)["style"] == "square"


# ── 창 ─────────────────────────────────────────────────────────
def test_dialog_live_apply_single_undo_and_lock(qapp):
    from app.ui.dlg_field_props import FieldPropsDialog
    d = _doc()
    x = A.add_field(d, 0, R(50, 50, 200, 80), "text")
    before = d.fitz.tobytes()
    n_undo = len(d._undo)
    seen = []
    dlg = FieldPropsDialog(None, d, [(0, x)], on_change=seen.append)
    assert [dlg.tabs.item(k).text() for k in range(dlg.tabs.count())] == ["일반", "디자인", "옵션", "동작", "서식",
                                                                           "유효성 검사", "계산"]
    dlg.fmt_kind.setCurrentIndex(1)                     # 번호
    dlg.fmt_w["number"]["cur"].setCurrentIndex(1)       # $
    dlg.val_g.button(1).setChecked(True); dlg.vmin.setText("0"); dlg.vmax.setText("100"); dlg._val_apply()
    assert P.read(d, 0, x)["format"]["currency"] == "$"
    assert P.read(d, 0, x)["validate"] == {"kind": "range", "min": 0.0, "max": 100.0}
    assert seen and len(d._undo) == n_undo + 1          # 바꿈 여럿이 되돌리기 하나
    dlg.lock.setChecked(True)
    assert not dlg.stack.isEnabled() and not dlg.tabs.isEnabled()
    assert P.is_locked(d, x)
    dlg.close()
    d.undo()
    assert P.read(d, 0, x)["format"] == {"kind": "none"} and not P.is_locked(d, x)
    assert len(d.fitz.tobytes()) == len(before)


@pytest.mark.parametrize("kind", ["text", "checkbox", "radio", "combo", "list", "button", "signature"])
def test_dialog_opens_each_type(qapp, kind):
    from app.ui.dlg_field_props import TABS_BY_TYPE, FieldPropsDialog
    d = _doc()
    x = A.add_field(d, 0, R(50, 50, 200, 80), kind)
    dlg = FieldPropsDialog(None, d, [(0, x)])
    assert dlg.tabs.count() == len(TABS_BY_TYPE[kind])
    dlg.close()


def test_dialog_mixed_items_common_tabs(qapp):
    from app.ui.dlg_field_props import FieldPropsDialog
    d = _doc()
    a = A.add_field(d, 0, R(50, 50, 200, 80), "text")
    b = A.add_field(d, 0, R(50, 100, 80, 130), "checkbox")
    dlg = FieldPropsDialog(None, d, [(0, a), (0, b)])
    assert [dlg.tabs.item(k).text() for k in range(dlg.tabs.count())] == ["일반", "디자인", "동작"]
    assert not dlg.name.isEnabled()
    dlg.close()


def test_calc_order_dialog(qapp):
    from app.ui.dlg_field_props import CalcOrderDialog
    d = _doc()
    a = A.add_field(d, 0, R(50, 50, 200, 80), "text", name="a")
    s1 = A.add_field(d, 0, R(50, 100, 200, 130), "text", name="s1")
    s2 = A.add_field(d, 0, R(50, 150, 200, 180), "text", name="s2")
    for x in (s1, s2):
        P.write(d, [(0, x)], {"calc": {"kind": "simple", "op": "SUM", "fields": ["a"]}})
    dlg = CalcOrderDialog(None, d)
    assert dlg.order() == [s1, s2]
    dlg.list.setCurrentRow(1); dlg._move(-1)
    assert dlg.order() == [s2, s1]
    P.set_calc_order(d, dlg.order())
    assert C.calc_order(d.fitz) == [s2, s1]
    dlg.close()
