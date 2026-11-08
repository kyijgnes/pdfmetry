"""「PDF메트리」 프린터: 어느 프로그램에서든 인쇄하면 PDF메트리에 새 문서로 열린다(알PDF 프린터처럼).

짜임
- 프린터 「PDF메트리」 = 윈도우에 들어 있는 Microsoft Print To PDF 드라이버 + 로컬 포트 \\\\.\\pipe\\PDFmetryPrint.
  (이 드라이버에는 다른 포트 모니터를 못 붙인다. 11-08 우리 모니터 DLL 로 해 보다 인쇄 서비스가 거절해 바꿈)
- 수신기 printer/listener.c(PDFmetryPrint.exe, 창 없음)가 로그인 때 떠서 파이프를 열어 두고, 인쇄가 들어오면
  PRINT_DIR\\<번호>\\<문서 이름>.pdf 로 받아 앱을 그 파일로 켠다. 앱은 새 문서로 열고 파일을 지운다(window.open_printed).
- 수신기는 앱 폴더 밖(LISTENER_HOME)에 복사해 띄운다. 앱 폴더에 두면 업데이트가 _internal 을 못 지운다.

설치·제거(프린터·포트)는 관리자 권한이 든다: `<exe> --printer-install` 을 관리자로 띄운다(설치 프로그램·환경 설정).
수신기 등록·시작(`--printer-start`)과 멈춤(`--printer-stop`)은 보통 권한으로.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

PRINTER_NAME = "PDF메트리"
PORT_NAME = r"\\.\pipe\PDFmetryPrint"
PIPE_LEAF = "PDFmetryPrint"
DRIVER_NAME = "Microsoft Print To PDF"
LISTENER = "PDFmetryPrint.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "PDFmetryPrint"
PRINT_DIR = os.path.join(tempfile.gettempdir(), "PDFmetry print")   # 수신기가 받는 자리(GetTempPath 와 같다)
LISTENER_HOME = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "PDFmetry", "printer")
INSTALL_FLAG, UNINSTALL_FLAG = "--printer-install", "--printer-uninstall"
START_FLAG, STOP_FLAG = "--printer-start", "--printer-stop"
FLAGS = (INSTALL_FLAG, UNINSTALL_FLAG, START_FLAG, STOP_FLAG)
# 11-08 처음 시도(포트 모니터 DLL)가 남긴 것. 설치·제거 때 걷는다
OLD_MONITOR = "PDFmetry Monitor"
OLD_DLL = "PDFmetryMonitor.dll"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "printer", "listener.c")


# ── 앱 쪽: 인쇄로 들어온 파일 ─────────────────────────────────
def is_printed(path: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(PRINT_DIR) + os.sep)
    except (TypeError, ValueError):
        return False


_PREFIX = re.compile(r"^(Microsoft (Word|Excel|PowerPoint)|한컴오피스 한글|Hwp)\s*-\s*", re.I)
_EXT = re.compile(r"\.(xlsx?|xlsm|csv|docx?|pptx?|hwpx?|txt|pdf|html?|png|jpe?g|gif|bmp)$", re.I)


def printed_title(path: str) -> str:
    """「Microsoft Excel - 수학반 진도표.xlsx」 → 「수학반 진도표.pdf」."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = _EXT.sub("", _PREFIX.sub("", stem.strip())).strip() or "인쇄 문서"
    return stem + ".pdf"


