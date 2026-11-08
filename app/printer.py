"""「PDF메트리」 프린터: 어느 프로그램에서든 인쇄하면 PDF메트리에 새 문서로 열린다(알PDF 프린터처럼).

짜임
- 프린터 「PDF메트리」 = 윈도우에 들어 있는 Microsoft Print To PDF 드라이버 + 우리 포트 「PDFmetry Port」.
- 포트 모니터 printer/monitor.c(PDFmetryMonitor.dll, System32)가 드라이버가 만든 PDF 를
  PRINT_DIR\\<작업>\\<문서 이름>.pdf 에 쓰고 인쇄를 건 사람의 세션에서 앱을 그 파일로 켠다.
- 앱은 그 폴더 파일을 「새 문서」(저장할 때 위치를 묻는다)로 열고 파일은 지운다(window.open_path).

설치·제거는 관리자 권한이 든다. 설치 프로그램과 환경 설정이 `<exe> --printer-install` 을 관리자로 띄운다.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

PRINTER_NAME = "PDF메트리"
PORT_NAME = "PDFmetry Port"
MONITOR_NAME = "PDFmetry Monitor"
DLL_NAME = "PDFmetryMonitor.dll"
DRIVER_NAME = "Microsoft Print To PDF"
MON_KEY = r"SYSTEM\CurrentControlSet\Control\Print\Monitors" + "\\" + MONITOR_NAME
PRINT_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "PDFmetry", "Print")
INSTALL_FLAG, UNINSTALL_FLAG = "--printer-install", "--printer-uninstall"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "printer", "monitor.c")


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
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MON_KEY):
            pass
    except OSError:
        return False
    return _printer_exists()


def _printer_exists() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Print\Printers" + "\\" + PRINTER_NAME):
            return True
    except OSError:
        return False


def dll_source() -> str | None:
    """설치본은 exe 옆 _internal\\printer, 소스 실행은 build\\printer(없으면 gcc 로 짓는다)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "printer", DLL_NAME)
        return p if os.path.isfile(p) else None
    out = os.path.join(ROOT, "build", "printer", DLL_NAME)
    if not os.path.isfile(out) or os.path.getmtime(out) < os.path.getmtime(SOURCE):
        try:
            build_dll(out)
        except Exception:  # noqa: BLE001
            return None
    return out


