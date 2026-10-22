# -*- coding: utf-8 -*-
"""
AIDA64 传感器读取器 —— 双通道

通道 A：共享内存（推荐，一次都不用重启 AIDA64）
    AIDA64 -> 文件 -> 设置 -> 外部应用程序 -> 勾选「允许将传感器值写入注册表」
    等等，那是注册表。共享内存是同一页里的另一个勾：
    「允许将传感器值写入共享内存」(Enable shared memory)
    勾上后 AIDA64 会创建名为 AIDA64_SensorValues 的共享内存，
    里面是一行行 XML：<temp><id>TCPU</id><label>CPU</label><value>45</value></temp>

通道 B：注册表
    HKCU\\Software\\FinalWire\\AIDA64\\SensorValues 下会出现一堆值。

两条通道都读，谁的字段全用谁的；同名字段以共享内存（更新更实时）为准。
AIDA64 的值名（id）在不同版本/硬件上会变，所以这里不写死映射表，
而是「全量采集 + 模糊匹配」，匹配不到的原始值原样保留，方便核对。
"""
import ctypes
import ctypes.wintypes as wt
import re
import xml.etree.ElementTree as ET
import logging

log = logging.getLogger(__name__)

KEY_PATH = r"Software\FinalWire\AIDA64\SensorValues"

# ---------------------------------------------------------------- 共享内存
SHM_NAMES = ("AIDA64_SensorValues", "Global\\AIDA64_SensorValues")
SHM_SIZE = 64 * 1024

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wt.HANDLE]

FILE_MAP_READ = 0x0004

# AIDA64 共享内存里的分类标签 -> 单位
_KIND_UNIT = {
    "temp": "°C", "fan": "RPM", "volt": "V", "curr": "A", "pwr": "W",
    "clk": "MHz", "flow": "L/h", "sys": "", "batt": "%", "fanrpm": "RPM",
}


def _read_shm():
    """
    读共享内存，返回 [{id, label, value, kind, unit}] 或 None（没开）
    """
    for name in SHM_NAMES:
        h = _k32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not h:
            continue
        try:
            p = _k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
            if not p:
                continue
            try:
                raw = ctypes.string_at(p, SHM_SIZE)
            finally:
                _k32.UnmapViewOfFile(p)
            # 截到第一个 \0
            raw = raw.split(b"\x00", 1)[0]
            if not raw:
                continue
            try:
                text = raw.decode("utf-8", "ignore")
            except Exception:
                continue
            return _parse_shm_xml(text)
        finally:
            _k32.CloseHandle(h)
    return None


def _parse_shm_xml(text):
    """AIDA64 的共享内存是一行行独立的 XML 元素，不是一个根节点"""
    out = []
    # 用正则粗切出每个 <kind>...</kind> 块，比拼 <root> 更稳
    for m in re.finditer(r"<(\w+)>(.*?)</\1>", text, re.S):
        kind, body = m.group(1).lower(), m.group(2)
        try:
            el = ET.fromstring(f"<{m.group(1)}>{body}</{m.group(1)}>")
        except ET.ParseError:
            continue
        d = {c.tag.lower(): (c.text or "").strip() for c in el}
        sid = d.get("id") or ""
        label = d.get("label") or sid
        val = d.get("value") or d.get("value1") or ""
        if not sid and not label:
            continue
        out.append({
            "id": sid,
            "label": label,
            "value": val,
            "kind": kind,
            "unit": _KIND_UNIT.get(kind, ""),
        })
    return out


# ---------------------------------------------------------------- 注册表
def _read_raw():
    """把 SensorValues 下所有值读成 {name: value}；没这个键就返回 None"""
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH) as k:
            i = 0
            while True:
                try:
                    name, val, typ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = val
                i += 1
    except FileNotFoundError:
        return None
    except Exception as e:
        log.debug("读取 AIDA64 注册表失败: %s", e)
        return None
    return out


