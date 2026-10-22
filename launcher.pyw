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
}


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
    try:
        with urllib.request.urlopen(URL + "api/snapshot", timeout=1.5) as r:
            return r.status == 200
    except Exception:
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


def icon_files():
    try:
        return sorted([f for f in os.listdir(ICONS)
                       if f.lower().endswith((".png", ".ico", ".jpg", ".svg"))])
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
        w.geometry("760x620")
        w.minsize(680, 540)
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

        field(t2, 0, "城市名", self.v_city, "顶部右上角天气卡显示的名字")
        field(t2, 1, "纬度", self.v_lat, "影响天气数据，如 30.2741")
        field(t2, 2, "经度", self.v_lon, "如 120.1551")
        tk.Checkbutton(t2, text="在顶部显示天气", variable=self.v_wx,
                       bg="#1b242b", fg="#cfe0ee", selectcolor="#131a20",
                       activebackground="#1b242b", activeforeground="#4fd1c5",
                       relief="flat", anchor="w").grid(row=3, column=1,
                                                       pady=(4, 8), sticky="w")
        tk.Label(t2, text="天气来自 open-meteo.com，免费无需 key；取不到时自动显示「—」",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=4, column=0, columnspan=3, padx=16, pady=(6, 0), sticky="w")
        tk.Label(t2, text="城市名不会自动反查经纬度，想换城市请一起改经纬度",
                 bg="#1b242b", fg="#6d8296", anchor="w").grid(
            row=5, column=0, columnspan=3, padx=16, pady=(2, 0), sticky="w")

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
                 weather=bool(self.v_wx.get()), apps=self.apps)
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

    def tray_text(item=None):
        return ("监控运行中" if server_alive() else "监控未运行")

    img = make_tray_image()
    menu = pystray.Menu(
        pystray.MenuItem(tray_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("启动监控", lambda: threading.Thread(target=do_start, daemon=True).start(),
                         default=True),
        pystray.MenuItem("停止监控", lambda: threading.Thread(target=do_stop, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("设置…", lambda: do_open()),
        pystray.MenuItem("打开监控页面", lambda: do_web()),
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
