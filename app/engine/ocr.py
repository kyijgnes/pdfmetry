# -*- coding: utf-8 -*-
"""문자 인식(OCR): 윈도우 내장 Windows.Media.Ocr 를 PowerShell 로 부른다. 돈 드는 엔진·tesseract 없음.

어드민 시험 등록 툴(circled_ocr.py)과 같은 방식. 그림 여러 장을 한 번에 넘기고 낱말 상자(픽셀)를 JSON 으로 받는다.
화면(Qt) 코드는 들여오지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Callable

import fitz

from .document import korean_font_name, redact_only
from .tools import EngineError, _pages, _say, korean_font, korean_font_path

Progress = Callable[[int, int, str], None] | None
TIMEOUT = 600
NO_LANG_MSG = "이 PC 에 윈도우 한국어 문자 인식이 없습니다"


class NoLanguage(EngineError):
    """고른 인식 언어가 이 PC 윈도우에 없다."""

_PS_COMMON = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
"""

_PS_OCR = r"""
param([string]$ListFile, [string]$Lang)
""" + _PS_COMMON + r"""
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
if ($null -eq $engine) { Write-Output 'NO_OCR_LANG'; exit 3 }
$utf8 = New-Object System.Text.UTF8Encoding $false
Get-Content -Path $ListFile -Encoding UTF8 | Where-Object { $_ -ne '' } | ForEach-Object {
    $png = $_
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($png)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @($res.Lines | ForEach-Object {
        $ln = $_
        $words = @($ln.Words | ForEach-Object {
            $r = $_.BoundingRect
            [pscustomobject]@{ text = $_.Text; x = [double]$r.X; y = [double]$r.Y; w = [double]$r.Width; h = [double]$r.Height } })
        [pscustomobject]@{ text = $ln.Text; words = $words } })
    $out = [System.IO.Path]::ChangeExtension($png, '.json')
    [System.IO.File]::WriteAllText($out, (ConvertTo-Json -InputObject $lines -Depth 5 -Compress), $utf8)
    $stream.Dispose()
}
"""

_PS_LANGS = _PS_COMMON + r"""
$langs = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }
Write-Output (ConvertTo-Json -InputObject @($langs) -Compress)
"""


def _run_ps(script_text: str, args: list[str], folder: str, name: str = "ocr.ps1") -> subprocess.CompletedProcess:
    script = os.path.join(folder, name)
    with open(script, "w", encoding="utf-8-sig") as f:
        f.write(script_text)
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, *args],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise EngineError(f"윈도우 문자 인식을 못 돌렸습니다 ({e})") from e


def available_languages() -> list[str]:
    """윈도우 문자 인식이 되는 언어 태그 목록(예: ['ko', 'en-US'])."""
    tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
    try:
        r = _run_ps(_PS_LANGS, [], tmp, "langs.ps1")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        return []
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace").strip() or "[]")
    except ValueError:
        return []
    if isinstance(data, str):
        data = [data]
    return [str(x) for x in data if x]


def _normalize(lines_json) -> list[dict]:
    """PowerShell JSON(줄 → 낱말) → 낱말 [{text,x,y,w,h,line}] (픽셀)."""
    if lines_json is None:
        return []
    if isinstance(lines_json, dict):
        lines_json = [lines_json]
    words: list[dict] = []
    for li, ln in enumerate(lines_json):
        ws = ln.get("words") if isinstance(ln, dict) else None
        if ws is None:
            continue
        if isinstance(ws, dict):
            ws = [ws]
        for w in ws:
            try:
                words.append({"text": str(w["text"]), "x": float(w["x"]), "y": float(w["y"]),
                              "w": float(w["w"]), "h": float(w["h"]), "line": li})
            except (KeyError, TypeError, ValueError):
                continue
    return words


