/* PDF메트리 프린터 포트 모니터(PDFmetryMonitor.dll)

   「PDF메트리」 프린터 = 윈도우에 들어 있는 Microsoft Print To PDF 드라이버 + 이 포트.
   드라이버가 만든 PDF 바이트를 받아 C:\ProgramData\PDFmetry\Print\<작업번호>\<문서 이름>.pdf 에 쓰고,
   인쇄를 건 사람의 세션에서 PDF메트리를 그 파일로 켠다(앱은 그 폴더 파일을 「새 문서」로 연다).

   인쇄 스풀러(spoolsv.exe, SYSTEM) 안에서 돈다. 여기서 죽으면 스풀러가 같이 죽으니 짧고 단순하게 둔다.
   설치·등록은 app/printer.py(관리자 권한). 앱 경로는 레지스트리 모니터 열쇠의 App 값.
   빌드: build.py 가 gcc 로(gcc -O2 -shared -municode ... -lwinspool -lwtsapi32 -luserenv). */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winspool.h>
#include <winsplp.h>
#include <wtsapi32.h>
#include <userenv.h>
#include <strsafe.h>

#define PORT_NAME L"PDFmetry Port"
#define MON_DESC  L"PDFmetry Port"
#define MON_KEY   L"SYSTEM\\CurrentControlSet\\Control\\Print\\Monitors\\PDFmetry Monitor"
#define DEF_DIR   L"C:\\ProgramData\\PDFmetry\\Print"

typedef struct {
    HANDLE file;
    DWORD job;
    WCHAR printer[260];
    WCHAR path[MAX_PATH];
    WCHAR user[256];
} PORT;

static void reg_str(LPCWSTR name, LPWSTR out, DWORD cch, LPCWSTR fallback)
{
    DWORD cb = cch * sizeof(WCHAR);
    out[0] = 0;
    if (RegGetValueW(HKEY_LOCAL_MACHINE, MON_KEY, name, RRF_RT_REG_SZ, NULL, out, &cb) != ERROR_SUCCESS || !out[0]) {
        if (fallback) StringCchCopyW(out, cch, fallback);
        else out[0] = 0;
    }
}

/* 파일 이름에 못 쓰는 글자는 _ 로, 끝의 점·공백은 뗀다 */
static void clean_name(LPCWSTR src, LPWSTR out, size_t cch)
{
    size_t n = 0;
    if (src) {
        for (; *src && n + 1 < cch && n < 100; src++) {
            WCHAR c = *src;
            if (c < 32 || c == L'\\' || c == L'/' || c == L':' || c == L'*' || c == L'?' ||
                c == L'"' || c == L'<' || c == L'>' || c == L'|')
                c = L'_';
            out[n++] = c;
        }
    }
    while (n > 0 && (out[n - 1] == L'.' || out[n - 1] == L' ')) n--;
    out[n] = 0;
    if (!n) StringCchCopyW(out, cch, L"\xC778\xC1C4 \xBB38\xC11C");   /* 「인쇄 문서」 */
}

/* ── 포트 목록 ───────────────────────────────────────────── */
static BOOL WINAPI EnumPortsW_(HANDLE hMonitor, LPWSTR pName, DWORD Level, LPBYTE pPorts, DWORD cbBuf,
                               LPDWORD pcbNeeded, LPDWORD pcReturned)
{
    DWORD cbName = (DWORD)sizeof(PORT_NAME), cbDesc = (DWORD)sizeof(MON_DESC), need;
    (void)hMonitor; (void)pName;
    *pcReturned = 0;
    if (Level == 1) need = sizeof(PORT_INFO_1W) + cbName;
    else if (Level == 2) need = sizeof(PORT_INFO_2W) + cbName + cbDesc;
    else { SetLastError(ERROR_INVALID_LEVEL); return FALSE; }
    *pcbNeeded = need;
    if (!pPorts || cbBuf < need) { SetLastError(ERROR_INSUFFICIENT_BUFFER); return FALSE; }
    {
        LPBYTE end = pPorts + cbBuf;
        LPWSTR name, desc = NULL;
        end -= cbName; name = (LPWSTR)end; CopyMemory(name, PORT_NAME, cbName);
        if (Level == 2) { end -= cbDesc; desc = (LPWSTR)end; CopyMemory(desc, MON_DESC, cbDesc); }
        if (Level == 1) {
            PORT_INFO_1W *p = (PORT_INFO_1W *)pPorts;
            p->pName = name;
        } else {
            PORT_INFO_2W *p = (PORT_INFO_2W *)pPorts;
            p->pPortName = name; p->pMonitorName = desc; p->pDescription = desc;
            p->fPortType = PORT_TYPE_WRITE; p->Reserved = 0;
        }
    }
    *pcReturned = 1;
    return TRUE;
}

