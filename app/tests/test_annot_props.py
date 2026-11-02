"""주석 속성 패널 남은 칸(잠금·선 끝·구름·첨부 아이콘·기본값으로·정보 바로 적용)과 주석 목록(답글 줄·서로 따라 고르기·
그 자리 편집·Delete·필터).

python -m pytest app/tests/test_annot_props.py
"""
from __future__ import annotations

import os

import fitz
import pytest

from app.engine import annot_style as S
from app.engine import annots as A
from app.engine.document import Document

RED, BLUE = (1, 0, 0), (0, 0, 1)


def _reopen(d: Document, tmp_path) -> Document:
    p = str(tmp_path / "x.pdf")
    d.fitz.save(p)
    return Document(p)


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── 엔진 ─────────────────────────────────────────────────────────
def test_lock_blocks_move_change_delete_and_survives_save(tmp_path):
    d = Document()
    x = A.rect(d, 0, fitz.Rect(100, 100, 200, 200))
    assert A.update(d, 0, x, locked=True) == x and A.is_locked(d, 0, x)
    r0 = fitz.Rect(A.get(d, 0, x).rect)
    A.move_by(d, 0, x, 50, 50)
    A.update(d, 0, x, stroke=BLUE, content="안 바뀜")
    assert fitz.Rect(A.get(d, 0, x).rect) == r0 and A.get(d, 0, x).info["content"] == ""
    assert A.delete(d, 0, x) is False and A.get(d, 0, x) is not None
    A.delete_all(d)
    assert A.get(d, 0, x) is not None
    d2 = _reopen(d, tmp_path)
    assert A.is_locked(d2, 0, x)
    info = [a for a in A.list_annots(d2) if a.xref == x][0]
    assert info.locked
    A.update(d2, 0, x, locked=False)
    assert not A.is_locked(d2, 0, x) and A.delete(d2, 0, x)


def test_all_ten_line_ends_survive_save(tmp_path):
    d = Document()
    ends = list(S.LINE_ENDS.values())
    assert len(ends) == 10 and sorted(ends) == list(range(10))
    xs = []
    for k, e in enumerate(ends):
        x = A.line(d, 0, fitz.Point(50, 50 + k * 30), fitz.Point(300, 50 + k * 30))
        A.update(d, 0, x, start=e, end=ends[-1 - k])
        xs.append(x)
    pl = A.polygon(d, 0, [fitz.Point(350, 50), fitz.Point(400, 100), fitz.Point(450, 60)], closed=False,
                   ends=(fitz.PDF_ANNOT_LE_CIRCLE, fitz.PDF_ANNOT_LE_SLASH))
    co = A.free_text(d, 0, fitz.Rect(300, 500, 450, 560), "말풍선", 13, RED, None, RED, 1, callout=fitz.Point(260, 650))
    A.update(d, 0, co, start=fitz.PDF_ANNOT_LE_DIAMOND)
    d2 = _reopen(d, tmp_path)
    for k, x in enumerate(xs):
        assert tuple(A.get(d2, 0, x).line_ends) == (ends[k], ends[-1 - k])
    assert tuple(A.get(d2, 0, pl).line_ends) == (fitz.PDF_ANNOT_LE_CIRCLE, fitz.PDF_ANNOT_LE_SLASH)
    a = A.get(d2, 0, co)
    assert A.line_ends(d2, a)[0] == fitz.PDF_ANNOT_LE_DIAMOND and S.style_of(d2, a)["start"] == fitz.PDF_ANNOT_LE_DIAMOND
    # 옮겨도(새로 만들어도) 선 끝이 남는다
    nx = A.move_by(d2, 0, pl, 5, 5)
    assert tuple(A.get(d2, 0, nx).line_ends) == (fitz.PDF_ANNOT_LE_CIRCLE, fitz.PDF_ANNOT_LE_SLASH)


