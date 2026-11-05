"""앱 안 클립보드: 종류별 칸 셋(양식 칸 · 주석 · 편집 객체), 탭·창 사이에서 같이 쓴다(설계 17, docs/편집 객체 설계.md 4.2).

칸에 담는 것은 다시 만들 수 있게 적은 자료(spec)다. 원본을 지우거나 실행 취소해도 붙일 수 있고 다른 문서에도 붙는다.
붙여넣기는 지금 모드에 맞는 칸만 본다: 손·선택 = 주석, 편집 모드 = 편집 객체, 양식 편집 = 양식 칸.

시스템 클립보드에는 그림은 그림으로, 글은 글자로 같이 올리고 우리 표시(MIME, 값은 복사 번호)를 단다.
편집 모드에서 붙일 때 표시가 우리 번호와 같으면 우리 칸을, 다르면 시스템 클립보드의 그림·글자를 새 객체로 넣는다.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

MIME = "application/x-pdfmetry-clip"

# 칸 셋. 같은 목록 객체를 그대로 고쳐 쓴다(form_tools.CLIP 이 FIELDS 를 가리킨다)
FIELDS: list[dict] = []    # 양식 칸(annots.field_spec)
ANNOTS: list[dict] = []    # 주석(annot_clip.annot_spec)
OBJECTS: list[dict] = []   # 편집 객체(objects.spec)

# 칸마다 어디서 복사했는지(문서 표시, 쪽). 같은 문서 같은 쪽이면 붙일 때 조금 비킨다
SOURCE: dict[str, tuple[int | None, int]] = {}

_serial = 0


def slot(name: str) -> list[dict]:
    return {"fields": FIELDS, "annots": ANNOTS, "objects": OBJECTS}[name]


def mode_of(v) -> str:
    """보기의 지금 모드에 맞는 칸 이름."""
    if getattr(v, "form_mode", False):
        return "fields"
    if getattr(v, "edit_mode", False):
        return "objects"
    return "annots"


def serial() -> int:
    return _serial


def put(name: str, specs: list[dict], doc_id: int | None, page: int, *, image: QImage | None = None,
        text: str = "") -> int:
    """칸 하나를 채우고 시스템 클립보드에 그림·글자와 우리 표시를 올린다. 새 복사 번호."""
    global _serial
    _serial += 1
    slot(name)[:] = list(specs)
    SOURCE[name] = (doc_id, page)
    md = QMimeData()
    md.setData(MIME, str(_serial).encode("ascii"))
    if image is not None and not image.isNull():
        md.setImageData(image)
    if text:
        md.setText(text)
    try:
        QApplication.clipboard().setMimeData(md)
    except RuntimeError:
        pass
    return _serial


def ours_on_system() -> bool:
    """시스템 클립보드의 표시가 우리 마지막 복사 번호와 같은가. 시스템 클립보드가 비었거나 우리 것이면 참."""
    md = QApplication.clipboard().mimeData()
    if md is None or not md.formats():
        return True
    if not md.hasFormat(MIME):
        return False
    try:
        return int(bytes(md.data(MIME)).decode("ascii") or 0) == _serial
    except ValueError:
        return False


def system_content() -> tuple[QImage | None, str]:
    """우리 것이 아닌 시스템 클립보드의 (그림, 글자). 없으면 (None, "")."""
    md = QApplication.clipboard().mimeData()
    if md is None:
        return None, ""
    img = None
    if md.hasImage():
        got = md.imageData()
        if isinstance(got, QImage) and not got.isNull():
            img = got
    text = md.text() if md.hasText() else ""
    return img, text


def can_paste(name: str) -> bool:
    """그 칸에 붙일 것이 있나. 편집 객체는 다른 프로그램에서 복사한 그림·글자도 붙는다."""
    if slot(name):
        return True
    if name == "objects" and not ours_on_system():
        img, text = system_content()
        return img is not None or bool(text.strip())
    return False


def same_place(name: str, doc_id: int, page: int) -> bool:
    return SOURCE.get(name) == (doc_id, page)


def forget(doc_id: int) -> None:
    """탭을 닫으면 칸에 적힌 문서 표시를 지운다(같은 번호의 새 문서를 원본으로 잘못 알지 않게)."""
    for lst in (FIELDS, ANNOTS, OBJECTS):
        for s in lst:
            if s.get("doc") == doc_id:
                s["doc"] = None
    for k, (d, pg) in list(SOURCE.items()):
        if d == doc_id:
            SOURCE[k] = (None, pg)


def clear() -> None:
    """시험용: 칸 셋을 다 비운다."""
    for lst in (FIELDS, ANNOTS, OBJECTS):
        lst.clear()
    SOURCE.clear()
