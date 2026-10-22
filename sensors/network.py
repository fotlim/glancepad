# -*- coding: utf-8 -*-
"""
网络信息采集。

两个独立通道：
  1. WiFi 状态 —— netsh wlan show interfaces（不花流量、不需要管理员）
  2. 天气     —— open-meteo.com（免 API key、免注册），按 settings.json 里的经纬度取

两者都带缓存，取不到就返回上一份好数据（stale），绝不抛给上层。
"""
import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, "settings.json")

# WMO 天气代码 -> (中文, emoji)
_WMO = {
    0: ("晴", "☀️"),
    1: ("大部晴朗", "🌤️"),
    2: ("多云", "⛅"),
    3: ("阴", "☁️"),
    45: ("雾", "🌫️"),
    48: ("雾凇", "🌫️"),
    51: ("小毛毛雨", "🌦️"),
    53: ("毛毛雨", "🌦️"),
    55: ("大毛毛雨", "🌧️"),
    56: ("冻毛毛雨", "🌧️"),
    57: ("强冻毛毛雨", "🌧️"),
    61: ("小雨", "🌦️"),
    63: ("中雨", "🌧️"),
    65: ("大雨", "🌧️"),
    66: ("冻雨", "🌧️"),
    67: ("强冻雨", "🌧️"),
    71: ("小雪", "🌨️"),
    73: ("中雪", "🌨️"),
    75: ("大雪", "❄️"),
    77: ("米雪", "🌨️"),
    80: ("阵雨", "🌦️"),
    81: ("强阵雨", "🌧️"),
    82: ("暴雨", "⛈️"),
    85: ("阵雪", "🌨️"),
    86: ("强阵雪", "❄️"),
    95: ("雷阵雨", "⛈️"),
    96: ("雷阵雨伴冰雹", "⛈️"),
    99: ("强雷阵雨伴冰雹", "⛈️"),
}


# ---------------------------------------------------------------- 配置
_defaults = {
    "city": "杭州市",
    "latitude": 30.2741,
    "longitude": 120.1551,
    "weather": True,
    "apps": [],
}

_settings_cache = {"t": 0.0, "v": None}


