"""가운데 문서 보기: 쪽 그림(따로 도는 일꾼이 그림)·확대·보기 방식·도구(손·선택·주석·편집·양식).

좌표: 화면 → 보기 회전 뒤 쪽 → 쪽(회전 반영된 fitz page.rect 좌표) → 회전 전 좌표(주석·글자가 쓰는 좌표).
보기 회전(view_rot)은 화면에서만 돈다. 파일의 /Rotate 는 그대로이고 저장·다시 열기에 안 남는다.
"""
from __future__ import annotations

import bisect
import math
import queue
import threading
import time
from dataclasses import dataclass, field

import fitz
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QImage, QKeyEvent, QKeySequence, QMouseEvent, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QTransform, QWheelEvent)
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsScene, QGraphicsView, QInputDialog, QMenu,
                               QStyleOptionGraphicsItem, QWidget)

from ..engine import annot_shapes as SH
from ..engine import annots as A
from ..engine import stamps as ST
from ..engine.document import Document
from . import context_menu as CM
from . import theme

GAP = 12  # 쪽 사이 간격(px)
MARGIN = 20
ZOOM_MIN, ZOOM_MAX = 0.05, 16.0   # 확대 범위 5%~1600%
RENDER_MAX_PX = 48e6   # 쪽 그림 한 장 화소 상한. 넘으면 이 크기로 그려 늘려 보인다(1600% 면 A4 한 장이 1억 화소를 넘는다)
RULER = 18             # 눈금자 두께(px)
ZOOM_FITS = {"창 너비": "fit_width", "창 높이": "fit_height", "창 크기": "fit_page", "실제 크기": "actual"}   # 배율 칸 맞추기 항목


def pct_text(z: float) -> str:
    """배율 글자: 100% · 33.3% (소수 한 자리는 딱 떨어지지 않을 때만)."""
    v = z * 100
    return f"{round(v)}%" if abs(v - round(v)) < 0.05 else f"{v:.1f}%"


# 배경: 문서 뒤 바탕(None 이면 테마 바탕) · 쪽 그림에 곱할 색(None 이면 그대로) · 밝기 뒤집기
BG_MODES = {
    "default": (None, None, False),
    "day": ("#FFFFFF", None, False),
    "eye": ("#B9D3BC", (204, 232, 207), False),
    "night": ("#3A3A3E", (215, 215, 215), True),
    "parchment": ("#D6C9AA", (245, 236, 214), False),
}
UNIT_PT = {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "pt": 1.0, "pc": 12.0}
# 눈금자: 단위마다 (큰 눈금 간격(단위), 작은 눈금 수)
RULER_TICKS = {"cm": (1, 10), "mm": (10, 10), "in": (1, 8), "pt": (72, 6), "pc": (6, 6)}
GRID_DEFAULT = {"w": 72 / 2.54, "h": 72 / 2.54, "left": 0.0, "top": 0.0, "sub": 3, "color": "#FF0000"}   # 간격·오프셋은 pt


def _num_pt(v, unit: str, default: float) -> float:
    """격자 값 하나를 pt 로. 숫자는 pt(환경 설정이 pt 로 저장), 「1cm」 처럼 단위를 붙인 글자도 받는다."""
    if isinstance(v, str):
        t = v.strip().lower()
        u = next((k for k in sorted(UNIT_PT, key=len, reverse=True) if t.endswith(k)), None)
        if u:
            t, unit = t[:-len(u)].strip(), u
        try:
            v = float(t)
        except ValueError:
            v = default
        return float(v) * UNIT_PT.get(unit, UNIT_PT["cm"])
    if not isinstance(v, (int, float)):
        v = default
    return float(v)


def _color(c, default: str = "#FF0000") -> QColor:
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        if all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in c[:3]) and any(isinstance(x, float) for x in c[:3]):
            return QColor.fromRgbF(*c[:3])
        return QColor(*(int(x) for x in c[:3]))
    q = QColor(c) if isinstance(c, str) else QColor()
    return q if q.isValid() else QColor(default)


def grid_spec(prefs: dict | None) -> dict:
    """환경 설정의 unit·grid 를 그리기 값으로: 간격·오프셋은 pt, 세분 수, 색."""
    prefs = prefs or {}
    unit = prefs.get("unit") if prefs.get("unit") in UNIT_PT else "cm"
    g = {**GRID_DEFAULT, **(prefs.get("grid") or {})}
    w = _num_pt(g.get("w"), unit, UNIT_PT["cm"])
    h = _num_pt(g.get("h"), unit, UNIT_PT["cm"])
    try:
        sub = max(1, min(20, int(g.get("sub") or 1)))
    except (TypeError, ValueError):
        sub = 3
    return {"w": w if w > 0.5 else UNIT_PT["cm"], "h": h if h > 0.5 else UNIT_PT["cm"],
            "left": _num_pt(g.get("left"), unit, 0.0), "top": _num_pt(g.get("top"), unit, 0.0),
            "sub": sub, "color": _color(g.get("color"))}


def tint_image(img: QImage, mode: str) -> QImage:
    """배경 모드대로 쪽 그림 색을 바꾼다(밤은 밝기를 뒤집은 뒤 곱하기)."""
    _, tint, invert = BG_MODES.get(mode, BG_MODES["default"])
    if tint is None and not invert:
        return img
    out = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if invert:
        out.invertPixels(QImage.InvertMode.InvertRgb)
    if tint is not None:
        p = QPainter(out)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        p.fillRect(out.rect(), QColor(*tint))
        p.end()
    return out

TOOLS_MARKUP = {"highlight": "Highlight", "underline": "Underline", "strike": "StrikeOut", "squiggly": "Squiggly"}
TOOLS_SHAPE = {"rect", "oval", "line", "arrow", "polygon", "polyline", "cloud", "areahl", "redact", "link", "crop",
               "addtext", "addimage", "textbox", "callout", "typewriter", "stamp", "signature", "snapshot", "ocrarea",
               "ftext", "fcheck", "fradio", "fcombo", "flist", "fbutton", "fimage", "fsign", "fdate",
               "m_distance", "m_perimeter", "m_area", "zoomarea", *SH.SHAPES}
SQUARE_TOOLS = {"rect", "oval", "areahl", *SH.SHAPES}
MEASURE_TOOLS = ("m_distance", "m_perimeter", "m_area")
SNAP_PX = 8   # 측정 스냅이 붙는 거리(화면 px)   # Shift 를 누르고 끌면 가로세로 같게
ERASER_RADIUS = 10   # 지우개 반지름 기본값(화면 px)


def flow_rects(words: list[tuple], a: fitz.Point, b: fitz.Point) -> list[fitz.Rect]:
    """글자 표시 도구: a 에서 b 까지 글 흐름대로 고른 낱말을 줄마다 네모 하나로.

    words 는 get_text("words") 꼴(x0,y0,x1,y1,글,덩어리,줄,낱말). 두 줄에 걸치면 첫 줄은 a 부터 끝까지,
    가운데 줄은 통째로, 끝 줄은 처음부터 b 까지."""
    ws = sorted(words, key=lambda w: (w[5], w[6], w[7]))
    if not ws:
        return []
    lines: list[tuple[int, int, fitz.Rect]] = []   # (처음 낱말 자리, 끝 다음 자리, 줄 네모)
    for k, w in enumerate(ws):
        if lines and (ws[lines[-1][0]][5], ws[lines[-1][0]][6]) == (w[5], w[6]):
            s0, _, r = lines[-1]
            lines[-1] = (s0, k + 1, r | fitz.Rect(w[:4]))
        else:
            lines.append((k, k + 1, fitz.Rect(w[:4])))

    def near(p: fitz.Point) -> int:
        def dist(r: fitz.Rect) -> float:
            dx = max(r.x0 - p.x, 0, p.x - r.x1)
            dy = max(r.y0 - p.y, 0, p.y - r.y1)
            return dx * dx + dy * dy * 4   # 위아래로 벗어난 것을 더 멀게(줄 사이 빈 곳은 가까운 줄로)
        return min(range(len(lines)), key=lambda n: dist(lines[n][2]))

    la, lb = near(a), near(b)
    if (lb, b.x) < (la, a.x):
        a, b, la, lb = b, a, lb, la
    s0, e0, _ = lines[la]
    start = next((k for k in range(s0, e0) if ws[k][2] > a.x), e0)
    s1, e1, _ = lines[lb]
    end = next((k + 1 for k in range(e1 - 1, s1 - 1, -1) if ws[k][0] < b.x), s1)
    out: list[fitz.Rect] = []
    for n in range(la, lb + 1):
        s, e, _ = lines[n]
        pick = ws[max(s, start):min(e, end)]
        if pick:
            r = fitz.Rect(pick[0][:4])
            for w in pick[1:]:
                r |= fitz.Rect(w[:4])
            out.append(r)
    return out


