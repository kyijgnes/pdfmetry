"""디지털 서명 화면: 서명 창 · 서명 모양 · 디지털 인증서(디지털 ID · 신뢰할 수 있는 인증서) · 추가 마법사 ·
인증서 정보 · 서명 속성 · 모든 서명 확인, 그리고 창(window)이 부르는 흐름 함수.

엔진은 `app/engine/dsign.py`. 시험은 `_exec`·`_save_path`·`_open_path` 를 바꿔 끼워 창을 띄우지 않고 돈다.
"""
from __future__ import annotations

import datetime as _dt
import os

import fitz
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QRadioButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ..engine import dsign as G

TITLE = "디지털 서명"
MSG_MULTI = "이 문서에는 서명할 수 있는 서명 칸이 여러 개 있습니다. 서명할 칸을 눌러 시작하세요."
MSG_DRAG = "서명이 보일 자리를 마우스로 끌어 그리세요."
MSG_NO_ID = "사용할 수있는 디지털 인증서를 찾을 수 없습니다. 「새 ID」 로 디지털 ID 를 만들거나 가져오세요."
MSG_SAVE_FAIL = "파일을 저장하지 못했습니다."
MSG_CLEAR_FAIL = "서명 {0}개를 삭제하지 못했습니다. 디지털 인증서가 로컬에서 감지되지 않았기 때문입니다."
MSG_CLEAR_ONE_FAIL = "서명을 삭제하지 못했습니다. 디지털 인증서가 로컬에서 감지되지 않았습니다."
MSG_NO_SIG = "이 문서에는 서명이 없습니다."
MSG_DEL_ID = "이 디지털 ID 를 삭제하시겠습니까?"
MSG_DEL_TRUST = "이 인증서를 삭제하시겠습니까? 그렇게 하면 더 이상 소유자의 신원을 확인할 수 없습니다."
COUNTRIES = (("", "없음"), ("KR", "대한민국"), ("US", "미국"), ("JP", "일본"), ("CN", "중국"), ("GB", "영국"),
             ("DE", "독일"), ("FR", "프랑스"), ("CA", "캐나다"), ("AU", "오스트레일리아"))
NEW_STYLE = "새로운 스타일 만들기"
REFRESH = "ID 목록 새로 고침"


# ── 시험이 바꿔 끼우는 자리 ─────────────────────────────────────────
def _exec(dlg) -> bool:
    return bool(dlg.exec())


def _save_path(win, default: str) -> str:
    p, _ = QFileDialog.getSaveFileName(win, "다른 이름으로 저장", default, "PDF 파일 (*.pdf)")
    return p


def _open_path(win, path: str):
    win.open_path(path)


# ── 설정 ─────────────────────────────────────────────────────────
def _settings(win) -> dict:
    return win.settings


def _prefs(win) -> dict:
    return win.prefs


def _save(win):
    fn = getattr(win, "save_settings_now", None)
    if fn:
        fn()


def ids(win) -> list:
    return _settings(win).setdefault("digital_ids", [])


def trusted(win) -> list:
    return _settings(win).setdefault("trusted_certs", [])


def style(win) -> dict:
    st = dict(G.DEFAULT_STYLE)
    st.update(_prefs(win).get("dsign_style") or {})
    return st


def _fmt(t) -> str:
    if not t:
        return ""
    return t.astimezone().strftime("%Y.%m.%d %H:%M:%S %z")


# ── 인증서 정보 ───────────────────────────────────────────────────
class CertInfoDialog(QDialog):
    def __init__(self, parent, cert):
        super().__init__(parent)
        self.setWindowTitle("인증서 정보")
        info = G.cert_info(cert)
        f = QFormLayout(self)
        f.addRow("인증서 이름", QLabel(info["name"]))
        f.addRow("발급자", QLabel(info["issuer"]))
        f.addRow("고유 이름", QLabel(info["dn"]))
        f.addRow("유효 시작", QLabel(_fmt(info["not_before"])))
        f.addRow("유효 끝", QLabel(_fmt(info["not_after"]) + ("  (만료됨)" if info["expired"] else "")))
        f.addRow("사용 목적", QLabel(", ".join(info["usages"])))
        f.addRow("일련 번호", QLabel(info["serial"]))
        for lb in self.findChildren(QLabel):
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(self.reject)
        f.addRow(bb)


