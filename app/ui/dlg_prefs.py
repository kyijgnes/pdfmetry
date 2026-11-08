"""환경 설정 창(Ctrl+K): 왼쪽 단추 일곱(일반 · 글꼴 · OCR · 단위 · 비밀번호 · 빈 PDF · 단축키), 아래 「적용」·「취소」.

「적용」은 한 번이라도 열어 본 페이지만 settings 에 쓴다(prefs 와 hotkeys). 파일 저장·화면 반영은 부르는 쪽.
열쇠 이름은 정해 둔 표를 따른다. 읽는 쪽(열기·저장·뷰어)은 이 값을 prefs 에서 그대로 꺼내 쓴다.
"""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QLocale, QRegularExpression, Qt
from PySide6.QtGui import QColor, QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..appinfo import APP_NAME
from . import dialogs as D
from . import shortcuts as K
from . import theme
from .dlg_properties import UNIT_PT, nav_buttons

PAGES = (("general", "일반"), ("font", "글꼴"), ("ocr", "OCR"), ("unit", "단위"), ("password", "비밀번호"),
         ("blank", "빈 PDF"), ("shortcut", "단축키"))

LAYOUTS = (("auto", "자동"), ("single", "단일 페이지 보기"), ("continuous", "한 페이지씩 계속하기"),
           ("facing", "두 페이지 보기"), ("facing_continuous", "두 페이지를 계속하기"))
ZOOMS = (("auto", "자동"), ("fit_width", "너비 맞춤"), ("fit_height", "높이 맞춤"), ("fit_page", "창 맞추기"),
         *((f"p{n}", f"{n}%") for n in (10, 25, 50, 75, 100, 125, 150, 200, 400, 600)), ("last", "마지막으로 쓴 확대"))
UNITS = (("in", "인치"), ("cm", "센티미터"), ("mm", "밀리미터"), ("pt", "포인트"), ("pc", "피카스"))
SIZES = (12, 5, 5.5, 6.5, 7.5, 8, 9, 10, 10.5, 11, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72)
BLANK_SIZES_MM = (("Letter", 215.9, 279.4), ("Tabloid", 279.4, 431.8), ("Legal", 215.9, 355.6),
                  ("A3", 297, 420), ("A4", 210, 297), ("A5", 148, 210))
BLANK_NAME = "제목 없음"
MM = 72 / 25.4

EDIT_FONT = {"font": "맑은 고딕", "style": "normal", "size": 14, "color": [0, 0, 0]}
COMMENT_FONT = {"font": "Malgun Gothic", "size": 14, "color": [1, 0, 0]}   # font: 글꼴 이름(fonts.font_families)
GRID = {"w": 72 / 2.54, "h": 72 / 2.54, "left": 0.0, "top": 0.0, "sub": 3, "color": [1, 0, 0]}
BLANK = {"name": BLANK_NAME, "count": 1, "size": "A4", "w": 210 * MM, "h": 297 * MM, "landscape": False}


# 사전 설정 비밀번호는 열기 쪽(dlg_password)과 같은 형식으로 덮는다
from .dlg_password import protect, unprotect  # noqa: E402,F401


def _grid_color(c) -> QColor:
    """격자 색은 [r,g,b](0~1)·[r,g,b](0~255)·"#rrggbb" 어느 것으로 적혀 있어도 받는다(뷰어 grid_spec 과 같게)."""
    from .viewer import grid_spec
    return grid_spec({"grid": {"color": c}})["color"]


def blank_name(text: str) -> str:
    """빈 PDF 파일 이름: 앞뒤 공백 빼고, 못 쓰는 글자는 _, 끝 .pdf 떼고, 비면 「제목 없음」."""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (text or "").strip())
    s = re.sub(r"\.pdf$", "", s, flags=re.I).strip()
    return s or BLANK_NAME


def lang_name(tag: str) -> str:
    loc = QLocale(tag)
    name = loc.nativeLanguageName()
    return f"{name} ({tag})" if name else tag


