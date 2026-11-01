"""꾸미기 템플릿: 저장·읽기·파일 복사본 · 기본 제공 · 쪽 범위 · 「템플릿에 추가」 · 템플릿 창(추가·편집)."""
import os

import fitz
import pytest

from app.engine import deco as E
from app.engine import deco_store as S


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    """템플릿을 실제 %APPDATA% 가 아니라 임시 폴더에 쓴다."""
    root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(root))
    return root


def make_doc(n=3) -> fitz.Document:
    d = fitz.open()
    for i in range(n):
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Hello {i + 1}", fontsize=14)
    return d


def png(path) -> str:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False); pix.clear_with(200)
    pix.save(str(path))
    return str(path)


# ── 저장 ──────────────────────────────────────────────────────
def test_folder_is_under_appdata(appdata):
    assert S.folder() == os.path.join(str(appdata), "PDFmetry", "decorations")
    S.save("watermark", "대외비", S.builtins("watermark")[0]["settings"])
    assert os.path.isfile(os.path.join(S.folder(), "watermark.json"))


@pytest.mark.parametrize("kind", list(E.DECO_KINDS))
def test_builtins_two_each_and_drawable(kind):
    b = S.builtins(kind)
    assert len(b) == 2 and all(t["builtin"] and t["range"] == S.ALL_PAGES for t in b)
    d = make_doc(2)
    for t in b:
        E.add_decoration(d, kind, **t["settings"])
    assert len(E.decorations(d, kind)) == 2


def test_builtin_values():
    w = S.builtins("watermark")
    assert [t["name"] for t in w] == ["비밀", "복사 금지"]
    assert all(t["settings"]["rotate"] == 45 and t["settings"]["opacity"] == 0.3 and t["settings"]["font"]["size"] == 80
               and not t["settings"]["tile"] for t in w)
    bg = [tuple(round(c * 255) for c in t["settings"]["color"]) for t in S.builtins("background")]
    assert bg == [(214, 226, 202), (227, 220, 199)]
    hf = S.builtins("header_footer")
    assert [t["settings"]["slots"] for t in hf] == [{"bottom": "<<Page##1##>>"}, {"bottom": "<<##1##/##n##>>"}]
    assert all(t["settings"]["font"]["size"] == 14 and t["settings"]["start_number"] == 1 for t in hf)
    bt = S.builtins("bates")
    assert [S.summary("bates", t["settings"]) for t in bt] == ["0001", "AAA0001"]
    s = bt[0]["settings"]
    assert s["position"] == "top_right" and s["font"]["size"] == 14
    assert abs(s["margins"]["right"] - 72) < 1e-6 and abs(s["margins"]["top"] - 1.25 * 72 / 2.54) < 1e-6
    d = make_doc(1)
    E.add_decoration(d, "page_number", **S.builtins("page_number")[0]["settings"])
    assert "페이지 1" in d[0].get_text()


def test_save_find_update_remove():
    s = {**S.builtins("watermark")[0]["settings"], "text": "검토용"}
    t = S.save("watermark", " 검토 ", s, {"how": "odd", "custom": "1-5"})
    assert t["name"] == "검토" and S.find_name("watermark", "검토")["id"] == t["id"]
    got = S.templates("watermark")
    assert len(got) == 1 and got[0]["settings"]["text"] == "검토용" and got[0]["range"] == {"how": "odd", "custom": "1-5"}
    S.save("watermark", "검토", {**s, "text": "바꿈"}, None, t["id"])
    got = S.templates("watermark")
    assert len(got) == 1 and got[0]["settings"]["text"] == "바꿈" and got[0]["range"] == S.ALL_PAGES
    assert S.remove("watermark", t["id"]) and S.templates("watermark") == [] and not S.remove("watermark", t["id"])


def test_blank_name_shows_id():
    t = S.save("bates", "", S.builtins("bates")[0]["settings"])
    assert S.display_name(S.templates("bates")[0]) == t["id"]


