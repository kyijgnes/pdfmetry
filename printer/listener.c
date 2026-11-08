/* PDF메트리 프린터 수신기(PDFmetryPrint.exe, 창 없음)

   「PDF메트리」 프린터 = 윈도우에 들어 있는 Microsoft Print To PDF 드라이버 + 로컬 포트 \\.\pipe\PDFmetryPrint.
   (이 드라이버는 다른 포트 모니터를 못 붙이게 막혀 있어 로컬 포트를 파이프로 쓴다, 11-08)
   로그인할 때 이 프로그램이 떠서 파이프를 열어 두고, 인쇄가 들어오면 PDF 를
   %TEMP%\PDFmetry print\<번호>\<문서 이름>.pdf 에 받아 PDF메트리를 그 파일로 켠다(앱은 새 문서로 열고 파일을 지운다).

   쓰는 법: PDFmetryPrint.exe "<PDFmetry.exe 경로>"   (경로를 안 주면 ..\..\PDFmetry.exe, 설치본 자리)
   하나만 돈다(파이프를 먼저 연 쪽). 등록·시작은 app/printer.py. 빌드: build.py 가 gcc 로. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winspool.h>
#include <shellapi.h>
#include <strsafe.h>

#define PIPE_NAME L"\\\\.\\pipe\\PDFmetryPrint"
#define PRINTER   L"PDF\xBA54\xD2B8\xB9AC"   /* 「PDF메트리」 */

static WCHAR g_app[MAX_PATH];
static LONG g_seq;

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

/* 지금 「PDF메트리」 프린터로 나가는 작업의 문서 이름 */
static void current_doc(LPWSTR out, size_t cch)
{
    HANDLE hp = NULL;
    DWORD need = 0, n = 0, i;
    out[0] = 0;
    if (!OpenPrinterW(PRINTER, &hp, NULL)) return;
    EnumJobsW(hp, 0, 32, 1, NULL, 0, &need, &n);
    if (need) {
        JOB_INFO_1W *jobs = (JOB_INFO_1W *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, need);
        if (jobs) {
            if (EnumJobsW(hp, 0, 32, 1, (LPBYTE)jobs, need, &need, &n) && n) {
                JOB_INFO_1W *pick = &jobs[0];
                for (i = 0; i < n; i++)
                    if (jobs[i].Status & JOB_STATUS_PRINTING) { pick = &jobs[i]; break; }
                if (pick->pDocument) StringCchCopyW(out, cch, pick->pDocument);
            }
            HeapFree(GetProcessHeap(), 0, jobs);
        }
    }
    ClosePrinter(hp);
}

static DWORD WINAPI serve(LPVOID arg)
{
    HANDLE pipe = (HANDLE)arg, file;
    WCHAR dir[MAX_PATH], sub[MAX_PATH], doc[260], name[128], path[MAX_PATH];
    BYTE buf[65536];
    DWORD got, put;
    LONGLONG total = 0;
    current_doc(doc, 260);
    clean_name(doc, name, 128);
    GetTempPathW(MAX_PATH, dir);
    StringCchCatW(dir, MAX_PATH, L"PDFmetry print");
    CreateDirectoryW(dir, NULL);
    StringCchPrintfW(sub, MAX_PATH, L"%s\\%lu-%ld", dir, (unsigned long)GetTickCount(), (long)InterlockedIncrement(&g_seq));
    CreateDirectoryW(sub, NULL);
    StringCchPrintfW(path, MAX_PATH, L"%s\\%s.pdf", sub, name);
    file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    for (;;) {   /* 스풀러가 포트를 닫으면(작업 끝) ERROR_BROKEN_PIPE */
        if (!ReadFile(pipe, buf, sizeof(buf), &got, NULL)) break;
        if (!got) continue;
        if (file != INVALID_HANDLE_VALUE) WriteFile(file, buf, got, &put, NULL);
        total += got;
    }
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (total > 0 && file != INVALID_HANDLE_VALUE) {
        WCHAR args[MAX_PATH + 4];
        StringCchPrintfW(args, MAX_PATH + 4, L"\"%s\"", path);
        ShellExecuteW(NULL, L"open", g_app, args, NULL, SW_SHOWNORMAL);
    } else {
        DeleteFileW(path);
        RemoveDirectoryW(sub);
    }
    return 0;
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR cmd, int show)
{
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    BOOL first = TRUE;
    (void)hi; (void)hp; (void)cmd; (void)show;
    if (argv && argc > 1) {
        StringCchCopyW(g_app, MAX_PATH, argv[1]);
    } else {   /* 설치본: <앱>\_internal\printer\PDFmetryPrint.exe → <앱>\PDFmetry.exe */
        WCHAR *p;
        GetModuleFileNameW(NULL, g_app, MAX_PATH);
        for (int up = 0; up < 3; up++) { p = wcsrchr(g_app, L'\\'); if (p) *p = 0; }
        StringCchCatW(g_app, MAX_PATH, L"\\PDFmetry.exe");
    }
    if (argv) LocalFree(argv);
    for (;;) {
        HANDLE pipe = CreateNamedPipeW(PIPE_NAME,
                                       PIPE_ACCESS_INBOUND | (first ? FILE_FLAG_FIRST_PIPE_INSTANCE : 0),
                                       PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                                       PIPE_UNLIMITED_INSTANCES, 0, 1 << 20, 0, NULL);
        if (pipe == INVALID_HANDLE_VALUE) {
            if (first) return 1;   /* 이미 다른 수신기가 돈다 */
            Sleep(1000); continue;
        }
        first = FALSE;
        if (ConnectNamedPipe(pipe, NULL) || GetLastError() == ERROR_PIPE_CONNECTED) {
            HANDLE t = CreateThread(NULL, 0, serve, pipe, 0, NULL);
            if (t) CloseHandle(t);
            else CloseHandle(pipe);
        } else {
            CloseHandle(pipe);
        }
    }
}
