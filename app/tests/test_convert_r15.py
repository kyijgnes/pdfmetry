"""라운드 15 가: 변환 창 저장 위치 기억·설정 칸, 「설정 변환」 모음 창, 변환 엔진 손질(표·한 시트·테두리·표지·주석·이미지 기반 PDF)."""
from __future__ import annotations

import os
import tempfile
import zipfile
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_convert_r15_test_")   # 사용자 설정을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from app.engine import convert as C  # noqa: E402
from app.ui import dlg_convert as DC  # noqa: E402
from app.ui import dlg_convert_settings as DS  # noqa: E402

LANGS = lambda: ["en-US", "ko"]   # noqa: E731  (PowerShell 을 안 부르게)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def table_doc() -> fitz.Document:
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 60), "Before table")
    x0, y0 = 72, 100
    for i in range(4):
        p.draw_line((x0, y0 + i * 20), (x0 + 300, y0 + i * 20))
    for j in range(4):
        p.draw_line((x0 + j * 100, y0), (x0 + j * 100, y0 + 60))
    for i in range(3):
        for j in range(3):
            p.insert_text((x0 + j * 100 + 5, y0 + i * 20 + 14), f"c{i}{j}")
    p.insert_text((72, 220), "After table")
    p.add_text_annot((300, 300), "note hello")
    d.new_page().insert_text((72, 72), "page two")
    return d


# ── 엔진 ──
def test_word_table(tmp_path):
    from docx import Document
    out = C.to_word(table_doc(), str(tmp_path / "t.docx"))
    dx = Document(out)
    assert len(dx.tables) == 1
    assert [c.text for c in dx.tables[0].rows[1].cells] == ["c10", "c11", "c12"]
    paras = [p.text for p in dx.paragraphs]
    assert "Before table" in paras and "After table" in paras and not any("c11" in p for p in paras)


def test_rtf_table_and_picture(tmp_path):
    d = table_doc()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False); pix.clear_with(200)
    d[1].insert_image(fitz.Rect(100, 300, 200, 400), pixmap=pix)
    s = open(C.to_rtf(d, str(tmp_path / "t.rtf")), encoding="ascii").read()
    assert "\\intbl" in s and "c11\\cell" in s and "\\pngblip" in s


def test_excel_one_sheet(tmp_path):
    from openpyxl import load_workbook
    d = table_doc()
    wb = load_workbook(C.to_excel(d, str(tmp_path / "a.xlsx")))
    assert wb.sheetnames == ["p1", "p2"]
    rows = [[c.value for c in r] for r in wb["p1"].iter_rows()]
    assert rows[0][0] == "Before table" and ["c10", "c11", "c12"] in rows and rows[-1][0] == "After table"   # 표 밖 글도 같은 시트
    wb = load_workbook(C.to_excel(d, str(tmp_path / "b.xlsx"), one_sheet=True))
    assert len(wb.sheetnames) == 1
    vals = [c.value for r in wb.active.iter_rows() for c in r]
    assert "page two" in vals and "c22" in vals


def test_image_merge_frame(tmp_path):
    from PIL import Image
    files = C.to_images(table_doc(), str(tmp_path), fmt="png", dpi=40, merge=True, frame_w=3, frame_color="#FF0000", stem="m")
    im = Image.open(files[0]).convert("RGB")
    w1 = fitz.open().new_page().rect.width * 40 / 72
    assert im.getpixel((0, 0)) == (255, 0, 0) and im.getpixel((2, 30)) == (255, 0, 0)
    assert im.getpixel((3, 30)) != (255, 0, 0) and abs(im.width - (w1 + 6)) <= 1
    half = C.to_images(table_doc(), str(tmp_path), fmt="png", dpi=40, merge=True, zoom=50, stem="h")
    assert abs(Image.open(half[0]).width - w1 / 2) <= 2
    plain = C.to_images(table_doc(), str(tmp_path), fmt="png", dpi=40, frame_w=3, frame_color="#FF0000", stem="p")
    assert Image.open(plain[0]).convert("RGB").getpixel((0, 0)) == (255, 255, 255)   # 쪽마다 한 장에는 테두리 없음