def test_file_template_keeps_copy(tmp_path):
    src = png(tmp_path / "도장.png")
    s = {**S.builtins("watermark")[0]["settings"], "text": None, "file": src}
    t = S.save("watermark", "도장", s)
    copy = t["settings"]["file"]
    assert os.path.dirname(copy) == os.path.join(S.folder(), "files") and copy.endswith(f"{t['id']}.png")
    os.remove(src)   # 원본이 사라져도 복사본으로 넣는다
    got = S.templates("watermark")[0]
    assert got["settings"]["file"] == copy and got["source"] == src
    d = make_doc(1)
    E.add_decoration(d, "watermark", **got["settings"])
    assert d[0].get_images()
    S.save("watermark", "도장", got["settings"], None, t["id"])   # 그대로 고치면 복사본도 그대로
    assert os.path.isfile(copy) and S.templates("watermark")[0]["source"] == src
    other = png(tmp_path / "새것.png")
    S.save("watermark", "도장", {**s, "file": other}, None, t["id"])
    assert S.templates("watermark")[0]["settings"]["file"] == copy   # 같은 id·같은 확장자 자리에 새로 복사
    S.remove("watermark", t["id"])
    assert not os.path.exists(copy)


def test_file_template_missing_source_raises(tmp_path):
    with pytest.raises(OSError):
        S.save("background", "없음", {**S.builtins("background")[0]["settings"], "file": str(tmp_path / "없음.png")})
    assert S.templates("background") == []


def test_broken_store_reads_empty():
    os.makedirs(S.folder(), exist_ok=True)
    with open(os.path.join(S.folder(), "bates.json"), "w", encoding="utf-8") as fp:
        fp.write("{깨짐")
    assert S.templates("bates") == []


def test_range_pages():
    assert S.range_pages(None, 4) == [0, 1, 2, 3]
    assert S.range_pages({"how": "even"}, 4) == [1, 3]
    assert S.range_pages({"how": "odd", "custom": "2-4"}, 5) == [2]
    for bad in ({"custom": "5"}, {"custom": "1-9"}, {"how": "even", "custom": "1"}):
        with pytest.raises(ValueError):
            S.range_pages(bad, 4)


# ── 창 ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pdoc(tmp_path):
    from app.engine.document import Document
    p = str(tmp_path / "꾸미기.pdf"); make_doc(3).save(p)
    return Document(p)


class _Viewer:
    current = 0


class _Tab:
    def __init__(self, doc):
        self.doc, self.viewer, self.n = doc, _Viewer(), 0

    def changed(self, _):
        self.n += 1


