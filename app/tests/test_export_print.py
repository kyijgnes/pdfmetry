"""내보내기 창(dlg_convert)·이미지/XML 변환·인쇄 창 손질 시험."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtPrintSupport import QPrinterInfo  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.engine import convert as C  # noqa: E402
from app.ui import dlg_convert as DC  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_doc(n=3) -> fitz.Document:
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=300, height=400)
        p.insert_text((50, 100), f"Hello {i + 1}", fontsize=20)
    return d


def test_to_images_formats(tmp_path):
    d = make_doc()
    for fmt, ext in (("jpeg", "jpg"), ("bmp", "bmp"), ("gif", "gif"), ("tiff", "tiff"), ("png", "png")):
        files = C.to_images(d, str(tmp_path), fmt=fmt, dpi=40, stem=fmt)
        assert len(files) == 3 and all(f.endswith("." + ext) and os.path.getsize(f) > 0 for f in files)


def test_to_images_merge_and_source_dpi(tmp_path):
    d = make_doc()
    one = C.to_images(d, str(tmp_path), fmt="png", dpi=None, stem="all", merge=True)
    assert one == [os.path.join(str(tmp_path), "all.png")]
    from PIL import Image
    im = Image.open(one[0])
    assert im.height >= 3 * 400 * 150 // 72 - 3          # 그림이 없는 쪽은 150dpi
    assert C.source_dpi(d[0]) == 150


def test_extract_images(tmp_path):
    d = make_doc(2)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False); pix.set_rect(pix.irect, (255, 0, 0))
    d[0].insert_image(fitz.Rect(10, 10, 110, 110), pixmap=pix)
    d[1].insert_image(fitz.Rect(10, 10, 60, 60), pixmap=pix)    # 같은 그림은 한 번만
    files = C.extract_images(d, str(tmp_path), stem="x")
    assert len(files) == 1 and os.path.getsize(files[0]) > 0
    assert C.source_dpi(d[0]) == 72   # 14dpi 짜리 그림이라도 72 아래로는 안 내려간다


def test_to_xml(tmp_path):
    out = C.to_xml(make_doc(2), str(tmp_path / "a.xml"))
    import xml.etree.ElementTree as ET
    root = ET.parse(out).getroot()
    pages = root.findall("page")
    assert len(pages) == 2 and pages[0].find("line").text == "Hello 1" and len(pages[0].find("line").get("bbox").split()) == 4


def test_unique_stem(tmp_path):
    f = str(tmp_path)
    assert DC.unique_stem(f, "a", ".docx") == "a"
    open(tmp_path / "a.docx", "w").close()
    assert DC.unique_stem(f, "a", ".docx") == "a_1"
    open(tmp_path / "a_1.docx", "w").close()
    assert DC.unique_stem(f, "a", ".docx") == "a_2"
    open(tmp_path / "b_p0001.png", "w").close()
    assert DC.unique_stem(f, "b", ".png", multi=True) == "b_1"


def test_convert_dialog_settings_persist(app, tmp_path):
    store: dict = {}
    dlg = DC.ConvertDialog(None, 5, 0, "png", "시험.pdf", str(tmp_path / "시험.pdf"), store)
    assert dlg.name.text() == "시험.png" or dlg.name.text() == "시험"   # 이름만(확장자는 따로)
    st = DC.ConvertSettings(None, "png", 5, 0, None, store.get("png"))
    st.dpi.setCurrentIndex(st.dpi.findData(300)); st.modes["merge"].setChecked(True)
    store["png"] = st.saved()
    again = DC.ConvertSettings(None, "png", 5, 0, None, store["png"])
    assert again.dpi.currentData() == 300 and again.modes["merge"].isChecked()
    dlg2 = DC.ConvertDialog(None, 5, 0, "png", "시험.pdf", str(tmp_path / "시험.pdf"), store)
    v = dlg2.values()
    assert v["mode"] == "merge" and v["dpi"] == 300 and v["pages"] is None and v["out"].endswith("시험.png")


def test_convert_dialog_unique_name(app, tmp_path):
    open(tmp_path / "시험.docx", "w").close()
    dlg = DC.ConvertDialog(None, 2, 0, "docx", "시험.pdf", str(tmp_path / "시험.pdf"), {})
    assert dlg.name.text() == "시험_1" and dlg.values()["out"].endswith("시험_1.docx")


def test_print_dialog_limits(app):
    from app.ui import printing as PR
    if not QPrinterInfo.availablePrinterNames():
        pytest.skip("프린터 없음")
    d = make_doc(4)
    dlg = PR.PrintDialog(None, d, 0, None, unit="in")
    assert dlg.copies.maximum() == 9999 and dlg.scale.maximum() == 800 and dlg.poster_scale.maximum() == 800
    assert dlg.nup_cols.maximum() == 99 and abs(dlg.overlap.maximum() - 2.0) < 0.01 and "in" in dlg.overlap.suffix()
    assert dlg.portrait_btn.isChecked()
    assert dlg.collate.isHidden() and dlg.options().color is True   # 흑백은 기본 끔
    dlg.copies.setValue(2)
    assert not dlg.collate.isHidden()
    assert dlg.options().collate is True
    dlg.mode_btns["poster"].setChecked(True); dlg.overlap.setValue(1.0); dlg.reverse.setChecked(True)
    r = dlg.remember()
    assert r["mode"] == "poster" and abs(r["overlap_mm"] - 25.4) < 0.1 and r["reverse"] is True
    dlg2 = PR.PrintDialog(None, d, 0, r, unit="in")
    assert dlg2._mode() == "poster" and dlg2.reverse.isChecked() and abs(dlg2.overlap.value() - 1.0) < 0.01
    assert dlg2.r_view.isHidden()   # view=None 이면 「최근 보기」 가 안 보인다
    dlg.landscape_btn.setChecked(True); dlg.done(0)
    assert PR.PrintDialog(None, d, 0, None).landscape_btn.isChecked()
    PR.PrintDialog._last_landscape = False


def test_print_dialog_bad_range_resets_subset(app):
    from app.ui import printing as PR
    if not QPrinterInfo.availablePrinterNames():
        pytest.skip("프린터 없음")
    dlg = PR.PrintDialog(None, make_doc(4), 0, None)
    dlg.subset.setCurrentIndex(1)
    dlg.r_custom.setChecked(True); dlg.range_edit.setText("9-99")
    assert dlg.subset.currentIndex() == 0 and dlg.err.text()
