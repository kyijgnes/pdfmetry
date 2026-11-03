"""창 하나: 문서 탭 · 리본 · 왼쪽 막대/패널 · 보기(뷰어 또는 구성 격자) · 오른쪽 속성 · 상태줄 · 시작 화면."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
import webbrowser

import fitz
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent, QColor, QFont, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtPrintSupport import QPrinterInfo
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QSlider, QSplitter, QStackedWidget,
                               QTabBar, QToolButton, QVBoxLayout, QWidget)

from ..engine import annot_shapes as SH
from ..engine import annot_style as S
from ..engine import annots as A
from ..engine import pages as P
from ..engine import stamps as ST
from ..engine.document import Document, DocumentError, FileLocked, NeedsPassword
from . import dialogs as D
from . import dlg_organize as O
from .dlg_deco import run_decorate
from .dlg_prefs import PreferencesDialog
from .dlg_properties import PropertiesDialog
from .dlg_measure import MeasureDialog
from .dlg_signature import SignatureDialog
from . import dlg_pagebox as PB
from . import dlg_split as DS
from . import dlg_stamp as DST
from . import dlg_password as PW
from . import fileops as F
from . import icons, tab_detach, theme
from . import shortcuts as K
from .inline_text import InlineText
from .inline_annot import InlineAnnot, NotePopup, comment_style
from .organize import OrganizeView
from .props import PropsPanel
from .quick_style import QuickStyle, eraser_radius, has_quick_style, style_key
from .ribbon import ANNOT_TOOLS, LABELS, TOOL_KEYS, Ribbon, keep_tool
from .sidebar import AnnotPanel, AttachmentPanel, BookmarkPanel, FieldPanel, LayerPanel, SearchPanel, ThumbnailPanel
from .start import StartPage
from .update import UpdateUI
from .viewer import PageView
from PySide6.QtCore import QRegularExpression  # noqa: E402  (보기 탭: 배율 칸 거르기)
from PySide6.QtGui import QRegularExpressionValidator  # noqa: E402
from . import fullscreen, tile as tiler  # noqa: E402
from . import dlg_adv_search as ADV, search_float as SF  # noqa: E402  (검색 및 바꾸기 판 · 고급 검색)
from .viewer import BG_MODES as VIEW_BG, ZOOM_FITS, ZOOM_MAX, ZOOM_MIN, pct_text  # noqa: E402

from ..appinfo import APP_NAME, ICON, VERSION, copy_old_data, data_dir  # noqa: E402

SETTINGS = os.path.join(data_dir(), "settings.json")
PDF_FILTER = "PDF 파일 (*.pdf);;모든 파일 (*)"
# 리본 단추 앞머리 → 꾸미기 종류(「watermark_update」 는 워터마크 업데이트)
DECO_KEYS = {"watermark": "watermark", "background": "background", "headerfooter": "header_footer",
             "pagenumber": "page_number", "bates": "bates"}
OPEN_FILTER = "PDF·이미지·문서 (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp *.xps *.epub *.txt *.svg *.cbz);;PDF 파일 (*.pdf);;모든 파일 (*)"


def _engine(name: str):
    """변환·도구·OCR 엔진은 따로 짓고 있어서 없을 수도 있다."""
    try:
        import importlib
        return importlib.import_module(f"app.engine.{name}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"{name} 엔진을 아직 쓸 수 없습니다 ({e})") from e


def load_settings() -> dict:
    copy_old_data()
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_settings(s: dict):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


class DocTabBar(QTabBar):
    """문서 탭 줄. 가운데 단추로 누르면 그 탭을 닫는다. 탭을 줄 위아래로 끌어내면 tornOff(그 탭 번호)."""
    tornOff = Signal(int)
    TEAR = 24   # 탭 줄 위아래로 이만큼 벗어나면 떼어 낸다

    def __init__(self):
        super().__init__()
        self._press = None

    def mousePressEvent(self, e):
        p = e.position().toPoint()
        self._press = p if e.button() == Qt.MouseButton.LeftButton and self.tabAt(p) >= 0 else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton and (p.y() < -self.TEAR or p.y() > self.height() + self.TEAR):
            # 탭 줄 안 순서 바꾸기를 끝내고(놓은 것으로 쳐서 제자리에 둔다) 끌어 떼기로 넘긴다
            rel = QMouseEvent(QEvent.Type.MouseButtonRelease, e.position(), e.globalPosition(), Qt.MouseButton.LeftButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            super().mouseReleaseEvent(rel)
            self._press = None
            self.tornOff.emit(self.currentIndex())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        if e.button() == Qt.MouseButton.MiddleButton:
            i = self.tabAt(e.position().toPoint())
            if i >= 0:
                self.tabCloseRequested.emit(i)
                return
        super().mouseReleaseEvent(e)


class DocTab(QWidget):
    """문서 하나의 화면 묶음."""
    def __init__(self, doc: Document, win: "MainWindow"):
        super().__init__()
        self.doc = doc
        self.win = win
        self.viewer = PageView(doc)
        self.organize = OrganizeView(doc)
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.viewer); self.view_stack.addWidget(self.organize)
        self.thumbs = ThumbnailPanel(doc)
        self.bookmarks = BookmarkPanel(doc, lambda: self.viewer.current)
        self.annots = AnnotPanel(doc)
        self.attachments = AttachmentPanel(doc)
        self.search = SearchPanel(doc)
        self.layers = LayerPanel(doc)
        self.fieldlist = FieldPanel(doc)
        self.side = QStackedWidget(); self.side.setObjectName("sidepanel"); self.side.setFixedWidth(240)
        self.panels = {"thumbs": self.thumbs, "bookmark": self.bookmarks, "comments": self.annots, "attach": self.attachments,
                       "search": self.search, "layers": self.layers, "fields": self.fieldlist}
        for p in self.panels.values():
            self.side.addWidget(p)
        self.side.hide()
        self.props = PropsPanel(self.viewer.props, self.viewer)
        self.props.autosave = lambda: self.win.prefs.get("annot_autosave_style", True)
        self.props.hide()
        self.warn = QLabel(""); self.warn.setObjectName("warn"); self.warn.hide()
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self.warn)
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(0)
        row.addWidget(self.side); row.addWidget(self.view_stack, 1); row.addWidget(self.props)
        root.addLayout(row)
        self.side_key = ""
        # 신호
        self.thumbs.pageClicked.connect(self.viewer.goto)
        self.thumbs.reordered.connect(self.reorder)
        # 탭이 다른 창으로 옮겨 가도 그때의 창(self.win)을 부르게 람다로 잇는다
        self.thumbs.pagesAction.connect(lambda *a: self.win.pages_action(*a))
        self.organize.reordered.connect(self.reorder)
        self.organize.pagesAction.connect(lambda *a: self.win.pages_action(*a))
        self.organize.openPage.connect(lambda i: (self.win.ribbon.set_tab("home"), self.viewer.goto(i)))
        self.organize.selectionChanged.connect(lambda pg: self.win.cur() is self and self.win.ribbon.show_range(pg, doc.page_count, P.labels(doc)))
        self.organize.zoomChanged.connect(lambda *a: self.win.sync_thumb_zoom(*a))
        self.bookmarks.gotoPage.connect(self.viewer.goto)
        self.bookmarks.changed.connect(lambda: self.win.mark_modified())
        self.annots.pickAnnot.connect(self.pick_annot)
        self.viewer.selectionChanged.connect(self.annots.follow)
        self.annots.changed.connect(lambda: self.changed(None))
        self.annots.importExport.connect(lambda *a: self.win.annots_import_export(*a))
        self.attachments.changed.connect(lambda: self.win.mark_modified())
        self.search.hitsChanged.connect(self.show_hits)
        self.search.replaced.connect(lambda pages: self.changed(pages))
        self.search.pickAnnot.connect(self.pick_annot)
        self.find_float = None   # 검색 및 바꾸기 떠 있는 판(Ctrl+T, 처음 열 때 만든다)
        self.layers.changed.connect(self.view_changed)
        self.fieldlist.gotoField.connect(lambda pg, r: self.viewer.goto(pg, r.y0))
        self.viewer.pageChanged.connect(self.thumbs.set_current)
        self.viewer.selectionChanged.connect(self.props.show_selection)
        self.props.applyToSel.connect(self.apply_props)
        self.props.deleteSel.connect(self.viewer.delete_selection)
        self.props.changed.connect(lambda d: None)
        if doc.fitz.is_encrypted or doc.password:
            self.warn.setText("암호 걸린 문서 · 저장해도 같은 열기 암호 유지"); self.warn.show()

    def show_side(self, key: str):
        if self.side_key == key and self.side.isVisible():
            self.side.hide(); self.side_key = ""
            return
        self.side_key = key
        self.side.setCurrentWidget(self.panels[key]); self.side.show()
        if key == "search":
            self.search.focus()

    def changed(self, pages: list[int] | None, structural: bool = False, mapping: list[int | None] | None = None):
        """문서가 바뀌었다: 뷰어·썸네일·목록을 새로 그린다.
        structural 일 때 mapping[새 쪽] = 옛 쪽 을 주면 안 바뀐 쪽 그림은 옮겨 쓰고 pages(새 쪽 번호)만 다시 그린다.
        mapping 없이 pages 만 주면 쪽 수는 그대로이고 그 쪽만 바뀐 것(회전)."""
        if structural and mapping is None and pages is not None:
            mapping = list(range(self.doc.page_count))
        dirty = set(pages or ())
        self.viewer.refresh_pages(pages, structural=structural, mapping=mapping)
        if structural:
            self.thumbs.reload(mapping, dirty); self.organize.reload(mapping, dirty)
        else:
            self.thumbs.refresh_pages(pages)
            self.organize.thumbs.forget(pages)   # 숨어 있으면 안 그리고, 다음에 보일 때 그린다
        self.annots.reload(); self.fieldlist.reload(); self.bookmarks.reload(); self.attachments.reload(); self.layers.reload()
        self.search.doc_changed(pages, structural)   # 찾아 둔 결과 자리를 바뀐 문서에 맞춘다
        if self.find_float is not None: self.find_float.doc_changed()
        self.win.mark_modified()

    def view_changed(self):
        """보기 설정(레이어 켜고 끄기)만 바뀌었다: 다시 그리되 고친 문서로 치지 않는다."""
        self.viewer.refresh_pages(None)
        self.thumbs.refresh_pages(None)
        self.organize.thumbs.forget(None)

    def reorder(self, order: list[int]):
        P.reorder(self.doc, order)
        self.viewer.clear_selection()
        self.changed(None, structural=True, mapping=list(order))

    def show_hits(self, hits, current, jump: bool | None = None):
        """쪽 위 검색 강조. jump 를 안 주면 검색 패널의 뜻(문서를 고쳐 다시 찾았으면 제자리)을 따른다."""
        self.viewer.search_hits = hits
        self.viewer.search_current = current
        if 0 <= current < len(hits) and (self.search.jump if jump is None else jump):
            pg, rs = hits[current]
            r = rs[0] if isinstance(rs, list) else rs
            self.viewer.goto(pg, max(0, r.y0 - 100))
        self.viewer.viewport().update()

    def pick_annot(self, page: int, xref: int):
        """주석 목록에서 고른 주석: 그 자리로 가서 문서에서도 고른다."""
        from .viewer import Selection
        a = A.get(self.doc, page, xref)
        if a is None:
            return
        v = self.viewer
        v.goto(page, a.rect.y0)
        v.sel = Selection("annot", page, xref, fitz.Rect(a.rect), text=a.info.get("content", ""),
                          extra={"kind": a.type[1], "kind_ko": A.KIND_NAMES.get(a.type[1], a.type[1]),
                                 "colors": A.annot_colors(self.doc, a), "opacity": a.opacity,
                                 "width": (a.border or {}).get("width", 1)})
        v.selectionChanged.emit(v.sel)
        v.viewport().update()

    def apply_props(self, d: dict):
        s = self.viewer.sel
        if s and s.kind == "annot":
            new = A.update(self.doc, s.page, s.xref, **d)  # 속성 패널이 그 종류에 맞는 인자만 준다
            if new:
                s.xref = new
            self.changed([s.page])

    def close(self):
        self.viewer.close(); self.thumbs.close(); self.organize.close(); self.search.close()
        self.doc.close()


class MainWindow(QMainWindow):
    def __init__(self, settings: dict | None = None):
        """settings: 같은 프로그램 안 다른 창과 함께 쓸 설정(탭을 떼어 만든 창). 없으면 파일에서 읽는다."""
        super().__init__()
        self.settings = settings if settings is not None else load_settings()
        self.settings.setdefault("recent", []); self.settings.setdefault("favorites", []); self.settings.setdefault("prefs", {})
        self.prefs = self.settings["prefs"]
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 820)
        self.setWindowIcon(QIcon(ICON) if os.path.isfile(ICON) else icons.icon("thumbs", 32, theme.ACCENT))
        self.tabs: list[DocTab] = []
        self._drop_image = ""   # 끌어 놓은 이미지 파일(이미지 추가가 파일 고르기 없이 받는다)
        self._receiver = None   # 다른 데서 켠 PDF메트리가 파일을 넘겨 오는 곳(single.Receiver)
        self._closing = False   # 창을 닫는 중(탭마다 설정을 쓰지 않고 끝에 한 번)
        self._emptied = False   # 탭을 모두 다른 창으로 옮겨서 닫는 창(창 크기·열린 문서를 기억하지 않는다)
        self._build()
        self.apply_theme()
        self._shortcuts()
        self.show_start()
        tab_detach.register(self)

    def show_initial(self):
        """켤 때 창 크기: 환경 설정 「열 때 창 크기」(마지막으로 닫은 크기 · 화면 가득 · 보통 크기)."""
        how = self.prefs.get("window_size", "last")
        geo = self.settings.get("window_geometry")
        if how == "last" and geo:
            from PySide6.QtCore import QByteArray
            ok = self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            if ok and self._on_screen():
                if self.settings.get("window_max"):   # 최대화로 닫았으면 최대화로(풀면 닫기 전 크기)
                    self.showMaximized(); return
                self.show(); return
            how = "max" if self.settings.get("window_max") else "normal"   # 그 모니터가 없어졌다
        if how == "max":
            self.showMaximized(); return
        scr = self.screen().availableGeometry()
        self.resize(min(1360, scr.width() - 40), min(820, scr.height() - 40))
        self.move(scr.center() - self.rect().center())
        self.show()

    def _on_screen(self) -> bool:
        """창 제목줄이 어느 화면 안에 들어 있나(모니터를 빼서 화면 밖으로 나가 있지 않나)."""
        from PySide6.QtCore import QRect
        g = self.normalGeometry() if self.normalGeometry().isValid() else self.geometry()
        bar = QRect(g.left() + 40, g.top() - 25, max(g.width() - 80, 40), 25)   # 제목줄 가운데
        return any(scr.availableGeometry().intersects(bar) for scr in QApplication.screens())

    def reopen_last(self):
        """환경 설정 「시작할 때 지난번 문서 다시 열기」."""
        if self.prefs.get("restore"):
            for p in self.settings.get("last_open", []):
                if os.path.isfile(p):
                    self.open_path(p)

    def open_forwarded(self, paths: list[str]):
        """다른 데서 켠 PDF메트리가 넘긴 파일을 마지막으로 쓴 창의 탭으로 연다. 파일이 없으면 창만 앞으로."""
        w = tab_detach.last_active(self)
        if w is not self:
            w.open_forwarded(paths); return
        if self.isMinimized():   # 최대화였으면 최대화로 되돌아온다
            self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_(); self.activateWindow()
        for p in paths:
            if os.path.isfile(p):
                self.open_path(p)

    def _remember_window(self):
        self.settings["window_max"] = self.isMaximized() or self.isFullScreen()
        if not self.isFullScreen():
            self.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")

    # ── 화면 짓기 ─────────────────────────────────────────────
    def _build(self):
        central = QWidget(); central.setObjectName("central"); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        # 문서 탭 줄
        tabrow = self.tabrow = QWidget(); tabrow.setObjectName("titlebar"); tl = QHBoxLayout(tabrow); tl.setContentsMargins(6, 0, 6, 0); tl.setSpacing(4)
        home = QToolButton(); home.setObjectName("quick"); home.setIcon(icons.icon("thumbs", 18, theme.ACCENT)); home.setToolTip("시작 화면")
        home.clicked.connect(self.show_start); tl.addWidget(home)
        self.doctabs = DocTabBar(); self.doctabs.setObjectName("doctabs")
        self.doctabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doctabs.customContextMenuRequested.connect(self._tab_menu); self.doctabs.setTabsClosable(True); self.doctabs.setMovable(True)
        self.doctabs.setExpanding(False); self.doctabs.setDrawBase(False); self.doctabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.doctabs.currentChanged.connect(self._tab_changed); self.doctabs.tabCloseRequested.connect(self.close_tab)
        self.doctabs.tabMoved.connect(self._tab_moved)
        self.doctabs.tornOff.connect(lambda i: tab_detach.start_drag(self, i))
        tl.addWidget(self.doctabs)
        addb = QToolButton(); addb.setObjectName("quick"); addb.setIcon(icons.icon("add", 16)); addb.setToolTip("열기"); addb.clicked.connect(self.open_dialog)
        tl.addWidget(addb); tl.addStretch()
        self.updates = UpdateUI(self); tl.addWidget(self.updates.bar)  # [새 버전 x 설치]·「설치 끝」. 시작 화면에서도 보이는 줄
        root.addWidget(tabrow)
        # 리본
        self.ribbon = Ribbon()
        self.ribbon.action.connect(self.on_action); self.ribbon.tool.connect(self.on_tool); self.ribbon.toggled.connect(self.on_toggle)
        self.ribbon.quickStyle.connect(self.open_quick_style)
        self.ribbon.tabChanged.connect(self.on_ribbon_tab); self.ribbon.searchRequested.connect(self.quick_search)
        self.ribbon.rangePicked.connect(self.pick_range)
        self.ribbon.fileMenu.connect(self.show_file_menu)
        root.addWidget(self.ribbon)
        # 본문
        self.body = QStackedWidget()
        self.start = StartPage(self.settings["recent"], self.settings["favorites"])
        self.start.openFile.connect(self.open_dialog); self.start.openPath.connect(self.open_path)
        self.start.tool.connect(self.start_tool); self.start.newBlank.connect(lambda: self.on_action("create_blank"))
        self.start.recentChanged.connect(lambda: save_settings(self.settings))
        self.body.addWidget(self.start)
        docarea = QWidget(); dl = QHBoxLayout(docarea); dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(0)
        self.navstrip = QWidget(); self.navstrip.setObjectName("navstrip"); self.navstrip.setFixedWidth(34)
        nl = QVBoxLayout(self.navstrip); nl.setContentsMargins(0, 6, 0, 6); nl.setSpacing(2)
        self.navbtns: dict[str, QToolButton] = {}
        for key, name, tip in (("thumbs", "thumbs", "썸네일"), ("bookmark", "bookmark", "북마크"), ("comments", "comments", "주석"),
                               ("attach", "attach", "첨부 파일"), ("search", "search", "검색"), ("layers", "layers", "레이어")):
            b = QToolButton(); b.setObjectName("nav"); b.setIcon(icons.icon(name, 16)); b.setToolTip(tip); b.setCheckable(True)
            b.clicked.connect(lambda _, k=key: self.toggle_side(k)); nl.addWidget(b); self.navbtns[key] = b
        nl.addStretch()
        hb = QToolButton(); hb.setObjectName("nav"); hb.setIcon(icons.icon("help", 16)); hb.setToolTip("도움말"); hb.clicked.connect(self.show_help); nl.addWidget(hb)
        dl.addWidget(self.navstrip)
        self.docstack = QStackedWidget(); dl.addWidget(self.docstack, 1)
        self.rightstrip = QWidget(); self.rightstrip.setObjectName("rightstrip"); self.rightstrip.setFixedWidth(34)
        rl = QVBoxLayout(self.rightstrip); rl.setContentsMargins(0, 6, 0, 6); rl.setSpacing(2)
        self.rightbtns = {}
        for key, name, tip in (("props", "props", "속성"), ("fields", "fields", "양식 칸"), ("sign", "signature", "서명"), ("translate", "translate", "글자 번역·검색")):
            b = QToolButton(); b.setObjectName("nav"); b.setIcon(icons.icon(name, 16)); b.setToolTip(tip); b.setCheckable(key in ("props", "fields"))
            b.setStyleSheet("QToolButton#nav{border-left:none; border-right:2px solid transparent} QToolButton#nav:checked{border-right:2px solid %s}" % theme.ACCENT)
            b.clicked.connect(lambda _, k=key: self.right_action(k)); rl.addWidget(b); self.rightbtns[key] = b
        rl.addStretch()
        dl.addWidget(self.rightstrip)
        self.body.addWidget(docarea)
        root.addWidget(self.body, 1)
        # 상태줄
        self.status = QWidget(); self.status.setObjectName("statusbar"); self.status.setFixedHeight(28)
        sl = QHBoxLayout(self.status); sl.setContentsMargins(8, 0, 8, 0); sl.setSpacing(4)
        self.size_label = QLabel(""); sl.addWidget(self.size_label)
        self.msg_label = QLabel(""); self.msg_label.setObjectName("dim"); sl.addWidget(self.msg_label)
        self.msg_label.setMinimumWidth(1)  # 긴 알림이 창 최소 폭을 밀어 올리지 않게(넘치면 잘리고 툴팁에 원문)
        sl.addStretch()
        self.sb_hand = QToolButton(); self.sb_hand.setObjectName("quick"); self.sb_hand.setIcon(icons.icon("hand", 16)); self.sb_hand.setCheckable(True); self.sb_hand.setToolTip("손")
        self.sb_sel = QToolButton(); self.sb_sel.setObjectName("quick"); self.sb_sel.setIcon(icons.icon("select", 16)); self.sb_sel.setCheckable(True); self.sb_sel.setToolTip("선택")
        self.sb_hand.clicked.connect(lambda: self.on_tool("hand")); self.sb_sel.clicked.connect(lambda: self.on_tool("select"))
        sl.addWidget(self.sb_hand); sl.addWidget(self.sb_sel); sl.addSpacing(8)
        for key, name in (("first", "first"), ("prev", "prev")):
            b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(name, 14)); b.clicked.connect(lambda _, k=key: self.on_action(k)); sl.addWidget(b)
        self.page_edit = QLineEdit(); self.page_edit.setFixedWidth(44); self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.returnPressed.connect(self._goto_entered); sl.addWidget(self.page_edit)
        self.page_total = QLabel("/ 0"); sl.addWidget(self.page_total)
        for key, name in (("next", "next"), ("last", "last")):
            b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(name, 14)); b.clicked.connect(lambda _, k=key: self.on_action(k)); sl.addWidget(b)
        self.sb_hist = {}
        for key, name in (("prev_view", "back"), ("next_view", "forward")):   # 이전 보기 · 다음 보기(Alt+← · Alt+→)
            b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(name, 14))
            b.clicked.connect(lambda _, k=key: self.on_action(k)); sl.addWidget(b); self.sb_hist[key] = b
        sl.addSpacing(8)
        for key, name, tip in (("layout_single", "single", "한 페이지씩"), ("layout_continuous", "continuous", "연속"),
                               ("layout_facing", "facing", "두 페이지"), ("layout_facing_continuous", "tile", "두 페이지 연속")):
            b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(name, 14)); b.setToolTip(tip)
            b.clicked.connect(lambda _, k=key: self.on_action(k)); sl.addWidget(b)
        sl.addStretch()
        zo = QToolButton(); zo.setObjectName("quick"); zo.setIcon(icons.icon("zoomout", 14)); zo.clicked.connect(lambda: self.on_action("zoomout")); sl.addWidget(zo)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal); self.zoom_slider.setRange(10, 800); self.zoom_slider.setFixedWidth(110)
        self.zoom_slider.sliderMoved.connect(self._zoom_slider); sl.addWidget(self.zoom_slider)
        zi = QToolButton(); zi.setObjectName("quick"); zi.setIcon(icons.icon("zoomin", 14)); zi.clicked.connect(lambda: self.on_action("zoomin")); sl.addWidget(zi)
        self.zoom_box = QComboBox(); self.zoom_box.setEditable(True); self.zoom_box.setFixedWidth(92)
        self.zoom_box.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%", "창 너비", "창 높이", "창 크기", "실제 크기"])
        self.zoom_box.activated.connect(self._zoom_box); self.zoom_box.lineEdit().returnPressed.connect(self._zoom_box)
        sl.addWidget(self.zoom_box)
        fs = QToolButton(); fs.setObjectName("quick"); fs.setIcon(icons.icon("fullscreen", 14)); fs.setToolTip("전체 화면"); fs.clicked.connect(lambda: self.on_action("fullscreen")); sl.addWidget(fs)
        root.addWidget(self.status)
        self.setAcceptDrops(True)
        self.autosave = F.Autosave(self)   # 고친 문서를 몇 분마다 복구 사본으로(환경 설정 「자동 저장」)

    def _shortcuts(self):
        """단축키를 건다(기본 키, 환경 설정 「단축키」 에서 바꾼 것은 settings 의 hotkeys). 바꾸면 다시 부른다."""
        for sc in getattr(self, "_sc", []):
            sc.setEnabled(False); sc.setParent(None); sc.deleteLater()
        self._sc = []
        self.keys = K.current(self.settings.get("hotkeys"))
        for key, seqs in self.keys.items():
            for seq in seqs:
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(lambda k=key: self.on_action(k))
                self._sc.append(sc)
        find = K.label(self.keys, "search")
        self.ribbon.search.setPlaceholderText(f"검색 ({find})" if find else "검색")

    def key_text(self, action: str) -> str:
        """메뉴 이름 뒤에 붙일 「\t키」(키가 없으면 빈 글자)."""
        k = K.label(self.keys, action)
        return "\t" + k if k else ""

    # ── 테마 ──────────────────────────────────────────────────
    def apply_theme(self):
        dark = bool(self.prefs.get("dark"))
        QApplication.instance().setStyleSheet(theme.qss(dark))
        self.ribbon.set_toggle("dark", dark)
        for t in self.tabs:
            t.viewer.setBackgroundBrush(QColor(theme.DARK["CANVAS"] if dark else theme.CANVAS))

    # ── 탭·문서 ───────────────────────────────────────────────
    def cur(self) -> DocTab | None:
        i = self.doctabs.currentIndex()
        return self.tabs[i] if 0 <= i < len(self.tabs) else None

    def show_start(self):
        self.body.setCurrentWidget(self.start)
        self.start.reload()
        self.ribbon.set_enabled_all(False)
        self.status.hide()
        self.doctabs.blockSignals(True); self.doctabs.setCurrentIndex(-1); self.doctabs.blockSignals(False)

    def _tab_changed(self, i: int):
        if i < 0 or i >= len(self.tabs):
            return
        t = self.tabs[i]
        self.body.setCurrentWidget(self.body.widget(1))
        self.docstack.setCurrentWidget(t)
        self.ribbon.set_enabled_all(True)
        self.status.show()
        self.setWindowTitle(f"{t.doc.name} - {APP_NAME}")
        for k, b in self.navbtns.items():
            b.setChecked(t.side.isVisible() and t.side_key == k)
        self.rightbtns["props"].setChecked(t.props.isVisible())
        self._sync_status(t)
        self.ribbon.reflect_tool(t.viewer.tool)
        self.ribbon.set_toggle("edit_all", t.viewer.edit_mode); self.ribbon.set_edit_target(t.viewer.edit_target)
        self.ribbon.set_toggle("form_edit", t.viewer.form_mode)
        self.ribbon.set_toggle("show_annots", t.viewer.show_annots)
        self.ribbon.set_annot_enabled(t.viewer.show_annots); self._sync_keep(t)
        self.ribbon.set_enabled("undo", t.doc.can_undo()); self.ribbon.set_enabled("redo", t.doc.can_redo())
        self.sync_thumb_zoom(*t.organize.can_zoom())
        self.ribbon.show_range(t.organize.selected_pages(), t.doc.page_count, P.labels(t.doc))

    def sync_thumb_zoom(self, can_out: bool, can_in: bool):
        self.ribbon.set_enabled("thumb_out", can_out); self.ribbon.set_enabled("thumb_in", can_in)

    def pick_range(self, spec: str):
        """구성 탭 범위 콤보: 짝수·홀수·모든 쪽이나 넣은 범위(레이블로 읽음)를 썸네일에서 고른다."""
        t = self.cur()
        if not t:
            return
        n = t.doc.page_count
        try:
            pages = P.subset(range(n), spec) if spec in ("all", "even", "odd") else                 P.parse_range(spec, n, strict=True, labels=P.labels(t.doc))
        except ValueError:
            pages = []
        if not pages:
            QMessageBox.warning(self, "페이지 범위", P.RANGE_ERROR)
            self.ribbon.range_combo.setFocus(); self.ribbon.range_combo.lineEdit().selectAll()
            return
        t.organize.select_pages(pages)
        t.organize.list.setFocus()

    def _tab_moved(self, a: int, b: int):
        self.tabs.insert(b, self.tabs.pop(a))

    def _wire(self, t: DocTab):
        """탭 뷰어 신호를 이 창에 잇는다. 다른 창으로 옮길 때 끊으려고 연결을 t._conns 에 둔다."""
        v = t.viewer
        t._conns = [
            v.pageChanged.connect(lambda i: self._sync_status(t)),
            v.zoomChanged.connect(lambda z: (self._sync_status(t), self._remember_zoom(t))),
            v.status.connect(self.say),
            v.requestDialog.connect(lambda kind, data, tab=t: self.on_request(tab, kind, data)),
            v.toolDone.connect(self._tool_done),
            v.docChanged.connect(lambda: (self.ribbon.set_enabled("undo", t.doc.can_undo()), self.ribbon.set_enabled("redo", t.doc.can_redo()))),
            v.selectionChanged.connect(lambda s: self._on_selection(t, s)),
        ]

    def _tab_label(self, t: DocTab) -> str:
        return ("● " if t.doc.modified else "") + t.doc.name

    def attach_tab(self, t: DocTab, index: int = -1):
        """다른 창에서 옮겨 온 탭을 index 자리(-1 이면 끝)에 붙이고 그 탭을 보여 준다."""
        t.win = self
        self._wire(t)
        if not 0 <= index <= len(self.tabs):
            index = len(self.tabs)
        self.tabs.insert(index, t)
        self.docstack.addWidget(t)
        self.doctabs.blockSignals(True)
        i = self.doctabs.insertTab(index, self._tab_label(t))
        self.doctabs.blockSignals(False)
        self.doctabs.setTabToolTip(i, t.doc.path or "")
        self.doctabs.setCurrentIndex(i)
        self._tab_changed(i)

    def detach_tab(self, t: DocTab):
        """탭을 이 창에서 빼기만 한다(문서는 닫지 않는다). 남은 탭이 없으면 시작 화면."""
        for c in getattr(t, "_conns", ()):
            QObject.disconnect(c)
        t._conns = []
        i = self.tabs.index(t)
        self.tabs.pop(i)
        self.docstack.removeWidget(t)
        self.doctabs.removeTab(i)
        t.setParent(None)
        if not self.tabs:
            self.show_start()

    def update_title(self):
        t = self.cur()
        self.setWindowTitle(f"{t.doc.name} - {APP_NAME}" if t else APP_NAME)

    def close_emptied(self):
        """탭을 모두 다른 창으로 옮긴 창: 묻지 않고 닫는다."""
        self._emptied = True
        self.close()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            tab_detach.activated(self)
        super().changeEvent(e)

    def add_doc(self, doc: Document, view: dict | None = None):
        """view: 그 문서를 지난번에 닫을 때의 보기(_remember_view). 있으면 환경 설정의 「열 때 확대·보기」 대신 그 자리로 연다.
        환경 설정이 「자동」이고 문서에 초기 보기(속성 창 「보기」)가 적혀 있으면 그 보기·쪽으로 연다."""
        t = DocTab(doc, self)
        self._wire(t)
        self.tabs.append(t)
        self.docstack.addWidget(t)
        i = self.doctabs.addTab(doc.name)
        self.doctabs.setTabToolTip(i, doc.path or "")
        pl = self.prefs.get("default_layout", "auto")
        zm = self.prefs.get("default_zoom", "auto")
        last = self.settings.get("last_zoom") or {}
        iv = doc.initial_view()   # 문서에 적힌 초기 보기(속성 창 「보기」)
        own = iv != {"mode": "none", "layout": "default", "zoom": "default", "page": 0}
        if own and (pl == "auto" or zm == "auto"):   # 문서가 정한 보기가 그 문서를 지난번 닫은 자리보다 먼저
            view = None
        if pl == "auto":   # 자동: 문서의 레이아웃, 없으면 연속
            pl = iv["layout"] if iv["layout"] != "default" else "continuous"
        if zm == "auto":   # 자동: 문서가 정한 배율, 없으면 너비 맞춤
            z = iv["zoom"]
            zm, last = (("fixed", {"zoom": z}) if isinstance(z, float) else
                        (z if z != "default" else "fit_width", last))
        elif zm == "last":   # 마지막으로 쓴 확대: 맞추기였으면 그 맞추기, 직접 고른 배율이면 그 배율
            zm = last.get("mode", "fit_width")
        elif re.fullmatch(r"p\d+", zm):   # 고정 배율(p10 … p600)
            zm, last = "fixed", {"zoom": int(zm[1:]) / 100}
        if view and view.get("layout") in ("single", "continuous", "facing", "facing_continuous"):
            pl = view["layout"]
        if view and view.get("mode") in ("fixed", "fit_width", "fit_page", "fit_height", "actual"):
            zm = view["mode"]; last = {"zoom": view.get("zoom", 1.0)}
        if not view and own:
            view = {"page": iv["page"], "y": 0, "x": 0}
        t.viewer.layout_mode = pl
        t.viewer.zoom_mode = zm
        t.viewer.cover_separate = bool(view and view.get("cover"))   # 지난번 표지 따로 보기
        t.viewer.set_layout_mode(pl)
        t.viewer.props["author"] = self.prefs.get("author", "")
        t.viewer.props["measure_unit"] = self.prefs.get("unit", "cm")
        # apply_theme 은 그때 열린 탭만 칠한다. 어두운 화면에서 새로 연 문서도 문서 뒤 바탕을 맞춘다
        t.viewer.setBackgroundBrush(QColor(theme.DARK["CANVAS"] if self.prefs.get("dark") else theme.CANVAS))
        self.doctabs.setCurrentIndex(i)
        self._tab_changed(i)
        if iv["mode"] in ("outlines", "thumbs"):   # 문서가 정한 탐색 탭
            self.toggle_side("bookmark" if iv["mode"] == "outlines" else "thumbs")
        if self.prefs.get("select_mode", "select") != t.viewer.tool:   # 처음 도구(환경 설정 「기본 선택 모드」)
            self.on_tool(self.prefs.get("select_mode", "select"))
        def first_view():
            if t.viewer.closed:
                return
            if t.viewer.zoom_mode == "fixed":
                t.viewer.set_zoom(float(last.get("zoom", 1.0)))
            t.viewer._apply_fit()
            if view and 0 <= int(view.get("page", 0)) < doc.page_count:
                t.viewer.set_view_pos(view["page"], float(view.get("y", 0)), int(view.get("x", 0)))
            else:
                t.viewer.goto(0, remember=False)
        QTimer.singleShot(60, first_view)

    def open_path(self, path: str, password: str | None = None):
        path = os.path.abspath(path)
        if path.lower().endswith(F.FDF_EXTS):   # 주석 파일은 지금 문서에 가져온다
            t = self.cur()
            if not t:
                QMessageBox.information(self, "열기", "주석 파일(FDF·XFDF)은 PDF 문서를 먼저 연 뒤 가져올 수 있습니다."); return
            try:
                n, skipped = F.import_annot_file(t.doc, path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "열기", f"읽을 수 없는 파일입니다 ({e})"); return
            t.changed(None); self.say(f"주석 {n}개를 가져왔습니다" + (f" · 못 가져온 것 {skipped}개" if skipped else "")); return
        hit = F.find_open(self, path)   # 이미 열린 파일: 떼어 낸 다른 창이면 그 창을 앞으로
        if hit:
            F.bring(*hit); return
        if self.tabs and not self.prefs.get("same_window", True):   # 「동일한 창에서 새 탭으로 문서 열기」 끔
            w = MainWindow(settings=self.settings)
            g = self.normalGeometry() if self.isMaximized() else self.geometry()
            w.resize(g.size()); w.move(g.topLeft() + QPointF(40, 40).toPoint()); w.show()
            w.open_path(path, password); return
        try:
            doc = Document(path, password)
        except NeedsPassword:
            def check(pw: str) -> bool:
                try:
                    Document(path, pw).close(); return True
                except DocumentError:
                    return False
            pw = PW.ask(self, os.path.basename(path), self.prefs, check,
                        on_presets=lambda: PW.edit_presets(self, self.prefs) and save_settings(self.settings))
            if pw is None:
                return
            try:
                doc = Document(path, pw)
            except DocumentError as e:
                QMessageBox.critical(self, "열기", str(e)); return
        except DocumentError as e:
            QMessageBox.critical(self, "열기", str(e)); return
        self.add_doc(doc, view=self._saved_view(path))
        self.add_recent(path)

    def add_recent(self, path: str):
        rec = self.settings["recent"]
        view = next((r["view"] for r in rec if r["path"] == path and r.get("view")), None)
        rec[:] = [r for r in rec if r["path"] != path]
        rec.insert(0, {"path": path, "time": time.time(), **({"view": view} if view else {})})
        del rec[self.prefs.get("recent_n", 20):]
        save_settings(self.settings)

    def open_dialog(self):
        # 마지막으로 연 폴더에서 시작(닿지 않는 공유폴더는 있다고 확인된 파일이 있을 때만, 없으면 내 문서)
        paths, _ = QFileDialog.getOpenFileNames(self, "열기", F.open_start_dir(self), F.OPEN_FILTER)
        if paths:
            self.settings["last_open_dir"] = os.path.dirname(paths[0])
        for p in paths:
            self.open_path(p)

    def _tab_menu(self, pos):
        """문서 탭 오른쪽 클릭: 저장 · 파일 위치 열기 · 경로 복사 · 여러 탭 닫기."""
        i = self.doctabs.tabAt(pos)
        if i < 0 or i >= len(self.tabs):
            return
        t = self.tabs[i]
        n = len(self.tabs)
        path = t.doc.path if t.doc.path and os.path.isfile(t.doc.path) else ""
        m = QMenu(self)
        m.addAction("저장" + self.key_text("save"), lambda: self.save(t)).setEnabled(t.doc.modified or not t.doc.path)
        m.addAction("다른 이름으로 저장" + self.key_text("saveas"), lambda: self.save(t, ask=True))
        m.addSeparator()
        m.addAction("파일 위치 열기", lambda: subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])).setEnabled(bool(path))
        m.addAction("경로 복사", lambda: (QApplication.clipboard().setText(os.path.normpath(path)), self.say("경로를 복사했습니다")
                                         )).setEnabled(bool(path))
        m.addSeparator()
        m.addAction("새 창으로 분리", lambda: tab_detach.move_tab(t)).setEnabled(n > 1)
        m.addSeparator()
        m.addAction("이 탭 닫기" + self.key_text("close"), lambda: self.close_tab(self.tabs.index(t)))
        m.addAction("다른 탭 닫기", lambda: self.close_tabs([x for x in self.tabs if x is not t], confirm=True)).setEnabled(n > 1)
        m.addAction("오른쪽 탭 닫기", lambda: self.close_tabs(self.tabs[self.tabs.index(t) + 1:], confirm=True)).setEnabled(i < n - 1)
        m.addAction("모두 닫기", lambda: self.close_tabs(list(self.tabs), confirm=True))
        m.exec(self.doctabs.mapToGlobal(pos))

    def close_tabs(self, tabs: list, confirm: bool = False) -> bool:
        """여러 탭을 뒤에서부터 닫는다. 저장을 묻다가 [취소] 하면 거기서 멈춘다.
        confirm: 탭 메뉴에서 사람이 고른 것(환경 설정 「여러 탭 닫기 전 확인」 이면 먼저 묻는다)."""
        if confirm and len(tabs) > 1 and self.prefs.get("confirm_close_many", True) and QMessageBox.question(
                self, "닫기", f"탭 {len(tabs)}개를 닫을까요?") != QMessageBox.StandardButton.Yes:
            return False
        for t in reversed(tabs):
            if t in self.tabs and not self.close_tab(self.tabs.index(t)):
                return False
        return True

    def _remember_view(self, t: DocTab):
        """다시 열 때 그 자리로: 최근 파일 항목에 마지막 쪽·쪽 안 높이·가로 스크롤·확대·보기 방식을 적는다(RecentFiles.ini 와 같음)."""
        if not t.doc.path or not t.viewer.items:
            return
        v = t.viewer
        page, y, x = v.view_pos()
        state = {"page": page, "y": y, "x": x, "zoom": round(v.zoom, 4), "mode": v.zoom_mode, "layout": v.layout_mode,
                 "cover": v.cover_separate}
        path = os.path.abspath(t.doc.path)
        for r in self.settings["recent"]:
            if os.path.abspath(r["path"]) == path:
                r["view"] = state
                return

    def _saved_view(self, path: str) -> dict | None:
        path = os.path.abspath(path)
        for r in self.settings["recent"]:
            if os.path.abspath(r["path"]) == path:
                return r.get("view")
        return None

    def close_tab(self, i: int) -> bool:
        t = self.tabs[i]
        self._remember_view(t)
        if t.doc.modified:
            r = QMessageBox.question(self, "닫기", f"「{t.doc.name}」 을 저장할까요?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not self.save(t):
                return False
        self.tabs.pop(i)
        self.docstack.removeWidget(t)
        self.doctabs.removeTab(i)
        t.close(); t.deleteLater()
        if not self._closing:
            save_settings(self.settings)   # 기억한 보기 자리(창을 닫을 때는 끝에 한 번만)
        if not self.tabs and not self._closing:   # 창을 닫는 중이면 시작 화면을 다시 그리지 않는다
            self.show_start()
        return True

    def closeEvent(self, e: QCloseEvent):
        rest = tab_detach.others(self)
        if not self._emptied:
            self._remember_window()
        if not rest:   # 창이 여럿이면 마지막에 닫는 창의 문서만 다음에 다시 연다
            self.settings["last_open"] = [t.doc.path for t in self.tabs if t.doc.path]
        if self._receiver:   # 닫는 동안 넘어오는 파일은 받지 않는다(닫히는 창에 열려 사라지지 않게)
            self._receiver.stop()
        self._closing = True
        while self.tabs:
            if not self.close_tab(len(self.tabs) - 1):
                self._closing = False
                if self._receiver:
                    self._receiver.listen()
                e.ignore(); return
        save_settings(self.settings)
        if self._receiver and rest:   # 다른 창이 남았으면 넘겨 오는 파일은 그 창들이 받는다
            rest[-1]._receiver, self._receiver = self._receiver, None
            rest[-1]._receiver.on_paths = rest[-1].open_forwarded
            rest[-1]._receiver.listen()
        elif self._receiver:   # 이 뒤에 켜지는 PDF메트리는 넘기지 않고 제 창을 띄운다
            from .. import single
            single.release()
        tab_detach.unregister(self)
        e.accept()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if os.path.isfile(u.toLocalFile())]
        images = [p for p in paths if p.lower().endswith(self.IMAGE_EXTS)]
        if self.cur() and len(paths) == 1 and images:
            # 열린 문서에 이미지 파일을 놓으면 어떻게 쓸지 묻는다
            m = QMenu(self)
            add = m.addAction("이미지로 추가"); new = m.addAction("새 PDF 파일로 만들기")
            pick = m.exec(self.mapToGlobal(e.position().toPoint()))
            if pick is add:
                self._drop_image = images[0]; self.on_tool("addimage")
            elif pick is new:
                self.create_from_paths(images)
            return
        for p in paths:
            self.open_path(p)

    def mark_modified(self):
        t = self.cur()
        if not t:
            return
        i = self.tabs.index(t)
        self.doctabs.setTabText(i, self._tab_label(t))
        self.ribbon.set_enabled("undo", t.doc.can_undo()); self.ribbon.set_enabled("redo", t.doc.can_redo())

    def say(self, msg: str):
        self.msg_label.setText(msg); self.msg_label.setToolTip(msg)
        QTimer.singleShot(5000, lambda: self.msg_label.setText("") if self.msg_label.text() == msg else None)

    def _zoom_slider(self, v: int):
        t = self.cur()
        if t:
            t.viewer.zoom_mode = "fixed"; t.viewer.set_zoom(v / 100)

    def _remember_zoom(self, t: DocTab):
        if t is self.cur():   # 다음에 여는 문서가 따라갈 확대(환경 설정 「열 때 확대」가 마지막 확대일 때)
            self.settings["last_zoom"] = {"mode": t.viewer.zoom_mode, "zoom": round(t.viewer.zoom, 4)}

    def _sync_status(self, t: DocTab):
        if t is not self.cur():
            return
        v = t.viewer
        w, h = t.doc.page_size(v.current)
        self.size_label.setText(f"{w / 72 * 2.54:.2f} x {h / 72 * 2.54:.2f} cm")
        self.page_edit.setText(str(v.current + 1)); self.page_total.setText(f"/ {t.doc.page_count}")
        if self.zoom_slider.maximum() != int(ZOOM_MAX * 100):   # 확대 범위·목록을 단계표로(상태줄을 지을 때는 옛 값)
            self.zoom_slider.setRange(int(ZOOM_MIN * 100), int(ZOOM_MAX * 100))
            self.zoom_box.clear(); self.zoom_box.addItems([pct_text(z) for z in PageView.STEPS] + list(ZOOM_FITS))
            self.zoom_box.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9.%]*"), self.zoom_box))
        self.zoom_slider.blockSignals(True); self.zoom_slider.setValue(int(round(v.zoom * 100))); self.zoom_slider.blockSignals(False)
        self.zoom_box.setEditText(pct_text(v.zoom))
        v.sync_prefs(self.prefs)
        self.ribbon.set_enabled("tile", tiler.doc_count() >= 2)
        self.sb_hand.setChecked(v.tool == "hand"); self.sb_sel.setChecked(v.tool == "select")
        self.sb_hist["prev_view"].setEnabled(bool(v._back)); self.sb_hist["next_view"].setEnabled(bool(v._fwd))
        i = self.tabs.index(t)
        self.doctabs.setTabText(i, ("● " if t.doc.modified else "") + t.doc.name)

    def _goto_entered(self):
        t = self.cur()
        if not t:
            return
        txt = self.page_edit.text().strip()
        labels = P.labels(t.doc)
        if txt in labels:
            t.viewer.goto(labels.index(txt))
        elif txt.isdigit() and 1 <= int(txt) <= t.doc.page_count:
            t.viewer.goto(int(txt) - 1)
        else:   # 못 읽으면 지금 쪽으로 되돌린다
            cur = t.viewer.current
            self.page_edit.setText(labels[cur] if 0 <= cur < len(labels) and labels[cur] else str(cur + 1))

    def _zoom_box(self, *_):
        t = self.cur()
        if not t:
            return
        txt = self.zoom_box.currentText().strip()
        if txt in ZOOM_FITS:
            t.viewer.set_zoom_mode(ZOOM_FITS[txt])
            return
        try:
            z = float(txt.replace("%", "")) / 100
        except ValueError:
            self.zoom_box.setEditText(pct_text(t.viewer.zoom)); return   # 못 읽으면 지금 배율로 되돌린다
        t.viewer.zoom_mode = "fixed"; t.viewer.set_zoom(max(ZOOM_MIN, min(ZOOM_MAX, z)))
        self.zoom_box.setEditText(pct_text(t.viewer.zoom))

    def toggle_side(self, key: str):
        t = self.cur()
        if not t:
            return
        t.show_side(key)
        for k, b in self.navbtns.items():
            b.setChecked(t.side.isVisible() and t.side_key == k)

    def right_action(self, key: str):
        t = self.cur()
        if not t:
            return
        if key == "props":
            t.props.setVisible(not t.props.isVisible()); self.rightbtns["props"].setChecked(t.props.isVisible())
        elif key == "fields":
            self.toggle_side("fields"); self.rightbtns["fields"].setChecked(t.side.isVisible() and t.side_key == "fields")
        elif key == "sign":
            self.on_action("sign_apply")
        elif key == "translate":
            s = t.viewer.selected_text()
            if s:
                webbrowser.open("https://papago.naver.com/?sk=auto&tk=ko&st=" + urllib.parse.quote(s[:500]))
            else:
                self.say("먼저 글자를 고르세요(선택 도구)")

    def _on_selection(self, t: DocTab, s):
        if s is not None and s.kind == "annot" and not t.props.isVisible() and t.viewer.tool == "select":
            pass  # 속성 패널은 사용자가 켠 것만 보인다
        self.ribbon.set_enabled("delete_sel", s is not None)

    def _tool_done(self, tool: str):
        t = self.cur()
        if not t:
            return
        # 주석 도구는 「도구 계속 쓰기」(종류마다 기억)를 따르고, 그 밖의 한 번 쓰는 도구는 선택으로 돌아간다.
        if tool in ANNOT_TOOLS:
            if not keep_tool(self.settings, tool):
                self.on_tool("select")
        elif tool in ("addtext", "addimage", "link", "crop", "snapshot", "ocrarea", "escape") or tool.startswith("f"):
            self.on_tool("select" if not t.viewer.form_mode else "form")

    def _sync_keep(self, t):
        """「도구 계속 쓰기」 토글을 지금 도구의 값으로. 주석 도구가 아니거나 지우개면 끔·회색."""
        tool = t.viewer.tool
        on = tool in ANNOT_TOOLS and tool != "eraser" and t.viewer.show_annots
        self.ribbon.set_toggle("keep_tool", keep_tool(self.settings, tool) if on else False)
        self.ribbon.set_enabled("keep_tool", on)

    def open_quick_style(self, tool: str, btn, only_color: bool = False):
        """켜진 주석 도구 단추를 다시 누름(또는 리본 「색」): 그 단추 아래에 빠른 스타일."""
        t = self.cur()
        if not t or not has_quick_style(tool):
            return
        if tool == "eraser":
            t.viewer.set_eraser_radius(eraser_radius(self.settings))
        q = QuickStyle(tool, t.viewer, self.settings, lambda: save_settings(self.settings), only_color, parent=self)
        q.changed.connect(lambda _k, t=t: None if t.viewer.closed else t.props.tool_selected(t.viewer.tool))
        self._quick = q   # 닫힐 때까지 붙잡아 둔다
        q.popup_under(btn if btn is not None and btn.isVisible() else self.ribbon)

    # ── 스탬프 · 서명 · 측정 창 ─────────────────────────────────────
    def pick_stamp(self) -> dict | None:
        """스탬프 메뉴를 마우스 자리에 띄워 고른 스탬프(spec). 아래 항목(만들기·관리·ID)은 메뉴를 닫은 뒤 연다."""
        got: list[dict] = []
        m = QMenu(self)
        DST.fill_stamp_menu(m, got.append, lambda k: QTimer.singleShot(0, lambda: self.on_action(k)))
        from PySide6.QtGui import QCursor
        m.exec(QCursor.pos())
        return got[0] if got else None

    def stamp_dialog(self, key: str):
        if key == "stamp_identity":
            self.edit_identity(); return
        author = A.user_author(self.prefs.get("author", ""))
        if key == "stamp_create":
            dlg = DST.CustomStampDialog(self)
        elif key == "stamp_create_dyn":
            dlg = DST.DynamicStampDialog(self, author, identity_edit=self.edit_identity)
        else:
            DST.StampManageDialog(self, author, identity_edit=self.edit_identity).exec(); return
        t = self.cur()
        if dlg.exec() and dlg.saved_id and t:   # 만든 스탬프로 바로 찍는다
            t.viewer.props["stamp_spec"] = {"group": "custom", "id": dlg.saved_id}
            self.on_tool("stamp_go")

    def edit_identity(self) -> bool:
        """ID 편집. 이름은 환경설정의 작성자와 같은 값(새 주석 작성자)."""
        st = ST.store(); ident = st.identity()
        dlg = DST.IdentityDialog(self, self.prefs.get("author", ""), ident["company"], ident["department"])
        if not dlg.exec():
            return False
        v = dlg.values()
        self.prefs["author"] = v["name"]; save_settings(self.settings)
        st.set_identity(v["company"], v["department"])
        for t in self.tabs:
            t.viewer.props["author"] = v["name"]
        return True

    def measure_settings(self):
        dlg = MeasureDialog(self, self.prefs.get("measure"))
        if dlg.exec():
            self.prefs["measure"] = dlg.values(); save_settings(self.settings)
            for t in self.tabs:
                t.viewer.props["measure"] = self.prefs["measure"]
            self.say(f"측정 축척 {A.ratio_text(self.prefs['measure'])}")

    # ── 리본 처리 ─────────────────────────────────────────────
    def on_ribbon_tab(self, key: str):
        t = self.cur()
        if not t:
            return
        if key == "page":
            # 구성 화면은 문서가 바뀔 때마다 맞춰 두므로(changed) 다 그린 썸네일은 그대로 쓴다
            t.view_stack.setCurrentWidget(t.organize); t.organize.reload(list(range(t.doc.page_count)))
        else:
            t.view_stack.setCurrentWidget(t.viewer)
        if key == "form" and not t.viewer.form_mode:
            pass
        if key != "form" and t.viewer.form_mode:
            t.viewer.set_form_mode(False); self.ribbon.set_toggle("form_edit", False)
        if key != "edit" and key != "home" and t.viewer.edit_mode:
            t.viewer.set_edit_mode(False); self.ribbon.set_toggle("edit_all", False); self.ribbon.reflect_tool(t.viewer.tool)

    def on_tool(self, key: str):
        t = self.cur()
        if not t:
            return
        if key in ("stamp", "stamp_pick"):   # 스탬프 메뉴에서 고른 뒤 찍는다
            spec = self.pick_stamp()
            if not spec:
                self.ribbon.reflect_tool(t.viewer.tool); return
            t.viewer.props["stamp_spec"] = spec; key = "stamp"
        elif key == "stamp_go":              # 스탬프를 이미 정했다(만들기 창에서 바로)
            key = "stamp"
        elif key in ("m_distance", "m_perimeter", "m_area"):
            t.viewer.props["measure"] = self.prefs.get("measure")
        elif key == "addimage":
            p, self._drop_image = self._drop_image, ""
            if not p:
                p, _ = QFileDialog.getOpenFileName(self, "넣을 이미지", "", "이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.webp)")
            if not p:
                return
            rd = QImageReader(p)
            if not rd.canRead() or not rd.size().isValid():
                QMessageBox.warning(self, "이미지 추가", "읽을 수 없는 사진 파일입니다.")
                return
            t.viewer.props["image_path"] = p
        elif key == "signature":   # 저장한 서명을 고르거나 그리기·타이핑·사진으로 새로
            dlg = SignatureDialog(self)
            if not dlg.exec() or not dlg.path:
                self.ribbon.reflect_tool(t.viewer.tool); return
            t.viewer.props["stamp_spec"] = {"group": "signature", "file": dlg.path}
        if key in ANNOT_TOOLS and not t.viewer.show_annots:
            self.say("주석이 숨겨져 있습니다. 「주석 보이기」를 켜면 주석 도구를 씁니다.")
            self.ribbon.reflect_tool(t.viewer.tool)
            return
        if key.startswith("f") and key in TOOL_KEYS and not t.viewer.form_mode:
            t.viewer.set_form_mode(True); self.ribbon.set_toggle("form_edit", True)
        if key in ("hand", "select") and t.viewer.form_mode:
            t.viewer.set_form_mode(False); self.ribbon.set_toggle("form_edit", False)
        if key in ("hand", "select") and t.viewer.edit_mode:
            t.viewer.set_edit_mode(False); self.ribbon.set_toggle("edit_all", False); self.ribbon.reflect_tool(t.viewer.tool)
        if key in ("addtext", "addimage"):
            t.viewer.begin_edit_tool(("text", "all") if key == "addtext" else ("image", "all"))
            self.ribbon.set_toggle("edit_all", True); self.ribbon.set_edit_target(t.viewer.edit_target)
        if key == "eraser":
            t.viewer.set_eraser_radius(eraser_radius(self.settings))
        t.viewer.set_tool(key)
        if key in SH.SHAPES:   # 모양 더보기 넷은 다각형의 기본값을 같이 쓴다
            S.to_viewer_props(style_key(key), S.store().get(style_key(key)), t.viewer.props)
        self.ribbon.reflect_tool(key)
        self._sync_keep(t)
        self._sync_status(t)
        if key in ("hand", "select"):
            self.say("")
        elif key == "addimage":
            self.say("이미지 추가: 넣을 자리를 누르세요. 끌면 그 크기로 넣습니다. Esc 로 그만둡니다.")
        elif key in ("note", "caret", "attach", "addtext"):   # 한 번 누르면 바로 넣는 도구
            self.say(f"{LABELS[key]}: 넣을 자리를 누르세요. Esc 로 그만둡니다.")
        elif key in LABELS and key not in ("form", "edit"):
            self.say(f"{LABELS[key]}: 문서 위에서 끌어 그리세요. Esc 로 그만둡니다.")

    EDIT_HINTS = {"all": "글자 덩어리를 두 번 눌러 고치고, 그림은 눌러 고른 뒤 Delete 로 지웁니다.",
                  "text": "글자 덩어리를 두 번 눌러 고칩니다.",
                  "image": "그림을 눌러 고른 뒤 Delete 로 지웁니다.",
                  "link": "링크를 눌러 고르고, 두 번 눌러 고칩니다."}

    def _set_edit(self, t, on: bool, target: str | None = None):
        """편집 켜기·끄기와 대상 고르기. 대상을 고르면 편집이 켜진다."""
        v = t.viewer
        v.set_edit_mode(on, target)
        self.ribbon.set_toggle("edit_all", on); self.ribbon.set_edit_target(v.edit_target)
        self.ribbon.reflect_tool(v.tool)
        self.say(self.EDIT_HINTS.get(v.edit_target, "") if on else "")

    def on_toggle(self, key: str, on: bool):
        t = self.cur()
        if key == "dark":
            self.prefs["dark"] = on; save_settings(self.settings); self.apply_theme(); return
        if not t:
            return
        if key == "edit_all":
            self._set_edit(t, on)
        elif key == "form_edit":
            t.viewer.set_form_mode(on); self.ribbon.reflect_tool(t.viewer.tool)
        elif key == "show_annots":
            # 보기 설정이다. 파일의 주석 숨김 표시를 바꾸면 저장할 때 주석이 숨은 채로 남는다.
            t.viewer.show_annots = on
            t.viewer.refresh_pages(None)
            self.ribbon.set_annot_enabled(on)
            if not on and t.viewer.tool in ANNOT_TOOLS:   # 숨긴 채 주석을 만들지 않게 도구를 끈다
                self.on_tool("select")
            self._sync_keep(t)
        elif key == "keep_tool":
            tool = t.viewer.tool
            if tool in ANNOT_TOOLS and tool != "eraser":
                self.settings.setdefault("keep_tool", {})[tool] = on; save_settings(self.settings)
            self._sync_keep(t)
        elif key == "read_mode":
            self.ribbon.stack.setVisible(not on); self.navstrip.setVisible(not on); self.rightstrip.setVisible(not on)
            if on:
                t.side.hide(); t.props.hide()

    def on_action(self, key: str):  # noqa: C901
        t = self.cur()
        v = t.viewer if t else None
        d = t.doc if t else None
        try:
            # 파일
            if key == "open": self.open_dialog()
            elif key == "save" and t: self.save(t)
            elif key == "saveas" and t: self.save(t, ask=True)
            elif key == "close" and t: self.close_tab(self.tabs.index(t))
            elif key == "quit": self.close()
            elif key == "print" and t: self.print_doc(t)
            elif key == "print_batch": self.batch_dialog()
            elif key == "properties" and t: self.properties(t)
            elif key == "preferences": self.preferences()
            elif key == "help": self.show_help()
            elif key == "mail" and t: self.mail(t)
            elif key == "next_tab" and self.tabs: self.doctabs.setCurrentIndex((self.doctabs.currentIndex() + 1) % len(self.tabs))
            elif key == "prev_tab" and self.tabs: self.doctabs.setCurrentIndex((self.doctabs.currentIndex() - 1) % len(self.tabs))
            elif key == "undo" and t:
                if d.undo(): t.viewer.clear_selection(); t.changed(None, structural=True)
            elif key == "redo" and t:
                if d.redo(): t.viewer.clear_selection(); t.changed(None, structural=True)
            # 생성
            elif key == "create_blank": self.create_blank()
            elif key == "create_files": self.create_from_files()
            elif key == "create_clipboard": self.create_from_clipboard()
            elif key == "create_scan": QMessageBox.information(self, "스캐너", "스캐너 연동은 넣지 않았습니다. 스캔 프로그램으로 이미지를 받은 뒤 「파일에서 생성」 으로 PDF 를 만드세요.")
            # 검색·버전(문서 없어도)
            elif key == "search_advanced": ADV.open_for(self)
            elif key == "about": self.show_help()
            # 보기
            elif not t: return
            elif key == "zoomin": v.zoom_in()
            elif key == "zoomout": v.zoom_out()
            elif key == "zoom_actual": v.set_zoom_mode("actual")
            elif key == "zoom_width": v.set_zoom_mode("fit_width")
            elif key == "zoom_height": v.set_zoom_mode("fit_height")
            elif key == "zoom_page": v.set_zoom_mode("fit_page")
            elif key == "zoom_area": self.on_tool("zoomarea")
            elif key == "layout_cover": v.set_cover_separate(not v.cover_separate)
            elif key.startswith("layout_"): v.set_layout_mode(key[len("layout_"):])
            elif key.startswith("canvas_"):
                # 배경 모드(앱 설정에 기억, 이 창의 모든 탭). 옛 이름(회색·흰·어두운·세피아)도 받는다
                mode = {"gray": "default", "white": "day", "dark": "night", "sepia": "parchment"}.get(key[7:], key[7:])
                if mode in VIEW_BG:
                    self.prefs["view_bg"] = mode; save_settings(self.settings)
                    for x in self.tabs: x.viewer.sync_prefs(self.prefs)
            elif key.startswith("rotate_view_"):
                # 보기 회전: 화면에서만 돈다(파일 /Rotate 는 그대로, 탭마다 따로, 저장·닫기에 안 남음)
                v.rotate_view(90 if key == "rotate_view_cw" else -90)   # "ccw" 도 "cw" 로 끝난다
            elif key == "fullscreen": fullscreen.get(self).toggle()
            elif key == "auto_bookmark": self.auto_bookmark(t)
            elif key in ("toggle_grid", "toggle_ruler"):
                attr = "show_grid" if key == "toggle_grid" else "show_ruler"
                on = not getattr(v, attr)
                for x in self.tabs: (x.viewer.set_grid if key == "toggle_grid" else x.viewer.set_ruler)(on)
            elif key in ("tile_h", "tile_v"): self.tile(key == "tile_h")
            elif key == "maximize": self.showNormal() if self.isMaximized() else self.showMaximized()
            elif key == "batch_convert": self.batch_dialog()
            elif key == "first": v.goto(0)
            elif key == "last": v.goto(d.page_count - 1)
            elif key == "next": v.next_page()
            elif key == "prev": v.prev_page()
            elif key == "goto":
                n, ok = QInputDialog.getInt(self, "쪽으로 이동", "쪽 번호", v.current + 1, 1, d.page_count)
                if ok: v.goto(n - 1)
            elif key == "prev_view": v.back()
            elif key == "next_view": v.forward()
            elif key in ("thumbs", "bookmark", "comments", "attach", "layers"): self.toggle_side(key)
            elif key == "search_panel": self.show_search()
            elif key == "search_replace": SF.open_for(t)
            elif key in ("search_next", "search_prev"):
                if t.find_float is not None and t.find_float.isVisible(): t.find_float.find(1 if key == "search_next" else -1)
                elif t.search.hits: t.search.step(1 if key == "search_next" else -1)
                else: self.show_search()
            elif key == "side_left":
                if t.side.isVisible(): t.side.hide()
                else: t.show_side(t.side_key or "thumbs")
                for k, b in self.navbtns.items(): b.setChecked(t.side.isVisible() and t.side_key == k)
            elif key == "side_right": self.right_action("props")
            elif key == "read_mode":
                on = self.ribbon.stack.isVisible(); self.ribbon.set_toggle("read_mode", on); self.on_toggle("read_mode", on)
            elif key in ("ribbon_next", "ribbon_prev"):
                keys = list(self.ribbon.pages); i = keys.index(self.ribbon.current_tab) if self.ribbon.current_tab in keys else 0
                self.ribbon.set_tab(keys[(i + (1 if key == "ribbon_next" else -1)) % len(keys)])
            elif key == "ribbon_fold": self.ribbon.stack.setVisible(not self.ribbon.stack.isVisible())
            elif key == "bookmark_add": t.show_side("bookmark") if not t.side.isVisible() else None; t.bookmarks.add()
            elif key == "bookmark_rename":
                if not t.bookmarks.tree.currentItem(): self.say("이름을 바꿀 북마크를 먼저 고르세요"); return
                if not (t.side.isVisible() and t.side_key == "bookmark"): t.show_side("bookmark")
                t.bookmarks.rename_current()
            elif key == "edit_all_toggle": self._set_edit(t, not v.edit_mode)
            elif key.startswith("edit_target_"): self._set_edit(t, True, key[len("edit_target_"):])
            elif key == "tool_hand": self.on_tool("hand")
            elif key == "tool_select": self.on_tool("select")
            elif key == "snapshot_tool": self.on_tool("snapshot")
            elif key == "annot_color":   # 리본 「색」: 지금 주석 도구의 색(주석 도구가 아니면 강조)
                btns = self.ribbon.buttons.get("annot_color") or [None]
                self.open_quick_style(v.tool if style_key(v.tool) else "highlight", btns[0], only_color=True)
            elif key == "delete_sel": v.delete_selection()
            elif key == "replace_image":
                if v.sel and v.sel.kind == "image": self.on_request(t, "replace_image", v.sel)
                else: self.say("편집 모드에서 이미지를 먼저 고르세요")
            # 편집 꾸미기
            elif key.split("_")[0] in DECO_KEYS:   # 워터마크·배경·머리글·쪽 번호·베이츠의 추가·업데이트·삭제
                run_decorate(self, t, DECO_KEYS[key.split("_")[0]], key.partition("_")[2] or "add")
            elif key == "pagelabel":
                sel = t.organize.selected_pages() if t.view_stack.currentWidget() is t.organize else []
                if not sel and t.side.isVisible() and t.side_key == "thumbs":
                    sel = t.thumbs.selected_pages()
                DS.run_page_label(self, t, sel)
            # 구성
            elif key in ("thumb_in", "thumb_out"): t.organize.zoom(1 if key == "thumb_in" else -1)
            elif key in ("rotate_left", "rotate_right", "delete_pages", "extract", "insert_blank", "insert_pdf", "insert_images", "crop_pages",
                         "resize", "replace", "duplicate", "pagebox", "delete_blank", "reverse", "rotate_pages", "split", "split_page"):
                pages = t.organize.selected_pages() if t.view_stack.currentWidget() is t.organize else []
                if not pages and t.side.isVisible() and t.side_key == "thumbs":
                    pages = t.thumbs.selected_pages()
                self.pages_action({"rotate_left": "rotate_left", "rotate_right": "rotate_right", "delete_pages": "delete", "extract": "extract",
                                   "crop_pages": "crop", "rotate_pages": "rotate", "delete_blank": "delete_blank"}.get(key, key), pages or [v.current])
            # 변환
            elif key.startswith("to_"): self.convert(t, key[3:])
            elif key == "convert_settings": self.convert(t, "docx")
            elif key == "batch": self.batch_dialog()
            # 도구
            elif key == "ocr": self.ocr(t)
            elif key == "merge": self.merge()
            elif key == "compare": self.compare(t)
            elif key == "compress": self.compress(t)
            elif key == "flatten": self.flatten(t)
            elif key == "deskew": self.deskew(t)
            elif key == "table": self.table_recognize(t)
            elif key == "extract_data": self.extract_data()
            elif key == "search": self.show_search()
            # 양식
            elif key == "recognize":
                n = A.recognize_fields(d); t.changed(None); self.say(f"양식 칸 {n}개를 만들었습니다")
            elif key == "form_export": self.form_data(t, "export")
            elif key == "form_import": self.form_data(t, "import")
            elif key == "form_reset": d.snapshot(); _engine("tools").reset_form(d.fitz); t.changed(None)   # 칸마다 기본값으로
            elif key == "form_set_default":
                d.snapshot(); n = _engine("tools").set_form_defaults(d.fitz); t.changed(None); self.say(f"{n}칸의 지금 값을 기본값으로 정했습니다")
            elif key == "form_preview": v.set_form_mode(False); self.ribbon.set_toggle("form_edit", False); self.say("미리보기: 칸을 두 번 눌러 값을 넣습니다")
            elif key.startswith("align_"):
                # 뷰어는 칸을 하나만 고르므로, 그 쪽의 칸을 창에서 여럿 골라 맞춘다
                pg = v.sel.page if v.sel and v.sel.kind == "widget" else v.current
                ws = [(w.xref, w.field_name, w.field_type_string) for w in d.fitz[pg].widgets()]
                if not ws: self.say("이 쪽에는 양식 칸이 없습니다"); return
                dlg = D.FieldAlignDialog(self, pg, ws, {v.sel.xref} if v.sel and v.sel.kind == "widget" else set(), key[6:])
                if not dlg.exec(): return
                vals = dlg.values()
                if not vals["xrefs"]: self.say("맞출 칸을 고르세요"); return
                d.snapshot(); _engine("tools").align_widgets(d.fitz, [(pg, x) for x in vals["xrefs"]], vals["how"]); t.changed([pg])
            # 보안
            elif key == "encrypt": self.encrypt(t)
            elif key == "decrypt": self.decrypt(t)
            elif key == "redact_search":
                dlg = D.RedactSearchDialog(self, d.page_count, v.current)
                if dlg.exec():
                    vals = dlg.values(); T = _engine("tools")
                    d.snapshot(); n = T.redact_search(d.fitz, vals["pattern"], regex=vals["regex"], pages=vals["pages"], fill=vals["fill"])
                    t.changed(None); self.say(f"{n}곳에 교정 표시를 했습니다. 「교정 적용」 을 누르면 지워집니다.")
            elif key == "redact_apply":
                if QMessageBox.question(self, "교정 적용", "교정 표시한 자리의 글자·그림을 영구히 지웁니다. 계속할까요?") == QMessageBox.StandardButton.Yes:
                    A.apply_redactions(d); t.changed(None)
            elif key == "hidden":
                dlg = D.HiddenDataDialog(self)
                if dlg.exec():
                    T = _engine("tools"); d.snapshot(); T.remove_hidden_data(d.fitz, **dlg.values()); t.changed(None, structural=True)
            elif key == "digital_sign":
                QMessageBox.information(self, "디지털 서명", "인증서 기반 디지털 서명은 넣지 않았습니다(공인 인증서·시점 확인 서버가 필요합니다).\n"
                                        "손글씨·도장 그림을 넣는 「서명 관리 → 서명 넣기」 를 쓰세요.")
            elif key in ("sign_new", "sign_apply"): self.on_tool("signature")
            elif key == "sign_clear": self.prefs.pop("signature_image", None); save_settings(self.settings)
            elif key == "crop": self.on_tool("crop")
            elif key == "stamp_pick": self.on_tool("stamp")
            elif key in ("stamp_create", "stamp_create_dyn", "stamp_manage", "stamp_identity"): self.stamp_dialog(key)
            elif key == "m_settings": self.measure_settings()
            elif key in TOOL_KEYS: self.on_tool(key)
            else: self.say(f"아직 없는 기능: {key}")
        except RuntimeError as e:
            QMessageBox.warning(self, "아직 못 씀", str(e))
        except ValueError as e:  # 엔진이 내는 입력 확인 문구(「찾을 글자를 넣어 주세요」 등)
            QMessageBox.warning(self, "확인", str(e))
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ in ("EngineError", "DocumentError"):  # 한국어로 쓴 안내(코드 흔적 없이)
                QMessageBox.warning(self, "확인", str(e)); return
            import traceback
            QMessageBox.critical(self, "오류", f"{e}\n\n{traceback.format_exc()[-1200:]}")

    # ── 페이지 동작(썸네일·구성·리본 공용) ───────────────────────
    def pages_action(self, key: str, pages: list[int]):  # noqa: C901
        t = self.cur()
        if not t:
            return
        d, v = t.doc, t.viewer
        pages = sorted(set(pages)) or [v.current]
        cur = pages[0]
        try:
            if key == "rotate_left": P.rotate(d, pages, -90); t.changed(pages, structural=True)
            elif key == "rotate_right": P.rotate(d, pages, 90); t.changed(pages, structural=True)
            elif key == "rotate":
                dlg = O.RotateDialog(self, d, v.current, pages)
                if dlg.exec() and dlg.values()["degrees"]:
                    vals = dlg.values(); P.rotate(d, vals["pages"], vals["degrees"]); t.changed(vals["pages"], structural=True)
            elif key == "delete":
                if not O.confirm_delete(self, d.page_count, len(pages)): return
                gone = set(pages); keep = [i for i in range(d.page_count) if i not in gone]
                P.delete(d, pages); v.clear_selection(); t.changed(None, structural=True, mapping=keep)
            elif key == "extract":
                self._extract(t, pages)
            elif key == "insert_blank":
                dlg = O.PlaceDialog(self, "빈 페이지 삽입", P.labels(d), v.current, self.prefs)
                if dlg.exec(): save_settings(self.settings); P.insert_blank(d, dlg.at()); t.changed(None, structural=True)
            elif key == "insert_pdf":
                paths, _ = QFileDialog.getOpenFileNames(self, "PDF에서 삽입", "", OPEN_FILTER)
                srcs = O.open_sources(self, paths, d.path) if paths else None
                if not srcs: return
                row, rng = O.source_range_row(srcs[0][2]); row.setVisible(len(srcs) == 1)
                dlg = O.PlaceDialog(self, "PDF에서 삽입", P.labels(d), v.current, self.prefs, row)
                if dlg.exec():
                    save_settings(self.settings)
                    sub = P.parse_range(rng.text(), srcs[0][2]) if len(srcs) == 1 and rng.text().strip() else None
                    n = P.insert_pdfs(d, dlg.at(), [(p, pw, sub) for p, pw, _ in srcs]); t.changed(None, structural=True)
                    self.say(f"{len(srcs)}개 문서에서 {n}쪽을 끼웠습니다")
            elif key == "insert_images":
                paths, _ = QFileDialog.getOpenFileNames(self, "끼울 이미지", "", "이미지 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp)")
                if paths: P.insert_images(d, cur + 1, paths); t.changed(None, structural=True)
            elif key == "duplicate":
                dup = set(pages); m = [k for i in range(d.page_count) for k in ((i, i) if i in dup else (i,))]
                P.duplicate(d, pages); t.changed(None, structural=True, mapping=m)
            elif key == "replace":
                dlg = O.ReplaceDialog(self, d, pages)
                if dlg.exec(): vals = dlg.values(); P.replace(d, **vals); v.clear_selection(); t.changed(None, structural=True)
            elif key == "crop":
                dlg = PB.CropDialog(self, d, pages, self.prefs.get("unit", "cm"))
                if dlg.exec(): vals = dlg.values(); P.crop(d, vals["pages"], margins=vals["margins"]); t.changed(None, structural=True)
            elif key == "resize":
                dlg = PB.PageSizeDialog(self, d, pages, self.prefs.get("unit", "cm"))
                if dlg.exec(): vals = dlg.values(); P.resize(d, vals["pages"], vals["width"], vals["height"], vals["fit"]); t.changed(None, structural=True)
            elif key == "pagebox":
                dlg = PB.PageBoxDialog(self, d, pages, self.prefs.get("unit", "cm"))
                if dlg.exec():
                    vals = dlg.values(); P.set_boxes(d, vals["pages"], vals["margins"], vals["size"], vals["offset"]); t.changed(None, structural=True)
            elif key == "pagelabel": self.on_action("pagelabel")
            elif key == "delete_blank":
                T = _engine("tools"); d.snapshot(); gone = T.delete_blank_pages(d.fitz)
                t.changed(None, structural=True); self.say(f"빈 쪽 {len(gone)}장을 지웠습니다")
            elif key == "reverse":
                rev = list(range(d.page_count - 1, -1, -1)); P.reorder(d, rev); t.changed(None, structural=True, mapping=rev)
            elif key == "split_page":
                from .dlg_splitpage import run_split_page
                run_split_page(self, t, pages)
            elif key == "split": DS.run_split(self, t)
        except ValueError as e:
            QMessageBox.warning(self, "페이지", str(e))
        except RuntimeError as e:
            QMessageBox.warning(self, "아직 못 씀", str(e))

    # ── 뷰어가 부탁한 대화상자 ─────────────────────────────────
    def on_request(self, t: DocTab, kind: str, data):  # noqa: C901
        d, v = t.doc, t.viewer
        if kind == "textbox":
            # 누른 자리에서 바로 쓰고, 다 쓰면 그 종류의 기본 모양으로 굽는다(빈 글이면 안 남김)
            st = comment_style(data["kind"], t.props.store.get(data["kind"]), self.prefs.get("comment_font"))
            def done(text: str, rect, callout, t=t, i=data["page"], k=data["kind"], st=st):
                if t.viewer.closed:
                    return
                if text.strip():
                    A.free_text(t.doc, i, rect, text, st["fontsize"], st["text_color"], st.get("fill"), st.get("border"),
                                st.get("width", 0), st.get("opacity", 1.0), callout=callout,
                                author=A.user_author(t.viewer.props["author"]), typewriter=(k == "typewriter"),
                                rotate=t.viewer.text_rotation(i))
                    t.changed([i])
                t.viewer.toolDone.emit(k)
            sp = data.get("scene_pt") or v.from_page_rect(data["page"], data["rect"]).topLeft()   # 끌어 그린 네모면 왼쪽 위
            InlineAnnot(v, data["page"], data["kind"], st, done, scene_pt=sp, drag=data.get("scene_rect"))
        elif kind == "textbox_edit":
            s = data
            info = A.freetext_info(d, s.page, s.xref)
            if info is None or info["subject"] == "Stamp":
                return self.on_request(t, "annot_props", s)
            v.clear_selection()
            A.peek_hide(d, s.page, s.xref, True); v.refresh_pages([s.page])   # 고치는 동안 쪽의 옛 글을 숨김
            def done(text: str, rect, _callout, t=t, s=s, old=info["text"]):
                A.peek_hide(t.doc, s.page, s.xref, False)
                if not t.viewer.closed and text != old:
                    A.edit_free_text(t.doc, s.page, s.xref, text, rect); t.changed([s.page])
                elif not t.viewer.closed:
                    t.viewer.refresh_pages([s.page])
            InlineAnnot(v, s.page, info["kind"], info, done, box=info["box"], text=info["text"], callout=info["callout"])
        elif kind in ("note", "note_open"):
            # 노트 팝업(내용·색·답글). 노트 도구로 이미 있는 노트를 누르면 그 노트를 연다
            i, pt = (data.page, fitz.Point(data.rect.tl)) if kind == "note_open" else (data["page"], data["point"])
            hit = data.xref if kind == "note_open" else next(
                (a.xref for a in d.fitz[i].annots() if a.type[1] == "Text" and not A.is_reply(d, a.xref)
                 and fitz.Rect(a.rect).contains(pt)), None)
            if hit is not None and kind == "note":
                pt = fitz.Point(A.get(d, i, hit).rect.tl)
            NotePopup(v, i, pt, hit, t.props.store.get("note")["color"], v.props["author"], lambda p, t=t: t.changed([p]))
        elif kind == "addtext":
            def done(text: str, rect, style: dict, t=t, i=data["page"]):
                self.prefs["addtext_style"] = style; save_settings(self.settings)   # 마지막 서식 기억
                if t.viewer.closed:
                    return
                if A.add_text(t.doc, i, rect, text, style["fontsize"], style["color"], style["bold"], style["align"],
                              italic=style["italic"], rotate=t.viewer.text_rotation(i)):
                    t.changed([i])
                self.on_tool("select" if not t.viewer.edit_mode else "edit")
            InlineText(v, data["page"], data["scene_pt"], self.prefs.get("addtext_style"), done, data.get("width"))
        elif kind == "edit_block":
            s = data
            dlg = D.TextBoxDialog(self, "edit_block", {"fontsize": s.extra.get("fontsize", 11), "text_color": (0, 0, 0)}, s.text)
            dlg.bold.setChecked(s.extra.get("bold", False))
            if dlg.exec():
                vals = dlg.values()
                A.edit_block(d, s.page, s.rect, vals["text"], vals["fontsize"], vals["text_color"] or (0, 0, 0), vals["bold"], vals["align"])
                v.clear_selection(); t.changed([s.page])
        elif kind == "addimage":
            p = v.props.get("image_path") or QFileDialog.getOpenFileName(self, "넣을 이미지", "", "이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.webp)")[0]
            if p:
                A.add_image(d, data["page"], data["rect"], p); t.changed([data["page"]])
            self.on_tool("select" if not v.edit_mode else "edit")
        elif kind == "replace_image":
            s = data
            p, _ = QFileDialog.getOpenFileName(self, "바꿀 이미지", "", "이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.webp)")
            if p:
                if s.extra.get("xref"):
                    A.replace_image(d, s.page, s.extra["xref"], p)
                else:
                    A.delete_image(d, s.page, s.rect); A.add_image(d, s.page, s.rect, p)
                v.clear_selection(); t.changed([s.page])
        elif kind == "save_image":
            s = data
            out, _ = QFileDialog.getSaveFileName(self, "이미지 저장", "이미지.png", "PNG (*.png)")
            if out:
                if s.extra.get("xref"):
                    pix = fitz.Pixmap(d.fitz, s.extra["xref"])
                    if pix.n - pix.alpha >= 4: pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(out)
                else:
                    d.render(s.page, 3.0, clip=s.rect).save(out)
                self.say(f"저장했습니다: {out}")
        elif kind == "caret":   # 대화상자 없이 바로 단다(내용은 속성 패널·주석 목록에서)
            A.caret(d, data["page"], data["point"], "", A.user_author(v.props["author"]),
                    v.props.get("caret_color", (0.5, 0, 0.5)), v.props["opacity"]); t.changed([data["page"]])
            self._tool_done("caret")
        elif kind == "attach":
            p, _ = QFileDialog.getOpenFileName(self, "첨부할 파일")
            if p:
                A.attachment(d, data["page"], data["point"], p, v.props["author"], v.props.get("attach_icon", "PushPin"),
                             v.props.get("attach_color"), v.props.get("opacity")); t.changed([data["page"]])
            self._tool_done("attach")
        elif kind == "link":
            from .dlg_link import LinkDialog
            dlg = LinkDialog(self, d, v.current, self.prefs, view_dest=v.link_dest_here())
            if dlg.exec():
                vals = dlg.values()
                A.add_link(d, data["page"], data["rect"], style=vals["style"], **vals["target"])
                dlg.remember(); save_settings(self.settings); t.changed([data["page"]])
            self.on_tool("select")
        elif kind == "edit_link":
            from .dlg_link import LinkDialog
            s = data
            info = A.link_info(d, s.page, s.xref)
            if info is None:
                return
            dlg = LinkDialog(self, d, v.current, self.prefs, info=info, view_dest=v.link_dest_here())
            if dlg.exec():
                vals = dlg.values()
                s.xref = A.update_link(d, s.page, s.xref, target=vals["target"], style=vals["style"]) or s.xref
                s.text = v.link_hover_text(A.link_info(d, s.page, s.xref))
                dlg.remember(); save_settings(self.settings); t.changed([s.page])
        elif kind == "crop":
            cb = d.fitz[data["page"]].cropbox
            dlg = D.CropDialog(self, d.page_count, data["page"], data["rect"], fitz.Rect(0, 0, cb.width, cb.height))
            if dlg.exec():
                vals = dlg.values(); P.crop(d, vals["pages"], vals["rect"]); t.changed(None, structural=True)
            self.on_tool("select")
        elif kind == "ocrarea":
            try:
                O = _engine("ocr")
                text = O.ocr_area(d.fitz, data["page"], data["rect"], lang=self.prefs.get("ocr_lang", "ko"))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "영역 OCR", str(e)); return
            dlg = D.FormDialog("영역 OCR 결과", self, "복사")
            from PySide6.QtWidgets import QPlainTextEdit
            te = QPlainTextEdit(text); te.setMinimumSize(420, 200); dlg.form.addRow(te)
            if dlg.exec():
                QApplication.clipboard().setText(te.toPlainText()); self.say("복사했습니다")
            self.on_tool("select")
        elif kind == "annot_props":
            s = data
            dlg = D.AnnotPropsDialog(self, s)
            if dlg.exec():
                vals = dlg.values()
                A.update(d, s.page, s.xref, stroke=vals["stroke"], fill=vals["fill"], width=vals["width"], opacity=vals["opacity"],
                         content=vals["content"], author=vals["author"])
                t.changed([s.page])
        elif kind == "field_props":
            s = data
            pg = d.fitz[s.page]  # 쪽 객체를 붙잡아 둔다(놓치면 칸이 풀려 w.update 가 멈춘다)
            w = next((w for w in pg.widgets() if w.xref == s.xref), None)
            if not w: return
            radio = w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
            dlg = D.FieldPropsDialog(self, w.field_name, w.field_type_string, w.field_value, w.choice_values or [], radio=radio)
            if dlg.exec():
                vals = dlg.values(); d.snapshot()
                if w.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX): w.choice_values = vals["options"]
                if w.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    v = vals["value"].strip()
                    w.field_value = v.lower() in ("yes", "on", "true", "1", "예") or v == (w.on_state() or "")
                elif w.field_type != fitz.PDF_WIDGET_TYPE_BUTTON: w.field_value = vals["value"]
                A.save_widget(w)
                A.rename_field(d, s.page, s.xref, vals["name"])  # 라디오는 같은 이름 묶음으로 옮겨 간다
                t.changed([s.page])
        elif kind == "fill_field":
            w = data["widget"]
            val = D.fill_field(self, w)
            if val is not None:
                A.set_field_value(d, data["page"], data["xref"], val); t.changed([data["page"]])
        elif kind == "open_uri":
            if QMessageBox.question(self, "링크", f"이 주소를 열까요?\n{data}") == QMessageBox.StandardButton.Yes:
                webbrowser.open(data)
        elif kind == "open_file":
            if data and os.path.exists(data) and QMessageBox.question(self, "링크", f"이 파일을 열까요?\n{data}") == QMessageBox.StandardButton.Yes:
                os.startfile(data)
        elif kind == "action": self.on_action(data)
        elif kind == "search_text": self.quick_search(data)
        elif kind == "bookmark_text":
            if not (t.side.isVisible() and t.side_key == "bookmark"): self.toggle_side("bookmark")
            t.bookmarks.add(data["text"], data["page"])
        elif kind == "rotate": P.rotate(d, data["pages"], data["deg"]); t.changed(list(data["pages"]), structural=True)
        elif kind == "delete_pages": self.pages_action("delete", data["pages"])

    # ── 파일 메뉴 ─────────────────────────────────────────────
    def show_file_menu(self):
        m = QMenu(self)
        m.setMinimumWidth(240)
        t = self.cur()
        m.addAction(icons.icon("open"), "열기" + self.key_text("open"), self.open_dialog)
        rm = m.addMenu(icons.icon("recent"), "최근 파일 열기")
        for r in self.settings["recent"][:15]:
            rm.addAction(os.path.basename(r["path"]), lambda p=r["path"]: self.open_path(p))
        cm = m.addMenu(icons.icon("create"), "생성")
        cm.addAction("파일에서 생성" + self.key_text("create_files"), lambda: self.on_action("create_files"))
        cm.addAction("빈 PDF" + self.key_text("create_blank"), lambda: self.on_action("create_blank"))
        cm.addAction("클립보드에서 가져오기", lambda: self.on_action("create_clipboard")).setEnabled(
            F.clipboard_kind(QApplication.clipboard().mimeData()) is not None)
        cm.addAction("스캐너에서 생성", lambda: self.on_action("create_scan"))
        m.addSeparator()
        a = m.addAction(icons.icon("save"), "저장" + self.key_text("save"), lambda: self.on_action("save")); a.setEnabled(bool(t))
        a = m.addAction(icons.icon("saveas"), "다른 이름으로 저장" + self.key_text("saveas"), lambda: self.on_action("saveas")); a.setEnabled(bool(t))
        vm = m.addMenu("다른 버전으로 저장"); vm.setEnabled(bool(t))
        vm.addAction("PDF/A", lambda: self.on_action("to_pdfa")); vm.addAction("압축한 PDF", lambda: self.on_action("compress")); vm.addAction("평면화한 PDF", lambda: self.on_action("flatten"))
        sm = m.addMenu(icons.icon("folder"), "최근 폴더에 저장"); sm.setEnabled(bool(t))
        seen = []
        known = self.start.stats.known   # 디스크를 여기서 보지 않는다(닿지 않는 공유폴더가 메뉴를 몇 초씩 붙잡음): 있다고 확인된 파일의 폴더만
        for r in self.settings["recent"]:
            dd = os.path.dirname(r["path"])
            if dd not in seen and (known.get(r["path"]) or (False,))[0]:
                seen.append(dd); sm.addAction(dd, lambda p=dd: self.save_to_dir(p))
            if len(seen) >= 8: break
        if self.prefs.get("share_dir"):
            m.addAction(icons.icon("share"), "공유폴더에 저장", lambda: self.save_to_dir(self.prefs["share_dir"])).setEnabled(bool(t))
        m.addSeparator()
        em = m.addMenu(icons.icon("export"), "내보내기"); em.setEnabled(bool(t))
        for label, key in (("Word", "to_docx"), ("Excel", "to_xlsx"), ("PowerPoint", "to_pptx"), ("이미지", "to_png"), ("HTML", "to_html"),
                           ("RTF", "to_rtf"), ("텍스트", "to_txt"), ("Epub", "to_epub"), ("PDF/A", "to_pdfa"), ("한글(HWPX)", "to_hwpx")):
            em.addAction(label, lambda k=key: self.on_action(k))
        a = m.addAction(icons.icon("print"), "인쇄하기" + self.key_text("print"), lambda: self.on_action("print")); a.setEnabled(bool(t))
        a = m.addAction(icons.icon("mail"), "메일로 보내기", lambda: self.on_action("mail")); a.setEnabled(bool(t))
        m.addSeparator()
        a = m.addAction(icons.icon("info"), "파일 속성" + self.key_text("properties"), lambda: self.on_action("properties")); a.setEnabled(bool(t))
        m.addAction(icons.icon("settings"), "환경 설정" + self.key_text("preferences"), lambda: self.on_action("preferences"))
        m.addAction(icons.icon("update"), self.updates.menu_label(), self.updates.check_now)
        m.addSeparator()
        a = m.addAction("닫기" + self.key_text("close"), lambda: self.on_action("close")); a.setEnabled(bool(t))
        m.addAction("끝내기", self.close)
        m.exec(self.ribbon.mapToGlobal(self.ribbon.rect().bottomLeft() - self.ribbon.rect().bottomLeft() + QPointF(6, 30).toPoint()))

    def _extract(self, t: DocTab, pages: list[int]):
        """페이지 내보내기: 고른 쪽을 임시 폴더에 PDF 하나로 저장해 새 탭으로 연다(저장 위치는 그 탭을 저장할 때 묻는다)."""
        from .dlg_extract import ExtractDialog
        d = t.doc
        dlg = ExtractDialog(self, d.page_count, t.viewer.current, pages, bool(d.fitz.permissions & fitz.PDF_PERM_MODIFY))
        if not dlg.exec():
            return
        vals = dlg.values()
        folder = os.path.join(data_dir(), "Temp", uuid.uuid4().hex)
        os.makedirs(folder, exist_ok=True)
        out = P.extract_name(folder, d.name)
        if D.ProgressDialog(self, "페이지 내보내기", P.extract, d, vals["pages"], out, creator=APP_NAME).run() is None:
            return
        new = Document(out, d.password)
        new.ask_save = True   # 「저장」 해도 임시 폴더에 그냥 쓰지 않고 저장 위치를 묻는다
        new.save_hint = os.path.join(os.path.dirname(d.path), os.path.basename(out)) if d.path else None
        new.modified = True
        self.add_doc(new)
        if vals["delete"]:
            gone = set(vals["pages"]); keep = [i for i in range(d.page_count) if i not in gone]
            P.delete(d, vals["pages"]); t.viewer.clear_selection(); t.changed(None, structural=True, mapping=keep)
        self.say(f"{len(vals['pages'])}쪽을 새 탭으로 내보냈습니다")

    # ── 저장 ──────────────────────────────────────────────────
    def save(self, t: DocTab, ask: bool = False) -> bool:
        path = t.doc.path
        must_ask = ask or not path or getattr(t.doc, "ask_save", False) or not path.lower().endswith(".pdf")
        if not must_ask and not t.doc.modified:   # 고친 것이 없으면 다시 쓰지 않는다(수정 시각 그대로)
            return True
        while True:
            if must_ask:
                path, _ = QFileDialog.getSaveFileName(self, "다른 이름으로 저장" if ask else "저장", F.save_suggestion(self, t.doc), PDF_FILTER)
                if not path:
                    return False
                if F.bad_path(self, path):
                    continue
            try:
                t.doc.save(path)
                break
            except FileLocked as e:   # 읽기 전용·다른 프로그램이 잡은 파일: 다른 이름으로 넘긴다
                if not F.ask_save_as_instead(self, str(e)):
                    return False
                must_ask = True
            except Exception as e:  # noqa: BLE001
                F.save_error(self, e)
                return False
        t.doc.ask_save = False; t.doc.save_hint = None
        self.autosave.drop(t.doc)
        self.settings["last_open_dir"] = os.path.dirname(path)
        self.add_recent(path)
        i = self.tabs.index(t)
        self.doctabs.setTabText(i, t.doc.name); self.doctabs.setTabToolTip(i, path)
        self.setWindowTitle(f"{t.doc.name} - {APP_NAME}")
        t.viewer.refresh_pages(None)
        self.say(f"저장했습니다: {path}")
        return True

    def save_to_dir(self, folder: str):
        t = self.cur()
        if not t:
            return
        path = os.path.join(folder, t.doc.name if t.doc.name.lower().endswith(".pdf") else "문서.pdf")
        if os.path.exists(path) and QMessageBox.question(self, "저장", f"같은 이름이 있습니다. 덮어쓸까요?\n{path}") != QMessageBox.StandardButton.Yes:
            return
        try:
            t.doc.save(path)
        except Exception as e:  # noqa: BLE001
            F.save_error(self, e); return
        self.autosave.drop(t.doc); self.add_recent(path); self.say(f"저장했습니다: {path}")

    def _suggest(self, t: DocTab, suffix: str) -> str:
        base = os.path.splitext(t.doc.path)[0] if t.doc.path else os.path.join(self.prefs.get("default_dir") or F.save_dir(self), os.path.splitext(t.doc.name)[0])
        return base + suffix

    def _offer_open(self, path: str, title: str = "완료"):
        """끝났다는 알림: [확인] · [폴더 열기]."""
        from .dlg_convert import success_box
        success_box(self, path, title)

    # ── 생성 ──────────────────────────────────────────────────
    def create_blank(self):
        """창 없이 환경 설정 「빈 PDF」 값으로(기본 A4 세로 한 쪽 「제목 없음」). 열린 문서와 이름이 겹치면 「제목 없음-2」."""
        name, count, w, h = F.blank_spec(self.prefs)
        doc = Document.blank(w, h, count)
        doc.title = F.unique_title(F.open_names(self), name)
        self.add_doc(doc)

    def create_from_files(self):
        """열기 창으로 고르고(둘 이상이면 순서 확인 창) 저장 안 된 새 탭으로 만든다."""
        start = self.settings.get("last_create_dir") or ""
        paths, _ = QFileDialog.getOpenFileNames(self, "파일에서 생성", start if F.safe_dir(self, start) else F.open_start_dir(self), F.CREATE_FILTER)
        if not paths:
            return
        self.settings["last_create_dir"] = os.path.dirname(paths[0])
        if len(paths) > 1:
            dlg = D.FileListDialog(self, "파일에서 생성", "만들기", F.CREATE_FILTER, paths=paths)
            if not dlg.exec() or not dlg.paths():
                return
            paths = dlg.paths()
        self.create_from_paths(paths)

    def create_from_paths(self, paths: list[str]):
        """파일들을 PDF 로 바꿔 저장 안 된 새 문서로 연다(저장할 때 원래 폴더에 그 이름으로 묻는다)."""
        C = _engine("convert")
        name = F.created_name(paths)
        out = F.temp_out(name)
        dlg = D.ProgressDialog(self, "PDF 생성", C.from_files, paths, out)
        dlg.worker.start(); dlg.exec(); dlg.worker.wait(3000)
        if dlg.error or not os.path.isfile(out):
            why = (dlg.error or "").lstrip("!").split("\n\n")[0]
            QMessageBox.critical(self, "PDF 생성 실패", "오류! PDF를 작성하지 못했습니다." + (f"\n{why}" if why else ""))
            return
        try:
            doc = Document(out)
        except DocumentError as e:
            QMessageBox.critical(self, "PDF 생성 실패", f"오류! PDF를 작성하지 못했습니다.\n{e}"); return
        doc.ask_save = True
        doc.save_hint = os.path.join(os.path.dirname(paths[0]), name)
        doc.modified = True
        self.add_doc(doc)

    def create_from_clipboard(self):
        cb = QApplication.clipboard()
        md = cb.mimeData()
        kind = F.clipboard_kind(md)
        if kind == "files":   # 파일 목록: 「파일에서 생성」 과 같은 길
            self.create_from_paths([u.toLocalFile() for u in md.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]); return
        if kind == "image":
            img = cb.image()
            pfd, png = tempfile.mkstemp(suffix=".png"); os.close(pfd); img.save(png)
            pdfbytes = fitz.open(png).convert_to_pdf(); os.remove(png)
            # 이름(경로)을 주면 Ctrl+S 가 묻지 않고 지금 폴더에 「클립보드.pdf」 로 저장해 버린다
            doc = Document.from_bytes(pdfbytes)
        elif kind == "text":   # 「파일에서 생성」 의 txt 길처럼 쪽을 늘려 가며 흘린다
            doc = Document.from_bytes(_engine("convert").text_to_pdf_bytes(md.text()))
        else:
            self.say("클립보드에 그림이나 글자가 없습니다"); return
        self.add_doc(doc)

    # ── 변환·도구 ─────────────────────────────────────────────
    def convert(self, t: DocTab, fmt: str):
        from .dlg_convert import ConvertDialog, FORMATS
        d, v = t.doc, t.viewer
        store = self.settings.setdefault("convert", {})
        dlg = ConvertDialog(self, d.page_count, v.current, fmt if fmt in {k for _, k, _ in FORMATS} else "docx", d.name, d.path, store)
        if not dlg.exec():
            return
        save_settings(self.settings)
        vals = dlg.values(); fmt = vals["fmt"]; out = vals["out"]; pages = vals["pages"]
        folder, stem = vals["folder"], vals["stem"]
        C = _engine("convert")
        src = d.copy()  # 지금 상태 그대로 변환(열린 문서를 그냥 save 하면 암호 문서가 망가진다)
        dpi = vals["dpi"] or None
        label = dlg.fmt.currentText().split(" (")[0]

        def job(progress=None):
            try:
                if pages and fmt in ("rtf", "pdfa", "hwpx", "xml"):
                    src.select(pages)  # 이 넷은 쪽 범위를 받지 않아 고른 범위가 무시됐다
                if fmt in ("jpg", "jpeg", "png", "bmp", "gif", "tiff"):
                    if vals["mode"] == "extract":
                        return C.extract_images(src, folder, pages=pages, progress=progress, stem=stem)
                    return C.to_images(src, folder, fmt=fmt, dpi=dpi, pages=pages, progress=progress, stem=stem,
                                       merge=vals["mode"] == "merge")
                if fmt == "docx":
                    if vals["ocr"] != "off":
                        self._ocr_before_word(src, pages, vals["ocr"] == "all", progress)
                    return C.to_word(src, out, pages=pages, progress=progress)
                if fmt == "xlsx": return C.to_excel(src, out, pages=pages, progress=progress)
                if fmt == "pptx": return C.to_pptx(src, out, pages=pages, dpi=vals["pptx_dpi"], progress=progress)
                if fmt == "txt": return C.to_text(src, out, pages=pages)
                if fmt == "html": return C.to_html(src, out, pages=pages)
                if fmt == "xml": return C.to_xml(src, out)
                if fmt == "rtf": return C.to_rtf(src, out)
                if fmt == "epub": return C.to_epub(src, out, pages=pages)
                if fmt == "pdfa": return C.to_pdfa(src, out)
                if fmt == "hwpx": return C.to_hwpx(src, out, progress=progress)
                raise RuntimeError(f"모르는 형식: {fmt}")
            finally:
                src.close()
        res = D.ProgressDialog(self, f"{label}로 변환", job).run()
        if res:
            files = res if isinstance(res, list) else [res]
            shown = files[0] if len(files) == 1 else folder
            self.say(f"변환했습니다: {shown}")
            self._offer_open(shown, "변환 성공")

    def _ocr_before_word(self, src, pages, force: bool, progress=None):
        """Word 로 내보내기 전에 글자층이 없는 쪽(force 면 모든 쪽)을 인식해 넣는다. 인식이 안 되는 PC 면 그냥 넘어간다."""
        O = _engine("ocr")
        try:
            O.ocr_pages(src, pages=pages, lang=self.prefs.get("ocr_lang") or "ko", dpi=300, progress=progress, force=force)
        except Exception:  # noqa: BLE001
            pass

    def run_tool(self, t: DocTab, fn_name: str, dlg):
        """워터마크·배경·머리글·베이츠처럼 문서를 바로 고치는 도구."""
        if not dlg.exec():
            return
        vals = dlg.values()
        T = _engine("tools")
        t.doc.snapshot()
        getattr(T, fn_name)(t.doc.fitz, **vals)
        t.changed(None)

    def ocr(self, t: DocTab):
        O = _engine("ocr")
        try:
            langs = O.available_languages()
        except Exception:  # noqa: BLE001
            langs = ["ko", "en"]
        dlg = D.OcrDialog(self, t.doc.page_count, t.viewer.current, langs)
        if not dlg.exec():
            return
        vals = dlg.values()
        # 일꾼에는 사본을 준다. 보는 문서를 그대로 주면 그리는 일꾼과 같은 문서를 동시에 만져 튕길 수 있다
        work = t.doc.copy()
        n = D.ProgressDialog(self, "OCR", O.ocr_pages, work, pages=vals["pages"], lang=vals["lang"], dpi=vals["dpi"], force=vals["force"]).run()
        if n is not None:
            t.doc.adopt(work)   # 되돌리기 사본을 뜬 뒤 바꿔 끼운다
            t.changed(None); self.say(f"글자 {n}개를 인식해 넣었습니다. 이제 찾기·복사가 됩니다.")
        else:
            work.close()

    def merge(self):
        t = self.cur()
        dlg = D.FileListDialog(self, "PDF 병합", "합치기", OPEN_FILTER, paths=[t.doc.path] if t and t.doc.path else None)
        if not dlg.exec() or len(dlg.paths()) < 1:
            return
        paths = dlg.paths()
        out, _ = QFileDialog.getSaveFileName(self, "합친 PDF", os.path.splitext(paths[0])[0] + "_합침.pdf", PDF_FILTER)
        if not out:
            return
        res = D.ProgressDialog(self, "PDF 병합", P.merge, paths, out).run()
        if res:
            self.open_path(out)

    def compare(self, t: DocTab):
        p, _ = QFileDialog.getOpenFileName(self, "비교할 다른 PDF", "", PDF_FILTER)
        if not p:
            return
        other = self._open_other(p)
        if other is None:
            return
        T = _engine("tools")
        mine = t.doc.copy()  # 보는 문서는 그리는 일꾼이 같이 쓴다. 비교 일꾼에는 사본을 준다
        res = D.ProgressDialog(self, "PDF 비교", T.compare, mine, other.fitz).run()
        mine.close(); other.close()
        if res is not None:
            D.CompareDialog(self, t.doc.name, os.path.basename(p), res).exec()

    def _open_other(self, path: str) -> Document | None:
        """비교 등에 쓸 다른 PDF 를 연다. 암호가 걸려 있으면 물어서 열고, 틀리면 다시 묻는다."""
        pw = None
        while True:
            try:
                return Document(path, pw)
            except NeedsPassword:
                if pw is not None:
                    QMessageBox.warning(self, "암호", "암호가 맞지 않습니다")
                pw = D.ask_password(self, os.path.basename(path))
                if pw is None:
                    return None
            except DocumentError as e:
                QMessageBox.critical(self, "열기", str(e))
                return None

    def compress(self, t: DocTab):
        size = os.path.getsize(t.doc.path) if t.doc.path and os.path.exists(t.doc.path) else len(t.doc.to_bytes())
        dlg = D.CompressDialog(self, size, self.prefs.get("compress_level", "high"), self.prefs.get("compress_meta", True))
        if not dlg.exec():
            return
        vals = dlg.values()
        self.prefs["compress_level"] = vals["level"]; self.prefs["compress_meta"] = vals["remove_metadata"]; save_settings(self.settings)
        out, _ = QFileDialog.getSaveFileName(self, "압축한 PDF", self._suggest(t, "_압축.pdf"), PDF_FILTER)
        if not out:
            return
        T = _engine("tools")
        src = t.doc.copy()
        # 못 줄이면 엔진이 파일을 만들지 않고 NotSmaller 를 낸다(ProgressDialog 가 그 문구를 띄움)
        res = D.ProgressDialog(self, "PDF 압축", T.compress, src, out, password=t.doc.password, before=size, **vals).run()
        src.close()
        if res:
            QMessageBox.information(self, "PDF 압축", f"{size / 1024:.0f} KB → {res['after'] / 1024:.0f} KB "
                                    f"({(1 - res['after'] / size) * 100:.0f}% 줄임)\n{out}")
            self.open_path(out)

    def flatten(self, t: DocTab):
        out, _ = QFileDialog.getSaveFileName(self, "평면화한 PDF", self._suggest(t, "_평면화.pdf"), PDF_FILTER)
        if not out:
            return
        T = _engine("tools")
        src = t.doc.copy()
        res = D.ProgressDialog(self, "평면화", T.flatten, src, out, password=t.doc.password).run()
        src.close()
        if res:
            self.open_path(out)

    def deskew(self, t: DocTab):
        T = _engine("tools")
        work = t.doc.copy()   # OCR 과 같은 까닭으로 사본에서
        angles = D.ProgressDialog(self, "기울기 보정", T.deskew, work).run()
        if angles is None:
            work.close()
        else:
            t.doc.adopt(work)
            t.changed(None, structural=True); self.say(f"{sum(1 for a in angles if abs(a) > 0.1)}쪽을 바로잡았습니다")

    def table_recognize(self, t: DocTab):
        O = _engine("ocr")
        rows = O.recognize_table(t.doc.fitz, t.viewer.current)
        if not rows:
            self.say("표를 찾지 못했습니다"); return
        out, _ = QFileDialog.getSaveFileName(self, "표 저장", self._suggest(t, f"_{t.viewer.current + 1}쪽_표.xlsx"), "Excel (*.xlsx)")
        if out:
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active
            for r in rows: ws.append(r)
            wb.save(out); self._offer_open(out)

    def extract_data(self):
        dlg = D.FileListDialog(self, "데이터 추출(양식 값 모으기)", "추출", PDF_FILTER)
        if not dlg.exec() or not dlg.paths():
            return
        out, _ = QFileDialog.getSaveFileName(self, "엑셀로 저장", "양식 데이터.xlsx", "Excel (*.xlsx)")
        if out:
            T = _engine("tools"); T.extract_data(dlg.paths(), out); self._offer_open(out)

    def form_data(self, t: DocTab, how: str):
        T = _engine("tools")
        if how == "export":
            # FDF·XFDF 는 다른 PDF 편집기·아크로뱃과 주고받는 표준 꼴(기본), JSON·CSV 는 엑셀 등으로 볼 때
            out, sel = QFileDialog.getSaveFileName(self, "양식 데이터 내보내기", self._suggest(t, ".fdf"),
                                                   "FDF (*.fdf);;XFDF (*.xfdf);;JSON (*.json);;CSV (*.csv)")
            if out:
                ext = {"FDF": ".fdf", "XFDF": ".xfdf", "JSON": ".json", "CSV": ".csv"}.get(sel.split(" ")[0], "")
                if ext and not out.lower().endswith(ext): out = os.path.splitext(out)[0] + ext
                T.export_form_data(t.doc.fitz, out, pdf_name=t.doc.name); self.say(f"양식 데이터를 저장했습니다: {out}")
        else:
            p, _ = QFileDialog.getOpenFileName(self, "양식 데이터 가져오기", "", "양식 데이터 (*.fdf *.xfdf *.json *.csv)")
            if p:
                t.doc.snapshot(); n = T.import_form_data(t.doc.fitz, p); t.changed(None); self.say(f"{n}칸을 채웠습니다")

    def batch_dialog(self):
        dlg = D.BatchDialog(self)
        if not dlg.exec():
            return
        job = dlg.values()
        if job["op"] == "print":  # 윈도우 「인쇄」 동작에 맡기면 PDF 기본 앱에 따라 늘 실패해서 앱이 직접 보낸다
            if not job["inputs"]:
                self.say("인쇄할 파일을 고르세요"); return
            self._batch_print(job["inputs"], job["options"].get("printer") or QPrinterInfo.defaultPrinterName()); return
        if not job["inputs"] or not job["out_dir"]:
            self.say("파일과 저장 폴더를 고르세요"); return
        T = _engine("tools")
        res = D.ProgressDialog(self, "일괄 작업", T.batch, [job]).run()
        if res is not None:
            ok = sum(1 for r in res if r.get("ok")); bad = [r for r in res if not r.get("ok")]
            msg = f"{ok}개 끝남" + (f", {len(bad)}개 실패:\n" + "\n".join(f"{os.path.basename(r['input'])}: {r.get('error')}" for r in bad[:8]) if bad else "")
            QMessageBox.information(self, "일괄 작업", msg); self._offer_open(job["out_dir"])

    def encrypt(self, t: DocTab):
        T = _engine("tools")
        dlg = D.EncryptDialog(self, T.encryption_method(t.doc.fitz))
        if not dlg.exec():
            return
        vals = dlg.values()
        if not dlg.to_new_file():
            # 일반적인 PDF 편집기처럼 열린 문서에 걸어 두고 다음 저장 때 파일에 건다(Document.save 가 security 를 본다)
            t.doc.security = vals; t.doc.modified = True; t.changed(None)
            self.say(f"저장하면 암호가 걸립니다 · {dict(D.EncryptDialog.METHODS)[vals['method']].split(' · ')[0]}")
            return
        out, _ = QFileDialog.getSaveFileName(self, "암호 건 PDF 저장", self._suggest(t, "_암호.pdf"), PDF_FILTER)
        if not out:
            return
        T.encrypt(t.doc.fitz, out, **vals)
        self.say(f"암호를 걸어 저장했습니다: {out}")

    def decrypt(self, t: DocTab):
        # 권한 암호만 걸린 문서는 is_encrypted 가 False 다. 암호화 정보로 본다.
        if not (t.doc.fitz.metadata or {}).get("encryption") and not t.doc.password:
            self.say("이 문서에는 암호가 없습니다"); return
        box = QMessageBox(QMessageBox.Icon.Question, "암호 풀기", "이 문서의 암호를 뺍니다.", parent=self)
        here = box.addButton("이 문서에서 빼기 · 저장할 때 적용", QMessageBox.ButtonRole.AcceptRole)
        new = box.addButton("암호 뺀 사본을 새 파일로", QMessageBox.ButtonRole.ActionRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is here:
            t.doc.security = {}; t.doc.modified = True; t.changed(None); self.say("저장하면 암호가 빠집니다"); return
        if box.clickedButton() is not new:
            return
        out, _ = QFileDialog.getSaveFileName(self, "암호 푼 PDF 저장", self._suggest(t, "_암호해제.pdf"), PDF_FILTER)
        if out:
            c = t.doc.copy()  # 열린 문서를 encryption 없이 저장하면 그 뒤 저장이 깨진다. 사본에서 푼다
            try:
                c.save(out, encryption=fitz.PDF_ENCRYPT_NONE, garbage=3, deflate=True)
            finally:
                c.close()
            self.say(f"암호를 풀어 저장했습니다: {out}")

    def auto_bookmark(self, t: DocTab):
        """글자 크기가 큰 줄을 제목으로 보고 북마크를 만든다."""
        d = t.doc
        sizes: dict[float, int] = {}
        heads = []
        for i in range(d.page_count):
            prot = d.fitz[i].rotation
            for b in d.text_blocks(i):
                if b.get("type") != 0: continue
                for l in b["lines"]:
                    # 화면에서 바로 선 줄만(비스듬한 워터마크·옆으로 누운 글은 제목이 아니다). → 0, ↑ 90, ← 180, ↓ 270
                    dx, dy = l["dir"]
                    if abs(dx) > 0.01 and abs(dy) > 0.01: continue
                    if int(round(math.degrees(math.atan2(-dy, dx)) / 90)) * 90 % 360 != prot: continue
                    # 수식 기호 글꼴의 사용자 영역 글자는 걷고, 글자·숫자가 남는 줄만 제목 후보로
                    txt = " ".join(re.sub(r"[-]", " ", "".join(s["text"] for s in l["spans"])).split())
                    if not txt or len(txt) > 80 or not re.search(r"[0-9A-Za-z가-힣]", txt): continue
                    sz = round(max(s["size"] for s in l["spans"]), 1)
                    sizes[sz] = sizes.get(sz, 0) + len(txt)
                    heads.append((i, sz, txt, l["bbox"][1]))
        if not sizes:
            self.say("글자가 없는 문서입니다"); return
        body = max(sizes, key=sizes.get)
        big = sorted({s for s in sizes if s >= body * 1.25}, reverse=True)[:3]
        if not big:
            self.say("제목으로 볼 만한 큰 글자가 없습니다"); return
        toc = [[big.index(sz) + 1, txt, i + 1] for i, sz, txt, _ in heads if sz in big]
        # 단계가 건너뛰지 않게
        fixed, prev = [], 0
        for lvl, txt, pg in toc:
            lvl = min(lvl, prev + 1); fixed.append([lvl, txt, pg]); prev = lvl
        d.set_flat_toc(fixed); t.bookmarks.reload(); t.show_side("bookmark") if not t.side.isVisible() else None
        self.say(f"북마크 {len(fixed)}개를 만들었습니다")

    def tile(self, horizontal: bool):
        """바둑판식 배열: 모든 창의 문서를 창 하나씩, 가로는 위아래로·세로는 좌우로 나눠 놓는다(tile.py)."""
        if fullscreen.get(self).active:
            fullscreen.get(self).leave()
        tiler.tile(self, horizontal)

    def view_checked(self, key: str):
        """보기 탭 펼침 메뉴의 체크(리본이 메뉴를 열 때 묻는다). 체크가 없는 항목은 None."""
        t = self.cur()
        if t is None:
            return None
        v = t.viewer
        modes = {"zoom_actual": "actual", "zoom_width": "fit_width", "zoom_height": "fit_height", "zoom_page": "fit_page"}
        if key in modes:
            return v.zoom_mode == modes[key]
        if key == "fullscreen":
            return fullscreen.get(self).active
        if key == "layout_cover":
            return v.cover_separate
        if key.startswith("layout_"):
            return v.layout_mode == key[len("layout_"):]
        if key.startswith("canvas_"):
            return v.bg_mode == key[len("canvas_"):]
        if key == "toggle_grid":
            return v.show_grid
        if key == "toggle_ruler":
            return v.show_ruler
        return None

    def annots_import_export(self, key: str):
        t = self.cur()
        if not t:
            return
        from ..engine import annot_xfdf as X
        if key == "export":
            out, flt = QFileDialog.getSaveFileName(self, "주석 내보내기", self._suggest(t, ".fdf"),
                                                   "FDF (*.fdf);;XFDF (*.xfdf);;PDF메트리 JSON (*.json)")
            if not out: return
            ext = os.path.splitext(out)[1].lower() or {"X": ".xfdf", "P": ".json"}.get(flt[:1], ".fdf")
            skipped = 0
            if ext == ".json":
                data = A.export_annots(t.doc); n = len(data)
                with open(out, "w", encoding="utf-8") as fp:
                    json.dump(data, fp, ensure_ascii=False, indent=1)
            else:
                raw, n, skipped = (X.to_fdf if ext == ".fdf" else X.to_xfdf)(t.doc, os.path.basename(t.doc.path or ""))
                with open(out, "wb") as fp:
                    fp.write(raw)
            self.say(f"주석 {n}개를 내보냈습니다" + (f" · 첨부 파일 등 못 담은 것 {skipped}개" if skipped else ""))
        else:
            p, _ = QFileDialog.getOpenFileName(self, "주석 가져오기", os.path.dirname(t.doc.path or ""),
                                               "주석 파일 (*.xfdf *.fdf *.json);;모든 파일 (*)")
            if not p: return
            try:
                with open(p, "rb") as fp:
                    raw = fp.read()
                head = raw.lstrip()[:16]
                if head.startswith(b"%FDF"):
                    n, skipped = X.import_fdf(t.doc, raw)
                elif head.startswith(b"<"):
                    n, skipped = X.import_xfdf(t.doc, raw)
                else:
                    data = json.loads(raw.decode("utf-8-sig"))
                    if not isinstance(data, list): raise ValueError("주석 목록이 아님")
                    n, skipped = A.import_annots(t.doc, data)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "주석 가져오기", f"읽을 수 없는 파일입니다 ({e})"); return
            t.changed(None); self.say(f"주석 {n}개를 가져왔습니다" + (f" · 못 가져온 것 {skipped}개" if skipped else ""))

    # ── 인쇄·메일·속성·설정·도움말 ──────────────────────────────
    def print_doc(self, t: DocTab):
        """인쇄 창(미리보기·크기·포스터·여러 페이지·소책자·양면·흑백). 고른 설정은 다음에도 그대로."""
        from .printing import PrintDialog, send
        if not QPrinterInfo.availablePrinterNames():
            QMessageBox.warning(self, "프린터 없음", "이 PC 에 설치된 프린터가 없습니다"); return
        dlg = PrintDialog(self, t.doc.fitz, t.viewer.current, self.prefs.get("print"), lock=t.doc.lock,
                          view=self._print_view(t), name=t.doc.name, unit=self.prefs.get("unit", "cm"))
        if not dlg.exec():
            return
        self.prefs["print"] = dlg.remember(); save_settings(self.settings)
        opts = dlg.options()
        err = send(self, t.doc.fitz, opts, t.doc.name, lock=t.doc.lock)
        if err == "그만둠":
            self.say("인쇄를 그만뒀습니다")
        elif err:
            QMessageBox.warning(self, "인쇄", err)
        else:
            self.say(f"인쇄를 보냈습니다 · {opts.printer} · {len(opts.pages) if opts.view is None else 1}쪽")

    def _print_view(self, t: DocTab):
        """인쇄 창의 「최근 보기」: 화면에 보이는 곳(쪽 좌표). 보이는 쪽이 정확히 하나일 때만, 아니면 None."""
        v = t.viewer
        if not v.items or v.zoom <= 0:
            return None
        vis = v.mapToScene(v.viewport().rect()).boundingRect()
        hit = []
        for i, it in enumerate(v.items):
            pr = it.mapRectToScene(it.page_rect())
            inter = vis.intersected(pr)
            if not inter.isEmpty():
                hit.append((i, pr, inter))
        if len(hit) != 1:
            return None
        i, pr, inter = hit[0]
        page = t.doc.fitz[i]
        r = (v._scene_rect_to_page(i, inter) * page.rotation_matrix) & page.rect   # 보기 회전을 풀어 쪽 좌표로
        return None if r.is_empty else (i, r)

    def _batch_print(self, inputs: list[str], printer: str):
        """일괄 인쇄: 파일마다 열어 인쇄 창과 같은 길(send)로 보낸다. 용지에 맞추기·쪽 모양 따라·컬러."""
        from .printing import PrintOptions, send
        ok, bad = 0, []
        for p in inputs:
            try:
                d = fitz.open(p)
                if d.needs_pass:
                    bad.append(f"{os.path.basename(p)}: 암호가 걸린 파일"); continue
                if not d.is_pdf:
                    d = fitz.open("pdf", d.convert_to_pdf())
                err = send(self, d, PrintOptions(printer=printer, pages=list(range(d.page_count))), os.path.basename(p))
                d.close()
            except Exception as e:  # noqa: BLE001
                err = f"열 수 없는 파일 ({e})"
            if err == "그만둠":
                break
            if err:
                bad.append(f"{os.path.basename(p)}: {err}")
            else:
                ok += 1
        QMessageBox.information(self, "일괄 인쇄", f"{ok}개 보냄" + (f", {len(bad)}개 못 보냄:\n" + "\n".join(bad[:8]) if bad else ""))

    def mail(self, t: DocTab):
        if not t.doc.path or t.doc.modified:
            if QMessageBox.question(self, "메일", "먼저 저장해야 보낼 수 있습니다. 저장할까요?") != QMessageBox.StandardButton.Yes or not self.save(t):
                return
        try:
            import win32com.client  # noqa: F401
            ol = win32com.client.Dispatch("Outlook.Application"); mail = ol.CreateItem(0)
            mail.Subject = t.doc.name; mail.Attachments.Add(t.doc.path); mail.Display()
        except Exception:  # noqa: BLE001
            webbrowser.open("mailto:?subject=" + urllib.parse.quote(t.doc.name))
            self.say("메일 프로그램을 열었습니다. 파일은 직접 붙이세요: " + t.doc.path)

    def properties(self, t: DocTab):
        def security(kind: str):   # 속성 창 「보안 정책」을 바꾸면 그 자리에서 암호 창을 띄운다
            if kind == "password":
                self.encrypt(t)
            elif kind == "none":
                self.decrypt(t)
        words = dict(self.settings.get("word_count") or {})
        dlg = PropertiesDialog(self, t.doc, t.viewer.current, self.settings, security)
        ok = dlg.exec()
        if (self.settings.get("word_count") or {}) != words:   # 단어 수는 취소해도 기억한다
            save_settings(self.settings)
        if not ok:
            return
        md, view = dlg.changed_metadata(), dlg.initial_view()
        changed = bool(md)
        if md:
            t.doc.set_metadata(md)
        if view is not None and t.doc.set_initial_view(view):
            changed = True
        if changed:
            self.mark_modified()

    def preferences(self, page: str = "general"):
        dlg = PreferencesDialog(self, self.settings, page)
        if not dlg.exec():
            return
        save_settings(self.settings); self._shortcuts(); self.apply_theme()
        for t in self.tabs:
            t.viewer.props["author"] = self.prefs.get("author", ""); t.viewer.props["measure_unit"] = self.prefs.get("unit", "cm")

    def show_help(self):
        QMessageBox.information(self, "도움말",
                                f"{APP_NAME} {VERSION}: 개인이 만든 무료 비영리 프로그램\n"
                                "이스트소프트 「알PDF」와 관계없음(알PDF 는 (주)이스트소프트의 상표)\n"
                                "소스는 AGPL-3.0 으로 공개(github.com/kyijgnes/rpdf), 오픈소스 고지는 설치 폴더 THIRD-PARTY-NOTICES.txt\n\n"
                                "홈: 손·선택·확대, 모두 편집(원문 글자·그림 고치기), 텍스트 추가, OCR, 자르기, 검색\n"
                                "편집: 텍스트·이미지·링크 추가, 워터마크·배경·머리글/바닥글·베이츠 번호\n"
                                "주석: 연필·지우개·강조·밑줄·취소선·글상자·말풍선·노트·도형·스탬프·서명·첨부·측정\n"
                                "변환: HWP·Word·Excel·PowerPoint·이미지·텍스트·HTML·RTF·EPUB·PDF/A, 파일에서 PDF 생성, 일괄\n"
                                "보기: 확대 방식·보기 방식·바탕색·읽기 모드·자동 북마크·전체 화면·어두운 화면\n"
                                "구성: 썸네일 격자에서 끌어 순서 바꾸기, 회전·삭제·추출·분할·삽입·자르기·크기·바꾸기·레이블\n"
                                "도구: OCR·영역 OCR·병합·비교·압축·평면화·일괄·기울기 보정·표 인식·데이터 추출\n"
                                "양식: 칸 만들기(텍스트·체크·라디오·콤보·목록·버튼·이미지·서명·날짜)·양식 인식·데이터 내보내기/가져오기\n"
                                "보안: 암호화·암호 풀기·교정 표시/검색/적용·숨겨진 데이터 삭제·서명 이미지\n\n"
                                "단축키: " + " · ".join(f"{K.label(self.keys, k)} {K.NAMES[k]}" for k in
                                                       ("open", "save", "print", "search", "undo", "next_tab", "zoom_page", "zoom_actual",
                                                        "zoom_width", "prev_view") if K.label(self.keys, k)) + "\n"
                                "Home/End 첫·마지막 쪽 · Esc 도구 끝 · 모든 단축키는 환경 설정 「단축키」 에서 바꿈")

    def show_search(self):
        """검색 패널을 열고(열려 있으면 그대로) 찾을 글자 칸에 커서."""
        t = self.cur()
        if not t:
            return
        if not (t.side.isVisible() and t.side_key == "search"):
            t.show_side("search")
        t.search.focus()
        for k, b in self.navbtns.items():
            b.setChecked(k == "search")

    def quick_search(self, text: str):
        t = self.cur()
        if not t:
            return
        self.show_search()
        t.search.edit.setText(text); t.search.run()

    def start_tool(self, key: str):
        """시작 화면 빠른 도구."""
        if key in ("edit", "ocr", "compress", "protect", "convert"):
            self.open_dialog()
            t = self.cur()
            if not t:
                return
            if key == "edit": self.ribbon.set_tab("edit"); self.on_toggle("edit_all", True); self.ribbon.set_toggle("edit_all", True)
            elif key == "ocr": self.ocr(t)
            elif key == "compress": self.compress(t)
            elif key == "protect": self.encrypt(t)
            elif key == "convert": self.ribbon.set_tab("convert"); self.convert(t, "docx")
        elif key == "merge": self.merge()
        elif key == "batch": self.batch_dialog()
        elif key == "create": self.create_from_files()
