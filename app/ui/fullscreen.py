"""전체 화면: 앱 창 하나에서 쪽만 보이게(리본·상태줄·양쪽 막대·탭 줄·옆 패널을 숨김).

들어가면 가운데에 나가는 법을 2초 보여 주고, Esc·F11 로 나온다. 숨긴 것은 나올 때 그대로 되살린다
(읽기 모드로 이미 숨겨 둔 것은 숨긴 채로). 따로 창을 만들지 않으니 탭·실행 취소가 그대로 이어진다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QWidget

HINT = "Esc 키를 누르면 전체 화면이 끝납니다"
HINT_MS = 2000


class FullScreen:
    def __init__(self, win):
        self.win = win
        self.active = False
        self._hidden: list[QWidget] = []
        self._was_max = False
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), win)
        self._esc.setContext(Qt.ShortcutContext.WindowShortcut)
        self._esc.setEnabled(False)
        self._esc.activated.connect(self.leave)
        self.hint = QLabel(HINT, win)
        self.hint.setObjectName("fullscreenHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("QLabel#fullscreenHint{background:rgba(30,30,30,200); color:white; border-radius:8px;"
                                " padding:14px 26px; font-size:15px}")
        self.hint.hide()
        self._hint_timer = QTimer(win)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self.hint.hide)

    def toggle(self):
        self.leave() if self.active else self.enter()

    def enter(self):
        t = self.win.cur()
        if self.active or t is None:
            return
        self.active = True
        self._was_max = self.win.isMaximized()
        self._hidden = self._siblings_to_hide(t.viewer)
        for w in self._hidden:
            w.hide()
        self.win.showFullScreen()
        self._esc.setEnabled(True)
        self._show_hint()
        t.viewer.setFocus()

    def leave(self):
        if not self.active:
            return
        self.active = False
        self._esc.setEnabled(False)
        self._hint_timer.stop()
        self.hint.hide()
        for w in self._hidden:
            try:
                w.show()
            except RuntimeError:   # 전체 화면 동안 닫힌 탭의 패널
                pass
        self._hidden = []
        self.win.showMaximized() if self._was_max else self.win.showNormal()

    def _siblings_to_hide(self, viewer: QWidget) -> list[QWidget]:
        """보기에서 창 가운데 위젯까지 올라가며, 그 줄에 있지 않은 보이는 위젯을 모은다."""
        out = []
        root = self.win.centralWidget()
        w = viewer
        while w is not None and w is not root:
            parent = w.parentWidget()
            if parent is None:
                break
            for c in parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                if c is not w and c.isVisible() and not c.isWindow() and c is not self.hint:
                    out.append(c)
            w = parent
        return out

    def _show_hint(self):
        h = self.hint
        h.adjustSize()
        g = self.win.rect()
        h.move((g.width() - h.width()) // 2, (g.height() - h.height()) // 2)
        h.raise_()
        h.show()
        # 창이 전체 화면으로 커진 뒤 다시 가운데로
        QTimer.singleShot(50, lambda: h.move((self.win.width() - h.width()) // 2, (self.win.height() - h.height()) // 2))
        self._hint_timer.start(HINT_MS)


def get(win) -> FullScreen:
    fs = getattr(win, "_fullscreen", None)
    if fs is None:
        fs = win._fullscreen = FullScreen(win)
    return fs