class UnitSpin(QDoubleSpinBox):
    """속은 pt, 보이기는 고른 단위."""
    def __init__(self, pt: float, unit: str, lo_pt: float = 0.0, hi: float = 1000.0):
        super().__init__()
        self.lo_pt, self.hi = lo_pt, hi
        self.set_unit(unit); self.set_pt(pt)

    def set_unit(self, unit: str):
        pt = self.pt() if hasattr(self, "unit") else 0.0
        self.unit, self.k = unit, UNIT_PT[unit]
        self.setDecimals(1 if unit in ("pt", "mm") else 3 if unit in ("in", "pc") else 2); self.setSuffix(f" {unit}")
        self.setRange(self.lo_pt / self.k, self.hi)
        if hasattr(self, "_set"):
            self.set_pt(pt)
        self._set = True

    def set_pt(self, pt: float):
        self.setValue(pt / self.k)

    def pt(self) -> float:
        return self.value() * self.k


class PasswordEdit(QLineEdit):
    """저장된 비밀번호는 보이지 않는다. 자리만 채워 두고 첫 키에 비운다."""
    MASK = "●" * 8

    def __init__(self, stored: bool):
        super().__init__()
        self.setEchoMode(QLineEdit.EchoMode.Password); self.setMaxLength(200)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.untouched = stored
        if stored:
            self.setText(self.MASK)

    def keyPressEvent(self, e):
        if self.untouched:
            self.untouched = False; self.clear()
        super().keyPressEvent(e)


