"""왼쪽 패널: 썸네일 · 북마크 · 주석 목록 · 첨부 파일 · 검색 · 레이어 · (양식 칸 · 서명)."""
from __future__ import annotations

import os
import queue
import time

import fitz
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QStyle, QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..engine import annots as A
from ..engine import pages as P
from ..engine.document import Bookmark, Document
from . import icons, theme
from .search_panel import SearchPanel  # noqa: F401  검색 패널은 search_panel.py


def panel_head(title: str, buttons=(), more=None, closable: bool = True) -> QWidget:
    """패널 머리: 제목 + 오른쪽 작은 단추들 + 닫기 ◀. 패널마다 높이·여백·밑줄이 달랐던 것을 한 모양으로.
    more: QMenu 를 채우는 함수. 주면 제목이 단추가 되어 누르면 그 패널의 더보기 메뉴가 뜬다.
    closable: 닫기 단추. 누르면 패널을 품은 탭의 panel_closed(머리)를 불러 그 옆 칸을 닫는다."""
    w = QWidget(); w.setObjectName("panelhead"); w.setFixedHeight(32)
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    hl = QHBoxLayout(w); hl.setContentsMargins(10, 0, 4, 0); hl.setSpacing(0)
    if more is None:
        t = QLabel(title); t.setObjectName("paneltitle")
        t.setMinimumWidth(t.fontMetrics().horizontalAdvance(title) + 8)   # 단추에 밀려 「첨부 파9」로 잘리지 않게
    else:
        hl.setContentsMargins(4, 0, 4, 0)
        t = QToolButton(); t.setObjectName("paneltitlebtn"); t.setText(title)
        t.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(t); m.aboutToShow.connect(lambda: (m.clear(), more(m))); t.setMenu(m)
    hl.addWidget(t); hl.addStretch()
    for name, tip, fn in buttons:
        b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(name, 16)); b.setToolTip(tip); b.setAccessibleName(tip)
        b.clicked.connect(fn); hl.addWidget(b)
    if closable:
        # QPushButton: 패널이 머리의 QToolButton 을 세어 쓰므로(썸네일 작게·크게) 다른 종류로
        c = QPushButton(); c.setObjectName("panelclose"); c.setFlat(True); c.setFixedSize(24, 24)
        c.setIcon(icons.icon("prev", 14)); c.setToolTip("닫기"); c.setAccessibleName("패널 닫기")
        def close():
            p = w.parentWidget()
            while p is not None and not hasattr(p, "panel_closed"):
                p = p.parentWidget()
            if p is not None:
                p.panel_closed(w)
        c.clicked.connect(close); hl.addWidget(c)
    return w


def group_card(title: str):
    """옅은 회색 패널 위의 흰 카드 하나: 제목 + 칸 목록. (카드, 칸 목록 QFormLayout) 을 돌려준다."""
    from PySide6.QtWidgets import QFormLayout, QFrame
    box = QFrame(); box.setObjectName("group")
    v = QVBoxLayout(box); v.setContentsMargins(10, 7, 10, 8); v.setSpacing(5)
    t = QLabel(title); t.setObjectName("grouptitle"); v.addWidget(t)
    f = QFormLayout(); f.setHorizontalSpacing(8); f.setVerticalSpacing(5); f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    v.addLayout(f)
    return box, f


def _tidy_tree(tree: QTreeWidget, stretch: int):
    """좁은 패널에서 가로 스크롤이 생기지 않게: 한 칸은 늘이고 나머지는 내용만큼, 넘치면 … 로 줄임."""
    h = tree.header()
    h.setStretchLastSection(False)
    for k in range(tree.columnCount()):
        h.setSectionResizeMode(k, QHeaderView.ResizeMode.Stretch if k == stretch else QHeaderView.ResizeMode.ResizeToContents)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tree.setTextElideMode(Qt.TextElideMode.ElideRight)
    tree.setIndentation(14)


class ThumbWorker(QThread):
    """부탁받은 쪽만 그리는 일꾼(나중에 부탁한 것부터). PyMuPDF 는 그리는 동안 GIL 을 놓지 않아
    전 쪽을 한꺼번에 그리면 화면이 그만큼 멈춘다(959쪽 열기 3.6초, 10-20 측정). 그래서 보이는 칸만 부탁받는다."""
    done = Signal(int, QImage, int)  # 쪽, 그림, 세대

    def __init__(self, doc: Document, width: int, dpr: float = 1.0, rot: int = 0):
        super().__init__()
        self.doc = doc
        self.rot = rot   # 보기 회전(화면에서만, 파일 /Rotate 는 그대로)
        self.width = int(width * dpr)
        self.dpr = dpr
        self.q: "queue.LifoQueue[tuple[int, int]]" = queue.LifoQueue()
        self.gen = 0
        self._stop = False

    def request(self, i: int):
        self.q.put((i, self.gen))

    def drop_queue(self):
        """아직 안 그린 부탁을 버린다(화면을 옮겨 안 보이게 된 칸)."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def stop(self):
        self._stop = True
        self.drop_queue()
        self.q.put((-1, -1))
        self.wait(2000)

    def run(self):
        while not self._stop:
            i, gen = self.q.get()
            if i < 0 or self._stop:
                break
            if gen != self.gen:
                continue
            try:
                w, h = self.doc.page_size(i)
                pix = self.doc.render(i, self.width / max(1, h if self.rot in (90, 270) else w))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                if self.rot:
                    img = img.transformed(QTransform().rotate(self.rot))
                img.setDevicePixelRatio(self.dpr)
                self.done.emit(i, img, gen)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.002)  # 한 장마다 GIL 을 놓아 화면이 숨 쉬게


class LazyThumbs:
    """PageGrid 의 썸네일을 보이는 칸(과 그 아래 한 화면)만 그려 채운다. 숨은 목록은 그리지 않는다."""
    AHEAD = 1.0  # 보이는 칸 아래로 화면 몇 개만큼 미리

    def __init__(self, grid: "PageGrid", doc: Document):
        self.grid, self.doc = grid, doc
        self.rot = 0   # 보기 회전 0·90·180·270
        self.worker: ThumbWorker | None = None
        self.have: set[int] = set()      # 다 그린 쪽
        self.asked: set[int] = set()     # 부탁해 둔 쪽
        self.timer = QTimer(grid); self.timer.setSingleShot(True); self.timer.timeout.connect(self._ask)
        grid.verticalScrollBar().valueChanged.connect(self.kick)
        grid.shown.connect(self.kick)

    def pixmaps(self) -> dict[int, QPixmap]:
        """다 그린 썸네일(쪽 → 그림). 쪽 구조가 바뀔 때 안 바뀐 쪽 그림을 옮겨 쓰려고."""
        out = {}
        for k in range(self.grid.count()):   # 끌어 옮긴 직후엔 칸 차례가 쪽 차례와 다르다: 칸에 적힌 쪽으로 찾는다
            it = self.grid.item(k)
            p = it.data(Qt.ItemDataRole.UserRole)
            if p in self.have:
                out[p] = it.data(ThumbDelegate.PIX)
        return out

    def start(self, width: int, dpr: float, keep: dict[int, QPixmap] | None = None):
        """keep: 새 쪽 → 옮겨 쓸 그림(그 쪽은 다시 그리지 않는다)."""
        self.stop()
        self.have.clear(); self.asked.clear()
        for p, pm in (keep or {}).items():
            if p < self.grid.count():
                self.grid.item(p).setData(ThumbDelegate.PIX, pm)
                self.have.add(p)
        self.worker = ThumbWorker(self.doc, width, dpr, self.rot)
        self.worker.done.connect(self._done)
        self.worker.start()
        self.kick()

    def stop(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

    def forget(self, pages=None):
        """이 쪽들은 다시 그린다(옛 그림은 새 그림이 올 때까지 둔다)."""
        if self.worker is None:
            return
        # 그리던 그림은 옛 문서 것이라 버린다(세대를 올림). 부탁해 둔 것도 다시 부탁한다
        self.worker.gen += 1; self.worker.drop_queue(); self.asked.clear()
        if pages is None:
            self.have.clear()
        else:
            self.have.difference_update(pages)
        self.kick()

    def busy(self) -> bool:
        return bool(self.asked - self.have)

    def kick(self, *_):
        self.timer.start(30)

    def _visible_rows(self) -> list[int]:
        g = self.grid
        vp = g.viewport().rect()
        vp.setBottom(int(vp.bottom() + vp.height() * self.AHEAD))
        first = g.indexAt(vp.topLeft() + QPoint(4, 4)).row()
        rows = []
        for k in range(max(0, first), g.count()):
            r = g.visualItemRect(g.item(k))
            if r.top() > vp.bottom():
                break
            if r.intersects(vp):
                rows.append(k)
        return rows

    def _ask(self):
        if self.worker is None or not self.grid.isVisible():
            return
        self.worker.drop_queue()
        self.asked &= self.have           # 버린 부탁은 다시 부탁할 수 있게
        want = [self.grid.item(k).data(Qt.ItemDataRole.UserRole) for k in self._visible_rows()]
        for p in reversed(want):          # 나중에 부탁한 것부터 그리니 위 칸이 먼저 나온다
            if p not in self.have and p not in self.asked:
                self.asked.add(p)
                self.worker.request(p)

    def _done(self, i: int, img: QImage, gen: int):
        if self.worker is None or gen != self.worker.gen:
            return
        self.have.add(i)
        if i < self.grid.count():
            it = self.grid.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == i:
                it.setData(ThumbDelegate.PIX, QPixmap.fromImage(img))


class ThumbDelegate(QStyledItemDelegate):
    """썸네일을 QIcon 크기 맞춤 없이 저장한 그림 그대로 그린다(고해상도 화면에서 작아지는 문제 피함)."""
    PIX = Qt.ItemDataRole.UserRole + 1

    def paint(self, p, opt, idx):
        r = opt.rect
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.CUR  # 어두운 화면에서도 맞는 색
        if selected:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c["SELECT_BG"]))
            p.drawRoundedRect(QRectF(r).adjusted(2, 1, -2, -1), 6, 6)
        pm = idx.data(self.PIX)
        label = idx.data(Qt.ItemDataRole.DisplayRole) or ""
        top = r.top() + 4
        if isinstance(pm, QPixmap) and not pm.isNull():
            w = pm.width() / pm.devicePixelRatio(); h = pm.height() / pm.devicePixelRatio()
            x = r.left() + (r.width() - w) / 2
            p.fillRect(QRectF(x + 1, top + 2, w, h), QColor(0, 0, 0, 22))
            p.drawPixmap(QRectF(x, top, w, h), pm, QRectF(pm.rect()))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(QPen(QColor(theme.ACCENT) if selected else QColor(c["FIELD_LINE"]), 2 if selected else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x, top, w, h))
        f = p.font(); f.setBold(selected); p.setFont(f)
        p.setPen(QColor(theme.ACCENT) if selected else QColor(c["TEXT_DIM"]))
        p.drawText(QRectF(r.left(), r.bottom() - 19, r.width(), 18), Qt.AlignmentFlag.AlignCenter, str(label))
        p.restore()

    def sizeHint(self, opt, idx):
        return idx.data(Qt.ItemDataRole.SizeHintRole) or QSize(100, 140)


class PageGrid(QListWidget):
    """쪽 썸네일 격자. 끌어 놓아 순서를 바꾸면 reordered(새 순서) 를 한 번 낸다.
    IconMode 는 Qt 가 놓을 자리를 받지 않아 끌어도 아무 일이 없다(10-19 진짜 끌기로 확인).
    그래서 ListMode 를 왼→오 흐름·줄바꿈으로 격자처럼 쓴다."""
    reordered = Signal(list)
    shown = Signal()  # 보이게 됐거나 크기가 바뀜(그릴 칸이 달라짐)

    def showEvent(self, e):
        super().showEvent(e)
        self.shown.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.shown.emit()

    def __init__(self, spacing: int, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)  # 보기 방식을 정하면 끌기가 꺼지므로 그 뒤에
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSpacing(spacing)
        self.setItemDelegate(ThumbDelegate(self))
        self._pending = False
        self.model().rowsMoved.connect(self._moved)

    def _moved(self, *_):
        # 여러 장을 끌면 한 장마다 rowsMoved 가 온다. 놓기가 다 끝난 뒤 한 번만 알린다.
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._emit)

    def _emit(self):
        self._pending = False
        order = [self.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.count())]
        if order != list(range(len(order))):
            self.reordered.emit(order)

    # 다른 문서 썸네일에서 끌어온 쪽(복사)·탐색기 PDF(그 자리에 끼움). accept_foreign 을 켠 칸만 받는다
    pagesDropped = Signal(object, list, int)   # 끌어온 칸(PageGrid), 그 칸에서 고른 쪽, 끼울 자리
    filesDropped = Signal(list, int)           # PDF 경로, 끼울 자리
    accept_foreign = False

    def _foreign(self, e) -> str | None:
        if not self.accept_foreign:
            return None
        src = e.source()
        if isinstance(src, PageGrid) and src is not self:
            return "pages"
        md = e.mimeData()
        if src is None and md.hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in md.urls()):
            return "files"
        return None

    def drop_index(self, pos) -> int:
        """놓은 자리 → 끼울 쪽 번호(칸 오른쪽 절반이면 그 뒤)."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.count()
        return idx.row() + (1 if pos.x() > self.visualRect(idx).center().x() else 0)

    def dragEnterEvent(self, e):
        if self._foreign(e):
            e.setDropAction(Qt.DropAction.CopyAction); e.accept(); return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._foreign(e):
            e.setDropAction(Qt.DropAction.CopyAction); e.accept(); return
        super().dragMoveEvent(e)

    def dropEvent(self, e):
        kind = self._foreign(e)
        if not kind:
            return super().dropEvent(e)
        at = self.drop_index(e.position().toPoint())
        e.setDropAction(Qt.DropAction.CopyAction); e.accept()
        if kind == "pages":
            src = e.source()
            pages = sorted(it.data(Qt.ItemDataRole.UserRole) for it in src.selectedItems())
            QTimer.singleShot(0, lambda: self.pagesDropped.emit(src, pages, at))   # 끌기가 끝난 뒤에 문서를 고친다
        else:
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".pdf")]
            QTimer.singleShot(0, lambda: self.filesDropped.emit(paths, at))


