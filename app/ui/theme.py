"""색·여백·글꼴 한 곳. 흰 바탕·옅은 회색 띠·빨간 강조.

화살표(⌄)·체크 표시는 Qt 기본 그림(테마마다 모양이 달라 하늘색 네모가 섞임) 대신 여기서 같은 굵기로 그려 쓴다.
"""
from __future__ import annotations

import os
import tempfile

ACCENT = "#E8383D"        # 강조 빨강: 색 면(탭 밑줄·켜진 도구·체크 바탕·슬라이더)에만
ACCENT_DARK = "#C9272C"   # 진한 빨강: 빨간 바탕에 흰 글자(기본 단추·입력칸 고른 글자), 라이트 테마의 빨강 글자
BG = "#FFFFFF"
BAR = "#F4F5F7"           # 리본 단추 줄·상태줄·세로 막대 바탕
BAR_LINE = "#E3E3E6"
CANVAS = "#EDEEF0"        # 문서 뒤 회색
TEXT = "#1B1C1F"
TEXT_DIM = "#63656B"      # 상태줄·제목줄 바탕에서도 글자 4.5:1 (11-09, 옛 #6F7177)
HOVER = "#F0F0F2"
PRESSED = "#E6E6E9"
SELECT_BG = "#FDECEC"
FONT = "Malgun Gothic"
FONT_PT = 9.5           # 맑은 고딕 9pt 는 획이 가늘어 흐려 보였다. 굵기는 400·700 두 가지뿐이라 제목은 700(bold)

# 11-09 대비 결정: 빨강 글자는 ACCENT_TEXT(라이트 #C9272C · 다크 #FF6B6F), 빨간 바탕 단추는 PRIMARY(#C9272C),
# 키보드 초점 테두리는 FOCUS(진한 파랑, 다크는 어두운 바탕에서 보이게 밝힌 파랑). 빨강은 초점에 쓰지 않는다.
# FIELD_LINE·SCROLL 은 칸 경계·손잡이가 바탕과 3:1 이 되게, TEXT_OFF 는 꺼진 단추 글자(TEXT_DIM 보다 흐리게).
LIGHT = {"BG": BG, "BAR": BAR, "BAR_LINE": BAR_LINE, "CANVAS": CANVAS, "TEXT": TEXT, "TEXT_DIM": TEXT_DIM,
         "TEXT_OFF": "#A9ABB0", "HOVER": HOVER, "PRESSED": PRESSED, "SELECT_BG": SELECT_BG, "FIELD": "#FFFFFF",
         "FIELD_LINE": "#8A8C92", "SCROLL": "#8A8C92", "SCROLL_HOVER": "#6A6C72", "WARN_BG": "#FDEBEC", "WARN_LINE": "#F5C9CB",
         "ACCENT_TEXT": ACCENT_DARK, "WARN_TEXT": ACCENT_DARK, "PRIMARY": ACCENT_DARK, "PRIMARY_HOVER": "#A81E23",
         "FOCUS": "#1A5FB4",
         # 층 나누기: 맨 위 탭 줄은 진하게, 패널 바탕은 옅은 회색, 그 위에 흰 카드
         "TITLEBAR": "#E9EAED", "PANEL": "#F6F7F9", "CARD": "#FFFFFF", "CARD_LINE": "#E3E4E8", "SHADE": "rgba(0,0,0,0.07)"}
DARK = {"BG": "#1F2023", "BAR": "#26272B", "BAR_LINE": "#35363B", "CANVAS": "#141517", "TEXT": "#EDEDEF",
        "TEXT_DIM": "#A3A5AB", "TEXT_OFF": "#5F6167", "HOVER": "#2E2F33", "PRESSED": "#393A3F", "SELECT_BG": "#3A2426",
        "FIELD": "#2A2B2F", "FIELD_LINE": "#76787E", "SCROLL": "#76787E", "SCROLL_HOVER": "#9A9CA2",
        "WARN_BG": "#3A2426", "WARN_LINE": "#5A3033",
        "ACCENT_TEXT": "#FF6B6F", "WARN_TEXT": "#FF8A8E", "PRIMARY": ACCENT_DARK, "PRIMARY_HOVER": "#A81E23",
        "FOCUS": "#5E9EF2",
        "TITLEBAR": "#17181A", "PANEL": "#1B1C1F", "CARD": "#25262A", "CARD_LINE": "#34353A", "SHADE": "rgba(0,0,0,0.35)"}

