"""서명 창: 저장한 서명 목록 + 새 서명 세 가지(그리기 · 타이핑 · 사진).

고른(또는 새로 만든) 서명 그림 경로를 돌려주고, 찍기는 뷰어가 Stamp 주석으로 한다(engine/stamps.py).
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFontComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from ..engine import stamps as S
from .dialogs import _buttons

INKS = [("검정", QColor(0, 0, 0)), ("파랑", QColor(20, 50, 160)), ("빨강", QColor(190, 0, 0))]
PAD_W, PAD_H = 520, 190
OUT_SCALE = 3          # 그린 서명을 몇 배 해상도로 굽나


def _png(img: QImage) -> bytes:
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def _array(img: QImage) -> np.ndarray:
    """ARGB32 그림의 (높이, 너비, 4) 배열(B·G·R·A 차례). 그림과 메모리를 같이 쓴다."""
    ptr = img.bits()
    return np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :img.width()]


def crop_alpha(img: QImage, margin: int = 6) -> QImage | None:
    """투명하지 않은 부분만 남긴다. 다 투명하면 None."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    a = _array(img)[:, :, 3] > 8
    ys, xs = np.nonzero(a)
    if not len(xs):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    r = QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1).adjusted(-margin, -margin, margin, margin).intersected(img.rect())
    return img.copy(r)


def clear_white(img: QImage, threshold: int = 225) -> QImage:
    """사진 서명: 흰 바탕(밝은 칸)을 투명하게."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    arr = _array(img)
    arr[arr[:, :, :3].min(axis=2) >= threshold] = 0
    return img


class DrawPad(QWidget):
    """마우스·펜으로 서명을 그리는 판."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(PAD_W, PAD_H)
        self.strokes: list[list[QPointF]] = []
        self.color = INKS[0][1]
        self.width = 3
        self.setCursor(Qt.CursorShape.CrossCursor)

    def clear(self):
        self.strokes.clear(); self.update()

    def mousePressEvent(self, e):
        self.strokes.append([e.position()]); self.update()

    def mouseMoveEvent(self, e):
        if self.strokes and e.buttons() & Qt.MouseButton.LeftButton:
            self.strokes[-1].append(e.position()); self.update()

    def _paint(self, p: QPainter, k: float = 1.0):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.color, self.width * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for s in self.strokes:
            if len(s) == 1:
                p.drawPoint(s[0] * k)
                continue
            path = QPainterPath(s[0] * k)
            for q in s[1:]:
                path.lineTo(q * k)
            p.drawPath(path)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        p.setPen(QPen(QColor(200, 200, 200), 1, Qt.PenStyle.DashLine))
        p.drawLine(20, PAD_H - 40, PAD_W - 20, PAD_H - 40)   # 서명 줄
        self._paint(p)
        p.end()

    def image(self) -> QImage | None:
        if not self.strokes:
            return None
        img = QImage(PAD_W * OUT_SCALE, PAD_H * OUT_SCALE, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); self._paint(p, OUT_SCALE); p.end()
        return crop_alpha(img, 4 * OUT_SCALE)


def type_image(text: str, family: str, color: QColor, px: int = 120) -> QImage | None:
    """타이핑한 이름을 그 글꼴로 그린 그림."""
    text = text.strip()
    if not text:
        return None
    font = QFont(family); font.setPixelSize(px)
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(font)
    w, h = fm.horizontalAdvance(text) + px, fm.height() + px // 2
    img = QImage(w, h, QImage.Format.Format_ARGB32); img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setFont(font); p.setPen(color)
    p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text); p.end()
    return crop_alpha(img)


