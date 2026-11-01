"""꾸미기 공용 틀: 옛 설정 바꾸기 · 매크로 · 글 상자 · 미리보기 · 글꼴 목록 · 공용 창 · 머리글과 페이지 번호의 관계."""
import datetime as dt
import os

import fitz
import pytest

from app.engine import deco as E
from app.engine import fonts as F
from app.engine import tools


def make_doc(n=3) -> fitz.Document:
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Hello {i + 1}", fontsize=14)
    return d


# ── 옛 설정 → 새 꼴 ─────────────────────────────────────────────
def test_upgrade_header_footer_placeholders_and_margin():
    s = E.upgrade_settings("header_footer", {"left": "{file}", "center": "{date}", "footer_right": "{page} / {total}",
                                             "margin_mm": 10, "font_size": 9, "date_format": "%Y.%m.%d",
                                             "pages": [0, 1]}, file_name="보고서", today=dt.date(2026, 11, 1))
    assert s["v"] == 2 and s["pages"] == [0, 1]
    assert s["slots"] == {"top_left": "보고서", "top": "2026.11.01", "bottom_right": "<<##1##>> / <<##n##>>"}
    assert all(abs(v - 10 * 72 / 25.4) < 1e-6 for v in s["margins"].values()) and set(s["margins"]) == {"top", "bottom", "left", "right"}
    assert s["font"]["size"] == 9
    assert E.upgrade_settings("header_footer", s) == s   # 새 꼴은 그대로


def test_upgrade_header_footer_keeps_stored_file_name():
    s = E.upgrade_settings("header_footer", {"center": "{file}", "file_name": "넣을 때 이름"}, file_name="지금 이름")
    assert s["slots"] == {"top": "넣을 때 이름"}


def test_upgrade_watermark_tile_scale_image_and_positions():
    s = E.upgrade_settings("watermark", {"text": "대외비", "position": "tile", "scale": 0.5})
    assert s["tile"] and s["position"] == "center" and s["scale_mode"] == "max" and s["scale_pct"] == 50
    assert s["font"]["size"] == 48 and s["opacity"] == 0.3 and s["rotate"] == 45
    s = E.upgrade_settings("watermark", {"image_path": "a.png", "position": "bottom_right"})
    assert s["file"] == "a.png" and s["position"] == "bottom_right" and s["offset"] == [-36.0, -36.0]
    assert E.upgrade_settings("watermark", {"text": "x"})["position"] == "center"


def test_upgrade_page_number_and_bates_positions():
    s = E.upgrade_settings("page_number", {"style": "{page} / {total}", "position": "footer_right", "start": 3})
    assert s["style"] == "<<##1##>> / <<##n##>>" and s["position"] == "bottom_right" and s["start"] == 3
    s = E.upgrade_settings("bates", {"prefix": "A-", "position": "header_left", "digits": 4})
    assert s["position"] == "top_left" and s["digits"] == 4
    assert E.upgrade_settings("bates", {})["position"] == "bottom_right"


def test_old_document_settings_redraw_the_same(tmp_path):
    """옛 꼴로 적힌 문서의 꾸미기를 업데이트로 다시 넣으면 새 꼴로 적히고 글은 그대로 나온다."""
    d = make_doc()
    E.add_decoration(d, "header_footer", footer_center="쪽 {page}/{total}", margin_mm=12)
    g = E.decorations(d, "header_footer")[0]
    assert g["settings"]["v"] == 2 and g["settings"]["slots"] == {"bottom": "쪽 <<##1##>>/<<##n##>>"}
    E.replace_decorations(d, "header_footer", [g])
    assert "쪽 2/3" in d[1].get_text() and "<<" not in d[1].get_text()


# ── 매크로 ───────────────────────────────────────────────────
def test_expand_macros():
    assert E.expand_macros("<<##1##>>·<<##n##>>·<<##1##/##n##>>", 2, 5) == "2·5·2/5"
    assert E.expand_macros("<<##1##of##n##>>", 2, 5) == "5 페이지 중 2"
    assert E.expand_macros("<<Page##1##>> <<Page##1##of##n##>>", 2, 5) == "페이지 2 5 페이지 중 2"
    assert E.expand_macros("<<모름>> {page}", 2, 5) == "<<모름>> 2"


# ── 글 상자 · 글꼴 ────────────────────────────────────────────
def test_fonts_list_and_fallback():
    fams = F.font_families()
    assert F.DEFAULT_FAMILY in fams and fams == sorted(fams, key=str.lower)
    path, fake_b, fake_i = F.font_file("")
    assert os.path.basename(path).lower() == "malgun.ttf" and not fake_b and not fake_i
    path, fake_b, fake_i = F.font_file(F.DEFAULT_FAMILY, bold=True, italic=True)
    assert os.path.basename(path).lower() == "malgunbd.ttf" and fake_i and not fake_b   # 기울인 파일이 없어 흉내
    assert F.font_file("없는 글꼴")[0] == F.font_file("")[0]


