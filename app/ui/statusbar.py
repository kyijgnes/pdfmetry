"""상태줄: 쪽 크기(환경 설정 단위) · 알림 · 손/선택 · 쪽 이동 · 이전/다음 보기 · 맞춤·보기 방식 펼침 · 확대 · 접기.

접으면 숨었다가 마우스를 창 아래 끝에 대면 미끄러져 나오고(300ms), 벗어나면 다시 숨는다(500ms).
접힘은 settings 최상위 status_collapsed 에 기억한다.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRegularExpression, Qt, QTimer
from PySide6.QtGui import QActionGroup, QCursor, QRegularExpressionValidator
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QSlider, QToolButton, QWidget

from . import fitwidth, icons
from .dlg_pagebox import FINE, UNITS
from .viewer import PageView, pct_text

HEIGHT = 31
SHOW_MS, HIDE_MS = 300, 500
HOT_H = 4   # 접었을 때 창 맨 아래에 남는 마우스 감지 띠

# 맞춤 방식 펼침: (동작 열쇠, 이름, 아이콘, 뷰어 zoom_mode)
FITS = (("zoom_actual", "실제 크기", "actual", "actual"), ("zoom_width", "너비 맞춤", "fitwidth", "fit_width"),
        ("zoom_height", "높이 맞춤", "fitpage", "fit_height"), ("zoom_page", "창 맞춤", "fitpage", "fit_page"),
        ("zoom_area", "영역 확대", "zoomin", None))
# 보기 방식 펼침: (동작 열쇠, 이름, 아이콘, 뷰어 layout_mode)
LAYOUTS = (("layout_single", "한 쪽 보기", "single", "single"), ("layout_continuous", "한 쪽 연속 보기", "continuous", "continuous"),
           ("layout_facing", "두 쪽 보기", "facing", "facing"), ("layout_facing_continuous", "두 쪽 연속 보기", "tile", "facing_continuous"))


def size_text(w_pt: float, h_pt: float, unit: str) -> str:
    """쪽 크기를 단위대로: 「21.00 x 29.70 cm」."""
    unit = unit if unit in UNITS else "cm"
    k = UNITS[unit]
    nd = 2 if unit in FINE else 0 if unit == "pt" else 1
    return f"{w_pt / k:.{nd}f} x {h_pt / k:.{nd}f} {unit}"


def step_index(zoom: float) -> int:
    """배율에 가장 가까운 단계표 자리(슬라이더 눈금)."""
    steps = PageView.STEPS
    return min(range(len(steps)), key=lambda i: abs(steps[i] - zoom))


class _PageEdit(QLineEdit):
    """쪽 칸 하나에 「3 / 12」. 눌러 고치기 시작하면 번호만 남기고 다 고른다."""
    def focusInEvent(self, e):
        super().focusInEvent(e)
        cur = self.text().split("/")[0].strip()
        if cur != self.text():
            self.setText(cur)
        QTimer.singleShot(0, self.selectAll)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        bar = self.parentWidget()
        if hasattr(bar, "show_page"):
            bar.show_page()


class StatusBar(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("statusbar"); self.setFixedHeight(HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._active = False     # 문서가 열려 있다(시작 화면이면 상태줄 없음)
        self._peek = False       # 접힌 채 미끄러져 나와 있다
        self._page = (0, 0)      # (지금 쪽 0부터, 전체 쪽 수)
        self._label = ""         # 지금 쪽 레이블(있으면 번호 대신)
        sl = QHBoxLayout(self); sl.setContentsMargins(8, 0, 8, 0); sl.setSpacing(4)
        self.size_label = QLabel(""); sl.addWidget(self.size_label)
        self.msg_label = QLabel(""); self.msg_label.setObjectName("dim"); sl.addWidget(self.msg_label)
        self.msg_label.setMinimumWidth(1)  # 긴 알림이 창 최소 폭을 밀어 올리지 않게(넘치면 잘리고 툴팁에 원문)
        sl.addStretch()
        self.sb_hand = self._btn("hand", 16, "손", lambda: win.on_tool("hand")); self.sb_hand.setCheckable(True)
        self.sb_sel = self._btn("select", 16, "선택", lambda: win.on_tool("select")); self.sb_sel.setCheckable(True)
        sl.addWidget(self.sb_hand); sl.addWidget(self.sb_sel); sl.addSpacing(8)
        for key in ("first", "prev"):
            sl.addWidget(self._btn(key, 14, "", lambda k=key: win.on_action(k)))
        self.page_edit = _PageEdit(); self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.returnPressed.connect(win._goto_entered); sl.addWidget(self.page_edit)
        self._fit_page_edit()
        for key in ("next", "last"):
            sl.addWidget(self._btn(key, 14, "", lambda k=key: win.on_action(k)))
        self.sb_hist = {}
        for key, name in (("prev_view", "back"), ("next_view", "forward")):   # 이전 보기 · 다음 보기(Alt+← · Alt+→)
            b = self._btn(name, 14, "", lambda k=key: win.on_action(k)); sl.addWidget(b); self.sb_hist[key] = b
        sl.addSpacing(8)
        self.fit_btn, self.fit_menu = self._drop("맞춤 방식", FITS, "fitwidth")
        self.layout_btn, self.layout_menu = self._drop("보기 방식", LAYOUTS, "continuous")
        self.layout_menu.addSeparator()
        self.cover_act = self.layout_menu.addAction("표지 따로 보기", lambda: self._run("layout_cover")); self.cover_act.setCheckable(True)
        sl.addWidget(self.fit_btn); sl.addWidget(self.layout_btn)
        sl.addStretch()
        sl.addWidget(self._btn("zoomout", 14, "축소", lambda: win.on_action("zoomout")))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal); self.zoom_slider.setRange(0, len(PageView.STEPS) - 1)
        self.zoom_slider.setFixedWidth(110); self.zoom_slider.setPageStep(1)
        self.zoom_slider.valueChanged.connect(win._zoom_slider); sl.addWidget(self.zoom_slider)
        sl.addWidget(self._btn("zoomin", 14, "확대", lambda: win.on_action("zoomin")))
        self.zoom_box = QComboBox(); self.zoom_box.setEditable(True)
        self.zoom_box.addItems([pct_text(z) for z in PageView.STEPS])
        fitwidth.fit(self.zoom_box, fitwidth.combo_texts(self.zoom_box))   # 「1600%」까지 다 보이게
        self.zoom_box.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9.%]*"), self.zoom_box))
        self.zoom_box.activated.connect(win._zoom_box); self.zoom_box.lineEdit().returnPressed.connect(win._zoom_box)
        self.zoom_box.installEventFilter(self)
        sl.addWidget(self.zoom_box)
        sl.addWidget(self._btn("fullscreen", 14, "전체 화면", lambda: win.on_action("fullscreen")))
        self.fold_btn = self._btn("down", 14, "하단 바 축소", self._fold_clicked); sl.addWidget(self.fold_btn)
        # 접었을 때 창 아래 끝의 감지 띠
        self.hot = QWidget(); self.hot.setObjectName("statushot"); self.hot.setFixedHeight(HOT_H)
        self.hot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hot.installEventFilter(self); self.hot.hide()
        self._in = QTimer(self); self._in.setSingleShot(True); self._in.setInterval(SHOW_MS); self._in.timeout.connect(self.peek)
        self._watch = QTimer(self); self._watch.setInterval(100); self._watch.timeout.connect(self._check_leave)
        self._away = 0   # 상태줄 밖에 머문 시간(ms)
        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.collapsed = bool(win.settings.get("status_collapsed"))
        self._sync_fold_btn()

    def _btn(self, icon: str, px: int, tip: str, fn) -> QToolButton:
        b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(icon, px))
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def _drop(self, tip: str, items, icon: str):
        b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(icon, 14)); b.setToolTip(tip)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(b); grp = QActionGroup(m); grp.setExclusive(True)
        for key, label, ic, mode in items:
            a = m.addAction(icons.icon(ic, 14), label, lambda k=key: self._run(k))
            a.setData(mode)
            if mode is not None:
                a.setCheckable(True); grp.addAction(a)
        b.setMenu(m)
        return b, m

    def _run(self, key: str):
        """펼침 메뉴 항목: 동작 뒤 아이콘·체크를 바로 맞춘다(보기 방식은 쪽이 안 바뀌면 알림이 없다)."""
        self.win.on_action(key)
        t = self.win.cur()
        if t is not None:
            self.win._sync_status(t)

    # ── 보이기 ────────────────────────────────────────────────
    def set_active(self, on: bool):
        """문서가 있을 때만 상태줄(접혔으면 감지 띠)."""
        self._active = on
        self._apply()

    def _apply(self):
        self._anim.stop()
        self.setMaximumHeight(HEIGHT)
        show = self._active and (not self.collapsed or self._peek)
        self.setVisible(show)
        self.hot.setVisible(self._active and self.collapsed and not self._peek)

    def set_collapsed(self, on: bool):
        self.collapsed = on
        self._peek = False; self._watch.stop(); self._in.stop()
        self.win.settings["status_collapsed"] = on
        self._sync_fold_btn()
        self._apply()

    def _fold_clicked(self):
        # 펴 있으면 접고, 접힌 채 나와 있으면 그대로 고정한다
        self.set_collapsed(not self.collapsed)
        self.win.save_settings_now()

    def _sync_fold_btn(self):
        self.fold_btn.setIcon(icons.icon("pin" if self.collapsed else "down", 14))
        self.fold_btn.setToolTip("하단 바 고정" if self.collapsed else "하단 바 축소")

    def peek(self):
        """접힌 상태줄을 미끄러져 나오게 한다."""
        if not (self._active and self.collapsed) or self._peek:
            return
        self._peek = True; self._away = 0
        self.hot.hide()
        self.setMaximumHeight(0); self.show()
        self._anim.stop(); self._anim.setDuration(SHOW_MS)
        self._anim.setStartValue(0); self._anim.setEndValue(HEIGHT); self._anim.start()
        self._watch.start()

    def unpeek(self):
        if not self._peek:
            return
        self._watch.stop()
        self._anim.stop(); self._anim.setDuration(HIDE_MS)
        self._anim.setStartValue(self.height()); self._anim.setEndValue(0)
        def done():
            self._anim.finished.disconnect(done)
            if self._peek:
                self._peek = False
                self._apply()
        self._anim.finished.connect(done)
        self._anim.start()

    def _check_leave(self):
        """나온 상태줄 밖으로 마우스가 나가 0.5초 지나면 숨긴다(메뉴·목록이 떠 있으면 기다림)."""
        inside = self.rect().contains(self.mapFromGlobal(QCursor.pos())) or QApplication.activePopupWidget() is not None \
            or self.page_edit.hasFocus() or self.zoom_box.lineEdit().hasFocus()
        self._away = 0 if inside else self._away + self._watch.interval()
        if self._away >= HIDE_MS:
            self.unpeek()

    def eventFilter(self, obj, e):
        if obj is getattr(self, "hot", None):
            if e.type() == QEvent.Type.Enter:
                self._in.start()
            elif e.type() == QEvent.Type.Leave:
                self._in.stop()
        elif obj is self.zoom_box and e.type() == QEvent.Type.Wheel:
            self.win.on_action("zoomin" if e.angleDelta().y() > 0 else "zoomout")
            return True
        return False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_size_label()

    def _slider_room(self) -> int:
        return self.zoom_slider.width() + self.layout().spacing()

    def minimumSizeHint(self):
        """확대 슬라이더는 좁으면 숨기므로 창 최소 폭에 넣지 않는다(쪽 칸·배율 칸이 넓어져도 창 최소 폭을 밀지 않게)."""
        s = super().minimumSizeHint()
        if not self.zoom_slider.isHidden():
            s.setWidth(max(0, s.width() - self._slider_room()))
        return s

    def _fit_size_label(self):
        """좁으면 쪽 크기 글자를 숨기고(가운데 단추와 겹치지 않게), 더 좁으면 확대 슬라이더도 숨긴다(±·배율 칸은 남음)."""
        lay = self.layout()
        need = lay.contentsMargins().left() + lay.contentsMargins().right()
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and w not in (self.size_label, self.msg_label, self.zoom_slider) and not w.isHidden():
                fixed = w.minimumWidth() == w.maximumWidth()
                need += (w.minimumWidth() if fixed else w.sizeHint().width()) + lay.spacing()
            elif w is None:
                need += lay.itemAt(i).sizeHint().width()
        show_slider = self.width() >= need + self._slider_room()
        if show_slider != (not self.zoom_slider.isHidden()):
            self.zoom_slider.setVisible(show_slider)
        if show_slider:
            need += self._slider_room()
        need += self.size_label.sizeHint().width() + 24
        self.size_label.setVisible(self.width() >= need)

    # ── 지금 문서 비추기 ───────────────────────────────────────
    def show_page(self, cur: int | None = None, total: int | None = None, label: str | None = None):
        if cur is not None:
            self._page = (cur, total)
            self._label = label or ""
        if self.page_edit.hasFocus():
            return
        cur, total = self._page
        text = f"{self._label or cur + 1} / {total}"
        self._fit_page_edit(text)
        self.page_edit.setText(text)

    def _fit_page_edit(self, text: str = ""):
        """쪽 칸 폭: 네 자리(「9999 / 9999」) 이상, 문서 쪽 수 자릿수·쪽 레이블이 길면 그만큼 넓힌다."""
        nines = "9" * max(4, len(str(self._page[1] or 0)))
        fitwidth.fit(self.page_edit, [f"{nines} / {nines}", text])

    def page_text(self) -> str:
        """쪽 칸에 넣은 것(「3 / 12」 면 3)."""
        return self.page_edit.text().split("/")[0].strip()

    def sync_view(self, v, prefs: dict, w_pt: float, h_pt: float):
        """뷰어 상태를 상태줄에: 쪽 크기 · 확대 · 맞춤 · 보기 방식 · 도구 · 보기 기록."""
        self.size_label.setText(size_text(w_pt, h_pt, prefs.get("unit", "cm")))
        self._fit_size_label()
        self.zoom_slider.blockSignals(True); self.zoom_slider.setValue(step_index(v.zoom)); self.zoom_slider.blockSignals(False)
        if not self.zoom_box.lineEdit().hasFocus():
            self.zoom_box.setEditText(pct_text(v.zoom))
        for m, btn, attr in ((self.fit_menu, self.fit_btn, "zoom_mode"), (self.layout_menu, self.layout_btn, "layout_mode")):
            now = getattr(v, attr)
            for a in m.actions():
                if a.isCheckable() and a.data() is not None:
                    a.setChecked(a.data() == now)
                    if a.data() == now:
                        btn.setIcon(a.icon())
            if attr == "zoom_mode" and now not in [x[3] for x in FITS]:   # 고정 배율이면 맞춤 표시를 지운다
                g = m.actions()[0].actionGroup()
                if g is not None and g.checkedAction() is not None:
                    g.setExclusive(False); g.checkedAction().setChecked(False); g.setExclusive(True)
        self.cover_act.setChecked(bool(v.cover_separate))
        self.sb_hand.setChecked(v.tool == "hand"); self.sb_sel.setChecked(v.tool == "select")
        self.sb_hist["prev_view"].setEnabled(bool(v._back)); self.sb_hist["next_view"].setEnabled(bool(v._fwd))

    def checked_fit(self) -> str | None:
        a = next((a for a in self.fit_menu.actions() if a.isChecked()), None)
        return a.data() if a else None

    def checked_layout(self) -> str | None:
        a = next((a for a in self.layout_menu.actions() if a.isChecked() and a is not self.cover_act), None)
        return a.data() if a else None