def test_add_to_template_on_apply(qapp, pdoc, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.ui.dlg_deco import dialog_class
    dlg = dialog_class("watermark")(None, pdoc, 0)
    assert not dlg.to_template.isHidden() and not dlg.tpl_name.isEnabled()
    dlg.text.setPlainText("초안")
    dlg.rng._custom("2-3")
    dlg.to_template.setChecked(True)
    dlg.accept()
    got = S.templates("watermark")
    assert dlg.result() and [t["name"] for t in got] == ["초안"] and got[0]["range"] == {"how": "all", "custom": "2-3"}
    # 이름이 겹치면 묻는다: 아니요면 창이 그대로, 예면 바꾼다
    asked = []

    def answer(yes):
        def q(_p, _t, text, *a, **k):
            asked.append(text)
            return QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        return staticmethod(q)
    dlg = dialog_class("watermark")(None, pdoc, 0)
    dlg.text.setPlainText("다른 글"); dlg.to_template.setChecked(True); dlg.tpl_name.setText("초안")
    monkeypatch.setattr(QMessageBox, "question", answer(False))
    dlg.accept()
    assert not dlg.result() and asked == ['이름 "초안"이(가) 이미 존재합니다. 내용을 바꾸시겠습니까?']
    monkeypatch.setattr(QMessageBox, "question", answer(True))
    dlg.accept()
    got = S.templates("watermark")
    assert dlg.result() and len(got) == 1 and got[0]["settings"]["text"] == "다른 글"


def test_template_window_add_many_one_undo(qapp, pdoc, monkeypatch):
    from app.ui import dlg_deco as DD
    from app.ui.dlg_deco_template import TemplateDialog
    S.save("watermark", "내 것", {**S.builtins("watermark")[0]["settings"], "text": "내 것"}, {"how": "all", "custom": "2"})
    seen = {}

    def run(self):
        seen["title"] = self.windowTitle()
        seen["cards"] = (len(self.builtin), len(self.custom))
        assert not self.ok.isEnabled()
        self.picked = [self.builtin[1]["id"], self.custom[0]["id"]]
        self.changed(); self.refresh()
        assert self.ok.isEnabled() and not self.preview.view.pixmap().isNull()
        self.accept()
        return self.result()
    monkeypatch.setattr(TemplateDialog, "exec", run)
    t = _Tab(pdoc)
    DD.run_decorate(None, t, "watermark", "template")
    assert seen == {"title": "워터마크 추가", "cards": (2, 1)}
    groups = E.decorations(pdoc.fitz, "watermark")
    assert [(g["settings"]["text"], g["pages"]) for g in groups] == [("복사 금지", [0, 1, 2]), ("내 것", [1])]
    assert t.n == 1
    pdoc.undo()
    assert E.decorations(pdoc.fitz, "watermark") == []   # 여러 장을 넣어도 실행 취소 한 단계


def test_template_window_range_error(qapp, pdoc, monkeypatch):
    from app.ui import dlg_deco_template as T
    S.save("bates", "넷째 쪽", S.builtins("bates")[0]["settings"], {"how": "all", "custom": "4"})
    warned = []
    monkeypatch.setattr(T, "_warn", lambda _p, text: warned.append(text))
    dlg = T.TemplateDialog(None, pdoc, "bates")
    dlg.picked = [dlg.custom[0]["id"]]
    dlg.accept()
    assert warned == ["유효한 페이지 범위를 입력하십시오."] and not dlg.result()


def test_template_window_cards_and_delete(qapp, pdoc, monkeypatch):
    from app.ui import dlg_deco_template as T
    S.save("background", "하늘", {**S.builtins("background")[0]["settings"], "color": (0.8, 0.9, 1)})
    dlg = T.TemplateDialog(None, pdoc, "background")
    cards = dlg.scroll.widget().findChildren(T.Card)
    assert [c.name.text() for c in cards] == ["연두빛 회색", "양피지", "새로 만들기", "하늘"]
    assert all(not c.pick.icon().isNull() for c in cards if c.name.text() != "새로 만들기")
    cards[0].pick.click()
    assert dlg.picked == [dlg.builtin[0]["id"]] and dlg.ok.isEnabled()
    asked = []
    monkeypatch.setattr(T, "ask", lambda _p, title, text: asked.append((title, text)) or True)
    dlg.delete_template(dlg.custom[0])
    assert asked == [("배경화면 삭제", '"하늘"을(를) 삭제하시겠습니까?')] and S.templates("background") == []
    assert dlg.picked == [dlg.builtin[0]["id"]]   # 다시 늘어놓아도 고른 것은 그대로


def test_template_window_new_and_edit(qapp, pdoc, monkeypatch):
    from app.ui import dlg_deco_template as T
    from app.ui.dlg_deco_text import BatesDialog

    def fill(self):
        self.prefix.setText("NEW"); self.tpl_name.setText("새 베이츠")
        assert self.to_template.isHidden() and self.ok.text() == "저장"
        self.accept(); return self.result()
    monkeypatch.setattr(BatesDialog, "exec", fill)
    dlg = T.TemplateDialog(None, pdoc, "bates")
    dlg.new_template()
    assert [t["name"] for t in dlg.custom] == ["새 베이츠"] and dlg.picked == [dlg.custom[0]["id"]]

    def edit(self):
        assert self.prefix.text() == "NEW" and self.tpl_name.text() == "새 베이츠"
        self.prefix.setText("FIX"); self.accept(); return self.result()
    monkeypatch.setattr(BatesDialog, "exec", edit)
    dlg.edit_template(dlg.custom[0])
    assert len(dlg.custom) == 1 and dlg.custom[0]["settings"]["prefix"] == "FIX"


def test_update_many_opens_edit_window(qapp, pdoc, monkeypatch):
    from app.ui import dlg_deco as DD
    from app.ui import dlg_deco_template as T
    E.add_decoration(pdoc.fitz, "watermark", text="하나", pages=[0])
    E.add_decoration(pdoc.fitz, "watermark", text="둘", pages=[1, 2])
    seen = {}

    def run(self):
        seen["title"] = self.windowTitle()
        cards = self.scroll.widget().findChildren(T.Card)
        seen["cards"] = [c.name.text() for c in cards]
        assert self.ok.isEnabled()
        self.drop_group(0)
        self.accept(); return self.result()
    monkeypatch.setattr(T.TemplateDialog, "exec", run)
    monkeypatch.setattr(T, "ask", lambda _p, title, text: seen.setdefault("ask", (title, text)) and True)
    t = _Tab(pdoc)
    DD.run_decorate(None, t, "watermark", "update")
    assert seen["title"] == "워터마크 편집" and len(seen["cards"]) == 2   # 기본 제공 칸 없음
    assert seen["ask"] == ("워터마크 삭제", "워터마크을(를) 삭제하시겠습니까?")
    assert [(g["settings"]["text"], g["pages"]) for g in E.decorations(pdoc.fitz, "watermark")] == [("둘", [1, 2])]
    pdoc.undo()
    assert len(E.decorations(pdoc.fitz, "watermark")) == 2
