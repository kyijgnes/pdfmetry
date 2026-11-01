# -*- coding: utf-8 -*-
"""PDF 분할(쪽 수·크기·책갈피)과 페이지 레이블 섹션 시험. 작은 PDF 를 그 자리에서 만든다."""
from __future__ import annotations

import os
import sys

import fitz
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.engine import pages as P  # noqa: E402
from app.engine.document import Document  # noqa: E402


def make(n: int = 10, toc=None, noise: bool = False) -> Document:
    f = fitz.open()
    for i in range(n):
        page = f.new_page(width=595, height=842)
        page.insert_text((72, 100), f"page {i + 1}", fontsize=20)
        if noise:   # 쪽마다 압축이 안 되는 이미지를 넣어 크기를 키운다
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 160, 160), False)
            pix.set_rect(pix.irect, (0, 0, 0))
            import random
            rnd = random.Random(i)
            pix.samples_mv[:] = bytes(rnd.getrandbits(8) for _ in range(len(pix.samples_mv)))
            page.insert_image(fitz.Rect(72, 150, 300, 380), pixmap=pix)
    if toc:
        f.set_toc(toc)
    f.set_metadata({"title": "원본 제목", "author": "작성자"})
    return Document.from_bytes(f.tobytes(), "보고서.pdf")


def names(outs):
    return [os.path.basename(o) for o in outs]


def test_split_by_pages(tmp_path):
    d = make(10)
    outs = P.split(d, str(tmp_path), "count", 4)
    assert names(outs) == ["보고서_파트1.pdf", "보고서_파트2.pdf", "보고서_파트3.pdf"]
    assert all(os.path.dirname(o) == str(tmp_path / "보고서") for o in outs)
    counts = [fitz.open(o).page_count for o in outs]
    assert counts == [4, 4, 2]
    assert fitz.open(outs[0]).metadata["title"] == "원본 제목"


def test_split_pages_not_more_than_total_copies_whole(tmp_path):
    outs = P.split(make(3), str(tmp_path), "count", 5)
    assert names(outs) == ["보고서.pdf"] and fitz.open(outs[0]).page_count == 3


def test_split_by_size(tmp_path):
    d = make(12, noise=True)
    whole = len(d.to_bytes())
    mb = whole / 3.5 / 1024 / 1024
    outs = P.split(d, str(tmp_path), "size", mb)
    assert len(outs) >= 4
    assert names(outs)[0] == "보고서_파트1.pdf"
    assert sum(fitz.open(o).page_count for o in outs) == 12
    limit = mb * 1024 * 1024
    assert all(os.path.getsize(o) <= limit for o in outs)


def test_split_by_size_single_page_over_limit(tmp_path):
    d = make(3, noise=True)
    outs = P.split(d, str(tmp_path), "size", 0.01)
    assert [fitz.open(o).page_count for o in outs] == [1, 1, 1]
    assert all(os.path.getsize(o) > 0.01 * 1024 * 1024 for o in outs)   # 창이 「압축」 안내를 띄우는 경우


def test_split_by_bookmarks(tmp_path):
    d = make(10, toc=[[1, "들어가며", 3], [2, "안쪽", 4], [1, "본론: 1/2", 5], [1, "맺음", 9]])
    outs = P.split(d, str(tmp_path), "bookmarks")
    assert names(outs) == ["보고서_머리글.pdf", "보고서_들어가며.pdf", "보고서_본론_ 1_2.pdf", "보고서_맺음.pdf"]
    assert [fitz.open(o).page_count for o in outs] == [2, 2, 4, 2]


def test_split_by_bookmarks_needs_top_level(tmp_path):
    with pytest.raises(ValueError, match="최상위 북마크"):
        P.split(make(4), str(tmp_path), "bookmarks")


def test_split_long_path_is_cut(tmp_path):
    d = make(2)
    outs = P.split(d, str(tmp_path), "count", 1, stem="가" * 300)
    assert all(len(o) <= 255 for o in outs) and len(outs) == 2


def test_label_section_resumes_original_numbering():
    d = make(10)
    P.apply_labels(d, 2, 4, "r", "", 1)   # 3~5쪽 i,ii,iii
    assert P.labels(d) == ["1", "2", "i", "ii", "iii", "6", "7", "8", "9", "10"]
    d.undo()
    assert P.labels(d) == [str(i) for i in range(1, 11)]


def test_label_inner_sections_removed_and_merged():
    d = make(10)
    P.apply_labels(d, 2, 4, "r", "", 1)
    P.apply_labels(d, 0, 9, "D", "", 1)
    assert P.read_page_labels(d.fitz) == [{"startpage": 0, "prefix": "", "firstpagenum": 1, "style": "D"}]


def test_label_extend_previous_section():
    d = make(10)
    P.apply_labels(d, 2, 4, "A", "부록-", 1)
    P.apply_labels(d, 5, 6, extend=True)
    assert P.labels(d)[2:8] == ["부록-A", "부록-B", "부록-C", "부록-D", "부록-E", "8"]


def test_label_start_letters_and_preview():
    assert P.label_number("iv", "r") == 4 and P.label_number("IV", "R") == 4
    assert P.label_number("aa", "a") == 27 and P.label_number("ivx", "r") is None
    assert P.label_number("7", "A") == 7
    assert P.label_preview("D", "A-", 1) == "A-1,A-2,A-3,..."


def test_page_of_label():
    d = make(10)
    P.apply_labels(d, 0, 1, "r", "", 1)
    assert P.page_of_label(d.fitz, "ii") == 1
    assert P.page_of_label(d.fitz, "5") == 4
    assert P.page_of_label(d.fitz, "99") is None