static BOOL WINAPI OpenPort_(HANDLE hMonitor, LPWSTR pName, PHANDLE pHandle)
{
    PORT *p;
    (void)hMonitor;
    if (!pName || lstrcmpiW(pName, PORT_NAME) != 0) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    p = (PORT *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(PORT));
    if (!p) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return FALSE; }
    p->file = INVALID_HANDLE_VALUE;
    *pHandle = (HANDLE)p;
    return TRUE;
}

/* 스풀러가 사용자를 흉내 내는 중일 수 있다. SYSTEM 으로 돌아가 일하고 끝나면 되돌린다 */
static HANDLE as_system(void)
{
    HANDLE t = NULL;
    if (!OpenThreadToken(GetCurrentThread(), TOKEN_IMPERSONATE, TRUE, &t)) t = NULL;
    RevertToSelf();
    return t;
}

static void restore(HANDLE t)
{
    if (t) { SetThreadToken(NULL, t); CloseHandle(t); }
}

static BOOL WINAPI StartDocPort_(HANDLE hPort, LPWSTR pPrinterName, DWORD JobId, DWORD Level, LPBYTE pDocInfo)
{
    PORT *p = (PORT *)hPort;
    WCHAR dir[MAX_PATH], sub[MAX_PATH], name[128];
    LPCWSTR doc = NULL;
    HANDLE hp = NULL, saved;
    if (!p) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    p->job = JobId; p->user[0] = 0;
    StringCchCopyW(p->printer, 260, pPrinterName ? pPrinterName : L"");
    if (pDocInfo && (Level == 1 || Level == 2)) doc = ((DOC_INFO_1W *)pDocInfo)->pDocName;
    if (pPrinterName && OpenPrinterW(pPrinterName, &hp, NULL)) {   /* 누가 걸었는지(세션 찾기용) */
        DWORD need = 0;
        GetJobW(hp, JobId, 1, NULL, 0, &need);
        if (need) {
            JOB_INFO_1W *ji = (JOB_INFO_1W *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, need);
            if (ji) {
                if (GetJobW(hp, JobId, 1, (LPBYTE)ji, need, &need) && ji->pUserName)
                    StringCchCopyW(p->user, 256, ji->pUserName);
                HeapFree(GetProcessHeap(), 0, ji);
            }
        }
        ClosePrinter(hp);
    }
    clean_name(doc, name, 128);
    saved = as_system();
    reg_str(L"Folder", dir, MAX_PATH, DEF_DIR);
    CreateDirectoryW(dir, NULL);
    StringCchPrintfW(sub, MAX_PATH, L"%s\\%lu-%lu", dir, (unsigned long)JobId, (unsigned long)GetTickCount());
    CreateDirectoryW(sub, NULL);
    StringCchPrintfW(p->path, MAX_PATH, L"%s\\%s.pdf", sub, name);
    p->file = CreateFileW(p->path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    restore(saved);
    if (p->file == INVALID_HANDLE_VALUE) return FALSE;
    return TRUE;
}

static BOOL WINAPI WritePort_(HANDLE hPort, LPBYTE pBuffer, DWORD cbBuf, LPDWORD pcbWritten)
{
    PORT *p = (PORT *)hPort;
    *pcbWritten = 0;
    if (!p || p->file == INVALID_HANDLE_VALUE) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    return WriteFile(p->file, pBuffer, cbBuf, pcbWritten, NULL);
}

static BOOL WINAPI ReadPort_(HANDLE hPort, LPBYTE pBuffer, DWORD cbBuffer, LPDWORD pcbRead)
{
    (void)hPort; (void)pBuffer; (void)cbBuffer;
    *pcbRead = 0;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

/* 인쇄를 건 사람의 세션: 이름이 같은 활성 세션, 없으면 콘솔 세션 */
static DWORD user_session(LPCWSTR user)
{
    DWORD sid = WTSGetActiveConsoleSessionId(), i, n = 0;
    WTS_SESSION_INFOW *list = NULL;
    if (user && user[0] && WTSEnumerateSessionsW(WTS_CURRENT_SERVER_HANDLE, 0, 1, &list, &n)) {
        for (i = 0; i < n; i++) {
            LPWSTR who = NULL; DWORD cb = 0;
            if (list[i].State != WTSActive) continue;
            if (WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, list[i].SessionId, WTSUserName, &who, &cb) && who) {
                BOOL same = lstrcmpiW(who, user) == 0;
                WTSFreeMemory(who);
                if (same) { sid = list[i].SessionId; break; }
            }
        }
        WTSFreeMemory(list);
    }
    return sid;
}

static void launch(PORT *p)
{
    WCHAR app[MAX_PATH], cmd[MAX_PATH * 2 + 8];
    HANDLE tok = NULL, prim = NULL;
    LPVOID env = NULL;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD sid;
    reg_str(L"App", app, MAX_PATH, NULL);
    if (!app[0] || GetFileAttributesW(app) == INVALID_FILE_ATTRIBUTES) return;
    sid = user_session(p->user);
    if (sid == 0xFFFFFFFF || !WTSQueryUserToken(sid, &tok)) return;
    if (!DuplicateTokenEx(tok, MAXIMUM_ALLOWED, NULL, SecurityIdentification, TokenPrimary, &prim)) { CloseHandle(tok); return; }
    CloseHandle(tok);
    if (!CreateEnvironmentBlock(&env, prim, FALSE)) env = NULL;
    StringCchPrintfW(cmd, MAX_PATH * 2 + 8, L"\"%s\" \"%s\"", app, p->path);
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si); si.lpDesktop = L"winsta0\\default";
    ZeroMemory(&pi, sizeof(pi));
    if (CreateProcessAsUserW(prim, app, cmd, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT | NORMAL_PRIORITY_CLASS,
                             env, NULL, &si, &pi)) {
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    }
    if (env) DestroyEnvironmentBlock(env);
    CloseHandle(prim);
}

