"""검색 공용: 주석 글 찾기 · 여러 파일 찾기(본문·주석·북마크·PDF 첨부) · 원본을 건드리지 않는 바꾸기(「_바꾸기.pdf」).

검색 패널·떠 있는 판·고급 검색이 같은 함수를 쓴다. Qt 를 모른다(고급 검색 일꾼 스레드에서도 부른다).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable

import fitz

from .document import Document, SearchHit, _unmangle, find_text_ctx, search_pattern

# 주석 글 문맥: 찾은 자리 앞 10자, 뒤 20자
ANNOT_BEFORE, ANNOT_AFTER = 10, 20
REPLACE_SUFFIX = "_바꾸기"

OK, ENCRYPTED, DAMAGED, MISSING = "ok", "encrypted", "damaged", "missing"


@dataclass
class AnnotHit(SearchHit):
    """주석 내용에서 찾은 것. rect 는 주석 자리, 누르면 그 주석을 고른다."""
    xref: int = 0


def _annot_rx(needle: str, case: bool) -> re.Pattern:
    # 주석 쪽은 단어 단위를 쓰지 않는다
    return search_pattern(needle, case=case)


def annot_hits_page(page: fitz.Page, pno: int, rx: re.Pattern) -> list[AnnotHit]:
    out = []
    for a in page.annots() or []:
        txt = (a.info or {}).get("content", "") or ""
        if not txt:
            continue
        flat = " ".join(txt.split())
        for m in rx.finditer(flat):
            if m.end() <= m.start():
                continue
            ctx = flat[max(0, m.start() - ANNOT_BEFORE):m.end() + ANNOT_AFTER]
            r = fitz.Rect(a.rect)
            out.append(AnnotHit(pno, r, m.group(0), [r], ctx, xref=a.xref))
    return out


def annot_search(doc: Document, needle: str, pages: Iterable[int] | None = None, case: bool = False) -> list[AnnotHit]:
    if not needle:
        return []
    rx = _annot_rx(needle, case)
    out: list[AnnotHit] = []
    for i in (list(pages) if pages is not None else range(doc.page_count)):
        with doc.lock:
            out += annot_hits_page(doc.fitz[i], i, rx)
    return out


def panel_search(doc: Document, needle: str, pages: Iterable[int] | None = None, case: bool = False, whole: bool = False,
                 comments: bool = False, progress: Callable | None = None) -> list[SearchHit]:
    """검색 패널·떠 있는 판 한 번 찾기: 본문(+ 주석 포함이면 그 쪽 주석). 쪽 차례, 같은 쪽은 본문 뒤에 주석."""
    pages = list(pages) if pages is not None else None
    res = doc.search(needle, pages=pages, case=case, whole_word=whole, progress=progress)
    if comments and needle:
        res = sorted(res + annot_search(doc, needle, pages, case), key=lambda h: h.page)   # sorted 는 같은 쪽 차례를 지킨다
    return res


# ── 여러 파일 찾기 ─────────────────────────────────────────────
@dataclass
class Found:
    kind: str                 # "text" · "annot" · "bookmark"
    page: int                 # 0부터, 북마크가 자리 없으면 -1
    rects: list               # 본문은 줄마다 네모, 주석은 주석 자리, 북마크는 빈 목록
    text: str                 # 찾은 글자
    context: str              # 결과 줄에 보일 앞뒤 글자
    xref: int = 0             # 주석
    index: int = -1           # 북마크: get_toc 차례
    attach: str = ""          # PDF 첨부 안에서 찾았으면 첨부 이름


@dataclass
class FileResult:
    path: str
    status: str = OK
    items: list[Found] = field(default_factory=list)


@dataclass
class Options:
    needle: str
    case: bool = False
    whole: bool = False
    comments: bool = False
    bookmarks: bool = False
    attach: bool = False


def pdf_files(folder: str) -> list[str]:
    """폴더와 하위 폴더의 *.pdf (이름 차례)."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(".pdf")]
    return out


def open_raw(path: str) -> tuple[fitz.Document | None, str]:
    """빈 암호로 연다. 암호가 필요하면 ENCRYPTED, 그 밖의 실패는 DAMAGED, 없으면 MISSING."""
    if not os.path.isfile(path):
        return None, MISSING
    try:
        with open(path, "rb") as fh:
            f = fitz.open("pdf", fh.read())
    except Exception:  # noqa: BLE001
        return None, DAMAGED
    if f.needs_pass and not f.authenticate(""):
        f.close()
        return None, ENCRYPTED
    if f.page_count == 0:
        f.close()
        return None, DAMAGED
    return f, OK


def search_fitz(f: fitz.Document, o: Options, lock=None, stop: Callable[[], bool] = lambda: False, attach: str = "") -> list[Found]:
    """fitz 문서 하나에서 찾는다(열린 문서면 그 문서의 lock 을 쪽마다 잡는다)."""
    lock = lock or threading.RLock()
    rx = search_pattern(o.needle, case=o.case, whole_word=o.whole)
    arx = _annot_rx(o.needle, o.case)
    out: list[Found] = []
    for i in range(f.page_count):
        if stop():
            return out
        with lock:
            page = f[i]
            for text, rects, ctx in find_text_ctx(page, rx):
                out.append(Found("text", i, rects, text, ctx, attach=attach))
            if o.comments:
                out += [Found("annot", h.page, h.rects, h.text, h.context, xref=h.xref, attach=attach)
                        for h in annot_hits_page(page, i, arx)]
    if o.bookmarks:
        with lock:
            toc = f.get_toc()
        for k, e in enumerate(toc):
            title = e[1] or ""
            if rx.search(" ".join(title.split())):
                m = rx.search(" ".join(title.split()))
                out.append(Found("bookmark", e[2] - 1, [], m.group(0), title, index=k, attach=attach))
    if o.attach and not attach:
        for name, data in pdf_attachments(f, lock):
            if stop():
                return out
            try:
                inner = fitz.open("pdf", data)
            except Exception:  # noqa: BLE001
                continue
            try:
                if inner.needs_pass and not inner.authenticate(""):
                    continue
                out += search_fitz(inner, o, stop=stop, attach=name)   # 첨부 안 첨부는 안 찾는다
            finally:
                inner.close()
    return out