def kept_thumbs(lazy: "LazyThumbs", mapping: list[int | None] | None, dirty=()) -> dict[int, QPixmap]:
    """mapping[새 쪽] = 옛 쪽 으로 옮겨 쓸 썸네일. dirty 에 든 새 쪽은 빼고(다시 그림)."""
    if mapping is None:
        return {}
    old = lazy.pixmaps()
    return {k: old[o] for k, o in enumerate(mapping) if o in old and k not in dirty}


def fill_grid(grid: PageGrid, doc: Document, thumb_w: int, pad_w: int, pad_h: int, rot: int = 0):
    """쪽마다 빈 흰 칸을 놓는다(그림은 LazyThumbs 가 보이는 칸부터 채움)."""
    grid.setUpdatesEnabled(False)
    grid.clear()
    labels = P.labels(doc)
    blank: dict[int, QPixmap] = {}   # 쪽 크기가 같으면 빈 그림 하나를 같이 쓴다
    for i in range(doc.page_count):
        w, hh = doc.page_size(i)
        if rot in (90, 270):
            w, hh = hh, w
        h = int(thumb_w * hh / max(1, w))
        if h not in blank:
            pm = QPixmap(thumb_w, h); pm.fill(Qt.GlobalColor.white); blank[h] = pm
        it = QListWidgetItem(labels[i])
        it.setData(Qt.ItemDataRole.UserRole, i)
        it.setData(ThumbDelegate.PIX, blank[h])
        it.setSizeHint(QSize(thumb_w + pad_w, h + pad_h))
        grid.addItem(it)
    grid.setUpdatesEnabled(True)


page_clip: dict = {"data": None, "n": 0}   # 쪽 클립보드(앱 안 여러 탭·창이 같이 씀): 고른 쪽만 담은 PDF 바이트


class _Prefs:
    """창 설정을 읽고 쓰는 자리. DocTab 이 창 설정으로 바꿔 끼운다(탭이 다른 창으로 옮겨 가도 그때 창 것)."""
    get_prefs = staticmethod(lambda: {})
    save_prefs = staticmethod(lambda: None)

    def pref(self, key: str, default=None):
        return self.get_prefs().get(key, default)

    def set_pref(self, key: str, value):
        self.get_prefs()[key] = value
        self.save_prefs()


def _key_filter(e, keys) -> bool | None:
    """창 단축키보다 먼저 이 칸이 받을 키(keys: (Key, 조합) → 할 일). 처리했으면 True."""
    if e.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
        return None
    fn = keys.get((e.key(), e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier))
    if fn is None:
        return None
    e.accept()
    if e.type() == QEvent.Type.KeyPress:
        fn()
    return True