# ── 서명 모양 ─────────────────────────────────────────────────────
class AppearanceWidget(QWidget):
    """제목 · 도형(없음 · 가져온 도형 · 이름) · 텍스트 항목 여덟 · 방향 · 미리보기."""

    def __init__(self, st: dict, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        f = QFormLayout(); v.addLayout(f)
        self.title = QLineEdit(st.get("title", "기본 스타일")); f.addRow("제목", self.title)
        g = QGroupBox("도형 구성"); gv = QVBoxLayout(g)
        self.shape = QButtonGroup(self)
        self.r_none = QRadioButton("도형 없음"); self.r_img = QRadioButton("가져온 도형"); self.r_name = QRadioButton("이름")
        for k, r in enumerate((self.r_none, self.r_img, self.r_name)):
            self.shape.addButton(r, k)
        row = QHBoxLayout(); row.addWidget(self.r_img)
        self.image = QLineEdit(st.get("image", "")); self.image.setReadOnly(True); row.addWidget(self.image, 1)
        self.browse = QPushButton("찾아보기"); row.addWidget(self.browse)
        gv.addWidget(self.r_none); gv.addLayout(row); gv.addWidget(self.r_name)
        (self.r_img if st.get("image") else self.r_name if st.get("shape") == "name" else self.r_none).setChecked(True)
        v.addWidget(g)
        g2 = QGroupBox("텍스트 구성 표시 항목"); grid = QHBoxLayout(g2)
        cols = [QVBoxLayout(), QVBoxLayout()]
        self.checks = {}
        for k, (key, lbl) in enumerate(G.STYLE_ITEMS):
            c = QCheckBox(lbl); c.setChecked(key in st.get("items", [])); self.checks[key] = c
            cols[k % 2].addWidget(c)
        for c in cols:
            grid.addLayout(c)
        v.addWidget(g2)
        f2 = QFormLayout(); v.addLayout(f2)
        self.dir = QComboBox(); self.dir.addItems(["자동", "왼쪽으로부터 오른쪽", "오른쪽으로부터 왼쪽"])
        self.dir.setCurrentIndex({"auto": 0, "ltr": 1, "rtl": 2}.get(st.get("dir", "auto"), 0))
        f2.addRow("텍스트 방향", self.dir)
        self.preview = QLabel(); self.preview.setMinimumSize(300, 110); self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background: white; border: 1px solid #bbb;")
        v.addWidget(QLabel("미리보기")); v.addWidget(self.preview)
        self.browse.clicked.connect(self._browse)
        for w in (*self.checks.values(),):
            w.toggled.connect(self.update_preview)
        self.shape.idToggled.connect(lambda *_: self.update_preview())
        self.dir.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "도형 가져오기", "", "이미지 (*.png *.jpg *.jpeg *.bmp)")
        if p:
            self.image.setText(p); self.r_img.setChecked(True); self.update_preview()

    def value(self) -> dict:
        sid = self.shape.checkedId()
        return {"title": self.title.text().strip() or "기본 스타일",
                "items": [k for k, _ in G.STYLE_ITEMS if self.checks[k].isChecked()],
                "image": self.image.text() if sid == 1 else "", "shape": "name" if sid == 2 else "",
                "dir": ("auto", "ltr", "rtl")[self.dir.currentIndex()]}

    def update_preview(self, *_):
        st = self.value()
        info = {"name": "홍길동", "dn": "CN=홍길동, E=hong@example.com"}
        lines = G.appearance_lines(st, info, "서명 이유", "서울", _dt.datetime.now(_dt.timezone.utc))
        data = G.appearance_pdf(200, 70, lines, st["image"], st["dir"], "홍길동" if st["shape"] == "name" else "")
        d = fitz.open("pdf", data)
        pm = d[0].get_pixmap(dpi=110)
        d.close()
        qp = QPixmap(); qp.loadFromData(pm.tobytes("png"))
        self.preview.setPixmap(qp)


class AppearanceDialog(QDialog):
    def __init__(self, parent, st: dict):
        super().__init__(parent)
        self.setWindowTitle("서명 스타일 구성")
        v = QVBoxLayout(self)
        self.w = AppearanceWidget(st); v.addWidget(self.w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); v.addWidget(bb)

    def value(self) -> dict:
        return self.w.value()