static BOOL WINAPI EndDocPort_(HANDLE hPort)
{
    PORT *p = (PORT *)hPort;
    HANDLE hp = NULL, saved;
    LARGE_INTEGER size;
    BOOL ok = FALSE;
    if (!p) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    if (p->file != INVALID_HANDLE_VALUE) {
        size.QuadPart = 0;
        GetFileSizeEx(p->file, &size);
        CloseHandle(p->file);
        p->file = INVALID_HANDLE_VALUE;
        ok = size.QuadPart > 0;
    }
    /* 작업을 끝냈다고 알려야 스풀러가 대기열에서 지운다 */
    if (p->printer[0] && OpenPrinterW(p->printer, &hp, NULL)) {
        SetJobW(hp, p->job, 0, NULL, JOB_CONTROL_SENT_TO_PRINTER);
        ClosePrinter(hp);
    }
    saved = as_system();
    if (ok) launch(p);
    else if (p->path[0]) DeleteFileW(p->path);
    restore(saved);
    return TRUE;
}

static BOOL WINAPI ClosePort_(HANDLE hPort)
{
    PORT *p = (PORT *)hPort;
    if (p) {
        if (p->file != INVALID_HANDLE_VALUE) CloseHandle(p->file);
        HeapFree(GetProcessHeap(), 0, p);
    }
    return TRUE;
}

/* 포트 설정 창은 없다(관리 화면이 물으면 「지원 안 함」) */
static BOOL WINAPI XcvOpenPort_(HANDLE hMonitor, LPCWSTR pszObject, ACCESS_MASK GrantedAccess, PHANDLE phXcv)
{
    (void)hMonitor; (void)pszObject; (void)GrantedAccess;
    *phXcv = (HANDLE)1;
    return TRUE;
}

static DWORD WINAPI XcvDataPort_(HANDLE hXcv, LPCWSTR pszDataName, PBYTE pInputData, DWORD cbInputData,
                                 PBYTE pOutputData, DWORD cbOutputData, PDWORD pcbOutputNeeded)
{
    (void)hXcv; (void)pszDataName; (void)pInputData; (void)cbInputData; (void)pOutputData; (void)cbOutputData;
    if (pcbOutputNeeded) *pcbOutputNeeded = 0;
    return ERROR_NOT_SUPPORTED;
}

static BOOL WINAPI XcvClosePort_(HANDLE hXcv)
{
    (void)hXcv;
    return TRUE;
}

static VOID WINAPI Shutdown_(HANDLE hMonitor)
{
    (void)hMonitor;
}

static MONITOR2 g_mon;

__declspec(dllexport) LPMONITOR2 WINAPI InitializePrintMonitor2(PMONITORINIT pMonitorInit, PHANDLE phMonitor)
{
    ZeroMemory(&g_mon, sizeof(g_mon));
    g_mon.cbSize = sizeof(MONITOR2);
    g_mon.pfnEnumPorts = EnumPortsW_;
    g_mon.pfnOpenPort = OpenPort_;
    g_mon.pfnStartDocPort = StartDocPort_;
    g_mon.pfnWritePort = WritePort_;
    g_mon.pfnReadPort = ReadPort_;
    g_mon.pfnEndDocPort = EndDocPort_;
    g_mon.pfnClosePort = ClosePort_;
    g_mon.pfnXcvOpenPort = XcvOpenPort_;
    g_mon.pfnXcvDataPort = XcvDataPort_;
    g_mon.pfnXcvClosePort = XcvClosePort_;
    g_mon.pfnShutdown = Shutdown_;
    *phMonitor = (HANDLE)pMonitorInit;
    return &g_mon;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r)
{
    (void)r;
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(h);
    return TRUE;
}
