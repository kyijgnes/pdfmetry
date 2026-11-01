"""페이지 구성: 회전·삭제·순서·끼우기·뽑기·나누기·합치기·바꾸기.

모든 함수는 Document 를 받고 바꾸기 전에 snapshot() 을 부른다.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

import fitz

from .document import Document, del_keys, keep_copy


RANGE_ERROR = "유효한 페이지 범위를 입력하십시오."


def parse_range(spec: str, total: int, strict: bool = False, labels: list[str] | None = None) -> list[int]:
    """'1-3,5,8-' → 0-based 목록. 빈 문자열이면 전부.

    strict: 쪽 범위 칸(PageRangeEdit)의 엄격한 방식. 쉼표로만 나누고, 겹친 것을 합쳐 작은 쪽부터 정렬하며,
    빈 글은 0쪽, 열린 범위(「8-」)·빈 조각·거꾸로·없는 쪽이 한 조각이라도 있으면 전체를 버린다(ValueError).
    labels: 쪽마다의 레이블(엄격한 방식에서만). 주면 조각을 레이블로 먼저 찾고, 없으면 쪽 번호로 읽는다."""
    if strict:
        return _parse_strict(spec, total, labels)
    # 「1 - 3」 을 띄어 쓰면 홀로 남은 「-」 가 「처음~끝」 으로 읽혀 모든 쪽이 골라졌다
    spec = re.sub(r"\s*-\s*", "-", (spec or "").strip())
    if not spec:
        return list(range(total))
    out: list[int] = []
    for part in re.split(r"[,\s]+", spec):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else total
            if a > b:
                raise ValueError(f"쪽 범위가 거꾸로입니다: {part}")
            if a < 1 or a > total:
                raise ValueError(f"없는 쪽입니다: {part} (1~{total}쪽)")
            out.extend(range(a - 1, min(b, total)))
        elif part.isdigit():
            n = int(part) - 1
            if not 0 <= n < total:
                raise ValueError(f"없는 쪽입니다: {part} (1~{total}쪽)")
            out.append(n)
        else:
            raise ValueError(f"쪽 범위를 읽을 수 없습니다: {part}")
    seen, res = set(), []
    for n in out:
        if n not in seen and 0 <= n < total:
            seen.add(n)
            res.append(n)
    return res


def _parse_strict(spec: str, total: int, labels: list[str] | None) -> list[int]:
    spec = (spec or "").strip()
    if not spec:
        return []

    def page_of(tok: str, start: int = 0) -> int:
        tok = tok.strip()
        if not tok:
            raise ValueError(RANGE_ERROR)
        i = _find_label(tok, labels, start, total) if labels else None
        if i is not None:
            return i
        if not tok.isdigit() or not 1 <= int(tok) <= total:
            raise ValueError(RANGE_ERROR)
        return int(tok) - 1

    def piece(part: str) -> range:
        part = part.strip()
        if labels:   # 「A-3」 처럼 「-」 가 든 레이블은 통째로 먼저 찾는다
            i = _find_label(part, labels, 0, total)
            if i is not None:
                return range(i, i + 1)
        cuts = [k for k, c in enumerate(part) if c == "-"]
        if not cuts:
            i = page_of(part)
            return range(i, i + 1)
        if not labels and len(cuts) > 1:
            raise ValueError(RANGE_ERROR)
        for k in cuts:   # 「A-1-A-3」: 양쪽이 다 쪽으로 읽히는 자리에서 나눈다
            try:
                a = page_of(part[:k])
                b = page_of(part[k + 1:], a)   # 범위 끝은 시작 쪽부터 처음 맞는 레이블
            except ValueError:
                continue
            if a <= b:
                return range(a, b + 1)
        raise ValueError(RANGE_ERROR)

    out: set[int] = set()
    for part in spec.split(","):
        out.update(piece(part))
    return sorted(out)


def _find_label(tok: str, labels: list[str], start: int, total: int) -> int | None:
    """레이블 글자로 쪽을 찾는다(start 쪽부터). 똑같은 레이블이 없으면 접두사가 같고 끝 숫자 값이 같은 것(「A-03」 → 「A-3」)."""
    n = min(total, len(labels))
    for i in range(start, n):
        if labels[i] == tok:
            return i
    m = re.fullmatch(r"(.*?)(\d+)", tok)
    if m:
        for i in range(start, n):
            lm = re.fullmatch(r"(.*?)(\d+)", labels[i] or "")
            if lm and lm.group(1) == m.group(1) and int(lm.group(2)) == int(m.group(2)):
                return i
    return None


def subset(pages: Iterable[int], how: str = "all") -> list[int]:
    """홀수(odd)·짝수(even) 쪽만 거른다. 쪽 번호는 1부터 센 것 기준(0-based 0 = 1쪽 = 홀수). all 이면 그대로."""
    if how == "odd":
        return [p for p in pages if p % 2 == 0]
    if how == "even":
        return [p for p in pages if p % 2 == 1]
    return list(pages)


def format_range(pages: Iterable[int], labels: list[str] | None = None) -> str:
    """0-based 쪽 목록 → 「1,3-5」. 이어진 번호는 묶는다. labels 를 주면 쪽 번호 대신 레이블로 쓴다."""
    nums = sorted({int(p) for p in pages})

    def name(i: int) -> str:
        return labels[i] if labels and i < len(labels) and labels[i] else str(i + 1)

    out, k = [], 0
    while k < len(nums):
        j = k
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(name(nums[k]) if j == k else f"{name(nums[k])}-{name(nums[j])}")
        k = j + 1
    return ",".join(out)


def rotate(doc: Document, pages: Iterable[int], degrees: int):
    doc.snapshot()
    with doc.lock:
        for i in pages:
            p = doc.fitz[i]
            p.set_rotation((p.rotation + degrees) % 360)


def delete(doc: Document, pages: Iterable[int]):
    pages = sorted(set(pages))
    if not pages:
        return
    if len(pages) >= doc.page_count:
        raise ValueError("쪽을 전부 지울 수는 없습니다")
    if pages[0] < 0 or pages[-1] >= doc.page_count:
        raise ValueError("없는 쪽은 지울 수 없습니다")
    doc.snapshot()
    with doc.lock:
        doc.fitz.delete_pages(pages)


def split_grid(doc: Document, pages: Iterable[int], cols: int = 2, rows: int = 1, *,
               xs: Iterable[float] | None = None, ys: Iterable[float] | None = None, progress=None) -> list[int]:
    """쪽 하나를 분할선으로 나눠 여러 쪽으로(「페이지 분할」, 스캔한 두 쪽 펼침 나누기).

    xs 는 세로 분할선, ys 는 가로 분할선 자리이고 보이는 쪽 너비·높이에 대한 비율(0~1 사이)이다. 안 주면
    cols × rows 칸으로 고르게 나눈다. 칸마다 쪽을 통째로 복사하고 보이는 영역(CropBox)만 그 칸으로 줄인다.
    글·주석·링크가 그대로 남는다. 칸 차례는 보이는 화면 기준 왼쪽 위 → 오른쪽 → 다음 줄.
    돌려주는 값은 새 쪽마다 원래 쪽 번호(되돌리기·썸네일용)."""
    if xs is None and ys is None:
        cols, rows = int(cols), int(rows)
        if not (1 <= cols <= 10 and 1 <= rows <= 10) or cols * rows == 1:
            raise ValueError("가로·세로 칸 수는 1~10 이고, 둘 다 1 이면 나눌 게 없습니다")
        xs = [c / cols for c in range(1, cols)]
        ys = [r / rows for r in range(1, rows)]
    xs = sorted({float(x) for x in xs or ()})
    ys = sorted({float(y) for y in ys or ()})
    if not (xs or ys) or len(xs) > 10 or len(ys) > 10 or any(not 0 < t < 1 for t in xs + ys):
        raise ValueError("분할선은 방향마다 1~10개, 쪽 안쪽(0~1 사이)에 있어야 합니다")
    xe, ye = [0.0] + xs + [1.0], [0.0] + ys + [1.0]
    targets = sorted({p for p in pages if 0 <= p < doc.page_count})
    doc.snapshot()
    mapping = []
    with doc.lock:
        f = doc.fitz
        for src in range(f.page_count):
            mapping.append(src)
        for n, pno in enumerate(reversed(targets)):   # 뒤에서부터 끼워 앞 번호가 밀리지 않게
            if progress:
                progress(n, len(targets), "분할 중")
            page = f[pno]
            vis = page.rect
            cb = page.cropbox
            w, h = vis.width, vis.height
            cells = [fitz.Rect(vis.x0 + xe[c] * w, vis.y0 + ye[r] * h, vis.x0 + xe[c + 1] * w, vis.y0 + ye[r + 1] * h)
                     for r in range(len(ye) - 1) for c in range(len(xe) - 1)]
            dm = page.derotation_matrix
            boxes = []
            for cell in cells:   # 보이는 좌표 → 회전 전 좌표(CropBox 원점 기준) → MediaBox 기준
                u = cell * dm
                u.normalize()
                boxes.append(fitz.Rect(u.x0 + cb.x0, u.y0 + cb.y0, u.x1 + cb.x0, u.y1 + cb.y0))
            for _ in boxes[1:]:
                f.fullcopy_page(pno, pno + 1 if pno + 1 < f.page_count else -1)   # -1 = 맨 뒤
            for k, box in enumerate(boxes):
                f[pno + k].set_cropbox(box)
            mapping[pno:pno + 1] = [pno] * len(boxes)
    return mapping


def move(doc: Document, src: list[int], dest: int):
    """src 쪽들을 dest 자리(0-based, 끼울 자리 앞 인덱스) 로 옮긴다."""
    order = list(range(doc.page_count))
    picked = [i for i in order if i in set(src)]
    rest = [i for i in order if i not in set(src)]
    before = sum(1 for i in src if i < dest)
    dest_in_rest = max(0, min(len(rest), dest - before))
    new_order = rest[:dest_in_rest] + picked + rest[dest_in_rest:]
    if new_order == order:
        return
    doc.snapshot()
    with doc.lock:
        doc.fitz.select(new_order)


def reorder(doc: Document, new_order: list[int]):
    doc.snapshot()
    with doc.lock:
        doc.fitz.select(new_order)


def insert_index(place: str, page: int, before: bool, total: int) -> int:
    """삽입 장소 → 끼울 자리(0-based). place: first(첫 쪽) · last(마지막 쪽) · page(page 쪽).
    「뒤에」면 그 쪽 다음 자리라서 「첫 쪽 뒤」는 2쪽 자리다."""
    if total <= 0:
        return 0
    target = 0 if place == "first" else total - 1 if place == "last" else max(0, min(int(page), total - 1))
    return target if before else target + 1


_BOXES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")


def _inherited(f: fitz.Document, xref: int, key: str) -> tuple[str, str]:
    """쪽 열쇠 값(물려받은 것까지). 없으면 ("null", "null")."""
    for _ in range(64):
        t, v = f.xref_get_key(xref, key)
        if t != "null":
            return t, v
        t, v = f.xref_get_key(xref, "Parent")
        if t != "xref":
            break
        xref = int(v.split()[0])
    return "null", "null"


def insert_blank(doc: Document, at: int, width: float | None = None, height: float | None = None, count: int = 1):
    """빈 쪽 count 장을 at 자리에. 크기를 안 주면 끼울 자리 바로 앞 쪽(맨 앞이면 1쪽)의 상자 다섯과 회전을 따른다."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        ref = min(max(at - 1, 0), f.page_count - 1) if f.page_count else -1
        follow = (width is None or height is None) and ref >= 0
        if not follow and (width is None or height is None):
            width, height = 595, 842
        for k in range(count):
            if not follow:
                f.new_page(pno=at + k, width=width, height=height)
                continue
            rx = f[ref].xref
            mb = _box(f, rx, "MediaBox") or [0, 0, 595, 842]
            f.new_page(pno=at + k, width=mb[2] - mb[0], height=mb[3] - mb[1])
            nx = f[at + k].xref
            for key in _BOXES:
                t, v = _inherited(f, rx, key)
                if t == "array":
                    f.xref_set_key(nx, key, v)
            t, v = _inherited(f, rx, "Rotate")
            if t == "int" and int(v) % 360:
                f.xref_set_key(nx, "Rotate", str(int(v) % 360))


