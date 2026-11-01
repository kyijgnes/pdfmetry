"""페이지 구성: 회전·삭제·순서·끼우기·뽑기·나누기·합치기·바꾸기.

모든 함수는 Document 를 받고 바꾸기 전에 snapshot() 을 부른다.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

import fitz

from .document import Document, del_keys


def parse_range(spec: str, total: int) -> list[int]:
    """'1-3,5,8-' → 0-based 목록. 빈 문자열이면 전부."""
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


def subset(pages: Iterable[int], how: str = "all") -> list[int]:
    """홀수(odd)·짝수(even) 쪽만 거른다. 쪽 번호는 1부터 센 것 기준(0-based 0 = 1쪽 = 홀수). all 이면 그대로."""
    if how == "odd":
        return [p for p in pages if p % 2 == 0]
    if how == "even":
        return [p for p in pages if p % 2 == 1]
    return list(pages)


def format_range(pages: Iterable[int]) -> str:
    """0-based 쪽 목록 → 「1,3-5」. 이어진 번호는 묶는다."""
    nums = sorted({int(p) + 1 for p in pages})
    out, k = [], 0
    while k < len(nums):
        j = k
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[k]) if j == k else f"{nums[k]}-{nums[j]}")
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


def split_grid(doc: Document, pages: Iterable[int], cols: int = 2, rows: int = 1) -> list[int]:
    """쪽 하나를 가로 cols × 세로 rows 칸으로 나눠 여러 쪽으로(「페이지 분할」, 스캔한 두 쪽 펼침 나누기).

    칸마다 쪽을 통째로 복사하고 보이는 영역(CropBox)만 그 칸으로 줄인다. 글·주석·링크가 그대로 남는다.
    칸 차례는 보이는 화면 기준 왼쪽 위 → 오른쪽 → 다음 줄. 돌려주는 값은 새 쪽마다 원래 쪽 번호(되돌리기·썸네일용)."""
    cols, rows = int(cols), int(rows)
    if not (1 <= cols <= 10 and 1 <= rows <= 10) or cols * rows == 1:
        raise ValueError("가로·세로 칸 수는 1~10 이고, 둘 다 1 이면 나눌 게 없습니다")
    targets = sorted({p for p in pages if 0 <= p < doc.page_count})
    doc.snapshot()
    mapping = []
    with doc.lock:
        f = doc.fitz
        for src in range(f.page_count):
            mapping.append(src)
        for pno in reversed(targets):   # 뒤에서부터 끼워 앞 번호가 밀리지 않게
            page = f[pno]
            vis = page.rect
            cb = page.cropbox
            w, h = vis.width / cols, vis.height / rows
            cells = [fitz.Rect(vis.x0 + c * w, vis.y0 + r * h, vis.x0 + (c + 1) * w, vis.y0 + (r + 1) * h)
                     for r in range(rows) for c in range(cols)]
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


def insert_blank(doc: Document, at: int, width: float | None = None, height: float | None = None, count: int = 1):
    doc.snapshot()
    with doc.lock:
        if width is None or height is None:
            ref = doc.fitz[min(max(at - 1, 0), doc.page_count - 1)].rect
            width, height = ref.width, ref.height
        for k in range(count):
            doc.fitz.new_page(pno=at + k, width=width, height=height)


def insert_from_pdf(doc: Document, at: int, path: str, pages: Iterable[int] | None = None, password: str | None = None):
    src = fitz.open(path)
    if src.needs_pass:
        if not password or not src.authenticate(password):
            src.close()
            raise ValueError("끼울 문서에 암호가 걸려 있습니다")
    if not src.is_pdf:
        src = fitz.open("pdf", src.convert_to_pdf())
    doc.snapshot()  # 확인을 마친 뒤에(실패해도 되돌리기 걸음이 남지 않게)
    doc.dedupe = True  # 끼운 문서와 같은 글꼴·그림이 겹칠 수 있어 저장 때 합친다
    with doc.lock:
        if pages is None:
            doc.fitz.insert_pdf(src, start_at=at)
        else:
            for k, i in enumerate(pages):
                doc.fitz.insert_pdf(src, from_page=i, to_page=i, start_at=at + k)
        src.close()


def insert_images(doc: Document, at: int, paths: list[str]):
    doc.snapshot()
    doc.dedupe = True
    with doc.lock:
        for k, p in enumerate(paths):
            img = fitz.open(p)
            pdf = fitz.open("pdf", img.convert_to_pdf())
            doc.fitz.insert_pdf(pdf, start_at=at + k)
            img.close(); pdf.close()


def extract(doc: Document, pages: Iterable[int], out_path: str) -> str:
    """고른 쪽만 새 PDF 로 저장."""
    pages = list(pages)
    with doc.lock:
        new = fitz.open()
        for i in pages:
            new.insert_pdf(doc.fitz, from_page=i, to_page=i)
        new.save(out_path, garbage=3, deflate=True)
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


def replace(doc: Document, pages: list[int], path: str, src_pages: list[int] | None = None):
    """고른 쪽들을 다른 PDF 의 쪽들로 바꾼다(지우고 그 자리에 끼움)."""
    pages = sorted(set(pages))
    if not pages:
        return
    # 원본을 먼저 열어 본다. 전에는 쪽을 먼저 지운 뒤 암호 문서에서 깨진 쪽을 끼우고 멈췄다.
    src = fitz.open(path)
    if src.needs_pass:
        src.close()
        raise ValueError("암호가 걸린 PDF 로는 바꿀 수 없습니다")
    if not src.is_pdf:
        src = fitz.open("pdf", src.convert_to_pdf())
    n = src.page_count
    if src_pages is not None and any(not 0 <= i < n for i in src_pages):
        src.close()
        raise ValueError(f"가져올 쪽이 원본에 없습니다(1~{n}쪽)")
    doc.snapshot()
    doc.dedupe = True
    with doc.lock:
        at = pages[0]
        doc.fitz.delete_pages(pages)
        if src_pages is None:
            doc.fitz.insert_pdf(src, start_at=at)
        else:
            for k, i in enumerate(src_pages):
                doc.fitz.insert_pdf(src, from_page=i, to_page=i, start_at=at + k)
        src.close()


def duplicate(doc: Document, pages: list[int]):
    doc.snapshot()
    doc.dedupe = True  # fullcopy_page 는 내용을 통째로 한 벌 더 만든다
    with doc.lock:
        for i in sorted(pages, reverse=True):
            doc.fitz.fullcopy_page(i, i + 1)


def crop(doc: Document, pages: Iterable[int], rect: fitz.Rect):
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


_NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


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
