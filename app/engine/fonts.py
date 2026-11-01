"""한글 글꼴 찾기. fitz 기본 글꼴은 한글이 없어서 맑은 고딕 파일을 직접 준다."""
from __future__ import annotations

import os

import fitz

_CANDIDATES = [
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\NanumGothic.ttf",
    r"C:\Windows\Fonts\gulim.ttc",
]
_BOLD = [r"C:\Windows\Fonts\malgunbd.ttf"]


def korean_fontfile(bold: bool = False) -> str:
    for p in (_BOLD if bold else []) + _CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("한글 글꼴(맑은 고딕)을 찾지 못했습니다")


def korean_font(bold: bool = False) -> fitz.Font:
    return fitz.Font(fontfile=korean_fontfile(bold))


# ── 윈도우 글꼴 목록(꾸미기 글꼴 칸) ─────────────────────────────────────
# 레지스트리의 글꼴 표(이름 → 파일)를 읽는다. 파일을 하나씩 열어 이름을 읽으면 수백 개라 창이 늦게 뜬다.
# 「Arial Bold Italic (TrueType)」 처럼 이름 끝의 굵게·기울임 낱말로 한 집안(family)의 네 모양을 묶는다.
# .ttc 는 첫 글꼴만 쓸 수 있어서(PyMuPDF 가 글꼴 차례를 못 받음) 「A & B」 이름의 첫 이름만 둔다.
_FONT_EXT = (".ttf", ".otf", ".ttc")
_STYLES = (("bold italic", (True, True)), ("bold oblique", (True, True)), ("italic", (False, True)),
           ("oblique", (False, True)), ("bold", (True, False)), ("regular", (False, False)))
_FAMILIES: dict[str, dict[tuple[bool, bool], str]] = {}
DEFAULT_FAMILY = "Malgun Gothic"   # 글꼴 칸이 비었을 때(옛 설정 포함) 쓰는 글꼴


def _registry_fonts() -> list[tuple[str, str]]:
    import winreg
    out = []
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        with key:
            k = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, k)
                except OSError:
                    break
                k += 1
                if isinstance(val, str):
                    out.append((name, val if os.path.isabs(val) else os.path.join(fonts_dir, val)))
    return out


def _split_style(name: str) -> tuple[str, tuple[bool, bool]]:
    low = name.lower()
    for word, style in _STYLES:
        if low.endswith(" " + word):
            return name[: -len(word) - 1].strip(), style
    return name, (False, False)


def _load_families() -> dict[str, dict[tuple[bool, bool], str]]:
    if _FAMILIES:
        return _FAMILIES
    try:
        entries = _registry_fonts()
    except Exception:  # noqa: BLE001  레지스트리를 못 읽으면 글꼴 폴더 파일 이름으로
        entries = []
    if not entries:
        d = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        entries = [(os.path.splitext(f)[0], os.path.join(d, f)) for f in os.listdir(d)] if os.path.isdir(d) else []
    for name, path in entries:
        if not path.lower().endswith(_FONT_EXT) or not os.path.exists(path):
            continue
        name = name.split("(")[0].split("&")[0].strip()
        if not name:
            continue
        fam, style = _split_style(name)
        _FAMILIES.setdefault(fam, {}).setdefault(style, path)
    for fam in [f for f, s in _FAMILIES.items() if (False, False) not in s]:   # 보통 모양이 없는 집안은 있는 것 하나를 보통으로
        st = _FAMILIES[fam]
        st[(False, False)] = st[sorted(st)[0]]
    return _FAMILIES


def font_families() -> list[str]:
    """고를 수 있는 글꼴 이름(가나다·ABC 차례)."""
    return sorted(_load_families(), key=str.lower)


def font_file(family: str = "", bold: bool = False, italic: bool = False) -> tuple[str, bool, bool]:
    """글꼴 이름 + 모양 → (파일 경로, 굵게를 흉내 낼지, 기울임을 흉내 낼지).

    굵은·기울인 파일이 있으면 그 파일을, 없으면 보통 파일을 주고 흉내를 낸다(획 두께·기울여 그리기).
    모르는 이름이나 빈 이름이면 맑은 고딕."""
    fams = _load_families()
    styles = fams.get(family or DEFAULT_FAMILY) or fams.get(DEFAULT_FAMILY)
    if not styles:
        return korean_fontfile(bold), False, italic
    for want in ((bold, italic), (bold, False), (False, italic), (False, False)):
        if want in styles:
            return styles[want], bold and not want[0], italic and not want[1]
    return korean_fontfile(bold), False, italic


_GLYPHS: dict[str, fitz.Font] = {}


def font_object(path: str) -> fitz.Font:
    """파일 하나의 fitz.Font(한 번 읽어 둠)."""
    f = _GLYPHS.get(path)
    if f is None:
        f = _GLYPHS[path] = fitz.Font(fontfile=path)
    return f


def covers(path: str, text: str) -> bool:
    """글꼴 파일이 text 의 글자를 다 가졌는지(빈칸·줄바꿈 빼고)."""
    f = font_object(path)
    return all(f.has_glyph(ord(c)) for c in text if not c.isspace())