def is_xfa(src: fitz.Document) -> bool:
    """XFA 양식 문서인가(쪽 끼우기가 그 양식을 옮기지 못한다)."""
    try:
        return src.is_pdf and src.xref_get_key(src.pdf_catalog(), "AcroForm/XFA")[0] != "null"
    except Exception:  # noqa: BLE001
        return False


def open_source(path: str, password: str | None = None) -> fitz.Document:
    """끼우거나 바꿀 때 쓸 원본을 연다. 암호가 맞지 않으면 ValueError."""
    src = fitz.open(path)
    if src.needs_pass and (not password or not src.authenticate(password)):
        src.close()
        raise ValueError(f"「{os.path.basename(path)}」 의 암호가 맞지 않습니다")
    if not src.is_pdf:
        src = fitz.open("pdf", src.convert_to_pdf())
    return src


def _ocg_name(f: fitz.Document, xref: int) -> str:
    return _pdf_text(f.xref_get_key(xref, "Name")[1])


def _pdf_hex(s: str) -> str:
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def _append(f: fitz.Document, xref: int, key: str, items: str):
    cur = f.xref_get_key(xref, key)
    body = cur[1][1:-1] if cur[0] == "array" else ""
    f.xref_set_key(xref, key, f"[{body} {items}]")


def _own_dict(f: fitz.Document, owner: int, key: str) -> int:
    """owner 의 key 사전을 간접 객체로 만들어 xref 를 돌려준다(없으면 빈 사전)."""
    cur = f.xref_get_key(owner, key)
    if cur[0] == "xref":
        return int(cur[1].split()[0])
    x = f.get_new_xref()
    f.update_object(x, cur[1] if cur[0] == "dict" else "<<>>")
    f.xref_set_key(owner, key, f"{x} 0 R")
    return x


