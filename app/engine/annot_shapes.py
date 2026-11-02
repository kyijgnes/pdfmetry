"""모양 더보기 넷(삼각형 · 십자가 · 구부러진 위쪽 화살표 · 말풍선)의 꼭짓점.

끌어 그린 네모를 받아 그 안에 꽉 차는 꼭짓점 목록을 돌려준다. 주석은 다각형(Polygon)으로 만들어
다른 뷰어에서도 그대로 보이고 속성 패널·옮기기도 다각형과 같다. 곡선은 짧은 선분으로 잇는다.
"""
from __future__ import annotations

import math

import fitz

SHAPES = {"triangle": "삼각형", "cross": "십자가", "bent_arrow": "구부러진 위쪽 화살표", "balloon": "말풍선"}


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int) -> list[tuple[float, float]]:
    """(cx, cy) 가 가운데인 호. 각도는 도(아래쪽이 90도, 쪽 좌표는 y 가 아래로 커짐). 끝점 둘 다 넣음."""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]


def triangle(r: fitz.Rect) -> list[tuple[float, float]]:
    return [((r.x0 + r.x1) / 2, r.y0), (r.x1, r.y1), (r.x0, r.y1)]


def cross(r: fitz.Rect) -> list[tuple[float, float]]:
    """더하기 꼴. 팔 굵기는 너비·높이의 1/3."""
    a1, a2 = r.x0 + r.width / 3, r.x0 + r.width * 2 / 3
    b1, b2 = r.y0 + r.height / 3, r.y0 + r.height * 2 / 3
    return [(a1, r.y0), (a2, r.y0), (a2, b1), (r.x1, b1), (r.x1, b2), (a2, b2),
            (a2, r.y1), (a1, r.y1), (a1, b2), (r.x0, b2), (r.x0, b1), (a1, b1)]


def bent_arrow(r: fitz.Rect) -> list[tuple[float, float]]:
    """아래 왼쪽에서 오른쪽으로 가다 둥글게 꺾여 위로 올라가는 화살표."""
    w, h = r.width, r.height
    t = min(w, h) * 0.22                      # 몸통 굵기
    hw = min(t * 1.1, w / 2)                  # 화살촉 반 너비
    hh = min(h * 0.3, hw * 1.6)               # 화살촉 높이
    cx = r.x1 - hw                            # 세로 몸통 가운데
    right, left = cx + t / 2, cx - t / 2
    big = min(t * 1.5, max(right - r.x0, t))  # 꺾이는 곳 바깥 반지름
    small = max(big - t, 0)
    ox, oy = right - big, r.y1 - big          # 꺾이는 곳 가운데
    pts = [(r.x0, r.y1)]
    pts += _arc(ox, oy, big, 90, 0, 8)        # 바깥 호: 아래 끝 → 오른쪽 끝
    pts += [(right, r.y0 + hh), (cx + hw, r.y0 + hh), (cx, r.y0), (cx - hw, r.y0 + hh), (left, r.y0 + hh)]
    pts += _arc(ox, oy, small, 0, 90, 8) if small > 0 else [(left, oy)]
    pts.append((r.x0, r.y1 - t))
    return pts


def balloon(r: fitz.Rect) -> list[tuple[float, float]]:
    """타원 몸통(위 3/4) + 왼쪽 아래로 뻗은 꼬리."""
    hb = r.height * 0.75
    cx, cy, rx, ry = (r.x0 + r.x1) / 2, r.y0 + hb / 2, r.width / 2, hb / 2
    gap0, gap1 = 105, 130                     # 꼬리가 붙는 자리(아래쪽 조금 왼쪽)
    n = 40
    pts = [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)))
           for a in (gap1 + (360 - (gap1 - gap0)) * k / n for k in range(n + 1))]
    pts.append((r.x0 + r.width * 0.18, r.y1))
    return pts


_FN = {"triangle": triangle, "cross": cross, "bent_arrow": bent_arrow, "balloon": balloon}


def shape_points(kind: str, rect: fitz.Rect) -> list[fitz.Point]:
    r = fitz.Rect(rect); r.normalize()
    return [fitz.Point(x, y) for x, y in _FN[kind](r)]
