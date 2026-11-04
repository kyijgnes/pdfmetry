"""리본: 윗줄(파일 · 빠른 단추 · 탭 · 검색칸) + 아랫줄(그 탭의 단추들, 가운데 정렬).

단추 정의는 TABS 자료로. kind: action(누르면 한 번) · tool(켜짐 유지, 뷰어 도구) · toggle(켜고 끄기) · menu(메뉴)
· range(구성 탭 쪽 범위 콤보).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QFontMetrics
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu, QSizePolicy,
                               QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..engine import pages as P
from . import icons, theme

# (key, icon, label, kind, menu_items)  menu_items: [(key, label)] 또는 None. label 이 "|" 면 구분선.
# 메뉴 항목 (key, label, [하위 항목]) 은 하위 메뉴, ("|", "|") 는 메뉴 안 구분선.
SEP = ("|",)
MENU_SEP = ("|", "|")


def DECO_MENU(prefix: str) -> list:
    """꾸미기 메뉴 넷: 추가 · 템플릿에서 추가 | 업데이트 · 삭제."""
    return [(prefix, "추가"), (f"{prefix}_template", "템플릿에서 추가"), MENU_SEP,
            (f"{prefix}_update", "업데이트"), (f"{prefix}_remove", "삭제")]

EDIT_TARGETS = (("all", "모두"), ("text", "텍스트"), ("image", "이미지"), ("link", "링크"))
EDIT_MENU = [(f"edit_target_{k}", lbl) for k, lbl in EDIT_TARGETS]

TABS: list[tuple[str, str, list]] = [
    ("home", "홈", [
        ("zoomout", "zoomout", "", "action", None), ("zoomin", "zoomin", "", "action", None), SEP,
        ("hand", "hand", "", "tool", None), ("select", "select", "", "tool", None), SEP,
        ("edit_all", "edit", "모두 편집", "toggle", EDIT_MENU), ("addtext", "addtext", "텍스트 추가", "tool", None), SEP,
        ("ocr", "ocr", "OCR", "action", None), ("crop", "crop", "자르기", "tool", None), SEP,
        ("search", "search", "검색", "action", None),
        ("home_more", "more", "더보기", "menu", [("snapshot", "스크린샷"), ("m_distance", "측정: 거리"), ("print", "인쇄"),
                                                 ("properties", "파일 속성")]),
    ]),
    ("edit", "편집", [
        ("edit_all", "edit", "모두 편집", "toggle", EDIT_MENU), ("addtext", "addtext", "텍스트 추가", "tool", None),
        ("addimage", "addimage", "이미지 추가", "tool", None),
        ("link", "link", "링크 추가", "tool", None), SEP,
        ("watermark", "watermark", "워터마크", "action", DECO_MENU("watermark")),
        ("background", "background", "배경화면", "action", DECO_MENU("background")),
        ("headerfooter", "headerfooter", "머리글·바닥글", "action", DECO_MENU("headerfooter")),
        ("edit_more", "more", "더보기", "menu", [("bates_menu", "베이츠 번호", DECO_MENU("bates")),
                                                 ("pagenumber_menu", "페이지 번호", DECO_MENU("pagenumber")), ("pagelabel", "페이지 레이블"),
                                                 ("replace_image", "이미지 바꾸기(고른 것)"), ("delete_sel", "고른 것 삭제")]),
    ]),
    # 묶음: 글자 표시 │ 그리기 │ 그림·스탬프 │ 글 주석 │ 체크 │ 도구. 더보기 split 의 여섯째 칸은 처음 쓸 도구.
    ("comment", "주석", [
        ("highlight", "highlight", "강조", "tool", None), ("areahl", "areahl", "영역 강조", "tool", None),
        ("underline", "underline", "밑줄", "tool", None), ("strike", "strike", "취소선", "tool", None),
        ("markup_more", "squiggly", "구불구불한 선", "tool", [("underline", "밑줄"), ("strike", "취소선"), ("squiggly", "구불구불한 선"),
                                                         ("caret", "삽입 기호")], "squiggly"),
        ("annot_color", "background", "색", "action", None), SEP,
        ("rect", "rect", "직사각형", "tool", None), ("oval", "oval", "타원", "tool", None), ("arrow", "arrow", "화살표", "tool", None),
        ("shape_more", "line", "선", "tool", [("rect", "직사각형"), ("arrow", "화살표"), ("line", "선"), ("oval", "타원"),
                                             ("polyline", "연결된 선"), ("cloud", "클라우드"), ("polygon", "다각형"), ("triangle", "삼각형"),
                                             ("shape_more2", "모양 더 보기", [("cross", "십자가"), ("bent_arrow", "구부러진 위쪽 화살표"),
                                                                         ("balloon", "말풍선")])], "line"),
        ("note", "note", "노트", "tool", None),
        ("measure", "measure", "거리 측정", "tool", [("m_distance", "거리"), ("m_perimeter", "둘레"), ("m_area", "영역"),
                                                    MENU_SEP, ("m_settings", "측정 설정")]), SEP,
        ("pencil", "pencil", "연필", "tool", None), ("eraser", "eraser", "지우개", "tool", None),
        ("stamp", "stamp", "스탬프", "tool", [("stamp_pick", "스탬프 고르기"), MENU_SEP,
                                            ("stamp_create", "사용자 지정 스탬프 만들기"),
                                            ("stamp_create_dyn", "사용자 지정 동적 스탬프 만들기"),
                                            ("stamp_manage", "스탬프 관리"), ("stamp_identity", "ID 편집")]),
        ("signature", "signature", "서명", "tool", None), SEP,
        ("typewriter", "typewriter", "타자기", "tool", None), ("textbox", "textbox", "텍스트 박스", "tool", None),
        ("callout", "callout", "텍스트 말풍선", "tool", None), ("attach", "attach", "첨부 파일", "tool", None), SEP,
        ("show_annots", "eye", "주석 보이기", "toggle", None), ("keep_tool", "pin", "도구 계속 쓰기", "toggle", None), SEP,
        ("snapshot", "snapshot", "스크린샷", "tool", None),
    ]),
    ("convert", "변환", [
        ("create", "create", "PDF 생성", "menu", [("create_files", "파일에서 생성"), ("create_blank", "빈 PDF"),
                                                 ("create_clipboard", "클립보드에서 가져오기"), ("create_scan", "스캐너에서 생성")]), SEP,
        ("to_hwpx", "hwp", "HWP로 변환", "action", None), ("to_docx", "word", "Word로 변환", "action", None),
        ("to_xlsx", "excel", "Excel로 변환", "action", None), ("to_pptx", "ppt", "PowerPoint로 변환", "action", None),
        ("to_png", "image", "이미지로 변환", "action", None), ("to_txt", "text", "텍스트로 변환", "action", None),
        ("convert_more", "more", "더보기", "menu", [("to_html", "HTML로"), ("to_rtf", "RTF로"), ("to_epub", "EPUB로"), ("to_pdfa", "PDF/A로"),
                                                    ("to_imgpdf", "이미지 기반 PDF로"), ("convert_settings", "설정 변환")]), SEP,
        ("batch", "batch", "일괄 변환", "action", None),
    ]),
    ("view", "보기", [
        ("zoomout", "zoomout", "", "action", None), ("zoomin", "zoomin", "", "action", None), SEP,
        ("zoom_menu", "actual", "확대 방식", "menu", [("zoom_actual", "실제 페이지 크기에 맞추기"), ("zoom_width", "창 너비에 맞추기"),
                                             ("zoom_height", "창 높이에 맞추기"), ("zoom_page", "창 크기에 맞추기"), MENU_SEP,
                                             ("fullscreen", "전체 화면"), ("zoom_area", "영역 확대")]),
        ("layout_menu", "continuous", "보기 방식", "menu", [("layout_single", "한 페이지씩 보기"), ("layout_continuous", "스크롤 활성화 상태로 페이지 보기"),
                                                   ("layout_facing", "양 옆으로 두페이지 보기"), ("layout_facing_continuous", "스크롤 활성화 상태로 양 옆으로 보기"),
                                                   MENU_SEP, ("layout_cover", "표지 따로 보기")]),
        ("canvas_menu", "background", "배경", "menu", [("canvas_default", "기본"), ("canvas_day", "낮"), ("canvas_eye", "눈 보호"),
                                                   ("canvas_night", "밤"), ("canvas_parchment", "양피지")]), SEP,
        ("read_mode", "read", "읽기 모드", "toggle", None),
        ("rotate_view", "rotate", "보기 회전", "menu", [("rotate_view_cw", "시계 방향"),
                                                ("rotate_view_ccw", "반시계 방향")]), SEP,
        ("auto_bookmark", "bookmark", "자동 북마크", "action", None),
        ("compare", "compare", "비교", "action", None), ("snapshot_tool", "snapshot", "캡처", "action", None),
        ("grid", "grid", "눈금자 & 격자", "menu", [("toggle_grid", "격자"), ("toggle_ruler", "눈금자")]),
        ("tile", "tile", "타일", "menu", [("tile_h", "가로 바둑판식 배열"), ("tile_v", "세로 바둑판식 배열")]), SEP,
        ("skin", "dark", "스킨", "menu", [("skin_system", "시스템 테마"), ("skin_light", "라이트"), ("skin_dark", "다크")]),
    ]),
    ("page", "구성", [
        ("thumb_out", "zoomout", "", "action", None), ("thumb_in", "zoomin", "", "action", None), SEP,
        ("rotate_left", "rotateleft", "", "action", None), ("rotate_right", "rotateright", "", "action", None),
        ("delete_pages", "delete", "", "action", None), ("page_range", "", "", "range", None), SEP,
        ("extract", "extract", "추출", "action", None),
        ("split", "split", "분할", "action", None),
        ("insert", "insert", "삽입", "menu", [("insert_blank", "빈 페이지"), ("insert_pdf", "PDF에서"), ("insert_images", "이미지에서")]), SEP,
        ("crop_pages", "crop", "자르기", "action", None), ("rotate_pages", "rotate", "회전", "action", None),
        ("resize", "resize", "크기", "action", None),
        ("page_more", "more", "더보기", "menu", [("split_page", "페이지 분할(칸으로 나누기)"), ("replace", "바꾸기"), ("duplicate", "복제"), ("pagebox", "페이지 박스"),
                                                 ("pagelabel", "페이지 레이블"), ("delete_blank", "빈 페이지 삭제"), ("reverse", "순서 뒤집기")]),
    ]),
    ("tools", "도구", [
        ("ocr", "ocr", "OCR", "action", None), ("ocrarea", "ocrarea", "영역 OCR", "tool", None), SEP,
        ("merge", "merge", "PDF 병합", "action", None), ("compare", "compare", "PDF 비교", "action", None), SEP,
        ("compress", "compress", "PDF 압축", "action", None), ("flatten", "flatten", "평면화", "action", None), SEP,
        ("batch", "batch", "일괄 편집", "action", None),
        ("tools_more", "more", "더보기", "menu", [("deskew", "기울기 보정"), ("table", "표 인식"), ("snapshot", "스크린샷"),
                                                  ("extract_data", "데이터 추출"), ("print", "인쇄")]),
    ]),
    ("form", "양식", [
        ("form_edit", "edit", "양식 편집", "toggle", None), SEP,
        ("ftext", "ftext", "텍스트 칸", "tool", None), ("fcheck", "fcheck", "체크 박스", "tool", None), ("fradio", "fradio", "라디오 버튼", "tool", None),
        ("fcombo", "fcombo", "콤보 박스", "tool", None), ("flist", "flist", "목록 박스", "tool", None), ("fbutton", "fbutton", "누름 버튼", "tool", None),
        ("fimage", "fimage", "이미지 칸", "tool", None), ("fsign", "fsign", "서명 칸", "tool", None), ("fdate", "fdate", "날짜 칸", "tool", None), SEP,
        ("recognize", "recognize", "양식 인식", "action", None), ("form_export", "export", "데이터 추출", "action", None),
        ("form_more", "more", "더보기", "menu", [("form_import", "데이터 가져오기"), ("form_reset", "기본값으로 되돌리기"),
                                                 ("form_set_default", "지금 값을 기본값으로"), ("form_preview", "미리보기"),
                                                 ("align_left", "정렬: 왼쪽"), ("align_right", "정렬: 오른쪽"), ("align_top", "정렬: 위"),
                                                 ("align_bottom", "정렬: 아래"), ("align_hcenter", "정렬: 가로 가운데"), ("align_vcenter", "정렬: 세로 가운데"),
                                                 ("align_dist_h", "정렬: 가로로 같은 간격"), ("align_dist_v", "정렬: 세로로 같은 간격"),
                                                 ("align_page_both", "페이지 가운데")]),
    ]),
    ("protect", "보안", [
        ("encrypt", "lock", "암호화", "action", None), SEP,
        ("redact", "redact", "교정 표시", "tool", None), ("redact_search", "redactsearch", "검색·교정", "action", None),
        ("redact_apply", "redactapply", "교정 적용", "action", None), ("hidden", "hidden", "숨겨진 데이터 삭제", "action", None), SEP,
        ("digital_sign", "cert", "디지털 서명", "action", None),
        ("sign_manage", "signature", "서명 관리", "menu", [("sign_new", "서명 이미지 등록"), ("sign_apply", "서명 넣기"), ("sign_clear", "등록한 서명 지우기")]),
    ]),
]

LABELS = {k: lbl for _, _, items in TABS for it in items if it != SEP for k, lbl in [(it[0], it[2])]}
LABELS.update({"hand": "손", "select": "선택", "zoomin": "확대", "zoomout": "축소", "pencil": "연필", "eraser": "지우개",
               "highlight": "강조", "underline": "밑줄", "typewriter": "타자기", "note": "노트", "rect": "직사각형", "stamp": "스탬프",
               "signature": "서명", "attach": "첨부 파일", "measure": "측정", "show_annots": "주석 보이기", "zoom_menu": "확대 방식",
               "layout_menu": "보기 방식", "canvas_menu": "배경", "thumb_out": "썸네일 축소", "thumb_in": "썸네일 확대",
               "rotate_left": "왼쪽 회전", "rotate_right": "오른쪽 회전", "delete_pages": "페이지 삭제", "ftext": "텍스트 칸",
               "fcheck": "체크 박스", "fradio": "라디오 버튼", "fcombo": "콤보 박스", "flist": "목록 박스", "fbutton": "누름 버튼",
               "fimage": "이미지", "fsign": "디지털 서명 칸", "fdate": "날짜 칸", "areahl": "영역 강조", "strike": "취소선",
               "squiggly": "구불구불한 선", "caret": "삽입 기호", "textbox": "텍스트 박스", "callout": "텍스트 말풍선", "oval": "타원",
               "line": "선", "arrow": "화살표", "polygon": "다각형", "polyline": "연결된 선", "cloud": "클라우드",
               "m_distance": "거리 측정", "m_perimeter": "둘레 측정", "m_area": "넓이 측정", "crop": "자르기", "link": "링크", "redact": "교정 표시",
               "ocrarea": "영역 OCR", "addtext": "텍스트 추가", "addimage": "이미지 추가", "snapshot": "스크린샷",
               "triangle": "삼각형", "cross": "십자가", "bent_arrow": "구부러진 위쪽 화살표", "balloon": "말풍선",
               "annot_color": "색", "keep_tool": "도구 계속 쓰기"})

# 메뉴 항목 중 뷰어 도구가 되는 것
TOOL_KEYS = {"hand", "select", "addtext", "addimage", "link", "crop", "pencil", "eraser", "highlight", "areahl", "underline",
             "strike", "squiggly", "caret", "typewriter", "textbox", "callout", "note", "rect", "oval", "line", "arrow",
             "polygon", "polyline", "cloud", "triangle", "cross", "bent_arrow", "balloon", "stamp", "signature", "attach",
             "m_distance", "m_perimeter", "m_area",
             "ocrarea", "redact", "snapshot", "ftext", "fcheck", "fradio", "fcombo", "flist", "fbutton", "fimage", "fsign", "fdate"}

# 주석 도구: 주석을 숨기면 못 쓰고, 「도구 계속 쓰기」를 종류마다 기억한다
ANNOT_TOOLS = {"highlight", "areahl", "underline", "strike", "squiggly", "caret", "rect", "oval", "arrow", "line", "polyline",
               "cloud", "polygon", "triangle", "cross", "bent_arrow", "balloon", "note", "m_distance", "m_perimeter", "m_area",
               "pencil", "eraser", "stamp", "signature", "typewriter", "textbox", "callout", "attach"}
KEEP_OFF = {"stamp", "signature", "attach"}   # 처음엔 한 번 쓰고 선택 도구로 돌아가는 것


def keep_tool(settings: dict, key: str) -> bool:
    """그 도구를 쓰고 나서도 계속 켜 둘까(settings.json 의 keep_tool). 지우개는 늘 계속(끌 칸이 없다)."""
    if key == "eraser":
        return True
    return bool((settings.get("keep_tool") or {}).get(key, key not in KEEP_OFF))

# 열 때 지금 상태에 체크를 다는 메뉴(창의 view_checked(항목)이 켜짐을 알려 준다)
CHECK_MENUS = {"zoom_menu", "layout_menu", "canvas_menu", "grid", "skin"}

# 빠른 실행 ▾ 의 단추 켜고 끄기(prefs["qat"]). 「리본」 은 리본 아랫줄 보이기(F8 접기와 같은 것)
QAT_ITEMS = (("save", "저장"), ("print", "인쇄"), ("undo", "실행 취소"), ("redo", "다시 실행"), ("mail", "메일로 보내기"),
             ("ribbon", "리본"))

COMMENT_ANNOT_KEYS = [it[0] for t, _, items in TABS if t == "comment" for it in items
                      if it != SEP and it[0] not in ("show_annots", "snapshot")]


class Ribbon(QWidget):
    action = Signal(str)          # action·menu 항목
    tool = Signal(str)            # 뷰어 도구 바뀜
    quickStyle = Signal(str, object)   # 켜진 도구 단추를 다시 누름: (도구, 단추) → 빠른 스타일
    toggled = Signal(str, bool)   # toggle 단추
    tabChanged = Signal(str)
    searchRequested = Signal(str)
    fileMenu = Signal()
    rangePicked = Signal(str)     # 구성 탭 범위 콤보: all·even·odd 또는 넣은 범위 글
    qatChanged = Signal(str, bool)   # 빠른 실행 ▾ 체크(단추 열쇠, 보임)
    foldChanged = Signal(bool)    # 리본 아랫줄 접힘
    RANGE_ITEMS = (("짝수 페이지", "even"), ("홀수 페이지", "odd"), ("모든 페이지", "all"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ribbon")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        # 윗줄
        top = QWidget(); top.setObjectName("ribbonbar"); tl = QHBoxLayout(top); tl.setContentsMargins(6, 3, 10, 0); tl.setSpacing(2)
        fb = QToolButton(); fb.setObjectName("filetab"); fb.setText("파일"); fb.clicked.connect(self.fileMenu.emit); tl.addWidget(fb)
        self.quick: dict[str, QToolButton] = {}
        for key, name, tip in (("save", "save", "저장"), ("print", "print", "인쇄"), ("undo", "undo", "실행 취소"),
                               ("redo", "redo", "다시 실행"), ("mail", "mail", "메일로 보내기")):
            b = QToolButton(); b.setObjectName("quick"); b.setIcon(icons.icon(name, 16)); b.setToolTip(tip)
            b.clicked.connect(lambda _, k=key: self.action.emit(k)); tl.addWidget(b); self.quick[key] = b
        qm = QToolButton(); qm.setObjectName("quick"); qm.setIcon(icons.icon("down", 14)); qm.setToolTip("빠른 실행 단추")
        menu = QMenu(qm)
        self.qat_acts: dict[str, QAction] = {}
        for key, label in QAT_ITEMS:
            a = menu.addAction(label); a.setCheckable(True); a.setChecked(True)
            a.toggled.connect(lambda on, k=key: self._qat_toggled(k, on)); self.qat_acts[key] = a
        menu.addSeparator()
        for key, label in (("saveas", "다른 이름으로 저장"), ("properties", "파일 속성"), ("preferences", "환경 설정")):
            menu.addAction(label, lambda k=key: self.action.emit(k))
        qm.setMenu(menu); qm.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup); tl.addWidget(qm)
        tl.addStretch(1)
        self.tabbtns: dict[str, QToolButton] = {}
        grp = QButtonGroup(self); grp.setExclusive(True)
        for key, label, _ in TABS:
            b = QToolButton(); b.setObjectName("ribbontab"); b.setText(label); b.setCheckable(True)
            b.clicked.connect(lambda _, k=key: self.set_tab(k)); grp.addButton(b); tl.addWidget(b); self.tabbtns[key] = b
            bf = QFont(theme.FONT); bf.setPointSizeF(theme.FONT_PT); bf.setBold(True)  # 고른 탭이 굵어져도 폭이 그대로이게
            b.setFixedWidth(QFontMetrics(bf).horizontalAdvance(label) + 24)
        tl.addStretch(1)
        self.search = QLineEdit(); self.search.setPlaceholderText("검색 (Ctrl+F)")
        self.search.setMinimumWidth(70); self.search.setMaximumWidth(150)  # 창이 좁으면 검색칸부터 줄인다(화면 반쪽 960px)
        self.search.returnPressed.connect(lambda: self.searchRequested.emit(self.search.text()))
        tl.addWidget(self.search)
        lay.addWidget(top)
        # 아랫줄
        self.stack = QStackedWidget(); self.stack.setObjectName("ribbonrow"); self.stack.setFixedHeight(40)
        self.pages: dict[str, QWidget] = {}
        self.buttons: dict[str, list[QToolButton]] = {}
        self.toolgroup: dict[str, list[QToolButton]] = {}
        for key, _, items in TABS:
            w = self._build(items)
            self.pages[key] = w; self.stack.addWidget(w)
        lay.addWidget(self.stack)
        # 리본 아래 옅은 그림자: 리본과 문서 자리가 한 판으로 붙어 밋밋하던 것을 층으로 나눈다
        shade = QWidget(); shade.setObjectName("ribbonshadow"); shade.setFixedHeight(4)
        shade.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True); lay.addWidget(shade)
        self._widths: dict[str, tuple[int, list[int], list[QToolButton]]] = {}
        self.current_tab = "home"
        self.folded = False
        self.set_edit_target("all")
        self.set_tab("home")

    # 창이 좁으면 단추 이름을 접고(아이콘만) 넓으면 편다. 이름을 늘 다 보이면 창을 1300px 아래로 못 줄였다.
    # 주석 탭처럼 단추가 많으면 다 펴지 못하므로 왼쪽 단추부터 들어가는 만큼만 이름을 편다.
    def _measure(self):
        for key, w in self.pages.items():
            btns = [b for b in w.findChildren(QToolButton) if b.text()]
            btns.sort(key=lambda b: w.layout().indexOf(b))
            lay = w.layout()
            extra = []
            for b in btns:
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly); small = b.sizeHint().width()
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon); extra.append(b.sizeHint().width() - small)
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            lay.invalidate(); compact = lay.minimumSize().width()
            self._widths[key] = (compact, extra, btns)
            w.setMinimumWidth(compact)
        self._fit()

    def _fit(self):
        avail = self.stack.width()
        for compact, extra, btns in self._widths.values():
            room = avail - compact
            for b, x in zip(btns, extra):
                fits = room >= x
                if fits:
                    room -= x
                else:
                    room = -1   # 하나가 못 펴지면 그 뒤는 다 접는다(이름이 띄엄띄엄 보이지 않게)
                style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon if fits else Qt.ToolButtonStyle.ToolButtonIconOnly
                if b.toolButtonStyle() != style:
                    b.setToolButtonStyle(style)

    def sizeHint(self):
        """창 처음 크기를 리본이 정하지 않게(이름을 다 편 주석 탭은 2700px 쯤 된다)."""
        s = super().sizeHint()
        return QSize(min(s.width(), 1360), s.height())

    def showEvent(self, e):
        super().showEvent(e)
        if not self._widths:
            self._measure()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._widths:
            self._fit()

    def _build(self, items) -> QWidget:
        w = QWidget(); hl = QHBoxLayout(w); hl.setContentsMargins(8, 3, 8, 4); hl.setSpacing(2)
        hl.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)  # 최소 폭은 _measure 가 정한다(아이콘만일 때 폭)
        w.setMinimumWidth(1)   # _measure 전에는 이름을 다 편 폭이 창 최소 폭이 되지 않게
        hl.addStretch(1)
        for it in items:
            if it == SEP:
                f = QFrame(); f.setObjectName("vsep"); f.setFrameShape(QFrame.Shape.VLine); f.setFixedHeight(18); hl.addWidget(f)
                continue
            key, icon, label, kind, menu_items, *first = it
            if kind == "range":
                hl.addWidget(self._range_combo())
                continue
            b = QToolButton(); b.setObjectName("rb"); b.setIcon(icons.icon(icon, 16)); b.setIconSize(icons.icon_size(16))
            b.setToolTip(LABELS.get(key, label))
            if label:
                b.setText(label); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setProperty("key", key); b.setProperty("kind", kind)
            if kind in ("tool", "toggle"):
                b.setCheckable(True)
            if menu_items:
                m = QMenu(b)
                self._fill_menu(m, menu_items, b, check=key in CHECK_MENUS)
                if key in CHECK_MENUS:
                    m.aboutToShow.connect(lambda m=m: self._sync_checks(m))
                b.setMenu(m)
                split = kind == "tool" or key == "edit_all"
                b.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup if split else QToolButton.ToolButtonPopupMode.InstantPopup)
                # 화살표 자리를 QSS 가 단추 모양마다 맞춘다(split: 눌러 쓰고 옆 화살표로 고름, drop: 눌러서 메뉴)
                b.setProperty("split" if split else "drop", True)
            if kind == "tool":
                b.setProperty("toolkey", first[0] if first else menu_items[0][0] if menu_items else key)
                b.clicked.connect(lambda checked, btn=b: self._tool_clicked(btn, checked))
            elif kind == "toggle":
                b.clicked.connect(lambda checked, k=key: self.toggled.emit(k, checked))
            elif kind == "action":
                b.clicked.connect(lambda _, k=key: self.action.emit(k))
            self.buttons.setdefault(key, []).append(b)
            hl.addWidget(b)
        hl.addStretch(1)
        return w

    def _range_combo(self) -> QComboBox:
        c = QComboBox(); c.setEditable(True); c.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        c.lineEdit().setPlaceholderText("페이지 번호 입력"); c.setFixedWidth(140)
        for text, how in self.RANGE_ITEMS:
            c.addItem(text, how)
        c.setCurrentIndex(-1); c.setEditText("")
        c.activated.connect(lambda k: self.rangePicked.emit(c.itemData(k)))
        c.lineEdit().returnPressed.connect(self._range_entered)
        self.range_combo = c
        return c

    def _range_entered(self):
        text = self.range_combo.currentText().strip()
        how = next((h for t, h in self.RANGE_ITEMS if t == text), None)
        if how is None:   # 항목 글과 같으면 activated 가 이미 보냈다
            self.rangePicked.emit(text)

    def show_range(self, pages: list[int], total: int, labels: list[str]):
        """썸네일 선택을 콤보에 비춘다: 짝수·홀수·모든 쪽과 같으면 그 항목, 아니면 레이블로 쓴 범위 글."""
        c = self.range_combo
        c.blockSignals(True)
        every = list(range(total))
        k = next((i for i in (2, 0, 1) if pages and pages == P.subset(every, self.RANGE_ITEMS[i][1])), -1)
        c.setCurrentIndex(k)
        if k < 0:
            c.setEditText(P.format_range(pages, labels))
        c.blockSignals(False)

    def _fill_menu(self, m: QMenu, items: list, btn: QToolButton, check: bool = False):
        for it in items:
            if it == MENU_SEP:
                m.addSeparator()
            elif len(it) == 3:
                self._fill_menu(m.addMenu(it[1]), it[2], btn)
            else:
                if it[0].startswith("edit_target_"):
                    a = m.addAction(it[1], lambda k=it[0]: self._menu_pick(k, btn))
                    a.setCheckable(True); a.setData(it[0][len("edit_target_"):])
                else:
                    a = m.addAction(it[1], lambda k=it[0]: self._menu_pick(k, btn))
                    if check:
                        a.setData("check:" + it[0])

    def _sync_checks(self, m: QMenu):
        """메뉴를 열 때 지금 켜진 항목에 체크(맞춤 방식·보기 방식·표지 따로·배경·격자·눈금자)."""
        ask = getattr(self.window(), "view_checked", None)
        for a in m.actions():
            d = a.data()
            if isinstance(d, str) and d.startswith("check:"):
                on = ask(d[6:]) if ask else None
                a.setCheckable(on is not None)
                if on is not None:
                    a.setChecked(bool(on))

    def _menu_pick(self, key: str, btn: QToolButton):
        if key in TOOL_KEYS and btn.property("kind") == "tool":
            btn.setProperty("toolkey", key)
            btn.setToolTip(LABELS.get(key, key))
            if btn.text():
                btn.setText(LABELS.get(key, btn.text()))
            btn.setChecked(True)
            self.tool.emit(key)
        else:
            self.action.emit(key)

    def _tool_clicked(self, btn: QToolButton, checked: bool):
        key = btn.property("toolkey")
        if checked:
            self.tool.emit(key)
        elif key in ANNOT_TOOLS:
            # 켜진 주석 도구를 다시 누르면 끄지 않고 빠른 스타일(끄기는 Esc·손 도구)
            btn.setChecked(True)
            self.quickStyle.emit(key, btn)
        else:
            self.tool.emit("hand")

    # ── 빠른 실행 · 접기 ────────────────────────────────────────
    def _qat_toggled(self, key: str, on: bool):
        if key == "ribbon":
            self.set_folded(not on)
        else:
            self.quick[key].setVisible(on)
        self.qatChanged.emit(key, on)

    def set_qat(self, shown: dict):
        """빠른 실행 단추 보이기(prefs["qat"], 없는 열쇠는 보임). 「리본」 은 set_folded 가 맡는다."""
        for key, a in self.qat_acts.items():
            if key == "ribbon":
                continue
            on = bool(shown.get(key, True))
            a.blockSignals(True); a.setChecked(on); a.blockSignals(False)
            self.quick[key].setVisible(on)

    def set_folded(self, on: bool):
        """리본 아랫줄 접기(F8 · 빠른 실행 「리본」 체크)."""
        on = bool(on)
        self.stack.setVisible(not on)
        a = self.qat_acts["ribbon"]
        a.blockSignals(True); a.setChecked(not on); a.blockSignals(False)
        if on != self.folded:
            self.folded = on
            self.foldChanged.emit(on)

    def set_tab(self, key: str):
        self.current_tab = key
        self.tabbtns[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        self.tabChanged.emit(key)

    def reflect_tool(self, tool: str):
        """뷰어 도구가 바뀌면 단추 켜짐을 맞춘다."""
        for key, btns in self.buttons.items():
            for b in btns:
                if b.property("kind") != "tool":
                    continue
                b.setChecked(b.property("toolkey") == tool or (key == tool))

    def set_toggle(self, key: str, on: bool):
        for b in self.buttons.get(key, []):
            b.blockSignals(True); b.setChecked(on); b.blockSignals(False)

    def set_edit_target(self, target: str):
        """「편집」 분할 단추: 글자를 대상 이름으로, 메뉴 체크를 대상에 맞춘다."""
        name = dict(EDIT_TARGETS).get(target, "모두")
        for b in self.buttons.get("edit_all", []):
            b.setText(f"{name} 편집")
            m = b.menu()
            for a in (m.actions() if m else []):
                a.setChecked(a.data() == target)
        if self._widths:
            self._measure()

    def set_enabled_all(self, on: bool):
        for btns in self.buttons.values():
            for b in btns:
                b.setEnabled(on)
        self.range_combo.setEnabled(on)
        for k, b in self.quick.items():
            if k != "mail":
                b.setEnabled(on)
        self.search.setEnabled(on)

    def set_annot_enabled(self, on: bool):
        """주석을 숨기면 주석 탭의 주석 도구 단추를 끈다(주석 보이기·스크린샷은 그대로)."""
        for key in COMMENT_ANNOT_KEYS:
            for b in self.buttons.get(key, []):
                if b.parentWidget() is self.pages["comment"]:
                    b.setEnabled(on)

    def set_enabled(self, key: str, on: bool):
        for b in self.buttons.get(key, []):
            b.setEnabled(on)
        if key in self.quick:
            self.quick[key].setEnabled(on)