def take_printed(path: str) -> bytes:
    """인쇄 파일을 읽고 그 작업 폴더째 지운다."""
    with open(path, "rb") as f:
        data = f.read()
    folder = os.path.dirname(os.path.abspath(path))
    if os.path.normcase(os.path.dirname(folder)) == os.path.normcase(PRINT_DIR):
        shutil.rmtree(folder, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass
    return data


# ── 상태 ──────────────────────────────────────────────────────
def installed() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Print\Printers" + "\\" + PRINTER_NAME):
            return True
    except OSError:
        return False


def listening() -> bool:
    """수신기가 파이프를 열어 두었나(파이프 목록만 본다. 열어 보면 그 자리를 써 버린다)."""
    try:
        return PIPE_LEAF.lower() in (n.lower() for n in os.listdir("\\\\.\\pipe\\"))
    except OSError:
        return False


# ── 수신기 빌드·위치 ──────────────────────────────────────────
def build_listener(out: str) -> str:
    """printer/listener.c → PDFmetryPrint.exe(64비트, 창 없음, gcc). build.py 도 이것을 부른다."""
    gcc = shutil.which("gcc")
    if not gcc:
        raise RuntimeError("gcc 가 없습니다(프린터 수신기를 지을 수 없음)")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # gcc·ld 는 한글 경로를 못 연다(이 저장소는 「코딩\14. 도구」 아래). 영문 임시 폴더에서 짓고 옮긴다
    tmp = tempfile.mkdtemp(prefix="pdfmetry_print_")
    try:
        src, exe = os.path.join(tmp, "listener.c"), os.path.join(tmp, LISTENER)
        shutil.copyfile(SOURCE, src)
        cmd = [gcc, "-O2", "-Wall", "-municode", "-mwindows", "-static", "-static-libgcc", "-s",
               "-o", exe, src, "-lwinspool", "-lshell32"]
        subprocess.run(cmd, check=True, cwd=tmp)
        shutil.copyfile(exe, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def bundled_listener() -> str | None:
    """설치본은 _internal\\printer, 소스 실행은 build\\printer(없거나 낡았으면 gcc 로 짓는다)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "printer", LISTENER)
        return p if os.path.isfile(p) else None
    out = os.path.join(ROOT, "build", "printer", LISTENER)
    if not os.path.isfile(out) or os.path.getmtime(out) < os.path.getmtime(SOURCE):
        try:
            build_listener(out)
        except Exception:  # noqa: BLE001
            return None
    return out


def app_exe() -> str:
    """인쇄 뒤 수신기가 켤 프로그램. 설치본은 이 exe, 소스 실행은 설치된 PDF메트리."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "PDFmetry", "PDFmetry.exe")


def _kill_listener():
    subprocess.run(["taskkill", "/F", "/IM", LISTENER], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ensure_listener(app: str | None = None) -> bool:
    """수신기를 앱 밖 자리에 (새것으로) 두고, 로그인 때 뜨게 등록하고, 안 돌고 있으면 띄운다. 띄웠거나 돌고 있으면 True."""
    import winreg
    src = bundled_listener()
    if not src:
        return False
    app = app or app_exe()
    dst = os.path.join(LISTENER_HOME, LISTENER)
    os.makedirs(LISTENER_HOME, exist_ok=True)
    same = os.path.isfile(dst) and open(src, "rb").read() == open(dst, "rb").read()
    if not same:
        if listening():
            _kill_listener()
        for _ in range(20):
            try:
                shutil.copyfile(src, dst); break
            except PermissionError:
                import time
                time.sleep(0.2)
        else:
            return False
    cmd = f'"{dst}" "{app}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
    if listening():
        return True
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    for extra in (0x01000000, 0):     # 앱이 꺼져도 같이 안 죽게 job 밖으로(안 되면 그냥)
        try:
            subprocess.Popen([dst, app], creationflags=flags | extra, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


def stop_listener():
    """수신기를 끄고 로그인 등록과 복사본을 지운다(프린터를 지울 때·앱을 제거할 때)."""
    import winreg
    _kill_listener()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
    except OSError:
        pass
    shutil.rmtree(LISTENER_HOME, ignore_errors=True)
    shutil.rmtree(PRINT_DIR, ignore_errors=True)


def app_command() -> tuple[str, str]:
    """관리자로 띄울 (프로그램, 인자 머리). 설치본은 exe, 소스 실행은 python -m app.main."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return sys.executable, "-m app.main"


def run_elevated(flag: str) -> int:
    """UAC 를 거쳐 `<앱> --printer-install` 을 띄우고 끝날 때까지 기다린다. 끝 코드(거절하면 1223)."""
    import ctypes
    from ctypes import wintypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    exe, head = app_command()
    params = " ".join(x for x in (head, flag) if x)
    sei = SEI(cbSize=ctypes.sizeof(SEI), fMask=0x40, lpVerb="runas", lpFile=exe, lpParameters=params,
              lpDirectory=ROOT if not getattr(sys, "frozen", False) else None, nShow=0)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return ctypes.GetLastError() or 1
    k = ctypes.windll.kernel32
    k.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    k.GetExitCodeProcess(sei.hProcess, ctypes.byref(code)); k.CloseHandle(sei.hProcess)
    return code.value


# ── 관리자 권한으로 도는 쪽 ───────────────────────────────────
def _winspool():
    import ctypes
    from ctypes import wintypes
    w = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class PRINTER_INFO_2(ctypes.Structure):
        _fields_ = [(n, wintypes.LPWSTR) for n in ("pServerName", "pPrinterName", "pShareName", "pPortName", "pDriverName",
                                                   "pComment", "pLocation")] + \
                   [("pDevMode", ctypes.c_void_p), ("pSepFile", wintypes.LPWSTR), ("pPrintProcessor", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR), ("pParameters", wintypes.LPWSTR), ("pSecurityDescriptor", ctypes.c_void_p)] + \
                   [(n, wintypes.DWORD) for n in ("Attributes", "Priority", "DefaultPriority", "StartTime", "UntilTime",
                                                  "Status", "cJobs", "AveragePPM")]

    class PRINTER_DEFAULTS(ctypes.Structure):
        _fields_ = [("pDatatype", wintypes.LPWSTR), ("pDevMode", ctypes.c_void_p), ("DesiredAccess", wintypes.DWORD)]

    w.DeleteMonitorW.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR, wintypes.LPWSTR]
    w.AddPrinterW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p]
    w.AddPrinterW.restype = wintypes.HANDLE
    w.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    w.DeletePrinter.argtypes = [wintypes.HANDLE]
    w.ClosePrinter.argtypes = [wintypes.HANDLE]
    w.XcvDataW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    w.PRINTER_INFO_2, w.PRINTER_DEFAULTS = PRINTER_INFO_2, PRINTER_DEFAULTS
    return w


def _local_port(w, verb: str) -> int:
    """로컬 포트 모니터에 「AddPort」·「DeletePort」. 끝 상태(0 = 됨)."""
    import ctypes
    from ctypes import wintypes
    pd = w.PRINTER_DEFAULTS(None, None, 1)   # SERVER_ACCESS_ADMINISTER
    h = wintypes.HANDLE()
    if not w.OpenPrinterW(",XcvMonitor Local Port", ctypes.byref(h), ctypes.byref(pd)):
        return ctypes.get_last_error() or 1
    try:
        buf = ctypes.create_unicode_buffer(PORT_NAME)
        need, status = wintypes.DWORD(), wintypes.DWORD()
        if not w.XcvDataW(h, verb, buf, ctypes.sizeof(buf), None, 0, ctypes.byref(need), ctypes.byref(status)):
            return ctypes.get_last_error() or 1
        return status.value
    finally:
        w.ClosePrinter(h)


def _delete_printer(w):
    import ctypes
    from ctypes import wintypes
    pd = w.PRINTER_DEFAULTS(None, None, 0x000F000C)   # PRINTER_ALL_ACCESS
    hp = wintypes.HANDLE()
    if w.OpenPrinterW(PRINTER_NAME, ctypes.byref(hp), ctypes.byref(pd)):
        w.DeletePrinter(hp); w.ClosePrinter(hp)


def _drop_old_monitor(w):
    """11-08 첫 시도(포트 모니터 DLL)가 남긴 등록·파일."""
    import ctypes
    w.DeleteMonitorW(None, None, OLD_MONITOR)
    dst = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", OLD_DLL)
    if os.path.isfile(dst):
        try:
            os.remove(dst)
        except OSError:   # 스풀러가 쥐고 있으면 다시 켤 때 지운다
            ctypes.windll.kernel32.MoveFileExW(dst, None, 4)   # MOVEFILE_DELAY_UNTIL_REBOOT


def install() -> None:
    import ctypes
    w = _winspool()
    _drop_old_monitor(w)
    st = _local_port(w, "AddPort")
    if st not in (0, 183):   # 183 = 이미 있음
        raise OSError(st, f"포트 추가 실패({st})")
    if not installed():
        pi = w.PRINTER_INFO_2()
        pi.pPrinterName, pi.pPortName, pi.pDriverName = PRINTER_NAME, PORT_NAME, DRIVER_NAME
        pi.pPrintProcessor, pi.pDatatype = "winprint", "RAW"
        pi.pComment = "PDF메트리로 PDF 만들기"
        pi.Attributes = 0x40   # PRINTER_ATTRIBUTE_LOCAL
        h = w.AddPrinterW(None, 2, ctypes.byref(pi))
        if not h:
            err = ctypes.get_last_error()
            raise OSError(err, f"프린터 추가 실패({err})")
        w.ClosePrinter(h)


def uninstall() -> None:
    w = _winspool()
    if installed():
        _delete_printer(w)
    _local_port(w, "DeletePort")
    _drop_old_monitor(w)


def cli(argv: list[str]) -> int:
    """창 없이 도는 프린터 일. 끝 코드 0 = 됨. 실패는 %TEMP%\\PDFmetry-printer.log 에 한 줄."""
    log = os.path.join(tempfile.gettempdir(), "PDFmetry-printer.log")
    try:
        if INSTALL_FLAG in argv:
            install()
        elif UNINSTALL_FLAG in argv:
            uninstall()
        elif START_FLAG in argv:
            if installed() and not ensure_listener():
                raise RuntimeError("수신기를 띄우지 못함")
        elif STOP_FLAG in argv:
            stop_listener()
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{argv}: {e!r}\n")
        except OSError:
            pass
        return 2