def _attach_entries(f: fitz.Document, lock=None) -> list[tuple[int, str, bytes]]:
    """PDF 첨부만 (차례, 보일 이름, 바이트). 한글 이름은 PyMuPDF 가 깨뜨려 주므로 document 와 같이 되살린다."""
    lock = lock or threading.RLock()
    out = []
    with lock:
        for k in range(f.embfile_count()):
            try:
                info = f.embfile_info(k)
                name = _unmangle(info.get("ufilename") or info.get("filename") or info.get("name") or "")
                if not name.lower().endswith(".pdf"):
                    continue
                out.append((k, name, f.embfile_get(k)))
            except Exception:  # noqa: BLE001
                continue
    return out


def pdf_attachments(f: fitz.Document, lock=None) -> list[tuple[str, bytes]]:
    """PDF 첨부만 (이름, 바이트)."""
    return [(name, data) for _k, name, data in _attach_entries(f, lock)]


def search_file(path: str, o: Options, opened: Document | None = None, stop: Callable[[], bool] = lambda: False) -> FileResult:
    """파일 하나. 앱에 열려 있는 문서(opened)면 그 문서를 쓰고, 아니면 디스크에서 빈 암호로 연다."""
    if opened is not None:
        try:
            return FileResult(path, OK, search_fitz(opened.fitz, o, opened.lock, stop))
        except Exception:  # noqa: BLE001  찾는 사이 문서가 닫혔다
            return FileResult(path, DAMAGED)
    f, st = open_raw(path)
    if f is None:
        return FileResult(path, st)
    try:
        return FileResult(path, OK, search_fitz(f, o, stop=stop))
    except Exception:  # noqa: BLE001
        return FileResult(path, DAMAGED)
    finally:
        f.close()


# ── 바꾸기(새 파일로만) ─────────────────────────────────────────
def out_name(path: str, folder: str) -> str:
    """「원본이름_바꾸기.pdf」, 있으면 「원본이름_바꾸기_1.pdf」…"""
    stem = os.path.splitext(os.path.basename(path))[0] + REPLACE_SUFFIX
    p = os.path.join(folder, stem + ".pdf")
    n = 1
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem}_{n}.pdf"); n += 1
    return p


def _wrap(f: fitz.Document) -> Document:
    """디스크와 상관없는 fitz 문서에 Document 의 찾기·바꾸기를 쓴다."""
    d = Document()
    d.fitz.close()
    d.fitz = f
    return d


def replace_in_fitz(f: fitz.Document, needle: str, repl: str, case: bool, whole: bool, attach: bool) -> int:
    """본문 글자만 바꾼다(주석은 그대로). 첨부 포함이면 PDF 첨부 안도 바꿔 다시 넣는다. 바꾼 개수."""
    d = _wrap(f)
    n = d.replace_text(d.search(needle, case=case, whole_word=whole), repl)
    if attach:
        for k, _name, data in _attach_entries(f):
            try:
                inner = fitz.open("pdf", data)
            except Exception:  # noqa: BLE001
                continue
            if inner.needs_pass and not inner.authenticate(""):
                inner.close(); continue
            got = replace_in_fitz(inner, needle, repl, case, whole, attach=False)
            if got:
                # embfile_upd 에 바이트를 주면 PyMuPDF 안에서 터진다(1.26). 내용 스트림 번호만 받아 그 스트림을 바꾼다
                new = inner.tobytes(garbage=2, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                xref = f.embfile_upd(k)
                f.update_stream(xref, new)
                f.xref_set_key(xref, "DL", str(len(new))); f.xref_set_key(xref, "Params/Size", str(len(new)))
                n += got
            inner.close()
    return n


def can_modify(f: fitz.Document) -> bool:
    return bool(f.permissions & fitz.PDF_PERM_MODIFY)


def replace_file(path: str, needle: str, repl: str, folder: str, case: bool = False, whole: bool = False,
                 attach: bool = False) -> tuple[str | None, int, str]:
    """원본을 디스크에서 새로 열어 바꾸고 folder 에 새 파일로 저장한다(원본·열린 문서는 그대로).
    (저장한 경로 또는 None, 바꾼 개수, 상태). 0개면 저장하지 않는다. 상태 DAMAGED·ENCRYPTED 는 건너뜀."""
    f, st = open_raw(path)
    if f is None:
        return None, 0, st
    try:
        if not can_modify(f):
            return None, 0, DAMAGED
        n = replace_in_fitz(f, needle, repl, case, whole, attach)
        if not n:
            return None, 0, OK
        try:
            f.subset_fonts()   # 글자 하나만 넣어도 맑은 고딕 통째가 실리지 않게
        except Exception:  # noqa: BLE001
            pass
        os.makedirs(folder, exist_ok=True)
        out = out_name(path, folder)
        f.save(out, garbage=2, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)   # 원본 보안 설정 유지
        return out, n, OK
    except Exception:  # noqa: BLE001
        return None, 0, DAMAGED
    finally:
        f.close()