def test_cloud_border_attach_icon_and_marked_text():
    d = Document()
    d.fitz[0].insert_text((72, 100), "Hello world", fontsize=14)
    r = A.rect(d, 0, fitz.Rect(100, 200, 200, 300))
    A.update(d, 0, r, clouds=1)
    assert A.get(d, 0, r).border["clouds"] == 1 and S.style_of(d, A.get(d, 0, r))["clouds"] == 1
    A.update(d, 0, r, clouds=0, dashes=(3, 3))
    assert A.get(d, 0, r).border["clouds"] <= 0 and S.style_of(d, A.get(d, 0, r))["clouds"] == 0 and tuple(A.get(d, 0, r).border["dashes"]) == (3, 3)
    p = os.path.abspath(__file__)
    x = A.attachment(d, 0, fitz.Point(300, 300), p, icon="Tag", color=BLUE)
    a = A.get(d, 0, x)
    assert S.key_of(d, a) == "attach" and S.style_of(d, a)["icon"] == "Tag"
    A.update(d, 0, x, icon="Paperclip")
    assert d.fitz.xref_get_key(x, "Name")[1] == "/Paperclip"
    A.text_markup(d, 0, [fitz.Rect(72, 88, 160, 104)], "Highlight")
    hl = [a for a in A.list_annots(d) if a.kind == "Highlight"][0]
    assert "Hello" in hl.marked


def test_line_tool_default_ends_reach_new_line(tmp_path):
    st = S.StyleStore(str(tmp_path / "s.json"))
    assert st.get("arrow")["end"] == fitz.PDF_ANNOT_LE_OPEN_ARROW and st.get("line")["end"] == fitz.PDF_ANNOT_LE_NONE
    st.remember("line", {"start": fitz.PDF_ANNOT_LE_SQUARE, "end": fitz.PDF_ANNOT_LE_CLOSED_ARROW})
    props: dict = {}
    S.to_viewer_props("line", st.get("line"), props)
    d = Document()
    x = A.line(d, 0, fitz.Point(10, 10), fitz.Point(100, 10), ends=props["line_ends"])
    assert tuple(A.get(d, 0, x).line_ends) == (fitz.PDF_ANNOT_LE_SQUARE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)


# ── 속성 패널 ───────────────────────────────────────────────────
def _panel(tmp_path):
    from app.ui.props import PropsPanel
    from app.ui.viewer import PageView
    d = Document()
    v = PageView(d)
    st = S.StyleStore(str(tmp_path / "s.json"))
    p = PropsPanel(v.props, v, st)
    applied = []
    p.applyToSel.connect(applied.append)
    return d, v, st, p, applied


def _sel(d, x):
    from app.ui.viewer import Selection
    a = A.get(d, 0, x)
    return Selection("annot", 0, x, fitz.Rect(a.rect), extra={"kind_ko": A.KIND_NAMES[a.type[1]]})


def test_shape_tools_show_polygon_defaults(qapp, tmp_path):
    d, v, st, p, _ = _panel(tmp_path)
    try:
        for t in ("triangle", "cross", "bent_arrow", "balloon"):
            v.set_tool(t)
            assert p.key == "polygon" and "고른 것 없음" not in p.kind.text() and p.look_box.isVisibleTo(p)
            assert p.look.isRowVisible(p.width) and p.look.isRowVisible(p.fill)
        p.width.setValue(3.3)
        assert st.get("polygon")["width"] == 3.3 and v.props["width"] == 3.3
    finally:
        v.close()


def test_props_new_fields_lock_default_and_info(qapp, tmp_path):
    d, v, st, p, applied = _panel(tmp_path)
    try:
        assert p.opacity.minimum() == 0 and p.width.singleStep() == 0.1
        v.set_tool("arrow")
        assert p.look.isRowVisible(p.start) and p.look.isRowVisible(p.end)
        assert p.end.currentText() == "열린 화살촉"
        x = A.line(d, 0, fitz.Point(50, 50), fitz.Point(200, 50))
        v.set_tool("select")
        p.show_selection(_sel(d, x))
        assert p.lock.isVisibleTo(p) and not p.lock.isChecked()
        p.end.setCurrentText("마름모")
        p._timer.timeout.emit()
        assert applied[-1]["end"] == fitz.PDF_ANNOT_LE_DIAMOND
        # 자동 저장을 끄면 고른 주석을 고쳐도 기본값은 그대로, 「기본값으로」를 눌러야 기억
        p.autosave = lambda: False
        w0 = st.get("line")["width"]
        p.width.setValue(5)
        p._timer.timeout.emit()
        assert st.get("line")["width"] == w0 != 5
        p.default_btn.click()
        assert st.get("line")["width"] == 5 and st.get("line")["end"] == fitz.PDF_ANNOT_LE_DIAMOND
        # 정보 칸: 떠날 때 바뀐 칸만 적용
        p.author.setText("윤"); p.author.editingFinished.emit()
        assert applied[-1] == {"author": "윤"}
        # 잠금
        p.lock.click()
        assert applied[-1] == {"locked": True} and not p.look_box.isEnabled() and p.lock.text() == "잠김"
        n = len(applied)
        p._timer.timeout.emit(); p.subject.setText("x"); p.subject.editingFinished.emit()
        assert len(applied) == n
        # 사각형은 구름 둘이 선 모양 목록에
        r = A.rect(d, 0, fitz.Rect(100, 300, 200, 400))
        A.update(d, 0, r, clouds=2)
        p.show_selection(_sel(d, r))
        assert p.dashes.currentText() == "구름 2" and p.look.isRowVisible(p.dashes)
        assert not p.look.isRowVisible(p.start)
        p.dashes.setCurrentText("점선 3·3")
        p._timer.timeout.emit()
        assert applied[-1]["clouds"] == 0 and applied[-1]["dashes"] == (3, 3)
    finally:
        v.close()