def langs_of(lang) -> list[str]:
    """`lang` 을 언어 태그 목록으로: 글자 하나("ko")·쉼표로 이은 글자("en-US,ko")·목록 모두 받는다. 겹친 것은 뺀다."""
    if lang is None:
        items: list = []
    elif isinstance(lang, str):
        items = lang.split(",")
    else:
        items = list(lang)
    out: list[str] = []
    for x in items:
        t = str(x).strip()
        if t and t not in out:
            out.append(t)
    return out or ["ko"]


def _ocr_images_one(png_paths: list[str], lang: str) -> list[list[dict]]:
    """언어 하나로 PNG 여러 장을 한 번에 인식(윈도우 OCR 엔진은 언어 하나로만 만들어진다)."""
    tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
    try:
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write("\n".join(os.path.abspath(p) for p in png_paths))
        r = _run_ps(_PS_OCR, ["-ListFile", lst, "-Lang", lang], tmp)
        if r.returncode == 3 or b"NO_OCR_LANG" in r.stdout:
            raise NoLanguage(NO_LANG_MSG if lang.lower().startswith("ko")
                             else f"이 PC 에 윈도우 문자 인식 언어({lang})가 없습니다")
        if r.returncode != 0:
            err = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
            raise EngineError(f"윈도우 문자 인식 실패 ({err[-1] if err else r.returncode})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for png in png_paths:
        jp = os.path.splitext(png)[0] + ".json"
        try:
            with open(jp, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = []
        finally:
            try:
                os.remove(jp)
            except OSError:
                pass
        out.append(_normalize(data))
    return out


def _score(words: list[dict]) -> tuple[int, int]:
    """언어별 결과 고르기 잣대: 낱말 수, 같으면 글자 수."""
    return len(words), sum(len(w["text"]) for w in words)


def ocr_images(png_paths: list[str], lang="ko") -> list[list[dict]]:
    """PNG 여러 장을 인식. 그림마다 낱말 목록.

    언어를 여러 개 주면 언어마다 한 번씩 돌려 그림마다 낱말이 가장 많은 결과를 고른다(윈도우 OCR 은 한 번에 한 언어).
    이 PC 에 없는 언어는 건너뛰고, 고른 언어가 하나도 없을 때만 오류."""
    if not png_paths:
        return []
    langs = langs_of(lang)
    best: list[list[dict]] | None = None
    missing: NoLanguage | None = None
    for lg in langs:
        try:
            got = _ocr_images_one(png_paths, lg)
        except NoLanguage as e:
            missing = e
            continue
        if best is None:
            best = got
        else:
            best = [g if _score(g) > _score(b) else b for b, g in zip(best, got)]
    if best is None:
        raise missing or EngineError(NO_LANG_MSG)
    return best


def ocr_image(png_path: str, lang="ko") -> list[dict]:
    """그림 한 장의 낱말 [{text, x, y, w, h, line}] (픽셀)."""
    if not os.path.exists(png_path):
        raise EngineError(f"그림 파일이 없습니다: {png_path}")
    return ocr_images([png_path], lang)[0]


def _render(page: fitz.Page, folder: str, name: str, dpi: int, clip: fitz.Rect | None = None) -> tuple[str, float]:
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, clip=clip, alpha=False)
    path = os.path.join(folder, name)
    pix.save(path)
    return path, 72.0 / dpi


def _write_layer(page: fitz.Page, words: list[dict], scale: float, origin: fitz.Point | None = None) -> int:
    """낱말들을 보이지 않는 글자층으로(render_mode=3). 넣은 낱말 수.
    낱말 상자는 보이는 쪽(회전 적용) 그림에서 잰 것이라, 자리는 derotation 으로 옮기고 글 방향은 rotate=page.rotation."""
    if not words:
        return 0
    font = korean_font()  # 글자 폭 재기용
    name = korean_font_name(page)
    page.insert_font(fontname=name, fontfile=korean_font_path())
    shape = page.new_shape()
    ox, oy = (origin.x, origin.y) if origin else (0.0, 0.0)
    inv = page.derotation_matrix
    n = 0
    for k, w in enumerate(words):
        text = w["text"].strip()
        if not text:
            continue
        x0, y0 = ox + w["x"] * scale, oy + w["y"] * scale
        ww, hh = w["w"] * scale, w["h"] * scale
        # 글자 크기는 낱말 폭에 맞춘다(고르기·검색 표시가 낱말을 다 덮게). 높이에서 너무 벗어나면 높이 쪽으로
        tl = font.text_length(text, fontsize=hh)
        fs = hh * ww / tl if tl > 0 else hh
        fs = max(2.0, min(max(fs, hh * 0.6), hh * 1.5))
        pos = fitz.Point(x0, y0 + hh * 0.85) * inv
        if k + 1 < len(words) and words[k + 1].get("line") == w.get("line"):
            text += " "  # 폭을 맞추면 낱말끼리 붙어 한 낱말로 읽히므로 띄어쓰기를 넣는다
        try:
            shape.insert_text(pos, text, fontsize=fs, fontname=name, rotate=page.rotation, render_mode=3)
            n += 1
        except Exception:
            continue
    if n:
        shape.commit()
    return n


def _drop_invisible_text(page: fitz.Page) -> None:
    """「다시 인식」: 전에 넣은 보이지 않는 글자층을 걷는다(안 그러면 두 벌). 보이는 글자·그림·선은 그대로."""
    if not any(s.get("type") == 3 for s in page.get_texttrace()):
        return
    whole = page.rect | (page.rect * page.derotation_matrix)
    redact_only(page, [whole], fill=False, images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE, text=fitz.PDF_REDACT_TEXT_REMOVE_INVISIBLE)


def ocr_pages(doc: fitz.Document, pages=None, lang: str | list[str] = "ko", dpi: int = 300,
              progress: Progress = None, force: bool = False) -> int:
    """글자층이 없는 쪽을 인식해 보이지 않는 글자층을 넣는다. 넣은 낱말 수. in place.
    lang 은 글자 하나("ko")도, 목록(["en-US", "ko"])도 받는다(목록이면 쪽마다 낱말이 많은 언어 결과)."""
    targets = [p for p in _pages(doc, pages) if force or not doc[p].get_text("text").strip()]
    if not targets:
        return 0
    tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
    total = 0
    try:
        pngs = []
        for k, pno in enumerate(targets):
            _say(progress, k, len(targets) * 2, f"그림 뜨는 중 {pno + 1}쪽")
            pngs.append(_render(doc[pno], tmp, f"p{pno + 1:04d}.png", dpi)[0])
        _say(progress, len(targets), len(targets) * 2, f"글자 읽는 중 ({len(targets)}쪽)")
        results = ocr_images(pngs, lang)
        for k, (pno, words) in enumerate(zip(targets, results)):
            _say(progress, len(targets) + k, len(targets) * 2, f"글자층 넣는 중 {pno + 1}쪽")
            page = doc[pno]
            if force:
                _drop_invisible_text(page)
            total += _write_layer(page, words, 72.0 / dpi)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _say(progress, len(targets) * 2, len(targets) * 2, "문자 인식 끝")
    return total


def ocr_area(doc: fitz.Document, page_index: int, rect_points, lang="ko", dpi: int = 300) -> str:
    """쪽의 네모 안 글자를 읽어 돌려준다(줄바꿈은 인식한 줄 단위)."""
    if not 0 <= page_index < doc.page_count:
        raise EngineError("쪽 번호가 범위를 벗어났습니다")
    page = doc[page_index]
    clip = visible_rect(page, rect_points)
    if clip.is_empty:
        raise EngineError("읽을 영역이 비어 있습니다")
    tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
    try:
        png, _ = _render(page, tmp, "area.png", dpi, clip)
        words = ocr_image(png, lang)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines: dict[int, list[dict]] = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    out = []
    for li in sorted(lines):
        ws = sorted(lines[li], key=lambda w: w["x"])
        out.append(" ".join(w["text"] for w in ws))
    return "\n".join(out)


def _cluster_rows(words: list[dict]) -> list[list[dict]]:
    """y 가 겹치는 낱말끼리 한 줄로."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["y"], w["x"])):
        cy = w["y"] + w["h"] / 2
        for row in rows:
            ry0 = min(r["y"] for r in row)
            ry1 = max(r["y"] + r["h"] for r in row)
            if ry0 - w["h"] * 0.3 <= cy <= ry1 + w["h"] * 0.3:
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x"])
    return rows


def _column_edges(rows: list[list[dict]]) -> list[float]:
    """모든 낱말을 x 축에 눕혀 빈 틈(평균 글자 높이 이상)이 있는 자리를 열 경계로."""
    spans = sorted((w["x"], w["x"] + w["w"]) for row in rows for w in row)
    if not spans:
        return []
    avg_h = sum(w["h"] for row in rows for w in row) / max(1, sum(len(r) for r in rows))
    gap_min = avg_h * 0.8
    merged = [list(spans[0])]
    for x0, x1 in spans[1:]:
        if x0 <= merged[-1][1] + gap_min:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    edges = []
    for a, b in zip(merged, merged[1:]):
        edges.append((a[1] + b[0]) / 2)
    return edges


def recognize_table(doc: fitz.Document, page_index: int, rect_points=None, lang="ko",
                    dpi: int = 300) -> list[list[str]]:
    """표를 2차원 글자 목록으로. 글자층이 있으면 find_tables, 없으면 OCR 낱말을 줄·열로 묶는다."""
    if not 0 <= page_index < doc.page_count:
        raise EngineError("쪽 번호가 범위를 벗어났습니다")
    page = doc[page_index]
    clip = fitz.Rect(rect_points) & page.rect if rect_points is not None else page.rect
    has_text = bool(page.get_text("text", clip=clip).strip())
    if has_text:
        try:
            tabs = page.find_tables(clip=clip)
            if tabs.tables:
                best = max(tabs.tables, key=lambda t: t.row_count * t.col_count)
                return [[("" if c is None else str(c)) for c in row] for row in best.extract()]
        except Exception:
            pass
        # 표 틀을 못 찾으면 글자 낱말을 줄·열로 묶는다
        words = [{"text": w[4], "x": w[0], "y": w[1], "w": w[2] - w[0], "h": w[3] - w[1], "line": w[6]}
                 for w in page.get_text("words", clip=clip)]
    else:
        tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
        try:
            png, scale = _render(page, tmp, "table.png", dpi, clip)
            words = ocr_image(png, lang)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for w in words:
            for k in ("x", "y", "w", "h"):
                w[k] *= scale
    if not words:
        return []
    rows = _cluster_rows(words)
    edges = _column_edges(rows)
    ncol = len(edges) + 1
    out: list[list[str]] = []
    for row in rows:
        cells = [""] * ncol
        for w in row:
            cx = w["x"] + w["w"] / 2
            ci = sum(1 for e in edges if cx > e)
            cells[ci] = (cells[ci] + " " + w["text"]).strip()
        out.append(cells)
    return out


# ── 영역 OCR: 그린 사각형들을 읽어 그 쪽 글자층으로 ─────────────────────
def visible_rect(page: fitz.Page, rect) -> fitz.Rect:
    """회전 전 쪽 좌표(보기가 넘기는 꼴) → 보이는 쪽(회전 적용) 좌표. 그림 뜨기 clip 은 보이는 쪽 좌표다."""
    r = fitz.Rect(rect)
    if page.rotation:
        r = r * page.rotation_matrix
    return r.normalize() & page.rect


def ocr_area_into(doc: fitz.Document, areas, lang="ko", dpi: int = 300, progress: Progress = None) -> int:
    """(쪽, 사각형) 여러 개를 읽어 그 자리에 보이지 않는 글자층을 넣는다. 넣은 낱말 수. in place.

    사각형은 회전 전 쪽 좌표. 같은 자리를 다시 읽으면 그 사각형 안의 옛 보이지 않는 글자는 걷고 넣는다(두 벌 방지).
    그림·보이는 글자는 그대로 둔다."""
    jobs = []
    for pno, rect in areas:
        if not 0 <= pno < doc.page_count:
            raise EngineError("쪽 번호가 범위를 벗어났습니다")
        clip = visible_rect(doc[pno], rect)
        if clip.is_empty or clip.width < 2 or clip.height < 2:
            continue
        jobs.append((pno, clip))
    if not jobs:
        raise EngineError("읽을 영역이 비어 있습니다")
    tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
    total = 0
    n = len(jobs)
    try:
        pngs = []
        for k, (pno, clip) in enumerate(jobs):
            _say(progress, k, n * 2, f"그림 뜨는 중 {pno + 1}쪽")
            pngs.append(_render(doc[pno], tmp, f"a{k:04d}.png", dpi, clip)[0])
        _say(progress, n, n * 2, f"글자 읽는 중 (영역 {n}개)")
        results = ocr_images(pngs, lang)
        for k, ((pno, clip), words) in enumerate(zip(jobs, results)):
            _say(progress, n + k, n * 2, f"글자층 넣는 중 {pno + 1}쪽")
            page = doc[pno]
            _drop_invisible_in(page, clip)
            total += _write_layer(page, words, 72.0 / dpi, origin=clip.tl)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _say(progress, n * 2, n * 2, "영역 문자 인식 끝")
    return total


def _drop_invisible_in(page: fitz.Page, clip: fitz.Rect) -> None:
    """보이는 쪽 좌표 clip 안의 보이지 않는 글자만 걷는다."""
    if not any(s.get("type") == 3 for s in page.get_texttrace()):
        return
    redact_only(page, [clip * page.derotation_matrix], fill=False, images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE, text=fitz.PDF_REDACT_TEXT_REMOVE_INVISIBLE)


# ── 표 인식: 문서 전체에서 표 찾기 ─────────────────────────────────────
def _row_segments(row: list[dict], gap_min: float) -> list[list[dict]]:
    """한 줄의 낱말을 큰 틈(gap_min 이상)마다 끊는다."""
    segs = [[row[0]]]
    for w in row[1:]:
        prev = segs[-1][-1]
        if w["x"] - (prev["x"] + prev["w"]) >= gap_min:
            segs.append([w])
        else:
            segs[-1].append(w)
    return segs


def _grid(rows: list[list[dict]]) -> list[list[str]]:
    edges = _column_edges(rows)
    ncol = len(edges) + 1
    out = []
    for row in rows:
        cells = [""] * ncol
        for w in row:
            cx = w["x"] + w["w"] / 2
            ci = sum(1 for e in edges if cx > e)
            cells[ci] = (cells[ci] + " " + w["text"]).strip()
        out.append(cells)
    return out


def tables_from_words(words: list[dict]) -> list[dict]:
    """틀(선) 없이 낱말 자리만으로 표를 찾는다(스캔 쪽 OCR 결과용).

    한 줄 안에 글자 높이보다 넓은 틈으로 갈린 덩어리가 둘 이상인 줄을 「표 줄」로 보고, 표 줄이 둘 이상 잇달아
    나오면 표 하나. 돌려주는 것: [{"bbox": (x0, y0, x1, y1), "rows": [[글자]]}] (낱말과 같은 좌표)."""
    if not words:
        return []
    avg_h = sum(w["h"] for w in words) / len(words)
    gap_min = avg_h * 1.2
    rows = _cluster_rows(words)
    rows.sort(key=lambda r: min(w["y"] for w in r))
    out: list[dict] = []
    run: list[list[dict]] = []

    def flush():
        if len(run) >= 2:
            ws = [w for r in run for w in r]
            x0 = min(w["x"] for w in ws); y0 = min(w["y"] for w in ws)
            x1 = max(w["x"] + w["w"] for w in ws); y1 = max(w["y"] + w["h"] for w in ws)
            grid = _grid(run)
            if grid and len(grid[0]) >= 2:
                out.append({"bbox": (x0, y0, x1, y1), "rows": grid})
        run.clear()

    for row in rows:
        if run and min(w["y"] for w in row) - max(w["y"] + w["h"] for w in run[-1]) > avg_h * 3:
            flush()   # 줄 사이가 크게 비면 다른 표
        if len(_row_segments(row, gap_min)) >= 2:
            run.append(row)
        else:
            flush()
    flush()
    return out


def _cells(rows) -> list[list[str]]:
    return [[("" if c is None else " ".join(str(c).split())) for c in row] for row in rows]


def find_tables(doc: fitz.Document, pages=None, lang="ko", dpi: int = 300, progress: Progress = None) -> list[dict]:
    """문서(또는 고른 쪽)의 표를 모두 찾는다. [{"page": 쪽, "bbox": (x0, y0, x1, y1) 회전 전 쪽 좌표, "rows": [[글자]]}].

    글자층이 있는 쪽은 PyMuPDF find_tables(선으로 그린 표), 글자층이 없는 쪽(스캔)은 OCR 낱말 자리로 묶는다.
    인식 언어가 없어 스캔 쪽을 못 읽으면 그 쪽은 건너뛴다."""
    targets = _pages(doc, pages)
    found: list[dict] = []
    scanned: list[int] = []
    n = len(targets)
    for k, pno in enumerate(targets):
        _say(progress, k, n + 1, f"표 찾는 중 {pno + 1}쪽")
        page = doc[pno]
        if not page.get_text("text").strip():
            if page.get_images(full=False):
                scanned.append(pno)
            continue
        try:
            tabs = page.find_tables()
        except Exception:  # noqa: BLE001
            continue
        for t in tabs.tables:
            rows = _cells(t.extract())
            if not rows or (len(rows) < 2 and len(rows[0]) < 2):
                continue
            found.append({"page": pno, "bbox": tuple(fitz.Rect(t.bbox) * page.derotation_matrix), "rows": rows})
    if scanned:
        _say(progress, n, n + 1, f"스캔 쪽 글자 읽는 중 ({len(scanned)}쪽)")
        tmp = tempfile.mkdtemp(prefix="pdfapp_ocr_")
        try:
            pngs = [_render(doc[p], tmp, f"t{p + 1:04d}.png", dpi)[0] for p in scanned]
            try:
                results = ocr_images(pngs, lang)
            except NoLanguage:
                results = [[] for _ in scanned]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        scale = 72.0 / dpi
        for pno, words in zip(scanned, results):
            for w in words:
                for key in ("x", "y", "w", "h"):
                    w[key] *= scale
            page = doc[pno]
            for t in tables_from_words(words):
                found.append({"page": pno, "bbox": tuple(fitz.Rect(t["bbox"]) * page.derotation_matrix), "rows": t["rows"]})
    found.sort(key=lambda t: (t["page"], t["bbox"][1], t["bbox"][0]))
    _say(progress, n + 1, n + 1, "표 찾기 끝")
    return found


def table_html(rows: list[list[str]]) -> str:
    """워드·엑셀에 붙여 넣을 HTML 표."""
    import html as _h
    body = "".join("<tr>" + "".join(f"<td>{_h.escape(c)}</td>" for c in row) + "</tr>" for row in rows)
    return ("<html><head><meta charset=\"utf-8\"></head><body>"
            f"<table border=\"1\" cellspacing=\"0\" cellpadding=\"2\">{body}</table></body></html>")


def table_tsv(rows: list[list[str]]) -> str:
    """탭 구분 글자(칸 안 줄바꿈·탭은 띄어쓰기로)."""
    return "\n".join("\t".join(" ".join(c.split()) for c in row) for row in rows)
