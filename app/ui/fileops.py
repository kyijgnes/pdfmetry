"""열기·저장·생성 둘레: 저장 앞 검사, 「다른 이름으로」 넘기기, 기본 이름, 빈 PDF 설정값, 자동 저장(복구 사본).

window.py 의 열기·저장·생성 함수가 여기를 부른다. 화면 없는 판단(이름·경로·설정값)과 창 몇 개만 둔다.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from ..appinfo import data_dir
from ..engine.document import Document
from . import tab_detach

PATH_MAX = 260   # 윈도우 경로 길이 한계(MAX_PATH)
BAD_CHARS = re.compile(r'[<>"|?*\x00-\x1f]')
UNTITLED = "제목 없음"
FDF_EXTS = (".fdf", ".xfdf")

OPEN_FILTER = ("공용 파일 (*.pdf *.fdf *.xfdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp *.xps *.epub *.txt *.svg *.cbz)"
               ";;PDF 파일 (*.pdf);;FDF 파일 (*.fdf);;XFDF 파일 (*.xfdf);;모든 파일 (*)")
_WORD = "*.doc *.docx *.docm *.dot *.dotx *.rtf *.odt"
_EXCEL = "*.xls *.xlsx *.xlsm *.csv *.ods"
_PPT = "*.ppt *.pptx *.pptm *.odp"
_IMAGE = "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp"
_OTHER = "*.hwp *.hwpx *.txt *.log *.ini *.xml *.htm *.html *.mht *.mhtml *.xps *.epub *.svg *.cbz"
CREATE_FILTER = ";;".join((
    "모든 파일 (*)",
    f"모든 지원 파일 (*.pdf {_WORD} {_EXCEL} {_PPT} {_IMAGE} {_OTHER})",
    "PDF 파일 (*.pdf)", f"Word 파일 ({_WORD})", f"Excel 파일 ({_EXCEL})", f"PowerPoint 파일 ({_PPT})",
    f"이미지 파일 ({_IMAGE})", f"그 밖의 파일 ({_OTHER})",
))
BLANK_SIZES = {"A3": (842, 1191), "A4": (595, 842), "A5": (420, 595), "B4": (729, 1032), "B5": (516, 729),
               "Letter": (612, 792), "Legal": (612, 1008)}


# ── 이름·경로 ─────────────────────────────────────────────────
def documents_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Documents")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def path_problem(path: str) -> str | None:
    """저장 앞 경로 검사. 문제가 있으면 알릴 문구."""
    if len(os.path.abspath(path)) >= PATH_MAX:
        return "오류! 경로가 너무 깁니다."
    drive, rest = os.path.splitdrive(path)
    name = os.path.basename(path)
    if BAD_CHARS.search(rest.replace("\\", "/")) or not name.strip(" .") or ":" in rest:
        return "경로가 잘못되었습니다."
    return None


def copy_name(path: str) -> str:
    """다른 이름으로 저장의 기본 이름: 「이름-복사.pdf」."""
    stem = os.path.splitext(path)[0]
    return stem + "-복사.pdf"


def unique_title(taken: set[str], base: str = UNTITLED) -> str:
    """열린 문서와 겹치지 않는 새 문서 이름: 「제목 없음.pdf」, 겹치면 「제목 없음-2.pdf」…"""
    low = {n.lower() for n in taken}
    if f"{base}.pdf".lower() not in low:
        return f"{base}.pdf"
    k = 2
    while f"{base}-{k}.pdf".lower() in low:
        k += 1
    return f"{base}-{k}.pdf"


def open_names(win) -> set[str]:
    return {t.doc.name for w in windows(win) for t in w.tabs}


def blank_spec(prefs: dict) -> tuple[str, int, float, float]:
    """환경 설정 「빈 PDF」 값(prefs["blank"] = {name, count, size, w, h, landscape}): 이름·쪽 수·너비·높이(pt).
    w·h 가 있으면 그 값(pt), 없으면 size 이름으로 찾는다."""
    b = prefs.get("blank") or {}
    name = str(b.get("name") or UNTITLED).strip() or UNTITLED
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    try:
        count = max(1, min(int(b.get("count", 1)), 9999))
    except (TypeError, ValueError):
        count = 1
    w, h = BLANK_SIZES.get(str(b.get("size") or "A4"), BLANK_SIZES["A4"])
    try:
        if float(b.get("w") or 0) > 0 and float(b.get("h") or 0) > 0:
            w, h = float(b["w"]), float(b["h"])
    except (TypeError, ValueError):
        pass
    if bool(b.get("landscape")) != (w > h):
        w, h = h, w
    return name, count, w, h


def save_dir(win) -> str:
    """새 문서·다른 이름으로 저장의 시작 폴더: 「새 PDF의 위치 저장」 을 켰으면 그 폴더, 아니면 마지막으로 쓴 폴더."""
    p = win.prefs
    if p.get("save_dir_on") and p.get("save_dir") and os.path.isdir(p["save_dir"]):
        return p["save_dir"]
    last = win.settings.get("last_open_dir") or ""
    return last if last and safe_dir(win, last) else documents_dir()


def safe_dir(win, folder: str) -> bool:
    """열기 창을 그 폴더에서 시작해도 되나. 공유폴더(\\\\…)는 닿지 않으면 열기 창이 한참 멈추니,
    있다고 확인된 최근 파일이 그 안에 있을 때만."""
    if not folder:
        return False
    if folder.startswith(("\\\\", "//")):
        known = win.start.stats.known
        return any(os.path.dirname(r["path"]) == folder and (known.get(r["path"]) or (False,))[0] for r in win.settings["recent"])
    return os.path.isdir(folder)


def open_start_dir(win) -> str:
    last = win.settings.get("last_open_dir") or ""
    return last if safe_dir(win, last) else documents_dir()


def save_suggestion(win, doc) -> str:
    """저장 창 기본 경로. 파일로 연 문서는 「이름-복사.pdf」, 새 문서는 이름 그대로(시작 폴더는 save_dir)."""
    hint = getattr(doc, "save_hint", None)
    if hint:
        return hint
    if doc.path and not getattr(doc, "ask_save", False) and doc.path.lower().endswith(".pdf"):
        return copy_name(doc.path)
    if doc.path:   # 이미지·문서를 PDF 로 바꿔 연 것: 그 자리에 「이름.pdf」
        return os.path.splitext(doc.path)[0] + ".pdf"
    name = doc.name if doc.name.lower().endswith(".pdf") else doc.name + ".pdf"
    return os.path.join(save_dir(win), name)


# ── 저장 앞뒤 창 ──────────────────────────────────────────────
def ask_save_as_instead(parent, why: str) -> bool:
    """이 자리에 쓸 수 없다: [다른 이름으로 저장] 을 고르면 True."""
    box = QMessageBox(QMessageBox.Icon.Warning, "저장", f"{why}\n다른 이름으로 저장하시겠습니까?", parent=parent)
    yes = box.addButton("다른 이름으로 저장", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


def save_error(parent, e: Exception):
    msg = str(e).strip()
    QMessageBox.critical(parent, "저장 오류", msg or "저장하는 과정에서 문제가 생겼습니다.")


def bad_path(parent, path: str) -> bool:
    """경로가 잘못됐으면 알리고 True."""
    why = path_problem(path)
    if why:
        QMessageBox.warning(parent, "유효하지 않은 경로", why)
    return bool(why)


# ── 창 찾기 ───────────────────────────────────────────────────
def windows(win) -> list:
    return [win] + [w for w in tab_detach.others(win) if w is not win]


def find_open(win, path: str):
    """그 파일을 연 창과 탭 번호(이 창부터, 다음은 떼어 낸 창들). 없으면 None."""
    path = os.path.normcase(os.path.abspath(path))
    for w in windows(win):
        for k, t in enumerate(w.tabs):
            if t.doc.path and os.path.normcase(os.path.abspath(t.doc.path)) == path:
                return w, k
    return None


def bring(w, k: int):
    if w.isMinimized():
        w.showNormal()
    w.raise_(); w.activateWindow()
    w.doctabs.setCurrentIndex(k)


# ── FDF·XFDF 를 열기로 ────────────────────────────────────────
def import_annot_file(doc, path: str) -> tuple[int, int]:
    """FDF·XFDF 주석 파일을 지금 문서에 넣는다(가져온 수, 못 가져온 수)."""
    from ..engine import annot_xfdf as X
    with open(path, "rb") as f:
        raw = f.read()
    head = raw.lstrip()[:16]
    if head.startswith(b"%FDF"):
        return X.import_fdf(doc, raw)
    if head.startswith(b"<"):
        return X.import_xfdf(doc, raw)
    raise ValueError("FDF·XFDF 파일이 아닙니다")


# ── 생성 ──────────────────────────────────────────────────────
def clipboard_kind(md) -> str | None:
    """클립보드에 PDF 로 만들 것이 있나: files · image · text · None."""
    if md is None:
        return None
    if md.hasUrls() and any(u.isLocalFile() and os.path.isfile(u.toLocalFile()) for u in md.urls()):
        return "files"
    if md.hasImage():
        return "image"
    if md.hasText() and md.text().strip():
        return "text"
    return None


def created_name(paths: list[str]) -> str:
    """파일에서 생성한 새 문서 이름: 하나면 「원래이름.pdf」, 여럿이면 「첫이름 합치기.pdf」."""
    stem = os.path.splitext(os.path.basename(paths[0]))[0]
    return f"{stem}.pdf" if len(paths) == 1 else f"{stem} 합치기.pdf"


def temp_out(name: str) -> str:
    folder = os.path.join(data_dir(), "Temp", uuid.uuid4().hex)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name)


# ── 자동 저장(복구 사본) ─────────────────────────────────────
def recover_dir() -> str:
    return os.path.join(data_dir(), "복구")


def _rid(doc) -> str:
    rid = getattr(doc, "_recover_id", None)
    if not rid:
        rid = doc._recover_id = uuid.uuid4().hex
    return rid


_QUIT_HOOKED = False
_HOLD = False   # 복구를 묻는 동안은 남은 사본을 지우지 않는다


class Autosave:
    """환경 설정 「자동 저장」(prefs autosave · autosave_min, 기본 켬·10분). 원본은 건드리지 않고
    고친 문서를 %APPDATA%\\PDFmetry\\복구\\ 에 사본으로 둔다. 저장하거나 닫으면 사본을 지우고,
    앱이 튕겨 남은 사본은 다음에 켤 때 열어 준다."""

    TICK_MS = 60_000

    def __init__(self, win):
        global _QUIT_HOOKED
        self.win = win
        self.last = time.monotonic()
        self.timer = QTimer(win); self.timer.setInterval(self.TICK_MS); self.timer.timeout.connect(self.tick); self.timer.start()
        app = QApplication.instance()
        if app is not None and not _QUIT_HOOKED:   # 정상 종료: 남은 사본(버리고 닫은 문서)을 지운다
            _QUIT_HOOKED = True
            app.aboutToQuit.connect(lambda: sweep(set()))

    def minutes(self) -> int:
        try:
            return max(1, min(int(self.win.prefs.get("autosave_min", 10)), 20))
        except (TypeError, ValueError):
            return 10

    def tick(self):
        live = live_ids(self.win)
        sweep(live)
        if not self.win.prefs.get("autosave", True):
            return
        if time.monotonic() - self.last < self.minutes() * 60 - 1:
            return
        self.last = time.monotonic()
        self.write_all()

    def write_all(self) -> int:
        n = 0
        for t in self.win.tabs:
            if write(t.doc):
                n += 1
        if n:
            self.win.say("자동 저장했습니다(복구용 사본)")
        return n

    def drop(self, doc):
        drop(doc)

    def offer_restore(self, ask=None) -> int:
        """남은 사본이 있으면 열지 묻는다. ask(n) → True/False (시험이 대신 답한다)."""
        global _HOLD
        items = pending()
        if not items:
            return 0
        _HOLD = True
        try:
            return self._restore(items, ask)
        finally:
            _HOLD = False

    def _restore(self, items, ask) -> int:
        if ask is None:
            ask = lambda n: QMessageBox.question(   # noqa: E731
                self.win, "복구", f"복구할 문서가 있습니다({n}개).\n자동 저장해 둔 사본을 열까요?") == QMessageBox.StandardButton.Yes
        opened = 0
        if ask(len(items)):
            for meta, pdf in items:
                try:
                    with open(pdf, "rb") as f:
                        doc = Document.from_bytes(f.read())
                except Exception:  # noqa: BLE001
                    continue
                doc.title = meta.get("name") or UNTITLED + ".pdf"
                orig = meta.get("path")
                doc.save_hint = orig if orig else None
                doc.ask_save = True
                self.win.add_doc(doc); opened += 1
        for meta, pdf in items:
            _remove(pdf)
        return opened


def write(doc) -> bool:
    """고친 문서 하나를 복구 사본으로. 암호 문서는 사본에 암호가 남지 않아 쓰지 않는다."""
    if not doc.modified or doc.password:
        return False
    folder = recover_dir()
    os.makedirs(folder, exist_ok=True)
    rid = _rid(doc)
    pdf = os.path.join(folder, rid + ".pdf")
    tmp = pdf + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(doc.to_bytes())
        os.replace(tmp, pdf)
        meta = {"name": doc.name, "path": doc.path or getattr(doc, "save_hint", None) or "", "time": time.time(), "pid": os.getpid()}
        with open(os.path.join(folder, rid + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001  자동 저장 실패로 편집을 막지 않는다
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    return True


def _remove(pdf: str):
    for p in (pdf, os.path.splitext(pdf)[0] + ".json"):
        try:
            os.remove(p)
        except OSError:
            pass


def drop(doc):
    rid = getattr(doc, "_recover_id", None)
    if rid:
        _remove(os.path.join(recover_dir(), rid + ".pdf"))


def live_ids(win) -> set[str]:
    return {t.doc._recover_id for w in windows(win) for t in w.tabs if getattr(t.doc, "_recover_id", None) and t.doc.modified}


def sweep(live: set[str]):
    """이 프로그램이 쓴 사본 가운데 열려 있지 않거나 고친 것이 없는 문서의 것을 지운다.
    앞서 튕긴 프로그램이 남긴 사본은 offer_restore 가 다룬다."""
    folder = recover_dir()
    if _HOLD or not os.path.isdir(folder):
        return
    mine = set()
    for f in os.listdir(folder):
        if f.endswith(".json"):
            try:
                with open(os.path.join(folder, f), encoding="utf-8") as fp:
                    if json.load(fp).get("pid") == os.getpid():
                        mine.add(f[:-5])
            except Exception:  # noqa: BLE001
                pass
    for f in os.listdir(folder):
        rid, ext = os.path.splitext(f)
        if ext in (".pdf", ".json") and rid in mine and rid not in live:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass


def pending() -> list[tuple[dict, str]]:
    folder = recover_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".pdf"):
            continue
        pdf = os.path.join(folder, f)
        try:
            with open(os.path.splitext(pdf)[0] + ".json", encoding="utf-8") as fp:
                meta = json.load(fp)
        except Exception:  # noqa: BLE001
            meta = {}
        out.append((meta, pdf))
    out.sort(key=lambda x: x[0].get("time", 0))
    return out