class ThumbnailPanel(_Prefs, QWidget):
    """썸네일: 눌러 이동, 끌어 순서 바꾸기(다른 문서로 끌면 복사, 탐색기 PDF 는 끼움), 오른쪽 클릭 메뉴, 쪽 복사·붙여넣기."""
    pageClicked = Signal(int)
    reordered = Signal(list)       # 새 순서
    pagesAction = Signal(str, list)  # pages_action 열쇠(회전·삭제·복사·붙여넣기 등), 고른 쪽
    pagesDropped = Signal(object, list, int)   # 끌어온 문서, 쪽, 끼울 자리
    filesDropped = Signal(list, int)           # 탐색기 PDF, 끼울 자리

    STEPS = (60, 90, 120, 150, 180)   # 썸네일 폭(px) 다섯 단계, 처음은 120
    MENU = (("복사", "copy"), ("붙여넣기", "paste"), ("삭제", "delete"), (None, None),
            ("페이지 삽입", (("빈 페이지", "insert_blank"), ("PDF에서", "insert_pdf"))), ("페이지 복제", "duplicate"),
            ("페이지 바꾸기", "replace"), ("페이지 내보내기", "extract"),
            ("페이지 회전", (("왼쪽으로 회전", "rotate_left"), ("오른쪽으로 회전", "rotate_right"))),
            ("OCR 페이지", "ocr"), ("페이지 크기", "resize"), ("페이지 박스", "pagebox"), ("페이지 레이블", "pagelabel"),
            ("페이지 인쇄", "print"), ("페이지를 이미지로 추출", "export_images"), (None, None),
            ("페이지 썸네일 확대", "zoom_in"), ("페이지 썸네일 축소", "zoom_out"))

    def __init__(self, doc: Document, thumb_w: int = 120, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.thumb_w = thumb_w
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = panel_head("썸네일", (("more", "더 보기", self._more), ("zoomin", "확대", lambda: self.zoom(1)),
                                    ("zoomout", "축소", lambda: self.zoom(-1)),
                                    ("rotateleft", "왼쪽으로 회전", lambda: self._act("rotate_left")),
                                    ("rotateright", "오른쪽으로 회전", lambda: self._act("rotate_right")),
                                    ("delete", "삭제", lambda: self._act("delete"))))
        self.more_btn, self.zoom_in, self.zoom_out, *_ = head.findChildren(QToolButton)
        lay.addWidget(head)
        self.list = PageGrid(3)
        self.list.owner_doc = doc
        self.list.accept_foreign = True
        self.list.itemClicked.connect(lambda it: self.pageClicked.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.reordered.connect(self.reordered)
        self.list.pagesDropped.connect(lambda src, pages, at: self.pagesDropped.emit(getattr(src, "owner_doc", None), pages, at))
        self.list.filesDropped.connect(self.filesDropped)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(lambda pos: self._menu(self.list.mapToGlobal(pos)))
        lay.addWidget(self.list)
        self.thumbs = LazyThumbs(self.list, doc)
        self.list.viewport().installEventFilter(self)
        self.list.installEventFilter(self)
        C, N = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.NoModifier
        self._keys = {(Qt.Key.Key_C, C): lambda: self._act("copy"), (Qt.Key.Key_V, C): lambda: self._act("paste"),
                      (Qt.Key.Key_A, C): self.list.selectAll, (Qt.Key.Key_Delete, N): lambda: self._act("delete")}
        self.reload()

    def set_width(self, w: int):
        self.thumb_w = w
        self.reload()

    def zoom(self, step: int):
        """다음·앞 단계로. 양 끝에 닿으면 그쪽 단추가 꺼진다."""
        k = min(range(len(self.STEPS)), key=lambda i: abs(self.STEPS[i] - self.thumb_w))
        w = self.STEPS[max(0, min(len(self.STEPS) - 1, k + step))]
        if w != self.thumb_w:
            self.set_width(w)

    def selected_pages(self) -> list[int]:
        return sorted(it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems())

    def _pages(self) -> list[int]:
        return self.selected_pages() or ([self.list.currentRow()] if self.list.currentRow() >= 0 else [])

    def _act(self, key: str):
        if key in ("zoom_in", "zoom_out"):
            return self.zoom(1 if key == "zoom_in" else -1)
        pages = self._pages()
        if pages or key == "paste":
            self.pagesAction.emit(key, pages)

    def reload(self, mapping: list[int | None] | None = None, dirty=()):
        keep = kept_thumbs(self.thumbs, mapping, dirty)
        self.zoom_out.setEnabled(self.thumb_w > self.STEPS[0]); self.zoom_in.setEnabled(self.thumb_w < self.STEPS[-1])
        fill_grid(self.list, self.doc, self.thumb_w, 12, 27, self.thumbs.rot)
        self._fit_items()
        self.thumbs.start(self.thumb_w, self.devicePixelRatioF(), keep)

    def set_view_rotation(self, deg: int):
        """탭의 보기 회전 각을 따라 썸네일을 돌려 다시 그린다. 파일은 안 바뀐다."""
        deg = int(deg) % 360 // 90 * 90
        if deg != self.thumbs.rot:
            self.thumbs.rot = deg
            self.reload()

    def _fit_items(self):
        """한 줄에 한 장만 들어가면 칸을 패널 폭만큼 넓혀 썸네일이 가운데 오게(왼쪽에 붙고 오른쪽이 비던 것)."""
        each = self.thumb_w + 12
        room = self.list.viewport().width() - 2 * self.list.spacing() - 1
        w = room if each <= room < 2 * each + 2 * self.list.spacing() else each
        for k in range(self.list.count()):
            it = self.list.item(k)
            if it.sizeHint().width() != w:
                it.setSizeHint(QSize(w, it.sizeHint().height()))

    def eventFilter(self, obj, e):
        if obj is self.list.viewport():
            if e.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._fit_items)
            elif e.type() == QEvent.Type.Wheel and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                dy = e.angleDelta().y()
                if dy:
                    self.zoom(1 if dy > 0 else -1)
                return True
        elif obj is self.list:
            done = _key_filter(e, self._keys)
            if done is not None:
                return done
        return super().eventFilter(obj, e)

    def refresh_pages(self, pages: list[int] | None):
        self.thumbs.forget(pages)

    def set_current(self, i: int):
        if 0 <= i < self.list.count():
            self.list.blockSignals(True)
            self.list.setCurrentRow(i)
            self.list.scrollToItem(self.list.item(i))
            self.list.blockSignals(False)

    def _more(self):
        b = self.more_btn
        self._menu(b.mapToGlobal(b.rect().bottomLeft()))

    def build_menu(self) -> QMenu:
        pages = self._pages()
        m = QMenu(self)

        def fill(menu, items):
            for label, key in items:
                if label is None:
                    menu.addSeparator()
                elif isinstance(key, tuple):
                    fill(menu.addMenu(label), key)
                else:
                    a = menu.addAction(label, lambda k=key: self._act(k))
                    a.setObjectName(key)
                    if key == "paste":
                        a.setEnabled(bool(page_clip["data"]))
                    elif key == "zoom_in":
                        a.setEnabled(self.thumb_w < self.STEPS[-1])
                    elif key == "zoom_out":
                        a.setEnabled(self.thumb_w > self.STEPS[0])
                    elif not pages:
                        a.setEnabled(False)
        fill(m, self.MENU)
        return m

    def _menu(self, gpos):
        self.build_menu().exec(gpos)

    def close(self):
        self.thumbs.stop()


class BookmarkPanel(_Prefs, QWidget):
    """북마크: 위 단추(더 보기·추가·이름 바꾸기·삭제·글자 키우기·줄이기), 굵게·기울임·색, 여러 개 끌어 옮기기,
    비었으면 「북마크 없음」 과 [자동 북마크]. 설정 열쇠 bm_font_pt(글자 크기 pt) · bm_style(새 북마크 기본 모양)."""
    gotoPage = Signal(int, object)  # 쪽, 쪽 안 높이(pt) 또는 None
    changed = Signal()
    autoRequested = Signal()       # 빈 목록의 [자동 북마크]
    printRequested = Signal(int)   # 「페이지 인쇄」: 그 북마크 쪽
    tocMade = Signal(int)          # 목차 쪽을 맨 앞에 끼움(끼운 쪽 수)
    DEST = Qt.ItemDataRole.UserRole + 1  # 쪽 안 자리(get_toc 넷째 칸)
    STYLE = Qt.ItemDataRole.UserRole + 2  # {bold, italic, color "#RRGGBB"}
    STEPS = (8.0, 9.0, 10.5, 12.0, 14.0, 16.0)   # 글자 크기 pt
    NEW_NAME = "새 북마크"

    def __init__(self, doc: Document, get_current_page, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.get_current_page = get_current_page
        self.font_pt = 9.0
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        head = panel_head("북마크", (("more", "더 보기", self._more), ("add", "북마크 추가", self.add),
                                    ("edit", "이름 바꾸기", self.rename_current), ("delete", "삭제", self.delete_selected),
                                    ("zoomin", "글자 키우기", lambda: self.set_font(self._step(1))),
                                    ("zoomout", "글자 줄이기", lambda: self.set_font(self._step(-1)))))
        self.more_btn, self.add_btn, self.rename_btn, self.delete_btn, self.font_up, self.font_down = head.findChildren(QToolButton)
        lay.addWidget(head)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); _tidy_tree(self.tree, 0)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda pos: self._menu(self.tree.mapToGlobal(pos), self.tree.itemAt(pos)))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._loading = False
        self._pending = False
        mdl = self.tree.model()
        for sig in (mdl.rowsMoved, mdl.rowsInserted, mdl.rowsRemoved):   # 여러 개를 끌어 옮겨도 저장(되돌리기)은 한 번
            sig.connect(self._queue_save)
        self.tree.itemChanged.connect(self._queue_save)
        self.tree.itemSelectionChanged.connect(self._sync_buttons)
        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        self._keys = {(Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier): self.delete_selected}
        lay.addWidget(self.tree)
        self.empty = QWidget(); el = QVBoxLayout(self.empty); el.setContentsMargins(10, 24, 10, 10); el.setSpacing(8)
        msg = QLabel("북마크 없음"); msg.setObjectName("dim"); msg.setAlignment(Qt.AlignmentFlag.AlignCenter); el.addWidget(msg)
        self.auto_btn = QPushButton("자동 북마크"); self.auto_btn.clicked.connect(self.autoRequested)
        el.addWidget(self.auto_btn, 0, Qt.AlignmentFlag.AlignHCenter); el.addStretch(1)
        lay.addWidget(self.empty)
        self.reload()

    # ── 설정 ──
    def apply_prefs(self):
        """창 설정을 받은 뒤 한 번: 글자 크기."""
        self.set_font(self.pref("bm_font_pt", 9.0), remember=False)

    def default_style(self) -> dict:
        from ..engine import outline as OL
        return OL.norm_style(self.pref("bm_style"))

    def _step(self, d: int) -> float:
        k = min(range(len(self.STEPS)), key=lambda i: abs(self.STEPS[i] - self.font_pt))
        return self.STEPS[max(0, min(len(self.STEPS) - 1, k + d))]

    def set_font(self, pt, remember: bool = True):
        try:
            pt = float(pt)
        except (TypeError, ValueError):
            pt = 9.0
        pt = min(self.STEPS, key=lambda s: abs(s - pt))
        self.font_pt = pt
        f = self.tree.font(); f.setPointSizeF(pt); self.tree.setFont(f)
        self._walk(self._paint)
        self.font_up.setEnabled(pt < self.STEPS[-1]); self.font_down.setEnabled(pt > self.STEPS[0])
        if remember:
            self.set_pref("bm_font_pt", pt)

    # ── 그리기 ──
    def _walk(self, fn, parent=None):
        parent = parent or self.tree.invisibleRootItem()
        for k in range(parent.childCount()):
            it = parent.child(k)
            fn(it)
            self._walk(fn, it)

    def _paint(self, it: QTreeWidgetItem):
        """모양(굵게·기울임·색)을 항목에 그대로. 검정은 글자색을 두어 어두운 화면에서도 보이게."""
        st = it.data(0, self.STYLE) or {}
        was = self.tree.signalsBlocked(); self.tree.blockSignals(True)
        f = QFont(self.tree.font()); f.setBold(bool(st.get("bold"))); f.setItalic(bool(st.get("italic")))
        it.setFont(0, f)
        c = st.get("color") or "#000000"
        it.setForeground(0, QColor(c) if c.upper() != "#000000" else self.tree.palette().text())
        self.tree.blockSignals(was)

    def _item(self, title: str, page: int, dest=None, style: dict | None = None) -> QTreeWidgetItem:
        from ..engine import outline as OL
        it = QTreeWidgetItem([title])
        it.setData(0, Qt.ItemDataRole.UserRole, page)
        it.setData(0, self.DEST, {k: v for k, v in dest.items() if k not in ("bold", "italic", "color")} if dest else None)
        it.setData(0, self.STYLE, OL.norm_style(style if style is not None else OL.style_of(dest)))
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self._paint(it)
        return it

    def reload(self):
        # 문서가 바뀔 때마다 불린다. 여기서 신호를 다시 이으면 이름 하나 바꿀 때 저장이 그 횟수만큼 돈다.
        self._loading = True
        self.tree.blockSignals(True)
        self.tree.clear()

        def add(parent, nodes: list[Bookmark]):
            for b in nodes:
                it = self._item(b.title, b.page, b.dest)
                (parent.addChild if isinstance(parent, QTreeWidgetItem) else self.tree.addTopLevelItem)(it)
                add(it, b.children)
        add(self.tree, self.doc.bookmarks())
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._loading = False
        self._show_empty()
        self._sync_buttons()

    def _show_empty(self):
        none = self.tree.topLevelItemCount() == 0
        self.tree.setVisible(not none); self.empty.setVisible(none)

    def _sync_buttons(self):
        sel = bool(self.tree.selectedItems())
        self.rename_btn.setEnabled(sel or self.tree.currentItem() is not None)
        self.delete_btn.setEnabled(sel)

    def _clicked(self, it, _):
        pg = it.data(0, Qt.ItemDataRole.UserRole)
        if pg is not None and pg >= 0:
            self.gotoPage.emit(pg, self.doc.bookmark_y(Bookmark(1, "", pg, dest=it.data(0, self.DEST))))

    # ── 저장 ──
    def _collect(self) -> list[Bookmark]:
        from ..engine import outline as OL

        def walk(parent) -> list[Bookmark]:
            out = []
            n = parent.childCount() if isinstance(parent, QTreeWidgetItem) else parent.topLevelItemCount()
            for k in range(n):
                it = parent.child(k) if isinstance(parent, QTreeWidgetItem) else parent.topLevelItem(k)
                pg = it.data(0, Qt.ItemDataRole.UserRole)
                b = Bookmark(1, it.text(0), 0 if pg is None else pg,
                             dest=OL.with_style(it.data(0, self.DEST), it.data(0, self.STYLE) or {}))
                b.children = walk(it)
                out.append(b)
            return out
        return walk(self.tree)

    def _queue_save(self, *_):
        if self._loading or self._pending:
            return
        self._pending = True
        QTimer.singleShot(0, self._flush)

    def _flush(self):
        if self._pending:
            self._save()

    def _save(self):
        self._pending = False
        self.doc.set_bookmarks(self._collect())
        self._show_empty()
        self.changed.emit()

    # ── 일 ──
    def rename_current(self):
        it = self.tree.currentItem() or next(iter(self.tree.selectedItems()), None)
        if it:
            self.tree.editItem(it, 0)

    def _insert(self, it: QTreeWidgetItem, parent: QTreeWidgetItem | None = None):
        if parent is not None:
            parent.addChild(it); parent.setExpanded(True)
        else:
            cur = self.tree.currentItem()
            if cur and cur.parent():
                cur.parent().insertChild(cur.parent().indexOfChild(cur) + 1, it)
            elif cur:
                self.tree.insertTopLevelItem(self.tree.indexOfTopLevelItem(cur) + 1, it)
            else:
                self.tree.addTopLevelItem(it)
        self._show_empty()
        self.tree.clearSelection()
        self.tree.setCurrentItem(it)
        self._save()
        self.tree.editItem(it, 0)   # 창을 띄우지 않고 이름 칸을 바로 연다

    def add(self, title: str = "", page: int | None = None):
        """지금 쪽(또는 page)에 「새 북마크」(고른 글자가 있으면 그 글자)를 바로 만들고 이름 칸을 연다."""
        pg = self.get_current_page() if page is None else page
        title = title if isinstance(title, str) else ""   # 단추·메뉴 신호는 checked(bool) 를 넘긴다
        self._insert(self._item(" ".join(title.split())[:255] or self.NEW_NAME, pg, style=self.default_style()))

    def _add_child(self, parent: QTreeWidgetItem):
        self._insert(self._item(self.NEW_NAME, self.get_current_page(), style=self.default_style()), parent)

    def _top_selected(self) -> list[QTreeWidgetItem]:
        """고른 것 가운데 위 항목이 같이 골린 것은 뺀다(위를 지우면 아래도 지워진다)."""
        sel = set(map(id, self.tree.selectedItems()))
        out = []
        for it in self.tree.selectedItems():
            p = it.parent()
            while p is not None and id(p) not in sel:
                p = p.parent()
            if p is None:
                out.append(it)
        return out

    def delete_selected(self):
        items = self._top_selected() or ([self.tree.currentItem()] if self.tree.currentItem() else [])
        if not items:
            return
        self._loading = True
        for it in items:
            (it.parent() or self.tree.invisibleRootItem()).removeChild(it)
        self._loading = False
        self._save()

    def _repoint(self, it: QTreeWidgetItem):
        if QMessageBox.question(self, "북마크 대상", "북마크 대상을 현재 페이지로 설정하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        self.tree.blockSignals(True)
        it.setData(0, self.DEST, None)  # 옛 쪽 안 자리는 버리고 새 쪽 맨 위로
        it.setData(0, Qt.ItemDataRole.UserRole, self.get_current_page())
        self.tree.blockSignals(False)
        self._save()

    def set_style(self, items: list[QTreeWidgetItem], st: dict):
        from ..engine import outline as OL
        st = OL.norm_style(st)
        for it in items:
            self.tree.blockSignals(True); it.setData(0, self.STYLE, dict(st)); self.tree.blockSignals(False)
            self._paint(it)
        self._save()

    def open_props(self, items: list[QTreeWidgetItem] | None = None):
        from .dlg_bookmark import BookmarkPropsDialog
        items = items or self.tree.selectedItems()
        if not items:
            return
        dlg = BookmarkPropsDialog(self, items[0].data(0, self.STYLE), lambda st: self.set_pref("bm_style", st))
        if dlg.exec():
            self.set_style(items, dlg.values())

    def make_toc(self):
        from ..engine import outline as OL
        from .dlg_bookmark import TocDialog
        entries = self.doc.flat_toc()
        if not entries:
            return
        dlg = TocDialog(self)
        if not dlg.exec():
            return
        try:
            n = OL.make_toc_pages(self.doc, entries, dlg.values())
        except ValueError as e:
            QMessageBox.warning(self, "목차 페이지", str(e)); return
        self.tocMade.emit(n)

    def _has_children(self) -> bool:
        found = []
        self._walk(lambda it: found.append(it) if it.childCount() else None)
        return bool(found)

    def build_menu(self, it: QTreeWidgetItem | None = None) -> QMenu:
        sel = self.tree.selectedItems()
        one = sel[0] if len(sel) == 1 else (it if it is not None and not sel else None)
        m = QMenu(self)

        def act(label, fn, on=True, name=""):
            a = m.addAction(label, fn); a.setEnabled(bool(on)); a.setObjectName(name)
            return a
        act("북마크 삭제", self.delete_selected, sel or it, "delete")
        act("북마크 이름 바꾸기", lambda: self.tree.editItem(one or sel[0], 0), one or sel, "rename")
        act("북마크 추가", self.add, True, "add")
        act("하위 북마크 추가", lambda: self._add_child(one), one, "add_child")
        act("대상 설정", lambda: self._repoint(one), one, "repoint")
        m.addSeparator()
        kids = self._has_children()
        act("모든 북마크 확장", self.tree.expandAll, kids, "expand")
        act("모든 북마크 축소", self.tree.collapseAll, kids, "collapse")
        m.addSeparator()
        act("북마크에서 목차 페이지 만들기", self.make_toc, self.tree.topLevelItemCount(), "toc")
        act("페이지 인쇄", lambda: self.printRequested.emit(max(0, one.data(0, Qt.ItemDataRole.UserRole) or 0)), one, "print")
        m.addSeparator()
        act("속성", lambda: self.open_props(sel or [one]), sel or one, "props")
        act("현재 모양을 새 기본값으로 사용", lambda: self.set_pref("bm_style", dict(one.data(0, self.STYLE))), one, "set_default")
        return m

    def _menu(self, gpos, it=None):
        self.build_menu(it).exec(gpos)

    def _more(self):
        b = self.more_btn
        self._menu(b.mapToGlobal(b.rect().bottomLeft()), self.tree.currentItem())

    def eventFilter(self, obj, e):
        if obj is self.tree.viewport() and e.type() == QEvent.Type.Wheel and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = e.angleDelta().y()
            if dy:
                self.set_font(self._step(1 if dy > 0 else -1))
            return True
        if obj is self.tree and self.tree.state() != QAbstractItemView.State.EditingState:
            done = _key_filter(e, self._keys)
            if done is not None:
                return done
        return super().eventFilter(obj, e)


class _FoundText(QStyledItemDelegate):
    """주석 목록 칸: 찾기 칸 글자가 든 줄은 그 글자만 굵게 그린다(나머지 꾸밈은 Qt 기본 그대로)."""

    def __init__(self, panel: "AnnotPanel"):
        super().__init__(panel.tree)
        self.panel = panel

    def paint(self, p, opt, idx):
        from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument, QTextOption
        from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
        from .annot_filter import bold_html, find_spans
        q = self.panel.find.text()
        o = QStyleOptionViewItem(opt); self.initStyleOption(o, idx)
        if not find_spans(o.text, q):
            return super().paint(p, opt, idx)
        text, o.text = o.text, ""
        st = o.widget.style() if o.widget else QApplication.style()
        st.drawControl(QStyle.ControlElement.CE_ItemViewItem, o, p, o.widget)
        r = st.subElementRect(QStyle.SubElement.SE_ItemViewItemText, o, o.widget).adjusted(3, 0, -3, 0)
        doc = QTextDocument(); doc.setDocumentMargin(0); doc.setDefaultFont(o.font)
        doc.setDefaultTextOption(QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
        doc.defaultTextOption().setWrapMode(QTextOption.WrapMode.NoWrap)
        doc.setHtml(f"<span style='white-space:pre'>{bold_html(text, q)}</span>")
        ctx = QAbstractTextDocumentLayout.PaintContext()
        role = QPalette.ColorRole.HighlightedText if o.state & QStyle.StateFlag.State_Selected else QPalette.ColorRole.Text
        ctx.palette.setColor(QPalette.ColorRole.Text, o.palette.color(role))
        p.save()
        p.translate(r.left(), r.top() + (r.height() - doc.size().height()) / 2)
        p.setClipRect(QRectF(0, 0, r.width(), r.height()))
        doc.documentLayout().draw(p, ctx)
        p.restore()


class _NoteEditor(_FoundText):
    """주석 목록 내용 칸을 그 자리에서 고친다: 여러 줄, Ctrl+Enter 확정, Esc 취소, 칸을 떠나도 확정."""

    def createEditor(self, parent, opt, idx):
        from PySide6.QtWidgets import QPlainTextEdit
        e = QPlainTextEdit(parent)
        e.setPlaceholderText("답장 입력" if self.panel._is_new_reply(idx) else "주석 내용")
        return e

    def setEditorData(self, e, idx):
        e.setPlainText(idx.data(Qt.ItemDataRole.EditRole) or "")
        e.selectAll()

    def setModelData(self, e, model, idx):
        self.panel._commit_edit(idx, e.toPlainText())

    def updateEditorGeometry(self, e, opt, idx):
        r = opt.rect
        e.setGeometry(r.x(), r.y(), max(r.width(), 120), max(r.height() * 3, 60))

    def eventFilter(self, e, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.commitData.emit(e)
            self.closeEditor.emit(e, QAbstractItemDelegate.EndEditHint.NoHint)
            return True
        return super().eventFilter(e, ev)


class AnnotPanel(QWidget):
    pickAnnot = Signal(int, int)  # page, xref: 뷰어에서도 그 주석을 고른다
    changed = Signal()
    importExport = Signal(str)

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.flt: dict | None = None      # 필터 창에서 고른 작성자·종류·색(없으면 전부)
        self._syncing = False
        self._collapsed: set[str] = set()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(panel_head("주석", tuple((name, tip, lambda _=False, k=key: self._action(k)) for name, tip, key in (
            ("import", "주석 가져오기", "import"), ("export", "주석 내보내기", "export"), ("delete", "모두 삭제", "delete_all")))))
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(8, 6, 8, 6); bl.setSpacing(4)
        row = QHBoxLayout(); row.setSpacing(4)
        # 묶는 기준(주석 목록: 쪽·날짜·작성자·종류). 저장하지 않고 늘 쪽순으로 연다
        self.group = QComboBox(); self.group.addItems(["쪽순", "날짜순", "작성자순", "종류순"])
        self.group.currentIndexChanged.connect(self.reload)
        self.filter_btn = QPushButton("필터"); self.filter_btn.setCheckable(True)
        self.filter_btn.clicked.connect(self.open_filter)
        row.addWidget(self.group, 1); row.addWidget(self.filter_btn); bl.addLayout(row)
        self.find = QLineEdit(); self.find.setPlaceholderText("내용·작성자 찾기"); self.find.setClearButtonEnabled(True)
        for cb in self.find.findChildren(QToolButton):
            cb.setAccessibleName("찾기 칸 지우기")
        self.find.textChanged.connect(self.reload)
        bl.addWidget(self.find)
        row = QHBoxLayout(); row.setSpacing(4)
        for text, fn in (("모두 펼치기", self.expand_all), ("모두 접기", self.collapse_all)):
            b = QPushButton(text); b.clicked.connect(fn); row.addWidget(b, 1)
        bl.addLayout(row)
        lay.addWidget(box)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["종류", "내용", "작성자", "날짜"]); _tidy_tree(self.tree, 1)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # 내용 칸만 직접 연다
        self.tree.setItemDelegateForColumn(1, _NoteEditor(self))
        self.tree.setItemDelegateForColumn(2, _FoundText(self))   # 찾은 글자 굵게(내용·작성자)
        self.tree.itemDelegateForColumn(1).closeEditor.connect(lambda *_: QTimer.singleShot(0, self._drop_empty_reply))
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.edit_item(it))
        self.tree.itemCollapsed.connect(lambda it: it.parent() is None and self._collapsed.add(it.text(0)))
        self.tree.itemExpanded.connect(lambda it: it.parent() is None and self._collapsed.discard(it.text(0)))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.installEventFilter(self)
        lay.addWidget(self.tree)
        self.empty = QLabel(""); self.empty.setObjectName("dim"); self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide(); lay.addWidget(self.empty)
        self.reload()

    NARROW = 340   # 이보다 좁으면 종류·내용 두 열만

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < self.NARROW
        for c in (2, 3):
            self.tree.setColumnHidden(c, narrow)

    @staticmethod
    def _day(mod: str) -> str:
        s = (mod or "").removeprefix("D:")
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 and s[:8].isdigit() else "날짜 없음"

    @staticmethod
    def _when(mod: str) -> str:
        """목록의 날짜 칸: 오늘 고친 것은 시:분, 아니면 yyyy/MM/dd."""
        s = (mod or "").removeprefix("D:")
        if len(s) < 12 or not s[:12].isdigit():
            return ""
        return f"{s[8:10]}:{s[10:12]}" if s[:8] == time.strftime("%Y%m%d") else f"{s[:4]}/{s[4:6]}/{s[6:8]}"

    def _passes(self, a: A.AnnotInfo) -> bool:
        if not self.flt:
            return True
        from .annot_filter import color_key
        return all(v in self.flt[k] for k, v in (("authors", a.author or ""), ("kinds", a.kind_ko),
                                                  ("colors", color_key(a.color))) if k in self.flt)

    def reload(self):
        keep = {d[1] for d in (it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()) if d}
        self._syncing = True
        try:
            self._reload(keep)
        finally:
            self._syncing = False

    def _reload(self, keep: set[int]):
        self.tree.clear()
        q = self.find.text().strip().lower()
        how = self.group.currentIndex()
        every = [a for a in A.list_annots(self.doc) if a.kind not in ("Link", "Widget", "Popup")]
        self._all = every
        replies: dict[int, list[A.AnnotInfo]] = {}
        rows = []
        for a in every:
            if a.irt:
                replies.setdefault(a.irt, []).append(a)
                continue
            if not self._passes(a):
                continue
            if q and q not in (a.content or "").lower() and q not in (a.author or "").lower() \
                    and q not in (a.marked or "").lower():
                continue
            head = (f"{a.page + 1}쪽", self._day(a.modified), a.author or "작성자 없음", a.kind_ko)[how]
            key = (a.page, "", a.author or "￿", a.kind_ko)[how]
            if how == 1:
                key = "" if head == "날짜 없음" else head
            rows.append((key, head, a))
        # 날짜는 새것부터, 나머지는 오름차순. 같은 묶음 안은 문서 차례(쪽 → 쪽 안 차례)
        rows.sort(key=lambda r: r[0], reverse=(how == 1))
        groups: dict[str, QTreeWidgetItem] = {}
        lock = icons.icon("lock", 14)
        for _key, head, a in rows:
            if head not in groups:
                pit = QTreeWidgetItem([head, "", "", ""])
                pit.setFlags(pit.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.tree.addTopLevelItem(pit)
                groups[head] = pit
            text = f"「{a.marked}」 {a.content}".strip() if a.marked else a.content
            it = QTreeWidgetItem([a.kind_ko, text, a.author, self._when(a.modified)])
            it.setData(1, Qt.ItemDataRole.EditRole, a.content)
            it.setText(1, text.replace("\n", " "))
            if how != 0:
                it.setText(0, f"{a.page + 1}쪽 {a.kind_ko}" if how != 3 else f"{a.page + 1}쪽")
            if a.locked:
                it.setIcon(0, lock)
            it.setData(0, Qt.ItemDataRole.UserRole, (a.page, a.xref, a.rect, 0, a.locked))
            groups[head].addChild(it)
            self._add_replies(it, a, replies)
            if a.xref in keep:
                it.setSelected(True)
        for head, pit in groups.items():
            pit.setExpanded(head not in self._collapsed)
            for k in range(pit.childCount()):
                pit.child(k).setExpanded(True)
        self.filter_btn.setChecked(bool(self.flt))
        self.empty.setText("찾은 주석 없음" if (q or self.flt) else "주석 없음")
        self.empty.setVisible(not rows)
        self.tree.setVisible(bool(rows))

    def _add_replies(self, parent: QTreeWidgetItem, a: A.AnnotInfo, replies: dict):
        """답글은 원 주석 아래 들여 쓴 줄로(답글의 답글은 한 칸 더)."""
        for r in replies.get(a.xref, ()):
            it = QTreeWidgetItem(["답글", r.content.replace("\n", " "), r.author, self._when(r.modified)])
            it.setData(1, Qt.ItemDataRole.EditRole, r.content)
            it.setText(1, r.content.replace("\n", " "))
            it.setData(0, Qt.ItemDataRole.UserRole, (r.page, r.xref, a.rect, a.xref, a.locked))
            parent.addChild(it)
            self._add_replies(it, r, replies)

    # ── 고르기(문서와 서로 따라감) ───────────────────────────────
    def _selected(self):
        if self._syncing:
            return
        its = [it for it in self.tree.selectedItems() if it.data(0, Qt.ItemDataRole.UserRole)]
        if len(its) == 1:
            it = its[0]
            while it.parent() is not None and it.parent().data(0, Qt.ItemDataRole.UserRole):
                it = it.parent()
            d = it.data(0, Qt.ItemDataRole.UserRole)
            self.pickAnnot.emit(d[0], d[1])

    def _item_of(self, xref: int) -> QTreeWidgetItem | None:
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            d = it.data(0, Qt.ItemDataRole.UserRole)
            if d and d[1] == xref:
                return it
            stack.extend(it.child(k) for k in range(it.childCount()))
        return None

    def follow(self, sel):
        """뷰어에서 고른 주석을 목록에서도 고른다(목록이 다시 뷰어를 부르지 않게)."""
        self._syncing = True
        try:
            it = self._item_of(sel.xref) if sel is not None and getattr(sel, "kind", "") == "annot" else None
            if it is None:
                self.tree.clearSelection()
                return
            if not it.isSelected() or len(self.tree.selectedItems()) != 1:
                self.tree.clearSelection(); it.setSelected(True)
            if it.parent() is not None:
                it.parent().setExpanded(True)
            self.tree.setCurrentItem(it, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.tree.scrollToItem(it)
        finally:
            self._syncing = False

    # ── 그 자리 편집 · 답글 ───────────────────────────────────
    def edit_item(self, it: QTreeWidgetItem | None):
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if not d or d[4]:   # 잠긴 주석(과 그 답글)은 못 고친다
            return
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self.tree.editItem(it, 1)

    def _is_new_reply(self, idx) -> bool:
        d = self.tree.itemFromIndex(idx).data(0, Qt.ItemDataRole.UserRole) if idx.isValid() else None
        return bool(d) and d[1] == 0

    def add_reply(self, it: QTreeWidgetItem):
        """줄 아래 빈 답글 줄을 열어 바로 쓰게 한다. 비운 채 닫으면 없어진다."""
        d = it.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[4]:
            return
        new = QTreeWidgetItem(["답글", "", "", ""])
        new.setData(0, Qt.ItemDataRole.UserRole, (d[0], 0, d[2], d[1], False))
        it.addChild(new); it.setExpanded(True)
        self.edit_item(new)

    def _commit_edit(self, idx, text: str):
        it = self.tree.itemFromIndex(idx)
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if not d:
            return
        page, xref, _r, parent, _l = d
        if xref == 0:
            if text.strip():
                A.add_reply(self.doc, page, parent, text)
            else:
                return
        elif text == (it.data(1, Qt.ItemDataRole.EditRole) or ""):
            return
        elif parent:
            A.update_reply(self.doc, page, xref, text)
        else:
            A.update(self.doc, page, xref, content=text)
        QTimer.singleShot(0, lambda: (self.reload(), self.changed.emit()))

    def _drop_empty_reply(self):
        """비운 채 닫은 답글 줄을 걷는다."""
        for it in list(self._items()):
            d = it.data(0, Qt.ItemDataRole.UserRole)
            if d and d[1] == 0 and it.parent() is not None:
                it.parent().removeChild(it)

    def _items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(k) for k in range(it.childCount()))

    # ── 지우기 · 메뉴 ─────────────────────────────────────────
    def delete_selected(self):
        """고른 줄을 확인 없이 지운다(잠긴 것은 남는다). 답글은 답글만."""
        ds = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()]
        ds = [d for d in ds if d and d[1] and not d[4]]
        if not ds:
            return
        for page, xref, _r, parent, _l in ds:
            if parent:
                A.delete_reply(self.doc, page, xref)
            else:
                A.delete(self.doc, page, xref)
        self.reload(); self.changed.emit()

    def expand_all(self):
        self._collapsed.clear(); self.tree.expandAll()

    def collapse_all(self):
        self._collapsed = {self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())}
        self.tree.collapseAll()

    def eventFilter(self, obj, e):
        if obj is self.tree and e.type() == QEvent.Type.KeyPress and self.tree.state() != QAbstractItemView.State.EditingState:
            if e.key() == Qt.Key.Key_Delete:
                self.delete_selected(); return True
            if e.key() == Qt.Key.Key_F2:
                self.edit_item(self.tree.currentItem()); return True
        return super().eventFilter(obj, e)

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        m = QMenu(self)
        if d:
            if not it.isSelected():
                self.tree.clearSelection(); it.setSelected(True)
            m.addAction("고치기", lambda: self.edit_item(it)).setEnabled(not d[4])
            m.addAction("답글", lambda: self.add_reply(it)).setEnabled(not d[4])
            m.addAction("삭제", self.delete_selected).setEnabled(not d[4])
        else:
            m.addAction("모두 펼치기", self.expand_all)
            m.addAction("모두 접기", self.collapse_all)
        m.exec(self.tree.mapToGlobal(pos))

    def open_filter(self):
        from .annot_filter import AnnotFilterDialog, color_key
        every = getattr(self, "_all", [])
        authors = sorted({a.author or "" for a in every if not a.irt})
        kinds = sorted({a.kind_ko for a in every if not a.irt})
        colors = sorted({color_key(a.color) for a in every if not a.irt})
        dlg = AnnotFilterDialog(self, authors, kinds, colors, self.flt)
        if dlg.exec():
            self.flt = dlg.values()
        self.reload()

    def _action(self, key: str):
        if key == "delete_all":
            if QMessageBox.question(self, "주석 모두 삭제", "이 문서의 주석을 전부 지울까요?") == QMessageBox.StandardButton.Yes:
                A.delete_all(self.doc); self.reload(); self.changed.emit()
        else:
            self.importExport.emit(key)


