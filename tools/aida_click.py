# -*- coding: utf-8 -*-
"""在 AIDA64 窗口里定位并点击工具栏按钮（Delphi 自绘，只能靠像素分割）。

用法:
  python aida_click.py list            列出窗口 + 工具栏按钮分段
  python aida_click.py shot <name>     截 AIDA64 窗口存到 %TEMP%\<name>.png
  python aida_click.py click <index>   点第 index 个工具栏按钮（从 1 开始）

坐标系统一用「物理像素」：进程先声明 PER_MONITOR_AWARE_V2。
"""
import ctypes, sys, os, time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)

try:
    u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        u32.SetProcessDPIAware()

TARGET_EXE = "aida64.exe"

EnumWindows = u32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def iter_windows():
    out = []

    def cb(hwnd, lparam):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if u32.IsWindowVisible(hwnd):
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, cls, 256)
            out.append((hwnd, pid.value, cls.value, buf.value))
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    return out


def find_main():
    import subprocess
    pids = set()
    for hwnd, pid, cls, title in iter_windows():
        if cls == "TAIDA64":
            pids.add(pid)
    if not pids:
        raise SystemExit("AIDA64 主窗口没找到，先启动 aida64.exe")
    pid = sorted(pids)[0]
    for hwnd, p, cls, title in iter_windows():
        if p == pid and cls == "TAIDA64":
            return hwnd, pid, title
    raise SystemExit("no TAIDA64 window")


def rect(hwnd):
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def grab(hwnd, path):
    from PIL import ImageGrab
    l, t, r, b = rect(hwnd)
    img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    img.save(path)
    return img, (l, t, r, b)


def segments(img, y0, y1, min_gap=3, min_w=14):
    """在 y0..y1 横向带上，按「空列」切出按钮段。"""
    w, h = img.size
    px = img.load()
    bg = px[2, y0 + 2]
    counts = []
    for x in range(w):
        c = 0
        for y in range(y0, y1):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 40:
                c += 1
        counts.append(c)
    segs, start = [], None
    for x, c in enumerate(counts):
        if c > 0:
            if start is None:
                start = x
        else:
            if start is not None:
                if x - start >= min_w:
                    segs.append((start, x - 1))
                start = None
    if start is not None and w - start >= min_w:
        segs.append((start, w - 1))
    # 合并间隔小于 min_gap 的段（图标内部可能有空隙）
    merged = []
    for s in segs:
        if merged and s[0] - merged[-1][1] <= min_gap:
            merged[-1] = (merged[-1][0], s[1])
        else:
            merged.append(list(s) if False else (s[0], s[1]))
    return merged


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    hwnd, pid, title = find_main()
    l, t, r, b = rect(hwnd)
    print(f"window hwnd={hwnd} pid={pid} title={title!r} rect=({l},{t})-({r},{b}) size={r-l}x{b-t}")

    if cmd == "shot":
        name = sys.argv[2] if len(sys.argv) > 2 else "aida_now"
        p = os.path.join(os.environ["TEMP"], name + ".png")
        img, box = grab(hwnd, p)
        print("saved", p, img.size, box)
        return

    if cmd == "list":
        p = os.path.join(os.environ["TEMP"], "aida_seg.png")
        img, box = grab(hwnd, p)
        print("saved", p, img.size)
        # 工具栏一般紧贴标题栏下方，先粗扫几条带
        for (y0, y1) in [(28, 62), (30, 70), (24, 56)]:
            s = segments(img, y0, y1)
            print(f"  band y={y0}..{y1} -> {len(s)} segs: {s[:20]}")
        return

    if cmd == "click":
        idx = int(sys.argv[2])
        p = os.path.join(os.environ["TEMP"], "aida_seg.png")
        img, box = grab(hwnd, p)
        s = segments(img, 28, 62)
        print(f"{len(s)} segs: {s}")
        if idx < 1 or idx > len(s):
            raise SystemExit(f"index out of range 1..{len(s)}")
        x0, x1 = s[idx - 1]
        cx = box[0] + (x0 + x1) // 2
        cy = box[1] + 45
        print(f"click seg#{idx} {s[idx-1]} -> screen ({cx},{cy})")
        u32.SetCursorPos(cx, cy)
        time.sleep(0.15)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(1.2)
        out = os.path.join(os.environ["TEMP"], "aida_after_click.png")
        grab(hwnd, out)
        print("after:", out)
        return


if __name__ == "__main__":
    main()