class SignatureDialog(QDialog):
    """확인하면 self.path 에 찍을 서명 그림 경로."""

    def __init__(self, parent, st: S.StampStore | None = None):
        super().__init__(parent)
        self.setWindowTitle("서명")
        self.st = st or S.store()
        self.path: str | None = None
        self._photo: QImage | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("저장한 서명"))
        self.list = QListWidget(); self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(150, 60)); self.list.setFixedHeight(96); self.list.setMovement(QListWidget.Movement.Static)
        self.list.setFlow(QListWidget.Flow.LeftToRight); self.list.setWrapping(False)
        row = QHBoxLayout(); row.addWidget(self.list, 1)
        self.del_btn = QPushButton("삭제"); self.del_btn.clicked.connect(self._delete); row.addWidget(self.del_btn)
        lay.addLayout(row)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._draw_tab(), "그리기")
        self.tabs.addTab(self._type_tab(), "타이핑")
        self.tabs.addTab(self._photo_tab(), "사진")
        lay.addWidget(self.tabs)
        self.keep = QCheckBox("서명 목록에 저장"); self.keep.setChecked(True); lay.addWidget(self.keep)
        lay.addWidget(_buttons(self, "서명 넣기"))
        self.list.itemSelectionChanged.connect(self._sync)
        self._fill()

    # 저장한 서명
    def _fill(self):
        self.list.clear()
        for e in self.st.signatures():
            it = QListWidgetItem(QIcon(QPixmap(self.st.image_path(e["file"]))), "")
            it.setData(Qt.ItemDataRole.UserRole, e["id"]); it.setSizeHint(QSize(160, 70))
            self.list.addItem(it)
        if not self.list.count():
            empty = QListWidgetItem("저장한 서명 없음"); empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setSizeHint(QSize(160, 70)); self.list.addItem(empty)
        else:
            self.list.setCurrentRow(self.list.count() - 1)
        self._sync()

    def _sync(self):
        self.del_btn.setEnabled(bool(self.list.selectedItems()))

    def _delete(self):
        it = self.list.currentItem()
        if it and QMessageBox.question(self, "서명", "고른 서명을 삭제할까요?") == QMessageBox.StandardButton.Yes:
            self.st.delete_signature(it.data(Qt.ItemDataRole.UserRole))
            self._fill()

    # 세 가지 입력
    def _ink_combo(self, on_change) -> QComboBox:
        c = QComboBox()
        for name, col in INKS:
            c.addItem(name, col)
        c.currentIndexChanged.connect(on_change)
        return c

    def _draw_tab(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        self.pad = DrawPad(); v.addWidget(self.pad, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        self.draw_ink = self._ink_combo(lambda *_: self._set_pad())
        self.draw_w = QSpinBox(); self.draw_w.setRange(1, 12); self.draw_w.setValue(3); self.draw_w.setSuffix(" px")
        self.draw_w.valueChanged.connect(lambda *_: self._set_pad())
        clear = QPushButton("지우기"); clear.clicked.connect(self.pad.clear)
        for x in (QLabel("색"), self.draw_ink, QLabel("굵기"), self.draw_w):
            row.addWidget(x)
        row.addStretch(1); row.addWidget(clear)
        v.addLayout(row)
        return w

    def _set_pad(self):
        self.pad.color = self.draw_ink.currentData(); self.pad.width = self.draw_w.value(); self.pad.update()

    def _type_tab(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        self.type_text = QLineEdit(); self.type_text.setPlaceholderText("이름 입력")
        row = QHBoxLayout()
        self.type_font = QFontComboBox()
        self.type_ink = self._ink_combo(lambda *_: self._show_type())
        for x in (QLabel("글꼴"), self.type_font, QLabel("색"), self.type_ink):
            row.addWidget(x)
        self.type_view = QLabel(); self.type_view.setFixedSize(PAD_W, 120)
        self.type_view.setAlignment(Qt.AlignmentFlag.AlignCenter); self.type_view.setStyleSheet("background:#ffffff;")
        v.addWidget(self.type_text); v.addLayout(row); v.addWidget(self.type_view, 0, Qt.AlignmentFlag.AlignHCenter)
        self.type_text.textChanged.connect(self._show_type); self.type_font.currentFontChanged.connect(self._show_type)
        return w

    def _type_image(self) -> QImage | None:
        return type_image(self.type_text.text(), self.type_font.currentFont().family(), self.type_ink.currentData())

    def _show_type(self, *_a):
        img = self._type_image()
        self.type_view.setPixmap(QPixmap.fromImage(img).scaled(PAD_W - 20, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                                               Qt.TransformationMode.SmoothTransformation)
                                 if img else QPixmap())

    def _photo_tab(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        row = QHBoxLayout()
        b = QPushButton("사진 고르기"); b.clicked.connect(self._pick_photo)
        self.photo_white = QCheckBox("흰 바탕 지우기"); self.photo_white.setChecked(True)
        self.photo_white.toggled.connect(self._show_photo)
        row.addWidget(b); row.addWidget(self.photo_white); row.addStretch(1)
        self.photo_view = QLabel("서명을 찍은 사진·그림 파일"); self.photo_view.setFixedSize(PAD_W, 150)
        self.photo_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_view.setStyleSheet("background:#ffffff; border:1px solid #c8c8c8;")
        v.addLayout(row); v.addWidget(self.photo_view, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _pick_photo(self):
        p, _ = QFileDialog.getOpenFileName(self, "서명 사진", "", "이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not p:
            return
        img = QImage(p)
        if img.isNull():
            QMessageBox.warning(self, "서명", "이미지 형식이 지원되지 않습니다.")
            return
        if max(img.width(), img.height()) > 1600:   # 큰 사진은 줄여서(흰 바탕 지우기가 느려지지 않게)
            img = img.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._photo = img
        self._show_photo()

    def _photo_image(self) -> QImage | None:
        if self._photo is None:
            return None
        img = clear_white(self._photo) if self.photo_white.isChecked() else self._photo.convertToFormat(QImage.Format.Format_ARGB32)
        return crop_alpha(img) if self.photo_white.isChecked() else img

    def _show_photo(self, *_a):
        img = self._photo_image()
        if img is not None:
            self.photo_view.setPixmap(QPixmap.fromImage(img).scaled(PAD_W - 20, 140, Qt.AspectRatioMode.KeepAspectRatio,
                                                                    Qt.TransformationMode.SmoothTransformation))

    def new_image(self) -> tuple[QImage | None, str]:
        """지금 탭에서 만든 서명(없으면 None)."""
        k = self.tabs.currentIndex()
        if k == 0:
            return self.pad.image(), "draw"
        if k == 1:
            return self._type_image(), "type"
        return self._photo_image(), "photo"

    def accept(self):
        img, how = self.new_image()
        if img is not None:
            png = _png(img)
            if self.keep.isChecked():
                self.path = self.st.add_signature(png, how)
            else:
                fd, self.path = tempfile.mkstemp(prefix="pdfmetry_sign_", suffix=".png")
                with os.fdopen(fd, "wb") as fp:
                    fp.write(png)
        else:
            it = self.list.currentItem()
            e = next((x for x in self.st.signatures() if it and x["id"] == it.data(Qt.ItemDataRole.UserRole)), None)
            if e is None:
                QMessageBox.information(self, "서명", "서명을 그리거나 입력하세요.")
                return
            self.path = self.st.image_path(e["file"])
        super().accept()
