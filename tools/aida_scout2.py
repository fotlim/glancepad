# -*- coding: utf-8 -*-
"""AIDA64 工具栏按钮侦察 v2：严格关闭对话框 + 只扫指定区间。
用法: python aida_scout2.py [cx1 cx2 ...]
"""
import ctypes, json, os, subprocess, sys, time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPF = os.path.join(BASE, "tools", "btnmap2.json")

WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_ESCAPE = 0x1B


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


def wins(apid):
    return [(h, cls(h), title(h)[:44]) for h in snap()
            if pidof(h) == apid and u32.IsWindowVisible(h)]


def close_extra(apid, main_h):
    """把除主窗以外的可见窗口全部关掉，直到只剩主窗。"""
    for _ in range(6):
        extra = [w for w in wins(apid) if w[0] != main_h]
        if not extra:
            return True
        for h, c, t in extra:
            u32.SetForegroundWindow(h)
            time.sleep(0.12)
            u32.PostMessageW(h, WM_CLOSE, 0, 0)
            time.sleep(0.25)
            u32.PostMessageW(h, WM_KEYDOWN, VK_ESCAPE, 0)
            u32.PostMessageW(h, WM_KEYUP, VK_ESCAPE, 0)
            time.sleep(0.2)
        time.sleep(0.6)
    return not [w for w in wins(apid) if w[0] != main_h]


def click(x, y):
    u32.SetCursorPos(x, y)
    time.sleep(0.14)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.1)
    u32.mouse_event(0x0004, 0, 0, 0, 0)


def main():
    cand = [int(a) for a in sys.argv[1:]] or [81, 125, 167, 213, 255, 297, 340, 402, 445, 489]
    apid = aida_pid()
    if not apid:
        print("AIDA64 not running")
        return 1
    main_h = None
    for h in snap():
        if pidof(h) == apid and cls(h) == "TAIDA64" and u32.IsWindowVisible(h):
            main_h = h
            break
    if not main_h:
        print("NO MAIN")
        return 1
    r = wintypes.RECT()
    u32.GetWindowRect(main_h, ctypes.byref(r))
    L, T = r.left, r.top
    print("main", main_h, "apid", apid, "rect", L, T, r.right - r.left, r.bottom - r.top)

    result = {}
    for cx in cand:
        ok = close_extra(apid, main_h)
        if not ok:
            print(f"cx={cx} SKIP: cannot clear dialogs -> {wins(apid)}")
            result[str(cx)] = ["<blocked>"]
            continue
        base = set(h for h, c, t in wins(apid))
        u32.ShowWindow(main_h, 9)
        u32.SetForegroundWindow(main_h)
        time.sleep(0.5)
        click(L + cx, T + 68)
        time.sleep(2.0)
        now = [x for x in wins(apid) if x[0] not in base]
        names = [(c, t) for h, c, t in now]
        result[str(cx)] = names
        print(f"cx={cx:4d} -> {names}", flush=True)
        if any("Preferences" in c or "设置" in t for c, t in names):
            print("!!! PREFERENCES at cx =", cx, flush=True)
            result["prefs_cx"] = cx
            with open(MAPF, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            return 0
    with open(MAPF, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("written", MAPF)
    return 0


if __name__ == "__main__":
    sys.exit(main())
