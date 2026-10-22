# -*- coding: utf-8 -*-
"""
LibreHardwareMonitor 采集通道。

LHM 本体在 ring0 里读传感器，我们不去碰它的 GUI，而是让它旁边的
`tools/lhm_bridge.exe` 常驻跑一份 Computer，通过 HTTP 把快照吐出来：

    GET http://127.0.0.1:8771/sensors  ->  {"ok":true,"sensors":[{...}, ...]}

本模块只负责轮询 + 把 LHM 的 sensor id 映射到面板要的字段。
LHM 的 id 是 `/<hw>/<index>/<kind>/<n>` 形式，比按名字模糊匹配靠谱得多，
所以这里**按 id 精确取**，只把 `name` 当作可读标签。

注意：CPU 温度 / 风扇 / 主板温度 依赖 PawnIO 内核驱动。
没装驱动时 `Core (Tctl/Tdie)` 恒为 0、`Package` 功率恒为 0、主板下无传感器，
这些都是**硬件/驱动没给**，不是 LHM 的 bug —— 这里统一把 0 温度当缺失处理。
"""
import json
import time
import logging
import urllib.request

log = logging.getLogger(__name__)

BRIDGE = "http://127.0.0.1:8771"
TIMEOUT = 2.5

# 缓存：桥接进程每秒推一次，我们没必要每帧都去问
_cache = {"t": 0.0, "raw": None}
_CACHE_TTL = 0.7

# LHM sensor id -> 面板字段
# 用精确 id 而不是名字，因为名字会随 LHM 版本/语言变，id 不会。
MAP = {
    # ---- CPU
    "/amdcpu/0/load/0":        "cpu_usage",
    "/amdcpu/0/temperature/2": "cpu_temp",      # Core (Tctl/Tdie)，无 PawnIO 时为 0
    "/amdcpu/0/temperature/4": "cpu_ccd_temp",  # CCD1 (Tdie)
    "/amdcpu/0/temperature/3": "soc_temp",      # SoC
    "/amdcpu/0/power/0":       "cpu_power",     # Package，无 PawnIO 时为 0
    "/amdcpu/0/voltage/0":     "cpu_volt",      # Core (SVI2 TFN)，无 PawnIO 时恒 1.55（不可信）
    "/amdcpu/0/clock/1":       "cpu_clock",     # Cores (Average)，无 PawnIO 时为 0

    # ---- GPU（这部分不需要驱动，nvidia 走 NVAPI，AIDA64 反而给不全）
    "/gpu-nvidia/0/temperature/0": "gpu_temp",
    "/gpu-nvidia/0/temperature/2": "gpu_hotspot",
    "/gpu-nvidia/0/load/0":        "gpu_usage",
    "/gpu-nvidia/0/load/3":        "gpu_mem_load",
    "/gpu-nvidia/0/power/0":       "gpu_power",
    "/gpu-nvidia/0/clock/0":       "gpu_clock",
    "/gpu-nvidia/0/clock/4":       "gpu_mem_clock",
    "/gpu-nvidia/0/voltage/0":     "gpu_volt",
    "/gpu-nvidia/0/smalldata/2":   "gpu_vram_total",
    "/gpu-nvidia/0/smalldata/1":   "gpu_vram_used",
    "/gpu-nvidia/0/smalldata/0":   "gpu_vram_free",

    # ---- 内存
    "/ram/load/0":  "mem_usage",
    "/ram/data/0":  "mem_used_gb",
    "/ram/data/1":  "mem_avail_gb",
}

# 无 PawnIO 驱动时这些值不可信（恒 0 或恒默认值），直接丢弃
_NEEDS_DRIVER = {"cpu_temp", "cpu_power", "cpu_volt", "cpu_clock"}

# 这些字段的 0 一定是「没读到」而不是「真的是 0」
_NONZERO = {"cpu_temp", "cpu_power", "cpu_clock", "mem_usage", "gpu_temp"}


# ---------------------------------------------------------------- 桥接状态
def pawnio_installed():
    """PawnIO 内核驱动装没装（LHM 0.9.6+ 的 ring0 后端）"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\PawnIO"):
            return True
    except OSError:
        return False


def bridge_alive():
    try:
        with urllib.request.urlopen(BRIDGE + "/status", timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception:
        return False


# ---------------------------------------------------------------- 取数
def _fetch():
    """拿一份 LHM 全量传感器列表，失败返回 None"""
    try:
        with urllib.request.urlopen(BRIDGE + "/sensors", timeout=TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            log.debug("LHM 桥接报错: %s", d.get("error"))
            return None
        return d.get("sensors") or []
    except Exception as e:
        log.debug("LHM 桥接连不上: %s", e)
        return None


def read():
    """
    返回 {ok, values, raw, fans, active}
    结构跟 aida64.read() 对齐，方便 hardware.py 统一处理。
    """
    now = time.time()
    if now - _cache["t"] < _CACHE_TTL and _cache["raw"] is not None:
        rows = _cache["raw"]
    else:
        rows = _fetch()
        if rows is None:
            return {"ok": False, "values": {}, "raw": {}, "fans": [],
                    "active": [], "pawnio": pawnio_installed()}
        _cache.update(t=now, raw=rows)

    has_driver = pawnio_installed()
    values = {}
    raw = {}
    fans = []

    for s in rows:
        sid = s.get("id") or ""
        stype = s.get("type") or ""
        name = s.get("name") or ""
        val = s.get("value")
        raw[f"{s.get('hw')} | {name}"] = val

        if stype == "Fan" and isinstance(val, (int, float)) and val > 0:
            if len(fans) < 6:
                fans.append({"label": name, "value": round(val), "unit": "RPM"})
            continue

        field = MAP.get(sid)
        if not field or val is None:
            continue
        if not isinstance(val, (int, float)):
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        if field in _NONZERO and val == 0:
            continue                                  # 0 = 没读到
        if field in _NEEDS_DRIVER and not has_driver:
            continue                                  # 没驱动时这些是假的
        values[field] = float(val)

    active = ["lhm"] if values else []
    return {
        "ok": bool(values),
        "values": values,
        "raw": raw,
        "fans": fans,
        "active": active,
        "pawnio": has_driver,
    }


def status():
    """给 /api/aida64/status 之类的地方用，说明这条通道通不通"""
    alive = bridge_alive()
    rows = _fetch() if alive else None
    has_driver = pawnio_installed()
    n = len(rows) if rows else 0
    hint = ""
    if not alive:
        hint = "LHM 桥接没在跑，试试启动 tools/LHM/lhm_bridge.exe"
    elif not has_driver:
        hint = ("LHM 已连上（%d 个传感器），但 PawnIO 驱动没装，"
                "CPU 温度/功耗/风扇/主板温度读不到。装 PawnIO 后重启桥接即可。" % n)
    return {
        "ok": alive,
        "bridge": alive,
        "sensors": n,
        "pawnio": has_driver,
        "hint": hint,
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    r = read()
    print("ok =", r["ok"], " pawnio =", r["pawnio"])
    for k, v in sorted(r["values"].items()):
        print(f"  {k:16s} {v}")
    if r["fans"]:
        print("  fans:", r["fans"])
