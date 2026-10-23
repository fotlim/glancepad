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
import re
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
    # ---- v1.1 新增 ----
    "screen": 0,            # 0=自动（挑非主屏），1..n=显示器序号（1 起，按 EnumDisplayMonitors 顺序）
    "font": "system",       # system / ttf；ttf 时用 font_file
    "font_file": "",        # 自定义字体文件名（放在 fonts/ 目录下）
    "bg_mode": "gradient",  # gradient / image
    "bg_image": "",         # 自定义背景图文件名（放在 backgrounds/ 目录下）
    "bg_effect": "blur",    # blur / dim / none
    "bg_blur": 18,          # 模糊半径 px
    "bg_opacity": 100,      # 背景图不透明度 %
    # ---- v1.1.1 新增 ----
    # 一言接口。默认走 hitokoto.cn；{cats} 会替换成 quote_cats（留空则不替换）。
    # 也可以填任意返回 JSON 的地址，字段名支持 hitokoto/text/content 三种。
    "quote_api": "https://v1.hitokoto.cn/?c={cats}",
    "quote_cats": "d,i,k",  # hitokoto 分类：d 文学 i 诗词 k 哲学
    "quote_refresh": 30,    # 一言刷新间隔（分钟），0=每天只换一次
    # ---- v1.1.1 新增：天气源 ----
    # auto = 优先中国天气网（中国气象局站点），失败自动回退 open-meteo
    # cn   = 只用中国天气网；open-meteo = 只用 open-meteo
    "weather_src": "auto",
    # ---- v1.1.2 新增：背景图自动对比度 ----
    # auto = 按背景图亮度自动加遮罩/加深玻璃/提亮次要文字；off = 固定用默认值
    "bg_contrast": "auto",
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
        "bssid": pick("BSSID", "AP BSSID"),
        "state": pick("State", "状态"),
        "radio": pick("Radio type", "无线电类型"),
        "auth": pick("Authentication", "身份验证"),
        "band": (pick("Band", "波段", "频带") or "").strip(),
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


def _cim_wlan():
    """netsh 读不到时的回退：走 Get-NetConnectionProfile，
    不需要「定位服务」，但只能拿到 SSID / 速率 / 网卡描述，没有信号强度。

    会命中一种很常见的坑：Windows 的「定位服务」总开关被关掉时，
    `netsh wlan show interfaces` 会报 "need location permission"，
    拿不到 SSID（连 SSID 都被判为位置信息）。
    """
    cmd = (
        "$p = Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -like '*WLAN*' -or "
        "$_.InterfaceAlias -like '*Wi-Fi*' } | Select-Object -First 1; "
        "if (-not $p) { '{}' ; exit }; "
        "$a = Get-NetAdapter -InterfaceIndex $p.InterfaceIndex -ErrorAction SilentlyContinue; "
        "[pscustomobject]@{ adapter=$p.InterfaceAlias; desc=$a.InterfaceDescription; "
        "ssid=$p.Name; state=($a.Status -as [string]); "
        "speed=($a.LinkSpeed -as [string]) } | ConvertTo-Json -Compress"
    )
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, timeout=8, creationflags=_CREATE_NO_WINDOW)
    except Exception as e:
        log.debug("Get-NetConnectionProfile 回退失败: %s", e)
        return None

    raw = p.stdout or b""
    text = ""
    for enc in ("utf-8", "mbcs", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    text = text.strip()
    if not text or not text.startswith("{"):
        return None
    try:
        d = json.loads(text)
    except Exception:
        return None

    ssid = (d.get("ssid") or "").strip()
    if not ssid:
        return None

    spd = None
    m = re.search(r"([\d.]+)", str(d.get("speed") or ""))
    if m:
        try:
            spd = float(m.group(1))
        except ValueError:
            spd = None

    return {
        "name": d.get("adapter"),
        "desc": d.get("desc"),
        "ssid": ssid,
        "bssid": None,
        "state": d.get("state"),
        "radio": None,
        "auth": None,
        "band": None,
        "channel": None,
        "rx_rate": spd,
        "tx_rate": spd,
        "signal": None,
        "signal_raw": None,
        "fallback": "cim",
    }


def wifi():
    """当前 WiFi 状态；没连 WiFi 返回 {ok: False}。"""
    now = time.time()
    if now - _wifi_cache["t"] < _WIFI_TTL and _wifi_cache["v"] is not None:
        return _wifi_cache["v"]

    w = _netsh_wlan()
    if not w:
        w = _cim_wlan()

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
            "fallback": w.get("fallback"),
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


# ---------------------------------------------------------------- 中国天气网（主源）
# 中国气象局官方数据，国内站点精度明显好于 open-meteo 的网格插值，
# 而且支持用中文城市名搜站点码、带 AQI 与气象预警。
_UA_CN = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "http://www.weather.com.cn/",
}

