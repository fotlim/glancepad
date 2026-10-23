# -*- coding: utf-8 -*-
"""
副屏监控 · 托盘启动器兼设置程序
================================
双击运行（.pyw 不弹黑框）。它只做三件事：

1. 常驻系统托盘 —— 不再弹小窗口
2. 右键菜单可 启动 / 停止 监控（kiosk 全屏退不出来的问题就靠这里）
3. 设置窗口 —— 改城市/天气，自定义底部 Dock 快捷启动项

settings.json 一改，正在跑的监控页面 20 秒内自动刷新 Dock（后端按 mtime 判断）。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(ROOT, "settings.json")
PS1 = os.path.join(ROOT, "start_monitor.ps1")
ICONS = os.path.join(ROOT, "static", "icons")
FONTS = os.path.join(ROOT, "fonts")
BGS = os.path.join(ROOT, "backgrounds")
PORT = 8770
URL = "http://127.0.0.1:%d/" % PORT
LOCK_PORT = 8779          # 单实例锁
NO_WIN = 0x08000000       # CREATE_NO_WINDOW

DEFAULT = {
    "city": "杭州市",
    "latitude": 30.2741,
    "longitude": 120.1551,
    "weather": True,
    "apps": [],
    # ---- 显示（v1.1）
    "screen": 0,             # 0=自动挑非主屏；1..n=按枚举序号
    "font": "system",        # system / ttf
    "font_file": "",
    "bg_mode": "gradient",   # gradient / image
    "bg_image": "",
    "bg_effect": "blur",     # blur / dim / none
    "bg_blur": 18,
    "bg_opacity": 100,
    # ---- 天气源（v1.1.1）
    "weather_src": "auto",   # auto / cn / open-meteo
}

# 天气源下拉的「显示文字 -> 实际值」对照（界面友好，存的是英文值）
_WSRC_LABEL = {
    "auto": "自动（中国天气网 → open-meteo）",
    "cn": "中国天气网（中国气象局）",
    "open-meteo": "open-meteo（国际通用）",
}
_WSRC_VALUE = {v: k for k, v in _WSRC_LABEL.items()}


def _wsrc_from_label(label):
    return _WSRC_VALUE.get(str(label or "").strip(), "auto")


# ---------------------------------------------------------------- 基础工具
def load_settings():
    d = dict(DEFAULT)
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            d.update(raw)
    except Exception:
        pass
    if not isinstance(d.get("apps"), list):
        d["apps"] = []
    return d


def save_settings(d):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)


def server_alive():
    """探测服务是否在跑。

    ⚠ 不能用 /api/snapshot！
    /api/snapshot 要现采 CPU/GPU/内存/磁盘/网络/天气/一言/历史，
    实测 4.5~5.6 秒才返回；原来这里 timeout=1.5 秒，于是服务明明在跑
    也一律判成「没起来」——托盘点「启动监控」永远不会走「已在运行」分支，
    而且 _wait_up() 20 秒后必定弹「启动超时」，用户看到的就是「打不开」。
    改用轻量端点 /api/theme（实测 38ms，冷启动 651ms）+ 3 秒超时。
    """
    for ep in ("api/theme", "api/apps/version"):
        try:
            with urllib.request.urlopen(URL + ep, timeout=3.0) as r:
                if r.status == 200:
                    return True
        except Exception:
            continue
    return False


def run_ps(args, wait=False):
    """跑 start_monitor.ps1，不外弹窗口"""
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", PS1] + args
    kw = dict(creationflags=NO_WIN, close_fds=True)
    if wait:
        kw.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.call(cmd, **kw)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kw)
    return 0


# ------------------------------------------------- 开机自启 / 桌面快捷方式
APP_KEY = "GlancePad"           # 注册表 Run 项名
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LNK_NAME = "副屏监控.lnk"


def _run_key_path():
    import winreg
    return winreg.HKEY_CURRENT_USER, RUN_KEY


def autostart_command():
    """返回写进 Run 项的完整命令行"""
    exe = sys.executable or ""
    d = os.path.dirname(exe)
    pw = os.path.join(d, "pythonw.exe")
    if not os.path.isfile(pw):
        pw = exe
    return '"%s" "%s"' % (pw, os.path.join(ROOT, "launcher.pyw"))


def autostart_enabled():
    try:
        import winreg
        h, sub = _run_key_path()
        with winreg.OpenKey(h, sub, 0, winreg.KEY_READ) as k:
            try:
                v, _ = winreg.QueryValueEx(k, APP_KEY)
                return bool(v)
            except FileNotFoundError:
                return False
    except Exception:
        return False


def set_autostart(flag):
    """开启/关闭开机自启，返回 (ok, 说明)"""
    try:
        import winreg
        h, sub = _run_key_path()
        with winreg.CreateKeyEx(h, sub, 0, winreg.KEY_SET_VALUE) as k:
            if flag:
                winreg.SetValueEx(k, APP_KEY, 0, winreg.REG_SZ, autostart_command())
                return True, "已开启开机自启"
            try:
                winreg.DeleteValue(k, APP_KEY)
            except FileNotFoundError:
                pass
            return True, "已关闭开机自启"
    except Exception as e:
        return False, "注册表操作失败：%r" % (e,)


def desktop_dir():
    for p in (os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
              os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Desktop")):
        if p and os.path.isdir(p):
            return p
    return None


def create_desktop_shortcut():
    """用 WScript.Shell 建桌面 .lnk，返回 (ok, 路径或错误)"""
    desk = desktop_dir()
    if not desk:
        return False, "找不到桌面目录"
    lnk = os.path.join(desk, LNK_NAME)
    ico = os.path.join(ROOT, "static", "glancepad.ico")
    exe = sys.executable or ""
    pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.isfile(pw):
        pw = exe
    ps = (
        "$s=New-Object -ComObject WScript.Shell;"
        "$l=$s.CreateShortcut('%s');"
        "$l.TargetPath='%s';"
        "$l.Arguments='\"%s\"';"
        "$l.WorkingDirectory='%s';"
        "$l.Description='副屏监控 GlancePad';"
        "%s"
        "$l.Save()"
    ) % (lnk.replace("'", "''"), pw.replace("'", "''"),
         os.path.join(ROOT, "launcher.pyw").replace("'", "''"),
         ROOT.replace("'", "''"),
         ("$l.IconLocation='%s,0';" % ico.replace("'", "''")) if os.path.isfile(ico) else "")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", ps],
                       creationflags=NO_WIN, capture_output=True)
    if r.returncode == 0 and os.path.isfile(lnk):
        return True, lnk
    return False, (r.stderr or b"").decode("mbcs", "ignore")[:200] or "创建失败"


def icon_files():
    try:
        return sorted([f for f in os.listdir(ICONS)
                       if f.lower().endswith((".png", ".ico", ".jpg", ".svg"))])
    except Exception:
        return []


# ------------------------------------------------- 显示器 / 字体 / 背景资源
def list_dir_files(folder, exts):
    try:
        out = [f for f in os.listdir(folder) if f.lower().endswith(tuple(exts))]
        return sorted(out)
    except Exception:
        return []


def enum_screens():
    """按 EnumDisplayMonitors 顺序枚举显示器（DPI 感知，返回原始像素坐标）。

    注意：**绝对不要按 \\.\DISPLAYn 的设备名硬编码**，设备号会随插拔变化，
    这里只依赖枚举顺序 + primary 标志。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]

        user32 = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        found = []

        def cb(hmon, hdc, lprc, lparam):
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcMonitor
                found.append({
                    "device": mi.szDevice,
                    "x": r.left, "y": r.top,
                    "w": r.right - r.left, "h": r.bottom - r.top,
                    "primary": bool(mi.dwFlags & 1),
                })
            return True

        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
        for i, s in enumerate(found):
            s["index"] = i + 1
        return found
    except Exception:
        return []


