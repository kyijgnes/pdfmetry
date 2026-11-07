"""시험마다 끝에 창·대화상자를 실제로 지운다.

창(MainWindow)은 자기 자식 신호에 self 를 붙잡은 람다를 여럿 잇는다. 그 고리는 Qt 연결 안에 있어
파이썬 gc 가 못 걷고, 창을 close() 해도 C++ 위젯이 그대로 남는다. 한 프로세스로 전체 시험을 돌리면
파일마다 위젯이 수천 개씩 쌓여 뒤쪽 시험이 느려지고, 끝내 앱 스타일시트를 다시 칠하다(apply_theme) 죽었다.
그래서 시험 하나가 끝날 때마다 남은 맨 위 위젯을 닫고, 지우기 대기(DeferredDelete)를 바로 처리한다.
(이벤트 루프 밖 processEvents 는 deleteLater 를 처리하지 않는다.)
"""
from __future__ import annotations

import gc
import os

import pytest


def _app():
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    return QApplication.instance()


def sweep():
    """남은 맨 위 위젯을 닫고 지운다. 지운 뒤 남은 위젯 수를 돌려준다."""
    app = _app()
    if app is None:
        return 0
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    try:
        from app.ui import tab_detach
    except Exception:
        tab_detach = None
    try:
        QApplication.clipboard().clear()   # offscreen Qt 는 HTML 클립보드를 남기면 죽는다
    except Exception:
        pass
    tops = [w for w in QApplication.topLevelWidgets() if shiboken6.isValid(w) and w.parent() is None]
    for w in tops:
        for t in list(getattr(w, "tabs", None) or []):   # 저장을 묻지 않게
            try:
                t.doc.modified = False
            except Exception:
                pass
        if w.isVisible():
            try:
                w.close()
            except Exception:
                pass
    if tab_detach is not None:
        tab_detach.windows.clear(); tab_detach._closed.clear()
        tab_detach._last = None; tab_detach._drag = None
    app.processEvents()
    for w in tops:
        if shiboken6.isValid(w):
            w.deleteLater()
    for _ in range(3):   # 지우다 새로 생긴 지우기 대기까지
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    gc.collect()
    return len(QApplication.allWidgets())


@pytest.fixture(autouse=True)
def _sweep_widgets():
    yield
    n = sweep()
    if os.environ.get("RPDF_WIDGET_LOG"):
        with open(os.environ["RPDF_WIDGET_LOG"], "a", encoding="utf-8") as f:
            f.write(f"{os.environ.get('PYTEST_CURRENT_TEST', '?').split(' ')[0]}\t{n}\n")
