"""문서 탭을 다른 창으로 옮기기: 끌어서 떼기 · 다른 창 탭 줄에 놓아 합치기 · 「새 창으로 분리」.
다시 열지 않고 같은 DocTab(문서·실행 취소 기록·보던 쪽·확대)을 그대로 옮긴다."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QDrag

MIME = "application/x-pdfmetry-tab"

windows: list = []        # 열려 있는 창(MainWindow), 연 차례
_last = None              # 마지막으로 활성화된 창(다른 데서 켠 PDF메트리가 넘긴 파일을 받는다)
_dragging = None          # 끌고 있는 DocTab
_closed: list = []        # 막 닫은 창(지워지기 전까지 잡아 둔다)


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
    global _last
    _last = win


def last_active(fallback=None):
    """파일을 받을 창: 마지막으로 활성화된 열린 창, 없으면 fallback."""
    if _last is not None and _last in windows and _last.isVisible():
        return _last
    alive = [w for w in windows if w.isVisible()]
    return alive[-1] if alive else fallback


def others(win) -> list:
    return [w for w in windows if w is not win and w.isVisible()]


def dragging():
    return _dragging


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


def start_drag(win, i: int):
    """탭 줄 밖으로 끌어낸 탭: 끌어 놓을 때까지 따라다니고, 다른 창 탭 줄에 놓으면 합치고, 창 밖에 놓으면 새 창으로 뗀다."""
    global _dragging
    if not 0 <= i < len(win.tabs):
        return
    t = win.tabs[i]
    drag = QDrag(win.doctabs)
    md = QMimeData(); md.setData(MIME, b"1")
    drag.setMimeData(md)
    pm = win.doctabs.grab(win.doctabs.tabRect(i))
    drag.setPixmap(pm); drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
    _dragging = t
    try:
        res = drag.exec(Qt.DropAction.MoveAction)
    finally:
        _dragging = None
    if res == Qt.DropAction.IgnoreAction and t.win is win and t in win.tabs and len(win.tabs) > 1:
        move_tab(t, None, at=QCursor.pos())


def drop_target_ok(win, pos: QPoint) -> bool:
    """끌고 있는 탭을 win 의 pos(창 좌표)에 놓을 수 있나. 제 창은 탭 줄 위에서만(본문에 놓으면 떼어 새 창)."""
    t = _dragging
    if t is None:
        return False
    if t.win is not win:
        return True
    return win.tabrow.geometry().contains(pos)


def drop(win, pos: QPoint, t) -> bool:
    """win 의 pos(창 좌표)에 끌고 온 탭 t 를 놓는다: 다른 창 탭이면 놓은 자리 탭 앞에 끼워 합친다."""
    if t is None or t.win is None or t not in t.win.tabs:
        return False
    if t.win is win:
        return True   # 제 탭 줄에 도로 놓음: 그대로
    bar = win.doctabs
    index = bar.tabAt(bar.mapFrom(win, pos))
    move_tab(t, win, index)
    return True

