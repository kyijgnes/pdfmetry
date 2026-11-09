"""속성 패널의 「정렬」 카드: 편집 객체를 고르면 쌓는 순서 · 맞춤 · 쪽 가운데 · 분배 단추(설계 17, 4.5·4.6).

단추는 보기의 requestDialog("action", 키) 로 창에 부탁하고, 창은 edit_tools.run 으로 넘긴다.
고른 수에 따라 단추를 켜고 끈다(맞춤 둘 이상 · 분배 셋 이상 · 순서와 쪽 가운데는 하나부터).
"""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QSizePolicy, QWidget

from .sidebar import group_card

# (줄 이름, 칸 수, 단추들). 단추는 왼쪽에서 오른쪽, 칸이 차면 다음 줄. 맞춤은 왼쪽 칸이 가로, 오른쪽 칸이 세로.
ROWS = (("순서", 2, (("front", "맨 앞으로"), ("back", "맨 뒤로"), ("forward", "앞으로"), ("backward", "뒤로"))),
        ("맞춤", 2, (("left", "왼쪽"), ("top", "위쪽"), ("hcenter", "가로 가운데"), ("vcenter", "세로 가운데"),
                    ("right", "오른쪽"), ("bottom", "아래쪽"))),
        ("쪽 가운데", 3, (("page_h", "가로"), ("page_v", "세로"), ("page_both", "한가운데"))),
        ("분배", 2, (("dist_h", "가로"), ("dist_v", "세로"))))
ORDER = ("front", "back", "forward", "backward")


def action_key(how: str) -> str:
    return f"obj_{how}" if how in ORDER else f"obj_align_{how}"


class ArrangeBox(QWidget):
    """「정렬」 카드 하나. show_sel(sel) 로 보이고 숨긴다."""

    def __init__(self, viewer=None, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout
        self.viewer = viewer
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.box, f = group_card("정렬")
        lay.addWidget(self.box)
        self.buttons: dict[str, QPushButton] = {}
        # 줄 이름은 단추 위 한 줄에 두고 단추는 카드 폭을 다 쓴다(이름 칸 옆에 세 칸을 두면 좁은 패널에서 잘렸다).
        for label, cols, items in ROWS:
            w = QWidget(); g = QGridLayout(w); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(4)
            for n, (how, text) in enumerate(items):
                b = QPushButton(text); b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                b.clicked.connect(lambda _=False, h=how: self._run(h))
                g.addWidget(b, n // cols, n % cols)
                self.buttons[how] = b
            for c in range(cols):
                g.setColumnStretch(c, 1)
            f.addRow(QLabel(label)); f.addRow(w)
        self.hide()

    def _run(self, how: str):
        v = self.viewer
        if v is not None:
            v.requestDialog.emit("action", action_key(how))

    def show_sel(self, sel) -> None:
        v = self.viewer
        on = bool(v is not None and getattr(v, "edit_mode", False) and sel is not None
                  and getattr(sel, "kind", "") in ("block", "image"))
        self.setVisible(on)
        if not on:
            return
        n = max(1, len(getattr(v, "sel_objs", []) or []))
        from .edit_tools import need
        for how, b in self.buttons.items():
            b.setEnabled(how in ORDER or n >= need(how))