def test_draw_text_box_lines_cells_and_fallback():
    d = make_doc(1)
    p = d[0]
    font = {"name": "Arial", "size": 20, "bold": True, "underline": True}
    box = E.draw_text_box(p, "첫 줄\nsecond line", font, "bottom_right", margins={"right": 30, "bottom": 40})
    assert abs(box.x1 - (595 - 30)) < 0.01 and abs(box.y1 - (842 - 40)) < 0.01
    t = p.get_text()
    assert "첫 줄" in t and "second line" in t
    used = {f[3] for f in p.get_fonts()}
    assert any("Malgun" in n for n in used)          # 한글이 든 글은 맑은 고딕으로 대신
    E.draw_text_box(p, "only latin", font, "top_left")
    assert any("Arial" in f[3] for f in p.get_fonts())
    assert p.get_drawings()                           # 밑줄
    assert E.draw_text_box(p, "  \n ", font) == fitz.Rect()


def test_anchor_point_cells():
    r = fitz.Rect(0, 0, 100, 200)
    m = {"top": 10, "bottom": 20, "left": 5, "right": 15}
    assert E.anchor_point(r, 10, 10, "top_left", m) == fitz.Point(5, 10)
    assert E.anchor_point(r, 10, 10, "bottom_right", m) == fitz.Point(75, 170)
    assert E.anchor_point(r, 10, 10, "center", None, (3, -4)) == fitz.Point(48, 91)


# ── 미리보기 ──────────────────────────────────────────────────
def test_render_preview_numbers_in_range_order(monkeypatch):
    d = make_doc(4)
    seen = []
    real = E._stamp

    def spy(doc, kind, s, targets, order=None, count=None):
        seen.append((order, count)); real(doc, kind, s, targets, order, count)
        seen.append(doc[0].get_text())
    monkeypatch.setattr(E, "_stamp", spy)
    s = {"v": 2, "style": "<<##1##>>/<<##n##>>", "position": "bottom", "start": 1}
    pix = E.render_preview(d, 3, "page_number", s, [1, 3], size_px=200)
    assert max(pix.width, pix.height) == 200
    assert seen[0] == ([1], 2) and "2/2" in seen[1]   # 범위 [2쪽, 4쪽] 의 둘째
    seen.clear()
    E.render_preview(d, 0, "page_number", s, [1, 3])  # 범위 밖이면 꾸미기 없음
    assert not seen
    assert "/" not in d[3].get_text()                 # 원본은 그대로


def test_render_preview_hides_old_kind():
    d = make_doc(1)
    E.add_decoration(d, "watermark", text="옛것")
    plain = E.render_preview(d, 0, "watermark", {"text": "x", "opacity": 1.0, "rotate": 0}, [], hide=("watermark",))
    clean = make_doc(1)
    assert plain.samples == E.render_preview(clean, 0, "watermark", {}, []).samples


def test_tools_reexports_and_batch_watermark(tmp_path):
    assert tools.watermark is not None and tools.DECO_KINDS["page_number"] == "페이지 번호"
    src = tmp_path / "a.pdf"; make_doc(2).save(str(src))
    res = tools.batch([{"op": "watermark", "inputs": [str(src)], "out_dir": str(tmp_path),
                        "options": {"text": "일괄", "position": "tile", "scale": 0.5}}])
    assert res[0]["ok"], res[0]["error"]
    assert "일괄" in fitz.open(res[0]["output"])[0].get_text()


# ── 공용 창 ───────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pdoc(tmp_path):
    from app.engine.document import Document
    p = str(tmp_path / "꾸미기.pdf"); make_doc(3).save(p)
    return Document(p)


@pytest.mark.parametrize("kind", ["watermark", "background", "header_footer", "page_number", "bates"])
def test_dialogs_on_frame_roundtrip(qapp, pdoc, kind):
    from app.ui.dlg_deco import DecoDialog, dialog_class
    dlg = dialog_class(kind)(None, pdoc, 0, unit="mm")
    assert isinstance(dlg, DecoDialog) and dlg.KIND == kind and not dlg.to_template.isVisible()
    if kind == "header_footer":
        dlg.slots["bottom"].setText("{page}")
    v = dlg.values()
    assert v["v"] == 2 and v["pages"] == [0, 1, 2]
    dlg.refresh()
    assert not dlg.preview.view.pixmap().isNull()
    again = dialog_class(kind)(None, pdoc, 0, update=True, unit="cm")
    again.load({k: x for k, x in v.items() if k != "pages"}, [1])
    w = again.values()
    assert w["pages"] == [1]
    for key in v:
        if key == "pages":
            continue
        a, b = v[key], w[key]
        if key == "margins":
            assert all(abs(a[s] - b[s]) < 0.2 for s in a)
        elif key == "font":
            assert {**a, "color": None} == {**b, "color": None}
        else:
            assert a == b or (isinstance(a, (tuple, list)) and list(a) == list(b)), key