def test_epub_cover(tmp_path):
    cover = str(tmp_path / "c.png")
    open(cover, "wb").write(table_doc()[0].get_pixmap(dpi=20).tobytes("png"))
    out = C.to_epub(table_doc(), str(tmp_path / "t.epub"), cover=cover)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        assert "OEBPS/cover.png" in names and 'properties="cover-image"' in opf
        assert opf.index('idref="cover"') < opf.index('idref="p1"')
    with zipfile.ZipFile(C.to_epub(table_doc(), str(tmp_path / "n.epub"))) as z:
        assert "cover-image" not in z.read("OEBPS/content.opf").decode("utf-8")


def test_text_annot_contents(tmp_path):
    s = open(C.to_text(table_doc(), str(tmp_path / "t.txt")), encoding="utf-8").read()
    first = s.split("\f")[0]
    assert first.rstrip().endswith("note hello") and "page two" in s
    s2 = open(C.to_text(table_doc(), str(tmp_path / "u.txt"), annots=False), encoding="utf-8").read()
    assert "note hello" not in s2


def test_image_pdf_no_text(tmp_path):
    out = C.to_image_pdf(table_doc(), str(tmp_path / "i.pdf"), dpi=60)
    d = fitz.open(out)
    assert len(d) == 2 and all(not p.get_text().strip() for p in d) and d[0].get_images()
    assert not list(d[0].annots())


def test_scan_excel_ocr(tmp_path):
    """스캔 PDF 를 「스캔된 PDF」 OCR 로 Excel 에: 글자가 나온다. OCR 이 안 되는 PC 는 건너뜀."""
    from openpyxl import load_workbook
    from app.engine import ocr as O
    from app.ui.window import MainWindow
    langs = O.available_languages()
    if not any(l.lower().startswith(("ko", "en")) for l in langs):
        pytest.skip("문자 인식 언어 없음")
    src = fitz.open(); p = src.new_page(width=400, height=200)
    p.insert_text((40, 100), "HELLO 2026", fontsize=36)
    scan = fitz.open(); sp = scan.new_page(width=400, height=200); sp.insert_image(sp.rect, pixmap=p.get_pixmap(dpi=200))
    assert not scan[0].get_text().strip()
    probe = fitz.open(); probe.insert_pdf(scan)
    try:
        O.ocr_pages(probe, lang=["en-US"] if "en-US" in langs else langs[:1], dpi=200)
    except Exception:  # noqa: BLE001
        pytest.skip("ocr_pages 가 언어 목록을 아직 못 받음(덩어리 나 합친 뒤)")
    fake = SimpleNamespace(prefs={"ocr_langs": [t for t in langs if t.lower().startswith(("en", "ko"))]})
    MainWindow._ocr_before_convert(fake, scan, None, False)
    wb = load_workbook(C.to_excel(scan, str(tmp_path / "s.xlsx")))
    vals = " ".join(str(c.value) for r in wb.active.iter_rows() for c in r if c.value)
    assert "2026" in vals


# ── 화면 ──
def test_output_location_remembered(app, tmp_path):
    doc_path = str(tmp_path / "원본" / "시험.pdf")
    os.makedirs(os.path.dirname(doc_path))
    out_store: dict = {}
    dlg = DC.ConvertDialog(None, 3, 0, "docx", "시험.pdf", doc_path, {}, out_store, {}, LANGS)
    assert dlg.where.currentText() == "원본 파일 위치" and dlg.folder.text() == os.path.dirname(doc_path)
    assert not dlg.folder.isEnabled()
    custom = str(tmp_path / "지정")
    os.makedirs(custom)
    dlg.where.setCurrentIndex(1); dlg.folder.setText(custom)
    dlg.accept()
    assert out_store == {"custom": True, "folder": custom}
    again = DC.ConvertDialog(None, 3, 0, "docx", "시험.pdf", doc_path, {}, out_store, {}, LANGS)
    assert again.where.currentText() == "폴더 지정" and again.folder.text() == custom
    assert again.values()["out"] == os.path.join(custom, "시험.docx")
    again.where.setCurrentIndex(0)
    assert again.folder.text() == os.path.dirname(doc_path)
    again.where.setCurrentIndex(1)
    assert again.folder.text() == custom


