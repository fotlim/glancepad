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

from sensors import hardware, media  # noqa: E402

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
        for k in ("city", "latitude", "longitude", "weather", "apps"):
            if k in body:
                cur[k] = body[k]
        _save_settings(cur)
        _api_cache["t"] = 0.0        # 立刻失效缓存
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("保存设置失败")
        return jsonify({"ok": False, "error": str(e)}), 500


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
    """首次运行时落一份默认设置（只放入本机真实存在的应用）"""
    if os.path.exists(SETTINGS):
        return
    apps = _probe_apps()
    _save_settings({"city": "杭州市", "latitude": 30.2741,
                    "longitude": 120.1551, "weather": True, "apps": apps})
    log.info("已生成默认 settings.json（%d 个快捷启动项）", len(apps))


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
