"""머리글/바닥글 · 페이지 번호 · 베이츠 번호: 날짜 형식 · 매크로 끼우기 · 번호 세기 · 세 창."""
import datetime as dt
import os

import fitz
import pytest

from app.engine import deco as E
from app.engine import deco_text as X

DAY = dt.date(2026, 3, 7)


def make_doc(n=3) -> fitz.Document:
    d = fitz.open()
    for i in range(n):
        d.new_page(width=595, height=842).insert_text((72, 400), f"Body {i + 1}", fontsize=12)
    return d


def _text(doc, pno) -> str:
    return doc[pno].get_text()


# ── 엔진 ─────────────────────────────────────────────────────
def test_date_formats_26():
    assert len(X.DATE_FORMATS) == 26 and len(set(X.DATE_FORMATS)) == 26 and X.DATE_FORMATS[0] == "m/d"
    got = {f: X.format_date(f, DAY) for f in ("m/d", "mm/dd/yy", "dd.mm.yyyy", "mm.yyy", "yy-mm-dd", "d", "m", "yyyy")}
    assert got == {"m/d": "3/7", "mm/dd/yy": "03/07/26", "dd.mm.yyyy": "07.03.2026", "mm.yyy": "03.026",
                   "yy-mm-dd": "26-03-07", "d": "7", "m": "3", "yyyy": "2026"}


def test_expand_dates_keeps_unknown():
    assert X.expand_dates("<<m/d>> · <<yyyy-mm-dd>> · <<모름>> · <<##1##>>", DAY) == "3/7 · 2026-03-07 · <<모름>> · <<##1##>>"


def test_insert_macro_spacing():
    assert X.insert_macro("쪽 ", 2, "<<##1##>>") == ("쪽 <<##1##>>", 11)
    assert X.insert_macro("<<##1##>>", 9, "<<m/d>>") == ("<<##1##>> <<m/d>>", 17)   # 앞이 >> 면 빈칸 하나
    assert X.insert_macro("ab", 1, "<<n>>") == ("a<<n>>b", 6)


def test_page_number_styles_six():
    assert [n for n, _ in X.PAGE_NUMBER_STYLES] == ["1", "n", "1 of n", "1/n", "Page 1", "Page 1 of n"]
    shown = [E.expand_macros(m, 2, 5) for _, m in X.PAGE_NUMBER_STYLES]
    assert shown == ["2", "5", "5 페이지 중 2", "2/5", "페이지 2", "5 페이지 중 2"]


def test_header_footer_multiline_date_and_margins():
    d = make_doc()
    X.header_footer(d, v=2, slots={"top_left": "첫 줄\n둘째 <<m/d>>", "bottom": "<<##1##/##n##>>"}, date=DAY.isoformat())
    t = _text(d, 1)
    assert "첫 줄" in t and "둘째 3/7" in t and "2/3" in t
    # 기본 여백: 위 0.5in, 왼쪽 1in
    blk = next(b for b in d[0].get_text("dict")["blocks"] if "첫 줄" in str(b))
    assert abs(blk["bbox"][0] - 72) < 2 and abs(blk["bbox"][1] - 36) < 3


def test_page_numbers_style_and_start():
    d = make_doc()
    X.page_numbers(d, v=2, style="<<Page##1##of##n##>>", position="bottom_right", start=4)
    assert "6 페이지 중 5" in _text(d, 1)


@pytest.mark.parametrize("marked, want", [(False, ["0001", "0003"]), (True, ["0001", "0002"])])
def test_bates_counts_skipped_pages_by_default(marked, want):
    d = make_doc(3)
    X.bates_numbers(d, pages=[0, 2], v=2, prefix="", suffix="", start=1, digits=4, position="center",
                    marked_only=marked)
    assert [w for w in want if w in _text(d, 0)] == [want[0]] and want[1] in _text(d, 2) and "000" not in _text(d, 1)


def test_bates_reverse_prefix_suffix_rotate():
    d = make_doc(3)
    X.bates_numbers(d, v=2, prefix="AB", suffix="-Z", start=7, digits=3, reverse=True, position="center",
                    rotate=45, opacity=0.5)
    assert "AB009-Z" in _text(d, 0) and "AB007-Z" in _text(d, 2)


def test_bates_order():
    assert X.bates_order({}, [1, 4, 5]) == ([0, 3, 4], 5)
    assert X.bates_order({"marked_only": True}, [1, 4, 5]) == ([0, 1, 2], 3)
    assert X.bates_order({}, []) == ([], 0)


def test_date_stays_fixed_on_update():
    """날짜는 처음 넣은 날로 남는다(설정의 date). 업데이트로 다시 넣어도 그대로."""
    d = make_doc(1)
    E.add_decoration(d, "header_footer", v=2, slots={"top": "<<yyyy-mm-dd>>"}, date="2020-01-02")
    g = E.decorations(d, "header_footer")[0]
    E.replace_decorations(d, "header_footer", [g])
    assert "2020-01-02" in _text(d, 0)


# ── 창 ───────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pdoc(tmp_path):
    from app.engine.document import Document
    p = str(tmp_path / "보고서.pdf"); make_doc(3).save(p)
    return Document(p)


