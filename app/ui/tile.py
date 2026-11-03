"""바둑판식 배열: 열린 문서마다 창 하나가 되게 탭을 떼어 내고, 모니터 작업 영역을 줄·칸으로 나눠 놓는다.

가로 바둑판식 배열은 창을 위아래로 쌓고(줄이 둘 이상), 세로 바둑판식 배열은 좌우로 늘어놓는다(칸이 둘 이상).
창 최소 크기 밑으로 줄여야 하면 줄(칸) 수를 줄이고 나머지 창을 앞줄부터 한 칸씩 더 얹는다.
탭은 tab_detach.move_tab 으로 옮겨 고친 내용·실행 취소·보던 자리가 그대로다.
"""
from __future__ import annotations

from PySide6.QtCore import QRect

from . import tab_detach

MIN_W, MIN_H = 360, 240   # 창을 이보다 작게 나누지 않는다


def doc_count() -> int:
    """모든 창에 열린 문서 수."""
    return sum(len(w.tabs) for w in tab_detach.windows)


def plan(n: int, area: QRect, horizontal: bool, min_w: int = MIN_W, min_h: int = MIN_H) -> list[QRect]:
    """창 n 개를 놓을 자리. horizontal=True 면 줄(위아래)로, False 면 칸(좌우)으로 먼저 나눈다."""
    if n <= 0:
        return []
    if horizontal:
        lines = max(1, min(n, area.height() // max(min_h, 1)))
    else:
        lines = max(1, min(n, area.width() // max(min_w, 1)))
    lines = max(lines, min(2, n))
    base, extra = divmod(n, lines)
    out = []
    for li in range(lines):
        k = base + (1 if li < extra else 0)
        if horizontal:
            y0 = area.y() + area.height() * li // lines
            y1 = area.y() + area.height() * (li + 1) // lines
            for j in range(k):
                x0 = area.x() + area.width() * j // k
                x1 = area.x() + area.width() * (j + 1) // k
                out.append(QRect(x0, y0, x1 - x0, y1 - y0))
        else:
            x0 = area.x() + area.width() * li // lines
            x1 = area.x() + area.width() * (li + 1) // lines
            for j in range(k):
                y0 = area.y() + area.height() * j // k
                y1 = area.y() + area.height() * (j + 1) // k
                out.append(QRect(x0, y0, x1 - x0, y1 - y0))
    return out


def tile(win, horizontal: bool) -> list:
    """win 이 있는 모니터에 모든 문서를 창 하나씩 나눠 놓는다. 놓은 창 목록을 돌려준다(문서가 하나면 그 창을 최대화)."""
    if doc_count() < 2:
        if win.tabs:
            win.showMaximized()
        return [win]
    order = [win] + [w for w in tab_detach.windows if w is not win]
    wins = []
    for w in order:
        if not w.tabs:
            continue
        wins.append(w)
        while len(w.tabs) > 1:   # 지금 보는 탭은 그 창에 남기고 나머지를 새 창으로
            cur = w.cur()
            t = next(x for x in reversed(w.tabs) if x is not cur)
            wins.append(tab_detach.move_tab(t))
    scr = (win.screen() or wins[0].screen()).availableGeometry()
    for w in wins:
        if w.isFullScreen() or w.isMaximized():
            w.showNormal()
    min_w = max(MIN_W, min((w.minimumSizeHint().width() for w in wins), default=MIN_W))
    min_h = max(MIN_H, min((w.minimumSizeHint().height() for w in wins), default=MIN_H))
    spots = plan(len(wins), scr, horizontal, min_w, min_h)
    for w, r in zip(wins, spots):
        fr = w.frameGeometry(); g = w.geometry()
        dl, dt = g.x() - fr.x(), g.y() - fr.y()
        dr, db = fr.right() - g.right(), fr.bottom() - g.bottom()
        w.setGeometry(r.adjusted(dl, dt, -dr, -db))   # 창 틀까지 칸에 들게
        w.show()
    wins[0].raise_(); wins[0].activateWindow()
    return wins


def plan_shape(n: int, area: QRect, horizontal: bool, min_w: int = MIN_W, min_h: int = MIN_H) -> tuple[int, int]:
    """시험용: (줄 수, 가장 긴 줄의 창 수)."""
    spots = plan(n, area, horizontal, min_w, min_h)
    key = (lambda r: r.y()) if horizontal else (lambda r: r.x())
    lines = sorted({key(r) for r in spots})
    longest = max(sum(1 for r in spots if key(r) == v) for v in lines) if lines else 0
    return len(lines), longest