ATTACH_BLOCKED = (".exe", ".bat", ".cmd", ".js", ".vbs", ".ps1", ".msi", ".scr")   # 늘 막고 저장만 된다


class AttachmentPanel(_Prefs, QWidget):
    """첨부 파일: 문서 첨부 + 쪽에 붙은 첨부(「N 페이지」 아래). 두 번 누르면 열기.
    PDF 첨부는 앱 새 탭으로, 다른 파일은 이번만·항상·안 함을 묻고(attach_allow 에 확장자별 기억), 실행 파일은 막는다."""
    changed = Signal()
    openPdf = Signal(str)   # 임시 폴더에 꺼낸 PDF 경로
    KIND = Qt.ItemDataRole.UserRole       # "doc" · "annot" · "page"(묶음 머리)
    KEY = Qt.ItemDataRole.UserRole + 1    # 문서 첨부 이름 또는 (쪽, xref)
    FILE = Qt.ItemDataRole.UserRole + 2   # 보여 줄 파일 이름

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        head = panel_head("첨부 파일", (("more", "더 보기", self._more), ("open", "열기", self.open_current),
                                       ("add", "첨부 파일 추가", self.add), ("edit", "설명 편집", self.edit_desc),
                                       ("delete", "삭제", self.delete_current), ("saveas", "다른 이름으로 저장", self.save)))
        (self.more_btn, self.open_btn, self.add_btn, self.desc_btn, self.delete_btn,
         self.save_btn) = head.findChildren(QToolButton)
        lay.addWidget(head)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); _tidy_tree(self.tree, 0)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda pos: self._menu(self.tree.mapToGlobal(pos)))
        self.tree.itemDoubleClicked.connect(lambda it, _: self.open_current() if it.data(0, self.KIND) != "page" else None)
        self.tree.currentItemChanged.connect(lambda *_: self._sync_buttons())
        lay.addWidget(self.tree)
        self.reload()

    # ── 목록 ──
    @staticmethod
    def _label(filename: str, size: int, desc: str) -> str:
        sz = f"{size} B" if size < 1024 else f"{size / 1024:.0f} KB"
        return f"{filename}  ({sz})" + (f" · {desc}" if desc else "")

    def page_attachments(self) -> list[dict]:
        """쪽에 붙은 첨부(FileAttachment 주석)."""
        out = []
        with self.doc.lock:
            for i in range(self.doc.page_count):
                for a in self.doc.fitz[i].annots(types=[fitz.PDF_ANNOT_FILE_ATTACHMENT]) or []:
                    try:
                        fi = A.attach_info(a)
                    except Exception:  # noqa: BLE001
                        continue
                    name = fi.get("filename") or "첨부"
                    out.append({"page": i, "xref": a.xref, "filename": name, "size": fi.get("length") or fi.get("size") or 0,
                                "desc": fi.get("description") or a.info.get("content", "")})
        return out

    def reload(self):
        self.tree.clear()
        for a in self.doc.attachments():
            desc = a["desc"] if a["desc"] and a["desc"] not in (a["filename"], a["name"]) else ""
            it = QTreeWidgetItem([self._label(a["filename"], a["size"], desc)])
            it.setData(0, self.KIND, "doc"); it.setData(0, self.KEY, a["name"]); it.setData(0, self.FILE, a["filename"])
            self.tree.addTopLevelItem(it)
        groups: dict[int, QTreeWidgetItem] = {}
        for a in self.page_attachments():
            g = groups.get(a["page"])
            if g is None:
                g = groups[a["page"]] = QTreeWidgetItem([f"{a['page'] + 1} 페이지"])
                g.setData(0, self.KIND, "page")
                self.tree.addTopLevelItem(g)
            it = QTreeWidgetItem([self._label(a["filename"], a["size"], a["desc"] if a["desc"] != a["filename"] else "")])
            it.setData(0, self.KIND, "annot"); it.setData(0, self.KEY, (a["page"], a["xref"])); it.setData(0, self.FILE, a["filename"])
            g.addChild(it)
        self.tree.expandAll()
        self._sync_buttons()

    def _current(self) -> QTreeWidgetItem | None:
        it = self.tree.currentItem()
        return it if it is not None and it.data(0, self.KIND) in ("doc", "annot") else None

    def _sync_buttons(self):
        on = self._current() is not None
        for b in (self.open_btn, self.desc_btn, self.delete_btn, self.save_btn):
            b.setEnabled(on)

    def _annot(self, key):
        """(쪽, 주석). 쪽을 같이 쥐고 있어야 주석이 쪽에서 떨어지지 않는다."""
        page = self.doc.fitz[key[0]]
        return page, page.load_annot(key[1])

    def data_of(self, it: QTreeWidgetItem) -> bytes:
        with self.doc.lock:
            if it.data(0, self.KIND) == "doc":
                return self.doc.fitz.embfile_get(it.data(0, self.KEY))
            _, a = self._annot(it.data(0, self.KEY))
            return a.get_file()

    # ── 일 ──
    def add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "첨부할 파일")
        done = False
        have = {a["filename"]: a["name"] for a in self.doc.attachments()}
        for p in paths:
            base = os.path.basename(p)
            if base in have:
                if QMessageBox.question(self, "첨부 파일", f"「{base}」 첨부 파일이 이미 있습니다. 바꾸시겠습니까?") \
                        != QMessageBox.StandardButton.Yes:
                    continue
                with open(p, "rb") as fh:
                    data = fh.read()
                self.doc.snapshot()
                with self.doc.lock:   # embfile_upd 는 내용 바꾸기가 깨져 있어(1.27) 지우고 같은 이름·설명으로 다시 넣는다
                    f = self.doc.fitz
                    desc = f.embfile_info(have[base]).get("description", "")
                    f.embfile_del(have[base])
                    f.embfile_add(have[base], data, filename=have[base], ufilename=base, desc=desc)
            else:
                self.doc.add_attachment(p)
            done = True
        if done:
            self.reload(); self.changed.emit()

    def save(self, *_):
        it = self._current()
        if not it:
            return
        out, _ = QFileDialog.getSaveFileName(self, "첨부 파일 저장", it.data(0, self.FILE) or "첨부")
        if out:
            with open(out, "wb") as fh:
                fh.write(self.data_of(it))

    def delete_current(self):
        it = self._current()
        if not it:
            return
        self.doc.snapshot()
        with self.doc.lock:
            if it.data(0, self.KIND) == "doc":
                self.doc.fitz.embfile_del(it.data(0, self.KEY))
            else:
                page, a = self._annot(it.data(0, self.KEY))
                page.delete_annot(a)
        self.reload(); self.changed.emit()

    def edit_desc(self):
        it = self._current()
        if not it:
            return
        with self.doc.lock:
            if it.data(0, self.KIND) == "doc":
                old = self.doc.fitz.embfile_info(it.data(0, self.KEY)).get("description", "")
            else:
                _page, a = self._annot(it.data(0, self.KEY))
                old = a.file_info.get("description") or a.info.get("content", "")
        text, ok = QInputDialog.getText(self, "설명 편집", "설명", text=old if old != it.data(0, self.FILE) else "")
        if not ok or text == old:
            return
        self.doc.snapshot()
        with self.doc.lock:
            if it.data(0, self.KIND) == "doc":
                self.doc.fitz.embfile_upd(it.data(0, self.KEY), desc=text)
            else:
                _page, a = self._annot(it.data(0, self.KEY))
                a.update_file(desc=text); a.set_info(content=text); a.update()
        self.reload(); self.changed.emit()

    def allow(self, ext: str) -> bool:
        """그 확장자를 열어도 되는지 묻는다(「항상」·「안 함」 은 attach_allow 에 기억)."""
        rule = (self.pref("attach_allow") or {}).get(ext)
        if rule == "always":
            return True
        if rule == "never":
            QMessageBox.information(self, "첨부 파일 열기", f"{ext} 첨부는 열지 않도록 정해 두었습니다. [다른 이름으로 저장]으로 꺼낼 수 있습니다.")
            return False
        box = QMessageBox(QMessageBox.Icon.Question, "첨부 파일 열기",
                          f"{ext} 첨부 파일을 엽니다. 믿을 수 있는 파일만 여십시오.", parent=self)
        once = box.addButton("이번만 허용", QMessageBox.ButtonRole.AcceptRole)
        always = box.addButton("항상 허용", QMessageBox.ButtonRole.AcceptRole)
        never = box.addButton("허용 안 함", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(once)
        box.exec()
        hit = box.clickedButton()
        if hit in (always, never):
            rules = dict(self.pref("attach_allow") or {})
            rules[ext] = "always" if hit is always else "never"
            self.set_pref("attach_allow", rules)
        return hit in (once, always)

    def open_current(self):
        import tempfile
        it = self._current()
        if not it:
            return
        name = os.path.basename(it.data(0, self.FILE) or "첨부") or "첨부"
        ext = os.path.splitext(name)[1].lower()
        if ext in ATTACH_BLOCKED:
            QMessageBox.warning(self, "첨부 파일 열기", "실행할 수 있는 첨부 파일은 열 수 없습니다. [다른 이름으로 저장]만 됩니다.")
            return
        if ext != ".pdf" and not self.allow(ext):
            return
        path = os.path.join(tempfile.mkdtemp(prefix="pdfmetry_attach_"), name)
        with open(path, "wb") as fh:
            fh.write(self.data_of(it))
        if ext == ".pdf":
            self.openPdf.emit(path)
        else:
            try:
                os.startfile(path)  # noqa: S606  사용자가 허용한 형식만
            except OSError as e:
                QMessageBox.warning(self, "첨부 파일 열기", f"열지 못했습니다: {e}")

    def build_menu(self) -> QMenu:
        on = self._current() is not None
        m = QMenu(self)
        for label, fn, en, name in (("첨부 파일 열기", self.open_current, on, "open"), ("첨부 파일 저장", self.save, on, "save"),
                                    ("첨부 파일 추가", self.add, True, "add"), ("설명 편집", self.edit_desc, on, "desc"),
                                    ("삭제", self.delete_current, on, "delete"), (None, None, None, None),
                                    ("모두 펼치기", self.tree.expandAll, True, "expand"),
                                    ("모두 접기", self.tree.collapseAll, True, "collapse")):
            if label is None:
                m.addSeparator(); continue
            a = m.addAction(label, fn); a.setEnabled(en); a.setObjectName(name)
        return m

    def _menu(self, gpos):
        self.build_menu().exec(gpos)

    def _more(self):
        b = self.more_btn
        self._menu(b.mapToGlobal(b.rect().bottomLeft()))


class LayerPanel(QWidget):
    """레이어: 체크로 보이기(잠김·상위 꺼짐이면 흐리게 막고 이름 뒤에 까닭), 이름 바꾸기·삭제·속성,
    ⋯ 메뉴에서 모든 쪽 / 보이는 쪽 레이어 · 처음 상태로 · 모두 펼치기·접기."""
    changed = Signal()  # 보기만 바뀜(문서는 그대로)
    edited = Signal()   # 레이어 정의가 바뀜(문서 고침)
    XREF = Qt.ItemDataRole.UserRole + 1
    NAME = Qt.ItemDataRole.UserRole + 2
    get_current_page = staticmethod(lambda: 0)   # DocTab 이 보는 쪽으로 바꿔 끼운다

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.scope = "all"   # "all" 모든 쪽 · "visible" 보이는 쪽
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        head = panel_head("레이어", (("more", "더 보기", self._more),))
        self.more_btn = head.findChild(QToolButton)
        lay.addWidget(head)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); _tidy_tree(self.tree, 0)
        self.tree.itemChanged.connect(self._toggle)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda pos: self._menu(self.tree.mapToGlobal(pos), self.tree.itemAt(pos)))
        lay.addWidget(self.tree)
        self.empty = QLabel("이 문서에는 레이어가 없습니다"); self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(self.empty)
        self.reload()

    def reload(self):
        from ..engine import outline as OL
        self.tree.blockSignals(True)
        self.tree.clear()
        rows = OL.layer_rows(self.doc)
        if self.scope == "visible" and rows:
            used = OL.page_layers(self.doc, max(0, min(self.doc.page_count - 1, self.get_current_page())))
            rows = [r for r in rows if r["xref"] in used]
        self.empty.setVisible(not rows)
        self.tree.setVisible(bool(rows))
        stack: list[tuple[int, QTreeWidgetItem]] = []
        dim = QColor(theme.CUR["TEXT_DIM"])
        for r in rows:
            why = " (잠김)" if r["locked"] else " (상위 꺼짐)" if r["parent_off"] else ""
            it = QTreeWidgetItem([r["name"] + why])
            it.setData(0, Qt.ItemDataRole.UserRole, r["number"])
            it.setData(0, self.XREF, r["xref"])
            it.setData(0, self.NAME, r["name"])
            if why:   # 못 바꾸는 체크칸은 흐리게(까닭은 이름 뒤에 그대로)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                it.setForeground(0, dim)
            else:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if r["on"] else Qt.CheckState.Unchecked)
            while stack and stack[-1][0] >= r["depth"]:
                stack.pop()
            (stack[-1][1].addChild if stack else self.tree.addTopLevelItem)(it)
            stack.append((r["depth"], it))
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _toggle(self, it, _col=0):
        self.doc.set_layer(it.data(0, Qt.ItemDataRole.UserRole), it.checkState(0) == Qt.CheckState.Checked)
        self.changed.emit()
        QTimer.singleShot(0, self.reload)  # 묶음(라디오) 레이어는 하나를 켜면 다른 것이 꺼진다

    def set_scope(self, scope: str):
        self.scope = scope
        self.reload()

    def rename(self, it: QTreeWidgetItem):
        from ..engine import outline as OL
        name, ok = QInputDialog.getText(self, "레이어 이름 바꾸기", "이름", text=it.data(0, self.NAME))
        if ok and name.strip() and name.strip() != it.data(0, self.NAME) and it.data(0, self.XREF) is not None:
            OL.rename_layer(self.doc, it.data(0, self.XREF), name)
            self.edited.emit()

    def delete(self, it: QTreeWidgetItem):
        from ..engine import outline as OL
        if it.data(0, self.XREF) is None:
            return
        if QMessageBox.question(self, "레이어 삭제", f"「{it.data(0, self.NAME)}」 레이어를 삭제할까요? 레이어로 묶였던 내용은 늘 보입니다.") \
                != QMessageBox.StandardButton.Yes:
            return
        OL.delete_layer(self.doc, it.data(0, self.XREF))
        self.edited.emit()

    def open_props(self, it: QTreeWidgetItem):
        from ..engine import outline as OL
        from .dlg_layer import LayerPropsDialog
        if it.data(0, self.XREF) is None:
            return
        dlg = LayerPropsDialog(self, OL.layer_props(self.doc, it.data(0, self.XREF)))
        if dlg.exec():
            OL.set_layer_props(self.doc, it.data(0, self.XREF), dlg.values())
            self.edited.emit()

    def reset(self):
        from ..engine import outline as OL
        OL.reset_layers(self.doc)
        self.reload(); self.changed.emit()

    def build_menu(self, it: QTreeWidgetItem | None, options: bool = False) -> QMenu:
        m = QMenu(self)

        def act(label, fn, on=True, name=""):
            a = m.addAction(label, fn); a.setEnabled(bool(on)); a.setObjectName(name)
            return a
        if options:
            for label, scope in (("모든 페이지의 레이어 목록", "all"), ("보이는 페이지의 레이어 목록", "visible")):
                a = act(label, lambda s=scope: self.set_scope(s), True, "scope_" + scope)
                a.setCheckable(True); a.setChecked(self.scope == scope)
            m.addSeparator()
            act("처음 상태로 되돌리기", self.reset, True, "reset")
            act("모두 펼치기", self.tree.expandAll, True, "expand")
            act("모두 접기", self.tree.collapseAll, True, "collapse")
            m.addSeparator()
        if it is not None and not options:
            a = act("레이어 보이기", lambda: it.setCheckState(0, Qt.CheckState.Unchecked if it.checkState(0) == Qt.CheckState.Checked
                                                             else Qt.CheckState.Checked),
                    bool(it.flags() & Qt.ItemFlag.ItemIsUserCheckable), "show")
            a.setCheckable(True); a.setChecked(it.checkState(0) == Qt.CheckState.Checked)
            act("이름 바꾸기", lambda: self.rename(it), True, "rename")
        act("레이어 삭제", lambda: self.delete(it), it is not None, "delete")
        act("레이어 속성", lambda: self.open_props(it), it is not None, "props")
        return m

    def _menu(self, gpos, it=None, options: bool = False):
        self.build_menu(it, options).exec(gpos)

    def _more(self):
        b = self.more_btn
        self._menu(b.mapToGlobal(b.rect().bottomLeft()), self.tree.currentItem(), options=True)


class FieldPanel(QWidget):
    """양식 칸 목록(오른쪽 패널용)."""
    gotoField = Signal(int, object)
    changed = Signal()

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(panel_head("양식 칸"))
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["이름", "종류", "값"]); _tidy_tree(self.tree, 0)
        self.tree.itemClicked.connect(lambda it, _: self.gotoField.emit(*it.data(0, Qt.ItemDataRole.UserRole)) if it.data(0, Qt.ItemDataRole.UserRole) else None)
        lay.addWidget(self.tree)
        self.empty = QLabel("이 문서에는 양식 칸이 없습니다"); self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(self.empty)
        self.reload()

    def reload(self):
        self.tree.clear()
        fields = A.fields(self.doc)
        self.empty.setVisible(not fields); self.tree.setVisible(bool(fields))
        for f in fields:
            it = QTreeWidgetItem([f["name"], A.FIELD_TYPE_KO.get(f["type"].lower().replace("button", "button"), f["type"]), "꺼짐" if str(f["value"]) == "Off" else str(f["value"] or "")])
            it.setData(0, Qt.ItemDataRole.UserRole, (f["page"], f["rect"]))
            self.tree.addTopLevelItem(it)