def test_apply_follows_required_fields(qapp, pdoc):
    from app.ui.dlg_deco import dialog_class
    wm = dialog_class("watermark")(None, pdoc, 0)
    assert wm.ok.isEnabled()
    wm.text.setPlainText("  ")
    assert not wm.ok.isEnabled()
    wm.is_file.setChecked(True)
    assert not wm.ok.isEnabled()
    wm.file.path.setText("없는 파일.png")
    assert wm.ok.isEnabled() and wm.check() == "이 파일이 존재하지 않습니다."
    hf = dialog_class("header_footer")(None, pdoc, 0)
    assert not hf.ok.isEnabled()
    hf.slots["top"].setText("머리")
    assert hf.ok.isEnabled()
    bt = dialog_class("bates")(None, pdoc, 0)
    bt.start.setText("")
    assert not bt.ok.isEnabled()


def test_margin_grid_units(qapp):
    from app.ui.dlg_deco import MarginGrid
    g = MarginGrid("in", {"top": 36, "bottom": 36, "left": 72, "right": 72})
    assert g.spins["top"].value() == 0.5 and g.spins["left"].value() == 1.0
    g.spins["right"].setValue(-1)
    assert g.value()["right"] == -72


def test_file_row_pdf_page(qapp, tmp_path):
    from app.ui.dlg_deco import FileRow
    p = str(tmp_path / "s.pdf"); make_doc(4).save(p)
    r = FileRow()
    r.path.setText(p)
    assert not r.page_row.isHidden() and r.page.maximum() == 4 and r.check() is None
    r.page.setValue(3)
    assert r.value() == (p, 2)
    bad = tmp_path / "bad.png"; bad.write_bytes(b"not an image")
    r.path.setText(str(bad))
    assert r.page_row.isHidden() and r.check() == "파일을 로드하지 못했습니다. 파일이 손상되었는지 확인하세요."


def test_pdf_page_as_watermark(tmp_path):
    p = str(tmp_path / "s.pdf"); make_doc(2).save(p)
    d = make_doc(1)
    E.add_decoration(d, "watermark", v=2, text=None, file=p, file_page=1, position="center", scale_mode="page",
                     scale_pct=50, rotate=30, opacity=0.5)
    assert "Hello 2" in d[0].get_text()


# ── 흐름: 머리글/바닥글과 페이지 번호 ────────────────────────────
class _Viewer:
    current = 0


class _Tab:
    def __init__(self, doc):
        self.doc, self.viewer, self.n = doc, _Viewer(), 0

    def changed(self, _):
        self.n += 1


def _kinds(doc):
    return sorted(g["kind"] for g in E.decorations(doc.fitz))


def test_header_footer_remove_takes_page_numbers_too(qapp, pdoc, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.ui.dlg_deco import run_decorate
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    t = _Tab(pdoc)
    E.add_decoration(pdoc.fitz, "header_footer", center="머리")
    E.add_decoration(pdoc.fitz, "page_number", style="{page}")
    E.add_decoration(pdoc.fitz, "bates", prefix="B")
    run_decorate(None, t, "page_number", "remove")
    assert _kinds(pdoc) == ["bates", "header_footer"]
    E.add_decoration(pdoc.fitz, "page_number", style="{page}")
    run_decorate(None, t, "header_footer", "remove")
    assert _kinds(pdoc) == ["bates"] and t.n == 2
    pdoc.undo()
    assert _kinds(pdoc) == ["bates", "header_footer", "page_number"]   # 한 번의 삭제가 실행 취소 한 단계


def test_header_footer_update_edits_page_number_as_header(qapp, pdoc, monkeypatch):
    from app.ui import dlg_deco as DD
    from app.ui.dlg_deco_text import HeaderFooterDialog
    monkeypatch.setattr(HeaderFooterDialog, "exec", lambda self: 1)
    loaded = {}
    real_load = HeaderFooterDialog.load

    def spy(self, s, pages):
        loaded["slots"] = dict(s.get("slots") or {}); real_load(self, s, pages)
        self.slots["top"].setText("머리 <<##1##>>")
    monkeypatch.setattr(HeaderFooterDialog, "load", spy)
    E.add_decoration(pdoc.fitz, "page_number", style="{page}", position="footer_center", pages=[0, 1])
    DD.run_decorate(None, _Tab(pdoc), "header_footer", "update")
    assert loaded["slots"] == {"bottom": "<<##1##>>"}
    assert _kinds(pdoc) == ["header_footer"]
    assert "머리 2" in pdoc.fitz[1].get_text() and "머리" not in pdoc.fitz[2].get_text()


def test_add_asks_when_exists(qapp, pdoc, monkeypatch):
    """이미 있으면 「예 · 아니요 · 기존 편집」. 머리글/바닥글은 페이지 번호도 센다. 「아니요」면 아무것도 안 한다."""
    from PySide6.QtWidgets import QMessageBox
    from app.ui import dlg_deco as DD
    asked = []

    def fake_exec(box):
        asked.append(box.text())
        box_buttons = {b.text(): b for b in box.buttons()}
        assert set(box_buttons) == {"예", "아니요", "기존 편집"}
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: box_buttons["아니요"])
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    E.add_decoration(pdoc.fitz, "page_number", style="{page}")
    DD.run_decorate(None, _Tab(pdoc), "header_footer", "template")
    assert asked == ["이 파일에 머리글 & 바닥글을(를) 이미 있습니다. 새로운 것을 추가하시겠습니까?"]
    assert _kinds(pdoc) == ["page_number"]