# ── 주석 목록 ───────────────────────────────────────────────────
def test_annot_list_replies_edit_delete_filter(qapp):
    from app.ui.sidebar import AnnotPanel
    d = Document()
    n = A.note(d, 0, fitz.Point(20, 20), "원 글", author="나")
    r = A.add_reply(d, 0, n, "답글 하나", author="너")
    A.rect(d, 0, fitz.Rect(100, 100, 150, 150), author="너")
    lk = A.rect(d, 0, fitz.Rect(200, 100, 250, 150), author="나")
    A.update(d, 0, lk, locked=True)
    p = AnnotPanel(d)
    picked = []
    p.pickAnnot.connect(lambda pg, x: picked.append(x))
    page = p.tree.topLevelItem(0)
    assert page.childCount() == 3                       # 답글은 위 줄로 따로 안 나온다
    it = p._item_of(n)
    assert it.childCount() == 1 and it.child(0).text(0) == "답글" and it.child(0).text(1) == "답글 하나"
    assert p.tree.columnCount() == 4 and p._item_of(lk).icon(0).isNull() is False
    # 목록에서 고르면 뷰어로, 답글을 고르면 원 주석
    it.child(0).setSelected(True)
    assert picked[-1] == n
    # 뷰어에서 고른 것을 목록이 따라 고른다(다시 뷰어를 부르지 않음)
    from app.ui.viewer import Selection
    k = len(picked)
    p.follow(Selection("annot", 0, lk, fitz.Rect()))
    assert [i.data(0, 256)[1] for i in p.tree.selectedItems()] == [lk] and len(picked) == k
    # 그 자리 편집: 내용 칸 확정
    p._commit_edit(p.tree.indexFromItem(p._item_of(n), 1), "고친 글")
    qapp.processEvents()
    assert A.get(d, 0, n).info["content"] == "고친 글"
    p._commit_edit(p.tree.indexFromItem(p._item_of(r), 1), "고친 답글")
    qapp.processEvents()
    assert A.replies(d, 0, n)[0]["content"] == "고친 답글"
    # 답글 달기: 빈 줄을 열고 확정하면 답글
    p.add_reply(p._item_of(n))
    new = [i for i in p._items() if i.data(0, 256) and i.data(0, 256)[1] == 0][0]
    p._commit_edit(p.tree.indexFromItem(new, 1), "둘째 답글")
    qapp.processEvents()
    assert [x["content"] for x in A.replies(d, 0, n)] == ["고친 답글", "둘째 답글"]
    # 여러 개 고르고 Delete: 잠긴 것은 남는다
    p.tree.selectAll()
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    p.eventFilter(p.tree, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert [a.xref for a in A.list_annots(d)] == [lk]
    # 필터: 작성자로 거르면 버튼이 눌린 모양
    A.rect(d, 0, fitz.Rect(10, 300, 50, 350), author="너")
    p.reload()
    p.flt = {"authors": {"너"}}
    p.reload()
    assert p.filter_btn.isChecked() and p.tree.topLevelItem(0).childCount() == 1
    p.flt = {"authors": {"없음"}}
    p.reload()
    assert p.empty.text() == "찾은 주석 없음"
    p.flt = None; p.reload()
    p.collapse_all(); p.reload()
    assert not p.tree.topLevelItem(0).isExpanded()
    p.expand_all()
    assert p.tree.topLevelItem(0).isExpanded()