# 中文天气文字 -> emoji（中国天气网给的是「多云转阴」这种，只能靠关键词判断）
_CN_ICONS = [
    ("雷", "⛈️"), ("冰雹", "⛈️"),
    ("暴雪", "❄️"), ("大雪", "❄️"), ("中雪", "🌨️"), ("小雪", "🌨️"), ("雪", "🌨️"),
    ("暴雨", "🌧️"), ("大雨", "🌧️"), ("中雨", "🌧️"), ("小雨", "🌦️"), ("雨", "🌦️"),
    ("雾", "🌫️"), ("霾", "🌫️"), ("沙", "🌪️"), ("尘", "🌪️"),
    ("晴", "☀️"), ("云", "⛅"), ("阴", "☁️"),
]

_cn_code_cache = {"t": 0.0, "city": "", "code": ""}
_cn_CODE_TTL = 7 * 24 * 3600.0     # 城市码基本不变，一周内不用重查


def _cn_icon(text, code=""):
    """按中文天气文字猜 emoji；文字判断不出来再看 d01..dXX 代码。"""
    t = text or ""
    for kw, ic in _CN_ICONS:
        if kw in t:
            # 「晴转阴」这种以第一个字为准，避免整句被后面的词抢走
            if len(t) > 1 and t[0] + "转" == t[:2]:
                for kw2, ic2 in _CN_ICONS:
                    if kw2 in t[0]:
                        return ic2
            return ic
    if code:
        try:
            n = int(re.sub(r"\D", "", code) or "0")
        except Exception:
            n = 0
        return _WMO.get(n, ("", "🌡️"))[1] or "🌡️"
    return "🌡️"


def _cn_request(url, timeout=8):
    req = urllib.request.Request(url, headers=_UA_CN)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _cn_city_code(city, force=False):
    """中文城市名 -> 中国天气网站点码（如 长春 -> 101060101）。"""
    city = (city or "").strip()
    if not city:
        return ""
    now = time.time()
    if (not force and _cn_code_cache["city"] == city
            and now - _cn_code_cache["t"] < _cn_CODE_TTL and _cn_code_cache["code"]):
        return _cn_code_cache["code"]

    # 中国天气网对「长春市」这种带后缀的查法会优先返回景点（码带字母后缀），
    # 拿不到市级码，所以原名查不到就再去掉「市/县/区」重试一次。
    # 另外用户可能填「吉林省长春市」这种带省前缀的写法，直接查是空结果，
    # 要按 省/自治区/特别行政区 切开只留最后一段（长春市）再查。
    tries = [city]
    tail = city
    mprov = re.split(r"(?:省|自治区|特别行政区)", city)
    if len(mprov) > 1:
        tail = mprov[-1].strip()
        if tail and tail not in tries:
            tries.append(tail)
    bare = re.sub(r"(市|县|区|自治州|地区|盟)$", "", tail)
    if bare and bare not in tries:
        tries.append(bare)

    for name in tries:
        url = "http://toy1.weather.com.cn/search?cityname=" + urllib.parse.quote(name)
        try:
            txt = _cn_request(url).strip()
            # 返回形如 ({...},{...}) 或直接 [...]
            txt = txt.lstrip("(").rstrip(")").strip()
            if not txt or txt in ("()", "[]"):
                continue
            arr = json.loads(txt)
            if isinstance(arr, dict):
                arr = [arr]
            code = ""
            for it in arr or []:
                ref = str(it.get("ref") or "")
                c = ref.split("~")[0]
                # 只认纯数字的市级码（景点码带字母后缀，如 10106010101A）
                if c.isdigit() and len(c) == 9:
                    code = c
                    break
            if code:
                _cn_code_cache.update(t=now, city=city, code=code)
                return code
        except Exception as e:
            log.debug("中国天气网 城市码查询失败(%s): %s", name, e)
    return ""