def _adopt_layers(f: fitz.Document, first_new: int, src: fitz.Document, label: str):
    """insert_pdf 는 쪽 자원과 함께 레이어(OCG)를 복사만 하고 문서 레이어 목록에는 올리지 않는다.
    새로 생긴 OCG 를 목록에 올리고 원본 파일 이름 묶음 아래에 둔다. 켜짐·꺼짐은 원본의 같은 이름 레이어를 따른다."""
    new = [x for x in range(first_new, f.xref_length()) if f.xref_get_key(x, "Type")[1] == "/OCG"]
    if not new:
        return
    state = {v["name"]: v["on"] for v in src.get_ocgs().values()}
    refs = " ".join(f"{x} 0 R" for x in new)
    off = " ".join(f"{x} 0 R" for x in new if state.get(_ocg_name(f, x), True) is False)
    cat = f.pdf_catalog()
    props = _own_dict(f, cat, "OCProperties")
    _append(f, props, "OCGs", refs)
    d = _own_dict(f, props, "D")
    _append(f, d, "Order", f"[{_pdf_hex(label)} [{refs}]]")
    if off:
        _append(f, d, "OFF", off)


def insert_pdfs(doc: Document, at: int, items: list[tuple]) -> int:
    """여러 문서를 고른 차례대로 at 자리부터 이어 끼운다. items: (경로, 암호, 가져올 쪽 또는 None).
    되돌리기 한 걸음으로 묶는다. 끼운 쪽 수를 돌려준다."""
    srcs = [(open_source(p, pw), pg) for p, pw, pg in items]   # 다 열어 본 뒤에 바꾼다(실패해도 되돌리기 걸음이 남지 않게)
    for src, pg in srcs:
        if pg is not None and any(not 0 <= i < src.page_count for i in pg):
            for s, _ in srcs:
                s.close()
            raise ValueError(f"가져올 쪽이 원본에 없습니다(1~{src.page_count}쪽)")
    doc.snapshot()
    doc.dedupe = True  # 끼운 문서와 같은 글꼴·그림이 겹칠 수 있어 저장 때 합친다
    n = 0
    with doc.lock:
        for (src, pg), (path, _, _) in zip(srcs, items):
            first_new = doc.fitz.xref_length()
            if pg is None:
                doc.fitz.insert_pdf(src, start_at=at + n)
                n += src.page_count
            else:
                for i in pg:
                    doc.fitz.insert_pdf(src, from_page=i, to_page=i, start_at=at + n)
                    n += 1
            _adopt_layers(doc.fitz, first_new, src, os.path.basename(path))
            src.close()
    return n


def insert_from_pdf(doc: Document, at: int, path: str, pages: Iterable[int] | None = None, password: str | None = None):
    insert_pdfs(doc, at, [(path, password, None if pages is None else list(pages))])


def insert_images(doc: Document, at: int, paths: list[str]):
    doc.snapshot()
    doc.dedupe = True
    with doc.lock:
        for k, p in enumerate(paths):
            img = fitz.open(p)
            pdf = fitz.open("pdf", img.convert_to_pdf())
            doc.fitz.insert_pdf(pdf, start_at=at + k)
            img.close(); pdf.close()


EXTRACT_PREFIX = "페이지"


def extract_name(folder: str, src_name: str) -> str:
    """추출한 문서의 경로: 「페이지 <원본 이름>.pdf」. 원본 이름은 150자에서 자르고, 같은 이름이 있으면 「-1」 「-2」… 를 붙인다."""
    stem = os.path.splitext(src_name or "문서")[0][:150]
    base = f"{EXTRACT_PREFIX} {stem}"
    path, k = os.path.join(folder, base + ".pdf"), 0
    while os.path.exists(path):
        k += 1
        path = os.path.join(folder, f"{base}-{k}.pdf")
    return path