def make_tray_image():
    """没有现成 ico 就画一个：深底圆角 + 青绿三角"""
    from PIL import Image, ImageDraw
    S = 128
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, S - 6, S - 6], radius=26, fill=(38, 50, 60, 255))
    d.rounded_rectangle([6, 6, S - 6, S - 6], radius=26, outline=(79, 209, 197, 255),
                        width=5)
    d.polygon([(64, 30), (102, 96), (26, 96)], fill=(79, 209, 197, 255))
    d.ellipse([54, 84, 74, 104], fill=(91, 157, 255, 255))
    return img


# ---------------------------------------------------------------- 应用编辑对话框
def edit_app(parent, app=None, title="添加应用"):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg="#1b242b")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    a = dict(app or {})
    vars_ = {
        "name": tk.StringVar(value=a.get("name", "")),
        "path": tk.StringVar(value=a.get("path", "")),
        "icon": tk.StringVar(value=a.get("icon", "")),
        "args": tk.StringVar(value=a.get("args", "")),
    }
    result = {"ok": False}

    def row(r, label, var, browse=None):
        tk.Label(win, text=label, bg="#1b242b", fg="#9fb3c8",
                 anchor="e", width=8).grid(row=r, column=0, padx=(16, 8), pady=6,
                                           sticky="e")
        e = tk.Entry(win, textvariable=var, width=42, bg="#131a20",
                     fg="#e6eef5", insertbackground="#4fd1c5",
                     relief="flat", highlightthickness=1,
                     highlightbackground="#2c3a45", highlightcolor="#4fd1c5")
        e.grid(row=r, column=1, padx=(0, 6), pady=6, sticky="we", ipady=4)
        if browse:
            tk.Button(win, text=browse[0], command=browse[1], bg="#26333d",
                      fg="#cfe0ee", relief="flat", padx=10).grid(
                row=r, column=2, padx=(0, 16), pady=6)
        return e

    def pick_exe():
        p = filedialog.askopenfilename(
            title="选择程序",
            filetypes=[("可执行文件", "*.exe"), ("快捷方式", "*.lnk"), ("全部", "*.*")])
        if p:
            vars_["path"].set(p)
            if not vars_["name"].get().strip():
                vars_["name"].set(os.path.splitext(os.path.basename(p))[0])

    def pick_icon():
        p = filedialog.askopenfilename(
            title="选择图标", initialdir=ICONS,
            filetypes=[("图片", "*.png *.ico *.jpg *.svg"), ("全部", "*.*")])
        if p:
            # 优先用 icons 目录里的文件名；目录外就转成绝对名存着（前端会当文件名处理）
            base = os.path.basename(p)
            vars_["icon"].set(os.path.join(ICONS, base) if
                              os.path.dirname(p) == ICONS else p)

    row(0, "名称", vars_["name"])
    row(1, "程序路径", vars_["path"], ("浏览…", pick_exe))
    row(2, "图标", vars_["icon"], ("选择…", pick_icon))
    row(3, "启动参数", vars_["args"])

    hint = tk.Label(win, text="图标留空会用默认图标；填 static/icons 下的文件名也行",
                    bg="#1b242b", fg="#6d8296", anchor="w")
    hint.grid(row=4, column=0, columnspan=3, padx=16, pady=(0, 4), sticky="we")

    def ok():
        name = vars_["name"].get().strip()
        path = vars_["path"].get().strip()
        if not name:
            messagebox.showwarning("缺名称", "给这个应用起个名字吧", parent=win)
            return
        if not path:
            messagebox.showwarning("缺路径", "还没选程序路径", parent=win)
            return
        a.update(name=name, path=path,
                 icon=vars_["icon"].get().strip(), args=vars_["args"].get().strip())
        if not a["icon"]:
            a.pop("icon", None)
        if not a["args"]:
            a.pop("args", None)
        result["ok"] = True
        win.destroy()

    bar = tk.Frame(win, bg="#1b242b")
    bar.grid(row=5, column=0, columnspan=3, pady=(10, 14))
    tk.Button(bar, text="确定", command=ok, bg="#4fd1c5", fg="#0c1418",
              relief="flat", padx=22, pady=4).pack(side="left", padx=6)
    tk.Button(bar, text="取消", command=win.destroy, bg="#2c3a45", fg="#cfe0ee",
              relief="flat", padx=22, pady=4).pack(side="left", padx=6)

    win.wait_window()
    return a if result["ok"] else None