def _cn_weather(city):
    """中国天气网当前天气 + 七天预报。取不到抛异常，由 weather() 兜底。"""
    code = _cn_city_code(city)
    if not code:
        raise RuntimeError("查不到该城市的中国天气网站点码")

    # --- 现在 ---
    sk_txt = _cn_request("http://d1.weather.com.cn/sk_2d/%s.html" % code)
    m = re.search(r"=\s*(\{.*\})", sk_txt, re.S)
    if not m:
        raise RuntimeError("实时天气格式变了")
    sk = json.loads(m.group(1))

    # --- 七天 ---
    page = _cn_request("http://www.weather.com.cn/weather/%s.shtml" % code)
    days = []
    i = page.find('class="t clearfix"')
    if i > 0:
        j = page.find("</ul>", i)
        blk = page[i:j if j > 0 else i + 6000]
        for it in re.findall(r'<li class="sky[^"]*">(.*?)</li>', blk, re.S):
            m1 = re.search(r"<h1>(.*?)</h1>", it, re.S)
            mw = re.search(r'<p[^>]*class="wea"[^>]*>(.*?)</p>', it, re.S)
            mt = re.search(r'class="tem">(.*?)</p>', it, re.S)
            hi = re.search(r"<span>(-?\d+)</span>", mt.group(1)) if mt else None
            lo = re.search(r"<i>(-?\d+)", mt.group(1)) if mt else None
            h1 = (m1.group(1).strip() if m1 else "")
            wea = (mw.group(1).strip() if mw else "")
            # "23日（今天）" -> date=10/23, label=今天
            md = re.match(r"(\d{1,2})日[（(](.*?)[）)]", h1)
            if md:
                day = int(md.group(1))
                label = md.group(2)
                date = "%d/%d" % (time.localtime().tm_mon, day)
            else:
                label, date = h1, h1
            days.append({
                "label": label,
                "date": date,
                "max": int(hi.group(1)) if hi else None,
                "min": int(lo.group(1)) if lo else None,
                "icon": _cn_icon(wea),
                "text": wea or "未知",
                "pop": None,
            })

    # --- 预警（有就带一条，没有就空）---
    alarm = ""
    try:
        dz = _cn_request("http://d1.weather.com.cn/dingzhi/%s.html" % code)
        # 注意：这个文件末尾不一定有分号（实测长春返回以 "}]}" 结尾），
        # 所以分号必须可选，否则整条预警解析不出来。
        mz = re.search(r"alarmDZ\d*\s*=\s*(\{.*\})", dz, re.S)
        if mz:
            aw = (json.loads(mz.group(1)).get("w") or [])
            if aw:
                alarm = str(aw[0].get("w13") or aw[0].get("w5") or "").strip()
    except Exception:
        pass

    temp = None
    try:
        temp = round(float(sk.get("temp")), 1)
    except Exception:
        pass

    txt = str(sk.get("weather") or "").strip()
    hum = None
    try:
        hum = int(re.sub(r"%", "", str(sk.get("SD") or sk.get("sd") or "")) or 0) or None
    except Exception:
        pass
    wind = None
    try:
        # WS 是「3级」，wse 是「14km/h」，换算成 m/s
        wse = str(sk.get("wse") or "")
        mm = re.search(r"([\d.]+)\s*km", wse)
        if mm:
            wind = round(float(mm.group(1)) / 3.6, 1)
    except Exception:
        pass

    if not days:
        raise RuntimeError("七天预报解析为空")

    return {
        "ok": True,
        "source": "weather.com.cn",
        "city": str(sk.get("cityname") or city),
        "temp": temp,
        "feels": None,
        "text": txt or days[0]["text"],
        "icon": _cn_icon(txt, sk.get("weathercode") or ""),
        "code": None,
        "humidity": hum,
        "wind": wind,
        "wind_dir": str(sk.get("WD") or ""),
        "is_day": None,
        "high": days[0]["max"],
        "low": days[0]["min"],
        "hourly": [],
        "days": days,
        "aqi": sk.get("aqi"),
        "alarm": alarm,
        "updated": int(time.time()),
    }


def _geo_lookup(city):
    """城市名 -> (lat, lon)。给 open-meteo 兜底用，避免经纬度和城市名对不上。"""
    city = (city or "").strip()
    if not city:
        return None
    for name in (city, city.rstrip("市"), city.rstrip("市") + "市"):
        if not name:
            continue
        u = ("https://geocoding-api.open-meteo.com/v1/search?name=%s"
             "&count=1&language=zh&format=json" % urllib.parse.quote(name))
        try:
            d = _http_json(u)
            res = d.get("results") or []
            if res:
                return (res[0].get("latitude"), res[0].get("longitude"))
        except Exception:
            continue
    return None


