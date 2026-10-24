# -*- coding: utf-8 -*-
"""
副屏监控 - 服务端

启动:  python app.py
访问:  http://127.0.0.1:8770/

接口:
  GET /api/snapshot        一次性拿全部数据
  GET /api/stream          SSE 实时推送（默认 1 秒一次）
  GET /api/aida64/status   看 AIDA64 有没有接上
"""
import argparse
import json
import logging
import os
import sys
import time

from flask import Flask, Response, jsonify, send_from_directory

# 允许 `python app.py` 直接跑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sensors import hardware, media, network  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
app.config["JSON_AS_ASCII"] = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")


@app.after_request
def no_cache(resp):
    if resp.mimetype in ("text/html", "application/javascript", "text/css"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/api/snapshot")
def api_snapshot():
    try:
        return jsonify(hardware.snapshot())
    except Exception as e:
        log.exception("采集失败")
        return jsonify({"error": str(e)}), 500


@app.route("/api/stream")
def api_stream():
    interval = float(os.environ.get("MONITOR_INTERVAL", "1.0"))

    def gen():
        # 先吐一帧，让前端立刻有数据
        while True:
            try:
                data = hardware.snapshot()
                yield "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
            except GeneratorExit:
                return
            except Exception as e:
                log.debug("stream 采集异常: %s", e)
                yield "data: " + json.dumps({"error": str(e)}) + "\n\n"
            time.sleep(interval)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@app.route("/api/aida64/status")
def api_aida_status():
    from sensors import aida64, lhm
    r = aida64.read()
    return jsonify({
        "ok": r["ok"],
        "value_count": len(r["raw"]),
        "mapped": r["values"],
        "hint": None if r["ok"] else
        "AIDA64 -> 文件 -> 设置 -> 外部应用程序 -> 勾选「允许将传感器值写入注册表」",
    })


@app.route("/api/sources")
def api_sources():
    """三条采集通道各自的状态，排障用"""
    from sensors import aida64, lhm
    a = aida64.status()
    l = lhm.status()
    return jsonify({
        "aida64": a,
        "lhm": l,
        "priorities": ["aida64", "lhm", "psutil/nvidia-smi"],
    })


# ============================================================
#  音乐播放器（Windows SMTC + pycaw 音量 + 歌词检索）
# ============================================================
@app.route("/api/media")
def api_media():
    try:
        return jsonify(media.status())
    except Exception as e:
        log.exception("取媒体状态失败")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/lyrics")
def api_lyrics():
    """整首歌词一次性给前端做滚动 PV（只读缓存，不阻塞）。
    参数给全就用参数，否则按当前在播的歌取。"""
    from flask import request
    try:
        title = (request.args.get("title") or "").strip()
        artist = (request.args.get("artist") or "").strip()
        dur = 0.0
        try:
            dur = float(request.args.get("dur") or 0)
        except Exception:
            dur = 0.0
        if not title:
            st = media.read()
            title = st.get("title") or ""
            artist = st.get("artist") or ""
            dur = st.get("duration") or 0.0
        lines = media.lyric_lines(title, artist, dur) if title else []
        return jsonify({"ok": True, "title": title, "artist": artist,
                        "count": len(lines), "lines": lines})
    except Exception as e:
        log.exception("取整首歌词失败")
        return jsonify({"ok": False, "error": str(e), "lines": [], "count": 0})


@app.route("/api/media/control", methods=["POST"])
def api_media_control():
    from flask import request
    try:
        body = request.get_json(force=True, silent=True) or {}
        act = (body.get("action") or "").strip()
        if act not in ("play", "pause", "toggle", "next", "prev", "stop"):
            return jsonify({"ok": False, "error": f"不支持的动作: {act}"}), 400
        return jsonify(media.control(act))
    except Exception as e:
        log.exception("媒体控制失败")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/media/volume", methods=["POST"])
def api_media_volume():
    from flask import request
    try:
        body = request.get_json(force=True, silent=True) or {}
        if "mute" in body:
            return jsonify(media.set_mute(bool(body.get("mute"))))
        pct = float(body.get("percent"))
        return jsonify(media.set_volume(pct))
    except Exception as e:
        log.exception("音量设置失败")
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================
#  快捷启动 Dock  /  设置
# ============================================================
import subprocess  # noqa: E402
import threading  # noqa: E402

SETTINGS = os.path.join(ROOT, "settings.json")

# POST /api/settings 允许写的键（白名单，防止随便往 settings.json 里塞东西）
SETTING_KEYS = (
    "city", "latitude", "longitude", "weather", "apps",
    # 外观 / 显示
    "screen", "font", "font_file",
    "bg_mode", "bg_image", "bg_effect", "bg_blur", "bg_opacity", "bg_contrast",
    # 一言源与刷新
    "quote_api", "quote_cats", "quote_refresh",
    # 天气源
    "weather_src",
)


def _load_settings():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(d):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)