def constrain(tool: str, a: QPointF, b: QPointF) -> QPointF:
    """Shift 로 끌 때 끝점: 네모 꼴은 가로세로 같게, 선·화살표는 45도 단위로."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    if tool in SQUARE_TOOLS:
        m = max(abs(dx), abs(dy))
        return QPointF(a.x() + math.copysign(m, dx or 1), a.y() + math.copysign(m, dy or 1))
    if tool in ("line", "arrow"):
        ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        d = math.hypot(dx, dy)
        return QPointF(a.x() + d * math.cos(ang), a.y() + d * math.sin(ang))
    return b


def _qimage(pix: fitz.Pixmap) -> QImage:
    fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
    return img.copy()  # samples 버퍼와 떼어 둔다


class RenderWorker(QThread):
    done = Signal(int, float, QImage, int)  # page, zoom, image, generation

    def __init__(self, doc: Document):
        super().__init__()
        self.doc = doc
        self.q: "queue.LifoQueue[tuple]" = queue.LifoQueue()
        self._stop = False
        self.gen = 0
        self.annots = True  # 주석 보이기(보기 설정)
        self.rot = 0   # 보기 회전(바꿀 때 invalidate 로 gen 을 올려 옛 각으로 그린 것은 버려진다)
        self.big_image: dict[int, bool] = {}   # 쪽마다: 100만 화소 넘는 그림이 있나(띠로 나누면 덜 멈추는 쪽). 문서가 바뀌면 비움

    def request(self, page: int, zoom: float):
        self.q.put((page, zoom, self.gen))

    def invalidate(self, content: bool = False):
        """content=True: 문서 내용이 바뀌었다(쪽마다 알아 둔 것도 버린다)."""
        self.gen += 1
        if content:
            self.big_image = {}
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def stop(self):
        self._stop = True
        self.q.put((-1, 0, 0))
        self.wait(2000)

    BAND_MS = 16   # 한 쪽이 이보다 오래 걸릴 것 같으면 띠로 나눠 그린다(그동안 화면이 멈추지 않게)
    BAND_EACH = 6  # 띠 하나에 들일 시간(ms)

    def run(self):
        ms_per_mp = None   # 이 문서에서 100만 화소 그리는 데 든 시간(최근 쪽들 평균). 스캔본은 15~20, 글자 PDF 는 2~8
        while not self._stop:
            page, zoom, gen = self.q.get()
            if page < 0 or self._stop:
                break
            if gen != self.gen:
                continue
            rot = self.rot
            try:
                big_image = self.big_image
                with self.doc.lock:
                    pg = self.doc.fitz[page]
                    r = pg.rect
                    if page not in big_image:   # 쪽 자원의 그림 목록만 본다(0.1~1ms)
                        big_image[page] = any(w * h > 1e6 for _, _, w, h, *_ in pg.get_images())
                    del pg
                mp = r.width * r.height * zoom * zoom / 1e6
                est = (ms_per_mp or 0) * mp
                t0 = time.perf_counter()
                # 글자·선만 있는 쪽은 띠마다 다시 그려 오히려 느리다. 큰 그림이 든 쪽만(그림 풀기는 한 번이고 띠마다 제 몫만 그림)
                if est > self.BAND_MS and big_image[page]:
                    pix = self.doc.render_bands(page, zoom, math.ceil(est / self.BAND_EACH), annots=self.annots,
                                                cancel=lambda g=gen: g != self.gen or self._stop)
                    if pix is None:
                        continue
                else:
                    pix = self.doc.render(page, zoom, annots=self.annots)
                ms = (time.perf_counter() - t0) * 1000 / max(mp, 0.05)
                ms_per_mp = ms if ms_per_mp is None else ms_per_mp * 0.6 + ms * 0.4
                img = _qimage(pix)
                if rot:
                    img = img.transformed(QTransform().rotate(rot))
            except Exception:  # noqa: BLE001
                continue
            if gen == self.gen:
                self.done.emit(page, zoom, img, gen)


class PageItem(QGraphicsItem):
    def __init__(self, view: "PageView", index: int):
        super().__init__()
        self.view = view
        self.index = index
        self.pm: QPixmap | None = None
        self.pm_zoom = 0.0
        self.w = self.h = 0.0
        self.setAcceptHoverEvents(False)

    def set_size(self, w: float, h: float):
        self.prepareGeometryChange()
        self.w, self.h = w, h

    def boundingRect(self) -> QRectF:
        # 그림자까지 넣어야 다시 그릴 때 그림자 자리가 지워지지 않는다(쪽 자리는 page_rect)
        return QRectF(-6, -4, self.w + 13, self.h + 14)

    def page_rect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def paint(self, p: QPainter, opt: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        r = self.page_rect()
        # 부드러운 그림자: 바깥으로 갈수록 옅은 테를 겹쳐 종이가 바탕에서 떠 보이게(한 겹 그림자는 밋밋했다)
        for k, a in ((6, 6), (4, 10), (2, 16), (1, 22)):
            p.fillRect(r.adjusted(-k + 1, -k + 3, k + 1, k + 3), QColor(0, 0, 0, a))
        p.fillRect(r, self.view.page_color())
        if self.pm is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.pm_zoom != self.view.zoom)
            p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        else:
            self.view.need_render(self.index)
        if self.view.show_grid:
            self.view.paint_grid(p, self)
        self.view.paint_overlay(p, self.index)


class Ruler(QWidget):
    """보기 위·왼쪽 눈금자(뷰포트 여백 자리에 얹는다). 그리기는 PageView.paint_ruler."""
    def __init__(self, view: "PageView", horizontal: bool):
        super().__init__(view)
        self.view = view
        self.horizontal = horizontal
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self.view.paint_ruler(p, self.horizontal, self.width(), self.height())
        finally:
            p.end()


@dataclass
class Selection:
    """지금 고른 것: 글자(words) · 주석(annot) · 양식 칸(widget) · 글자 덩어리(block) · 그림(image)."""
    kind: str = ""
    page: int = -1
    xref: int = -1
    rect: fitz.Rect | None = None  # 회전 전 좌표
    words: list[tuple] = field(default_factory=list)
    text: str = ""
    extra: dict = field(default_factory=dict)


class PageView(QGraphicsView):
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    docChanged = Signal()
    selectionChanged = Signal(object)  # Selection | None
    status = Signal(str)
    toolDone = Signal(str)  # 도구 하나 쓰고 나서(스탬프 등은 계속 쓰기)
    requestDialog = Signal(str, object)  # 화면에 대화상자를 부탁(종류, 자료)
    toolChanged = Signal(str)  # 속성 패널이 그 도구의 기억해 둔 모양을 얹는다
    shared_prefs: dict | None = None   # 마지막으로 받은 환경 설정(sync_prefs). 새로 여는 문서도 같은 배경·격자로 시작

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setBackgroundBrush(QBrush(QColor(theme.CANVAS)))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self.closed = False
        self.zoom = 1.0
        self.zoom_mode = "fit_width"  # fixed / fit_width / fit_page / fit_height
        self.layout_mode = "continuous"  # single / continuous / facing / facing_continuous
        self.current = 0
        self.items: list[PageItem] = []
        self.tool = "hand"
        self.edit_mode = False      # 텍스트·이미지 편집(원문)
        self.edit_target = "all"    # 편집 대상: all · text · image · link
        self._tool_before_edit = "hand"   # 편집을 끄면 돌아갈 도구
        self._img_hover = None      # 이미지 추가 미리보기: (쪽, 장면 사각형)
        self._img_pm: tuple[str, QPixmap | None] = ("", None)
        self.form_mode = False      # 양식 칸 만들기·편집
        self.read_mode = False
        self.show_annots = True
        self.cover_separate = False   # 두 쪽 보기에서 첫 쪽(표지)만 혼자
        self.bg_mode = "default"      # 배경 모드(BG_MODES)
        self.show_grid = False
        self.show_ruler = False
        self.smooth_scroll = True     # 휠 스크롤을 부드럽게
        self.unit = "cm"              # 눈금자 단위
        self.grid = grid_spec(None)
        self._scroll_anim: QPropertyAnimation | None = None
        self._rulers: tuple = ()
        if PageView.shared_prefs is not None:   # 앞서 연 문서가 받은 환경 설정(새 탭이 처음부터 같은 배경으로 그리게)
            self._read_prefs(PageView.shared_prefs)
        self.view_rot = 0   # 보기 회전 0·90·180·270(시계 방향, 화면에서만. 탭마다 따로)
        self.words_cache: dict[int, list[tuple]] = {}
        self.blocks_cache: dict[int, list[dict]] = {}  # 편집 모드 글자 덩어리(문서가 바뀌면 비움)

        # 도구 속성(오른쪽 패널이 바꿈)
        self.props = {"stroke": (1, 0, 0), "fill": None, "width": 1.5, "opacity": 1.0,
                      "hl_color": (1, 1, 0), "text_color": (0, 0, 0), "fontsize": 11, "ink_color": (0.1, 0.1, 0.9),
                      "ink_width": 2.0, "author": "", "redact_fill": (0, 0, 0),
                      "measure_unit": "cm", "measure_scale": 1.0, "field_type": "text", "image_path": "",
                      "link_target": {}, "eraser_radius": ERASER_RADIUS,
                      "stamp_spec": None,   # 찍을 스탬프·서명(engine/stamps.py 의 spec)
                      "measure": None}      # 측정 축척·스냅·라벨(settings.json 의 measure)

        self.sel: Selection | None = None
        self._drag_start: QPointF | None = None  # 화면(뷰포트) 좌표
        self._drag_page = -1
        self._drag_rect: QRectF | None = None    # 장면 좌표
        self._ink: list[QPointF] = []
        self._poly: list[QPointF] = []           # 다각형·연결선·측정 진행 중(장면 좌표)
        self._poly_page = -1
        self._snap_cache: dict[int, list] = {}   # 측정 스냅 점(쪽마다, 문서가 바뀌면 비움)
        self._snap_mark: QPointF | None = None   # 지금 붙은 스냅 점(장면 좌표)
        self._panning = False
        self._pan_last = QPointF()
        self._pan_start = QPointF()
        self._moving_annot = False
        self._move_last: fitz.Point | None = None
        self._resize_corner = -1   # 고른 링크의 모서리를 끄는 중(0 왼쪽 위 · 1 오른쪽 위 · 2 왼쪽 아래 · 3 오른쪽 아래)
        self._hover_link = ""      # 손 도구로 올린 링크 안내(같은 것을 거듭 띄우지 않는다)
        self._erase_last: tuple[int, fitz.Point] | None = None  # 지우개가 지난 자리(쪽, 쪽 좌표)
        self._hover_block: fitz.Rect | None = None
        self.search_hits: list[tuple[int, list[fitz.Rect]]] = []  # (쪽, 줄마다 네모)
        self.search_current = -1
        self._back: list[tuple[int, float, int]] = []   # 이전 보기 자리(view_pos)
        self._fwd: list[tuple[int, float, int]] = []
        self._space_tool = ""   # Space 를 누르는 동안 손 도구로 바꾸기 전의 도구
        self._rects: list[fitz.Rect] = []   # 쪽 크기(rebuild 때 한 번 읽는다)
        self._tops: list[float] = []        # 보이는 쪽의 위 끝(장면 y, 차례대로). 보이는 쪽 찾기용
        self._shown: list[int] = []         # 그 쪽 번호
        self._held: set[int] = set()        # 그림을 들고 있는 쪽

        self.worker = RenderWorker(doc)
        self.worker.annots = self.show_annots
        self.worker.done.connect(self._rendered)
        self.worker.start()
        self._pending: set[int] = set()
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._apply_fit)
        self.rebuild()

    @property
    def show_annots(self) -> bool:
        return self._show_annots

    @show_annots.setter
    def show_annots(self, on: bool):
        self._show_annots = on
        if hasattr(self, "worker"):
            self.worker.annots = on

    # ── 보기 설정(배경·격자·눈금자·부드러운 스크롤) ──────────────────
    def _read_prefs(self, prefs: dict):
        self.bg_mode = prefs.get("view_bg") if prefs.get("view_bg") in BG_MODES else "default"
        self.smooth_scroll = bool(prefs.get("smooth_scroll", True))
        self.unit = prefs.get("unit") if prefs.get("unit") in UNIT_PT else "cm"
        self.grid = grid_spec(prefs)

    def sync_prefs(self, prefs: dict):
        """환경 설정이 바뀌었으면 얹는다(배경이 바뀌면 쪽을 다시 그림). 바뀐 것이 없으면 아무 일도 안 한다."""
        PageView.shared_prefs = prefs
        old = (self.bg_mode, self.smooth_scroll, self.unit, self.grid)
        self._read_prefs(prefs)
        if old == (self.bg_mode, self.smooth_scroll, self.unit, self.grid):
            return
        if old[0] != self.bg_mode:
            self._repaint_pages()
        self.viewport().update()

    def set_bg_mode(self, mode: str):
        if mode not in BG_MODES or mode == self.bg_mode:
            return
        self.bg_mode = mode
        self._repaint_pages()

    def _repaint_pages(self):
        """쪽 그림을 버리고 다시 그린다(문서는 그대로라 docChanged 는 안 낸다)."""
        self.worker.invalidate()
        self._pending.clear()
        for it in self.items:
            it.pm = None
            it.update()
        self.viewport().update()
        QTimer.singleShot(0, self._request_visible)

    def page_color(self) -> QColor:
        """그림이 오기 전 쪽 자리 색(흰 종이에 배경 모드를 얹은 색)."""
        _, tint, invert = BG_MODES.get(self.bg_mode, BG_MODES["default"])
        base = 0 if invert else 255
        if tint is None:
            return QColor(base, base, base)
        return QColor(*(base * c // 255 for c in tint))

    def drawBackground(self, p: QPainter, rect: QRectF):
        canvas = BG_MODES.get(self.bg_mode, BG_MODES["default"])[0]
        if canvas is None:   # 기본은 테마 바탕(창이 setBackgroundBrush 로 정함)
            super().drawBackground(p, rect)
        else:
            p.fillRect(rect, QColor(canvas))

    def set_grid(self, on: bool):
        self.show_grid = bool(on)
        self.viewport().update()

    def set_ruler(self, on: bool):
        self.show_ruler = bool(on)
        if on and not self._rulers:
            self._rulers = (Ruler(self, True), Ruler(self, False))
        self.setViewportMargins(RULER if on else 0, RULER if on else 0, 0, 0)
        for r in self._rulers:
            r.setVisible(self.show_ruler)
        self._place_rulers()
        self._update_rulers()

    def paint_grid(self, p: QPainter, it: "PageItem"):
        """쪽 위 격자(쪽 왼쪽 위에서 오프셋만큼 떨어져 간격마다, 사이는 세분한 옅은 선)."""
        g = self.grid
        z = self.zoom
        r = it.page_rect()
        col = QColor(g["color"])
        sub = QColor(col); sub.setAlpha(60)
        col.setAlpha(150)
        p.save()
        p.setClipRect(r)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for step, off, horizontal in ((g["w"], g["left"], False), (g["h"], g["top"], True)):
            minor = step / g["sub"]
            if minor * z < 3:   # 너무 촘촘하면 세분 선은 건너뛴다
                minor = step
            length = r.height() if not horizontal else r.width()
            end = (r.width() if not horizontal else r.height()) / z
            k = math.floor(-off / minor)
            while True:
                v = off + k * minor
                if v > end:
                    break
                if v >= 0:
                    major = abs((v - off) / step - round((v - off) / step)) < 1e-6
                    p.setPen(QPen(col if major else sub, 0))
                    x = v * z
                    if horizontal:
                        p.drawLine(QPointF(0, x), QPointF(length, x))
                    else:
                        p.drawLine(QPointF(x, 0), QPointF(x, length))
                k += 1
        p.restore()

    def _place_rulers(self):
        if not self._rulers:
            return
        top, left = self._rulers
        vp = self.viewport().geometry()
        top.setGeometry(vp.left() - RULER, vp.top() - RULER, vp.width() + RULER, RULER)
        left.setGeometry(vp.left() - RULER, vp.top(), RULER, vp.height())
        top.raise_(); left.raise_()

    def _update_rulers(self):
        for r in self._rulers:
            r.update()

    def paint_ruler(self, p: QPainter, horizontal: bool, w: int, h: int):
        """눈금자 하나(지금 쪽 왼쪽 위가 0, 환경 설정 단위). 위 눈금자는 왼쪽 RULER px 가 모서리."""
        p.fillRect(0, 0, w, h, QColor("#F3F3F3"))
        p.setPen(QPen(QColor("#B0B0B0"), 0))
        if horizontal:
            p.drawLine(0, h - 1, w, h - 1)
        else:
            p.drawLine(w - 1, 0, w - 1, h)
        if not self.items:
            return
        i = max(0, min(self.current, len(self.items) - 1))
        origin = self.mapFromScene(self.items[i].pos())
        unit_pt = UNIT_PT.get(self.unit, UNIT_PT["cm"])
        major_u, minor_n = RULER_TICKS.get(self.unit, (1, 10))
        px_unit = unit_pt * self.zoom
        mult = 1
        while major_u * mult * px_unit < 40:   # 숫자가 겹치지 않게 큰 눈금을 1·2·5·10… 배로 넓힌다
            mult = mult * 5 // 2 if str(mult)[0] == "2" else mult * 2
        major = major_u * mult
        minor = major / (minor_n if mult == 1 else 5 if str(mult)[0] in "15" else 4)
        f = QFont(self.font()); f.setPixelSize(9); p.setFont(f)
        p.setPen(QPen(QColor("#505050"), 0))
        shift = RULER if horizontal else 0
        o = origin.x() if horizontal else origin.y()
        length = (w - shift) if horizontal else h
        step_px = minor * px_unit
        k = math.floor(-o / step_px)
        while True:
            pos = o + k * step_px
            if pos > length:
                break
            if pos >= 0:
                v = k * minor
                is_major = abs(v / major - round(v / major)) < 1e-6
                tick = 8 if is_major else 4
                if horizontal:
                    x = shift + pos
                    p.drawLine(QPointF(x, h - tick), QPointF(x, h - 1))
                    if is_major:
                        p.drawText(QPointF(x + 2, 9), f"{round(v):g}")
                else:
                    p.drawLine(QPointF(w - tick, pos), QPointF(w - 1, pos))
                    if is_major:
                        p.save(); p.translate(3, pos + 2); p.rotate(90)
                        p.drawText(QPointF(0, 0), f"{round(v):g}"); p.restore()
            k += 1
        if horizontal:
            p.fillRect(0, 0, RULER - 1, h - 1, QColor("#F3F3F3"))

    # ── 배치 ──────────────────────────────────────────────────
    def close(self):
        # 닫은 뒤에도 창 맞춤 타이머·singleShot 이 돌아 닫힌 문서를 읽다 「document closed」 가 났다
        self.closed = True
        self._fit_timer.stop()
        self.worker.stop()

    def _disp_rect(self, i: int) -> fitz.Rect:
        """쪽 크기(파일 회전 반영, 보기 회전 전). 확대할 때마다 쪽마다 fitz 에 물으면 959쪽 문서에서 한 번에 15ms 가 들어 rebuild 때 읽어 둔다."""
        if i < len(self._rects):
            return self._rects[i]
        return self.doc.fitz[i].rect

    def page_rect_pt(self, i: int) -> fitz.Rect:
        """화면에 보이는 쪽 크기(파일 회전·보기 회전 반영)."""
        r = self._disp_rect(i)
        return fitz.Rect(0, 0, r.height, r.width) if self.view_rot in (90, 270) else r

    def view_matrix(self, i: int) -> fitz.Matrix:
        """쪽(파일 회전 반영) 좌표 → 보기 회전 뒤 좌표. 시계 방향, 돌린 쪽의 왼쪽 위가 원점."""
        r = self._disp_rect(i)
        w, h = r.width, r.height
        return {90: fitz.Matrix(0, 1, -1, 0, h, 0), 180: fitz.Matrix(-1, 0, 0, -1, w, h),
                270: fitz.Matrix(0, -1, 1, 0, 0, w)}.get(self.view_rot, fitz.Matrix(1, 0, 0, 1, 0, 0))

    def set_view_rotation(self, deg: int):
        """보기 회전을 deg(90 의 배수)로. 보던 쪽을 그대로 보여 준다. 파일은 안 바뀐다."""
        deg = int(deg) % 360 // 90 * 90
        if deg == self.view_rot or self.closed:
            return
        i = self.current
        self.view_rot = deg
        self.worker.rot = deg
        self.worker.invalidate()
        self._pending.clear()
        for it in self.items:
            it.pm = None
        self._held = set()
        self.relayout()
        self._apply_fit()
        if self.items:
            self.goto(i, remember=False)
        self.viewport().update()

    def rotate_view(self, delta: int):
        self.set_view_rotation(self.view_rot + delta)

    def text_rotation(self, i: int) -> int:
        """새 글이 지금 화면에 바로 서는 방향(쪽 회전 + 보기 회전)."""
        return (self.doc.fitz[i].rotation + self.view_rot) % 360

    def rebuild(self, mapping: list[int | None] | None = None, dirty=()):
        """쪽 수·크기가 바뀌었을 때 전부 다시 놓는다. mapping[새 쪽] = 옛 쪽 이면 그 그림을 옮겨 쓴다
        (dirty 에 든 새 쪽은 다시 그림). 회전·삭제·순서 바꾸기에서 안 바뀐 쪽까지 다시 그리지 않게."""
        old = {it.index: (it.pm, it.pm_zoom) for it in self.items if it.pm is not None} if mapping is not None else {}
        self.scene_.clear()
        self.items = []
        self.words_cache.clear(); self.blocks_cache.clear()
        n = self.doc.page_count
        with self.doc.lock:
            self._rects = [fitz.Rect(self.doc.fitz[i].rect) for i in range(n)]
        self._held = set()
        for i in range(n):
            it = PageItem(self, i)
            if mapping is not None and i < len(mapping) and i not in dirty and mapping[i] in old:
                it.pm, it.pm_zoom = old[mapping[i]]
                self._held.add(i)
            self.scene_.addItem(it)
            self.items.append(it)
        self.worker.invalidate(content=True)
        self._pending.clear()  # 버린 부탁이 남아 있으면 그 쪽을 다시 부탁하지 못해 흰 쪽으로 남는다
        self.relayout()
        self.current = min(self.current, n - 1)

    def visible_pages_for_layout(self) -> list[int]:
        n = self.doc.page_count
        if self.layout_mode == "single":
            return [self.current]
        if self.layout_mode == "facing":
            a = self.row_start(self.current)
            return [i for i in ((a,) if self.cover_separate and a == 0 else (a, a + 1)) if i < n]
        return list(range(n))

    def row_start(self, i: int) -> int:
        """두 쪽 보기에서 i 쪽이 든 줄의 첫 쪽(표지 따로면 0쪽 혼자, 그다음부터 둘씩)."""
        if not self.cover_separate:
            return i - i % 2
        return 0 if i <= 0 else i - (i - 1) % 2

    def relayout(self):
        z = self.zoom
        y = MARGIN
        maxw = 0
        pages = self.visible_pages_for_layout()
        on = set(pages)   # 목록에 「in」 을 쓰면 959쪽에서 쪽 수의 제곱만큼 돈다
        for it in self.items:
            it.setVisible(it.index in on)
        facing = self.layout_mode.startswith("facing")
        if not facing:
            for i in pages:
                it = self.items[i]
                r = self.page_rect_pt(i)
                it.set_size(r.width * z, r.height * z)
                it.setPos(0, y)
                maxw = max(maxw, r.width * z)
                y += r.height * z + GAP
            for i in pages:  # 가운데 맞춤
                it = self.items[i]
                it.setPos((maxw - it.w) / 2 + MARGIN, it.pos().y())
            self.scene_.setSceneRect(0, 0, maxw + 2 * MARGIN, y + MARGIN)
        else:
            if self.cover_separate and pages and pages[0] == 0:
                rest = pages[1:]
                rows = [[0]] + [rest[k:k + 2] for k in range(0, len(rest), 2)]
            else:
                rows = [pages[k:k + 2] for k in range(0, len(pages), 2)]
            for row in rows:
                x = MARGIN
                if row == [0] and self.cover_separate:   # 표지는 오른쪽 칸(책을 펼친 모양)
                    x += self.page_rect_pt(0).width * z + GAP
                rh = 0
                for i in row:
                    it = self.items[i]
                    r = self.page_rect_pt(i)
                    it.set_size(r.width * z, r.height * z)
                    it.setPos(x, y)
                    x += r.width * z + GAP
                    rh = max(rh, r.height * z)
                maxw = max(maxw, x - GAP + MARGIN)
                y += rh + GAP
            self.scene_.setSceneRect(0, 0, maxw, y + MARGIN)
        # 보이는 쪽 찾기용: 위 끝이 차례대로 늘어나는 목록(두 쪽 보기는 한 줄 두 쪽이 같은 값). 옛 그림은 늘려 보여 주다가 새 그림이 오면 바꾼다
        self._shown = list(pages)
        self._tops = [self.items[i].pos().y() for i in pages]
        self.viewport().update()
        self._update_rulers()
        QTimer.singleShot(0, self._request_visible)

    def set_layout_mode(self, mode: str):
        self.layout_mode = mode
        self.relayout()
        self.goto(self.current)

    def set_cover_separate(self, on: bool):
        self.cover_separate = bool(on)
        self.relayout()
        self.goto(self.current, remember=False)

    # ── 확대 ──────────────────────────────────────────────────
    def set_zoom(self, z: float, anchor: QPointF | None = None):
        z = max(ZOOM_MIN, min(ZOOM_MAX, z))
        if abs(z - self.zoom) < 1e-4:
            return
        old = self.zoom
        # 닻: 뷰포트 가운데(또는 마우스) 가 가리키던 장면 점을 유지
        vp = anchor if anchor is not None else QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        scene_pt = self.mapToScene(vp.toPoint())
        self.zoom = z
        self.worker.invalidate()
        self._pending.clear()
        self.relayout()
        ratio = z / old
        new_pt = QPointF(scene_pt.x() * ratio, scene_pt.y() * ratio)
        self.centerOn(new_pt + (QPointF(self.viewport().width() / 2, self.viewport().height() / 2) - vp))
        self.zoomChanged.emit(z)

    # 확대·축소 단추가 건너는 배율(단계표에서 5~1600% 만). Ctrl+휠은 1.1배씩 부드럽게
    STEPS = (0.05, 0.10, 0.15, 0.25, 1 / 3, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 3.00, 4.00, 6.00, 8.00, 12.00, 16.00)

    def zoom_in(self):
        self.zoom_mode = "fixed"
        self.set_zoom(next((s for s in self.STEPS if s > self.zoom * 1.001), self.STEPS[-1]))

    def zoom_out(self):
        self.zoom_mode = "fixed"
        self.set_zoom(next((s for s in reversed(self.STEPS) if s < self.zoom * 0.999), self.STEPS[0]))

    def zoom_to_rect(self, r: QRectF, click: bool = False, at: QPointF | None = None):
        """영역 확대: 끈 네모가 창에 차게. 한 번 누르면 그 자리를 닻으로 한 칸 확대."""
        self.zoom_mode = "fixed"
        if click or r.width() < 4 or r.height() < 4:
            nxt = next((s for s in self.STEPS if s > self.zoom * 1.001), self.STEPS[-1])
            self.set_zoom(nxt, QPointF(self.mapFromScene(at)) if at is not None else None)
            return
        vw, vh = self.viewport().width() - 8, self.viewport().height() - 8
        z = self.zoom * min(vw / r.width(), vh / r.height())
        c = r.center()
        old = self.zoom
        self.set_zoom(z)
        ratio = self.zoom / old
        self.centerOn(QPointF(c.x() * ratio, c.y() * ratio))

    def set_zoom_mode(self, mode: str):
        self.zoom_mode = mode
        self._apply_fit()

    def _apply_fit(self):
        if self.closed or self.zoom_mode == "fixed" or not self.items:
            return
        i = max(0, min(self.current, len(self.items) - 1))
        r = self.page_rect_pt(i)
        vw = self.viewport().width() - 2 * MARGIN - 4
        vh = self.viewport().height() - 2 * MARGIN - 4
        if self.layout_mode.startswith("facing"):
            vw = (vw - GAP) / 2
        if self.zoom_mode == "fit_width":
            z = vw / r.width
        elif self.zoom_mode == "fit_height":
            z = vh / r.height
        elif self.zoom_mode == "actual":
            z = self.logicalDpiX() / 72.0
        else:
            z = min(vw / r.width, vh / r.height)
        self.set_zoom(z)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_rulers()
        self._fit_timer.start(80)

    # ── 그리기 요청 ────────────────────────────────────────────
    def need_render(self, i: int):
        if i in self._pending:
            return
        self._pending.add(i)
        self.worker.request(i, self.render_zoom(i))

    def render_zoom(self, i: int) -> float:
        """i 쪽을 그릴 배율. 그림이 RENDER_MAX_PX 를 넘으면 줄여 그리고 화면에서 늘린다."""
        r = self.page_rect_pt(i)
        area = max(r.width * r.height, 1.0)
        return min(self.zoom, math.sqrt(RENDER_MAX_PX / area))

    def _request_visible(self):
        if self.closed:
            return
        vis = self.visible_pages()
        for i in vis:
            it = self.items[i]
            if it.pm is None or abs(it.pm_zoom - self.render_zoom(i)) > 1e-6:
                self.need_render(i)
        self._evict(vis)

    KEEP_NEAR = 3              # 보이는 쪽 앞뒤로 그림을 남겨 둘 쪽 수
    KEEP_BYTES = 300 * 2**20   # 남겨 둔 그림이 이보다 많으면 먼 쪽부터 버린다

    def _evict(self, vis: list[int]):
        """화면에서 먼 쪽 그림을 버린다. 안 버리면 끝까지 훑을 때 쪽 수만큼 쌓였다(126쪽 1GB, 10-20 측정).
        다시 보이면 paint 가 그리기를 부탁한다."""
        if not vis:
            return
        lo, hi = vis[0], vis[-1]
        held = []
        for i in list(self._held):   # 그림을 든 쪽만 본다(모든 쪽을 훑지 않게)
            it = self.items[i] if i < len(self.items) else None
            if it is None or it.pm is None:
                self._held.discard(i)
                continue
            d = lo - it.index if it.index < lo else it.index - hi if it.index > hi else 0
            if d > self.KEEP_NEAR:
                it.pm = None
            else:
                held.append((d, it))
        total = sum(it.pm.width() * it.pm.height() * 4 for _, it in held)
        for d, it in sorted(held, key=lambda x: -x[0]):
            if total <= self.KEEP_BYTES or d == 0:
                break
            total -= it.pm.width() * it.pm.height() * 4
            it.pm = None

    def _rendered(self, page: int, zoom: float, img: QImage, gen: int):
        self._pending.discard(page)
        if gen != self.worker.gen or page >= len(self.items):
            return
        it = self.items[page]
        it.pm = QPixmap.fromImage(tint_image(img, self.bg_mode))
        it.pm_zoom = zoom
        self._held.add(page)
        it.update()

    def refresh_pages(self, pages: list[int] | None = None, structural: bool = False,
                      mapping: list[int | None] | None = None):
        """문서가 바뀐 뒤 다시 그린다. structural=True 면 쪽 수·크기도 바뀐 것(mapping 은 rebuild 를 볼 것)."""
        self.words_cache.clear(); self.blocks_cache.clear(); self._snap_cache.clear()
        if structural:
            self.rebuild(mapping, set(pages or ()))
        else:
            self.worker.invalidate(content=True)
            self._pending.clear()
            for it in self.items:
                if pages is None or it.index in pages:
                    it.pm = None
                    it.update()
        self.docChanged.emit()
        QTimer.singleShot(0, self._request_visible)

    def _candidates(self, vr: QRectF) -> list[int]:
        """화면 높이 vr 에 걸칠 수 있는 쪽만(위 끝 목록을 반으로 나눠 찾는다). 스크롤할 때마다 모든 쪽을 훑으면 959쪽에서 몇 ms 씩 들었다."""
        if len(self._tops) != len(self._shown):
            return [it.index for it in self.items if it.isVisible()]
        # 쪽은 위아래로 겹치지 않으니 화면 위 끝보다 위에서 시작한 쪽은 마지막 줄(두 쪽 보기면 둘)만 걸칠 수 있다
        lo = max(0, bisect.bisect_right(self._tops, vr.top()) - 2)
        hi = bisect.bisect_right(self._tops, vr.bottom())
        return self._shown[lo:hi]

    def visible_pages(self) -> list[int]:
        vr = self.mapToScene(self.viewport().rect()).boundingRect()
        out = []
        for i in self._candidates(vr):
            it = self.items[i]
            if it.isVisible() and it.sceneBoundingRect().intersects(vr):
                out.append(i)
        return out

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._update_rulers()
        self._update_current()
        QTimer.singleShot(0, self._request_visible)

    def _update_current(self):
        vr = self.mapToScene(self.viewport().rect()).boundingRect()
        best, best_area = self.current, -1.0
        mid = vr.center().y()
        for i in self._candidates(vr):
            it = self.items[i]
            if not it.isVisible():
                continue
            r = it.mapRectToScene(it.page_rect())
            if r.top() <= mid <= r.bottom():
                best = it.index
                break
            inter = r.intersected(vr)
            a = inter.width() * inter.height()
            if a > best_area:
                best_area, best = a, it.index
        if best != self.current:
            self.current = best
            self.pageChanged.emit(best)

    # ── 이동 ──────────────────────────────────────────────────
    HISTORY = 50  # 이전 보기로 돌아갈 수 있는 자리 수

    def goto(self, i: int, y_pt: float | None = None, remember: bool = True):
        """i 쪽으로(y_pt 를 주면 그 높이가 위에서 조금 아래에 오게). 다른 쪽으로 건너뛰면 떠나는 자리를 「이전 보기」 에 남긴다."""
        if self.closed:
            return
        i = max(0, min(i, self.doc.page_count - 1))
        if remember and i != self.current and self.items:
            self._push_history(self._back)
            self._fwd.clear()
        if self.layout_mode in ("single", "facing") and i not in self.visible_pages_for_layout():
            self.current = i
            self.relayout()
        it = self.items[i]
        y = it.pos().y() - 8
        if y_pt is not None:
            y = it.pos().y() + y_pt * self.zoom - 40
        self.verticalScrollBar().setValue(int(y))
        if self.current != i:
            self.current = i
            self.pageChanged.emit(i)
        QTimer.singleShot(0, self._request_visible)

    def next_page(self):
        if self.layout_mode.startswith("facing"):
            a = self.row_start(self.current)
            self.goto(a + (1 if self.cover_separate and a == 0 else 2), remember=False)
        else:
            self.goto(self.current + 1, remember=False)

    def prev_page(self):
        if self.layout_mode.startswith("facing"):
            a = self.row_start(self.current)
            self.goto(self.row_start(a - 1) if a > 0 else 0, remember=False)
        else:
            self.goto(self.current - 1, remember=False)

    def view_pos(self) -> tuple[int, float, int]:
        """지금 보는 자리: (쪽, 화면 맨 위가 그 쪽 위에서 몇 pt 아래인가, 가로 스크롤 값)."""
        if not self.items:
            return 0, 0.0, 0
        i = max(0, min(self.current, len(self.items) - 1))
        y = (self.verticalScrollBar().value() - self.items[i].pos().y()) / (self.zoom or 1)
        return i, round(y, 1), self.horizontalScrollBar().value()

    def set_view_pos(self, i: int, y_pt: float, x: int = 0):
        """view_pos 로 잰 자리로 그대로 돌아간다(이전 보기·다시 열 때)."""
        if self.closed or not self.items:
            return
        i = max(0, min(int(i), len(self.items) - 1))
        if self.layout_mode in ("single", "facing") and i not in self.visible_pages_for_layout():
            self.current = i
            self.relayout()
        self.verticalScrollBar().setValue(int(self.items[i].pos().y() + y_pt * self.zoom))
        self.horizontalScrollBar().setValue(int(x))
        if self.current != i:
            self.current = i
            self.pageChanged.emit(i)
        QTimer.singleShot(0, self._request_visible)

    def _push_history(self, stack: list):
        stack.append(self.view_pos())
        del stack[:-self.HISTORY]

    def back(self):
        """이전 보기(Alt+←): 건너뛰기 전에 보던 자리로."""
        if self._back:
            self._push_history(self._fwd)
            self.set_view_pos(*self._back.pop())

    def forward(self):
        if self._fwd:
            self._push_history(self._back)
            self.set_view_pos(*self._fwd.pop())

    # ── 좌표 ──────────────────────────────────────────────────
    def page_at(self, scene_pt: QPointF) -> int:
        for i in self._candidates(QRectF(scene_pt.x(), scene_pt.y(), 1, 1)):
            it = self.items[i]
            if it.isVisible() and it.mapRectToScene(it.page_rect()).contains(scene_pt):
                return it.index
        return -1

    def to_page(self, i: int, scene_pt: QPointF) -> fitz.Point:
        """장면 좌표 → 회전 전 쪽 좌표."""
        it = self.items[i]
        lp = scene_pt - it.pos()
        p = fitz.Point(lp.x() / self.zoom, lp.y() / self.zoom)
        if self.view_rot:
            p = p * ~self.view_matrix(i)
        return p * self.doc.fitz[i].derotation_matrix

    def from_page_rect(self, i: int, r: fitz.Rect) -> QRectF:
        """회전 전 쪽 좌표 → 장면 좌표 QRectF."""
        it = self.items[i]
        rr = fitz.Rect(r) * self.doc.fitz[i].rotation_matrix
        if self.view_rot:
            rr = rr * self.view_matrix(i)
        rr.normalize()
        return QRectF(it.pos().x() + rr.x0 * self.zoom, it.pos().y() + rr.y0 * self.zoom,
                      rr.width * self.zoom, rr.height * self.zoom)

    def from_page_point(self, i: int, p: fitz.Point) -> QPointF:
        it = self.items[i]
        q = fitz.Point(p) * self.doc.fitz[i].rotation_matrix
        if self.view_rot:
            q = q * self.view_matrix(i)
        return QPointF(it.pos().x() + q.x * self.zoom, it.pos().y() + q.y * self.zoom)

    def _scene_rect_to_page(self, i: int, r: QRectF) -> fitz.Rect:
        a = self.to_page(i, r.topLeft())
        b = self.to_page(i, r.bottomRight())
        out = fitz.Rect(a, b)
        out.normalize()
        return out

    def words(self, i: int) -> list[tuple]:
        if i not in self.words_cache:
            self.words_cache[i] = self.doc.words(i)
        return self.words_cache[i]

    def blocks(self, i: int) -> list[dict]:
        """편집 모드에서 마우스가 움직일 때마다·다시 그릴 때마다 쓰여 쪽마다 한 번만 읽는다."""
        if i not in self.blocks_cache:
            self.blocks_cache[i] = self.doc.text_blocks(i)
        return self.blocks_cache[i]

    # ── 도구 ──────────────────────────────────────────────────
    def set_tool(self, tool: str):
        self.tool = tool
        self._img_hover = None
        self._poly.clear()
        self._ink.clear()
        self._drag_rect = None
        self._space_tool = ""
        self._apply_cursor()
        self.viewport().update()

    def _apply_cursor(self):
        tool = self.tool
        if tool == "eraser":
            self.viewport().setCursor(self._eraser_cursor())
            self.viewport().update()
            self.toolChanged.emit(tool)
            return
        cur = {"hand": Qt.CursorShape.OpenHandCursor, "select": Qt.CursorShape.IBeamCursor,
               "addtext": Qt.CursorShape.IBeamCursor}.get(tool)
        if cur is None:
            cur = Qt.CursorShape.CrossCursor if tool in TOOLS_SHAPE or tool in TOOLS_MARKUP or tool in (
                "pencil", "eraser", "note", "caret", "attach") else Qt.CursorShape.ArrowCursor
        self.viewport().setCursor(QCursor(cur))
        self.viewport().update()
        self.toolChanged.emit(tool)

    def _eraser_cursor(self) -> QCursor:
        """지우개 마우스 모양: 지우는 크기 그대로의 원."""
        r = int(self.props.get("eraser_radius", ERASER_RADIUS))
        n = 2 * r + 3
        pm = QPixmap(n, n); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 220), 3)); p.drawEllipse(QPointF(n / 2, n / 2), r, r)
        p.setPen(QPen(QColor(40, 40, 40), 1)); p.drawEllipse(QPointF(n / 2, n / 2), r, r)
        p.end()
        return QCursor(pm, n // 2, n // 2)

    def set_eraser_radius(self, r: int):
        self.props["eraser_radius"] = int(r)
        if self.tool == "eraser":
            self.viewport().setCursor(self._eraser_cursor())

    def _eraser_r(self) -> float:
        return self.props.get("eraser_radius", ERASER_RADIUS) / self.zoom

    def set_edit_mode(self, on: bool, target: str | None = None):
        if target:
            self.edit_target = target
        if on:
            self._remember_tool_before_edit()
            self.edit_mode = True
            self.form_mode = False
            self.set_tool("edit")
        else:
            self.edit_mode = False
            self.set_tool(self._tool_before_edit)
        self._hover_block = None
        self.clear_selection()

    def _remember_tool_before_edit(self):
        if not self.edit_mode:
            self._tool_before_edit = self.tool if self.tool not in ("edit", "form") else "hand"

    def begin_edit_tool(self, kinds: tuple[str, ...]):
        """텍스트·이미지 추가가 편집을 켠다. 지금 대상이 kinds 에 없으면 「모두」로 맞춘다."""
        self._remember_tool_before_edit()
        self.edit_mode = True
        self.form_mode = False
        if self.edit_target not in kinds:
            self.edit_target = "all"
        self.clear_selection()

    def _edit_blocks(self, i: int) -> list[dict]:
        """편집 대상에 맞는 덩어리만: 텍스트는 글 덩어리, 이미지는 그림 자리, 링크는 없음."""
        want = {"text": (0,), "image": (1,), "all": (0, 1)}.get(self.edit_target, ())
        return [b for b in self.blocks(i) if b.get("type") in want]

    def set_form_mode(self, on: bool):
        self.form_mode = on
        if on:
            self.edit_mode = False
            self.set_tool("form")
        else:
            self.set_tool("hand")
        self.clear_selection()

    def clear_selection(self):
        if self.sel is not None:
            self.sel = None
            self.selectionChanged.emit(None)
            self.viewport().update()

    # ── 마우스 ────────────────────────────────────────────────
    def wheelEvent(self, e: QWheelEvent):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_mode = "fixed"
            f = 1.1 if e.angleDelta().y() > 0 else 1 / 1.1
            self.set_zoom(self.zoom * f, QPointF(e.position()))
            e.accept()
            return
        if self.layout_mode in ("single", "facing"):
            sb = self.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum()
            at_top = sb.value() <= sb.minimum()
            if e.angleDelta().y() < 0 and at_bottom:
                self.next_page(); e.accept(); return
            if e.angleDelta().y() > 0 and at_top:
                self.prev_page(); e.accept(); return
        dy = e.angleDelta().y()
        if (self.smooth_scroll and dy and e.pixelDelta().isNull() and not e.angleDelta().x()
                and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._smooth_scroll(-dy / 120 * QApplication.wheelScrollLines() * self.verticalScrollBar().singleStep())
            e.accept()
            return
        super().wheelEvent(e)

    def _smooth_scroll(self, dy: float):
        """휠 한 칸을 짧은 애니메이션으로. 도는 중에 또 굴리면 남은 거리에 이어 붙인다."""
        sb = self.verticalScrollBar()
        a = self._scroll_anim
        start = sb.value()
        running = a is not None and a.state() == QPropertyAnimation.State.Running
        target = (a.endValue() if running else start) + dy
        target = int(max(sb.minimum(), min(sb.maximum(), target)))
        if a is None:
            a = self._scroll_anim = QPropertyAnimation(sb, b"value", self)
            a.setDuration(140)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.stop()
        a.setStartValue(start)
        a.setEndValue(target)
        a.start()

    def mousePressEvent(self, e: QMouseEvent):
        sp = self.mapToScene(e.position().toPoint())
        i = self.page_at(sp)
        if e.button() == Qt.MouseButton.MiddleButton or (self.tool == "hand" and e.button() == Qt.MouseButton.LeftButton):
            self._panning = True
            self._pan_last = e.position()
            self._pan_start = e.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            if self.tool == "hand" and i >= 0:
                self._try_select_annot(i, sp, e)
            return
        if e.button() == Qt.MouseButton.RightButton:
            self._context_menu(e, i, sp)
            return
        if e.button() != Qt.MouseButton.LeftButton or i < 0:
            return
        self._drag_start = e.position()
        self._drag_page = i
        t = self.tool
        if t in ("select", "edit", "form"):
            if self._try_select_annot(i, sp, e):
                return
            if t == "edit" and self._links_editable() and self._press_link(i, sp):
                return
            if t == "edit":
                if self._press_content(i, sp) or self._select_block_or_image(i, sp):
                    return
            self._drag_start_scene = sp
            self._drag_end_scene = sp
            if t == "form":
                self._drag_rect = QRectF(sp, sp)
                return
            self._drag_rect = QRectF(sp, sp)
            self.clear_selection()
        elif self._links_editable() and self._press_link(i, sp):
            return
        elif t == "textbox":
            # 텍스트 상자는 끌면 그린 크기로, 한 번 누르면 누른 자리에(놓을 때 정한다)
            self._drag_rect = QRectF(sp, sp)
            self._drag_start_scene = sp
            self._drag_end_scene = sp
            return
        elif t in ("callout", "typewriter"):
            # 누른 자리에서 바로 쓰는 글 주석(말풍선은 누른 자리가 지시선 끝)
            self.requestDialog.emit("textbox", {"page": i, "scene_pt": sp, "kind": t})
            return
        elif t in TOOLS_MARKUP or t in TOOLS_SHAPE:
            sp = self._snap_scene(i, sp)
            if t in ("polygon", "polyline", "cloud", "m_perimeter", "m_area"):
                if self._poly and self._poly_page != i:
                    self._poly.clear()
                self._poly.append(sp)
                self._poly_page = i
                self.viewport().update()
                return
            self._drag_rect = QRectF(sp, sp)
            self._drag_start_scene = sp
            self._drag_end_scene = sp
        elif t == "pencil":
            self._ink = [sp]
        elif t == "eraser":
            pt = self.to_page(i, sp)
            self._erase_last = (i, pt)
            if A.erase_ink_at(self.doc, i, pt, radius=self._eraser_r()):
                self.refresh_pages([i])
        elif t in ("note", "caret", "attach"):
            pt = self.to_page(i, sp)
            self.requestDialog.emit(t, {"page": i, "point": pt})
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._panning:
            d = e.position() - self._pan_last
            self._pan_last = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        sp = self.mapToScene(e.position().toPoint())
        if self._moving_annot and self.sel and self.sel.kind in ("annot", "widget", "link", "block", "image"):
            i = self.sel.page
            pt = self.to_page(i, sp)
            if self._resize_corner >= 0:
                r = fitz.Rect(self.sel.rect)
                if self._resize_corner in (0, 2):
                    r.x0 = pt.x
                else:
                    r.x1 = pt.x
                if self._resize_corner in (0, 1):
                    r.y0 = pt.y
                else:
                    r.y1 = pt.y
                self.sel.rect = r
                self.viewport().update()
                return
            if self._move_last is not None:
                dx, dy = pt.x - self._move_last.x, pt.y - self._move_last.y
                self.sel.rect = self.sel.rect + (dx, dy, dx, dy)
                self.viewport().update()
            self._move_last = pt
            return
        if self.tool in MEASURE_TOOLS:
            had = self._snap_mark
            sp = self._snap_scene(self.page_at(sp), sp)
            if had != self._snap_mark:
                self.viewport().update()
        if self._drag_rect is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._drag_start_scene is not None and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                sp = constrain(self.tool, self._drag_start_scene, sp)
            self._drag_rect = QRectF(self._drag_rect.topLeft(), sp).normalized() if self._drag_start_scene is None                 else QRectF(self._drag_start_scene, sp).normalized()
            self._drag_end_scene = sp
            self.viewport().update()
            return
        if self.tool == "pencil" and self._ink and e.buttons() & Qt.MouseButton.LeftButton:
            self._ink.append(sp)
            self.viewport().update()
            return
        if self.tool == "eraser" and e.buttons() & Qt.MouseButton.LeftButton:
            i = self.page_at(sp)
            if i >= 0:
                # 지난 자리부터 지금 자리까지 지나간 길 전체로 지운다(빠르게 끌면 움직임 사이가 듬성듬성하다)
                pt = self.to_page(i, sp)
                prev = self._erase_last[1] if self._erase_last and self._erase_last[0] == i else None
                self._erase_last = (i, pt)
                if A.erase_ink_at(self.doc, i, pt, radius=self._eraser_r(), prev=prev):
                    self.refresh_pages([i])
            return
        if self._poly:
            self.viewport().update()
        if self.edit_mode and self.tool == "edit":
            i = self.page_at(sp)
            hb = None
            if i >= 0:
                pt = self.to_page(i, sp)
                for b in self._edit_blocks(i):
                    if fitz.Rect(b["bbox"]).contains(pt):
                        hb = (i, fitz.Rect(b["bbox"]))
                        break
            if hb != self._hover_block:
                self._hover_block = hb
                self.viewport().update()
        if self.tool == "addimage":
            i = self.page_at(sp)
            hv = (i, self._image_scene_rect(i, sp)) if i >= 0 else None
            if hv is None or hv[1] is None:
                hv = None
            if hv != self._img_hover:
                self._img_hover = hv
                self.viewport().update()
        # 손 도구에서 링크 위면 손가락
        if self.tool == "hand":
            i = self.page_at(sp)
            x = self._link_at(i, self.to_page(i, sp)) if i >= 0 else 0
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if x else Qt.CursorShape.OpenHandCursor)
            msg = self.link_hover_text(A.link_info(self.doc, i, x)) if x else ""
            if msg != self._hover_link:
                self._hover_link = msg
                self.status.emit(msg)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._panning:
            self._panning = False
            if self.tool == "hand":
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
                sp = self.mapToScene(e.position().toPoint())
                # 끌어서 넘기다 링크 위에서 놓은 것은 누른 것이 아니다
                if (e.position() - self._pan_start).manhattanLength() < 4:
                    self._follow_link(sp)
            return
        if self._moving_annot:
            self._moving_annot = False
            if self.sel and self.sel.rect is not None:
                self._commit_move()
            self._resize_corner = -1
            return
        sp = self.mapToScene(e.position().toPoint())
        i = self._drag_page
        t = self.tool
        if self._drag_rect is not None and i >= 0:
            r = self._drag_rect
            self._drag_rect = None
            small = r.width() < 3 and r.height() < 3
            pr = self._scene_rect_to_page(i, r)
            if t in ("select", "edit"):
                self._select_words(i, pr, click=small, scene_pt=sp)
            elif t == "form":
                if not small:
                    self._make_field(i, pr)
            elif t in TOOLS_MARKUP:
                pt = self.to_page(i, sp)
                if small:
                    # 한 번 눌렀으면 그 낱말 하나
                    rects = [fitz.Rect(w[:4]) for w in self.words(i) if fitz.Rect(w[:4]).contains(pt)][:1]
                else:
                    # 글 흐름대로: 누른 자리부터 놓은 자리까지(두 줄이면 첫 줄 끝까지 + 둘째 줄 처음부터)
                    rects = flow_rects(self.words(i), self.to_page(i, self._drag_start_scene or r.topLeft()), pt)
                if rects:
                    # 줄 네모의 위아래를 줄여 넘긴다(줄 사이가 좁으면 옆 줄 낱말까지 집혔다)
                    bands = [fitz.Rect(q.x0, q.y0 + q.height * 0.3, q.x1, q.y1 - q.height * 0.3) for q in rects]
                    A.text_markup(self.doc, i, bands, TOOLS_MARKUP[t], color=self.props["hl_color"],
                                  opacity=self.props["opacity"], author=self.props["author"])
                    self.refresh_pages([i])
                    self.toolDone.emit(t)
            elif t == "addtext":
                # 누른 자리에 바로 치는 글 상자. 끌어 그렸으면 그 왼쪽 위에서 그린 너비로
                self.requestDialog.emit("addtext", {"page": i, "scene_pt": r.topLeft(),
                                                    "width": None if small else r.width() / self.zoom})
            elif t == "textbox":
                self.requestDialog.emit("textbox", {"page": i, "kind": t, "scene_pt": self._drag_start_scene or sp}
                                        if small else {"page": i, "kind": t, "scene_pt": r.topLeft(), "scene_rect": QRectF(r)})
            elif t == "addimage" and small:
                # 한 번 누르면 미리보기 자리(원래 크기, 쪽보다 크면 줄임)에 바로 넣는다
                ir = self._image_scene_rect(i, sp)
                if ir is not None:
                    self.requestDialog.emit("addimage", {"page": i, "rect": self._scene_rect_to_page(i, ir)})
            elif t in ("stamp", "signature") and small:
                # 한 번 누르면 기억한 크기로 누른 자리에
                spec = self.props.get("stamp_spec")
                if spec:
                    try:
                        pr = ST.default_rect(self.doc, i, self.to_page(i, sp), spec,
                                             author=A.user_author(self.props["author"]))
                    except ValueError as ex:
                        self.status.emit(str(ex))
                    else:
                        self._shape_done(i, pr, r, click=True)
            elif t == "zoomarea":
                self.zoom_to_rect(r, small, sp)
            elif not small:
                self._shape_done(i, pr, r)
            self.viewport().update()
        elif t == "pencil" and len(self._ink) > 1 and i >= 0:
            pts = [self.to_page(i, p) for p in self._ink]
            self._ink = []
            A.ink(self.doc, i, [pts], stroke=self.props["ink_color"], width=self.props["ink_width"],
                  opacity=self.props["opacity"], author=self.props["author"])
            self.refresh_pages([i])
            self.toolDone.emit(t)
        self._drag_start = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        sp = self.mapToScene(e.position().toPoint())
        i = self.page_at(sp)
        if i < 0:
            return
        if self._poly and self.tool in ("polygon", "polyline", "cloud", "m_perimeter", "m_area"):
            self._finish_poly()
            return
        if self.sel and self.sel.kind == "annot" and self.sel.rect and self.sel.rect.contains(self.to_page(i, sp)):
            # 글상자는 그 자리에서 고치고, 노트는 팝업을 연다
            k = self.sel.extra.get("kind")
            self.requestDialog.emit({"FreeText": "textbox_edit", "Text": "note_open"}.get(k, "annot_props"), self.sel)
            return
        if self.sel and self.sel.kind == "widget":
            self.requestDialog.emit("field_props", self.sel)
            return
        if self.sel and self.sel.kind == "link" and self.sel.rect and self.sel.rect.contains(self.to_page(i, sp)):
            self.requestDialog.emit("edit_link", self.sel)
            return
        if self.edit_mode and self.sel and self.sel.kind == "block":
            self.requestDialog.emit("edit_block", self.sel)
            return
        if self.tool in ("hand", "select"):
            # 양식 칸 값 넣기(읽기 모드에서도)
            pt = self.to_page(i, sp)
            for w in self.doc.fitz[i].widgets():
                if fitz.Rect(w.rect).contains(pt):
                    self.requestDialog.emit("fill_field", {"page": i, "xref": w.xref, "widget": w})
                    return
            # 낱말 하나 고르기
            for w in self.words(i):
                if fitz.Rect(w[:4]).contains(pt):
                    self._select_words(i, fitz.Rect(w[:4]), click=False, scene_pt=sp)
                    return

    def keyPressEvent(self, e: QKeyEvent):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self._poly.clear(); self._drag_rect = None; self.clear_selection(); self.viewport().update()
            if self.tool not in ("hand", "select", "edit", "form"):
                self.toolDone.emit("escape")
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._poly:
            self._finish_poly(); return
        if k == Qt.Key.Key_Delete and self.sel:
            self.delete_selection(); return
        if k in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if CM.focus_step(self, k == Qt.Key.Key_Tab and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                return
            super().keyPressEvent(e)
            return
        if k == Qt.Key.Key_Space and not e.isAutoRepeat() and self.tool != "hand" and not self._space_tool:
            # 누르는 동안만 손 도구. 떼면 하던 도구로
            self._space_tool = self.tool
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor); self.tool = "hand"
            return
        if k == Qt.Key.Key_Space:
            return
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if k in (Qt.Key.Key_Home,):
            self.goto(0); return
        if k in (Qt.Key.Key_End,):
            self.goto(self.doc.page_count - 1); return
        # 다음 쪽: PageDown · Ctrl+↓ · Enter (한 쪽·두 쪽 보기는 → 도), 이전 쪽: PageUp · Ctrl+↑ · Shift+Enter (←)
        enter = k in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        # ← → 는 가로로 넘길 것이 없으면(한 쪽·두 쪽 보기, 또는 가로 막대가 없을 때) 쪽 넘기기
        flip = self.layout_mode in ("single", "facing") or self.horizontalScrollBar().maximum() <= 0
        if (k == Qt.Key.Key_PageDown or (ctrl and k == Qt.Key.Key_Down) or (enter and not shift)
                or (k == Qt.Key.Key_Right and flip)):
            self.next_page(); return
        if (k == Qt.Key.Key_PageUp or (ctrl and k == Qt.Key.Key_Up) or (enter and shift)
                or (k == Qt.Key.Key_Left and flip)):
            self.prev_page(); return
        if ctrl and k == Qt.Key.Key_Plus and e.modifiers() & Qt.KeyboardModifier.KeypadModifier:
            self.zoom_in(); return   # 숫자 키판 + (큰 키판 Ctrl++ 는 Ctrl+Shift+= 오른쪽 회전이라 단축키 표에 없다)
        if e.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection(); return
        if e.matches(QKeySequence.StandardKey.SelectAll) and self.tool in ("select", "hand"):
            i = self.current
            ws = self.words(i)
            if ws:
                r = fitz.Rect(ws[0][:4])
                for w in ws:
                    r |= fitz.Rect(w[:4])
                self._select_words(i, r, click=False, scene_pt=None)
            return
        super().keyPressEvent(e)

    def focusNextPrevChild(self, nxt: bool) -> bool:
        """Tab · Shift+Tab 은 Qt 가 초점 이동으로 먼저 가져가므로 여기서 문서 안 대상 고르기부터 한다."""
        return CM.focus_step(self, nxt) or super().focusNextPrevChild(nxt)

    def keyReleaseEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat() and self._space_tool:
            tool, self._space_tool = self._space_tool, ""
            self._panning = False
            self.tool = tool; self._apply_cursor()   # set_tool 은 그리던 다각형을 지운다
            return
        super().keyReleaseEvent(e)

    # ── 고르기 ────────────────────────────────────────────────
    def _try_select_annot(self, i: int, sp: QPointF, e: QMouseEvent) -> bool:
        pt = self.to_page(i, sp)
        if self.show_annots and not self.form_mode and not self.edit_mode and self.tool in ("select", "hand"):
            for a in self.doc.fitz[i].annots():
                if a.type[1] in ("Link", "Widget", "Popup"):
                    continue
                if fitz.Rect(a.rect).contains(pt):
                    self.sel = Selection("annot", i, a.xref, fitz.Rect(a.rect), text=a.info.get("content", ""),
                                         extra={"kind": a.type[1], "kind_ko": A.KIND_NAMES.get(a.type[1], a.type[1]),
                                                "colors": A.annot_colors(self.doc, a), "opacity": a.opacity,
                                                "width": (a.border or {}).get("width", 1)})
                    self.selectionChanged.emit(self.sel)
                    if self.tool == "select" and not A.is_locked(self.doc, i, a.xref):   # 잠긴 주석은 끌어도 그대로
                        self._moving_annot = True
                        self._move_last = pt
                    self.viewport().update()
                    return True
        if self.form_mode:
            for w in self.doc.fitz[i].widgets():
                if fitz.Rect(w.rect).contains(pt):
                    self.sel = Selection("widget", i, w.xref, fitz.Rect(w.rect), text=w.field_name,
                                         extra={"type": w.field_type_string, "value": w.field_value})
                    self.selectionChanged.emit(self.sel)
                    self._moving_annot = True
                    self._move_last = pt
                    self.viewport().update()
                    return True
        return False

    def _select_block_or_image(self, i: int, sp: QPointF) -> bool:
        pt = self.to_page(i, sp)
        for b in self._edit_blocks(i):
            if fitz.Rect(b["bbox"]).contains(pt):
                if b.get("type") == 0:
                    text = "\n".join("".join(s["text"] for s in l["spans"]) for l in b["lines"])
                    span = b["lines"][0]["spans"][0]
                    self.sel = Selection("block", i, -1, fitz.Rect(b["bbox"]), text=text,
                                         extra={"fontsize": span["size"], "font": span["font"],
                                                "bold": bool(span["flags"] & 16)})
                else:
                    self.sel = Selection("image", i, -1, fitz.Rect(b["bbox"]), extra={"xref": 0})
                    for im in A.images_on_page(self.doc, i):
                        if im["rect"].intersects(fitz.Rect(b["bbox"])):
                            self.sel.extra["xref"] = im["xref"]
                            break
                self.sel.extra["rect"] = fitz.Rect(self.sel.rect)
                self.selectionChanged.emit(self.sel)
                self._moving_annot = True
                self._resize_corner = -1
                self._move_last = pt
                self.viewport().update()
                return True
        return False

    def _press_content(self, i: int, sp: QPointF) -> bool:
        """고른 글자 덩어리·그림의 모서리 손잡이를 누르면 크기 바꾸기, 몸통을 누르면 옮기기."""
        s = self.sel
        if not (s and s.kind in ("block", "image") and s.page == i and s.rect is not None):
            return False
        pt = self.to_page(i, sp)
        tol = 5 / self.zoom
        for n, c in enumerate((s.rect.tl, s.rect.tr, s.rect.bl, s.rect.br)):
            if abs(c.x - pt.x) <= tol and abs(c.y - pt.y) <= tol:
                self._resize_corner = n
                self._moving_annot = True
                return True
        if not s.rect.contains(pt):
            return False
        self._moving_annot = True
        self._resize_corner = -1
        self._move_last = pt
        return True

    def place_content(self, new: fitz.Rect, **image_kw) -> bool:
        """고른 글자 덩어리·그림을 new 자리(회전 전 쪽 좌표)로. 그림은 돌리기·뒤집기·불투명도도 같이 받는다."""
        s = self.sel
        if not s or s.kind not in ("block", "image"):
            return False
        old = fitz.Rect(s.extra.get("rect") or s.rect)
        new = fitz.Rect(new); new.normalize()
        if new.width < 2 or new.height < 2:
            s.rect = old   # 너무 작게 줄인 것은 되돌린다
            self.viewport().update()
            return False
        same = all(abs(a - b) < 0.01 for a, b in zip(old, new))
        if s.kind == "block":
            if same:
                return False
            st = s.extra.get("style") or A.block_style(self.doc, s.page, old)
            A.edit_block(self.doc, s.page, old, s.text, st["fontsize"], st["color"], st["bold"], st.get("align", 0),
                         italic=st["italic"], family=st["family"], new_rect=new)
            s.extra["style"] = st
        else:
            if same and not image_kw:
                return False
            try:
                new = A.transform_image(self.doc, s.page, s.extra.get("xref", 0), old,
                                        new_rect=None if same else new, **image_kw)
            except ValueError as e:
                s.rect = old
                self.status.emit(str(e))
                self.viewport().update()
                return False
        s.rect = fitz.Rect(new)
        s.extra["rect"] = fitz.Rect(new)
        self.refresh_pages([s.page])
        self.selectionChanged.emit(s)
        return True

    def _select_words(self, i: int, pr: fitz.Rect, click: bool, scene_pt: QPointF | None):
        ws = [w for w in self.words(i) if fitz.Rect(w[:4]).intersects(pr)] if not click else []
        if not ws:
            self.clear_selection()
            return
        ws.sort(key=lambda w: (w[5], w[6], w[7]))
        lines: list[str] = []
        cur_line, cur_key = [], None
        for w in ws:
            key = (w[5], w[6])
            if key != cur_key and cur_line:
                lines.append(" ".join(cur_line)); cur_line = []
            cur_key = key
            cur_line.append(w[4])
        if cur_line:
            lines.append(" ".join(cur_line))
        r = fitz.Rect(ws[0][:4])
        for w in ws:
            r |= fitz.Rect(w[:4])
        self.sel = Selection("words", i, -1, r, words=ws, text="\n".join(lines))
        self.selectionChanged.emit(self.sel)
        self.viewport().update()

    def selected_text(self) -> str:
        return self.sel.text if self.sel and self.sel.kind == "words" else ""

    def copy_selection(self):
        t = self.selected_text()
        if t:
            QApplication.clipboard().setText(t)
            self.status.emit("글자를 복사했습니다")

    def delete_selection(self):
        s = self.sel
        if not s:
            return
        if s.kind == "annot":
            A.delete(self.doc, s.page, s.xref)
        elif s.kind == "widget":
            A.delete_field(self.doc, s.page, s.xref)
        elif s.kind == "link":
            A.delete_link(self.doc, s.page, s.xref)
        elif s.kind == "block":
            A.edit_block(self.doc, s.page, s.rect, "")
        elif s.kind == "image":
            A.delete_image(self.doc, s.page, s.rect)
        else:
            return
        self.clear_selection()
        self.refresh_pages([s.page])

    def _commit_move(self):
        s = self.sel
        if not s or s.rect is None:
            return
        if s.kind == "annot":
            a = A.get(self.doc, s.page, s.xref)
            if a is not None:
                old = fitz.Rect(a.rect)
                if abs(old.x0 - s.rect.x0) > 0.01 or abs(old.y0 - s.rect.y0) > 0.01:
                    new = A.update(self.doc, s.page, s.xref, rect=s.rect)
                    if new:
                        s.xref = new  # 선·연필·다각형은 옮기면 새로 만들어진다
                    self.refresh_pages([s.page])
        elif s.kind == "link":
            s.rect.normalize()
            old = s.extra.get("rect")
            if s.rect.width < 2 or s.rect.height < 2:
                s.rect = fitz.Rect(old)   # 너무 작게 줄인 것은 되돌린다
            elif old is None or any(abs(a - b) > 0.01 for a, b in zip(old, s.rect)):
                s.xref = A.update_link(self.doc, s.page, s.xref, rect=s.rect) or s.xref
                s.extra["rect"] = fitz.Rect(s.rect)
                self.refresh_pages([s.page])
            self.viewport().update()
        elif s.kind == "widget":
            self.doc.snapshot()
            for w in self.doc.fitz[s.page].widgets():
                if w.xref == s.xref:
                    w.rect = s.rect
                    A.save_widget(w)
            self.refresh_pages([s.page])
        elif s.kind in ("block", "image"):
            self.place_content(s.rect)
        self._move_last = None

    def _follow_link(self, sp: QPointF):
        i = self.page_at(sp)
        if i < 0:
            return
        x = self._link_at(i, self.to_page(i, sp))
        info = A.link_info(self.doc, i, x) if x else None
        if not info:
            return
        if info["kind"] == "goto":
            self.goto_link(info)
        elif info["kind"] == "uri":
            self.requestDialog.emit("open_uri", info["uri"])
        elif info["kind"] == "file":
            self.requestDialog.emit("open_file", A.resolve_link_file(info["file"], self.doc.path))
        else:
            self.status.emit("링크가 잘못되었습니다.")

    def goto_link(self, info: dict):
        """쪽 이동 링크를 따라간다: 담아 둔 배율(0 이면 보던 배율 그대로)로 바꾸고 왼쪽 위 자리를 화면 왼쪽 위에."""
        if self.closed or not self.items:
            return
        self._push_history(self._back)
        self._fwd.clear()
        if info["zoom"] and abs(info["zoom"] - self.zoom) > 0.001:
            self.zoom_mode = "fixed"
            self.set_zoom(info["zoom"])
        i = max(0, min(info["to_page"], len(self.items) - 1))
        p = info["to_point"] or fitz.Point(0, 0)
        q = p * self.view_matrix(i) if self.view_rot else p
        x = int(self.items[i].pos().x() + q.x * self.zoom) if q.x > 0 else 0
        self.set_view_pos(i, max(0.0, q.y), x)

    # ── 링크 고르기·옮기기 ─────────────────────────────────────
    def _links_editable(self) -> bool:
        """만든 링크를 고르고 옮기는 상태: 링크 도구, 또는 편집 대상이 링크인 편집 모드."""
        return self.show_annots and (self.tool == "link" or (
            self.edit_mode and self.tool == "edit" and getattr(self, "edit_target", "") == "link"))

    def _link_at(self, i: int, pt: fitz.Point) -> int:
        """그 자리의 링크 xref(겹치면 나중에 넣은 것). 없으면 0."""
        if i < 0:
            return 0
        found = 0
        for l in self.doc.fitz[i].get_links():
            if l.get("xref") and fitz.Rect(l["from"]).contains(pt):
                found = l["xref"]
        return found

    def _press_link(self, i: int, sp: QPointF) -> bool:
        pt = self.to_page(i, sp)
        s = self.sel
        if s and s.kind == "link" and s.page == i:
            # 고른 링크의 모서리 손잡이를 누르면 크기 바꾸기
            tol = 5 / self.zoom
            for n, c in enumerate((s.rect.tl, s.rect.tr, s.rect.bl, s.rect.br)):
                if abs(c.x - pt.x) <= tol and abs(c.y - pt.y) <= tol:
                    self._resize_corner = n
                    self._moving_annot = True
                    return True
        x = self._link_at(i, pt)
        info = A.link_info(self.doc, i, x) if x else None
        if info is None:
            if s and s.kind == "link":
                self.clear_selection()
            return False
        self.sel = Selection("link", i, x, fitz.Rect(info["rect"]), text=self.link_hover_text(info),
                             extra={"kind": "Link", "kind_ko": "링크", "rect": fitz.Rect(info["rect"])})
        self.selectionChanged.emit(self.sel)
        self._moving_annot = True
        self._resize_corner = -1
        self._move_last = pt
        self.viewport().update()
        return True

    def link_dest_here(self) -> tuple[fitz.Point, float]:
        """쪽 이동 링크에 담을 지금 보기: (보는 쪽의 왼쪽 위 자리, 배율). 맞춤 보기면 배율 0(여는 쪽의 배율 그대로)."""
        i, y, x = self.view_pos()
        left = max(0.0, (x - self.items[i].pos().x()) / (self.zoom or 1)) if self.items else 0.0
        zoom = round(self.zoom, 4) if self.zoom_mode == "fixed" else 0.0
        p = fitz.Point(left, y)
        if self.view_rot and self.items:   # 돌린 화면의 왼쪽 위 → 쪽(파일 회전 반영) 좌표
            r = self._disp_rect(i)
            p = p * ~self.view_matrix(i)
            p = fitz.Point(min(max(p.x, 0.0), r.width), min(max(p.y, 0.0), r.height))
        return fitz.Point(round(max(0.0, p.x), 1), max(0.0, p.y)), zoom

    @staticmethod
    def link_hover_text(info: dict | None) -> str:
        if not info or info["kind"] == "bad":
            return "링크가 잘못되었습니다."
        if info["kind"] == "goto":
            return f"페이지로 이동: {info['to_page'] + 1}"
        if info["kind"] == "uri":
            return f"링크 열기: {info['uri']}"
        return f"파일 열기: {info['file']}"

    # ── 도형 끝 ───────────────────────────────────────────────
    def _snap_scene(self, i: int, sp: QPointF) -> QPointF:
        """측정 도구: 쪽 그림의 끝점·중점이 가까우면(화면 SNAP_PX) 그 점으로 붙인다."""
        self._snap_mark = None
        if self.tool not in MEASURE_TOOLS or i < 0:
            return sp
        snap = A.measure_ratio(self.props.get("measure"))["snap"]
        if not any(snap.values()):
            return sp
        pts = self._snap_cache.get(i)
        if pts is None:
            with self.doc.lock:
                pts = self._snap_cache[i] = A.snap_points(self.doc.fitz[i])
        q = A.nearest_snap(pts, self.to_page(i, sp), SNAP_PX / max(self.zoom, 0.01), snap)
        if q is None:
            return sp
        self._snap_mark = self.from_page_point(i, q)
        return self._snap_mark

    def _measure_done(self, i: int, pts: list[fitz.Point], kind: str):
        from ..engine import annot_style as AS
        P = self.props
        st = AS.store().get("m_" + kind)
        m = A.measure_ratio(P.get("measure"), P.get("measure_unit", "cm"), P.get("measure_scale", 1.0))
        value = A.measure(pts, kind, ratio=m)
        label = f"{m['label']} {value}".strip()
        A.measure_annot(self.doc, i, pts, kind, label, st["stroke"], A.user_author(P["author"]),
                        width=st["width"], opacity=st["opacity"], ratio=m)
        name = {"distance": "거리", "perimeter": "둘레", "area": "영역"}[kind]
        self.status.emit(f"{name} {value} (축척 {A.ratio_text(m)})")

    def _shape_done(self, i: int, pr: fitz.Rect, scene_r: QRectF, click: bool = False):
        t = self.tool
        P = self.props
        d = self.doc
        a, b = self.to_page(i, scene_r.topLeft()), self.to_page(i, scene_r.bottomRight())
        if t == "rect":
            A.rect(d, i, pr, P["stroke"], P["fill"], P["width"], P["opacity"], P["author"])
        elif t == "oval":
            A.oval(d, i, pr, P["stroke"], P["fill"], P["width"], P["opacity"], P["author"])
        elif t in ("line", "arrow", "m_distance"):
            # 실제 끌기 방향대로 선을 긋는다
            s0 = self.to_page(i, self._drag_start_scene or scene_r.topLeft())
            s1 = self.to_page(i, self._drag_end_scene or scene_r.bottomRight())
            if t == "m_distance":
                self._measure_done(i, [s0, s1], "distance")
            else:
                A.line(d, i, s0, s1, P["stroke"], P["width"], arrow=(t == "arrow"), opacity=P["opacity"],
                       author=P["author"], ends=P.get("line_ends"))
        elif t in SH.SHAPES:
            A.polygon(d, i, SH.shape_points(t, pr), P["stroke"], P["fill"], P["width"], closed=True,
                      opacity=P["opacity"], author=P["author"])
        elif t == "areahl":
            A.area_highlight(d, i, pr, P["hl_color"], P["opacity"], P["author"])
        elif t == "redact":
            A.mark_redact(d, i, pr, P["redact_fill"])
        elif t == "textbox":
            self.requestDialog.emit("textbox", {"page": i, "rect": pr, "kind": "textbox"}); return
        elif t == "callout":
            self.requestDialog.emit("textbox", {"page": i, "rect": pr, "kind": "callout"}); return
        elif t == "typewriter":
            self.requestDialog.emit("textbox", {"page": i, "rect": pr, "kind": "typewriter"}); return
        elif t == "addimage":
            self.requestDialog.emit("addimage", {"page": i, "rect": pr}); return
        elif t in ("stamp", "signature"):
            spec = P.get("stamp_spec")
            if not spec:
                return
            author = A.user_author(P["author"])
            try:
                if not click:   # 끌어서 찍은 크기를 다음에 한 번 눌러 찍을 크기로
                    ST.remember_drag(d, i, pr, spec, author=author)
                ST.add(d, i, pr, spec, author=author)
            except ValueError as ex:
                self.status.emit(str(ex)); return
        elif t == "link":
            self.requestDialog.emit("link", {"page": i, "rect": pr}); return
        elif t == "crop":
            self.requestDialog.emit("crop", {"page": i, "rect": pr}); return
        elif t == "snapshot":
            self.snapshot_to_clipboard(i, pr); return
        elif t == "ocrarea":
            self.requestDialog.emit("ocrarea", {"page": i, "rect": pr}); return
        elif t.startswith("f"):
            self._make_field(i, pr); return
        else:
            return
        self.refresh_pages([i])
        self.toolDone.emit(t)

    _drag_start_scene: QPointF | None = None
    _drag_end_scene: QPointF | None = None

    def _make_field(self, i: int, pr: fitz.Rect):
        t = self.tool if self.tool.startswith("f") and self.tool != "form" else "f" + self.props["field_type"]
        kind = {"ftext": "text", "fcheck": "checkbox", "fradio": "radio", "fcombo": "combo", "flist": "list",
                "fbutton": "button", "fimage": "image", "fsign": "signature", "fdate": "text"}.get(t, "text")
        if kind in ("checkbox", "radio"):
            s = max(10, min(pr.width, pr.height))
            pr = fitz.Rect(pr.x0, pr.y0, pr.x0 + s, pr.y0 + s)
        if pr.height < 8:
            pr.y1 = pr.y0 + 18
        A.add_field(self.doc, i, pr, kind, date=(t == "fdate"), label="버튼" if kind == "button" else "")
        self.refresh_pages([i])
        self.toolDone.emit(t)

    def _finish_poly(self):
        if len(self._poly) < 2:
            self._poly.clear(); return
        i = self._poly_page
        pts = [self.to_page(i, p) for p in self._poly]
        self._poly.clear()
        P = self.props
        t = self.tool
        if t in ("polygon", "cloud"):
            if len(pts) >= 3:
                A.polygon(self.doc, i, pts, P["stroke"], P["fill"], P["width"], closed=True, cloud=(t == "cloud"),
                          opacity=P["opacity"], author=P["author"])
        elif t == "polyline":
            A.polygon(self.doc, i, pts, P["stroke"], None, P["width"], closed=False, opacity=P["opacity"],
                      author=P["author"], ends=P.get("line_ends"))
        elif t == "m_perimeter":
            self._measure_done(i, pts, "perimeter")
        elif t == "m_area" and len(pts) >= 3:
            self._measure_done(i, pts, "area")
        self.refresh_pages([i])
        self.toolDone.emit(t)

    def snapshot_to_clipboard(self, i: int, pr: fitz.Rect):
        pix = self.doc.render(i, max(2.0, self.zoom), clip=pr)
        img = _qimage(pix)
        if self.view_rot:
            img = img.transformed(QTransform().rotate(self.view_rot))
        QApplication.clipboard().setImage(img)
        self.status.emit("화면을 클립보드에 복사했습니다")
        self.toolDone.emit("snapshot")

    # ── 오른쪽 클릭 ───────────────────────────────────────────
    def _context_menu(self, e: QMouseEvent, i: int, sp: QPointF):
        CM.build(self, i, sp).exec(e.globalPosition().toPoint())

    def _markup_sel(self, kind: str):
        if self.sel and self.sel.kind == "words":
            A.text_markup(self.doc, self.sel.page, [fitz.Rect(w[:4]) for w in self.sel.words], kind,
                          color=self.props["hl_color"], author=self.props["author"])
            pg = self.sel.page
            self.clear_selection()
            self.refresh_pages([pg])

    def _redact_sel(self):
        if self.sel and self.sel.kind == "words":
            pg = self.sel.page
            for w in self.sel.words:
                A.mark_redact(self.doc, pg, fitz.Rect(w[:4]), self.props["redact_fill"])
            self.clear_selection()
            self.refresh_pages([pg])

    # ── 덧그리기 ──────────────────────────────────────────────
    def paint_overlay(self, p: QPainter, i: int):
        """PageItem 안에서 부른다(아이템 좌표 = 장면 좌표 - pos)."""
        it = self.items[i]
        off = -it.pos()
        p.save()
        p.translate(off)
        accent = QColor(theme.ACCENT)
        # 검색 결과
        for (pg, rs) in self.search_hits:
            if pg == i:
                for r in (rs if isinstance(rs, list) else [rs]):
                    p.fillRect(self.from_page_rect(i, r), QColor(255, 200, 0, 90))
        if 0 <= self.search_current < len(self.search_hits) and self.search_hits[self.search_current][0] == i:
            rs = self.search_hits[self.search_current][1]
            p.setPen(QPen(QColor(230, 120, 0), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for r in (rs if isinstance(rs, list) else [rs]):
                p.drawRect(self.from_page_rect(i, r))
        # 편집 모드: 글자 덩어리 상자
        if self.edit_mode:
            p.setPen(QPen(QColor(120, 120, 220, 160), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for b in self._edit_blocks(i):
                p.drawRect(self.from_page_rect(i, fitz.Rect(b["bbox"])))
            if self._hover_block and self._hover_block[0] == i:
                p.setPen(QPen(accent, 1.5))
                p.drawRect(self.from_page_rect(i, self._hover_block[1]))
        if self.tool == "addimage" and self._img_hover and self._img_hover[0] == i:
            pm = self.image_pixmap()
            if pm is not None:
                p.save()
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.setOpacity(0.6)
                p.drawPixmap(self._img_hover[1], pm, QRectF(pm.rect()))
                p.restore()
                p.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(self._img_hover[1])
        # 링크: MuPDF 는 링크 테두리를 그리지 않아 여기서 그린다. 고를 수 있을 때는 안 보이는 링크 자리도 점선으로
        if self.show_annots:
            self._paint_links(p, i)
        # 양식 모드: 칸 상자
        if self.form_mode:
            p.setPen(QPen(QColor(60, 140, 240, 200), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(60, 140, 240, 25))
            for w in self.doc.fitz[i].widgets():
                p.drawRect(self.from_page_rect(i, fitz.Rect(w.rect)))
        # 고른 것
        s = self.sel
        if s and s.page == i:
            if s.kind == "words":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(50, 120, 240, 80))
                for w in s.words:
                    p.drawRect(self.from_page_rect(i, fitz.Rect(w[:4])))
            elif s.rect is not None:
                rr = self.from_page_rect(i, s.rect)
                p.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rr)
                p.setBrush(QBrush(Qt.GlobalColor.white))
                p.setPen(QPen(accent, 1))
                for c in (rr.topLeft(), rr.topRight(), rr.bottomLeft(), rr.bottomRight()):
                    p.drawRect(QRectF(c.x() - 3, c.y() - 3, 6, 6))
        # 진행 중 끌기
        if self._drag_rect is not None and self._drag_page == i:
            r = self._drag_rect
            t = self.tool
            if t in ("select", "edit", "form") or t in TOOLS_MARKUP:
                p.setPen(QPen(QColor(50, 120, 240), 1, Qt.PenStyle.DashLine))
                p.setBrush(QColor(50, 120, 240, 30))
                p.drawRect(r)
            elif t in ("line", "arrow", "m_distance"):
                p.setPen(QPen(self._qcolor(self.props["stroke"]), self.props["width"] * self.zoom))
                a = self._drag_start_scene or r.topLeft()
                b = self._drag_end_scene or r.bottomRight()
                p.drawLine(a, b)
            elif t == "oval":
                p.setPen(QPen(self._qcolor(self.props["stroke"]), self.props["width"] * self.zoom))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(r)
            elif t in SH.SHAPES and r.width() > 1 and r.height() > 1:
                p.setPen(QPen(self._qcolor(self.props["stroke"]), self.props["width"] * self.zoom))
                p.setBrush(Qt.BrushStyle.NoBrush)
                shape = SH.shape_points(t, fitz.Rect(r.left(), r.top(), r.right(), r.bottom()))
                p.drawPolygon(QPolygonF([QPointF(q.x, q.y) for q in shape]))
            else:
                p.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
                p.setBrush(QColor(232, 56, 61, 25) if t != "redact" else QColor(0, 0, 0, 120))
                p.drawRect(r)
        if self._ink and self._drag_page == i:
            p.setPen(QPen(self._qcolor(self.props["ink_color"]), self.props["ink_width"] * self.zoom,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath(self._ink[0])
            for q in self._ink[1:]:
                path.lineTo(q)
            p.drawPath(path)
        if self._poly and self._poly_page == i:
            p.setPen(QPen(self._qcolor(self.props["stroke"]), max(1.0, self.props["width"] * self.zoom)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            poly = QPolygonF(self._poly)
            p.drawPolyline(poly)
            cur = self.mapToScene(self.viewport().mapFromGlobal(QCursor.pos()))
            p.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
            p.drawLine(self._poly[-1], cur)
            for q in self._poly:
                p.setBrush(Qt.GlobalColor.white)
                p.drawEllipse(q, 3, 3)
        if self._snap_mark is not None and self.tool in MEASURE_TOOLS and self.page_at(self._snap_mark) == i:
            p.setPen(QPen(QColor(0, 120, 215), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(self._snap_mark.x() - 4, self._snap_mark.y() - 4, 8, 8))
        p.restore()

    def image_pixmap(self) -> QPixmap | None:
        """이미지 추가로 고른 파일의 그림(미리보기용, 파일이 바뀌어야 다시 읽는다)."""
        path = self.props.get("image_path") or ""
        if self._img_pm[0] != path:
            pm = QPixmap(path) if path else None
            self._img_pm = (path, pm if pm is not None and not pm.isNull() else None)
        return self._img_pm[1]

    def _image_scene_rect(self, i: int, sp: QPointF) -> QRectF | None:
        """누른 자리가 가운데인 그림 자리. 원래 크기는 픽셀 x 0.75 pt(96dpi), 쪽보다 크면 비율대로 줄이고 쪽 안으로 밀어 넣는다."""
        pm = self.image_pixmap()
        if pm is None or i < 0:
            return None
        it = self.items[i]
        page = it.mapRectToScene(it.page_rect())
        w, h = pm.width() * 0.75 * self.zoom, pm.height() * 0.75 * self.zoom
        f = min(1.0, page.width() / w, page.height() / h)
        w, h = w * f, h * f
        x = min(max(sp.x() - w / 2, page.left()), page.right() - w)
        y = min(max(sp.y() - h / 2, page.top()), page.bottom() - h)
        return QRectF(x, y, w, h)

    def _paint_links(self, p: QPainter, i: int):
        editable = self._links_editable()
        for l in self.doc.fitz[i].get_links():
            x = l.get("xref")
            if not x:
                continue
            rr = self.from_page_rect(i, fitz.Rect(l["from"]))
            st = A.link_style(self.doc, x)
            if st["visible"]:
                pen = QPen(self._qcolor(st["color"]), max(1.0, st["width"] * self.zoom))
                if st["line"] == "dash":
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                if st["line"] == "underline":
                    p.drawLine(rr.bottomLeft(), rr.bottomRight())
                else:
                    p.drawRect(rr)
            elif editable:
                p.setPen(QPen(QColor(60, 140, 240, 200), 1, Qt.PenStyle.DashLine))
                p.setBrush(QColor(60, 140, 240, 25))
                p.drawRect(rr)

    @staticmethod
    def _qcolor(c) -> QColor:
        if c is None:
            return QColor(0, 0, 0, 0)
        return QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))

