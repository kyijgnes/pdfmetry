# -*- coding: utf-8 -*-
"""링크 엔진 시험(외관 키·쪽 이동 위치·상대 경로·고치기·지우기·되돌리기). 작은 PDF 를 그 자리에서 만들어 쓴다."""
from __future__ import annotations

import os
import sys

import fitz
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.engine import annots as A  # noqa: E402
from app.engine.document import Document  # noqa: E402

R = fitz.Rect(72, 72, 200, 100)


def _doc(n=3) -> Document:
    d = Document()
    for _ in range(n - 1):
        d.fitz.new_page()
    return d


def _reopen(d: Document) -> fitz.Document:
    return fitz.open("pdf", d.to_bytes())


def test_style_keys_written_and_survive_save(tmp_path):
    """보이는 직사각형·색·두께·대시·하이라이트가 /Border·/BS·/C·/H 로 들어가고 저장 뒤 다시 열어도 남는다."""
    d = _doc()
    st = {"visible": True, "color": (1, 0, 0), "width": 3, "line": "dash", "highlight": "push"}
    x = A.add_link(d, 0, R, uri="https://example.com", style=st)
    assert x
    f = _reopen(d)
    assert f.xref_get_key(x, "BS/W")[1] == "3"
    assert f.xref_get_key(x, "BS/S")[1] == "/D"
    assert f.xref_get_key(x, "H")[1] == "/P"
    assert f.xref_get_key(x, "C")[1].replace(" ", "") == "[100]"
    assert "[3 2]" in f.xref_get_key(x, "Border")[1]
    path = str(tmp_path / "링크.pdf")
    with open(path, "wb") as fh:
        fh.write(d.to_bytes())
    assert A.link_style(Document(path), x) == st


def test_invisible_rect_has_zero_width_and_keeps_highlight():
    d = _doc()
    x = A.add_link(d, 0, R, to_page=1, style={"visible": False, "highlight": "outline"})
    f = _reopen(d)
    assert f.xref_get_key(x, "BS/W")[1] == "0"
    assert f.xref_get_key(x, "Border")[1].replace(" ", "") == "[000]"
    assert f.xref_get_key(x, "H")[1] == "/O"
    assert A.link_style(d, x)["visible"] is False


def test_underline_and_default_style():
    d = _doc()
    x = A.add_link(d, 0, R, uri="https://a.b", style={"line": "underline"})
    st = A.link_style(d, x)
    assert st["line"] == "underline" and st["visible"] and st["color"] == (0.5, 0.5, 0.5) and st["width"] == 1
    assert st["highlight"] == "invert"


def test_goto_keeps_position_and_zoom():
    """쪽 이동 링크는 쪽 번호와 함께 왼쪽 위 자리·배율을 담는다."""
    d = _doc()
    x = A.add_link(d, 0, R, to_page=2, to_point=fitz.Point(30, 250), zoom=1.5)
    info = A.link_info(d, 0, x)
    assert info["kind"] == "goto" and info["to_page"] == 2
    assert abs(info["to_point"].x - 30) < 0.5 and abs(info["to_point"].y - 250) < 0.5
    assert info["zoom"] == pytest.approx(1.5)
    f = _reopen(d)
    assert "/XYZ" in f.xref_get_key(x, "A/D")[1]


def test_goto_out_of_range_raises():
    d = _doc()
    with pytest.raises(ValueError):
        A.add_link(d, 0, R, to_page=5)
    with pytest.raises(ValueError):
        A.add_link(d, 0, R)


def test_relative_file_path(tmp_path):
    doc_path = str(tmp_path / "a" / "문서.pdf")
    target = str(tmp_path / "a" / "sub" / "첨부.pdf")
    rel = A.link_file_path(target, doc_path, True)
    assert rel == "sub/첨부.pdf"
    assert A.link_file_path(target, doc_path, False) == target.replace("\\", "/")
    assert A.link_file_path(target, None, True) == target.replace("\\", "/")
    assert os.path.normcase(A.resolve_link_file(rel, doc_path)) == os.path.normcase(os.path.normpath(target))
    d = _doc()
    x = A.add_link(d, 0, R, file=rel)
    assert A.link_info(d, 0, x)["file"] == "sub/첨부.pdf"


def test_update_moves_and_retargets_with_one_undo():
    d = _doc()
    x = A.add_link(d, 0, R, uri="https://old.example", style={"color": (0, 0, 1), "width": 2})
    moved = fitz.Rect(100, 300, 220, 330)
    y = A.update_link(d, 0, x, rect=moved)
    info = A.link_info(d, 0, y)
    assert info["uri"] == "https://old.example" and info["style"]["color"] == (0, 0, 1) and info["style"]["width"] == 2
    assert abs(info["rect"].y0 - 300) < 0.5
    z = A.update_link(d, 0, y, target={"to_page": 1}, style={**info["style"], "highlight": "none"})
    info = A.link_info(d, 0, z)
    assert info["kind"] == "goto" and info["to_page"] == 1 and info["style"]["highlight"] == "none"
    assert len(A.links(d, 0)) == 1
    assert d.undo()   # 대상 바꾸기 되돌림
    assert [l["kind"] for l in A.links(d, 0)] == [fitz.LINK_URI]
    assert d.undo()   # 옮기기 되돌림
    assert abs(A.links(d, 0)[0]["from"].y0 - 72) < 0.5


def test_delete_by_xref_and_undo():
    d = _doc()
    x = A.add_link(d, 0, R, uri="https://a.b")
    A.add_link(d, 0, fitz.Rect(72, 200, 200, 230), to_page=1)
    A.delete_link(d, 0, x)
    ls = A.links(d, 0)
    assert len(ls) == 1 and ls[0]["kind"] == fitz.LINK_GOTO
    assert d.undo()
    assert len(A.links(d, 0)) == 2


def test_bad_link_kind():
    d = _doc()
    x = A.add_link(d, 0, R, to_page=2)
    with d.lock:
        d.fitz.xref_set_key(x, "A/D", "[999 0 R /XYZ 0 0 0]")
    info = A.link_info(d, 0, x)
    assert info is not None and info["kind"] == "bad"
