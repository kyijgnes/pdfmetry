"""워터마크·배경: 타일 간격 · 오프셋 · 여러 줄 · 페이지 비율/최대 척도 · 회전 · PDF 한 쪽(암호 포함) · 창의 칸과 켜짐."""
import json
import math

import fitz
import pytest

from app.engine import deco as E
from app.engine import deco_mark as M
from app.engine.tools import EngineError


def make_doc(n=1, w=595, h=842) -> fitz.Document:
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=w, height=h)
        p.insert_text((72, 100), f"Hello {i + 1}", fontsize=14)
    return d


def wm(**kw) -> dict:
    s = {"v": 2, "text": "WM", "file": None, "file_page": 0, "font": {"size": 24}, "position": "center",
         "offset": [0, 0], "tile": False, "tile_gap": [2, 2], "scale_mode": "max", "scale_pct": 50,
         "rotate": 0, "opacity": 1.0}
    s.update(kw)
    return s


def word_boxes(page, word="WM") -> list[fitz.Rect]:
    return [fitz.Rect(w[:4]) for w in page.get_text("words") if w[4] == word]


def make_png(path, w=200, h=100, color=(255, 0, 0)):
    from PIL import Image
    Image.new("RGB", (w, h), color).save(path)
    return str(path)


# ── 엔진 ───────────────────────────────────────────────────────
def test_tile_step_follows_font_size():
    a = M.tile_step({"size": 10}, (2, 2))
    b = M.tile_step({"size": 20}, (2, 2))
    assert a[0] > 0 and a[1] > 0 and abs(b[0] - 2 * a[0]) < 1e-6 and abs(b[1] - 2 * a[1]) < 1e-6
    assert M.tile_step({"size": 10}, (0, 0)) == (0, 0)
    one = M.tile_step({"size": 10}, (1, 1))
    assert abs(M.tile_step({"size": 10}, (5, 3))[0] - 5 * one[0]) < 1e-6


def test_tile_gap_changes_count_and_covers_page():
    counts = []
    for gap in ([0, 0], [2, 2], [10, 10]):
        d = make_doc()
        M.watermark(d, **wm(tile=True, tile_gap=gap))
        boxes = word_boxes(d[0])
        counts.append(d[0].get_text().count("WM"))   # 간격 0 이면 붙어서 한 단어로 읽힌다
        if gap == [2, 2]:   # 쪽 네 귀퉁이 근처까지 덮는다
            assert min(b.x0 for b in boxes) < 60 and max(b.x1 for b in boxes) > 535
            assert min(b.y0 for b in boxes) < 60 and max(b.y1 for b in boxes) > 780
    assert counts[0] > counts[1] > counts[2] > 1


def test_tile_rows_follow_rotation():
    d = make_doc()
    M.watermark(d, **wm(tile=True, rotate=45))
    dirs = {tuple(round(v, 2) for v in ln["dir"]) for b in d[0].get_text("dict")["blocks"] if b["type"] == 0
            for ln in b["lines"] for sp in ln["spans"] if sp["text"].strip() == "WM"}
    assert dirs == {(0.71, -0.71)}


def test_offset_moves_box():
    d0, d1 = make_doc(), make_doc()
    M.watermark(d0, **wm())
    M.watermark(d1, **wm(offset=[30, -40]))
    a, b = word_boxes(d0[0])[0], word_boxes(d1[0])[0]
    assert abs(b.x0 - a.x0 - 30) < 0.5 and abs(b.y0 - a.y0 + 40) < 0.5
    assert abs((a.x0 + a.x1) / 2 - 297.5) < 1   # 가운데


def test_corner_cell_keeps_rotated_box_inside():
    d = make_doc()
    M.watermark(d, **wm(text="CORNER", position="bottom_right", rotate=45, font={"size": 48}))
    r = fitz.Rect()
    for b in d[0].get_text("dict")["blocks"]:
        if b["type"] == 0 and "CORNER" in "".join(s["text"] for ln in b["lines"] for s in ln["spans"]):
            r = fitz.Rect(b["bbox"])
    assert not r.is_empty and r.x1 <= 595.5 and r.y1 <= 842.5 and r.x0 > 300 and r.y0 > 500


def test_multiline_text():
    d = make_doc()
    M.watermark(d, **wm(text="첫줄\n둘째줄"))
    lines = [b for b in word_boxes(d[0], "첫줄") + word_boxes(d[0], "둘째줄")]
    assert len(lines) == 2 and lines[1].y0 > lines[0].y1 - 1


