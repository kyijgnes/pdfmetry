"""라운드 17 나: PDF → PPT 고칠 수 있는 글 상자(글자 뺀 배경 + 글 덩어리마다 글 상자), 「쪽 그림으로」 는 옛 동작."""
from __future__ import annotations

import io
import os
import re
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_convert_pptx_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: E402
from pptx.oxml.ns import qn  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.engine import convert as C  # noqa: E402
from app.engine import convert_pptx as P  # noqa: E402
from app.ui import dlg_convert as DC  # noqa: E402
from app.ui import dlg_convert_settings as DS  # noqa: E402

LANGS = lambda: ["en-US", "ko"]   # noqa: E731
PT = 12700


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def text_doc() -> fitz.Document:
    """글자층 있는 시험 PDF: 굵은 빨간 영어 제목, 한글 한 줄, 여러 줄 덩어리, 도형 하나, 기울임 한 줄."""
    d = fitz.open()
    p = d.new_page()
    p.draw_rect(fitz.Rect(40, 40, 320, 110), color=(0, 0, 1), fill=(0.9, 0.9, 1))
    p.insert_text((50, 70), "Hello World Title", fontname="hebo", fontsize=18, color=(1, 0, 0))
    p.insert_text((50, 160), "한글 문장이 잘 나오는지 봅니다", fontname="korea", fontsize=12)
    p.insert_textbox(fitz.Rect(50, 200, 420, 330),
                     "여러 줄로 된 글 덩어리입니다. 줄이 바뀌어도 위치가 맞아야 합니다. Second line English text here.",
                     fontname="korea", fontsize=11)
    p.insert_text((50, 400), "Italic line", fontname="heit", fontsize=10, color=(0, 0.5, 0))
    p2 = d.new_page()
    p2.insert_text((72, 100), "둘째 쪽 글자", fontname="korea", fontsize=14)
    return d


def _flat(s: str) -> str:
    return re.sub(r"\s", "", s)


def _boxes(slide):
    return [sh for sh in slide.shapes if sh.shape_type == MSO_SHAPE_TYPE.TEXT_BOX]


def test_text_boxes_cover_page_text(tmp_path):
    d = text_doc()
    out = C.to_pptx(d, str(tmp_path / "a.pptx"), dpi=72)
    prs = Presentation(out)
    assert len(prs.slides) == 2
    # 슬라이드 크기 = 쪽 크기
    assert abs(prs.slide_width - d[0].rect.width * PT) < PT and abs(prs.slide_height - d[0].rect.height * PT) < PT
    for pno, sl in enumerate(prs.slides):
        src = _flat(d[pno].get_text())
        got = _flat("".join(sh.text_frame.text for sh in _boxes(sl)))
        common = sum(min(src.count(c), got.count(c)) for c in set(src))
        assert common / len(src) > 0.9, (pno, src, got)
        # 배경 그림 하나가 맨 아래(첫 도형)
        assert sl.shapes[0].shape_type == MSO_SHAPE_TYPE.PICTURE
        # 노트에도 글자
        assert _flat(sl.notes_slide.notes_text_frame.text)[:4] == src[:4]
    assert "한글문장이잘나오는지봅니다" in _flat("".join(sh.text_frame.text for sh in _boxes(prs.slides[0])))


def test_box_positions_follow_blocks(tmp_path):
    d = text_doc()
    prs = Presentation(C.to_pptx(d, str(tmp_path / "a.pptx"), dpi=72))
    blocks = P.text_blocks(d[0])
    boxes = _boxes(prs.slides[0])
    assert len(boxes) == len(blocks)
    for b, sh in zip(blocks, boxes):
        r = b["bbox"]
        assert abs(sh.left / PT - r.x0) < 2 and abs(sh.top / PT - r.y0) < 2
        assert sh.width / PT >= r.width - 1 and sh.width / PT < r.width * 1.1 + 4
        assert abs(sh.height / PT - r.height) < 2


def test_run_styles_and_korean(tmp_path):
    d = text_doc()
    prs = Presentation(C.to_pptx(d, str(tmp_path / "a.pptx"), dpi=72))
    runs = {r.text.strip(): r for sh in _boxes(prs.slides[0]) for p in sh.text_frame.paragraphs for r in p.runs}
    title = runs["Hello World Title"]
    assert title.font.bold and abs(title.font.size.pt - 18) < 0.6 and str(title.font.color.rgb) == "FF0000"
    assert title._r.rPr.find(qn("a:latin")).get("typeface") == "Arial"
    it = runs["Italic line"]
    assert it.font.italic and not it.font.bold and str(it.font.color.rgb) == "008000"
    ko = runs["한글 문장이 잘 나오는지 봅니다"]
    rpr = ko._r.rPr
    assert rpr.get("lang") == "ko-KR"   # 한글 낱말 단위 줄나눔
    ea = rpr.find(qn("a:ea")).get("typeface")
    assert ea in ("Malgun Gothic", "Batang") or P._norm(ea) in P.installed_fonts()   # 이 PC 에 있는 글꼴로
    # 여러 줄 덩어리는 한 상자에 문단 여러 개
    multi = [sh for sh in _boxes(prs.slides[0]) if "여러 줄로" in sh.text_frame.text][0]
    assert len(multi.text_frame.paragraphs) >= 2 and multi.text_frame.word_wrap is False