def test_dialog_order_name_and_errors(app, tmp_path, monkeypatch):
    dlg = DC.ConvertDialog(None, 3, 0, "docx", "시험.pdf", str(tmp_path / "시험.pdf"), {}, {}, {}, LANGS)
    assert dlg.fmt.itemData(0) == "hwpx" and dlg.fmt.findData("imgpdf") >= 0 and dlg.fmt.findData("pdfa") >= 0
    ok = dlg.bb.button(QDialogButtonBox.StandardButton.Ok)
    dlg.name.setText(""); assert not ok.isEnabled()
    dlg.name.setText("a"); assert ok.isEnabled()
    assert DC.check_folder("") == DC.ERR_INVALID
    assert DC.check_folder(str(tmp_path / "없음")) == DC.ERR_FOLDER
    assert DC.check_folder("C:\\a|b") == DC.ERR_PATH
    assert DC.check_folder("C:\\" + "a" * 300) == DC.ERR_LONG
    made = str(tmp_path / "새" / "폴더")
    assert DC.check_folder(made, create=True) is None and os.path.isdir(made)


def test_settings_fields(app, tmp_path):
    prefs: dict = {}
    st = DC.ConvertSettings(None, "xlsx", 3, 0, None, None, prefs, LANGS)
    assert st.fields.ocr.currentData() == "scan" and st.fields.lang.langs() == ["en-US", "ko"]
    st.fields.one_sheet.setChecked(True); st.fields.ocr.setCurrentIndex(st.fields.ocr.findData("all"))
    st.fields.lang.set_langs(["ko"])
    st.accept()
    assert st.saved() == {"one_sheet": True, "ocr": "all"} and prefs["ocr_langs"] == ["ko"]
    st2 = DC.ConvertSettings(None, "xlsx", 3, 0, None, st.saved(), prefs, LANGS)
    assert st2.fields.one_sheet.isChecked() and st2.fields.lang.langs() == ["ko"]
    st2.findChild(QDialogButtonBox).buttons()   # [기본] 이 있다
    st2._reset()
    assert not st2.fields.one_sheet.isChecked() and st2.fields.ocr.currentData() == "scan"
    # 언어 0개면 막힘
    st2.fields.lang.set_langs([])
    assert st2.fields.error()[0] == DC.LANG_NONE
    # 이미지: 품질 여덟, 합치기일 때만 확대·테두리
    im = DC.ConvertSettings(None, "png", 3, 0, None, None, prefs, LANGS)
    assert [im.fields.dpi.itemData(i) for i in range(im.fields.dpi.count())] == [0, 72, 96, 120, 150, 300, 450, 600]
    assert im.fields.lang is None and not im.fields.form.isRowVisible(im.fields.zoom)
    im.fields.modes["merge"].setChecked(True)
    assert im.fields.form.isRowVisible(im.fields.frame_w)
    im.fields.frame_w.setValue(3); im.fields.frame_color.color = (1, 0, 0)
    v = im.saved()
    assert v["mode"] == "merge" and v["frame_w"] == 3 and v["frame_color"] == "#FF0000" and v["zoom"] == 100
    # PDF/A: 쪽 범위만
    pa = DC.ConvertSettings(None, "pdfa", 3, 0, None, None, prefs, LANGS)
    assert pa.saved() == {} and pa.fields.ocr is None
    # EPUB 표지
    ep = DC.ConvertSettings(None, "epub", 3, 0, None, None, prefs, LANGS)
    assert ep.fields.cover_off.isChecked() and not ep.fields.cover_path.isEnabled()
    ep.fields.cover_on.setChecked(True)
    assert ep.fields.error()[0].startswith("표지")
    pic = str(tmp_path / "c.png"); open(pic, "wb").write(b"x")
    ep.fields.cover_path.setText(pic)
    assert ep.fields.error() is None and ep.saved()["cover"] == pic