def test_text_page_ratio_and_tile_ignores_it():
    d = make_doc()
    M.watermark(d, **wm(text="WIDE", scale_mode="page", scale_pct=50))
    r = word_boxes(d[0], "WIDE")[0]
    assert abs(r.width - 595 * 0.5) < 595 * 0.06
    d2 = make_doc()
    M.watermark(d2, **wm(text="WIDE", scale_mode="page", scale_pct=50, tile=True))
    assert all(b.height < 40 for b in word_boxes(d2[0], "WIDE"))   # 타일이면 글꼴 크기(24) 그대로


def test_image_scale_modes_and_rotation(tmp_path):
    png = make_png(tmp_path / "a.png", 200, 100)
    d = make_doc()
    M.watermark(d, **wm(text=None, file=png, scale_mode="max", scale_pct=100))
    r = d[0].get_image_rects(d[0].get_images()[0][0])[0]
    assert abs(r.width - 200) < 1 and abs(r.height - 100) < 1 and abs((r.x0 + r.x1) / 2 - 297.5) < 1
    d = make_doc()
    M.watermark(d, **wm(text=None, file=png, scale_mode="page", scale_pct=50))
    r = d[0].get_image_rects(d[0].get_images()[0][0])[0]
    assert abs(r.width - 595 * 0.5) < 1
    d = make_doc()
    M.watermark(d, **wm(text=None, file=png, scale_mode="max", scale_pct=100, rotate=30))
    r = d[0].get_image_rects(d[0].get_images()[0][0])[0]
    t = math.radians(30)
    assert abs(r.width - (200 * math.cos(t) + 100 * math.sin(t))) < 2


def test_image_tile(tmp_path):
    png = make_png(tmp_path / "a.png", 60, 30)
    d = make_doc()
    M.watermark(d, **wm(text=None, file=png, scale_mode="max", scale_pct=100, tile=True))
    xref = d[0].get_images()[0][0]
    assert {im[0] for im in d[0].get_images()} == {xref} and len(d[0].get_image_rects(xref)) > 20   # 그림 객체는 하나


def test_pdf_page_background_centered_and_scaled(tmp_path):
    p = str(tmp_path / "src.pdf")
    src = make_doc(2, 200, 100); src.save(p)
    d = make_doc()
    E.add_decoration(d, "background", v=2, color=None, file=p, file_page=1, scale_mode="max", scale_pct=100,
                     rotate=0, opacity=1.0)
    hit = [w for w in d[0].get_text("words") if w[4] == "Hello" and w[1] > 300]
    assert hit and abs(hit[0][0] - (297.5 - 100 + 72)) < 2   # 200×100 쪽이 가운데에 원래 크기로
    assert "Hello 1" in d[0].get_text()


def test_background_rotated_image_centered(tmp_path):
    png = make_png(tmp_path / "b.png", 300, 100)
    d = make_doc()
    M.background(d, color=None, file=png, scale_mode="max", scale_pct=100, rotate=90, opacity=1.0, v=2)
    r = d[0].get_image_rects(d[0].get_images()[0][0])[0]
    assert abs(r.width - 100) < 2 and abs(r.height - 300) < 2 and abs((r.y0 + r.y1) / 2 - 421) < 1