def weather():
    """当前天气 + 未来几小时预报 + 七天预报。

    主源：中国天气网（weather.com.cn，中国气象局数据，国内更准）
    兜底：open-meteo.com（免费无 key，经纬度会按城市名自动校正）
    两个都失败就返回上一份缓存。
    """
    st = settings()
    if not st.get("weather", True):
        return {"ok": False, "hint": "天气已关闭"}

    now = time.time()
    # 兜底结果（open-meteo）用短 TTL，主源结果用正常 TTL：
    # 否则开机第一帧主源偶发失败时，会把 open-meteo 的读数缓存 10 分钟，
    # 页面长时间没有 AQI / 预警，看起来像「天气不准」。
    _ttl = _weather_cache.get("ttl") or _WEATHER_TTL
    if now - _weather_cache["t"] < _ttl and _weather_cache["v"]:
        v = dict(_weather_cache["v"])
        v["cached"] = True
        return v

    city = (st.get("city") or "").strip()
    src = (st.get("weather_src") or "auto").lower()

    # ---- 主源：中国天气网 ----
    if city and src in ("auto", "cn", "weather.com.cn"):
        try:
            v = _cn_weather(city)
            _weather_cache.update(t=now, v=v, ttl=_WEATHER_TTL)
            return v
        except Exception as e:
            log.debug("中国天气网失败，转 open-meteo: %s", e)

    lat = st.get("latitude", 30.2741)
    lon = st.get("longitude", 120.1551)
    # 城市名和经纬度对不上时（比如城市改成长春但坐标还是杭州的默认值），
    # 用城市名反查一次坐标，别拿错地方的天气糊弄人。
    if city:
        g = _geo_lookup(city)
        if g and g[0] is not None and g[1] is not None:
            try:
                if abs(float(g[0]) - float(lat)) > 1.5 or abs(float(g[1]) - float(lon)) > 1.5:
                    log.info("坐标与城市不符，按「%s」校正为 %s,%s", city, g[0], g[1])
                    lat, lon = g[0], g[1]
            except Exception:
                lat, lon = g[0], g[1]
    q = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                 "precipitation_probability_max,wind_speed_10m_max,"
                 "sunrise,sunset",
        "timezone": "Asia/Shanghai",
        "forecast_days": 7,
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

        # 七天预报（第 0 天是今天，前端自己决定要不要跳过）
        dts = daily.get("time") or []
        dmax = daily.get("temperature_2m_max") or []
        dmin = daily.get("temperature_2m_min") or []
        dcode = daily.get("weather_code") or []
        dpop = daily.get("precipitation_probability_max") or []
        dweek = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
        days = []
        for i, dt in enumerate(dts):
            c = int(dcode[i] or 0) if i < len(dcode) else 0
            try:
                wd = time.strptime(dt, "%Y-%m-%d")
                label = "今天" if i == 0 else dweek[(wd.tm_wday + 1) % 7]
                md = "%d/%d" % (wd.tm_mon, wd.tm_mday)
            except Exception:
                label, md = dt, dt
            days.append({
                "label": label,
                "date": md,
                "max": round(float(dmax[i])) if i < len(dmax) and dmax[i] is not None else None,
                "min": round(float(dmin[i])) if i < len(dmin) and dmin[i] is not None else None,
                "icon": _WMO.get(c, ("", "🌡️"))[1],
                "text": _WMO.get(c, ("未知", ""))[0],
                "pop": dpop[i] if i < len(dpop) else None,
            })

        v = {
            "ok": True,
            "source": "open-meteo",
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
            "days": days,
            "updated": int(now),
        }
    except Exception as e:
        log.debug("天气解析失败: %s", e)
        if _weather_cache["v"]:
            v = dict(_weather_cache["v"])
            v["stale"] = True
            return v
        return {"ok": False, "hint": "天气数据异常"}

    # 这是兜底源的结果，只缓存 60 秒，好让下一次请求还能回到中国天气网主源
    _weather_cache.update(t=now, v=v, ttl=60.0)
    return v


# ---------------------------------------------------------------- 一言 / 历史上的今天
_quote_cache = {"t": 0.0, "day": "", "v": None, "url": ""}
_QUOTE_TTL = 1800.0          # 一言 30 分钟换一次（同一句不会一秒一变）

_hist_cache = {"t": 0.0, "day": "", "v": None}
_HIST_TTL = 6 * 3600.0       # 历史上的今天每 6 小时刷一次，一天内基本不变

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s):
    """百度百科的 title 里带 <a> 链接，去掉标签只留文字。"""
    return _TAG_RE.sub("", s or "").replace("&nbsp;", " ").strip()