# ---------------------------------------------------------------- 设置窗口
class SettingsWindow:
    def __init__(self, root, on_status_change=None):
        self.root = root
        self.on_status_change = on_status_change
        self.win = None
        self.apps = []
        self.sel = None

    # ---- 打开/聚焦
    def show(self):
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()

    def _build(self):
        import tkinter as tk
        from tkinter import ttk, messagebox

        d = load_settings()
        self.cfg = d
        self.apps = [dict(a) for a in d.get("apps", [])]

        w = tk.Toplevel(self.root)
        self.win = w
        w.title("副屏监控 · 设置")
        w.configure(bg="#1b242b")
        w.geometry("820x700")
        w.minsize(720, 600)
        w.protocol("WM_DELETE_WINDOW", w.withdraw)

        style = ttk.Style(w)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background="#1b242b", borderwidth=0)
        style.configure("TNotebook.Tab", background="#26333d", foreground="#cfe0ee",
                        padding=(16, 7), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#4fd1c5")],
                  foreground=[("selected", "#0c1418")])
        style.configure("Treeview", background="#131a20", fieldbackground="#131a20",
                        foreground="#dbe7f0", rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background="#26333d",
                        foreground="#9fb3c8", relief="flat")
        style.map("Treeview", background=[("selected", "#2f6f69")])

        head = tk.Frame(w, bg="#131a20")
        head.pack(fill="x")
        tk.Label(head, text="  副屏监控", bg="#131a20", fg="#4fd1c5",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", pady=10)
        self.lb_state = tk.Label(head, text="", bg="#131a20", fg="#8fa7ba")
        self.lb_state.pack(side="right", padx=14)

        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=14, pady=10)

        # ---------------- Tab1: 快捷启动 Dock
        t1 = tk.Frame(nb, bg="#1b242b")
        nb.add(t1, text="  快捷启动 Dock  ")

        tip = tk.Label(t1, text="这里列出的应用会显示在副屏底部 Dock 上，点一下就能启动",
                       bg="#1b242b", fg="#6d8296", anchor="w")
        tip.pack(fill="x", padx=8, pady=(10, 6))

        box = tk.Frame(t1, bg="#1b242b")
        box.pack(fill="both", expand=True, padx=8)

        cols = ("name", "path", "icon")
        self.tv = ttk.Treeview(box, columns=cols, show="headings", selectmode="browse")
        self.tv.heading("name", text="名称")
        self.tv.heading("path", text="程序路径")
        self.tv.heading("icon", text="图标")
        self.tv.column("name", width=150, anchor="w")
        self.tv.column("path", width=430, anchor="w")
        self.tv.column("icon", width=130, anchor="w")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<<TreeviewSelect>>", self._on_select)
        self.tv.bind("<Double-1>", lambda e: self.edit_sel())

        bar = tk.Frame(t1, bg="#1b242b")
        bar.pack(fill="x", padx=8, pady=8)
        for text, cmd in (("添加…", self.add), ("编辑…", self.edit_sel),
                          ("删除", self.del_sel), ("上移", lambda: self.move(-1)),
                          ("下移", lambda: self.move(1))):
            tk.Button(bar, text=text, command=cmd, bg="#26333d", fg="#cfe0ee",
                      relief="flat", padx=14, pady=4).pack(side="left", padx=(0, 6))

        # ---------------- Tab2: 天气/位置
        t2 = tk.Frame(nb, bg="#1b242b")
        nb.add(t2, text="  天气与位置  ")

        def field(parent, r, label, var, tip_text=""):
            tk.Label(parent, text=label, bg="#1b242b", fg="#9fb3c8", width=10,
                     anchor="e").grid(row=r, column=0, padx=(16, 8), pady=8, sticky="e")
            e = tk.Entry(parent, textvariable=var, width=26, bg="#131a20",
                         fg="#e6eef5", insertbackground="#4fd1c5", relief="flat",
                         highlightthickness=1, highlightbackground="#2c3a45",
                         highlightcolor="#4fd1c5")
            e.grid(row=r, column=1, pady=8, sticky="w", ipady=4)
            if tip_text:
                tk.Label(parent, text=tip_text, bg="#1b242b", fg="#6d8296",
                         anchor="w").grid(row=r, column=2, padx=(10, 0), sticky="w")
            return e

        self.v_city = tk.StringVar(value=str(d.get("city", "杭州市")))
        self.v_lat = tk.StringVar(value=str(d.get("latitude", 30.2741)))
        self.v_lon = tk.StringVar(value=str(d.get("longitude", 120.1551)))
        self.v_wx = tk.BooleanVar(value=bool(d.get("weather", True)))
        _wsrc = str(d.get("weather_src", "auto") or "auto").lower()
        self.v_wsrc = tk.StringVar(value=_WSRC_LABEL.get(_wsrc, _WSRC_LABEL["auto"]))

        field(t2, 0, "城市名", self.v_city, "用来查中国天气网的站点码，也显示在天气卡上")

        tk.Label(t2, text="天气源", bg="#1b242b", fg="#9fb3c8", width=10,
                 anchor="e").grid(row=1, column=0, padx=(16, 8), pady=8, sticky="e")
        self.cb_wsrc = ttk.Combobox(t2, textvariable=self.v_wsrc, width=24,
                                    state="readonly",
                                    values=list(_WSRC_LABEL.values()))
        self.cb_wsrc.grid(row=1, column=1, pady=8, sticky="w")
        tk.Label(t2, text="推荐「自动」：中国气象局数据，取不到再回退 open-meteo",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=1, column=2, padx=(10, 0), sticky="w")

        field(t2, 2, "纬度", self.v_lat, "只在 open-meteo 或回头校正时用到")
        field(t2, 3, "经度", self.v_lon, "如 125.3228（长春）")
        tk.Checkbutton(t2, text="在顶部显示天气", variable=self.v_wx,
                       bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                       activebackground="#1b242b", activeforeground="#4fd1c5",
                       relief="flat", anchor="w").grid(row=4, column=1,
                                                       pady=(4, 8), sticky="w")
        tk.Label(t2, text="「自动 / 中国天气网」用的是中国气象局站点，比 open-meteo 的网格插值准",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=5, column=0, columnspan=3, padx=16, pady=(6, 0), sticky="w")
        tk.Label(t2, text="改城市名后如果读数还是不对，可点「保存设置」重启监控，会自动校正坐标",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=6, column=0, columnspan=3, padx=16, pady=(2, 0), sticky="w")

        # ---------------- Tab2b: 一言
        tq = tk.Frame(nb, bg="#1b242b")
        nb.add(tq, text="  一言  ")

        self.v_qapi = tk.StringVar(
            value=str(d.get("quote_api", "https://v1.hitokoto.cn/?c={cats}")))
        self.v_qcats = tk.StringVar(value=str(d.get("quote_cats", "d,i,k")))
        self.v_qref = tk.StringVar(value=str(d.get("quote_refresh", 30)))

        tk.Label(tq, text="接口地址", bg="#1b242b", fg="#9fb3c8",
                 anchor="e", width=10).grid(row=0, column=0, padx=(16, 8),
                                            pady=(18, 8), sticky="e")
        tk.Entry(tq, textvariable=self.v_qapi, width=54, bg="#131a20",
                 fg="#e6eef5", insertbackground="#4fd1c5", relief="flat",
                 highlightthickness=1, highlightbackground="#2c3a45",
                 highlightcolor="#4fd1c5").grid(row=0, column=1, pady=(18, 8),
                                                sticky="w", ipady=4)
        tk.Label(tq, text="要返回 JSON；字段名支持 hitokoto / text / content / sentence",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=1, column=1, padx=(0, 0), pady=(0, 4), sticky="w")

        tk.Label(tq, text="分类", bg="#1b242b", fg="#9fb3c8",
                 anchor="e", width=10).grid(row=2, column=0, padx=(16, 8),
                                            pady=8, sticky="e")
        tk.Entry(tq, textvariable=self.v_qcats, width=26, bg="#131a20",
                 fg="#e6eef5", insertbackground="#4fd1c5", relief="flat",
                 highlightthickness=1, highlightbackground="#2c3a45",
                 highlightcolor="#4fd1c5").grid(row=2, column=1, pady=8,
                                                sticky="w", ipady=4)
        tk.Label(tq, text="仅对 hitokoto 生效：d 文学 / i 诗词 / k 哲学 / a 动画 / b 漫画",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=2, column=2, padx=(10, 16), sticky="w")

        tk.Label(tq, text="刷新间隔", bg="#1b242b", fg="#9fb3c8",
                 anchor="e", width=10).grid(row=3, column=0, padx=(16, 8),
                                            pady=8, sticky="e")
        frq = tk.Frame(tq, bg="#1b242b")
        frq.grid(row=3, column=1, pady=8, sticky="w")
        self.sp_qref = tk.Spinbox(frq, from_=0, to=1440, increment=5, width=6,
                                  textvariable=self.v_qref, bg="#131a20",
                                  fg="#e6eef5", insertbackground="#4fd1c5",
                                  relief="flat", buttonbackground="#26333d",
                                  highlightthickness=1,
                                  highlightbackground="#2c3a45")
        self.sp_qref.pack(side="left", ipady=3)
        tk.Label(frq, text=" 分钟换一句（0 = 每天只换一次）", bg="#1b242b",
                 fg="#6d8296").pack(side="left")

        tk.Button(tq, text="立即换一句", command=self.refresh_quote_click,
                  bg="#26333d", fg="#cfe0ee", relief="flat", padx=14,
                  pady=4).grid(row=4, column=1, pady=(14, 4), sticky="w")
        self.lb_quote = tk.Label(tq, text="", bg="#1b242b", fg="#6d8296",
                                 anchor="w", justify="left", wraplength=560)
        self.lb_quote.grid(row=5, column=0, columnspan=3, padx=16,
                           pady=(6, 0), sticky="w")
        tk.Label(tq, text="改完点右下角「保存设置」，面板约 20 秒内自动换新；"
                          "接口取不到时沿用上一句，不会变成空白。",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=6, column=0, columnspan=3, padx=16, pady=(10, 0), sticky="w")

        # ---------------- Tab3: 系统（开机自启 / 快捷方式）
        t3 = tk.Frame(nb, bg="#1b242b")
        nb.add(t3, text="  系统  ")

        self.v_auto = tk.BooleanVar(value=autostart_enabled())
        row0 = tk.Frame(t3, bg="#1b242b")
        row0.pack(fill="x", padx=16, pady=(14, 4))
        tk.Checkbutton(row0, text="开机自动启动（登录后在托盘待命，不弹窗、不占任务栏）",
                       variable=self.v_auto, bg="#1b242b", fg="#cfe0ee",
                       selectcolor="#131a20", activebackground="#1b242b",
                       activeforeground="#4fd1c5", relief="flat", anchor="w",
                       command=self.apply_autostart).pack(side="left")
        self.lb_auto = tk.Label(t3, text="", bg="#1b242b", fg="#6d8296", anchor="w")
        self.lb_auto.pack(fill="x", padx=16)

        tk.Frame(t3, bg="#26333d", height=1).pack(fill="x", padx=16, pady=14)

        row1 = tk.Frame(t3, bg="#1b242b")
        row1.pack(fill="x", padx=16)
        tk.Button(row1, text="创建桌面快捷方式", command=self.make_shortcut,
                  bg="#26333d", fg="#cfe0ee", relief="flat",
                  padx=16, pady=5).pack(side="left")
        self.lb_lnk = tk.Label(row1, text="", bg="#1b242b", fg="#6d8296")
        self.lb_lnk.pack(side="left", padx=12)

        tk.Label(t3, text="提示：开机自启只写当前用户的注册表 Run 项，不需要管理员权限；\n"
                          "      随时勾掉这里就是取消。卸载请运行安装目录下的 install.ps1 -Uninstall。",
                 bg="#1b242b", fg="#6d8296", justify="left", anchor="w").pack(
            fill="x", padx=16, pady=(16, 0))

        # ---------------- Tab4: 显示（副屏 / 字体 / 背景）
        t4 = tk.Frame(nb, bg="#1b242b")
        nb.add(t4, text="  显示  ")

        def grp(parent, text):
            tk.Label(parent, text=text, bg="#1b242b", fg="#4fd1c5",
                     font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(
                fill="x", padx=16, pady=(14, 4))

        def hint(parent, text):
            tk.Label(parent, text=text, bg="#1b242b", fg="#6d8296",
                     justify="left", anchor="w").pack(fill="x", padx=16, pady=(2, 0))

        # ---- 副屏
        grp(t4, "副屏选择")
        scr_row = tk.Frame(t4, bg="#1b242b")
        scr_row.pack(fill="x", padx=16)
        self.screens = enum_screens()
        labels = ["自动（挑非主屏）"]
        self.screen_map = {labels[0]: 0}
        for s in self.screens:
            lb = "%d. %s  %dx%d @(%d,%d)%s" % (
                s["index"], s["device"], s["w"], s["h"], s["x"], s["y"],
                "  [主屏]" if s["primary"] else "")
            labels.append(lb)
            self.screen_map[lb] = s["index"]
        cur = int(d.get("screen", 0) or 0)
        cur_lb = labels[0]
        for lb in labels:
            if self.screen_map[lb] == cur:
                cur_lb = lb
                break
        self.v_screen = tk.StringVar(value=cur_lb)
        self.cb_screen = ttk.Combobox(scr_row, textvariable=self.v_screen,
                                      values=labels, state="readonly", width=52)
        self.cb_screen.pack(side="left")
        tk.Button(scr_row, text="重新检测", command=self.rescan_screens,
                  bg="#26333d", fg="#cfe0ee", relief="flat",
                  padx=12, pady=3).pack(side="left", padx=10)
        hint(t4, "选好点「保存设置」，下次启动生效。显示器的设备名会随插拔变化，这里按枚举顺序记序号。")

        tk.Frame(t4, bg="#26333d", height=1).pack(fill="x", padx=16, pady=12)

        # ---- 字体
        grp(t4, "字体")
        f1 = tk.Frame(t4, bg="#1b242b")
        f1.pack(fill="x", padx=16)
        self.v_font = tk.StringVar(value=str(d.get("font", "system") or "system"))
        tk.Radiobutton(f1, text="系统默认", variable=self.v_font, value="system",
                       bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                       activebackground="#1b242b", activeforeground="#4fd1c5",
                       relief="flat").pack(side="left")
        tk.Radiobutton(f1, text="自定义 TTF", variable=self.v_font, value="ttf",
                       bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                       activebackground="#1b242b", activeforeground="#4fd1c5",
                       relief="flat").pack(side="left", padx=(14, 0))

        f2 = tk.Frame(t4, bg="#1b242b")
        f2.pack(fill="x", padx=16, pady=(6, 0))
        self.font_files = list_dir_files(FONTS, (".ttf", ".otf", ".ttc"))
        self.v_font_file = tk.StringVar(value=str(d.get("font_file", "") or ""))
        self.cb_font = ttk.Combobox(f2, textvariable=self.v_font_file,
                                    values=self.font_files, state="readonly", width=42)
        self.cb_font.pack(side="left")
        tk.Button(f2, text="导入字体…", command=self.import_font, bg="#26333d",
                  fg="#cfe0ee", relief="flat", padx=12, pady=3).pack(side="left", padx=8)
        hint(t4, "导入的字体复制到程序目录 fonts/ 下，不会改动系统字体。仅对监控页面生效。")

        tk.Frame(t4, bg="#26333d", height=1).pack(fill="x", padx=16, pady=12)

        # ---- 背景
        grp(t4, "背景")
        b1 = tk.Frame(t4, bg="#1b242b")
        b1.pack(fill="x", padx=16)
        self.v_bg_mode = tk.StringVar(value=str(d.get("bg_mode", "gradient") or "gradient"))
        tk.Radiobutton(b1, text="默认渐变", variable=self.v_bg_mode, value="gradient",
                       bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                       activebackground="#1b242b", activeforeground="#4fd1c5",
                       relief="flat").pack(side="left")
        tk.Radiobutton(b1, text="自定义图片", variable=self.v_bg_mode, value="image",
                       bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                       activebackground="#1b242b", activeforeground="#4fd1c5",
                       relief="flat").pack(side="left", padx=(14, 0))

        b2 = tk.Frame(t4, bg="#1b242b")
        b2.pack(fill="x", padx=16, pady=(6, 0))
        self.bg_files = list_dir_files(BGS, (".jpg", ".jpeg", ".png", ".webp", ".bmp"))
        self.v_bg_image = tk.StringVar(value=str(d.get("bg_image", "") or ""))
        self.cb_bg = ttk.Combobox(b2, textvariable=self.v_bg_image,
                                  values=self.bg_files, state="readonly", width=42)
        self.cb_bg.pack(side="left")
        tk.Button(b2, text="导入图片…", command=self.import_bg, bg="#26333d",
                  fg="#cfe0ee", relief="flat", padx=12, pady=3).pack(side="left", padx=8)

        b3 = tk.Frame(t4, bg="#1b242b")
        b3.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(b3, text="处理方式", bg="#1b242b", fg="#9fb3c8").pack(side="left")
        self.v_bg_effect = tk.StringVar(value=str(d.get("bg_effect", "blur") or "blur"))
        for val, txt in (("blur", "模糊"), ("dim", "压暗"), ("none", "原图")):
            tk.Radiobutton(b3, text=txt, variable=self.v_bg_effect, value=val,
                           bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                           activebackground="#1b242b", activeforeground="#4fd1c5",
                           relief="flat").pack(side="left", padx=(10, 0))

        b4 = tk.Frame(t4, bg="#1b242b")
        b4.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(b4, text="模糊半径", bg="#1b242b", fg="#9fb3c8", width=8,
                 anchor="w").pack(side="left")
        self.v_bg_blur = tk.IntVar(value=int(d.get("bg_blur", 18) or 18))
        tk.Scale(b4, from_=0, to=60, orient="horizontal", variable=self.v_bg_blur,
                 bg="#1b242b", fg="#9fb3c8", troughcolor="#131a20",
                 highlightthickness=0, length=260, sliderrelief="flat",
                 activebackground="#4fd1c5").pack(side="left")
        self.lb_blur = tk.Label(b4, text="", bg="#1b242b", fg="#6d8296", width=14,
                                anchor="w")
        self.lb_blur.pack(side="left", padx=6)
        self.v_bg_blur.trace_add("write", lambda *_: self.lb_blur.config(
            text="%d px" % self.v_bg_blur.get()))
        self.lb_blur.config(text="%d px" % self.v_bg_blur.get())

        b5 = tk.Frame(t4, bg="#1b242b")
        b5.pack(fill="x", padx=16, pady=(2, 0))
        tk.Label(b5, text="不透明度", bg="#1b242b", fg="#9fb3c8", width=8,
                 anchor="w").pack(side="left")
        self.v_bg_op = tk.IntVar(value=int(d.get("bg_opacity", 100) or 100))
        tk.Scale(b5, from_=10, to=100, orient="horizontal", variable=self.v_bg_op,
                 bg="#1b242b", fg="#9fb3c8", troughcolor="#131a20",
                 highlightthickness=0, length=260, sliderrelief="flat",
                 activebackground="#4fd1c5").pack(side="left")
        self.lb_op = tk.Label(b5, text="", bg="#1b242b", fg="#6d8296", width=14,
                              anchor="w")
        self.lb_op.pack(side="left", padx=6)
        self.v_bg_op.trace_add("write", lambda *_: self.lb_op.config(
            text="%d%%" % self.v_bg_op.get()))
        self.lb_op.config(text="%d%%" % self.v_bg_op.get())

        b6 = tk.Frame(t4, bg="#1b242b")
        b6.pack(fill="x", padx=16, pady=(4, 0))
        self.v_bg_contrast = tk.StringVar(
            value=str(d.get("bg_contrast", "auto") or "auto"))
        cb_auto = tk.Checkbutton(
            b6, text="根据背景亮度自动增强文字对比度",
            variable=self.v_bg_contrast, onvalue="auto", offvalue="off",
            bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
            activebackground="#1b242b", activeforeground="#4fd1c5",
            relief="flat")
        cb_auto.pack(side="left")

        hint(t4, "模糊半径只在「处理方式 = 模糊」时起作用；不透明度越低，背景图越淡。\n"
                 "图片复制到程序目录 backgrounds/ 下，原图不会被修改。\n"
                 "自动对比度会按背景图的亮度分析结果自动加深遮罩、压暗卡片、提亮次要文字，照片类背景建议保持开启。")

        # ---------------- 底部按钮
        foot = tk.Frame(w, bg="#131a20")
        foot.pack(fill="x", side="bottom")
        self.b_start = tk.Button(foot, text="启动监控", command=self.start,
                                 bg="#4fd1c5", fg="#0c1418", relief="flat",
                                 padx=20, pady=5)
        self.b_start.pack(side="left", padx=(14, 8), pady=10)
        self.b_stop = tk.Button(foot, text="停止监控", command=self.stop,
                                bg="#e05c5c", fg="#fff", relief="flat",
                                padx=20, pady=5)
        self.b_stop.pack(side="left", padx=(0, 8), pady=10)
        tk.Button(foot, text="保存设置", command=self.save, bg="#26333d",
                  fg="#cfe0ee", relief="flat", padx=20, pady=5).pack(
            side="right", padx=14, pady=10)
        tk.Button(foot, text="关闭", command=w.withdraw, bg="#26333d",
                  fg="#cfe0ee", relief="flat", padx=18, pady=5).pack(
            side="right", padx=(0, 8), pady=10)

        self._fill_tree()
        self.refresh_state()

    # ---- 列表操作
    def _fill_tree(self):
        self.tv.delete(*self.tv.get_children())
        for i, a in enumerate(self.apps):
            self.tv.insert("", "end", iid=str(i),
                           values=(a.get("name", ""), a.get("path", ""),
                                   os.path.basename(a.get("icon") or "") or "默认"))

    def _on_select(self, _e=None):
        s = self.tv.selection()
        self.sel = int(s[0]) if s else None

    def add(self):
        a = edit_app(self.win, None, "添加应用")
        if a:
            self.apps.append(a)
            self._fill_tree()

    def edit_sel(self):
        if self.sel is None or self.sel >= len(self.apps):
            return
        a = edit_app(self.win, self.apps[self.sel], "编辑应用")
        if a:
            self.apps[self.sel] = a
            self._fill_tree()

    def del_sel(self):
        from tkinter import messagebox
        if self.sel is None or self.sel >= len(self.apps):
            return
        n = self.apps[self.sel].get("name", "")
        if messagebox.askyesno("删除", "确定从 Dock 移除「%s」？" % n, parent=self.win):
            self.apps.pop(self.sel)
            self.sel = None
            self._fill_tree()

    def move(self, d):
        if self.sel is None or self.sel >= len(self.apps):
            return
        j = self.sel + d
        if j < 0 or j >= len(self.apps):
            return
        self.apps[self.sel], self.apps[j] = self.apps[j], self.apps[self.sel]
        self.sel = j
        self._fill_tree()
        self.tv.selection_set(str(j))
        self.tv.see(str(j))

    # ---- 显示页签
    def rescan_screens(self):
        self.screens = enum_screens()
        labels = ["自动（挑非主屏）"]
        self.screen_map = {labels[0]: 0}
        for s in self.screens:
            lb = "%d. %s  %dx%d @(%d,%d)%s" % (
                s["index"], s["device"], s["w"], s["h"], s["x"], s["y"],
                "  [主屏]" if s["primary"] else "")
            labels.append(lb)
            self.screen_map[lb] = s["index"]
        self.cb_screen.config(values=labels)
        if self.v_screen.get() not in labels:
            self.v_screen.set(labels[0])

    def _import_into(self, folder, types, exts, title, var, combobox):
        from tkinter import filedialog, messagebox
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            messagebox.showerror(title, "无法创建目录：%s" % e, parent=self.win)
            return
        path = filedialog.askopenfilename(parent=self.win, title=title, filetypes=types)
        if not path:
            return
        name = os.path.basename(path)
        dst = os.path.join(folder, name)
        try:
            shutil.copyfile(path, dst)
        except Exception as e:
            messagebox.showerror(title, "复制失败：%s" % e, parent=self.win)
            return
        combobox.config(values=list_dir_files(folder, exts))
        var.set(name)
        self.lb_state.config(text="已导入 %s" % name, fg="#57d98a")

    def import_font(self):
        self._import_into(
            FONTS, [("字体文件", "*.ttf *.otf *.ttc")], (".ttf", ".otf", ".ttc"),
            "选择字体文件", self.v_font_file, self.cb_font)
        self.v_font.set("ttf")

    def import_bg(self):
        self._import_into(
            BGS, [("图片", "*.jpg *.jpeg *.png *.webp *.bmp")],
            (".jpg", ".jpeg", ".png", ".webp", ".bmp"),
            "选择背景图片", self.v_bg_image, self.cb_bg)
        self.v_bg_mode.set("image")

    # ---- 系统
    def apply_autostart(self):
        want = bool(self.v_auto.get())
        ok, msg = set_autostart(want)
        if ok:
            self.lb_auto.config(text=msg, fg="#57d98a")
        else:
            self.v_auto.set(not want)
            self.lb_auto.config(text=msg, fg="#e05c5c")

    def make_shortcut(self):
        ok, msg = create_desktop_shortcut()
        if ok:
            self.lb_lnk.config(text="已创建：" + msg, fg="#57d98a")
        else:
            self.lb_lnk.config(text="失败：" + str(msg), fg="#e05c5c")

    # ---- 一言
    def _qref_value(self):
        """刷新间隔读成 int；乱填就按 30 分钟算，别让保存直接失败。"""
        try:
            return max(0, int(float(self.v_qref.get())))
        except Exception:
            return 30

    def refresh_quote_click(self):
        """立即换一句：先把当前填的地址/分类存进去，再请求后端强制刷新。"""
        self.lb_quote.config(text="正在取…", fg="#ffbe5c")

        def work():
            msg, color = "", "#6d8296"
            try:
                d = load_settings()
                d["quote_api"] = (self.v_qapi.get().strip()
                                  or "https://v1.hitokoto.cn/?c={cats}")
                d["quote_cats"] = self.v_qcats.get().strip()
                d["quote_refresh"] = self._qref_value()
                save_settings(d)
            except Exception as e:
                logerr("保存一言设置失败: %r" % (e,))
            try:
                req = urllib.request.Request(URL + "api/quote/refresh",
                                             data=b"{}", method="POST",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    j = json.loads(r.read().decode("utf-8"))
                q = j.get("quote") or {}
                if q.get("text"):
                    who = q.get("who") or q.get("from") or ""
                    msg = "取到了：" + q["text"] + ("　—— " + who if who else "")
                    color = "#57d98a"
                else:
                    msg = "接口没返回内容，检查地址是不是返回 JSON"
                    color = "#ffbe5c"
            except Exception as e:
                msg = "请求失败：%s（监控没启动时会这样）" % (e,)
                color = "#e05c5c"
            self.root.after(0, lambda: self.lb_quote.config(text=msg, fg=color))

        threading.Thread(target=work, daemon=True).start()

    # ---- 保存
    def save(self):
        from tkinter import messagebox
        try:
            lat = float(self.v_lat.get())
            lon = float(self.v_lon.get())
        except ValueError:
            messagebox.showerror("数字格式错", "纬度和经度要填数字", parent=self.win)
            return
        d = load_settings()
        d.update(city=self.v_city.get().strip() or "杭州市",
                 latitude=lat, longitude=lon,
                 weather=bool(self.v_wx.get()), apps=self.apps,
                 screen=int(self.screen_map.get(self.v_screen.get(), 0)),
                 font=self.v_font.get() or "system",
                 font_file=self.v_font_file.get() or "",
                 bg_mode=self.v_bg_mode.get() or "gradient",
                 bg_image=self.v_bg_image.get() or "",
                 bg_effect=self.v_bg_effect.get() or "blur",
                 bg_blur=int(self.v_bg_blur.get()),
                 bg_opacity=int(self.v_bg_op.get()),
                 bg_contrast=self.v_bg_contrast.get() or "auto",
                 quote_api=self.v_qapi.get().strip() or "https://v1.hitokoto.cn/?c={cats}",
                 quote_cats=self.v_qcats.get().strip(),
                 quote_refresh=self._qref_value(),
                 weather_src=_wsrc_from_label(self.v_wsrc.get()))
        try:
            save_settings(d)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.win)
            return
        self.lb_state.config(text="已保存 · Dock 20 秒内自动刷新", fg="#57d98a")
        if self.on_status_change:
            self.on_status_change()

    # ---- 启停
    def start(self):
        if server_alive():
            self.lb_state.config(text="监控已在运行", fg="#8fa7ba")
            return
        self.lb_state.config(text="正在启动…", fg="#ffbe5c")
        run_ps(["-Port", str(PORT)])
        threading.Thread(target=self._wait_up, daemon=True).start()

    def _wait_up(self):
        for _ in range(40):
            time.sleep(0.5)
            if server_alive():
                self.root.after(0, self.refresh_state)
                return
        self.root.after(0, lambda: self.lb_state.config(
            text="启动超时，看 monitor.log.err", fg="#e05c5c"))

    def stop(self):
        from tkinter import messagebox
        if not messagebox.askyesno("停止监控", "确定停止副屏监控？\n（服务、采集桥、全屏窗口都会关掉）",
                                   parent=self.win):
            return
        self.lb_state.config(text="正在停止…", fg="#ffbe5c")
        run_ps(["-Stop", "-Port", str(PORT)])
        threading.Thread(target=self._wait_down, daemon=True).start()

    def _wait_down(self):
        for _ in range(30):
            time.sleep(0.4)
            if not server_alive():
                break
        self.root.after(0, self.refresh_state)

    def refresh_state(self):
        if not (self.win and self.win.winfo_exists()):
            return
        alive = server_alive()
        self.lb_state.config(
            text=("● 监控运行中  " + URL) if alive else "○ 监控未运行",
            fg="#57d98a" if alive else "#8fa7ba")
        self.b_start.config(state="disabled" if alive else "normal")
        self.b_stop.config(state="normal" if alive else "disabled")


def logerr(msg):
    """pythonw 下 stderr 是 None，任何异常都必须落文件，否则静默失败查不出来"""
    try:
        with open(os.path.join(ROOT, "launcher.err.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 托盘
def main():
    import tkinter as tk
    import pystray

    # 单实例
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        logerr("launcher 已在运行，本次退出")
        return

    root = tk.Tk()
    root.withdraw()
    root.title("副屏监控")
    root.report_callback_exception = lambda *a: logerr("tk: " + repr(a))

    state = {"win": None}

    def get_settings_window():
        if state["win"] is None:
            state["win"] = SettingsWindow(root, on_status_change=refresh_tray)
        return state["win"]

    def do_open():
        root.after(0, lambda: get_settings_window().show())

    def do_start():
        if not server_alive():
            run_ps(["-Port", str(PORT)])
        refresh_tray()

    def do_stop():
        run_ps(["-Stop", "-Port", str(PORT)])
        refresh_tray()

    def do_quit():
        icon.stop()
        root.after(0, root.destroy)

    def do_web():
        import webbrowser
        webbrowser.open(URL)

    def do_reshow():
        """把监控画面重新拉回副屏。

        用途：万一在 kiosk 全屏里误开了页面（比如点邮箱链接跳到网页），
        面板被顶掉了、又没法退出全屏，从托盘点这一项就能一键恢复。
        直接跑 start_monitor.ps1：它会关掉旧监控窗口再重开一个全屏 kiosk。
        """
        run_ps(["-Port", str(PORT)])
        refresh_tray()

    def do_toggle_auto():
        ok, msg = set_autostart(not autostart_enabled())
        logerr("toggle autostart: %s %s" % (ok, msg))
        refresh_tray()

    def do_shortcut():
        ok, msg = create_desktop_shortcut()
        try:
            icon.notify(msg if ok else ("创建失败：" + str(msg)), "桌面快捷方式")
        except Exception:
            pass

    def tray_text(item=None):
        return ("监控运行中" if server_alive() else "监控未运行")

    def tray_auto(item=None):
        return ("取消开机自启" if autostart_enabled() else "开机自启")

    img = make_tray_image()
    menu = pystray.Menu(
        pystray.MenuItem(tray_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("启动监控", lambda: threading.Thread(target=do_start, daemon=True).start(),
                         default=True),
        pystray.MenuItem("停止监控", lambda: threading.Thread(target=do_stop, daemon=True).start()),
        pystray.MenuItem("重新显示监控画面", lambda: threading.Thread(target=do_reshow, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("设置…", lambda: do_open()),
        pystray.MenuItem("打开监控页面", lambda: do_web()),
        pystray.MenuItem(tray_auto, lambda: threading.Thread(target=do_toggle_auto, daemon=True).start()),
        pystray.MenuItem("创建桌面快捷方式", lambda: threading.Thread(target=do_shortcut, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", lambda: do_quit()),
    )
    icon = pystray.Icon("subscreen-monitor", img, "副屏监控", menu)
    state["icon"] = icon

    def refresh_tray():
        try:
            icon.update_menu()
        except Exception as e:
            logerr("update_menu: " + repr(e))

    def run_icon():
        try:
            icon.run()          # 阻塞，直到 icon.stop()
            logerr("icon.run 正常结束")
        except Exception:
            import traceback
            logerr("icon.run 异常:\n" + traceback.format_exc())

    def ticker():
        n = 0
        while True:
            time.sleep(5)
            n += 1
            try:
                icon.update_menu()
            except Exception as e:
                logerr("ticker: " + repr(e))
            if n == 1:
                logerr("心跳 OK，托盘已启动（pystray %s）" %
                       getattr(pystray, "__version__", "?"))

    threading.Thread(target=run_icon, daemon=True).start()
    threading.Thread(target=ticker, daemon=True).start()

    # 不弹窗口（用户要的就是「缩成托盘」），只弹一条气泡提示它在哪儿
    def hello():
        time.sleep(1.2)
        try:
            icon.notify("托盘已就绪，右键图标可 停止监控 / 设置", "副屏监控")
        except Exception as e:
            logerr("notify: " + repr(e))

    threading.Thread(target=hello, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        logerr("fatal:\n" + traceback.format_exc())
        raise
