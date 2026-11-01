"""문서 탭을 다른 창으로 옮기기: 끌어서 떼기 · 다른 창 탭 줄로 끌고 가 합치기 · 「새 창으로 분리」.
다시 열지 않고 같은 DocTab(문서·실행 취소 기록·보던 쪽·확대)을 그대로 옮긴다.

끌기는 크롬 탭처럼: 탭을 줄 밖으로 끌어내는 순간 그 탭이 새 창이 되어 마우스를 따라오고,
다른 창 탭 줄 가까이 가면 바로 그 줄에 붙는다. 붙은 뒤에도 단추를 놓기 전까지는 줄 안에서 자리를 옮기거나 다시 떼어 낼 수 있다."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

windows: list = []        # 열려 있는 창(MainWindow), 연 차례
_last = None              # 마지막으로 활성화된 창(다른 데서 켠 PDF메트리가 넘긴 파일을 받는다)
_closed: list = []        # 막 닫은 창(지워지기 전까지 잡아 둔다)
_drag = None              # 지금 끌고 있는 탭(TabDrag)

TEAR = 24     # 탭 줄 위아래로 이만큼 벗어나면 떼어 낸다
SNAP = 18     # 다른 창 탭 줄에서 이만큼 안으로 들어오면 붙는다


def register(win):
    windows.append(win)


def unregister(win):
    """닫은 창을 뺀다. 창 닫기(closeEvent) 한가운데서 마지막 참조가 사라지면 창이 바로 지워지므로
    이벤트 루프가 한 바퀴 돈 뒤에 놓아 준다."""
    global _last
    if win in windows:
        windows.remove(win)
        _closed.append(win)
        QTimer.singleShot(0, lambda: _closed.remove(win) if win in _closed else None)
    if _last is win:
        _last = None


def activated(win):
    """windows 를 마지막으로 쓴 차례로 둔다(겹친 창 탭 줄에 끌고 가면 위에 있는 창에 붙게)."""
    global _last
    _last = win
    if win in windows:
        windows.remove(win); windows.append(win)


def last_active(fallback=None):
    """파일을 받을 창: 마지막으로 활성화된 열린 창, 없으면 fallback."""
    if _last is not None and _last in windows and _last.isVisible():
        return _last
    alive = [w for w in windows if w.isVisible()]
    return alive[-1] if alive else fallback


def others(win) -> list:
    return [w for w in windows if w is not win and w.isVisible()]


def move_tab(t, target=None, index: int = -1, at: QPoint | None = None):
    """탭 t 를 target 창으로 옮긴다. target 이 없으면 새 창을 at(화면 좌표) 근처에 띄운다.
    옮긴 뒤 탭이 하나도 남지 않은 창은 저장을 묻지 않고 닫는다. 옮겨 간 창을 돌려준다."""
    src = t.win
    if target is src:
        return src
    src.detach_tab(t)
    if target is None:
        from .window import MainWindow
        target = MainWindow(settings=src.settings)
        g = src.normalGeometry() if src.isMaximized() or src.isFullScreen() else src.geometry()
        target.resize(g.size())
        p = at if at is not None else src.geometry().topLeft() + QPoint(40, 40)
        target.move(p - QPoint(120, 15))   # 끌어 온 탭이 새 창 탭 줄 근처에 오게
        target.show()
    target.attach_tab(t, index)
    target.raise_(); target.activateWindow()
    if not src.tabs:
        src.close_emptied()
    else:
        src.update_title()
    return target


def _left_down() -> bool:
    """왼쪽 단추를 누르고 있나. 끄는 동안 창이 닫히고 새로 생겨도 놓친 놓기가 없게 윈도우에서는 장치 상태를 직접 본다."""
    if sys.platform == "win32" and QApplication.platformName() == "windows":
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)


def _bar_zone(win) -> QRect:
    """win 의 탭 줄(탭이 없는 오른쪽 빈자리까지) 화면 좌표. 붙는 범위."""
    row = win.tabrow
    return QRect(row.mapToGlobal(QPoint(0, 0)), row.size())


class TabDrag:
    """단추를 놓을 때까지 탭 하나를 따라간다.
    떠 있음(floating): 탭 하나짜리 창이 마우스를 따라 움직인다. 다른 창 탭 줄 안으로 들어가면 거기 붙는다.
    붙어 있음(docked): 그 창 탭 줄 안에서 마우스 자리로 탭 순서를 옮긴다. 줄 위아래로 벗어나면 다시 뗀다."""
    INTERVAL = 10

    def __init__(self, t, cursor=QCursor.pos, pressed=_left_down):
        self.t = t
        self.cursor = cursor
        self.pressed = pressed
        self.floating = False
        self.offset = QPoint()
        self.timer = QTimer(); self.timer.setInterval(self.INTERVAL); self.timer.timeout.connect(self.step)

    def start(self):
        global _drag
        _drag = self
        self.tear(self.cursor())
        self.timer.start()

    def stop(self):
        global _drag
        self.timer.stop()
        if _drag is self:
            _drag = None

    def tear(self, pos: QPoint):
        """탭을 떼어 마우스를 따라오는 창으로. 탭 하나뿐인 창이면 그 창이 그대로 따라온다."""
        win = self.t.win
        if len(win.tabs) > 1:
            win = move_tab(self.t, None, at=pos)
        elif win.isMaximized() or win.isFullScreen():
            win.showNormal()
        win.raise_(); win.activateWindow()
        bar = win.doctabs
        i = win.tabs.index(self.t)
        # 창 테두리 왼쪽 위에서 잡은 탭 가운데까지(창을 움직여도 탭이 마우스 밑에 있게)
        frame = win.geometry().topLeft() - win.frameGeometry().topLeft()
        self.offset = bar.mapTo(win, bar.tabRect(i).center()) + frame
        self.floating = True
        self._follow(pos)

    def _follow(self, pos: QPoint):
        self.t.win.move(pos - self.offset)

    def step(self):
        if self.t.win is None or self.t.win not in windows:
            self.stop(); return
        pos = self.cursor()
        if not self.pressed():
            self.stop(); return
        if self.floating:
            self._follow(pos)
            for o in reversed(others(self.t.win)):   # 마지막으로 쓴 창부터(겹쳤으면 위에 있는 창)
                zone = _bar_zone(o).adjusted(0, -SNAP, 0, SNAP)
                if zone.contains(pos):
                    bar = o.doctabs
                    move_tab(self.t, o, bar.tabAt(bar.mapFromGlobal(pos)))
                    self.floating = False
                    return
            return
        win = self.t.win
        zone = _bar_zone(win)
        if pos.y() < zone.top() - TEAR or pos.y() > zone.bottom() + TEAR:
            self.tear(pos); return
        bar = win.doctabs
        cur = win.tabs.index(self.t)
        j = bar.tabAt(bar.mapFromGlobal(QPoint(pos.x(), bar.mapToGlobal(bar.rect().center()).y())))
        if j >= 0 and j != cur:
            bar.moveTab(cur, j)   # tabMoved 로 창의 탭 목록도 같이 옮긴다


def start_drag(win, i: int):
    """탭 줄 밖으로 끌어낸 탭: 단추를 놓을 때까지 따라간다."""
    if _drag is not None or not 0 <= i < len(win.tabs):
        return None
    d = TabDrag(win.tabs[i])
    d.start()
    return d