# ── 디지털 ID 추가 마법사 ──────────────────────────────────────────
class AddIdWizard(QDialog):
    """① 가져오기/새로 만들기 → ② 식별 정보 → ③ 파일·비밀번호 → ④ 확인. 가져오기는 ① → 파일·비밀번호 → ④."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("디지털 ID 추가")
        self.entry: dict | None = None
        v = QVBoxLayout(self)
        self.stack = QStackedWidget(); v.addWidget(self.stack, 1)
        # ① 방식
        p0 = QWidget(); l0 = QVBoxLayout(p0)
        l0.addWidget(QLabel("서명에 사용할 디지털 ID 를 추가합니다."))
        self.r_import = QRadioButton("파일에서 기존 디지털 ID를 가져오기"); self.r_new = QRadioButton("새로운 디지털 ID 만들기")
        self.r_new.setChecked(True); l0.addWidget(self.r_import); l0.addWidget(self.r_new)
        l0.addWidget(QLabel("저장 위치: 새 PKCS#12 디지털 ID 파일")); l0.addStretch(1)
        # ② 가져오기: 파일·비밀번호
        p1 = QWidget(); f1 = QFormLayout(p1)
        row = QHBoxLayout(); self.imp_path = QLineEdit(); b = QPushButton("찾아보기"); row.addWidget(self.imp_path, 1); row.addWidget(b)
        b.clicked.connect(self._pick_import)
        f1.addRow("파일", row)
        self.imp_pw = QLineEdit(); self.imp_pw.setEchoMode(QLineEdit.EchoMode.Password); f1.addRow("비밀번호", self.imp_pw)
        # ③ 식별 정보
        p2 = QWidget(); f2 = QFormLayout(p2)
        self.name = QLineEdit(); self.unit = QLineEdit(); self.org = QLineEdit(); self.email = QLineEdit()
        self.country = QComboBox()
        for code, lbl in COUNTRIES:
            self.country.addItem(f"{code} - {lbl}" if code else lbl, code)
        f2.addRow("이름*", self.name); f2.addRow("구성 단위", self.unit); f2.addRow("조직 이름", self.org)
        f2.addRow("이메일 주소*", self.email); f2.addRow("국가/지역", self.country)
        # ④ 파일 이름·비밀번호
        p3 = QWidget(); f3 = QFormLayout(p3)
        row = QHBoxLayout(); self.path = QLineEdit(); b2 = QPushButton("찾아보기"); row.addWidget(self.path, 1); row.addWidget(b2)
        b2.clicked.connect(self._pick_new)
        f3.addRow("파일 이름", row)
        self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2 = QLineEdit(); self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        f3.addRow("비밀번호", self.pw); f3.addRow("비밀번호 확인", self.pw2)
        # ⑤ 확인
        p4 = QWidget(); l4 = QVBoxLayout(p4)
        l4.addWidget(QLabel("추가될 디지털 ID"))
        self.summary = QListWidget(); l4.addWidget(self.summary)
        for p in (p0, p1, p2, p3, p4):
            self.stack.addWidget(p)
        bar = QHBoxLayout(); v.addLayout(bar)
        bar.addStretch(1)
        self.back = QPushButton("< 이전"); self.next = QPushButton("다음 >"); self.cancel = QPushButton("취소")
        bar.addWidget(self.back); bar.addWidget(self.next); bar.addWidget(self.cancel)
        self.back.clicked.connect(self.go_back); self.next.clicked.connect(self.go_next); self.cancel.clicked.connect(self.reject)
        self._sync()

    def _pick_import(self):
        p, _ = QFileDialog.getOpenFileName(self, "디지털 ID 가져오기", "", "디지털 ID 파일 (*.pfx *.p12)")
        if p:
            self.imp_path.setText(p)

    def _pick_new(self):
        p, _ = QFileDialog.getSaveFileName(self, "디지털 ID 파일", self.path.text(), "디지털 ID 파일 (*.pfx *.p12)")
        if p:
            self.path.setText(p)

    def _sync(self):
        k = self.stack.currentIndex()
        self.back.setEnabled(k > 0)
        self.next.setText("마침" if k == 4 else "다음 >")

    def _err(self, msg: str):
        QMessageBox.warning(self, "디지털 ID 추가", msg)

    def go_back(self):
        k = self.stack.currentIndex()
        self.stack.setCurrentIndex({1: 0, 2: 0, 3: 2, 4: 1 if self.r_import.isChecked() else 3}.get(k, 0))
        self._sync()

    def go_next(self) -> bool:
        """다음 걸음. 막히면 문구를 띄우고 False."""
        k = self.stack.currentIndex()
        try:
            if k == 0:
                self.stack.setCurrentIndex(1 if self.r_import.isChecked() else 2)
            elif k == 1:
                self.entry = G.import_pfx(self.imp_path.text().strip(), self.imp_pw.text())
                self._show_summary(); self.stack.setCurrentIndex(4)
            elif k == 2:
                G.check_identity(self.name.text(), self.email.text(), "x" * G.MIN_PW)
                if not self.path.text().strip():
                    self.path.setText(G.default_id_path(self.name.text()))
                self.stack.setCurrentIndex(3)
            elif k == 3:
                G.check_identity(self.name.text(), self.email.text(), self.pw.text(), self.pw2.text())
                p = self.path.text().strip()
                if not p.lower().endswith((".pfx", ".p12")):
                    raise G.DsignError("파일 이름은 .pfx 또는 .p12 로 끝나야 합니다.")
                if os.path.exists(p) and QMessageBox.question(self, "디지털 ID 추가", f"{p}\n같은 파일이 있습니다. 바꿀까요?") \
                        != QMessageBox.StandardButton.Yes:
                    return False
                self.entry = G.create_id(self.name.text(), self.email.text(), self.pw.text(), p, org=self.org.text(),
                                         unit=self.unit.text(), country=self.country.currentData() or "")
                self._show_summary(); self.stack.setCurrentIndex(4)
            else:
                self.accept()
        except G.DsignError as e:
            self._err(str(e)); return False
        self._sync()
        return True

    def _show_summary(self):
        self.summary.clear()
        c = G.entry_cert(self.entry)
        info = G.cert_info(c)
        self.summary.addItem(f"{info['name']}  ·  발행인 {info['issuer']}  ·  만료 {info['not_after'].astimezone():%Y.%m.%d}")


# ── 디지털 인증서(디지털 ID · 서명 모양 · 신뢰할 수 있는 인증서) ────────────────
class CertManagerDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("디지털 인증서")
        self.resize(560, 440)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget(); v.addWidget(self.tabs, 1)
        # 디지털 ID
        p = QWidget(); h = QHBoxLayout(p)
        self.id_list = QListWidget(); h.addWidget(self.id_list, 1)
        col = QVBoxLayout(); h.addLayout(col)
        self.b_add = QPushButton("추가"); self.b_info = QPushButton("인증서 정보"); self.b_export = QPushButton("내보내기")
        self.b_del = QPushButton("삭제"); self.b_refresh = QPushButton("새로 고침")
        for b in (self.b_add, self.b_info, self.b_export, self.b_del, self.b_refresh):
            col.addWidget(b)
        col.addStretch(1)
        self.tabs.addTab(p, "디지털 ID")
        # 서명 모양
        p2 = QWidget(); v2 = QVBoxLayout(p2)
        self.look = AppearanceWidget(style(win)); v2.addWidget(self.look)
        self.tabs.addTab(p2, "서명 모양")
        # 신뢰할 수 있는 인증서
        p3 = QWidget(); h3 = QHBoxLayout(p3)
        self.tr_list = QListWidget(); h3.addWidget(self.tr_list, 1)
        col3 = QVBoxLayout(); h3.addLayout(col3)
        self.t_add = QPushButton("추가"); self.t_info = QPushButton("인증서 정보"); self.t_del = QPushButton("삭제")
        self.t_refresh = QPushButton("새로 고침")
        for b in (self.t_add, self.t_info, self.t_del, self.t_refresh):
            col3.addWidget(b)
        col3.addStretch(1)
        self.tabs.addTab(p3, "신뢰할 수 있는 인증서")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(self.close_save); v.addWidget(bb)
        self.b_add.clicked.connect(self.add_id); self.b_info.clicked.connect(self.info_id)
        self.b_export.clicked.connect(self.export_id); self.b_del.clicked.connect(self.delete_id)
        self.b_refresh.clicked.connect(self.fill)
        self.t_add.clicked.connect(self.add_trust); self.t_info.clicked.connect(self.info_trust)
        self.t_del.clicked.connect(self.delete_trust); self.t_refresh.clicked.connect(self.fill)
        self.fill()

    def close_save(self):
        _prefs(self.win)["dsign_style"] = self.look.value(); _save(self.win)
        self.reject()

    def fill(self):
        self.id_list.clear()
        lst = ids(self.win)
        lst[:] = [e for e in lst if os.path.exists(e.get("path", ""))]
        for e in lst:
            c = G.entry_cert(e)
            tail = ""
            if c is not None:
                info = G.cert_info(c)
                tail = f"  ·  만료 {info['not_after'].astimezone():%Y.%m.%d}" + ("(만료됨)" if info["expired"] else "")
            it = QListWidgetItem(f"{e.get('name', '')}  ·  디지털 ID 파일{tail}"); it.setData(Qt.ItemDataRole.UserRole, e)
            self.id_list.addItem(it)
        if not lst:
            self.id_list.addItem(MSG_NO_ID)
        self.tr_list.clear()
        for s in trusted(self.win):
            try:
                n = G.cert_info(G.b64_cert(s))["name"]
            except Exception:
                n = "(읽을 수 없는 인증서)"
            it = QListWidgetItem(n); it.setData(Qt.ItemDataRole.UserRole, s); self.tr_list.addItem(it)

    def _cur(self, lst: QListWidget):
        it = lst.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def add_id(self) -> dict | None:
        w = AddIdWizard(self)
        if not _exec(w) or not w.entry:
            return None
        G.add_entry(ids(self.win), w.entry); _save(self.win); self.fill()
        return w.entry

    def info_id(self):
        e = self._cur(self.id_list)
        c = G.entry_cert(e) if e else None
        if c is not None:
            _exec(CertInfoDialog(self, c))

    def export_id(self):
        e = self._cur(self.id_list)
        if not e:
            return
        p, _ = QFileDialog.getSaveFileName(self, "공개 인증서 내보내기", e.get("name", "인증서") + ".cer", "인증서 (*.cer *.pem)")
        if p:
            G.export_cert(e, p)
            QMessageBox.information(self, "디지털 인증서", "공개 인증서를 내보냈습니다.")

    def delete_id(self):
        e = self._cur(self.id_list)
        if not e or QMessageBox.question(self, "디지털 인증서", MSG_DEL_ID) != QMessageBox.StandardButton.Yes:
            return
        G.delete_entry(ids(self.win), e); _save(self.win); self.fill()

    def add_trust(self):
        p, _ = QFileDialog.getOpenFileName(self, "신뢰할 인증서 추가", "", "인증서 (*.cer *.crt *.der *.pem)")
        if not p:
            return
        try:
            c = G.read_cert_file(p)
        except G.DsignError as e:
            QMessageBox.warning(self, "디지털 인증서", str(e)); return
        add_trusted(self.win, G.cert_b64(c)); self.fill()

    def info_trust(self):
        s = self._cur(self.tr_list)
        if s:
            _exec(CertInfoDialog(self, G.b64_cert(s)))

    def delete_trust(self):
        s = self._cur(self.tr_list)
        if not s or QMessageBox.question(self, "디지털 인증서", MSG_DEL_TRUST) != QMessageBox.StandardButton.Yes:
            return
        trusted(self.win)[:] = [x for x in trusted(self.win) if x != s]; _save(self.win); self.fill()


def add_trusted(win, b64: str):
    lst = trusted(win)
    if b64 not in lst:
        lst.append(b64); _save(win)


# ── 서명 창 ──────────────────────────────────────────────────────
class SignDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(TITLE)
        pr = _prefs(win)
        f = QFormLayout(self)
        self.id_box = QComboBox(); f.addRow("로그인", self.id_box)
        self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.EchoMode.Password); f.addRow("비밀번호", self.pw)
        self.new_id = QPushButton("새 ID"); f.addRow("", self.new_id)
        row = QHBoxLayout(); self.issuer = QLabel(""); self.info = QPushButton("정보"); row.addWidget(self.issuer, 1); row.addWidget(self.info)
        f.addRow("디지털 발급자", row)
        self.look = QComboBox(); f.addRow("외관", self.look)
        self.reason = QComboBox(); self.reason.setEditable(True); self.reason.addItems(G.REASONS)
        self.reason.setCurrentText(pr.get("dsign_reason") or G.REASONS[0]); f.addRow("이유", self.reason)
        self.location = QLineEdit(pr.get("dsign_location", "")); f.addRow("지역", self.location)
        self.lock = QCheckBox("서명 후 문서 잠금"); self.lock.setChecked(bool(pr.get("dsign_lock", False))); f.addRow("", self.lock)
        bb = QDialogButtonBox(); self.ok = bb.addButton("서명", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.try_accept); bb.rejected.connect(self.reject); f.addRow(bb)
        self.style = style(win)
        self.fill_ids()
        self.fill_looks()
        self.id_box.activated.connect(self._id_picked)
        self.id_box.currentIndexChanged.connect(self._show_issuer)
        self.look.activated.connect(self._look_picked)
        self.new_id.clicked.connect(self.make_id)
        self.info.clicked.connect(self.show_info)

    def fill_ids(self, select: dict | None = None):
        self.id_box.blockSignals(True); self.id_box.clear()
        for e in ids(self.win):
            if os.path.exists(e.get("path", "")):
                self.id_box.addItem(e.get("name") or os.path.basename(e["path"]), e)
        self.id_box.addItem(REFRESH, None)
        if select:
            k = next((i for i in range(self.id_box.count()) if (self.id_box.itemData(i) or {}).get("path") == select.get("path")), 0)
            self.id_box.setCurrentIndex(k)
        self.id_box.blockSignals(False)
        self._show_issuer()

    def fill_looks(self):
        self.look.clear(); self.look.addItem(self.style.get("title", "기본 스타일")); self.look.addItem(NEW_STYLE)

    def _id_picked(self, k: int):
        if self.id_box.itemData(k) is None:
            self.fill_ids()

    def _look_picked(self, k: int):
        if self.look.itemText(k) != NEW_STYLE:
            return
        d = AppearanceDialog(self, self.style)
        if _exec(d):
            self.style = d.value(); _prefs(self.win)["dsign_style"] = self.style; _save(self.win)
        self.fill_looks(); self.look.setCurrentIndex(0)

    def _show_issuer(self, *_):
        e = self.entry()
        c = G.entry_cert(e) if e else None
        self.issuer.setText(G.cert_info(c)["issuer"] if c is not None else "")
        self.info.setEnabled(c is not None)

    def entry(self) -> dict | None:
        return self.id_box.currentData()

    def make_id(self):
        w = AddIdWizard(self)
        if _exec(w) and w.entry:
            G.add_entry(ids(self.win), w.entry); _save(self.win); self.fill_ids(w.entry)

    def show_info(self):
        e = self.entry()
        c = G.entry_cert(e) if e else None
        if c is not None:
            _exec(CertInfoDialog(self, c))

    def check(self) -> str:
        """서명 전에 막을 것(빈 문자열이면 통과). 비밀번호·만료는 여기서 미리 본다."""
        e = self.entry()
        if not e:
            return MSG_NO_ID
        c = G.entry_cert(e)
        if c is not None and G.cert_info(c)["expired"]:
            return G.MSG_EXPIRED
        if not self.pw.text():
            return G.MSG_NEED_PW
        try:
            G.make_signer(e["path"], self.pw.text())
        except G.DsignError as x:
            return str(x)
        return ""

    def try_accept(self):
        msg = self.check()
        if msg:
            QMessageBox.warning(self, TITLE, msg); return
        pr = _prefs(self.win)
        pr["dsign_reason"] = self.reason.currentText().strip()
        pr["dsign_location"] = self.location.text().strip()
        pr["dsign_lock"] = self.lock.isChecked()
        _save(self.win)
        self.accept()

    def request(self) -> G.SignRequest:
        e = self.entry()
        return G.SignRequest(e["path"], self.pw.text(), reason=self.reason.currentText().strip() or G.REASONS[0],
                             location=self.location.text().strip(), lock=self.lock.isChecked(), style=dict(self.style))


# ── 서명 속성 · 모든 서명 확인 ──────────────────────────────────────
class SigPropsDialog(QDialog):
    def __init__(self, win, tab, state: G.SigState):
        super().__init__(win)
        self.win, self.tab, self.state = win, tab, state
        self.setWindowTitle("서명 속성")
        v = QVBoxLayout(self)
        self.head = QLabel(); v.addWidget(self.head)
        f = QFormLayout(); v.addLayout(f)
        self.f_signer, self.f_reason, self.f_date, self.f_loc = QLabel(), QLabel(), QLabel(), QLabel()
        f.addRow("서명자", self.f_signer); f.addRow("원인", self.f_reason); f.addRow("날짜", self.f_date); f.addRow("지역", self.f_loc)
        self.cert_btn = QPushButton("인증서 표시"); f.addRow("", self.cert_btn)
        g = QGroupBox("유효성 요약"); gv = QVBoxLayout(g)
        self.cov, self.ident = QLabel(), QLabel()
        for lb in (self.cov, self.ident):
            lb.setWordWrap(True); gv.addWidget(lb)
        gv.addWidget(QLabel(G.TIME_NOTE))
        v.addWidget(g)
        bar = QHBoxLayout(); v.addLayout(bar)
        self.again = QPushButton("서명 유효성 검사"); close = QPushButton("닫기")
        bar.addStretch(1); bar.addWidget(self.again); bar.addWidget(close)
        close.clicked.connect(self.accept)
        self.again.clicked.connect(self.revalidate)
        self.cert_btn.clicked.connect(lambda: self.state.cert is not None and _exec(CertInfoDialog(self, self.state.cert)))
        self.show_state()

    def show_state(self):
        s = self.state
        self.head.setText(s.status_text)
        self.f_signer.setText(s.signer); self.f_reason.setText(s.reason); self.f_date.setText(_fmt(s.when))
        self.f_loc.setText(s.location)
        self.cov.setText(s.coverage); self.ident.setText(s.identity_text)
        self.cert_btn.setEnabled(s.cert is not None)

    def revalidate(self):
        st = next((x for x in validate_tab(self.win, self.tab) if x.field == self.state.field), None)
        if st:
            self.state = st; self.show_state()


class ValidateAllDialog(QDialog):
    def __init__(self, win, tab, states: list[G.SigState]):
        super().__init__(win)
        self.win, self.tab, self.states = win, tab, states
        self.setWindowTitle("모든 서명 확인")
        v = QVBoxLayout(self)
        self.msg = QLabel(G.summary(states)); v.addWidget(self.msg)
        self.list = QListWidget(); v.addWidget(self.list, 1)
        for s in states:
            self.list.addItem(f"{s.field}  ·  {s.signer}  ·  {s.status_text}")
        bar = QHBoxLayout(); v.addLayout(bar)
        self.props = QPushButton("서명 속성"); close = QPushButton("닫기")
        bar.addStretch(1); bar.addWidget(self.props); bar.addWidget(close)
        close.clicked.connect(self.accept)
        self.props.clicked.connect(self._props)
        if states:
            self.list.setCurrentRow(0)

    def _props(self):
        k = self.list.currentRow()
        if 0 <= k < len(self.states):
            _exec(SigPropsDialog(self.win, self.tab, self.states[k]))


# ── 흐름 ─────────────────────────────────────────────────────────
def tab_bytes(tab) -> bytes:
    """서명·검증할 바이트. 저장된 그대로면 파일 바이트(앞 서명이 깨지지 않게), 고쳤으면 지금 문서."""
    d = tab.doc
    if d.path and not d.modified and os.path.exists(d.path):
        with open(d.path, "rb") as fp:
            return fp.read()
    return d.fitz.tobytes()


def validate_tab(win, tab) -> list[G.SigState]:
    try:
        return G.validate(tab_bytes(tab), trusted(win), tab.doc.password)
    except Exception:
        return []


def start(win, tab):
    """보안 탭 「디지털 서명」."""
    f = tab.doc.fitz
    empty = G.empty_sig_fields(f)
    if len(empty) == 1:
        return sign_field(win, tab, empty[0]["name"])
    if len(empty) > 1:
        QMessageBox.information(win, TITLE, MSG_MULTI)
        return None
    QMessageBox.information(win, TITLE, MSG_DRAG)
    arm_drag(win, tab)
    return None


def arm_drag(win, tab):
    """칸 도구(fsign)로 자리를 끌게 하고, 칸이 생기면 그 자리로 서명한다."""
    before = {s["xref"] for s in G.sig_widgets(tab.doc.fitz)}
    was_clean = bool(tab.doc.path) and not tab.doc.modified
    v = tab.viewer

    def done(tool: str):
        try:
            v.toolDone.disconnect(done)
        except (RuntimeError, TypeError):
            pass
        if tool != "fsign":
            return
        new = [s for s in G.sig_widgets(tab.doc.fitz) if s["xref"] not in before]
        if not new:
            return
        s = new[-1]
        if was_clean and tab.doc.can_undo():   # 칸을 되돌리고 서명할 때 같은 자리에 만든다(원본 바이트 그대로)
            tab.doc.undo(); tab.doc.modified = False
            tab.changed([s["page"]])
            sign_new(win, tab, s["page"], s["rect"])
        else:
            tab.changed([s["page"]])
            sign_field(win, tab, s["name"])
    v.toolDone.connect(done)
    win.on_tool("fsign")


def _run_sign(win, tab, fill) -> str | None:
    dlg = SignDialog(win)
    if not ids(win):
        QMessageBox.information(win, TITLE, MSG_NO_ID)
    if not _exec(dlg):
        return None
    req = dlg.request()
    fill(req)
    req.doc_password = tab.doc.password
    try:
        data = G.sign_bytes(tab_bytes(tab), req)
    except G.DsignError as e:
        QMessageBox.warning(win, TITLE, str(e)); return None
    folder = os.path.dirname(tab.doc.path) if tab.doc.path else os.path.expanduser("~")
    out = _save_path(win, os.path.join(folder, G.signed_name(tab.doc.path)))
    if not out:
        return None
    try:
        with open(out, "wb") as fp:
            fp.write(data)
    except OSError:
        QMessageBox.warning(win, TITLE, MSG_SAVE_FAIL); return None
    _open_path(win, out)
    return out


def sign_field(win, tab, name: str) -> str | None:
    def fill(r: G.SignRequest):
        r.field = name
    return _run_sign(win, tab, fill)


def sign_new(win, tab, page: int, rect: fitz.Rect) -> str | None:
    def fill(r: G.SignRequest):
        r.new_page, r.new_rect = page, fitz.Rect(rect)
    return _run_sign(win, tab, fill)


def validate_all(win, tab):
    states = validate_tab(win, tab)
    if not states:
        QMessageBox.information(win, "모든 서명 확인", MSG_NO_SIG); return []
    _exec(ValidateAllDialog(win, tab, states))
    bar = getattr(tab, "sign_bar", None)
    if bar is not None:
        bar.show_states(states)
    return states


def _my_certs(win) -> list[str]:
    return [e["cert"] for e in ids(win) if e.get("cert") and os.path.exists(e.get("path", ""))]


def clear_all(win, tab, only: str | None = None) -> int:
    """모든 서명 제거(또는 칸 하나). 지운 쪽 수를 돌려준다."""
    if not G.has_signatures(tab.doc.fitz):
        QMessageBox.information(win, "모든 서명 제거", MSG_NO_SIG); return 0
    tab.doc.snapshot()
    pages, failed = G.clear(tab.doc.fitz, _my_certs(win), only=only)
    if pages:
        tab.changed(pages)
        win.mark_modified()
    elif not failed:
        pass
    if failed:
        QMessageBox.warning(win, "모든 서명 제거", MSG_CLEAR_ONE_FAIL if only else MSG_CLEAR_FAIL.format(failed))
    bar = getattr(tab, "sign_bar", None)
    if bar is not None:
        bar.check()
    return len(pages)


def show_props(win, tab, name: str):
    st = next((x for x in validate_tab(win, tab) if x.field == name), None)
    if st is None:
        QMessageBox.information(win, "서명 속성", MSG_NO_SIG); return None
    dlg = SigPropsDialog(win, tab, st)
    _exec(dlg)
    return dlg


def validate_one(win, tab, name: str):
    st = next((x for x in validate_tab(win, tab) if x.field == name), None)
    if st is None:
        QMessageBox.information(win, "서명 인증", MSG_NO_SIG); return None
    QMessageBox.information(win, "서명 인증", f"{st.status_text}\n{st.coverage}\n{st.identity_text}")
    return st


def manage(win):
    d = CertManagerDialog(win)
    _exec(d)
    return d


def field_clicked(win, tab, data: dict) -> bool:
    """읽기 모드에서 서명 칸을 눌렀다: 빈 칸이면 서명, 서명된 칸이면 속성. 서명 칸이 아니면 False."""
    w = data.get("widget")
    if w is None or w.field_type != fitz.PDF_WIDGET_TYPE_SIGNATURE:
        return False
    name = w.field_name or ""
    hit = next((s for s in G.sig_widgets(tab.doc.fitz) if s["xref"] == data.get("xref")), None)
    if hit and hit["signed"]:
        show_props(win, tab, name)
    else:
        sign_field(win, tab, name)
    return True


def request(win, tab, data: dict):
    """우클릭·띠에서 온 일: {"do": "clear"|"validate"|"props", "name": 칸 이름}."""
    do, name = data.get("do"), data.get("name", "")
    if do == "clear":
        clear_all(win, tab, only=name)
    elif do == "validate":
        validate_one(win, tab, name)
    elif do == "props":
        show_props(win, tab, name)


def on_action(win, tab, key: str):
    """window.on_action 의 dsign_* 갈래."""
    if key == "dsign_ids":
        return manage(win)
    if tab is None:
        return None
    if key == "digital_sign":
        return start(win, tab)
    if key == "dsign_validate":
        return validate_all(win, tab)
    if key == "dsign_clear":
        return clear_all(win, tab)
    return None
