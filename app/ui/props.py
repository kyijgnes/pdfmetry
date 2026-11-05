"""오른쪽 속성 패널: 고른 주석의 모양·정보, 또는 지금 도구의 기본 모양.

일반적인 PDF 편집기처럼 그 종류에 맞는 칸만 보이고, 고른 주석은 바꾸는 즉시 적용한다.
바꾼 모양은 그 종류의 기본값으로 기억한다(engine/annot_style, 「마지막에 고친 모양을 기본값으로」, 환경설정에서 끔).
끄면 「기본값으로」 단추를 눌렀을 때만 기억한다. 잠긴 주석은 잠금만 풀 수 있다.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..engine import annot_shapes as SH
from ..engine import annot_style as S
from ..engine import annots as A
from .dialogs import ColorButton, from_rgb01
from .props_content import ContentProps
from .sidebar import group_card, panel_head

# 속성 패널 색 견본 여섯: 노랑·빨강·주황·파랑·초록·보라
SWATCHES = [(1, 238 / 255, 54 / 255), (1, 0, 0), (1, 154 / 255, 0), (49 / 255, 131 / 255, 1), (0, 253 / 255, 0),
            (194 / 255, 76 / 255, 1)]
_TEXT_KEYS = ("textbox", "callout", "typewriter")
_MEASURE = ("m_distance", "m_perimeter", "m_area")
_CLOUD_KEYS = ("rect", "oval", "polygon", "cloud")
_MEASURE_IT = ("/LineDimension", "/PolyLineDimension", "/PolygonDimension")


def _pdf_date(s: str) -> str:
    """D:20261021142000+09'00' → 2026-10-21 14:20."""
    s = (s or "").removeprefix("D:")
    if len(s) < 12 or not s[:12].isdigit():
        return ""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"


def tool_key(tool: str) -> str | None:
    """도구가 기본값을 기억하는 칸. 모양 더보기 넷은 다각형 칸을 같이 쓴다. 측정 도구는 「m_…」 그대로."""
    if tool in SH.SHAPES:
        return "polygon"
    return tool if tool in S.DEFAULTS or tool in _MEASURE else None


def measure_info(doc, a) -> dict | None:
    """측정 주석이면 결과(내용)와 축척. 측정 주석이 아니면 None."""
    f = doc.fitz
    it = f.xref_get_key(a.xref, "IT")[1]
    if it not in _MEASURE_IT and f.xref_get_key(a.xref, "Measure")[0] == "null":
        return None
    scale = f.xref_get_key(a.xref, "Measure/R")
    return {"result": a.info.get("content", ""), "scale": scale[1].strip("()") if scale[0] == "string" else ""}


class PropsPanel(QWidget):
    changed = Signal(dict)       # 도구 기본 속성 바뀜
    applyToSel = Signal(dict)    # 고른 주석에 적용(A.update 인자)
    deleteSel = Signal()

    def __init__(self, props: dict, viewer=None, store: S.StyleStore | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("proppanel")
        self.setFixedWidth(260)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.props = props
        self.viewer = viewer
        self.store = store or S.store()
        self.autosave = lambda: True     # 창이 환경설정(prefs["annot_autosave_style"])으로 바꿔 단다
        self.key: str | None = None     # 지금 보이는 종류(도구 이름)
        self.sel = None                  # 고른 주석(없으면 도구 기본값)
        self._loading = False
        self._info0: dict = {}
        self._measure: dict | None = None
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(300)
        self._timer.timeout.connect(self._apply_style)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(panel_head("속성"))
        # 옅은 회색 바탕 위에 쓰임새별 흰 카드. 창이 낮으면 스크롤
        body = QWidget(); body.setObjectName("propbody")
        lay = QVBoxLayout(body); lay.setContentsMargins(8, 6, 8, 8); lay.setSpacing(6)
        scroll = QScrollArea(); scroll.setWidget(body); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QWidget#propbody { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)
        head = QHBoxLayout(); head.setSpacing(6)
        self.kind = QLabel(""); self.kind.setObjectName("dim"); self.kind.setWordWrap(True); head.addWidget(self.kind, 1)
        self.lock = QPushButton("잠그기"); self.lock.setCheckable(True); self.lock.toggled.connect(self._lock_toggled)
        head.addWidget(self.lock, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.edit_props = ContentProps(viewer); lay.addWidget(self.edit_props)   # 편집: 고른 글자 덩어리·그림

        self.look_box, f = group_card("모양"); lay.addWidget(self.look_box); self.look = f
        sw = QWidget(); sl = QHBoxLayout(sw); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(4)
        for c in SWATCHES:
            b = QToolButton(); b.setFixedSize(20, 20)
            b.setStyleSheet(f"QToolButton{{border:1px solid rgba(0,0,0,40); border-radius:3px; background:{from_rgb01(c).name()};}}")
            b.clicked.connect(lambda _=False, c=c: self._swatch(c)); sl.addWidget(b)
        sl.addStretch()
        self.swatches = sw; f.addRow("", sw)
        self.color = ColorButton((1, 0, 0)); f.addRow("색", self.color)
        self.border = ColorButton((1, 0, 0), allow_none=True); f.addRow("테두리색", self.border)
        self.fill = ColorButton(None, allow_none=True); f.addRow("채움색", self.fill)
        self.width = QDoubleSpinBox(); self.width.setRange(0.1, 12); self.width.setSingleStep(0.1); self.width.setDecimals(1)
        f.addRow("두께", self.width)
        self.dashes = QComboBox(); f.addRow("선 모양", self.dashes)
        self.start = QComboBox(); self.start.addItems(list(S.LINE_ENDS)); f.addRow("시작 모양", self.start)
        self.end = QComboBox(); self.end.addItems(list(S.LINE_ENDS)); f.addRow("끝 모양", self.end)
        self.icon = QComboBox(); self.icon.addItems(list(S.ATTACH_ICONS)); f.addRow("아이콘", self.icon)
        for c in (self.dashes, self.start, self.end, self.icon):   # 긴 이름(거꾸로 닫힌 화살촉 등)이 패널을 넓히지 않게
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(6)
        op = QWidget(); ol = QHBoxLayout(op); ol.setContentsMargins(0, 0, 0, 0); ol.setSpacing(6)
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(0, 100)
        self.op_label = QLabel("100%"); self.op_label.setMinimumWidth(34)
        ol.addWidget(self.opacity, 1); ol.addWidget(self.op_label)
        self.op_row = op; f.addRow("불투명도", op)
        self.fontsize = QDoubleSpinBox(); self.fontsize.setRange(5, 200); self.fontsize.setDecimals(1); f.addRow("글자 크기", self.fontsize)
        self.m_result = QLabel(""); self.m_result.setWordWrap(True); f.addRow("측정 결과", self.m_result)
        self.m_scale = QLabel(""); self.m_scale.setWordWrap(True); f.addRow("축척", self.m_scale)
        btns = QHBoxLayout(); btns.setSpacing(6)
        self.default_btn = QPushButton("기본값으로"); self.default_btn.clicked.connect(self._use_as_default); btns.addWidget(self.default_btn)
        self.reset_btn = QPushButton("처음 값으로"); self.reset_btn.clicked.connect(self._reset); btns.addWidget(self.reset_btn)
        lay.addLayout(btns)

        self.info_box, f = group_card("정보"); lay.addWidget(self.info_box)
        self.author = QLineEdit(); f.addRow("작성자", self.author)
        self.subject = QLineEdit(); f.addRow("제목", self.subject)
        self.dates = QLabel(""); self.dates.setObjectName("dim"); f.addRow("", self.dates)
        self.content = QPlainTextEdit(); self.content.setPlaceholderText("주석 내용"); self.content.setMaximumHeight(80)
        f.addRow(self.content)
        # 칸을 떠날 때 바로 적용(적용 단추 없음)
        self.author.editingFinished.connect(self._apply_info); self.subject.editingFinished.connect(self._apply_info)
        self.content.installEventFilter(self)
        from .dlg_redact import RedactProps
        self.redact_props = RedactProps(viewer); lay.addWidget(self.redact_props)   # 교정 도구·교정 표시
        self.del_btn = QPushButton("고른 주석 삭제"); self.del_btn.clicked.connect(self.deleteSel.emit); lay.addWidget(self.del_btn)
        lay.addStretch()
        self._align_labels(body)

        for w in (self.color, self.border, self.fill):
            w.clicked.connect(self._edited)
        self.width.valueChanged.connect(self._edited); self.fontsize.valueChanged.connect(self._edited)
        for c in (self.dashes, self.start, self.end, self.icon):
            c.currentIndexChanged.connect(self._edited)
        self.opacity.valueChanged.connect(self._opacity_moved); self.opacity.sliderReleased.connect(self._edited)
        if viewer is not None and hasattr(viewer, "toolChanged"):
            viewer.toolChanged.connect(self.tool_selected)
        self.show_selection(None)

    @staticmethod
    def _align_labels(body: QWidget):
        """카드마다 이름 칸 폭이 달라 입력칸이 들쭉날쭉하던 것을 가장 긴 이름에 맞춘다."""
        labels = [f.labelForField(f.itemAt(i, QFormLayout.ItemRole.FieldRole).widget())
                  for f in body.findChildren(QFormLayout) for i in range(f.rowCount())
                  if f.itemAt(i, QFormLayout.ItemRole.FieldRole)]
        labels = [l for l in labels if l is not None]
        w = max((l.sizeHint().width() for l in labels), default=0)
        for l in labels:
            l.setMinimumWidth(w)

    def eventFilter(self, obj, e):
        if obj is self.content and e.type() == QEvent.Type.FocusOut:
            self._apply_info()
        return super().eventFilter(obj, e)

    # ── 무엇을 보이나 ─────────────────────────────────────────
    def _tool(self) -> str:
        return getattr(self.viewer, "tool", "") if self.viewer is not None else ""

    def _tool_key(self) -> str | None:
        return tool_key(self._tool())

    def _fields(self) -> tuple[str, ...]:
        if self.key in _MEASURE:   # 측정 도구의 축척·단위는 측정 설정 창에서
            return ()
        fs = S.FIELDS.get(self.key, ())
        if self.sel is None:
            fs = tuple(x for x in fs if x not in S.SELECTED_ONLY)
        if self.key == "stamp":
            fs = () if self.sel is None else ("stroke", "opacity")   # 새 스탬프는 고른 스탬프 모양 그대로
        if self._measure is not None:
            fs += ("m_result", "m_scale")
        return fs

    def show_selection(self, sel):
        annot = sel is not None and sel.kind == "annot"
        tool = self._tool()
        tkey = self._tool_key()
        self._measure = None
        self.del_btn.setVisible(sel is not None and sel.kind in ("annot", "widget", "block", "image"))
        if annot and tkey is None:
            self.sel = sel
            a = self._annot()
            self.key = S.key_of(self.viewer.doc, a) if a is not None else None
            style = S.style_of(self.viewer.doc, a, self.key) if a is not None else {}
            self._measure = measure_info(self.viewer.doc, a) if a is not None else None
            name = S.TOOL_NAMES.get(self.key) or sel.extra.get("kind_ko", "주석")
            self.kind.setText(f"{name} · 고른 주석")
            self._load(style)
            self._load_info(a)
            self._set_lock(a is not None and A.is_locked(self.viewer.doc, sel.page, sel.xref))
        else:
            self.sel = None
            self.key = tkey
            if tkey:
                name = SH.SHAPES.get(tool) or S.TOOL_NAMES.get(tkey) or {
                    "m_distance": "거리 측정", "m_perimeter": "둘레 측정", "m_area": "영역 측정"}[tkey]
                self.kind.setText(f"{name} · 새로 만들 때 쓰는 값")
                self._load(self.store.get(tkey) if tkey in S.DEFAULTS else {})
            else:
                self.kind.setText({"words": "글자 선택", "widget": "양식 칸", "block": "글자 덩어리", "image": "이미지"}.get(
                    getattr(sel, "kind", ""), "고른 것 없음"))
            self._set_lock(False)
        self.lock.setVisible(self.sel is not None)
        self.edit_props.show_sel(sel if self.key is None else None)
        self._layout_rows()
        self.redact_props.show_sel(sel, tool)

    def _layout_rows(self):
        fs = self._fields()
        main = next((x for x in ("color", "stroke", "text_color") if x in fs), None)
        rows = {self.swatches: main is not None, self.color: main is not None, self.border: "border" in fs,
                self.fill: "fill" in fs, self.width: "width" in fs, self.dashes: "dashes" in fs,
                self.start: "start" in fs, self.end: "end" in fs, self.icon: "icon" in fs,
                self.op_row: "opacity" in fs, self.fontsize: "fontsize" in fs,
                self.m_result: "m_result" in fs, self.m_scale: "m_scale" in fs and bool(self.m_scale.text())}
        for w, on in rows.items():
            self.look.setRowVisible(w, on)
        if main:
            self.look.labelForField(self.color).setText({"color": "색", "stroke": "선 색", "text_color": "글자색"}[main])
        if self.key in _TEXT_KEYS:
            self.look.labelForField(self.width).setText("테두리 두께")
            self.look.labelForField(self.fill).setText("바탕색")
        else:
            self.look.labelForField(self.width).setText("두께")
            self.look.labelForField(self.fill).setText("채움색")
        # 말풍선은 지시선 끝 하나
        self.look.labelForField(self.start).setText("지시선 끝" if self.key == "callout" else "시작 모양")
        self.look_box.setVisible(any(rows.values()))
        self.reset_btn.setVisible(self.sel is None and self.key in S.DEFAULTS)
        self.default_btn.setVisible(self.sel is not None and self.key in S.DEFAULTS)
        self.info_box.setVisible(self.sel is not None)

    def _set_lock(self, on: bool):
        self.lock.blockSignals(True)
        self.lock.setChecked(on); self.lock.setText("잠김" if on else "잠그기")
        self.lock.blockSignals(False)
        for w in (self.look_box, self.info_box, self.default_btn, self.del_btn):
            w.setEnabled(not on)

    def _annot(self):
        if self.sel is None or self.viewer is None:
            return None
        try:
            return A.get(self.viewer.doc, self.sel.page, self.sel.xref)
        except Exception:  # noqa: BLE001
            return None

    def _fill_dashes(self):
        """선 모양 목록: 사각형·타원·다각형은 구름 둘을 더 보인다."""
        names = list(S.DASHES) + (list(S.CLOUDS) if self.key in _CLOUD_KEYS else [])
        if [self.dashes.itemText(i) for i in range(self.dashes.count())] != names:
            self.dashes.clear(); self.dashes.addItems(names)

    def _load(self, s: dict):
        self._loading = True
        try:
            main = s.get("color") or s.get("text_color") or s.get("stroke")
            self.color.color = tuple(main) if main else (1, 0, 0); self.color._paint()
            self.border.color = tuple(s["border"]) if s.get("border") else None; self.border._paint()
            self.fill.color = tuple(s["fill"]) if s.get("fill") else None; self.fill._paint()
            # 굵기는 0.1 단위, 글상자 테두리만 0(없음)까지
            self.width.setMinimum(0 if self.key in _TEXT_KEYS else 0.1)
            self.width.setValue(float(s.get("width", 1) or 0))
            self._fill_dashes()
            clouds = int(s.get("clouds") or 0)
            name = next((n for n, v in S.CLOUDS.items() if v == clouds), None) if clouds else None
            d = tuple(s.get("dashes") or ())
            name = name or next((n for n, v in S.DASHES.items() if v == d), "실선")
            self.dashes.setCurrentText(name)
            ends = list(S.LINE_ENDS.values())
            self.start.setCurrentIndex(ends.index(s.get("start", 0)) if s.get("start", 0) in ends else 0)
            self.end.setCurrentIndex(ends.index(s.get("end", 0)) if s.get("end", 0) in ends else 0)
            icons = list(S.ATTACH_ICONS.values())
            self.icon.setCurrentIndex(icons.index(s["icon"]) if s.get("icon") in icons else icons.index("PushPin"))
            op = s.get("opacity", 1.0)
            self.opacity.setValue(int(round((1.0 if op is None or op < 0 else op) * 100)))
            self.op_label.setText(f"{self.opacity.value()}%")
            self.fontsize.setValue(float(s.get("fontsize", 14)))
            m = self._measure or {}
            self.m_result.setText(m.get("result", "")); self.m_scale.setText(m.get("scale", ""))
        finally:
            self._loading = False

    def _load_info(self, a):
        info = a.info if a is not None else {}
        self.author.setText(info.get("title", ""))
        self.subject.setText(info.get("subject", ""))
        self.content.setPlainText(info.get("content", "") if a is not None else (self.sel.text or ""))
        made, mod = _pdf_date(info.get("creationDate", "")), _pdf_date(info.get("modDate", ""))
        self.dates.setText(" · ".join(x for x in (f"만듦 {made}" if made else "", f"고침 {mod}" if mod else "") if x))
        self.dates.setVisible(bool(made or mod))
        self._info0 = self._info()

    # ── 바꿀 때 ───────────────────────────────────────────────
    def _values(self) -> dict:
        """보이는 칸의 값을 그 종류의 칸 이름으로."""
        fs = self._fields()
        v: dict = {}
        c = self.color.color
        for k in ("color", "stroke", "text_color"):
            if k in fs:
                v[k] = c
        if "border" in fs:
            v["border"] = self.border.color
        if "fill" in fs:
            v["fill"] = self.fill.color
        if "width" in fs:
            v["width"] = round(self.width.value(), 1)
        if "dashes" in fs:
            name = self.dashes.currentText()
            v["dashes"] = S.DASHES.get(name, ())
            if "clouds" in fs:
                v["clouds"] = S.CLOUDS.get(name, 0)
        if "start" in fs:
            v["start"] = S.LINE_ENDS[self.start.currentText()]
        if "end" in fs:
            v["end"] = S.LINE_ENDS[self.end.currentText()]
        if "icon" in fs:
            v["icon"] = S.ATTACH_ICONS[self.icon.currentText()]
        if "opacity" in fs:
            v["opacity"] = self.opacity.value() / 100
        if "fontsize" in fs:
            v["fontsize"] = self.fontsize.value()
        return v

    def _swatch(self, c):
        self.color.color = tuple(c); self.color._paint()
        self._edited()

    def _opacity_moved(self, *_):
        self.op_label.setText(f"{self.opacity.value()}%")
        if not self.opacity.isSliderDown():  # 끄는 동안은 놓을 때 한 번만(되돌리기가 한 걸음씩 쌓이지 않게)
            self._edited()

    def _edited(self, *_):
        if self._loading or self.key is None:
            return
        if self.sel is not None:
            self._timer.start()
            return
        if self.key in S.DEFAULTS:
            self.store.remember(self.key, self._values())
            self._push_tool()

    def _push_tool(self):
        S.to_viewer_props(self.key, self.store.get(self.key), self.props)
        self.changed.emit(dict(self.props))

    def _reset(self):
        if self.key in S.DEFAULTS:
            self.store.reset(self.key)
            self._load(self.store.get(self.key))
            self._push_tool()

    def _use_as_default(self):
        """고른 주석의 모양을 그 종류의 기본값으로(자동 저장을 꺼 둔 사람이 쓰는 단추)."""
        if self.sel is not None and self.key in S.DEFAULTS:
            self.store.remember(self.key, self._values())
            S.to_viewer_props(self.key, self.store.get(self.key), self.props)

    def _apply_style(self):
        """고른 주석에 바로 적용하고, 자동 저장이 켜져 있으면 그 종류의 기본값으로도 기억한다."""
        if self.sel is None or self.lock.isChecked():
            return
        v = self._values()
        d: dict = {"opacity": v.get("opacity")}
        main = v.get("color", v.get("stroke", v.get("text_color")))
        if main is not None:
            d["stroke"] = main
        if "fill" in v:
            d["fill"] = v["fill"] if v["fill"] is not None else ()
        if self.key == "areahl":
            d["fill"] = main
        for k in ("width", "fontsize", "border", "dashes", "clouds", "start", "end", "icon"):
            if k in v:
                d[k] = v[k]
        if self.key == "callout":
            d.pop("end", None)
        self.applyToSel.emit(d)
        if self.autosave():
            self.store.remember(self.key, v)

    def _info(self) -> dict:
        return {"content": self.content.toPlainText(), "author": self.author.text(), "subject": self.subject.text()}

    def _apply_info(self):
        if self.sel is None or self.lock.isChecked():
            return
        now = self._info()
        if now == self._info0:
            return
        d = {k: v for k, v in now.items() if v != self._info0.get(k)}
        self._info0 = now
        self.applyToSel.emit(d)

    def _lock_toggled(self, on: bool):
        if self.sel is None:
            return
        self._apply_info()   # 잠그기 전에 쓰던 내용부터
        self._set_lock(on)
        self.applyToSel.emit({"locked": on})

    # ── 창이 부름 ─────────────────────────────────────────────
    def tool_selected(self, tool: str):
        """도구를 고르면 그 종류의 기억해 둔 값을 뷰어 도구 속성에 얹는다."""
        key = tool_key(tool)
        if key in S.DEFAULTS:
            S.to_viewer_props(key, self.store.get(key), self.props)
        self.show_selection(None if key is not None else self.sel)

    def remember(self, key: str, values: dict):
        """대화상자로 만든 주석(글상자 등)의 값을 그 종류의 기본값으로."""
        if self.store.remember(key, {k: v for k, v in values.items() if v is not None or k in ("fill", "border")}):
            S.to_viewer_props(key, self.store.get(key), self.props)