def test_background_has_no_text_but_keeps_shapes(tmp_path):
    d = text_doc()
    prs = Presentation(C.to_pptx(d, str(tmp_path / "a.pptx"), dpi=72))
    pix = fitz.Pixmap(io.BytesIO(prs.slides[0].shapes[0].image.blob).getvalue())
    # 한글 줄 자리(y 150~162)는 흰 바탕만
    dark = sum(1 for x in range(50, 200) for y in range(150, 162) if sum(pix.pixel(x, y)[:3]) < 600)
    assert dark == 0
    # 도형(연한 파랑 채움)은 남는다
    assert pix.pixel(300, 100)[:3] != (255, 255, 255)
    # 원본 문서는 그대로(글자를 지운 것은 사본)
    assert "한글" in d[0].get_text()


def test_image_mode_keeps_old_behavior(tmp_path):
    d = text_doc()
    prs = Presentation(C.to_pptx(d, str(tmp_path / "b.pptx"), dpi=40, mode="image"))
    for sl in prs.slides:
        assert len(sl.shapes) == 1 and sl.shapes[0].shape_type == MSO_SHAPE_TYPE.PICTURE
    assert "둘째 쪽" in prs.slides[1].notes_slide.notes_text_frame.text


def _scan_doc(with_ocr_layer: bool) -> fitz.Document:
    """글자를 그림으로만 가진 쪽(스캔). with_ocr_layer 면 OCR 처럼 보이지 않는 글자층(render_mode=3)도."""
    src = fitz.open()
    sp = src.new_page(width=300, height=200)
    sp.draw_rect(fitz.Rect(0, 0, 300, 200), color=None, fill=(1, 1, 0.85))
    sp.insert_text((30, 80), "SCANNED TEXT", fontsize=20)
    png = sp.get_pixmap(dpi=100).tobytes("png")
    d = fitz.open()
    p = d.new_page(width=300, height=200)
    p.insert_image(p.rect, stream=png)
    if with_ocr_layer:
        p.insert_text((30, 80), "SCANNED TEXT", fontsize=20, render_mode=3)
    return d


def test_scan_without_text_is_picture_only(tmp_path):
    prs = Presentation(C.to_pptx(_scan_doc(False), str(tmp_path / "s.pptx"), dpi=72))
    assert [sh.shape_type for sh in prs.slides[0].shapes] == [MSO_SHAPE_TYPE.PICTURE]


def test_ocr_layer_becomes_filled_boxes(tmp_path):
    """OCR 글자층만 있는 쪽: 배경 그림은 그대로(글자 있는 스캔), 글 상자는 그 자리 배경색으로 칠해 덮는다."""
    d = _scan_doc(True)
    prs = Presentation(C.to_pptx(d, str(tmp_path / "o.pptx"), dpi=72))
    boxes = _boxes(prs.slides[0])
    assert len(boxes) == 1 and boxes[0].text_frame.text.strip() == "SCANNED TEXT"
    fill = boxes[0].fill.fore_color.rgb
    r, g, b = int(str(fill)[0:2], 16), int(str(fill)[2:4], 16), int(str(fill)[4:6], 16)
    assert r > 240 and g > 240 and 190 < b < 235   # 연한 노랑 바탕


def test_mixed_page_sizes_are_centered(tmp_path):
    d = fitz.open()
    d.new_page(width=600, height=400).insert_text((50, 50), "wide")
    d.new_page(width=300, height=400).insert_text((50, 50), "narrow")
    prs = Presentation(C.to_pptx(d, str(tmp_path / "m.pptx"), dpi=40))
    sl = prs.slides[1]
    pic = sl.shapes[0]
    assert pic.left > 0   # 가운데로
    box = _boxes(sl)[0]
    assert box.left > pic.left


def test_font_mapping():
    assert P.base_font_name("ABCDEF+NanumGothic-Bold") == "NanumGothic"
    assert P.base_font_name("Helvetica") == "Helvetica"
    assert P.font_for("Helvetica-Bold", "abc")[0] == "Arial"
    assert P.font_for("Times-Roman", "abc")[0] == "Times New Roman"
    assert P.font_for("XYZMyeongjo", "한글")[1] == "Batang"
    assert P.font_for("Type3 (4 0 R)", "한글")[1] == "Malgun Gothic"


def test_convert_dialog_pptx_mode(app):
    st = DC.ConvertSettings(None, "pptx", 3, 0, None, None, {}, LANGS)
    f = st.fields
    assert f.pptx_mode.currentData() == "text" and [f.pptx_mode.itemText(i) for i in range(2)] == ["글 상자로", "쪽 그림으로"]
    assert f.values()["pptx_mode"] == "text"
    f.pptx_mode.setCurrentIndex(f.pptx_mode.findData("image"))
    assert f.values()["pptx_mode"] == "image"
    f.reset()
    assert f.pptx_mode.currentData() == "text"
    # 「설정 변환」 모음 창도 같은 칸·같은 저장
    store: dict = {}
    hub = DS.ConvertSettingsDialog(None, store, {}, start="pptx", langs_fn=LANGS)
    hub.fields["pptx"].pptx_mode.setCurrentIndex(1)
    hub.accept()
    assert store["pptx"]["pptx_mode"] == "image"
    st2 = DC.ConvertSettings(None, "pptx", 3, 0, None, store["pptx"], {}, LANGS)
    assert st2.fields.pptx_mode.currentData() == "image"