def _settings_mtime():
    try:
        return os.path.getmtime(SETTINGS)
    except OSError:
        return 0.0


_api_cache = {"t": 0.0, "apps": [], "version": ""}


def _apps():
    """读 settings.json 里的 apps，带 mtime 缓存。version 用来让前端判断要不要重渲染。"""
    now = time.time()
    mt = _settings_mtime()
    ver = str(mt)
    if now - _api_cache["t"] < 0.8 and _api_cache["version"] == ver:
        return _api_cache["apps"], ver

    d = _load_settings()
    out = []
    for a in (d.get("apps") or []):
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip()
        path = (a.get("path") or "").strip()
        if not name or not path:
            continue
        icon = a.get("icon")
        if not icon:
            icon = "/static/icons/_default.svg"
        elif not str(icon).startswith(("http://", "https://", "/")):
            icon = "/static/icons/" + os.path.basename(str(icon))
        out.append({"name": name, "path": path, "icon": icon,
                    "args": a.get("args") or ""})
    _api_cache.update(t=now, apps=out, version=ver)
    return out, ver


@app.route("/api/apps")
def api_apps():
    apps, _ = _apps()
    return jsonify({"ok": True, "apps": apps})


@app.route("/api/apps/version")
def api_apps_version():
    _, ver = _apps()
    return jsonify({"ok": True, "version": ver})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    d = _load_settings()
    d.setdefault("city", "杭州市")
    d.setdefault("apps", [])
    return jsonify(d)


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    from flask import request
    try:
        body = request.get_json(force=True, silent=True) or {}
        cur = _load_settings()
        for k in SETTING_KEYS:
            if k in body:
                cur[k] = body[k]
        _save_settings(cur)
        _api_cache["t"] = 0.0        # 立刻失效缓存
        # 城市名 / 天气源 / 坐标改过之后，天气缓存和站点码缓存都要重来，
        # 否则用户改完城市还得干等 10 分钟。
        try:
            network._settings_cache["t"] = 0.0
            network._weather_cache.update(t=0.0, v=None)
            network._cn_code_cache.update(t=0.0, city="", code="")
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("保存设置失败")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------- 一言：立即换一句 ----------------
@app.route("/api/quote/refresh", methods=["POST"])
def api_quote_refresh():
    """强制重新取一句（设置里「立即换一句」用）。

    顺带把 settings 缓存也打掉，这样刚刚保存的新接口地址/分类能立刻生效，
    不用等 network.settings() 那 5 秒缓存过期。
    """
    try:
        network._settings_cache["t"] = 0.0
        return jsonify({"ok": True, "quote": network.quote(force=True)})
    except Exception as e:
        log.exception("刷新一言失败")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------- 外观：字体 / 背景 / 副屏 ----------------
FONTS = os.path.join(ROOT, "fonts")
BGS = os.path.join(ROOT, "backgrounds")


@app.route("/api/theme")
def api_theme():
    """外观相关设置（前端拿去切 body class、设背景图 src）。"""
    d = _load_settings()
    bg_mode = (d.get("bg_mode") or "gradient").lower()
    img = _safe_name(d.get("bg_image"))
    stats = _bg_stats(img) if bg_mode == "image" else None
    return jsonify({
        "ok": True,
        "font": d.get("font") or "system",
        "font_file": d.get("font_file") or "",
        "bg_mode": d.get("bg_mode") or "gradient",
        "bg_image": d.get("bg_image") or "",
        "bg_effect": d.get("bg_effect") or "blur",
        "bg_blur": int(d.get("bg_blur") or 18),
        "bg_opacity": int(d.get("bg_opacity") or 100),
        "bg_contrast": d.get("bg_contrast") or "auto",
        "bg_stats": stats,
    })


