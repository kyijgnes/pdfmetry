"""레이블 모드 범위 파서(리본 범위 콤보가 씀): 로마자·접두사 레이블, 「-」 가 든 레이블, 숫자로 되읽기."""
import os
import tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="rpdf_labels_test_")   # 사용자 설정(최근 파일)을 건드리지 않게

import fitz  # noqa: E402
import pytest  # noqa: E402

from app.engine import pages as P  # noqa: E402


def labelled() -> list[str]:
    """i ii iii iv · A-1 … A-4 · 1 2 (10쪽)"""
    f = fitz.open()
    for _ in range(10):
        f.new_page()
    P.write_page_labels(f, [{"startpage": 0, "style": "r", "prefix": "", "firstpagenum": 1},
                            {"startpage": 4, "style": "D", "prefix": "A-", "firstpagenum": 1},
                            {"startpage": 8, "style": "D", "prefix": "", "firstpagenum": 1}])
    labs = P.page_label_strings(f)
    assert labs == ["i", "ii", "iii", "iv", "A-1", "A-2", "A-3", "A-4", "1", "2"]
    return labs


@pytest.mark.parametrize("spec,want", [
    ("iii", [2]),
    ("A-3", [6]),                 # 「-」 가 든 레이블은 통째로
    ("A-03", [6]),                # 끝 숫자 값이 같으면 같은 레이블
    ("ii-iv", [1, 2, 3]),
    ("A-1-A-3", [4, 5, 6]),       # 레이블 둘을 잇는 범위
    ("iv-A-2", [3, 4, 5]),
    ("iii-1", [2, 3, 4, 5, 6, 7, 8]),   # 범위 끝은 시작 쪽부터 처음 맞는 레이블(9쪽의 「1」)
    ("1,2", [8, 9]),              # 레이블이 먼저
    ("5,7", [4, 6]),              # 맞는 레이블이 없으면 쪽 번호
    ("iii, A-3 ,2", [2, 6, 9]),
])
def test_label_ok(spec, want):
    assert P.parse_range(spec, 10, strict=True, labels=labelled()) == want


@pytest.mark.parametrize("spec", ["v", "A-9", "A-3-ii", "B-1", "iii-", "11", ""])
def test_label_bad(spec):
    got = None
    try:
        got = P.parse_range(spec, 10, strict=True, labels=labelled())
    except ValueError:
        return
    assert got == []   # 빈 글은 0쪽(부르는 쪽이 오류 문구를 띄움)


def test_format_with_labels():
    labs = labelled()
    assert P.format_range([2, 4, 5, 6], labs) == "iii,A-1-A-3"
    # 레이블로 쓴 글을 다시 읽으면 같은 쪽
    for pages in ([0, 1, 2], [3, 4], [6, 8, 9], [0, 9]):
        assert P.parse_range(P.format_range(pages, labs), 10, strict=True, labels=labs) == pages


def test_numbers_without_labels_unchanged():
    assert P.parse_range("1-3,5", 10, strict=True) == [0, 1, 2, 4]
    with pytest.raises(ValueError):
        P.parse_range("1-2-3", 10, strict=True)