def test_header_dialog_slots_remembered(qapp, pdoc):
    from app.ui.dlg_deco_text import HeaderFooterDialog
    dlg = HeaderFooterDialog(None, pdoc, 0, unit="in")
    assert dlg.pos.value() == "top_left" and dlg.edit.placeholderText() == "왼쪽 머리글 텍스트 편집"
    assert not dlg.ok.isEnabled() and dlg.font.value()["size"] == 11
    assert [dlg.margins.spins[s].value() for s in ("top", "bottom", "left", "right")] == [0.5, 0.5, 1.0, 1.0]
    assert dlg.date_style.count() == 26 and dlg.style.count() == 6
    dlg.edit.setPlainText("머리")
    dlg.pos.btns["bottom"].setChecked(True)
    assert dlg.edit.toPlainText() == "" and dlg.edit.placeholderText() == "가운데 바닥글 텍스트 편집"
    dlg.style.setCurrentIndex(3); dlg.insert(X.PAGE_NUMBER_STYLES[3][1])
    dlg.insert("<<m/d>>")
    assert dlg.edit.toPlainText() == "<<##1##/##n##>> <<m/d>>"
    dlg.pos.btns["top_left"].setChecked(True)
    assert dlg.edit.toPlainText() == "머리" and dlg.ok.isEnabled()
    v = dlg.values()
    assert v["slots"] == {"top_left": "머리", "bottom": "<<##1##/##n##>> <<m/d>>"} and v["date"] == dt.date.today().isoformat()
    dlg.edit.setPlainText("가" * 600)
    assert len(dlg.edit.toPlainText()) == 512


def test_header_dialog_opens_first_filled_slot(qapp, pdoc):
    from app.ui.dlg_deco_text import HeaderFooterDialog
    dlg = HeaderFooterDialog(None, pdoc, 0, update=True)
    dlg.load({"v": 2, "slots": {"bottom_right": "끝", "top_right": "오른"}, "date": "2021-05-06"}, [0])
    assert dlg.pos.value() == "top_right" and dlg.edit.toPlainText() == "오른"
    assert dlg.values()["date"] == "2021-05-06"


def test_old_document_fills_update_dialog(qapp, pdoc):
    """옛 {page}·{total}·{date}·{file} 문서가 업데이트 창에 새 매크로·글로 채워진다."""
    from app.ui.dlg_deco_text import HeaderFooterDialog, PageNumberDialog
    old = {"left": "{file}", "center": "{date}", "footer_center": "{page} / {total}", "margin_mm": 10}
    s = E.upgrade_settings("header_footer", old, "보고서", today=DAY)
    dlg = HeaderFooterDialog(None, pdoc, 0, update=True, unit="mm")
    dlg.load(s, [0, 1, 2])
    assert dlg.slots["top_left"].text() == "보고서" and dlg.slots["top"].text() == "2026-03-07"
    assert dlg.slots["bottom"].text() == "<<##1##>> / <<##n##>>"
    assert abs(dlg.margins.spins["top"].value() - 10) < 0.01
    pn = PageNumberDialog(None, pdoc, 0, update=True)
    pn.load(E.upgrade_settings("page_number", {"style": "{page} / {total}", "position": "footer_left"}), [0])
    assert pn.style.currentText() == "1 / n" and pn.values()["style"] == "<<##1##>> / <<##n##>>"
    assert pn.pos.value() == "bottom_left"


def test_page_number_dialog_one_slot(qapp, pdoc):
    from app.ui.dlg_deco_text import PageNumberDialog
    dlg = PageNumberDialog(None, pdoc, 0)
    assert dlg.pos.value() == "bottom" and dlg.style.currentText() == "1" and dlg.font.value()["size"] == 11
    dlg.style.setCurrentIndex(5); dlg.pos.btns["top_right"].setChecked(True)
    h = X.page_number_as_header(dlg.settings())
    assert h["slots"] == {"top_right": "<<Page##1##of##n##>>"} and h["margins"] == X.HF_MARGINS


def test_bates_dialog_defaults_and_digits(qapp, pdoc):
    from app.ui.dlg_deco_text import BatesDialog
    dlg = BatesDialog(None, pdoc, 0)
    assert dlg.start.text() == "1" and dlg.start.maxLength() == 8
    assert dlg.prefix.maxLength() == 8 and dlg.suffix.maxLength() == 8
    assert dlg.pos.value() == "center" and dlg.font.value()["size"] == 24 and dlg.margins.value() == E.ZERO_MARGINS
    assert not dlg.reverse.isChecked() and not dlg.marked_only.isChecked()
    dlg.start.setText("0042"); dlg.look.rotate.setValue(-45); dlg.look.opacity.setValue(40)
    v = dlg.settings()
    assert (v["start"], v["digits"], v["rotate"], v["opacity"], v["marked_only"]) == (42, 4, -45.0, 0.4, False)
    dlg.start.setText("")
    assert not dlg.ok.isEnabled()
    again = BatesDialog(None, pdoc, 0, update=True)
    again.load({**v, "marked_only": True}, [1])
    assert again.start.text() == "0042" and again.marked_only.isChecked() and again.look.value() == (-45.0, 0.4)


def test_dialog_previews_draw(qapp, pdoc):
    from app.ui.dlg_deco_text import BatesDialog, HeaderFooterDialog, PageNumberDialog
    for cls in (HeaderFooterDialog, PageNumberDialog, BatesDialog):
        dlg = cls(None, pdoc, 0)
        if cls is HeaderFooterDialog:
            dlg.edit.setPlainText("머리 <<##1##>>")
        dlg.refresh()
        assert not dlg.preview.view.pixmap().isNull()