CUR: dict = dict(LIGHT)  # 지금 테마. 아이콘이 그릴 때 여기 색을 쓴다(qss() 가 바꿈)

# 스킨: 시스템 테마(윈도우 앱 모드를 따라감) · 라이트 · 다크. prefs["theme"], 옛 prefs["dark"] 는 옮긴다.
SKINS = (("system", "시스템 테마"), ("light", "라이트"), ("dark", "다크"))
_system_dark: bool | None = None   # 윈도우가 알려 준 앱 모드(바뀜 신호로 받은 값). 없으면 그때 물어본다


def skin(prefs: dict) -> str:
    """지금 스킨 이름. 옛 설정(dark: true/false)이 남아 있으면 theme 열쇠로 옮긴다."""
    if "dark" in prefs:
        old = prefs.pop("dark")
        prefs.setdefault("theme", "dark" if old else "light")
    t = prefs.get("theme", "light")
    return t if t in dict(SKINS) else "light"


def set_system_scheme(scheme) -> None:
    """윈도우 앱 모드가 바뀌었다(QStyleHints.colorSchemeChanged)."""
    from PySide6.QtCore import Qt
    global _system_dark
    _system_dark = scheme == Qt.ColorScheme.Dark


def system_dark() -> bool:
    if _system_dark is not None:
        return _system_dark
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001
        return False


def is_dark(prefs: dict) -> bool:
    s = skin(prefs)
    return system_dark() if s == "system" else s == "dark"