def _quote_url(cfg):
    """按 settings 拼一言地址。

    hitokoto 的分类要写成重复参数 `?c=d&c=i&c=k`，不接受 `?c=d,i,k`（400），
    所以这里把用户填的「逗号分隔」展开成重复参数。
    """
    url = (cfg.get("quote_api") or "").strip() or _defaults["quote_api"]
    cats = (cfg.get("quote_cats") or "").strip()
    if "{cats}" in url:
        if cats:
            # 兼容中文逗号、空格分隔
            parts = [p for p in re.split(r"[,，\s]+", cats) if p]
            join = "&c=" if "c={cats}" in url else ","
            url = url.replace("{cats}", join.join(parts))
        else:
            # 用户没配分类：把 ?c={cats} / &c={cats} 整个参数去掉，别留个空 c=
            url = re.sub(r"[?&]c=\{cats\}", "", url)
            url = url.replace("{cats}", "")
    return url


def quote(force=False):
    """一言。默认 hitokoto.cn，可在设置里改地址与刷新间隔。

    刷新间隔来自 settings.quote_refresh（分钟），0 表示一天只换一次。
    换了地址/分类会立刻重新取，不用等缓存过期。
    """
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    cfg = settings()
    url = _quote_url(cfg)
    try:
        ttl = max(0, int(cfg.get("quote_refresh", 30))) * 60.0
    except Exception:
        ttl = _QUOTE_TTL

    hit = (not force and _quote_cache["v"] and _quote_cache["day"] == today
           and (ttl <= 0 or now - _quote_cache["t"] < ttl))
    if hit and _quote_cache.get("url") == url:
        return _quote_cache["v"]

    v = None
    try:
        d = _http_json(url, timeout=5)
        # 不同源用的字段名不一样，挨个试
        text = ""
        for k in ("hitokoto", "text", "content", "sentence"):
            text = (d.get(k) or "").strip() if isinstance(d, dict) else ""
            if text:
                break
        if text:
            v = {
                "ok": True,
                "text": text,
                "from": ((d.get("from") or d.get("source") or "") or "").strip(),
                "who": ((d.get("from_who") or d.get("author") or "") or "").strip(),
            }
    except Exception as e:
        log.debug("一言请求失败: %s", e)

    if v is None:
        if _quote_cache["v"]:
            v = dict(_quote_cache["v"])
            v["stale"] = True
        else:
            v = {"ok": False, "text": "心之所向，素履以往。", "from": "", "who": ""}

    _quote_cache.update(t=now, day=today, v=v, url=url)
    return v


def history(force=False):
    """历史上的今天（百度百科 cms 接口）。返回今天最多 8 条大事。"""
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    if (not force and _hist_cache["v"] and _hist_cache["day"] == today
            and now - _hist_cache["t"] < _HIST_TTL):
        return _hist_cache["v"]

    md = time.strftime("%m%d")
    v = None
    try:
        d = _http_json("https://baike.baidu.com/cms/home/eventsOnHistory/%s.json"
                       % time.strftime("%m"), timeout=8)
        month = d.get(time.strftime("%m")) or {}
        evs = month.get(md) or []
        items = []
        for e in evs:
            if not isinstance(e, dict):
                continue
            title = _strip_tags(e.get("title"))
            if not title:
                continue
            items.append({"year": str(e.get("year") or ""), "title": title})
        if items:
            # 优先挑近代的（年份 >= 1500），看起来更有意思
            items.sort(key=lambda x: (-int(x["year"])
                                      if x["year"].isdigit() else 0))
            v = {"ok": True, "date": "%d月%d日" % (int(md[:2]), int(md[2:])),
                 "items": items[:8], "total": len(items)}
    except Exception as e:
        log.debug("历史上的今天请求失败: %s", e)

    if v is None:
        if _hist_cache["v"]:
            v = dict(_hist_cache["v"])
            v["stale"] = True
        else:
            v = {"ok": False, "date": "", "items": [], "hint": "取不到历史数据"}

    _hist_cache.update(t=now, day=today, v=v)
    return v


def status():
    """排障用：各通道通不通。"""
    w = wifi()
    we = weather()
    q = quote()
    h = history()
    return {
        "wifi": {"ok": w.get("ok"), "ssid": w.get("ssid")},
        "weather": {"ok": we.get("ok"), "city": we.get("city"),
                    "temp": we.get("temp"), "days": len(we.get("days") or [])},
        "quote": {"ok": q.get("ok"), "text": (q.get("text") or "")[:24]},
        "history": {"ok": h.get("ok"), "count": len(h.get("items") or [])},
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps({"wifi": wifi(), "weather": weather(),
                      "quote": quote(), "history": history()},
                     ensure_ascii=False, indent=2))
