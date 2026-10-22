# -*- coding: utf-8 -*-
"""AIDA64 工具栏按钮侦察：逐个点击 16 个按钮，记录每个按钮弹出的窗口类名/标题。
结果写到 tools/btnmap.json，成功后下次可直接按类名找 Preferences。
"""
import ctypes, json, os, subprocess, sys, time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_aida64():
    """定位 aida64.exe：环境变量 AIDA64_EXE > 正在运行的进程 > 常见安装位置"""
    env = os.environ.get("AIDA64_EXE")
    if env and os.path.exists(env):
        return env
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='aida64.exe'\" | "
             "Select-Object -First 1 -ExpandProperty ExecutablePath"],
            capture_output=True, text=True, timeout=8,
            creationflags=0x08000000,
        ).stdout.strip()
        if out and os.path.exists(out):
            return out
    except Exception:
        pass
    for p in (
        r"C:\Program Files\FinalWire\AIDA64 Business\aida64.exe",
        r"C:\Program Files (x86)\FinalWire\AIDA64 Business\aida64.exe",
        r"C:\Program Files\FinalWire\AIDA64 Extreme\aida64.exe",
    ):
        if os.path.exists(p):
            return p
    return ""


EXE = find_aida64()
MAPF = os.path.join(BASE, "tools", "btnmap.json")

WM_CLOSE = 0x0010


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, b, 256)
    return b.value


def title(h):
    b = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(h, b, 512)
    return b.value


def pidof(h):
    p = wintypes.DWORD()
    u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


EP = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def snap():
    t = []
    u32.EnumWindows(EP(lambda h, l: (t.append(h), True)[1]), 0)
    return t


def aida_pid():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq aida64.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    if "aida64.exe" not in out.lower():
        return None
    try:
        return int(out.split('","')[1])
    except Exception:
        return None


def find_main(apid, timeout=40):
    for _ in range(timeout):
        for h in snap():
            if pidof(h) == apid and cls(h) == "TAIDA64" and u32.IsWindowVisible(h):
                return h
        time.sleep(1)
    return None


def win_list(apid):
    r = []
    for h in snap():
        if pidof(h) == apid and u32.IsWindowVisible(h):
            r.append((h, cls(h), title(h)[:40]))
    return r


def click(x, y):
    u32.SetCursorPos(x, y)
    time.sleep(0.12)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.09)
    u32.mouse_event(0x0004, 0, 0, 0, 0)


def main():
    apid = aida_pid()
    if not apid:
        print("AIDA64 not running, launching")
        subprocess.Popen([EXE])
        time.sleep(12)
        apid = aida_pid()
    print("apid", apid)
    main_h = find_main(apid)
    if not main_h:
        print("NO MAIN WINDOW")
        return 1
    r = wintypes.RECT()
    u32.GetWindowRect(main_h, ctypes.byref(r))
    L, T = r.left, r.top
    print("main", main_h, "rect", L, T, r.right - r.left, r.bottom - r.top)

    cand = [81, 125, 167, 213, 255, 297, 340, 402, 445, 489, 552, 596, 638, 680, 743, 786]
    before = set(h for h, c, t in win_list(apid))
    result = {}
    for cx in cand:
        base = set(h for h, c, t in win_list(apid))
        click(L + cx, T + 68)
        time.sleep(1.6)
        now = [x for x in win_list(apid) if x[0] not in base]
        names = [(c, t) for h, c, t in now]
        result[str(cx)] = names
        print(f"cx={cx:4d} -> {names}")
        if any(c == "TForm_Preferences" for c, t in names):
            print("!!! FOUND PREFERENCES at cx=", cx)
            result["prefs_cx"] = cx
            break
        for h, c, t in now:
            if c == "#32770" or c.startswith("TForm"):
                u32.PostMessageW(h, WM_CLOSE, 0, 0)
                time.sleep(0.4)
        time.sleep(0.2)

    with open(MAPF, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("written", MAPF)
    return 0


if __name__ == "__main__":
    sys.exit(main())
