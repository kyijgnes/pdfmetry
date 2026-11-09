"""화면 읽기 이름: 아이콘만 있는 단추에 읽을 이름을 붙이는 도우미.

Qt 는 툴팁을 화면 읽기 이름으로 쓰지 않는다. 글자가 없고 이름도 없는 단추는 「단추」로만 읽히므로,
단추를 다 만든 뒤 한 번 훑어 툴팁을 이름으로 옮긴다(툴팁은 새로 넣지 않는다).
"""
from __future__ import annotations

from PySide6.QtWidgets import QAbstractButton, QWidget


def name_icon_buttons(root: QWidget) -> int:
    """root 아래에서 글자·이름이 없는 단추에 툴팁을 이름으로 붙인다. 붙인 개수를 돌려준다."""
    n = 0
    for b in root.findChildren(QAbstractButton):
        if b.text().replace("&", "").strip() or b.accessibleName():
            continue
        tip = b.toolTip().strip()
        if tip:
            b.setAccessibleName(tip)
            n += 1
    return n


def unnamed_icon_buttons(root: QWidget) -> list[QAbstractButton]:
    """글자도 이름도 없는 단추(시험용)."""
    return [b for b in root.findChildren(QAbstractButton)
            if not b.text().replace("&", "").strip() and not b.accessibleName()
            and b.objectName() != "qt_tableview_cornerbutton"]   # 표 왼쪽 위 모서리(안 보이는 Qt 기본 단추)