def settings():
    """读 settings.json；文件不存在就给默认值（不报错）。"""
    now = time.time()
    if now - _settings_cache["t"] < 5 and _settings_cache["v"] is not None:
        return _settings_cache["v"]
    v = dict(_defaults)
    try:
        if os.path.exists(SETTINGS):
            with open(SETTINGS, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                v.update({k: d[k] for k in d if k in _defaults or True})
    except Exception as e:
        log.debug("读 settings.json 失败: %s", e)
    _settings_cache.update(t=now, v=v)
    return v


# ---------------------------------------------------------------- WiFi
_wifi_cache = {"t": 0.0, "v": None}
_WIFI_TTL = 4.0


def _netsh_wlan():
    """解析 netsh wlan show interfaces，返回第一个已连接接口的字段。"""
    try:
        p = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                           capture_output=True, timeout=6,
                           creationflags=_CREATE_NO_WINDOW)
    except Exception as e:
        log.debug("netsh 调用失败: %s", e)
        return None
    raw = p.stdout or b""
    for enc in ("mbcs", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    if not text.strip():
        return None

    info = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not k or not v:
            continue
        info[k] = v

    # 中英文系统字段名都兼容（netsh 输出随系统语言变化）
    def pick(*names):
        for n in names:
            if n in info and info[n]:
                return info[n]
        return None

    ssid = pick("SSID", "SSID 名称", "SSID 名")
    if not ssid:
        return None

    sig = pick("Signal", "信号")
    sig_pct = None
    if sig:
        m = sig.replace("%", "").strip()
        try:
            sig_pct = int(float(m))
        except ValueError:
            sig_pct = None

    def num(*names):
        for n in names:
            v = pick(n)
            if not v:
                continue
            try:
                return float(str(v).replace("%", "").strip())
            except ValueError:
                continue
        return None

    rx = num("Receive rate (Mbps)", "接收速率(Mbps)", "接收速率 (Mbps)")
    tx = num("Transmit rate (Mbps)", "传输速率(Mbps)", "传输速率 (Mbps)")
    # 极少数系统把速率写成 "585"；取不到就留空，不瞎编
    return {
        "name": pick("Name", "名称"),
        "desc": pick("Description", "描述", "说明"),
        "ssid": ssid,
        "bssid": pick("BSSID"),
        "state": pick("State", "状态"),
        "radio": pick("Radio type", "无线电类型"),
        "auth": pick("Authentication", "身份验证"),
        "band": (pick("Band", "频带") or "").strip(),
        "channel": pick("Channel", "通道", "信道"),
        "rx_rate": rx,
        "tx_rate": tx,
        "signal": sig_pct,
        "signal_raw": sig,
    }


def _if_addrs(ifname):
    """取网卡的 IPv4 / IPv6 地址。"""
    out = {"ipv4": None, "ipv6": None}
    if not ifname:
        return out
    try:
        import psutil
        addrs = psutil.net_if_addrs().get(ifname) or []
    except Exception as e:
        log.debug("net_if_addrs 失败: %s", e)
        return out
    for a in addrs:
        fam = str(getattr(a, "family", ""))
        ip = (a.address or "").split("%")[0]
        if not ip:
            continue
        if "AF_INET6" in fam or ":" in ip:
            if out["ipv6"] is None and ip.lower().startswith("fe80"):
                out["ipv6"] = a.address          # 链路本地，带 %15 作用域
            elif out["ipv6"] is None or str(out["ipv6"]).lower().startswith("fe80"):
                out["ipv6"] = ip
        elif "AF_INET" in fam or "." in ip:
            if not ip.startswith("169.254") and out["ipv4"] is None:
                out["ipv4"] = ip
    return out


def wifi():
    """当前 WiFi 状态；没连 WiFi 返回 {ok: False}。"""
    now = time.time()
    if now - _wifi_cache["t"] < _WIFI_TTL and _wifi_cache["v"] is not None:
        return _wifi_cache["v"]

    w = _netsh_wlan()
    if not w:
        v = {"ok": False, "hint": "未检测到 WiFi 连接"}
    else:
        addr = _if_addrs(w.get("name"))
        sig = w.get("signal")
        bars = 0
        if sig is not None:
            bars = 4 if sig >= 75 else 3 if sig >= 50 else 2 if sig >= 25 else 1
        radio = (w.get("radio") or "").strip()
        # "802.11ac" 这种直接显示；有的系统给 "802.11ax (Wi-Fi 6)"
        v = {
            "ok": True,
            "adapter": w.get("name"),
            "desc": w.get("desc"),
            "ssid": w.get("ssid"),
            "bssid": w.get("bssid"),
            "radio": radio.split("(")[0].strip() if radio else None,
            "band": w.get("band"),
            "channel": w.get("channel"),
            "auth": w.get("auth"),
            "rx_mbps": w.get("rx_rate"),
            "tx_mbps": w.get("tx_rate"),
            "signal": sig,
            "bars": bars,
            "ipv4": addr.get("ipv4"),
            "ipv6": addr.get("ipv6"),
        }
    _wifi_cache.update(t=now, v=v)
    return v


# ---------------------------------------------------------------- 天气
_weather_cache = {"t": 0.0, "v": None}
_WEATHER_TTL = 600.0     # 10 分钟，天气不用更勤


def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={
        "User-Agent": "subscreen-monitor/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def weather():
    """open-meteo 当前天气 + 未来几小时预报。失败返回上一份缓存。"""
    st = settings()
    if not st.get("weather", True):
        return {"ok": False, "hint": "天气已关闭"}

    now = time.time()
    if now - _weather_cache["t"] < _WEATHER_TTL and _weather_cache["v"]:
        v = dict(_weather_cache["v"])
        v["cached"] = True
        return v

    lat = st.get("latitude", 30.2741)
    lon = st.get("longitude", 120.1551)
    q = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,sunrise,sunset",
        "timezone": "Asia/Shanghai",
        "forecast_days": 2,
        "wind_speed_unit": "ms",
    })
    url = "https://api.open-meteo.com/v1/forecast?" + q

    try:
        d = _http_json(url)
    except Exception as e:
        log.debug("天气请求失败: %s", e)
        if _weather_cache["v"]:
            v = dict(_weather_cache["v"])
            v["stale"] = True
            return v
        return {"ok": False, "hint": "天气服务不可达"}

    try:
        cur = d.get("current") or {}
        code = int(cur.get("weather_code") or 0)
        text, icon = _WMO.get(code, ("未知", "🌡️"))

        # 当前小时之后的 6 个整点
        hourly = d.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        codes = hourly.get("weather_code") or []
        pops = hourly.get("precipitation_probability") or []
        nowh = (cur.get("time") or "")[:13]
        idx = 0
        for i, t in enumerate(times):
            if t[:13] == nowh:
                idx = i
                break
        nxt = []
        for i in range(idx, min(idx + 7, len(times))):
            c = int(codes[i] or 0) if i < len(codes) else 0
            nxt.append({
                "hour": (times[i][11:16] if len(times[i]) > 15 else times[i]),
                "temp": round(temps[i]) if i < len(temps) and temps[i] is not None else None,
                "icon": _WMO.get(c, ("", "🌡️"))[1],
                "pop": pops[i] if i < len(pops) else None,
            })

        daily = d.get("daily") or {}
        hi = (daily.get("temperature_2m_max") or [None])[0]
        lo = (daily.get("temperature_2m_min") or [None])[0]

        v = {
            "ok": True,
            "city": st.get("city") or "本地",
            "temp": round(float(cur.get("temperature_2m")), 1)
                    if cur.get("temperature_2m") is not None else None,
            "feels": round(float(cur.get("apparent_temperature")), 1)
                     if cur.get("apparent_temperature") is not None else None,
            "text": text,
            "icon": icon,
            "code": code,
            "humidity": cur.get("relative_humidity_2m"),
            "wind": round(float(cur.get("wind_speed_10m") or 0), 1),
            "wind_dir": cur.get("wind_direction_10m"),
            "is_day": cur.get("is_day"),
            "high": round(float(hi)) if hi is not None else None,
            "low": round(float(lo)) if lo is not None else None,
            "hourly": nxt,
            "updated": int(now),
        }
    except Exception as e:
        log.debug("天气解析失败: %s", e)
        if _weather_cache["v"]:
            v = dict(_weather_cache["v"])
            v["stale"] = True
            return v
        return {"ok": False, "hint": "天气数据异常"}

    _weather_cache.update(t=now, v=v)
    return v


def status():
    """排障用：两个通道各通不通。"""
    w = wifi()
    we = weather()
    return {
        "wifi": {"ok": w.get("ok"), "ssid": w.get("ssid")},
        "weather": {"ok": we.get("ok"), "city": we.get("city"),
                    "temp": we.get("temp")},
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps({"wifi": wifi(), "weather": weather()},
                     ensure_ascii=False, indent=2))
