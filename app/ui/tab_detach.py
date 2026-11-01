"""문서 탭을 다른 창으로 옮기기: 끌어서 떼기 · 다른 창 탭 줄로 끌고 가 합치기 · 「새 창으로 분리」.
다시 열지 않고 같은 DocTab(문서·실행 취소 기록·보던 쪽·확대)을 그대로 옮긴다.

끌기는 크롬 탭처럼: 탭을 줄 밖으로 끌어내는 순간 그 탭이 새 창이 되어 마우스를 따라오고,
다른 창 탭 줄 가까이 가면 바로 그 줄에 붙는다. 붙은 뒤에도 단추를 놓기 전까지는 줄 안에서 자리를 옮기거나 다시 떼어 낼 수 있다.
창을 코드로 옮겨서 윈도우 끌어 붙이기가 안 걸리므로 화면 절반 붙이기는 여기서 한다: 떠 있는 창을 끄는 마우스가
화면 왼쪽·오른쪽 끝에 닿으면 붙을 자리를 반투명 틀로 보여 주고 놓으면 그 절반으로, 위쪽 끝이면 최대화."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

windows: list = []        # 열려 있는 창(MainWindow), 연 차례
_last = None              # 마지막으로 활성화된 창(다른 데서 켠 PDF메트리가 넘긴 파일을 받는다)
_closed: list = []        # 막 닫은 창(지워지기 전까지 잡아 둔다)
_drag = None              # 지금 끌고 있는 탭(TabDrag)

TEAR = 24     # 탭 줄 위아래로 이만큼 벗어나면 떼어 낸다
SNAP = 18     # 다른 창 탭 줄에서 이만큼 안으로 들어오면 붙는다
EDGE = 3      # 화면 끝에서 이만큼 안이면 화면 절반 붙이기


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
        target.resize(snapped_size(src) or g.size())
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


def _screens() -> list:
    """모니터마다 (화면 전체, 작업 표시줄을 뺀 작업 영역)."""
    return [(s.geometry(), s.availableGeometry()) for s in QGuiApplication.screens()]


def snap_target(pos: QPoint, screens: list):
    """끄는 마우스가 pos 에 있을 때 붙을 자리: ("left"|"right"|"max", 작업 영역 안 사각형) 또는 None.
    마우스가 있는 모니터 기준. 옆(위)에 다른 모니터가 이어진 끝은 넘어가는 길이라 붙이지 않는다."""
    for geo, avail in screens:
        if not geo.contains(pos):
            continue

        def open_side(p: QPoint) -> bool:   # 끝 바로 바깥 점이 다른 모니터에 들어가지 않나
            return not any(g.contains(p) for g, _ in screens if g != geo)
        half = avail.width() // 2
        if pos.x() - geo.left() < EDGE and open_side(QPoint(geo.left() - 1, pos.y())):
            return "left", QRect(avail.left(), avail.top(), half, avail.height())
        if geo.right() - pos.x() < EDGE and open_side(QPoint(geo.right() + 1, pos.y())):
            return "right", QRect(avail.left() + half, avail.top(), avail.width() - half, avail.height())
        if pos.y() - geo.top() < EDGE and open_side(QPoint(pos.x(), geo.top() - 1)):
            return "max", QRect(avail)
        return None
    return None


def snap_window(win, kind: str, rect: QRect):
    """win 을 rect(창 테두리까지 넣은 바깥 크기)에 붙인다. 다시 끌어내면 돌아갈 크기를 적어 둔다."""
    if kind == "max":
        win.showMaximized()
        return
    if win.isMaximized() or win.isFullScreen():
        win.showNormal()
    before = win.geometry().size()
    fg, g = win.frameGeometry(), win.geometry()
    inner = rect.adjusted(g.left() - fg.left(), g.top() - fg.top(), g.right() - fg.right(), g.bottom() - fg.bottom())
    win.setGeometry(inner)
    win._snap = (QSize(before), QRect(win.geometry()))


def snapped_size(win) -> QSize | None:
    """화면 절반에 붙인 그대로인 창이면 붙이기 전 크기. 그 뒤 손으로 옮기거나 크기를 바꿨으면 None."""
    snap = getattr(win, "_snap", None)
    if snap is None or win.isMaximized() or win.geometry() != snap[1]:
        return None
    return snap[0]


class SnapPreview(QWidget):
    """붙을 자리를 보여 주는 반투명 틀. 마우스를 받지 않고 창을 활성화하지도 않는다."""
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: #9AA4B2; border: 2px solid #FFFFFF;")
        self.setWindowOpacity(0.35)


class TabDrag:
    """단추를 놓을 때까지 탭 하나를 따라간다.
    떠 있음(floating): 탭 하나짜리 창이 마우스를 따라 움직인다. 다른 창 탭 줄 안으로 들어가면 거기 붙는다.
    붙어 있음(docked): 그 창 탭 줄 안에서 마우스 자리로 탭 순서를 옮긴다. 줄 위아래로 벗어나면 다시 뗀다."""
    INTERVAL = 10

    def __init__(self, t, cursor=QCursor.pos, pressed=_left_down, screens=_screens):
        self.t = t
        self.cursor = cursor
        self.pressed = pressed
        self.screens = screens
        self.floating = False
        self.offset = QPoint()
        self.snap = None          # 지금 놓으면 붙을 자리(snap_target 결과)
        self.preview = None       # 그 자리를 보여 주는 틀(SnapPreview)
        self.timer = QTimer(); self.timer.setInterval(self.INTERVAL); self.timer.timeout.connect(self.step)

    def start(self):
        global _drag
        _drag = self
        self.tear(self.cursor())
        self.timer.start()

    def stop(self):
        global _drag
        self.timer.stop()
        self._show_snap(None)
        if _drag is self:
            _drag = None

    def _show_snap(self, snap):
        self.snap = snap
        if snap is None:
            if self.preview is not None:
                self.preview.hide()
            return
        if self.preview is None:
            self.preview = SnapPreview()
        self.preview.setGeometry(snap[1])
        if not self.preview.isVisible():
            self.preview.show()
            self.t.win.raise_()   # 끄는 창이 틀 위에 오게

    def drop(self):
        """단추를 놓았다: 떠 있는 창이 화면 끝에 닿아 있었으면 거기 붙인다."""
        snap = self.snap if self.floating else None
        self.stop()
        if snap is not None and self.t.win is not None:
            snap_window(self.t.win, *snap)

    def tear(self, pos: QPoint):
        """탭을 떼어 마우스를 따라오는 창으로. 탭 하나뿐인 창이면 그 창이 그대로 따라온다."""
        win = self.t.win
        if len(win.tabs) > 1:
            win = move_tab(self.t, None, at=pos)
        elif win.isMaximized() or win.isFullScreen():
            win.showNormal()
        elif snapped_size(win) is not None:   # 화면 절반에 붙여 둔 창: 붙이기 전 크기로
            win.resize(snapped_size(win))
        win._snap = None
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
            self.drop(); return
        if self.floating:
            self._follow(pos)
            for o in reversed(others(self.t.win)):   # 마지막으로 쓴 창부터(겹쳤으면 위에 있는 창)
                zone = _bar_zone(o).adjusted(0, -SNAP, 0, SNAP)
                if zone.contains(pos):
                    self._show_snap(None)
                    bar = o.doctabs
                    move_tab(self.t, o, bar.tabAt(bar.mapFromGlobal(pos)))
                    self.floating = False
                    return
            self._show_snap(snap_target(pos, self.screens()))
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