def _images(c: dict, dark: bool) -> dict[str, str]:
    """화살표·체크 그림을 1배·2배로 그려 임시 폴더에 둔다. QSS 는 url 의 @2x 를 고해상도 화면에서 알아서 쓴다."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

    folder = os.path.join(tempfile.gettempdir(), "pdfeditor_qss")
    os.makedirs(folder, exist_ok=True)
    shapes = {  # 이름: (가로, 세로, 선 색, 점들[0~1], 굵기)
        "down": (10, 6, c["TEXT_DIM"], [(0.1, 0.15), (0.5, 0.85), (0.9, 0.15)], 1.3),
        "up": (10, 6, c["TEXT_DIM"], [(0.1, 0.85), (0.5, 0.15), (0.9, 0.85)], 1.3),
        "check": (10, 8, "#FFFFFF", [(0.08, 0.5), (0.38, 0.85), (0.92, 0.12)], 1.6),
        "dot": (8, 8, "#FFFFFF", None, 0),
        # 문서 탭 닫기: 평소엔 흐린 선 ×, 마우스를 올리거나 고른 탭이면 본문색 ×
        "close": (10, 10, c["TEXT_DIM"], [(0.15, 0.15), (0.85, 0.85), None, (0.85, 0.15), (0.15, 0.85)], 1.3),
        "close_on": (10, 10, c["TEXT"], [(0.15, 0.15), (0.85, 0.85), None, (0.85, 0.15), (0.15, 0.85)], 1.4),
    }
    out = {}
    for name, (w, h, col, pts, lw) in shapes.items():
        base = os.path.join(folder, f"{'dark' if dark else 'light'}_{name}")
        for scale, suffix in ((1, ""), (2, "@2x")):
            pm = QPixmap(w * scale, h * scale)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if pts:
                p.setPen(QPen(QColor(col), lw * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                path = QPainterPath(QPointF(pts[0][0] * w * scale, pts[0][1] * h * scale))
                jump = False
                for pt in pts[1:]:
                    if pt is None:   # 선 끊기(× 의 두 획)
                        jump = True; continue
                    x, y = pt
                    if jump:
                        path.moveTo(x * w * scale, y * h * scale); jump = False
                    else:
                        path.lineTo(x * w * scale, y * h * scale)
                p.drawPath(path)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(w * scale / 2, h * scale / 2), w * scale * 0.22, h * scale * 0.22)
            p.end()
            pm.save(base + suffix + ".png")
        out[name] = (base + ".png").replace("\\", "/")
    return out


def colors(dark: bool = False) -> dict:
    """테마 색 한 벌(밝음·어두움 공통 열쇠 + 강조색). 대비 시험도 이것을 잰다."""
    c = dict(DARK if dark else LIGHT)
    c.update(ACCENT=ACCENT, ACCENT_DARK=ACCENT_DARK, FONT=FONT)
    return c


def _apply_palette(c: dict) -> None:
    """QSS 가 닿지 않는 그림(이름 없는 단추의 기본 바탕, 자리표시 글자)도 테마 색을 쓰게 앱 팔레트를 맞춘다."""
    try:
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        pal = QPalette(app.palette())
        R = QPalette.ColorRole
        for role, key in ((R.Window, "BG"), (R.Base, "FIELD"), (R.AlternateBase, "PANEL"), (R.Button, "FIELD"),
                          (R.WindowText, "TEXT"), (R.Text, "TEXT"), (R.ButtonText, "TEXT"), (R.ToolTipBase, "BG"),
                          (R.ToolTipText, "TEXT"), (R.PlaceholderText, "TEXT_DIM"), (R.Highlight, "PRIMARY"),
                          (R.Link, "ACCENT_TEXT"), (R.Mid, "FIELD_LINE"), (R.Dark, "FIELD_LINE")):
            pal.setColor(role, QColor(c[key]))
        pal.setColor(R.HighlightedText, QColor("#FFFFFF"))
        for role in (R.WindowText, R.Text, R.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["TEXT_OFF"]))
        app.setPalette(pal)
    except Exception:  # noqa: BLE001  (팔레트를 못 바꿔도 QSS 는 그대로 쓴다)
        pass


def qss(dark: bool = False) -> str:
    c = colors(dark)
    CUR.clear(); CUR.update(c)
    _apply_palette(c)
    try:
        img = _images(c, dark)
    except Exception:  # noqa: BLE001  (그림을 못 만들면 Qt 기본 화살표로)
        img = {}
    down, up, check, dot, xdim, xon = (f'url("{img[k]}")' if k in img else "none"
                                       for k in ("down", "up", "check", "dot", "close", "close_on"))
    F = c["FOCUS"]
    return f"""
    * {{ font-family: "{c['FONT']}"; font-size: {FONT_PT}pt; color: {c['TEXT']}; }}
    QMainWindow, QDialog, QWidget#central {{ background: {c['BG']}; }}
    QWidget#titlebar {{ background: {c['TITLEBAR']}; border-bottom: 1px solid {c['BAR_LINE']}; }}
    QWidget#ribbon {{ background: {c['BG']}; }}
    QStackedWidget#ribbonrow {{ background: {c['BAR']}; border-top: 1px solid {c['BAR_LINE']}; border-bottom: 1px solid {c['BAR_LINE']}; }}
    QWidget#ribbonshadow {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {c['SHADE']}, stop:1 rgba(0,0,0,0)); }}
    QWidget#ribbonbar {{ background: {c['BG']}; }}
    QWidget#statusbar {{ background: {c['BAR']}; border-top: 1px solid {c['BAR_LINE']}; }}
    QWidget#statusbar QLabel {{ color: {c['TEXT_DIM']}; }}
    QWidget#navstrip, QWidget#rightstrip {{ background: {c['BAR']}; }}
    QWidget#navstrip {{ border-right: 1px solid {c['BAR_LINE']}; }}
    QWidget#rightstrip {{ border-left: 1px solid {c['BAR_LINE']}; }}
    QWidget#sidepanel {{ background: {c['PANEL']}; border-right: 1px solid {c['BAR_LINE']}; }}
    QWidget#sidepanel QListWidget, QWidget#sidepanel QTreeWidget, QWidget#sidepanel QHeaderView,
    QWidget#sidepanel QHeaderView::section {{ background: {c['PANEL']}; }}
    QWidget#proppanel {{ background: {c['PANEL']}; border-left: 1px solid {c['BAR_LINE']}; }}
    QFrame#group {{ background: {c['CARD']}; border: 1px solid {c['CARD_LINE']}; border-radius: 8px; }}
    QFrame#group QLabel {{ background: transparent; }}
    QLabel#grouptitle {{ font-weight: bold; color: {c['TEXT']}; padding: 0; }}
    /* 인쇄 창: 설정 두 칸 · 회색 미리보기 · 아래 단추 줄 */
    QWidget#printcol {{ background: {c['PANEL']}; border-right: 1px solid {c['BAR_LINE']}; }}
    QLabel#printhead {{ font-weight: bold; }}
    QWidget#printpreview {{ background: {c['CANVAS']}; }}
    QLabel#printfile {{ font-size: 8pt; }}
    QFrame#printnav {{ background: {c['BG']}; border: 1px solid {c['BAR_LINE']}; border-radius: 4px; }}
    QDialog#printdlg QLineEdit#printpos {{ border: none; padding: 0; background: transparent; min-height: 0; }}
    QWidget#printbottom {{ background: {c['PANEL']}; border-top: 1px solid {c['BAR_LINE']}; }}
    QDialog#printdlg QComboBox, QDialog#printdlg QSpinBox, QDialog#printdlg QDoubleSpinBox, QDialog#printdlg QLineEdit {{
        min-height: 16px; padding-top: 2px; padding-bottom: 2px; }}
    QDialog#printdlg QComboBox:disabled, QDialog#printdlg QSpinBox:disabled, QDialog#printdlg QDoubleSpinBox:disabled,
    QDialog#printdlg QLineEdit:disabled {{ background: {c['HOVER']}; border-color: {c['BAR_LINE']}; }}
    QToolButton#printstep {{ border: 2px solid transparent; border-radius: 3px; background: transparent; color: {c['TEXT_DIM']};
        font-size: 12pt; padding: 0; }}
    QToolButton#printstep:hover {{ color: {c['ACCENT_TEXT']}; }}
    QToolButton#printstep:disabled {{ color: {c['TEXT_OFF']}; }}
    QPushButton#printlink {{ border: 2px solid transparent; border-radius: 3px; background: transparent; color: {c['ACCENT_TEXT']};
        padding: 0 2px; min-height: 0; }}
    QPushButton#printlink:hover {{ color: {c['ACCENT_TEXT']}; text-decoration: underline; background: transparent; }}
    QWidget#printtabs {{ border-bottom: 1px solid {c['BAR_LINE']}; }}
    QToolButton#printtab {{ border: 2px solid transparent; border-top-width: 0; padding: 4px 4px; background: transparent;
        font-weight: bold; }}
    QToolButton#printtab:hover {{ color: {c['ACCENT_TEXT']}; }}
    QToolButton#printtab:checked {{ color: {c['ACCENT_TEXT']}; border-bottom-color: {c['ACCENT']}; }}
    QToolButton#printtab:disabled {{ color: {c['TEXT_OFF']}; }}
    QToolButton#orient {{ border: 1px solid transparent; border-radius: 3px; background: transparent; }}
    QToolButton#orient:hover {{ background: {c['HOVER']}; }}
    QToolButton#orient:checked {{ background: {c['PRESSED']}; border-color: {c['FIELD_LINE']}; }}
    QToolButton#printprops {{ border: 1px solid {c['FIELD_LINE']}; border-radius: 11px; background: {c['FIELD']};
        min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; padding: 0; }}
    QToolButton#printprops:hover {{ border-color: {c['ACCENT']}; }}

    /* 패널 머리(썸네일·북마크·주석…): 높이·글자·밑줄을 한 모양으로 */
    QWidget#panelhead {{ background: {c['BG']}; border-bottom: 1px solid {c['BAR_LINE']}; }}
    QWidget#panelshadow {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {c['SHADE']}, stop:1 rgba(0,0,0,0)); }}
    QLabel#paneltitle {{ font-weight: bold; padding: 0; }}
    QLabel#section {{ color: {c['TEXT_DIM']}; font-weight: bold; padding: 6px 0 2px 0; }}

    /* 리본 탭: 고른 탭은 굵게. 폭은 ribbon.py 가 굵은 글자 폭으로 못박아 줄이 출렁이지 않는다.
       초점 테두리 자리(2px)를 늘 비워 두어 초점이 와도 글자가 움직이지 않는다 */
    QToolButton#ribbontab {{ background: transparent; border: 2px solid transparent; border-radius: 3px;
        padding: 4px 9px 4px 9px; color: {c['TEXT']}; }}
    QToolButton#ribbontab:hover {{ color: {c['ACCENT_TEXT']}; }}
    QToolButton#ribbontab:checked {{ color: {c['ACCENT_TEXT']}; font-weight: bold; border-bottom-color: {c['ACCENT']};
        border-radius: 0; }}
    QToolButton#ribbontab:disabled {{ color: {c['TEXT_OFF']}; }}
    QToolButton#filetab {{ background: transparent; border: 2px solid transparent; border-radius: 3px;
        padding: 4px 9px 2px 8px; font-weight: bold; }}
    QToolButton#filetab:hover {{ color: {c['ACCENT_TEXT']}; }}

    /* 리본 단추 */
    QToolButton#rb {{ background: transparent; border: 2px solid transparent; border-radius: 4px; padding: 2px 5px; }}
    QToolButton#rb:hover {{ background: {c['HOVER']}; }}
    QToolButton#rb:pressed {{ background: {c['PRESSED']}; }}
    QToolButton#rb:checked {{ background: {c['SELECT_BG']}; color: {c['ACCENT_TEXT']}; }}
    QToolButton#rb:disabled {{ color: {c['TEXT_OFF']}; }}
    QToolButton#rb[drop="true"] {{ padding-right: 16px; }}
    QToolButton#rb::menu-indicator {{ image: {down}; subcontrol-origin: padding; subcontrol-position: right center;
        right: 4px; width: 10px; height: 6px; }}
    QToolButton#rb[split="true"] {{ padding-right: 18px; }}
    QToolButton#rb::menu-button {{ border: none; background: transparent; width: 16px;
        border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
    QToolButton#rb::menu-button:hover {{ background: {c['PRESSED']}; }}
    QToolButton#rb::menu-arrow {{ image: {down}; width: 10px; height: 6px; }}
    QToolButton#quick {{ background: transparent; border: 2px solid transparent; padding: 2px; border-radius: 4px; }}
    QPushButton#panelclose, QToolButton#stripbtn {{ background: transparent; border: 2px solid transparent; border-radius: 4px; padding: 0; }}
    QPushButton#panelclose:hover, QToolButton#stripbtn:hover {{ background: {c['HOVER']}; }}
    QToolButton#paneltitlebtn {{ background: transparent; border: 2px solid transparent; font-weight: bold; padding: 2px 4px; border-radius: 4px; }}
    QToolButton#paneltitlebtn:hover {{ background: {c['HOVER']}; }}
    QWidget#leftfold, QWidget#rightfold {{ background: {c['BAR']}; }}
    QWidget#leftfold {{ border-right: 1px solid {c['BAR_LINE']}; }}
    QWidget#rightfold {{ border-left: 1px solid {c['BAR_LINE']}; }}
    QWidget#statushot {{ background: {c['BAR_LINE']}; }}
    QToolButton#quick:hover {{ background: {c['HOVER']}; }}
    QToolButton#quick:pressed {{ background: {c['PRESSED']}; }}
    QToolButton#quick:checked {{ background: {c['SELECT_BG']}; }}
    QToolButton#quick:disabled {{ color: {c['TEXT_OFF']}; }}
    QToolButton#quick::menu-indicator {{ image: none; width: 0; }}
    QFrame#vsep {{ color: {c['BAR_LINE']}; margin: 0 4px; }}

    /* 왼쪽·오른쪽 세로 막대 */
    QToolButton#nav {{ background: transparent; border: 2px solid transparent; padding: 6px 4px 6px 6px; }}
    QToolButton#nav:hover {{ background: {c['HOVER']}; }}
    QToolButton#nav:checked {{ background: {c['BG']}; border-left-color: {c['ACCENT']}; }}

    /* 서식 단추(#fmt)와 카드 안 이름 없는 켜고 끄는 단추(굵게·기울임·밑줄·정렬 등): 칸처럼 테두리, 켜지면 옅은 빨강 */
    QToolButton#fmt, QFrame#group QToolButton[checkable="true"] {{ background: {c['FIELD']}; color: {c['TEXT']}; border: 1px solid {c['FIELD_LINE']};
        border-radius: 4px; padding: 3px 6px; }}
    QToolButton#fmt:hover, QFrame#group QToolButton[checkable="true"]:hover {{ background: {c['HOVER']}; }}
    QToolButton#fmt:checked, QFrame#group QToolButton[checkable="true"]:checked {{ background: {c['SELECT_BG']}; color: {c['ACCENT_TEXT']};
        border-color: {c['ACCENT']}; }}
    QToolButton#fmt:disabled, QFrame#group QToolButton[checkable="true"]:disabled {{ color: {c['TEXT_OFF']}; }}

    /* 문서 탭 */
    QTabBar#doctabs::tab {{ background: transparent; border: 2px solid transparent;
        padding: 6px 8px 4px 10px; min-width: 120px; max-width: 220px; color: {c['TEXT_DIM']}; }}
    QTabBar#doctabs::tab:selected {{ background: {c['BG']}; border-top-color: {c['ACCENT']}; color: {c['TEXT']}; font-weight: bold; }}
    QTabBar#doctabs::tab:hover:!selected {{ background: {c['HOVER']}; }}
    QTabBar#doctabs::tab:focus {{ border-color: {F}; }}
    QTabBar#doctabs::close-button {{ subcontrol-position: right; image: {xdim}; border: none; border-radius: 3px;
        background: transparent; width: 14px; height: 14px; }}
    QTabBar#doctabs::close-button:selected {{ image: {xon}; }}
    QTabBar#doctabs::close-button:hover {{ image: {xon}; background: {c['PRESSED']}; }}

    /* 단추·입력칸. 기본 단추는 진한 빨강 바탕(흰 글자 5.5:1) */
    QPushButton {{ background: {c['FIELD']}; border: 1px solid {c['FIELD_LINE']}; border-radius: 4px; padding: 4px 12px;
        min-height: 16px; }}
    QPushButton:hover {{ background: {c['HOVER']}; }}
    QPushButton:pressed {{ background: {c['PRESSED']}; }}
    QPushButton:default, QPushButton#accent {{ background: {c['PRIMARY']}; color: white; border-color: {c['PRIMARY']}; font-weight: bold; }}
    QPushButton:default:hover, QPushButton#accent:hover, QPushButton:default:pressed, QPushButton#accent:pressed {{
        background: {c['PRIMARY_HOVER']}; border-color: {c['PRIMARY_HOVER']}; }}
    QPushButton:disabled {{ color: {c['TEXT_OFF']}; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
        background: {c['FIELD']}; border: 1px solid {c['FIELD_LINE']}; border-radius: 4px; padding: 3px 6px;
        min-height: 18px; selection-background-color: {c['PRIMARY']}; selection-color: white;
        placeholder-text-color: {c['TEXT_DIM']}; }}
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {c['SCROLL_HOVER']}; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {c['TEXT_OFF']}; }}
    QComboBox {{ padding-right: 18px; }}
    QComboBox QLineEdit {{ border: none; padding: 0; margin: 0; background: transparent; min-height: 0; }}
    QComboBox::drop-down {{ border: none; width: 18px; subcontrol-origin: padding; subcontrol-position: right center; }}
    QComboBox::down-arrow {{ image: {down}; width: 10px; height: 6px; }}
    QComboBox QAbstractItemView {{ background: {c['BG']}; border: 1px solid {c['BAR_LINE']}; padding: 2px;
        selection-background-color: {c['HOVER']}; selection-color: {c['TEXT']}; outline: none; }}
    QAbstractSpinBox {{ padding-right: 18px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 16px; }}
    QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin-top: 2px; }}
    QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 2px; }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {c['HOVER']}; border-radius: 3px; }}
    QAbstractSpinBox::up-arrow {{ image: {up}; width: 8px; height: 5px; }}
    QAbstractSpinBox::down-arrow {{ image: {down}; width: 8px; height: 5px; }}
    QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator, QListView::indicator, QTreeView::indicator {{
        width: 14px; height: 14px; border: 1px solid {c['FIELD_LINE']}; background: {c['FIELD']}; }}
    QCheckBox::indicator, QListView::indicator, QTreeView::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c['ACCENT']}; }}
    QCheckBox::indicator:checked, QListView::indicator:checked, QTreeView::indicator:checked {{
        background: {c['ACCENT']}; border-color: {c['ACCENT']}; image: {check}; }}
    QRadioButton::indicator:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT']}; image: {dot}; }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {c['HOVER']}; border-color: {c['BAR_LINE']}; }}
    QCheckBox::indicator:checked:disabled {{ background: {c['TEXT_OFF']}; border-color: {c['TEXT_OFF']}; image: {check}; }}
    QRadioButton::indicator:checked:disabled {{ background: {c['TEXT_OFF']}; border-color: {c['TEXT_OFF']}; image: {dot}; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {c['TEXT_OFF']}; }}

    /* 목록 */
    QListWidget, QTreeWidget, QTreeView, QListView, QTableWidget {{ background: {c['BG']}; border: 1px solid transparent;
        selection-background-color: {c['SELECT_BG']}; selection-color: {c['TEXT']}; outline: none; }}
    QListWidget::item, QTreeWidget::item {{ padding: 3px 2px; border-left: 2px solid transparent; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background: {c['SELECT_BG']}; color: {c['TEXT']};
        border-left-color: {c['ACCENT']}; }}
    QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected {{ background: {c['HOVER']}; }}
    QHeaderView {{ background: {c['BG']}; border: none; }}
    QHeaderView::section {{ background: {c['BG']}; color: {c['TEXT_DIM']}; border: none;
        border-bottom: 1px solid {c['BAR_LINE']}; padding: 4px 6px; }}

    /* 문서 보기 둘레: 두 테마 모두 옅은 선(다크에서 밝은 선이 튀던 것) */
    QGraphicsView {{ border: 1px solid {c['BAR_LINE']}; }}

    /* 스크롤바: 가늘게, 바탕 없이 */
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['SCROLL']}; border-radius: 2px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['SCROLL_HOVER']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['SCROLL']}; border-radius: 2px; min-width: 30px; }}
    QScrollBar::handle:horizontal:hover {{ background: {c['SCROLL_HOVER']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QAbstractScrollArea::corner {{ background: transparent; }}

    /* 메뉴 */
    QMenu {{ background: {c['BG']}; border: 1px solid {c['BAR_LINE']}; border-radius: 6px; padding: 4px; }}
    QMenu::item {{ padding: 5px 24px 5px 10px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {c['HOVER']}; }}
    QMenu::item:disabled {{ color: {c['TEXT_OFF']}; }}
    QMenu::separator {{ height: 1px; background: {c['BAR_LINE']}; margin: 4px 8px; }}
    QMenu::right-arrow {{ image: none; }}

    /* 그 밖 */
    QSlider::groove:horizontal {{ height: 3px; background: {c['BAR_LINE']}; border-radius: 1px; }}
    QSlider::sub-page:horizontal {{ background: {c['ACCENT']}; border-radius: 1px; }}
    QSlider::handle:horizontal {{ width: 8px; height: 8px; margin: -5px 0; background: {c['ACCENT']}; border-radius: 6px;
        border: 2px solid transparent; }}
    QSlider::handle:horizontal:hover {{ background: {c['ACCENT_DARK']}; }}
    QToolTip {{ background: {c['BG']}; color: {c['TEXT']}; border: 1px solid {c['BAR_LINE']}; padding: 4px 6px; }}
    QGroupBox {{ border: 1px solid {c['BAR_LINE']}; border-radius: 6px; margin-top: 10px; padding: 8px 8px 6px 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['TEXT']}; font-weight: bold; }}
    QTabWidget::pane {{ border: 1px solid {c['BAR_LINE']}; border-radius: 6px; top: -1px; padding: 2px; }}
    QTabWidget QTabBar::tab {{ background: transparent; border: 2px solid transparent; border-top-width: 0;
        padding: 6px 10px 6px 10px; color: {c['TEXT_DIM']}; }}
    QTabWidget QTabBar::tab:selected {{ color: {c['ACCENT_TEXT']}; border-bottom-color: {c['ACCENT']}; }}
    QTabWidget QTabBar::tab:focus {{ border-color: {F}; border-top-width: 0; }}
    QProgressBar {{ border: none; border-radius: 3px; background: {c['BAR_LINE']}; text-align: center; max-height: 6px; }}
    QProgressBar::chunk {{ background: {c['ACCENT']}; border-radius: 3px; }}
    QLabel#dim {{ color: {c['TEXT_DIM']}; }}
    QLabel#warn {{ background: {c['WARN_BG']}; color: {c['WARN_TEXT']}; font-weight: bold; padding: 5px 12px; border-bottom: 1px solid {c['WARN_LINE']}; }}
    QFrame#card {{ background: {c['BG']}; border: 1px solid {c['BAR_LINE']}; border-radius: 6px; }}
    QFrame#card:hover {{ border-color: {c['ACCENT']}; }}

    /* 키보드 초점: 모든 종류에 진한 파랑 테두리(빨강은 오류처럼 보여 쓰지 않음). 테두리 자리는 위에서 미리 비워 둠 */
    QToolButton:focus, QFrame#group QToolButton[checkable="true"]:focus, QToolButton#fmt:focus,
    QToolButton#rb:focus, QToolButton#quick:focus, QToolButton#nav:focus, QToolButton#ribbontab:focus,
    QToolButton#filetab:focus, QToolButton#printtab:focus, QToolButton#printstep:focus, QToolButton#orient:focus,
    QToolButton#printprops:focus, QToolButton#stripbtn:focus, QToolButton#paneltitlebtn:focus,
    QPushButton#panelclose:focus, QPushButton#printlink:focus, QFrame#card:focus {{ border-color: {F}; }}
    QPushButton:focus {{ border: 2px solid {F}; padding: 3px 11px; }}
    QPushButton#panelclose:focus {{ padding: 0; }}
    QPushButton#printlink:focus {{ padding: 0 2px; }}
    QToolButton#ribbontab:checked:focus {{ border-color: {F}; border-bottom-color: {F}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 2px solid {F}; padding: 2px 5px; }}
    QComboBox:focus {{ padding-right: 17px; }}
    QAbstractSpinBox:focus {{ padding-right: 17px; }}
    QDialog#printdlg QComboBox:focus, QDialog#printdlg QSpinBox:focus, QDialog#printdlg QDoubleSpinBox:focus,
    QDialog#printdlg QLineEdit:focus {{ padding-top: 1px; padding-bottom: 1px; }}
    QComboBox QLineEdit:focus, QDialog#printdlg QLineEdit#printpos:focus {{ border: none; padding: 0; }}
    QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border: 2px solid {F}; width: 12px; height: 12px; }}
    QSlider::handle:horizontal:focus {{ border-color: {F}; }}
    QListWidget:focus, QTreeWidget:focus, QTreeView:focus, QListView:focus, QTableWidget:focus,
    QGraphicsView:focus {{ border-color: {F}; }}
    """