def build_dll(out: str) -> str:
    """printer/monitor.c → PDFmetryMonitor.dll(64비트, gcc). build.py 도 이것을 부른다."""
    gcc = shutil.which("gcc")
    if not gcc:
        raise RuntimeError("gcc 가 없습니다(프린터 모니터 DLL 을 지을 수 없음)")
    import tempfile
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # gcc·ld 는 한글 경로를 못 연다(이 저장소는 「코딩\14. 도구」 아래). 영문 임시 폴더에서 짓고 옮긴다
    tmp = tempfile.mkdtemp(prefix="pdfmetry_mon_")
    try:
        src, dll = os.path.join(tmp, "monitor.c"), os.path.join(tmp, DLL_NAME)
        shutil.copyfile(SOURCE, src)
        cmd = [gcc, "-O2", "-Wall", "-shared", "-municode", "-static", "-static-libgcc", "-s",
               "-o", dll, src, "-lwinspool", "-lwtsapi32", "-luserenv", "-ladvapi32"]
        subprocess.run(cmd, check=True, cwd=tmp)
        shutil.copyfile(dll, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def app_command() -> tuple[str, str]:
    """관리자로 띄울 (프로그램, 인자 머리). 설치본은 exe, 소스 실행은 python -m app.main."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return sys.executable, "-m app.main"


def run_elevated(flag: str, app: str | None = None) -> int:
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
    params = " ".join(x for x in (head, flag, f'--app "{app}"' if app else "") if x)
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

    class MONITOR_INFO_2(ctypes.Structure):
        _fields_ = [("pName", wintypes.LPWSTR), ("pEnvironment", wintypes.LPWSTR), ("pDLLName", wintypes.LPWSTR)]

    class PRINTER_INFO_2(ctypes.Structure):
        _fields_ = [(n, wintypes.LPWSTR) for n in ("pServerName", "pPrinterName", "pShareName", "pPortName", "pDriverName",
                                                   "pComment", "pLocation")] + \
                   [("pDevMode", ctypes.c_void_p), ("pSepFile", wintypes.LPWSTR), ("pPrintProcessor", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR), ("pParameters", wintypes.LPWSTR), ("pSecurityDescriptor", ctypes.c_void_p)] + \
                   [(n, wintypes.DWORD) for n in ("Attributes", "Priority", "DefaultPriority", "StartTime", "UntilTime",
                                                  "Status", "cJobs", "AveragePPM")]

    class PRINTER_DEFAULTS(ctypes.Structure):
        _fields_ = [("pDatatype", wintypes.LPWSTR), ("pDevMode", ctypes.c_void_p), ("DesiredAccess", wintypes.DWORD)]

    w.AddMonitorW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p]
    w.DeleteMonitorW.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR, wintypes.LPWSTR]
    w.AddPrinterW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p]
    w.AddPrinterW.restype = wintypes.HANDLE
    w.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    w.DeletePrinter.argtypes = [wintypes.HANDLE]
    w.ClosePrinter.argtypes = [wintypes.HANDLE]
    w.MONITOR_INFO_2, w.PRINTER_INFO_2, w.PRINTER_DEFAULTS = MONITOR_INFO_2, PRINTER_INFO_2, PRINTER_DEFAULTS
    return w


def _restart_spooler():
    for verb in ("stop", "start"):
        subprocess.run(["sc.exe", verb, "Spooler"], capture_output=True)
        if verb == "stop":
            import time
            for _ in range(40):
                r = subprocess.run(["sc.exe", "query", "Spooler"], capture_output=True, text=True, errors="replace")
                if "STOPPED" in r.stdout:
                    break
                time.sleep(0.25)


def _copy_dll(src: str) -> None:
    dst = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", DLL_NAME)
    if os.path.isfile(dst):
        with open(src, "rb") as a, open(dst, "rb") as b:
            if a.read() == b.read():
                return
    try:
        shutil.copyfile(src, dst)
    except PermissionError:   # 스풀러가 옛 DLL 을 쥐고 있다
        subprocess.run(["sc.exe", "stop", "Spooler"], capture_output=True)
        import time
        for _ in range(40):
            try:
                shutil.copyfile(src, dst); break
            except PermissionError:
                time.sleep(0.25)
        else:
            raise
        subprocess.run(["sc.exe", "start", "Spooler"], capture_output=True)
        time.sleep(1.0)


def install(app: str) -> None:
    import ctypes
    import winreg
    src = dll_source()
    if not src:
        raise RuntimeError("프린터 모니터 DLL 을 찾지 못했습니다")
    _copy_dll(src)
    os.makedirs(PRINT_DIR, exist_ok=True)
    # 인쇄 파일은 스풀러(SYSTEM)가 쓰고 사용자가 읽고 지운다
    subprocess.run(["icacls", PRINT_DIR, "/grant", "*S-1-5-32-545:(OI)(CI)M"], capture_output=True)
    w = _winspool()
    mi = w.MONITOR_INFO_2(MONITOR_NAME, None, DLL_NAME)
    if not w.AddMonitorW(None, 2, ctypes.byref(mi)):
        err = ctypes.get_last_error()
        if err != 3006:   # ERROR_PRINT_MONITOR_ALREADY_INSTALLED
            raise OSError(err, f"AddMonitor 실패({err})")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, MON_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, "App", 0, winreg.REG_SZ, app)
        winreg.SetValueEx(k, "Folder", 0, winreg.REG_SZ, PRINT_DIR)
    if not _printer_exists():
        pi = w.PRINTER_INFO_2()
        pi.pPrinterName, pi.pPortName, pi.pDriverName = PRINTER_NAME, PORT_NAME, DRIVER_NAME
        pi.pPrintProcessor, pi.pDatatype = "winprint", "RAW"
        pi.pComment = "PDF메트리로 PDF 만들기"
        pi.Attributes = 0x40   # PRINTER_ATTRIBUTE_LOCAL
        h = w.AddPrinterW(None, 2, ctypes.byref(pi))
        if not h:
            err = ctypes.get_last_error()
            raise OSError(err, f"AddPrinter 실패({err})")
        w.ClosePrinter(h)


def uninstall() -> None:
    import ctypes
    import winreg
    w = _winspool()
    if _printer_exists():
        from ctypes import wintypes
        pd = w.PRINTER_DEFAULTS(None, None, 0x000F000C)   # PRINTER_ALL_ACCESS
        hp = wintypes.HANDLE()
        if w.OpenPrinterW(PRINTER_NAME, ctypes.byref(hp), ctypes.byref(pd)):
            w.DeletePrinter(hp); w.ClosePrinter(hp)
    if not w.DeleteMonitorW(None, None, MONITOR_NAME):
        err = ctypes.get_last_error()
        if err not in (3000, 1801, 2):   # 이미 없음
            _restart_spooler()   # 지운 프린터를 스풀러가 아직 쥐고 있으면 다시 켠 뒤 한 번 더
            w.DeleteMonitorW(None, None, MONITOR_NAME)
    try:
        winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, MON_KEY, winreg.KEY_WOW64_64KEY)
    except OSError:
        pass
    dst = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", DLL_NAME)
    try:
        os.remove(dst)
    except OSError:   # 스풀러가 쥐고 있으면 다시 켤 때 지운다
        try:
            ctypes.windll.kernel32.MoveFileExW(dst, None, 4)   # MOVEFILE_DELAY_UNTIL_REBOOT
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(os.path.dirname(PRINT_DIR), ignore_errors=True)


def cli(argv: list[str]) -> int:
    """`--printer-install [--app 경로]` · `--printer-uninstall`. 관리자 권한으로 불린다. 끝 코드 0 = 됨."""
    log = os.path.join(os.environ.get("TEMP", "."), "PDFmetry-printer.log")
    try:
        if INSTALL_FLAG in argv:
            app = argv[argv.index("--app") + 1] if "--app" in argv else None
            if not app:
                app = sys.executable if getattr(sys, "frozen", False) else ""
            install(app)
        else:
            uninstall()
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{argv}: {e!r}\n")
        except OSError:
            pass
        return 2