def extract(doc: Document, pages: Iterable[int], out_path: str, creator: str = "", progress=None) -> str:
    """고른 쪽만 새 PDF 로 저장. 원본의 레이어·문서 정보·암호(보안 설정)는 그대로 두고,
    작성자는 윈도 사용자, 만든·고친 날짜는 지금, 만든 프로그램은 creator 로 채운다."""
    pages = sorted(set(pages))
    if not pages:
        raise ValueError(RANGE_ERROR)
    if progress:
        progress(0, 0, "페이지 내보내기 중…")
    with doc.lock:
        new = keep_copy(doc.fitz, doc.password)   # 통째 사본에서 쪽을 고르면 레이어·문서 정보가 따라온다
    try:
        new.select(pages)
        now = fitz.get_pdf_now()
        meta = dict(new.metadata or {})
        meta.update(author=os.environ.get("USERNAME") or meta.get("author") or "", creationDate=now, modDate=now)
        if creator:
            meta["creator"] = creator
        new.set_metadata({k: v for k, v in meta.items() if k not in ("format", "encryption")})
        new.save(out_path, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    finally:
        new.close()
    return out_path


SPLIT_PART = "파트"       # <이름>_파트N.pdf
SPLIT_HEAD = "머리글"     # 첫 책갈피 앞쪽들
NO_TOP_BOOKMARK = "문서에 최상위 북마크 있지 않으므로 분할할 수 없습니다. 옵션을 변경하고 다시 시도하십시오."
_BAD_NAME = re.compile(r'[\/:*?"<>|\x00-\x1f]')


def split_folder(out_dir: str, stem: str) -> str:
    """분할 결과는 저장 폴더 아래 원본 이름 하위 폴더에 모인다. 파일 이름 자리(40자)를 남기게 폴더 이름을 자른다."""
    name = _BAD_NAME.sub("_", stem).strip() or "문서"
    room = 255 - 40 - len(os.path.join(out_dir, ""))
    return os.path.join(out_dir, name[:max(1, room)].rstrip(" .") or "문서")


def _fit_name(folder: str, name: str, ext: str = ".pdf", limit: int = 255) -> str:
    """전체 경로가 limit 자를 넘으면 이름 뒤를 잘라 맞춘다."""
    room = limit - len(os.path.join(folder, "")) - len(ext)
    return os.path.join(folder, (name[:max(1, room)] if len(name) > room else name) + ext)


def _bookmark_groups(doc: Document, total: int, stem: str) -> list[tuple[str, list[int]]]:
    tops = sorted((max(0, min(total - 1, t[2] - 1)), t[1]) for t in doc.flat_toc() if t[0] == 1 and t[2] > 0)
    if not tops:
        raise ValueError(NO_TOP_BOOKMARK)
    out = []
    if tops[0][0] > 0:
        out.append((f"{stem}_{SPLIT_HEAD}", list(range(0, tops[0][0]))))
    seen: dict[str, int] = {}
    for k, (a, title) in enumerate(tops):
        b = tops[k + 1][0] if k + 1 < len(tops) else total
        name = f"{stem}_{_BAD_NAME.sub('_', title).strip().rstrip('.') or '북마크'}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name += f" ({seen[name]})"
        out.append((name, list(range(a, max(a + 1, b)))))   # 같은 쪽 책갈피 둘이면 앞것도 그 쪽 하나
    return out


def _part(doc: Document, pages: list[int], meta: dict) -> fitz.Document:
    new = fitz.open()
    for i in pages:
        new.insert_pdf(doc.fitz, from_page=i, to_page=i)
    new.set_metadata(meta)
    return new


def _size_groups(doc: Document, total: int, limit: int, meta: dict, progress=None) -> list[list[int]]:
    """앞에서부터 한도 안에 드는 가장 긴 구간을 찾는다(두 배씩 늘려 본 뒤 이분 찾기). 한 쪽만으로 넘치면 그 쪽 하나."""
    def size(a: int, n: int) -> int:
        f = _part(doc, list(range(a, a + n)), meta)
        try:
            return len(f.tobytes(garbage=3, deflate=True))
        finally:
            f.close()

    groups, s = [], 0
    while s < total:
        if progress:
            progress(s, total, f"{s + 1}쪽부터 나누는 중")
        lo, hi = 1, 1                       # lo: 들어간다고 확인한 쪽 수
        if size(s, 1) <= limit:
            hi = 2
            while s + hi <= total and size(s, hi) <= limit:
                lo, hi = hi, hi * 2
            hi = min(hi, total - s + 1)     # hi: 안 들어가는(또는 끝을 넘는) 쪽 수
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if size(s, mid) <= limit:
                    lo = mid
                else:
                    hi = mid
        groups.append(list(range(s, s + lo)))
        s += lo
    return groups


def split(doc: Document, out_dir: str, mode: str = "count", value=1, ranges: list[str] | None = None,
          stem: str | None = None, progress=None) -> list[str]:
    """mode: count(파일 당 쪽 수) · size(파일 당 MB) · bookmarks(최상위 책갈피마다) · parts(몇 덩어리로) · ranges(범위 목록) · each(한 쪽씩).

    out_dir 아래 원본 이름 하위 폴더를 만들고 <이름>_파트N.pdf(책갈피는 <이름>_<제목>.pdf)로 저장한다.
    """
    total = doc.page_count
    stem = stem or os.path.splitext(doc.name)[0]
    folder = split_folder(out_dir, stem)
    md = doc.fitz.metadata or {}
    meta = {k: md.get(k) or "" for k in ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")}
    named: list[tuple[str, list[int]]] | None = None
    groups: list[list[int]] = []
    if mode == "each":
        groups = [[i] for i in range(total)]
    elif mode == "count":
        n = max(1, int(value))
        groups = [list(range(i, min(i + n, total))) for i in range(0, total, n)]
    elif mode == "size":
        limit = int(float(value) * 1024 * 1024)
        groups = _size_groups(doc, total, limit, meta, progress)
    elif mode == "parts":
        k = max(1, min(int(value), total))
        base, extra = divmod(total, k)
        s = 0
        for j in range(k):
            n = base + (1 if j < extra else 0)
            groups.append(list(range(s, s + n)))
            s += n
    elif mode == "ranges":
        groups = [parse_range(r, total) for r in (ranges or []) if r.strip()]
    elif mode == "bookmarks":
        named = _bookmark_groups(doc, total, stem)
    else:
        raise ValueError(f"모르는 나누기 방식: {mode}")
    groups = [g for g in groups if g]
    if named is None:
        if len(groups) == 1 and len(groups[0]) == total:
            named = [(stem, groups[0])]      # 쪽 수가 한 파일 몫 이하면 통째로 <이름>.pdf
        else:
            named = [(f"{stem}_{SPLIT_PART}{n}", g) for n, g in enumerate(groups, 1)]
    os.makedirs(folder, exist_ok=True)
    outs = []
    with doc.lock:
        for k, (name, g) in enumerate(named):
            if progress:
                progress(k, len(named), f"{k + 1} / {len(named)} 저장 중")
            new = _part(doc, g, meta)
            out = _fit_name(folder, name)
            new.save(out, garbage=3, deflate=True)
            new.close()
            outs.append(out)
    return outs


def merge(paths: list[str], out_path: str, progress=None) -> str:
    """파일 여러 개를 차례대로 하나로. 이미지·텍스트 파일도 쪽으로 끼운다."""
    from .document import toc_entries
    out = fitz.open()
    toc: list[list] = []
    for n, p in enumerate(paths):
        src = fitz.open(p)
        if src.needs_pass:
            raise ValueError(f"암호가 걸린 파일입니다: {os.path.basename(p)}")
        if not src.is_pdf:
            src = fitz.open("pdf", src.convert_to_pdf())
        toc += toc_entries(src, page_offset=out.page_count)  # insert_pdf 는 북마크를 안 가져온다
        out.insert_pdf(src)
        src.close()
        if progress:
            progress(n + 1, len(paths), os.path.basename(p))
    if toc:
        out.set_toc(toc)
    out.save(out_path, garbage=3, deflate=True)
    out.close()
    return out_path


_CONTENT_KEYS = ("Contents", "Resources", *_BOXES, "Rotate", "UserUnit", "Group")


def _swap_content(f: fitz.Document, dst: int, src: int):
    """dst 쪽(xref)의 내용·자원·상자·회전만 src 쪽 것으로 바꾼다. 주석(/Annots)은 그대로 남는다."""
    gone = []
    for key in _CONTENT_KEYS:
        t, v = _inherited(f, src, key)
        if t == "null":
            gone.append(key)
        else:
            f.xref_set_key(dst, key, v)
    if "Rotate" in gone:   # 부모에게서 물려받은 회전이 남지 않게 0 으로 못박는다
        gone.remove("Rotate")
        f.xref_set_key(dst, "Rotate", "0")
    if gone:
        del_keys(f, dst, gone)


def replace(doc: Document, pages: list[int], path: str, src_pages: list[int] | None = None,
            keep_annots: bool = False, password: str | None = None):
    """고른 쪽들을 다른 PDF 의 쪽들로 바꾼다. 두 쪽 수는 달라도 된다.
    keep_annots 가 거짓이면 대상 쪽을 지우고 그 첫 자리에 원본 쪽을 끼운다.
    참이면 앞에서부터 짧은 쪽 수만큼 쪽 내용만 갈아 끼워 주석을 남기고, 원본이 많으면 남은 쪽을 뒤에 끼우고
    대상이 많으면 남은 대상 쪽을 지운다."""
    pages = sorted(set(pages))
    if not pages:
        return
    # 원본을 먼저 열어 본다. 전에는 쪽을 먼저 지운 뒤 암호 문서에서 깨진 쪽을 끼우고 멈췄다.
    src = open_source(path, password)
    n = src.page_count
    if src_pages is not None and any(not 0 <= i < n for i in src_pages):
        src.close()
        raise ValueError(f"가져올 쪽이 원본에 없습니다(1~{n}쪽)")
    sp = list(range(n)) if src_pages is None else list(src_pages)
    if len(pages) >= doc.page_count and not sp:
        src.close()
        raise ValueError("쪽을 전부 지울 수는 없습니다")
    doc.snapshot()
    doc.dedupe = True
    with doc.lock:
        f = doc.fitz
        if not keep_annots:
            at = pages[0]
            f.delete_pages(pages)
            for k, i in enumerate(sp):
                f.insert_pdf(src, from_page=i, to_page=i, start_at=at + k)
        else:
            k = min(len(pages), len(sp))
            for j, i in enumerate(sp[k:]):   # 남는 원본 쪽은 마지막 대상 쪽 뒤에
                f.insert_pdf(src, from_page=i, to_page=i, start_at=pages[-1] + 1 + j)
            base = f.page_count
            for i in sp[:k]:   # 갈아 끼울 원본은 잠깐 맨 뒤에 붙였다가 내용만 옮기고 걷는다
                f.insert_pdf(src, from_page=i, to_page=i)
            for j in range(k):
                _swap_content(f, f[pages[j]].xref, f[base + j].xref)
            if f.page_count > base:
                f.delete_pages(list(range(base, f.page_count)))
            if len(pages) > k:
                f.delete_pages(pages[k:])
        src.close()


def duplicate(doc: Document, pages: list[int]):
    doc.snapshot()
    doc.dedupe = True  # fullcopy_page 는 내용을 통째로 한 벌 더 만든다
    with doc.lock:
        for i in sorted(pages, reverse=True):
            doc.fitz.fullcopy_page(i, i + 1)


def crop(doc: Document, pages: Iterable[int], rect: fitz.Rect | None = None, margins: dict | None = None):
    """margins(자르기 창): MediaBox 안쪽으로 들어온 거리(pt, 보이는 방향의 top·left·bottom·right)로 쪽마다 CropBox 만 정한다.
    rect(홈 탭 자르기 도구): 지금 보이는 영역 기준 상자."""
    if margins is not None:
        doc.snapshot()
        with doc.lock:
            f = doc.fitz
            for i in pages:
                rot, media = page_geom(f, i)
                r = _inset(media, margins, rot)
                if r:
                    f.xref_set_key(f[i].xref, "CropBox", _arr(r))
        return
    doc.snapshot()
    with doc.lock:
        for i in pages:
            p = doc.fitz[i]
            # rect 는 지금 보이는 영역(cropbox) 왼쪽 위 기준의 회전 전 쪽 좌표. set_cropbox 는 mediabox 기준이라
            # 이미 자른 쪽은 cropbox 원점만큼 옮겨야 한다(안 그러면 다시 자를 때 엉뚱한 곳이 남는다).
            cb = p.cropbox
            r = (fitz.Rect(rect) + (cb.x0, cb.y0, cb.x0, cb.y0)) & p.mediabox
            if not r.is_empty:
                p.set_cropbox(r)


def set_box(doc: Document, pages: Iterable[int], box: str, rect: fitz.Rect):
    """box: media / crop / trim / bleed / art"""
    doc.snapshot()
    with doc.lock:
        for i in pages:
            p = doc.fitz[i]
            r = fitz.Rect(rect)
            if box != "media":
                r &= p.mediabox  # 대화상자 cm 반올림으로 MediaBox 를 살짝 넘는 일이 있다
            getattr(p, f"set_{box}box")(r)


# ── 박스 여백: 보이는 방향(회전 반영)의 위·왼쪽·아래·오른쪽 거리 ↔ PDF 좌표(아래가 0) 상자 ──
SIDES = ("top", "right", "bottom", "left")   # 시계 방향
BOX_KEYS = {"crop": "CropBox", "bleed": "BleedBox", "trim": "TrimBox", "art": "ArtBox"}


def _to_view(pdf: dict, rot: int) -> dict:
    """PDF 쪽 네 변의 값 → 보이는 방향. /Rotate 90 이면 PDF 왼쪽 변이 화면 위로 간다."""
    k = (rot // 90) % 4
    return {SIDES[j]: pdf[SIDES[(j - k) % 4]] for j in range(4)}


def _to_pdf(view: dict, rot: int) -> dict:
    k = (rot // 90) % 4
    return {SIDES[(j - k) % 4]: view[SIDES[j]] for j in range(4)}


def _arr(r: list[float]) -> str:
    return "[" + " ".join(f"{v:g}" for v in r) + "]"


def page_geom(f: fitz.Document, i: int) -> tuple[int, list[float]]:
    """(회전, MediaBox PDF 좌표)."""
    page = f[i]
    rot, xref = page.rotation, page.xref
    del page
    return rot, _box(f, xref, "MediaBox") or [0, 0, 595, 842]


def view_size(media: list[float], rot: int) -> tuple[float, float]:
    """MediaBox 의 보이는 방향 가로·세로(pt)."""
    w, h = media[2] - media[0], media[3] - media[1]
    return (h, w) if rot in (90, 270) else (w, h)


def _inset(media: list[float], margins: dict, rot: int) -> list[float] | None:
    m = _to_pdf(margins, rot)
    r = [media[0] + m["left"], media[1] + m["bottom"], media[2] - m["right"], media[3] - m["top"]]
    r = [max(r[0], media[0]), max(r[1], media[1]), min(r[2], media[2]), min(r[3], media[3])]
    return r if r[2] > r[0] and r[3] > r[1] else None


def box_margins(f: fitz.Document, i: int, box: str = "crop") -> dict:
    """쪽 i 의 박스가 MediaBox 안쪽으로 들어온 거리(pt, 보이는 방향). 없는 블리드·트림·아트 박스는 자르기 박스를 따른다."""
    rot, media = page_geom(f, i)
    xref = f[i].xref
    r = _box(f, xref, BOX_KEYS[box]) if box != "crop" else None
    r = r or _box(f, xref, "CropBox") or media
    pdf = {"left": r[0] - media[0], "bottom": r[1] - media[1], "right": media[2] - r[2], "top": media[3] - r[3]}
    return {k: max(0.0, v) for k, v in _to_view(pdf, rot).items()}


def set_boxes(doc: Document, pages: Iterable[int], margins: dict, size: tuple[float, float] | None = None,
              offset: tuple[float, float] | None = None):
    """페이지 박스 창: margins = {"crop"|"bleed"|"trim"|"art": 여백 dict(pt, 보이는 방향)}, 준 박스만 새로 정한다.

    size(보이는 방향 가로·세로 pt)가 지금 쪽보다 크면 MediaBox 를 넓힌다(줄이지는 않는다). 원래 쪽은 가운데(offset 없음)나
    offset(왼쪽 위에서 x·y)에 놓이고 내용은 제자리라 주석을 옮길 일이 없다. 여백은 넓힌 MediaBox 기준이다.
    넓힐 때 손대지 않은 박스는 제자리, 다만 MediaBox 와 같던 박스는 새 MediaBox 를 따른다.
    끝으로 포함 관계를 맞춘다: Bleed·Crop ⊆ Media, Trim ⊆ Bleed, Art ⊆ Trim (교집합이 없으면 [0 0 0 0])."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        for i in pages:
            rot, media = page_geom(f, i)
            xref = f[i].xref
            cur = {b: _box(f, xref, k) for b, k in BOX_KEYS.items()}
            if size:
                w, h = view_size(media, rot)
                ex, ey = max(0.0, size[0] - w), max(0.0, size[1] - h)
                if ex or ey:
                    ox, oy = (ex / 2, ey / 2) if offset is None else (min(max(offset[0], 0), ex), min(max(offset[1], 0), ey))
                    g = _to_pdf({"top": oy, "left": ox, "bottom": ey - oy, "right": ex - ox}, rot)
                    old = media
                    media = [old[0] - g["left"], old[1] - g["bottom"], old[2] + g["right"], old[3] + g["top"]]
                    f.xref_set_key(xref, "MediaBox", _arr(media))
                    for b, r in cur.items():
                        if r and all(abs(a - c) < 0.01 for a, c in zip(r, old)):
                            cur[b] = list(media)
            for b, m in margins.items():
                cur[b] = _inset(media, m, rot) or [0, 0, 0, 0]

            def inside(r, outer):
                if r is None:
                    return None
                q = [max(r[0], outer[0]), max(r[1], outer[1]), min(r[2], outer[2]), min(r[3], outer[3])]
                return q if q[2] > q[0] and q[3] > q[1] else [0, 0, 0, 0]
            crop = cur["crop"] = inside(cur["crop"], media)
            bleed = cur["bleed"] = inside(cur["bleed"], media)
            cur["trim"] = inside(cur["trim"], bleed or crop or media)
            cur["art"] = inside(cur["art"], cur["trim"] or crop or media)
            for b, r in cur.items():
                if r is not None:
                    f.xref_set_key(xref, BOX_KEYS[b], _arr(r))


_NUM =re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _box(f: fitz.Document, xref: int, key: str) -> list[float] | None:
    """쪽 상자(PDF 좌표, 물려받은 것까지)."""
    for _ in range(64):
        t, v = f.xref_get_key(xref, key)
        if t == "array":
            n = [float(x) for x in _NUM.findall(v)]
            if len(n) == 4:
                return [min(n[0], n[2]), min(n[1], n[3]), max(n[0], n[2]), max(n[1], n[3])]
        t, v = f.xref_get_key(xref, "Parent")
        if t != "xref":
            return None
        xref = int(v.split()[0])
    return None


def _move_points(text: str, m: fitz.Matrix) -> str:
    """숫자 배열 [x y x y …] 의 점들을 옮긴다(안쪽 배열이 있어도 됨, InkList)."""
    def arr(a: re.Match) -> str:
        n = [float(x) for x in _NUM.findall(a.group(0))]
        out = []
        for k in range(0, len(n) - 1, 2):
            q = fitz.Point(n[k], n[k + 1]) * m
            out += [q.x, q.y]
        return "[" + " ".join(f"{v:g}" for v in out) + "]"
    return re.sub(r"\[[^\[\]]*\]", arr, text)


def resize(doc: Document, pages: Iterable[int], width: float, height: float, fit: bool = True):
    """쪽 크기를 바꾼다(width·height 는 보이는 방향). fit 이면 내용을 비율대로 맞추고 가운데, 아니면 꽉 차게 늘린다.
    새 쪽에 내용만 옮기면 주석·링크·양식 칸이 사라지므로, 그 쪽 그대로 MediaBox 를 바꾸고 내용·주석 좌표를 같은 변환으로 옮긴다."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        for i in sorted(set(pages)):
            page = f[i]
            px, rot = page.xref, page.rotation
            annots = [a[0] for a in page.annot_xrefs()]
            contents = page.get_contents()
            del page
            media = _box(f, px, "MediaBox") or [0, 0, 595, 842]
            crop = _box(f, px, "CropBox") or media
            crop = [max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3])]
            sw, sh = crop[2] - crop[0], crop[3] - crop[1]
            if sw <= 0 or sh <= 0:
                continue
            pw, ph = (height, width) if rot in (90, 270) else (width, height)
            if fit:
                sx = sy = min(pw / sw, ph / sh)
                ox, oy = (pw - sw * sx) / 2, (ph - sh * sy) / 2
            else:
                sx, sy, ox, oy = pw / sw, ph / sh, 0.0, 0.0
            m = fitz.Matrix(sx, 0, 0, sy, ox - sx * crop[0], oy - sy * crop[1])  # PDF 좌표(아래가 0)
            # 내용: 바깥 q…Q 로 감싸 변환을 걸고, 보이던 칸(CropBox) 밖은 잘라 낸다(새 여백에 비치지 않게)
            head, tail = f.get_new_xref(), f.get_new_xref()
            f.update_object(head, "<<>>")
            f.update_stream(head, (f"q {m.a:g} 0 0 {m.d:g} {m.e:g} {m.f:g} cm "
                                   f"{crop[0]:g} {crop[1]:g} {sw:g} {sh:g} re W n\n").encode())
            f.update_object(tail, "<<>>")
            f.update_stream(tail, b"\nQ\n")
            f.xref_set_key(px, "Contents", "[" + " ".join(f"{x} 0 R" for x in [head, *contents, tail]) + "]")
            f.xref_set_key(px, "MediaBox", f"[0 0 {pw:g} {ph:g}]")
            del_keys(f, px, ("CropBox", "TrimBox", "BleedBox", "ArtBox"))
            # 주석·링크·양식 칸: 자리와 꼭짓점 좌표를 같은 변환으로
            for ax in annots:
                for key in ("Rect", "QuadPoints", "Vertices", "L", "CL", "InkList"):
                    t, v = f.xref_get_key(ax, key)
                    if t == "array":
                        nv = _move_points(v, m)
                        if key == "Rect":
                            n = [float(x) for x in _NUM.findall(nv)]
                            nv = f"[{min(n[0], n[2]):g} {min(n[1], n[3]):g} {max(n[0], n[2]):g} {max(n[1], n[3]):g}]"
                        f.xref_set_key(ax, key, nv)
                t, v = f.xref_get_key(ax, "RD")
                if t == "array":
                    n = [float(x) for x in _NUM.findall(v)]
                    if len(n) == 4:
                        f.xref_set_key(ax, "RD", f"[{n[0] * sx:g} {n[1] * sy:g} {n[2] * sx:g} {n[3] * sy:g}]")


# 쪽 레이블: PyMuPDF 의 set_page_labels 는 한글 접두어를 BOM 없는 UTF-8 로 넣고, get_label·get_page_labels 는
# UTF-16 글자열을 「<FEFF…>」 그대로 돌려준다. 그래서 읽기·쓰기·번호 만들기를 여기서 한다.
def _pdf_text(s: str) -> str:
    """PDF 글자열 표기(<…> 16진 또는 (…))를 글자로. BOM 없는 16진은 옛 PyMuPDF 가 넣은 UTF-8 로 본다."""
    m = re.fullmatch(r"\s*<([0-9A-Fa-f\s]*)>\s*", s or "")
    if not m:
        return s or ""
    b = bytes.fromhex(re.sub(r"\s", "", m.group(1)))
    if b[:2] == b"\xfe\xff":
        return b[2:].decode("utf-16-be", "replace")
    if b[:3] == b"\xef\xbb\xbf":
        b = b[3:]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def read_page_labels(f: fitz.Document) -> list[dict]:
    out = []
    for l in f.get_page_labels() or []:
        d = dict(l)
        d["prefix"] = _pdf_text(d.get("prefix", ""))
        out.append(d)
    return sorted(out, key=lambda l: l["startpage"])


def write_page_labels(f: fitz.Document, labels: list[dict]) -> None:
    nums = []
    for l in sorted(labels, key=lambda l: l["startpage"]):
        part = ""
        if l.get("style"):
            part += f"/S/{l['style']}"
        if l.get("prefix"):
            part += "/P" + fitz.get_pdf_str(l["prefix"])
        if int(l.get("firstpagenum", 1) or 1) != 1:
            part += f"/St {int(l['firstpagenum'])}"
        nums.append(f"{int(l['startpage'])}<<{part}>>")
    cat = f.pdf_catalog()
    if nums:
        f.xref_set_key(cat, "PageLabels", "<</Nums[" + " ".join(nums) + "]>>")
    else:
        del_keys(f, cat, ["PageLabels"])


def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                 (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out += r
            n -= v
    return out


def _label(rule: dict, n: int) -> str:
    style = rule.get("style", "")
    num = "" if not style else str(n) if style == "D" else _roman(n) if style == "r" else _roman(n).upper() if style == "R" \
        else chr(97 + (n - 1) % 26) * ((n - 1) // 26 + 1) if style == "a" else chr(65 + (n - 1) % 26) * ((n - 1) // 26 + 1)
    return rule.get("prefix", "") + num


def page_label_strings(f: fitz.Document) -> list[str]:
    rules = read_page_labels(f)
    out = []
    for i in range(f.page_count):
        rule = next((r for r in reversed(rules) if r["startpage"] <= i), None)
        out.append(_label(rule, int(rule.get("firstpagenum", 1) or 1) + i - rule["startpage"]) if rule else "")
    return out


def set_labels(doc: Document, start_page: int, style: str = "D", prefix: str = "", first: int = 1):
    """style: D(1,2,3) r(i,ii) R(I,II) a(a,b) A(A,B) ''(접두어만)"""
    doc.snapshot()
    with doc.lock:
        labels = [l for l in read_page_labels(doc.fitz) if l.get("startpage") != start_page]
        labels.append({"startpage": start_page, "prefix": prefix, "style": style, "firstpagenum": first})
        write_page_labels(doc.fitz, labels)


LABEL_STYLES = [("D", "1,2,3,..."), ("r", "i,ii,iii,..."), ("R", "I,II,III,..."), ("a", "a,b,c,..."), ("A", "A,B,C,..."), ("", "없음")]
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def label_number(text: str, style: str) -> int | None:
    """시작 칸 글자를 번호로. 숫자는 늘 받고, 로마자·알파벳은 그 스타일일 때 받는다(iv → 4, aa → 27)."""
    t = (text or "").strip()
    if t.isdigit():
        return int(t)
    if style in ("r", "R") and t and all(c in _ROMAN for c in t.lower()):
        v = [_ROMAN[c] for c in t.lower()]
        n = sum(-x if i + 1 < len(v) and x < v[i + 1] else x for i, x in enumerate(v))
        return n if _roman(n) == t.lower() and (t.islower() if style == "r" else t.isupper()) else None
    if style in ("a", "A") and t and len(set(t)) == 1 and t.isalpha() and t.isascii() \
            and (t.islower() if style == "a" else t.isupper()):
        return (len(t) - 1) * 26 + ord(t[0].lower()) - 96
    return None


def label_preview(style: str, prefix: str, start: int) -> str:
    rule = {"style": style, "prefix": prefix}
    return ",".join(_label(rule, start + k) for k in range(3)) + ",..."


def page_of_label(f: fitz.Document, text: str, after: int = 0) -> int | None:
    """레이블 글자(또는 쪽 번호)로 쪽을 찾는다. after 쪽부터 처음 맞는 레이블, 없으면 쪽 번호로 읽는다."""
    t = (text or "").strip()
    if not t:
        return None
    labs = page_label_strings(f)
    for i in list(range(after, f.page_count)) + list(range(0, after)):
        if labs[i] and labs[i] == t:
            return i
    return int(t) - 1 if t.isdigit() and 1 <= int(t) <= f.page_count else None


def label_at(f: fitz.Document, page: int) -> dict:
    """그 쪽에 걸린 스타일·접두사·번호. 레이블이 없으면 1,2,3 / 빈칸 / 1."""
    rule = next((r for r in reversed(read_page_labels(f)) if r["startpage"] <= page), None)
    if not rule:
        return {"style": "D", "prefix": "", "start": 1}
    return {"style": rule.get("style", ""), "prefix": rule.get("prefix", ""),
            "start": int(rule.get("firstpagenum", 1) or 1) + page - rule["startpage"]}


def _rule_for(rules: list[dict], page: int) -> dict | None:
    return next((r for r in reversed(rules) if r["startpage"] <= page), None)


def apply_labels(doc: Document, first: int, last: int, style: str = "D", prefix: str = "", start: int = 1,
                 extend: bool = False):
    """first~last 쪽(0부터)에 레이블 섹션. 안쪽 섹션은 걷고, last 다음 쪽은 원래 번호를 잇는다.
    extend 면 바로 앞 섹션의 스타일·접두사·번호를 범위까지 늘린다."""
    doc.snapshot()
    with doc.lock:
        f = doc.fitz
        total = f.page_count
        rules = read_page_labels(f) or [{"startpage": 0, "style": "D", "prefix": "", "firstpagenum": 1}]
        for r in rules:
            r["firstpagenum"] = int(r.get("firstpagenum", 1) or 1)
        nxt = None
        if last + 1 < total and not any(r["startpage"] == last + 1 for r in rules):
            r = _rule_for(rules, last + 1)
            if r:
                nxt = {"startpage": last + 1, "style": r.get("style", ""), "prefix": r.get("prefix", ""),
                       "firstpagenum": r["firstpagenum"] + last + 1 - r["startpage"]}
        head = rules[0] if rules[0]["startpage"] == 0 else None
        rules = [r for r in rules if not first <= r["startpage"] <= last]
        if not extend:
            rules.append({"startpage": first, "style": style, "prefix": prefix, "firstpagenum": int(start)})
        elif first == 0:   # 1쪽부터 늘리면 앞 섹션이 없다: 1쪽 섹션을 그대로 둔다
            rules.append(head or {"startpage": 0, "style": "D", "prefix": "", "firstpagenum": 1})
        if nxt:
            rules.append(nxt)
        rules.sort(key=lambda r: r["startpage"])
        merged: list[dict] = []
        for r in rules:   # 앞 섹션을 그대로 잇는 섹션은 합친다
            p = merged[-1] if merged else None
            if p and p.get("style", "") == r.get("style", "") and p.get("prefix", "") == r.get("prefix", "") \
                    and (not r.get("style") or p["firstpagenum"] + r["startpage"] - p["startpage"] == r["firstpagenum"]):
                continue
            merged.append(r)
        write_page_labels(f, merged)


def labels(doc: Document) -> list[str]:
    try:
        return [s or str(i + 1) for i, s in enumerate(page_label_strings(doc.fitz))]
    except Exception:  # noqa: BLE001
        return [str(i + 1) for i in range(doc.page_count)]