def _safe_name(s):
    """只允许纯文件名，挡住 ../ 之类的路径穿越。"""
    s = os.path.basename(str(s or "").replace("\\", "/"))
    return s if s and s not in (".", "..") else ""


# ---------- 背景图亮度分析（自动对比度） ----------
# 换成自定义背景图后，浅色照片会让卡片里的浅灰字看不清。
# 这里把图缩到 32x32 估算上/中/下三段亮度，再据此自动加遮罩、加深玻璃、提亮次要文字色。
_bg_stats_cache = {"key": None, "v": None}


def _bg_stats(name):
    """返回 {lum, top, mid, bot, std}（0~1 相对亮度），失败返回 None。"""
    if not name:
        return None
    path = os.path.join(BGS, name)
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (name, int(st.st_mtime), st.st_size)
    if _bg_stats_cache.get("key") == key:
        return _bg_stats_cache.get("v")
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB").resize((32, 32))
            px = list(im.getdata())
    except Exception as e:
        log.warning("背景图亮度分析失败: %s", e)
        return None
    if not px:
        return None

    def _lum(c):
        r, g, b = (x / 255.0 for x in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    ls = [_lum(c) for c in px]
    n = len(ls)
    mean = sum(ls) / n
    third = max(1, n // 3)
    top = sum(ls[:third]) / third
    mid = sum(ls[third:2 * third]) / max(1, third)
    bot = sum(ls[2 * third:]) / max(1, n - 2 * third)
    std = (sum((x - mean) ** 2 for x in ls) / n) ** 0.5
    v = {"lum": mean, "top": top, "mid": mid, "bot": bot, "std": std}
    _bg_stats_cache.update(key=key, v=v)
    return v


@app.route("/api/theme.css")
def api_theme_css():
    """动态 CSS：自定义字体的 @font-face + 两个受设置影响的 CSS 变量。

    必须排在 style.css 之后引入，否则会被 style.css 的默认值盖掉。
    """
    d = _load_settings()
    font = (d.get("font") or "system").lower()
    ff = _safe_name(d.get("font_file"))
    blur = int(d.get("bg_blur") or 18)
    op = int(d.get("bg_opacity") or 100)
    op = max(0, min(100, op))

    # @font-face 是顶层规则，绝对不能塞进 :root{} 里面，否则整条无效（自定义字体会不生效）。
    use_custom = font == "ttf" and ff and os.path.exists(os.path.join(FONTS, ff))
    parts = []
    if use_custom:
        parts.append('@font-face{font-family:"GlancePad-Custom";'
                     'src:url("/fonts/%s");font-display:swap;}' % ff)

    parts.extend([":root{", "--bg-blur:%dpx;" % blur, "--bg-opacity:%.2f;" % (op / 100.0)])
    if use_custom:
        parts.append('--font-family:"GlancePad-Custom",'
                     '"HarmonyOS Sans SC","Microsoft YaHei UI",sans-serif;')
    else:
        parts.append('--font-family:"HarmonyOS Sans SC","Microsoft YaHei UI",'
                     '"PingFang SC",-apple-system,"Segoe UI",sans-serif;')
    parts.append("}")
    css = "".join(parts)
    return Response(css, mimetype="text/css",
                    headers={"Cache-Control": "no-store, max-age=0"})


@app.route("/fonts/<path:name>")
def serve_font(name):
    return send_from_directory(FONTS, name)


@app.route("/backgrounds/<path:name>")
def serve_bg(name):
    return send_from_directory(BGS, name)


@app.route("/api/files")
def api_files():
    """给设置界面列出已有的自定义字体 / 背景图。"""
    def ls(p, exts):
        try:
            return sorted([f for f in os.listdir(p)
                           if os.path.isfile(os.path.join(p, f))
                           and f.lower().endswith(exts)])
        except OSError:
            return []
    return jsonify({"ok": True,
                    "fonts": ls(FONTS, (".ttf", ".otf", ".ttc")),
                    "backgrounds": ls(BGS, (".jpg", ".jpeg", ".png", ".webp", ".bmp"))})


@app.route("/api/screens")
def api_screens():
    """枚举显示器，按系统顺序 + 主屏标志，供设置界面选副屏。

    注意：不能按 \\\\.\\DISPLAYn 的设备名判断（多显示器时编号会变），
    只能按 EnumDisplayMonitors 的返回顺序 + Primary 标志。
    """
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    out = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(RECT), wintypes.LPARAM)

    def _cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append({
                "index": len(out) + 1,
                "device": mi.szDevice,
                "x": r.left, "y": r.top,
                "w": r.right - r.left, "h": r.bottom - r.top,
                "primary": bool(mi.dwFlags & 1),
            })
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, MONITORENUMPROC(_cb), 0)
    except Exception as e:
        log.warning("枚举显示器失败: %s", e)
    return jsonify({"ok": True, "screens": out})


