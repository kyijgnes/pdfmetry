"""「구성」 탭: 쪽 썸네일 격자. 끌어 순서 바꾸기, 여러 장 골라 회전·삭제·내보내기·끼우기."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from ..engine.document import Document
from .sidebar import LazyThumbs, PageGrid, fill_grid, kept_thumbs


# 썸네일 비율(%) 열한 단계, 처음 20%. 100% 는 A4 폭(210mm)을 96dpi 로 그린 794px
ZOOM_STEPS = (10, 12.5, 15, 20, 25, 30, 40, 50, 65, 80, 100)
FULL_W = 794


class OrganizeView(QWidget):
    reordered = Signal(list)
    pagesAction = Signal(str, list)
    openPage = Signal(int)
    selectionChanged = Signal(list)   # 고른 쪽(0부터)
    zoomChanged = Signal(bool, bool)  # 더 줄일 수 있나, 더 키울 수 있나

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.step = ZOOM_STEPS.index(20)
        self.thumb_w = self._width()
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 16, 16, 16)
        self.list = PageGrid(14)
        self.list.setStyleSheet("QListWidget{background: transparent;}")
        self.list.reordered.connect(self.reordered)
        self.list.itemDoubleClicked.connect(lambda it: self.openPage.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemSelectionChanged.connect(lambda: self.selectionChanged.emit(self.selected_pages()))
        lay.addWidget(self.list)
        self.thumbs = LazyThumbs(self.list, doc)
        self.reload()

    def _width(self) -> int:
        return round(FULL_W * ZOOM_STEPS[self.step] / 100)

    def zoom(self, step: int):
        """다음·앞 단계로. 양 끝에서는 그대로이고 zoomChanged 로 그쪽 단추를 끈다."""
        k = max(0, min(len(ZOOM_STEPS) - 1, self.step + step))
        if k != self.step:
            self.step = k
            self.thumb_w = self._width()
            self.reload()
        self.zoomChanged.emit(*self.can_zoom())

    def can_zoom(self) -> tuple[bool, bool]:
        return self.step > 0, self.step < len(ZOOM_STEPS) - 1

    def percent(self) -> float:
        return ZOOM_STEPS[self.step]

    def selected_pages(self) -> list[int]:
        return sorted(it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems())

    def select_pages(self, pages: list[int]):
        want = set(pages)
        self.list.blockSignals(True)   # 한 장씩 고를 때마다 selectionChanged 가 나가지 않게, 끝에 한 번
        self.list.clearSelection()
        first = None
        for k in range(self.list.count()):
            it = self.list.item(k)
            if it.data(Qt.ItemDataRole.UserRole) in want:
                it.setSelected(True)
                first = first or it
        self.list.blockSignals(False)
        if first:
            self.list.scrollToItem(first)
        self.selectionChanged.emit(self.selected_pages())

    def reload(self, mapping: list[int | None] | None = None, dirty=()):
        keep = kept_thumbs(self.thumbs, mapping, dirty)
        fill_grid(self.list, self.doc, self.thumb_w, 20, 40)
        self.thumbs.start(self.thumb_w, self.devicePixelRatioF(), keep)

    def _menu(self, pos):
        pages = self.selected_pages()
        m = QMenu(self)
        for label, key in (("왼쪽 회전", "rotate_left"), ("오른쪽 회전", "rotate_right"), (None, None),
                           ("빈 페이지 끼우기", "insert_blank"), ("PDF 에서 끼우기", "insert_pdf"), ("바꾸기", "replace"),
                           ("복제", "duplicate"), (None, None), ("내보내기", "extract"), ("자르기", "crop"),
                           ("크기", "resize"), ("삭제", "delete")):
            if label is None:
                m.addSeparator()
            else:
                a = m.addAction(label, lambda k=key: self.pagesAction.emit(k, pages))
                a.setEnabled(bool(pages) or key in ("insert_blank", "insert_pdf"))
        m.exec(self.list.mapToGlobal(pos))

    def close(self):
        self.thumbs.stop()