class PreferencesDialog(QDialog):
    def __init__(self, parent, settings: dict, page: str = "general"):
        super().__init__(parent)
        self.settings = settings
        self.prefs = settings.setdefault("prefs", {})
        self.setWindowTitle("환경 설정"); self.setMinimumSize(720, 530); self.resize(720, 530)
        lay = QVBoxLayout(self)
        row = QHBoxLayout(); lay.addLayout(row, 1)
        left = QVBoxLayout(); left.setSpacing(2); row.addLayout(left)
        self.stack = QStackedWidget(); row.addWidget(self.stack, 1)
        self.group = nav_buttons(self, PAGES, left)
        self.built: dict[str, QWidget] = {}
        for _ in PAGES:
            holder = QWidget(); QVBoxLayout(holder).setContentsMargins(12, 0, 0, 0); self.stack.addWidget(holder)
        self.group.idClicked.connect(self.show_page)
        lay.addWidget(D._buttons(self, "적용"))
        keys = [k for k, _ in PAGES]
        self.show_page(keys.index(page) if page in keys else 0)

    def show_page(self, n: int):
        key = PAGES[n][0]
        self.group.button(n).setChecked(True)
        if key not in self.built:
            w = getattr(self, f"_build_{key}")()
            self.built[key] = w
            self.stack.widget(n).layout().addWidget(w)
        self.stack.setCurrentIndex(n)

    @staticmethod
    def _combo(items, val) -> QComboBox:
        c = QComboBox()
        for data, text in items:
            c.addItem(text, data)
        c.setCurrentIndex(max(0, c.findData(val)))
        return c

    @staticmethod
    def _scroll(w: QWidget) -> QScrollArea:
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QScrollArea.Shape.NoFrame); sa.setWidget(w)
        sa.viewport().setAutoFillBackground(False); w.setAutoFillBackground(False)   # 창 바탕 그대로
        return sa

    # ── 일반 ─────────────────────────────────────────────────────
    def _build_general(self) -> QWidget:
        p = self.prefs
        w = QWidget(); f = QFormLayout(w)
        self.recent_n = QSpinBox(); self.recent_n.setRange(0, 100); self.recent_n.setValue(int(p.get("recent_n", 20)))
        f.addRow("최근 파일 목록", self.recent_n)
        self.layout_ = self._combo(LAYOUTS, p.get("default_layout", "auto")); f.addRow("기본 페이지 레이아웃", self.layout_)
        zoom = p.get("default_zoom", "auto")
        items = ZOOMS + ((("actual", "실제 크기"),) if zoom == "actual" else ())   # 옛 설정값은 그대로 보인다
        self.zoom = self._combo(items, zoom); f.addRow("기본 확대/축소", self.zoom)
        self.select_mode = self._combo((("select", "선택"), ("hand", "손")), p.get("select_mode", "select"))
        f.addRow("기본 선택 모드", self.select_mode)
        self.smooth = QCheckBox("부드러운 스크롤 효과 사용"); self.smooth.setChecked(p.get("smooth_scroll", True)); f.addRow(self.smooth)
        sd = QHBoxLayout()
        self.save_dir_on = QCheckBox("새 PDF의 위치 저장"); self.save_dir_on.setChecked(p.get("save_dir_on", False))
        self.save_dir = QLineEdit(p.get("save_dir") or os.path.join(os.path.expanduser("~"), "Documents"))
        pick = QPushButton("…"); pick.setFixedWidth(32)
        pick.clicked.connect(lambda: self._pick_dir(self.save_dir))
        self.save_dir_on.toggled.connect(lambda on: (self.save_dir.setEnabled(on), pick.setEnabled(on)))
        self.save_dir.setEnabled(self.save_dir_on.isChecked()); pick.setEnabled(self.save_dir_on.isChecked())
        sd.addWidget(self.save_dir, 1); sd.addWidget(pick)
        f.addRow(self.save_dir_on); f.addRow(sd)
        au = QHBoxLayout()
        self.autosave = QCheckBox("모든 파일 자동 저장"); self.autosave.setChecked(p.get("autosave", True))
        self.autosave_min = QSpinBox(); self.autosave_min.setRange(1, 20); self.autosave_min.setValue(int(p.get("autosave_min", 10)))
        self.autosave.toggled.connect(self.autosave_min.setEnabled); self.autosave_min.setEnabled(self.autosave.isChecked())
        au.addWidget(self.autosave); au.addWidget(self.autosave_min); au.addWidget(QLabel("분")); au.addStretch(1)
        f.addRow(au)
        self.restore = QCheckBox(f"{APP_NAME} 실행 중 마지막 세션에서 PDF 열기"); self.restore.setChecked(p.get("restore", False)); f.addRow(self.restore)
        self.confirm_many = QCheckBox("여러 탭을 닫기 전에 확인"); self.confirm_many.setChecked(p.get("confirm_close_many", True)); f.addRow(self.confirm_many)
        self.same_window = QCheckBox("동일한 창에서 새 탭으로 문서 열기"); self.same_window.setChecked(p.get("same_window", True)); f.addRow(self.same_window)
        self.autosave_style = QCheckBox("마지막으로 고친 주석 모양을 기본값으로")
        self.autosave_style.setChecked(p.get("annot_autosave_style", True)); f.addRow(self.autosave_style)
        # 알PDF 에 없는 우리 칸
        self.author = QLineEdit(p.get("author", "")); f.addRow("주석 작성자 이름", self.author)
        self.skin = self._combo(theme.SKINS, theme.skin(p)); f.addRow("스킨", self.skin)
        self.winsize = self._combo((("last", "마지막으로 닫은 크기"), ("max", "화면 가득"), ("normal", "보통 크기")),
                                   p.get("window_size", "last"))
        f.addRow("열 때 창 크기", self.winsize)
        self.share_dir = QLineEdit(p.get("share_dir", "")); f.addRow("공유폴더", self.share_dir)
        self.default_dir = QLineEdit(p.get("default_dir", "")); f.addRow("변환 저장 폴더", self.default_dir)
        if os.name == "nt":
            pr = QHBoxLayout()
            self.printer_state = QLabel(); self.printer_btn = QPushButton()
            self.printer_btn.clicked.connect(self._toggle_printer)
            pr.addWidget(self.printer_state); pr.addWidget(self.printer_btn); pr.addStretch(1)
            f.addRow(f"{APP_NAME} 프린터", pr)
            self._show_printer()
        return self._scroll(w)

    def _show_printer(self):
        from .. import printer
        on = printer.installed()
        self.printer_state.setText("설치됨" if on else "설치 안 됨")
        self.printer_btn.setText("프린터 지우기" if on else "프린터 설치")

    def _toggle_printer(self):
        """관리자 권한으로 「PDF메트리」 프린터를 깔거나 지운다(UAC 창이 뜬다)."""
        from .. import printer
        on = printer.installed()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            code = printer.run_elevated(printer.UNINSTALL_FLAG if on else printer.INSTALL_FLAG)
        finally:
            QApplication.restoreOverrideCursor()
        self._show_printer()
        if code == 1223:   # 사용자가 UAC 에서 「아니요」
            return
        if code != 0 or printer.installed() == on:
            QMessageBox.warning(self, "환경 설정", "프린터를 " + ("지우지" if on else "설치하지") + " 못했습니다.")

    def _pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "폴더 고르기", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def _save_general(self):
        self.prefs.update({
            "recent_n": self.recent_n.value(), "default_layout": self.layout_.currentData(),
            "default_zoom": self.zoom.currentData(), "select_mode": self.select_mode.currentData(),
            "smooth_scroll": self.smooth.isChecked(), "save_dir_on": self.save_dir_on.isChecked(),
            "save_dir": self.save_dir.text().strip(), "autosave": self.autosave.isChecked(),
            "autosave_min": self.autosave_min.value(), "restore": self.restore.isChecked(),
            "confirm_close_many": self.confirm_many.isChecked(), "same_window": self.same_window.isChecked(),
            "annot_autosave_style": self.autosave_style.isChecked(), "author": self.author.text(),
            "theme": self.skin.currentData(), "window_size": self.winsize.currentData(),
            "share_dir": self.share_dir.text(), "default_dir": self.default_dir.text()})

    # ── 글꼴 ─────────────────────────────────────────────────────
    def _size_combo(self) -> QComboBox:
        c = QComboBox(); c.setEditable(True)
        c.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,3}(\.\d)?"), c))
        for s in sorted(SIZES):
            c.addItem(f"{s:g}")
        return c

    def _build_font(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        g1 = QGroupBox("텍스트 글꼴 편집"); f1 = QFormLayout(g1)
        self.ef_font = QFontComboBox(); f1.addRow("글꼴", self.ef_font)
        self.ef_style = QComboBox()
        for data, text in (("normal", "일반"), ("bold", "굵게"), ("italic", "기울임꼴")):
            self.ef_style.addItem(text, data)
        f1.addRow("스타일", self.ef_style)
        self.ef_size = self._size_combo(); f1.addRow("크기", self.ef_size)
        self.ef_color = D.ColorButton(); f1.addRow("색상", self.ef_color)
        r1 = QPushButton("기본 재설정"); r1.clicked.connect(lambda: self._fill_edit_font(EDIT_FONT)); f1.addRow(r1)
        v.addWidget(g1)
        g2 = QGroupBox("주석 텍스트 박스 글꼴"); f2 = QFormLayout(g2)
        from ..engine import fonts as F
        self.cf_font = QComboBox(); self.cf_font.setEditable(True)
        self.cf_font.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cf_font.addItems(F.font_families()); f2.addRow("글꼴", self.cf_font)
        self.cf_size = self._size_combo(); f2.addRow("크기", self.cf_size)
        self.cf_color = D.ColorButton(); f2.addRow("색상", self.cf_color)
        r2 = QPushButton("기본 재설정"); r2.clicked.connect(lambda: self._fill_comment_font(COMMENT_FONT)); f2.addRow(r2)
        v.addWidget(g2); v.addStretch(1)
        self._fill_edit_font({**EDIT_FONT, **(self.prefs.get("edit_font") or {})})
        self._fill_comment_font({**COMMENT_FONT, **(self.prefs.get("comment_font") or {})})
        return w

    def _fill_edit_font(self, d: dict):
        self.ef_font.setCurrentFont(QFont(d["font"]))
        self.ef_style.setCurrentIndex(max(0, self.ef_style.findData(d["style"])))
        self.ef_size.setEditText(f"{float(d['size']):g}")
        self.ef_color.color = tuple(d["color"]); self.ef_color._paint()

    def _fill_comment_font(self, d: dict):
        self.cf_font.setCurrentText(d.get("font") or COMMENT_FONT["font"])
        self.cf_size.setEditText(f"{float(d['size']):g}")
        self.cf_color.color = tuple(d["color"]); self.cf_color._paint()

    @staticmethod
    def _size_val(c: QComboBox, dflt: float) -> float:
        try:
            return max(1.0, min(999.0, float(c.currentText())))
        except ValueError:
            return dflt

    def _save_font(self):
        self.prefs["edit_font"] = {"font": self.ef_font.currentFont().family(), "style": self.ef_style.currentData(),
                                   "size": self._size_val(self.ef_size, 14), "color": list(self.ef_color.color)}
        from ..engine import fonts as F
        fam = self.cf_font.currentText().strip()
        self.prefs["comment_font"] = {"font": fam if fam in F.font_families() else COMMENT_FONT["font"],
                                      "size": self._size_val(self.cf_size, 14), "color": list(self.cf_color.color)}

    # ── OCR ──────────────────────────────────────────────────────
    def _build_ocr(self) -> QWidget:
        from . import dlg_ocr as DO
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("언어 인식"))
        avail = DO.languages()
        self._ocr_chosen = DO.current_langs(self.prefs, avail)   # 옛 ocr_lang 은 여기서 ocr_langs 로 옮겨진다
        self.ocr_langs = DO.LangList(avail, self._ocr_chosen)
        v.addWidget(self.ocr_langs, 1)
        return self._scroll(w)

    def _check_ocr(self) -> bool:
        if not self.ocr_langs.checked():
            QMessageBox.warning(self, "환경 설정", "OCR을 수행할 언어를 선택하십시오.")
            return False
        return True

    def _save_ocr(self):
        self.prefs.pop("ocr_lang", None)
        self.prefs["ocr_langs"] = self.ocr_langs.checked()

    # ── 단위 · 격자 ──────────────────────────────────────────────
    def _build_unit(self) -> QWidget:
        unit = self.prefs.get("unit", "cm")
        unit = unit if unit in UNIT_PT else "cm"
        grid = {**GRID, **(self.prefs.get("grid") or {})}
        w = QWidget(); v = QVBoxLayout(w)
        g = QGroupBox("페이지 및 모눈 단위"); gl = QHBoxLayout(g)
        self.unit_group = QButtonGroup(self)
        for n, (data, text) in enumerate(UNITS):
            r = QRadioButton(text); r.setProperty("unit", data); self.unit_group.addButton(r, n); gl.addWidget(r)
            r.setChecked(data == unit)
        v.addWidget(g)
        g2 = QGroupBox("격자"); f = QFormLayout(g2)
        self.grid_w = UnitSpin(grid["w"], unit, lo_pt=1.0); f.addRow("선 사이 너비", self.grid_w)
        self.grid_h = UnitSpin(grid["h"], unit, lo_pt=1.0); f.addRow("선 사이 높이", self.grid_h)
        self.grid_left = UnitSpin(grid["left"], unit); f.addRow("왼쪽 끝으로 그리드 오프셋", self.grid_left)
        self.grid_top = UnitSpin(grid["top"], unit); f.addRow("위쪽 끝으로 그리드 오프셋", self.grid_top)
        self.grid_sub = QSpinBox(); self.grid_sub.setRange(0, 100); self.grid_sub.setValue(int(grid["sub"])); f.addRow("세분", self.grid_sub)
        self.grid_color = D.ColorButton(D.to_rgb01(_grid_color(grid["color"]))); f.addRow("눈금선 색", self.grid_color)
        v.addWidget(g2); v.addStretch(1)
        self.unit_group.idClicked.connect(self._unit_changed)
        return w

    def _unit_changed(self, n: int):
        u = UNITS[n][0]
        for s in (self.grid_w, self.grid_h, self.grid_left, self.grid_top):
            s.set_unit(u)

    def _save_unit(self):
        self.prefs["unit"] = self.unit_group.checkedButton().property("unit")
        self.prefs["grid"] = {"w": self.grid_w.pt(), "h": self.grid_h.pt(), "left": self.grid_left.pt(),
                              "top": self.grid_top.pt(), "sub": self.grid_sub.value(),
                              "color": list(self.grid_color.color)}

    # ── 비밀번호 ─────────────────────────────────────────────────
    def _build_password(self) -> QWidget:
        w = QWidget(); f = QFormLayout(w)
        note = QLabel(f"암호화된 문서를 열 때 {APP_NAME}는 아래 설정한 비밀번호를 사용하여 암호화된 문서를 잠금 해제합니다.")
        note.setWordWrap(True); f.addRow(note)
        stored = list(self.prefs.get("preset_pw") or [])
        self.pw_edits: list[tuple[PasswordEdit, str]] = []
        for n in range(3):
            tok = stored[n] if n < len(stored) else ""
            e = PasswordEdit(bool(tok)); self.pw_edits.append((e, tok)); f.addRow(f"비밀번호 {n + 1}", e)
        self.pw_auto = QCheckBox("문서의 잠금을 자동으로 해제하려면 항상 위의 비밀번호를 사용하십시오")
        self.pw_auto.setChecked(self.prefs.get("preset_pw_auto", False)); f.addRow(self.pw_auto)
        return w

    def _save_password(self):
        out = []
        for e, tok in self.pw_edits:
            if e.untouched:
                out.append(tok)
            elif e.text().strip():
                out.append(protect(e.text().strip()))
        self.prefs["preset_pw"] = [t for t in out if t]   # 빈 칸 없이 채운 것만
        self.prefs["preset_pw_auto"] = self.pw_auto.isChecked()

    # ── 빈 PDF ───────────────────────────────────────────────────
    def _build_blank(self) -> QWidget:
        b = {**BLANK, **(self.prefs.get("blank") or {})}
        unit = self.prefs.get("unit", "cm")
        unit = unit if unit in UNIT_PT else "cm"
        w = QWidget(); f = QFormLayout(w)
        self.blank_name = QLineEdit(b["name"]); f.addRow("파일 이름", self.blank_name)
        self.blank_count = QSpinBox(); self.blank_count.setRange(1, 1000); self.blank_count.setValue(int(b["count"])); f.addRow("페이지", self.blank_count)
        self.blank_size = QComboBox()
        for name, wmm, hmm in BLANK_SIZES_MM:
            self.blank_size.addItem(f"{name} {wmm:g} x {hmm:g} mm", name)
        self.blank_size.addItem("사용자 지정", "custom")
        self.blank_size.setCurrentIndex(max(0, self.blank_size.findData(b["size"])))
        f.addRow("크기", self.blank_size)
        self.blank_w = UnitSpin(b["w"], unit, lo_pt=1.0, hi=20000); f.addRow("너비", self.blank_w)
        self.blank_h = UnitSpin(b["h"], unit, lo_pt=1.0, hi=20000); f.addRow("높이", self.blank_h)
        orient = QHBoxLayout(); self.blank_orient = QButtonGroup(self)
        for n, text in enumerate(("세로", "가로")):
            r = QRadioButton(text); self.blank_orient.addButton(r, n); orient.addWidget(r)
        self.blank_orient.button(1 if b["landscape"] else 0).setChecked(True); orient.addStretch(1)
        f.addRow("방향", orient)
        self.blank_size.currentIndexChanged.connect(self._blank_size_changed); self._blank_size_changed()
        return w

    def _blank_size_changed(self):
        custom = self.blank_size.currentData() == "custom"
        self.blank_w.setEnabled(custom); self.blank_h.setEnabled(custom)
        for name, wmm, hmm in BLANK_SIZES_MM:
            if name == self.blank_size.currentData():
                self.blank_w.set_pt(wmm * MM); self.blank_h.set_pt(hmm * MM)

    def _save_blank(self):
        self.prefs["blank"] = {"name": blank_name(self.blank_name.text()), "count": self.blank_count.value(),
                               "size": self.blank_size.currentData(), "w": self.blank_w.pt(), "h": self.blank_h.pt(),
                               "landscape": self.blank_orient.checkedId() == 1}

    # ── 단축키 ───────────────────────────────────────────────────
    def _build_shortcut(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel("단축키 리스트"))
        self.keys = K.ShortcutEditor(self.settings.get("hotkeys")); v.addWidget(self.keys, 1)
        return w

    def _save_shortcut(self):
        self.settings["hotkeys"] = self.keys.values()

    # ── 적용 ─────────────────────────────────────────────────────
    def accept(self):
        keys = [k for k, _ in PAGES]
        for key in self.built:
            check = getattr(self, f"_check_{key}", None)
            if check and not check():
                self.show_page(keys.index(key)); return
        for key in self.built:
            getattr(self, f"_save_{key}")()
        super().accept()