def test_encrypted_pdf_needs_password_not_stored(tmp_path):
    p = str(tmp_path / "lock.pdf")
    make_doc(1, 200, 100).save(p, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u1", owner_pw="o1")
    M._PASSWORDS.clear()
    assert M.pdf_locked(p)
    with pytest.raises(EngineError):
        M.watermark(make_doc(), **wm(text=None, file=p))
    assert not M.try_password(p, "틀림") and M.try_password(p, "u1") and not M.pdf_locked(p)
    d = make_doc()
    E.add_decoration(d, "watermark", **wm(text=None, file=p))
    assert "Hello 1" in d[0].get_text() and d[0].get_text().count("Hello") == 2
    raw = E.decorations(d, "watermark")[0]["settings"]
    assert "u1" not in json.dumps(raw)
    M._PASSWORDS.clear()


def test_old_batch_style_call_still_works():
    d = make_doc()
    M.watermark(d, text="옛날", position="tile", scale=0.5)
    assert d[0].get_text().count("옛날") > 4


# ── 창 ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pdoc(tmp_path):
    from app.engine.document import Document
    p = str(tmp_path / "꾸미기.pdf"); make_doc(3).save(p)
    return Document(p)


def test_watermark_dialog_defaults(qapp, pdoc):
    from app.ui.dlg_deco_mark import WatermarkDialog
    dlg = WatermarkDialog(None, pdoc, 0)
    s = dlg.settings()
    assert dlg.is_text.isChecked() and s["text"] == "" and not dlg.ok.isEnabled()
    assert s["font"]["size"] == 24 and tuple(s["font"]["color"]) == (0, 0, 0)
    assert s["rotate"] == 45 and s["opacity"] == 1.0 and s["position"] == "center" and s["offset"] == [0, 0]
    assert s["tile"] is False and s["tile_gap"] == [2, 2] and not dlg.gap_x.isEnabled()
    assert dlg.scale.value() == ("page", 50) and dlg.scale.max_pct.value() == 100
    assert dlg.text.placeholderText() == "내용" and dlg.unit == "cm"


def test_watermark_tile_disables_position_and_ratio(qapp, pdoc):
    from app.ui.dlg_deco_mark import WatermarkDialog
    dlg = WatermarkDialog(None, pdoc, 0)
    dlg.is_file.setChecked(True)
    assert dlg.scale.page.isChecked()
    dlg.tile.setChecked(True)
    assert not dlg.pos.isEnabled() and not dlg.off_v.isEnabled() and not dlg.scale.page.isEnabled()
    assert dlg.scale.max.isChecked() and dlg.gap_x.isEnabled() and dlg.gap_y.isEnabled()
    dlg.tile.setChecked(False)
    assert dlg.pos.isEnabled() and dlg.scale.page.isChecked()   # 직전 비율 선택을 되살림
    dlg.is_text.setChecked(True)
    dlg.fit.setChecked(True)
    assert not dlg.font.size.isEnabled() and not dlg.tile.isEnabled()
    assert dlg.settings()["scale_mode"] == "page"
    dlg.fit.setChecked(False)
    assert dlg.font.size.isEnabled() and dlg.tile.isEnabled()


def test_watermark_dialog_values_roundtrip(qapp, pdoc):
    from app.ui.dlg_deco_mark import WatermarkDialog
    dlg = WatermarkDialog(None, pdoc, 0, unit="mm")
    dlg.text.setPlainText("한 줄\n두 줄")
    dlg.off_h.setValue(10); dlg.off_v.setValue(-5)
    dlg.tile.setChecked(True); dlg.gap_x.setCurrentText("15"); dlg.gap_y.setCurrentText("0")
    v = dlg.values()
    assert abs(v["offset"][0] - 10 * 72 / 25.4) < 0.01 and abs(v["offset"][1] + 5 * 72 / 25.4) < 0.01
    assert v["tile_gap"] == [15, 0] and v["text"] == "한 줄\n두 줄"
    again = WatermarkDialog(None, pdoc, 0, update=True, unit="cm")
    again.load({k: x for k, x in v.items() if k != "pages"}, v["pages"])
    w = again.values()
    assert w["tile_gap"] == [15, 0] and w["tile"] and all(abs(a - b) < 0.2 for a, b in zip(v["offset"], w["offset"]))
    again.refresh()
    assert not again.preview.view.pixmap().isNull()


def test_background_dialog_apply_rules(qapp, pdoc, tmp_path):
    from app.ui.dlg_deco_mark import BackgroundDialog
    dlg = BackgroundDialog(None, pdoc, 0)
    s = dlg.settings()
    assert dlg.is_color.isChecked() and tuple(s["color"]) == (1, 1, 1) and not dlg.ok.isEnabled()
    assert s["rotate"] == 0 and s["opacity"] == 1.0 and dlg.scale.value() == ("max", 100)
    dlg.color.color = (0.84, 0.89, 0.79); dlg.changed()   # 색 고르기 창을 닫은 뒤와 같음
    assert dlg.ok.isEnabled()
    dlg.is_file.setChecked(True)
    assert not dlg.ok.isEnabled()
    dlg.file.path.setText(make_png(tmp_path / "bg.png"))
    assert dlg.ok.isEnabled() and dlg.check() is None


def test_locked_pdf_asks_password(qapp, tmp_path, monkeypatch):
    from app.ui import dialogs as D
    from app.ui.dlg_deco_mark import MarkFileRow
    p = str(tmp_path / "lock.pdf")
    make_doc(3).save(p, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="own")
    M._PASSWORDS.clear()
    asked = iter(["틀림", "pw"])
    monkeypatch.setattr(D, "ask_password", lambda parent, name: next(asked))
    r = MarkFileRow()
    r.path.setText(p)
    assert not M.pdf_locked(p) and r.check() is None and r.page.maximum() == 3
    M._PASSWORDS.clear()
    monkeypatch.setattr(D, "ask_password", lambda parent, name: None)
    r2 = MarkFileRow()
    r2.path.setText(p)
    assert r2.check() == "암호가 걸린 파일입니다: lock.pdf"