@app.route("/api/launch", methods=["POST"])
def api_launch():
    """点 Dock 图标 -> 起进程。只允许 settings.json 里登记过的路径，防止被当任意命令执行器。"""
    from flask import request
    try:
        body = request.get_json(force=True, silent=True) or {}
        path = (body.get("path") or "").strip()
        name = (body.get("name") or "").strip()
        apps, _ = _apps()
        allowed = {a["path"]: a for a in apps}
        if path not in allowed:
            return jsonify({"ok": False, "error": "路径不在快捷启动列表里"}), 403
        if not os.path.exists(path):
            return jsonify({"ok": False, "error": f"文件不存在: {path}"}), 404

        a = allowed[path]
        workdir = os.path.dirname(path) or None
        extra = (a.get("args") or "").strip()

        # 优先用 os.startfile（ShellExecuteEx）：最接近资源管理器里双击的效果，
        # 能正确处理启动器类程序（哔哩哔哩、图吧工具箱这种）和需要提权的程序。
        # 之前统一用 DETACHED_PROCESS + Popen 时，这类"二次 ShellExecute 子进程"的
        # 启动器拿不到可用桌面会话，会静默退出，表现就是点了没反应。
        if not extra:
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                log.info("启动应用(ShellExecute): %s (%s)", name or os.path.basename(path), path)
                return jsonify({"ok": True})
            except OSError as e:
                log.warning("ShellExecute 失败，回退 Popen: %s", e)

        # 兜底：需要带启动参数，或 startfile 不可用时走 Popen
        cmd = [path] + (extra.split() if extra else [])
        creation = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen(cmd, cwd=workdir, close_fds=True,
                         creationflags=creation,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        log.info("启动应用: %s (%s)", name or os.path.basename(path), path)
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("启动失败")
        return jsonify({"ok": False, "error": str(e)}), 500


# 允许从面板「外部打开」的站点白名单（只放行邮箱登录页的域名）。
# 为什么需要外部打开：副屏面板是 Chrome --kiosk 全屏，window.open 出来的新标签
# 会顶掉面板本身，而 kiosk 没有标签栏/地址栏，用户就再也回不到监控画面了。
# 所以邮箱链接交给系统默认浏览器（主屏普通窗口）打开，面板保持不动。
_OPEN_URL_HOSTS = {
    "mail.163.com", "mail.126.com",
    "outlook.live.com", "outlook.office.com",
    "mail.google.com", "accounts.google.com",
}


@app.route("/api/open-url", methods=["POST"])
def api_open_url():
    """用系统默认浏览器打开白名单内的网址（副屏面板保持不动）。"""
    from flask import request
    from urllib.parse import urlparse
    try:
        body = request.get_json(force=True, silent=True) or {}
        url = (body.get("url") or "").strip()
        if not url:
            return jsonify({"ok": False, "error": "缺少 url"}), 400
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return jsonify({"ok": False, "error": "只允许 http/https"}), 403
        host = (p.hostname or "").lower()
        if host not in _OPEN_URL_HOSTS:
            return jsonify({"ok": False, "error": "该域名不在白名单里: %s" % host}), 403
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except OSError:
            import webbrowser
            if not webbrowser.open(url):
                return jsonify({"ok": False, "error": "无法调用默认浏览器"}), 500
        log.info("外部浏览器打开: %s", url)
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("打开网址失败")
        return jsonify({"ok": False, "error": str(e)}), 500


def _first_exist(paths):
    """返回第一个真实存在的路径，都不存在则 None"""
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def _probe_apps():
    """按常见安装位置探测可用应用；探不到的就不加，避免 Dock 里出现死图标。
    想加别的程序请用「托盘右键 → 设置…」或直接改 settings.json。"""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad = os.environ.get("LOCALAPPDATA", "")
    windir = os.environ.get("SystemRoot", r"C:\Windows")

    def j(base, *parts):
        return os.path.join(base, *parts) if base else None

    # (显示名, 候选路径列表, 图标文件)
    cand = [
        ("Chrome", [j(pf, "Google", "Chrome", "Application", "chrome.exe"),
                    j(pf86, "Google", "Chrome", "Application", "chrome.exe"),
                    j(lad, "Google", "Chrome", "Application", "chrome.exe")], "chrome.png"),
        ("Edge", [j(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
                  j(pf, "Microsoft", "Edge", "Application", "msedge.exe")], "edge.png"),
        ("VS Code", [j(lad, "Programs", "Microsoft VS Code", "Code.exe"),
                     j(pf, "Microsoft VS Code", "Code.exe")], "vscode.png"),
        ("微信", [j(pf, "Tencent", "WeChat", "WeChat.exe"),
                  j(pf86, "Tencent", "WeChat", "WeChat.exe"),
                  j(pf, "Tencent", "Weixin", "Weixin.exe")], "wechat.png"),
        ("QQ", [j(pf, "Tencent", "QQNT", "QQ.exe"),
                j(pf86, "Tencent", "QQ", "Bin", "QQ.exe")], "qq.png"),
        ("网易云音乐", [j(pf, "NetEase", "CloudMusic", "cloudmusic.exe"),
                        j(pf86, "NetEase", "CloudMusic", "cloudmusic.exe")], "music.png"),
        ("Steam", [j(pf86, "Steam", "Steam.exe"),
                   j(pf, "Steam", "Steam.exe")], "steam.png"),
        ("PotPlayer", [j(pf, "DAUM", "PotPlayer", "PotPlayerMini64.exe"),
                       j(pf86, "DAUM", "PotPlayer", "PotPlayerMini64.exe")], "potplayer.png"),
        ("任务管理器", [j(windir, "System32", "Taskmgr.exe")], "taskmgr.png"),
        ("计算器", [j(windir, "System32", "calc.exe")], "calc.png"),
        ("终端", [j(windir, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")], "term.png"),
    ]
    apps = []
    for name, paths, icon in cand:
        p = _first_exist(paths)
        if p:
            apps.append({"name": name, "path": p, "icon": icon})
    return apps


def _ensure_settings():
    """首次运行时落一份默认设置；老版本设置文件会自动补齐新增的键。

    注意：后端读取（sensors/network.py:settings）本来就会用 _defaults 兜底，
    这里补齐只是为了让 settings.json 文件本身也完整，便于用户手改和设置界面回填。
    """
    defaults = {
        "city": "杭州市", "latitude": 30.2741, "longitude": 120.1551,
        "weather": True, "apps": [],
        # 显示（v1.1）
        "screen": 0, "font": "system", "font_file": "",
        "bg_mode": "gradient", "bg_image": "",
        "bg_effect": "blur", "bg_blur": 18, "bg_opacity": 100,
        # 背景图自动对比度（v1.1.2）：auto / off
        "bg_contrast": "auto",
        # 一言（v1.1.1）：接口地址、hitokoto 分类、刷新间隔（分钟，0=每天一次）
        "quote_api": "https://v1.hitokoto.cn/?c={cats}",
        "quote_cats": "d,i,k",
        "quote_refresh": 30,
        # 天气源（v1.1.1）：auto / cn / open-meteo
        "weather_src": "auto",
    }
    if not os.path.exists(SETTINGS):
        defaults["apps"] = _probe_apps()
        _save_settings(defaults)
        log.info("已生成默认 settings.json（%d 个快捷启动项）", len(defaults["apps"]))
        return

    cur = _load_settings()
    if not isinstance(cur, dict):
        cur = {}
    missing = [k for k in defaults if k not in cur]
    if not missing:
        return
    # 只补缺失键，不动用户已有值
    for k in missing:
        cur[k] = defaults[k]
    if cur.get("apps") is None:
        cur["apps"] = defaults["apps"]
    try:
        _save_settings(cur)
        log.info("settings.json 已补齐新键: %s", ", ".join(missing))
    except Exception as e:  # 只读目录等情况，不影响启动
        log.warning("补齐 settings.json 失败: %s", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    os.environ["MONITOR_INTERVAL"] = str(args.interval)

    _ensure_settings()

    log.info("副屏监控启动 -> http://%s:%d/", args.host, args.port)
    log.info("数据源优先级: AIDA64 > LibreHardwareMonitor > psutil/nvidia-smi")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