def test_settings_hub_shares_store(app):
    store: dict = {}
    prefs: dict = {"ocr_langs": ["en-US", "ko"]}
    hub = DS.ConvertSettingsDialog(None, store, prefs, start="xlsx", langs_fn=LANGS)
    assert hub.stack.currentIndex() == 1
    assert [b.text() for b in hub.group.buttons()] == ["Word", "Excel", "PowerPoint", "이미지", "텍스트", "RTF", "Epub", "HTML"]
    hub.fields["xlsx"].one_sheet.setChecked(True)
    hub.fields["png"].modes["merge"].setChecked(True); hub.fields["png"].frame_w.setValue(3)
    hub.fields["docx"].ocr.setCurrentIndex(hub.fields["docx"].ocr.findData("off"))
    hub.fields["txt"].lang.list.item(0).setCheckState(Qt.CheckState.Unchecked)   # 다른 칸 언어도 같이
    assert hub.fields["html"].lang.langs() == ["ko"]
    hub.accept()
    assert store["xlsx"]["one_sheet"] is True and store["docx"]["ocr"] == "off" and prefs["ocr_langs"] == ["ko"]
    assert store["jpg"]["mode"] == "merge" and store["tiff"]["frame_w"] == 3
    # 변환 창 [설정] 이 그 값을 그대로
    st = DC.ConvertSettings(None, "xlsx", 3, 0, None, store.get("xlsx"), prefs, LANGS)
    assert st.fields.one_sheet.isChecked() and st.fields.lang.langs() == ["ko"]
    dlg = DC.ConvertDialog(None, 3, 0, "png", "시험.pdf", None, store, {}, prefs, LANGS)
    assert dlg.values()["frame_w"] == 3 and dlg.values()["mode"] == "merge"


def test_done_box(app, tmp_path):
    f = str(tmp_path / "a.docx")
    box = DC.convert_done_box(None, "Word", [f], str(tmp_path), exec_=False)
    assert box.text().startswith("Word(으)로 변환되었습니다.") and box.file_btn is not None and box.folder_btn is not None
    box2 = DC.convert_done_box(None, "PNG", [f, f], str(tmp_path), exec_=False)
    assert "폴더에 있습니다" in box2.text() and box2.file_btn is None


# ── 창 이음 ──
def test_window_convert_glue(app, tmp_path, monkeypatch):
    """리본 「이미지 기반 PDF로」 · 「설정 변환」 이 창과 설정을 잇는다."""
    import time
    from app.ui import window as W

    def pump(sec=0.15):
        end = time.perf_counter() + sec
        while time.perf_counter() < end:
            app.processEvents(); time.sleep(0.005)
    monkeypatch.setattr(DC, "_LANGS", ["en-US", "ko"])   # PowerShell 을 안 부르게
    src = str(tmp_path / "글.pdf"); table_doc().save(src)
    w = W.MainWindow(); w.show(); pump(0.05)
    try:
        w.open_path(src); pump(0.25)

        def hub_exec(self):
            self.fields["xlsx"].one_sheet.setChecked(True); self.accept(); return 1
        monkeypatch.setattr(DS.ConvertSettingsDialog, "exec", hub_exec)
        w.on_action("convert_settings")
        assert w.settings["convert"]["xlsx"]["one_sheet"] is True
        seen = {}

        def conv_exec(self):
            seen["fmt"] = self.key(); seen["one"] = self.store["xlsx"]["one_sheet"]; self.accept(); return 1
        monkeypatch.setattr(DC.ConvertDialog, "exec", conv_exec)
        monkeypatch.setattr(DC, "convert_done_box", lambda *a, **k: seen.setdefault("done", a))
        w.on_action("to_imgpdf"); pump(0.3)
        assert seen["fmt"] == "imgpdf" and seen["one"] is True
        out = str(tmp_path / "글_1.pdf")   # 원본 「글.pdf」 가 같은 폴더에 있어 비켜 감
        assert os.path.isfile(out) and not fitz.open(out)[0].get_text().strip()
        assert seen["done"][1] == "이미지 기반 PDF" and seen["done"][2] == [out]
        assert w.settings["convert_out"] == {"custom": False}
    finally:
        for t in list(w.tabs):
            t.doc.modified = False
        w.close(); pump(0.05)