def _num(v):
    """从 '1234', '12.5 %', '5.2 GHz' 之类里抠出数字"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


# 字段 -> (必须包含的关键字, 排除词)
RULES = [
    ("cpu_usage",      ["cpuutil", "cpuusage", "cpu total", "cpu load"],  []),
    ("cpu_temp",       ["cputemp", "cpudiode", "cpu package", "cpu 温度"], ["core", "hotspot"]),
    ("cpu_pkg_temp",   ["cpupkg", "cpu package"],                        []),
    ("cpu_clock",      ["cpuclk", "cpu clock", "cpufreq", "cpu MHz"],    []),
    ("cpu_volt",       ["cpuvid", "cpuvolt", "vcore", "cpu core volt"],  []),
    ("cpu_power",      ["cpupwr", "cpupower", "cpu package pwr"],        []),
    ("gpu_usage",      ["gpuutil", "gpu1util", "gpuusage", "gpu load"],  []),
    ("gpu_temp",       ["gputemp", "gpu1temp", "gpu 温度"],              ["hotspot", "mem", "memory", "vram"]),
    ("gpu_hotspot",    ["gpuhotspot", "gpu1hotspot", "hotspot"],         []),
    ("gpu_mem_temp",   ["gpumemtemp", "gpu1memtemp", "gpu memory temp", "vram temp"], []),
    ("gpu_clock",      ["gpuclk", "gpu1clk", "gpu clock", "gpu core"],   ["mem"]),
    ("gpu_volt",       ["gpuvid", "gpuvolt", "gpu1vddc"],                []),
    ("gpu_power",      ["gpupwr", "gpupower", "gpu1pwr"],                []),
    ("mem_usage",      ["memutil", "memusage", "memory usage"],          []),
    ("mem_temp",       ["memtemp", "dimm", "memory temp"],               []),
    ("mb_temp",        ["mbtemp", "motherboard", "mainboard", "主板"],   []),
]


def _match(pairs):
    """pairs: [(name, value)] -> {field: float}"""
    got = {}
    low = [(n.lower(), v) for n, v in pairs]
    for field, keys, excludes in RULES:
        if field in got:
            continue
        for lname, val in low:
            if any(x in lname for x in excludes):
                continue
            if any(k in lname for k in keys):
                n = _num(val)
                if n is not None:
                    got[field] = n
                    break
    return got


# ---------------------------------------------------------------- 对外
def read():
    """
    返回:
      {ok, values, raw, fans, shm, reg, active}
    ok=False 表示两条通道都没数据。
    """
    shm = _read_shm()
    reg = _read_raw()

    if not shm and not reg:
        return {"ok": False, "values": {}, "raw": {}, "fans": [],
                "shm": [], "reg": {}, "active": []}

    pairs = []
    if reg:
        pairs += [(n, v) for n, v in reg.items()]      # 先放注册表
    if shm:
        pairs += [(s["label"], s["value"]) for s in shm]  # 共享内存覆盖
    values = _match(pairs)

    # 风扇：带 fan 且 >0
    fans = []
    if shm:
        for s in shm:
            if s["kind"] in ("fan", "fanrpm"):
                n = _num(s["value"])
                if n and n > 0:
                    fans.append({"label": s["label"] or s["id"], "rpm": int(n)})
    for name, val in (reg or {}).items():
        if "fan" in name.lower():
            n = _num(val)
            if n and n > 0:
                fans.append({"label": name, "rpm": int(n)})
    fans.sort(key=lambda x: -x["rpm"])

    active = (["shared-memory"] if shm else []) + (["registry"] if reg else [])

    return {"ok": True, "values": values, "raw": reg or {},
            "fans": fans[:6], "shm": shm or [], "reg": reg or {},
            "active": active}


def status():
    """给 /api/aida64/status 用的诊断信息"""
    r = read()
    return {
        "ok": r["ok"],
        "active": r["active"],
        "shm_items": len(r["shm"]),
        "reg_values": len(r["reg"]),
        "mapped": r["values"],
        "fans": r["fans"],
        "hint": None if r["ok"] else (
            "需要打开 AIDA64 -> 文件 -> 设置 -> 外部应用程序，"
            "勾选「允许将传感器值写入共享内存」（或「写入注册表」）后点确定"
        ),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    r = read()
    if not r["ok"]:
        print("两条通道都没读到 AIDA64 传感器。请确认：")
        print("  1) AIDA64 正在运行")
        print("  2) AIDA64 -> 文件 -> 设置 -> 外部应用程序")
        print("     勾选「允许将传感器值写入共享内存」或「写入注册表」")
    else:
        print("通道:", r["active"])
        print("映射结果:", r["values"])
        print("风扇:", r["fans"])
        print("共享内存条目:", len(r["shm"]))
        print("注册表值个数:", len(r["reg"]))
        for s in r["shm"][:20]:
            print("   ", s["kind"], s["id"], s["label"], "=", s["value"], s["unit"])
