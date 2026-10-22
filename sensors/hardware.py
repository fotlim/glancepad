# -*- coding: utf-8 -*-
"""
硬件数据采集。

优先级：AIDA64（共享内存 / 注册表）> LibreHardwareMonitor 桥接 > psutil / nvidia-smi。
三层都拿不到的字段才留空，所以 AIDA64 没开、LHM 没装驱动时也能跑，
只是热力图/风扇那块会显示 `--`。
"""
import os
import re
import csv
import io
import time
import ctypes
import ctypes.wintypes as wintypes
import shutil
import logging
import subprocess

import psutil

from . import aida64
from . import lhm
from . import network

log = logging.getLogger(__name__)

_NVIDIA = shutil.which("nvidia-smi")

# psutil 第一次调用 cpu_percent 会返回 0，先预热一次
psutil.cpu_percent(interval=None)
_last_net = None
_last_net_t = None


# ---------------------------------------------------------------- CPU 细分
# 自己算差分：psutil.cpu_times_percent 首次调用恒为 0，而且和 cpu_percent
# 共用内部基准，两个交替采样会互相干扰。这里维护独立快照，稳定可控。
_cpu_prev = {"t": 0.0, "total": None, "procs": None}


def _cpu_snapshot():
    t = psutil.cpu_times()
    s = float(t.user + t.system + t.idle + getattr(t, "iowait", 0.0)
              + getattr(t, "nice", 0.0) + getattr(t, "irq", 0.0)
              + getattr(t, "softirq", 0.0) + getattr(t, "steal", 0.0))
    return {"user": t.user, "system": t.system, "idle": t.idle,
            "iowait": getattr(t, "iowait", 0.0), "total": s}


def _cpu_time_delta():
    """返回 user/system/idle/iowait 各占时间比例（0..1）。
    间隔太短（<0.25s）时差分噪声大，沿用上次结果，避免第一帧全 0。"""
    now = _cpu_snapshot()
    prev = _cpu_prev["total"]
    _cpu_prev["total"] = now
    if not prev:
        return _cpu_prev.get("last")
    dt = now["total"] - prev["total"]
    if dt <= 0.25:
        return _cpu_prev.get("last")
    res = {k: max(0.0, min(1.0, (now[k] - prev[k]) / dt))
           for k in ("user", "system", "idle", "iowait")}
    _cpu_prev["last"] = res
    return res


def _cpu_percpu_delta():
    """每核占用百分比（0..100）。间隔太短就沿用上次。"""
    cur = psutil.cpu_times(percpu=True)
    prev = _cpu_prev["procs"]
    _cpu_prev["procs"] = cur
    if not prev or len(prev) != len(cur):
        return _cpu_prev.get("lastp") or []
    out = []
    tiny = True
    for a, b in zip(cur, prev):
        ta = (a.user + a.system + a.idle + getattr(a, "iowait", 0.0)
              + getattr(a, "nice", 0.0) + getattr(a, "irq", 0.0)
              + getattr(a, "softirq", 0.0))
        tb = (b.user + b.system + b.idle + getattr(b, "iowait", 0.0)
              + getattr(b, "nice", 0.0) + getattr(b, "irq", 0.0)
              + getattr(b, "softirq", 0.0))
        d = ta - tb
        if d <= 0:
            out.append(0.0)
            continue
        if d > 0.25:
            tiny = False
        used = ((a.user - b.user) + (a.system - b.system)
                + (getattr(a, "nice", 0.0) - getattr(b, "nice", 0.0)))
        out.append(round(max(0.0, min(100.0, used / d * 100)), 0))
    if tiny and _cpu_prev.get("lastp"):
        return _cpu_prev["lastp"]
    _cpu_prev["lastp"] = out
    return out


# 打底一次，让第一帧就有差分基准
try:
    _cpu_prev["total"] = _cpu_snapshot()
    _cpu_prev["procs"] = psutil.cpu_times(percpu=True)
    _cpu_prev["t"] = time.time()
    # 再等 0.25s 采一次，让首帧就有真实的 user/system 占比（不然第一帧全是 0）
    time.sleep(0.25)
    _cpu_time_delta()
    _cpu_percpu_delta()
except Exception:
    pass


# ---------------------------------------------------------------- GPU
_gpu_nv_cache = {"t": 0.0, "v": None}
_GPU_NV_TTL = 0.9      # 别每帧都起进程，省 CPU / 内存


def _gpu_nvidia():
    """读 nvidia-smi，失败返回 None（结果缓存 0.9s）"""
    if not _NVIDIA:
        return None
    now = time.time()
    if now - _gpu_nv_cache["t"] < _GPU_NV_TTL:
        return _gpu_nv_cache["v"]
    fields = ("name,temperature.gpu,utilization.gpu,utilization.memory,"
              "utilization.encoder,utilization.decoder,"
              "memory.used,memory.total,"
              "power.draw,clocks.gr,clocks.max.gr,clocks.mem,fan.speed")
    v = None
    try:
        p = subprocess.run(
            [_NVIDIA, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if p.returncode == 0 and p.stdout.strip():
            row = next(csv.reader(io.StringIO(p.stdout.strip().splitlines()[0])))
            row = [c.strip() for c in row]

            def f(i):
                try:
                    return float(row[i])
                except (ValueError, IndexError):
                    return None

            v = {
                "name": row[0],
                "temp": f(1),
                "usage": f(2),
                "mem_util": f(3),
                "enc_util": f(4),
                "dec_util": f(5),
                "vram_used_mb": f(6),
                "vram_total_mb": f(7),
                "power": f(8),
                "clock": f(9),
                "clock_max": f(10),
                "mem_clock": f(11),
                "fan": f(12),
            }
    except Exception as e:
        log.debug("nvidia-smi 失败: %s", e)
    _gpu_nv_cache.update(t=now, v=v)
    return v


def _gpu_wmi():
    """没有 nvidia-smi 时的兜底（只有名字和显存）"""
    try:
        import subprocess as sp
        out = sp.run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_VideoController | Select-Object -First 1 -Property Name,AdapterRAM | ConvertTo-Json -Compress"],
                     capture_output=True, text=True, timeout=6,
                     creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
        import json
        d = json.loads(out.stdout)
        return {"name": d.get("Name", "GPU"),
                "vram_total_mb": round((d.get("AdapterRAM") or 0) / 1048576, 0)}
    except Exception:
        return None


# ---------------------------------------------------------------- 网速
def _net_speed():
    global _last_net, _last_net_t
    c = psutil.net_io_counters()
    now = time.time()
    up = down = 0.0
    if _last_net is not None and _last_net_t:
        dt = max(now - _last_net_t, 1e-6)
        up = max(c.bytes_sent - _last_net.bytes_sent, 0) / dt / 1048576 * 8
        down = max(c.bytes_recv - _last_net.bytes_recv, 0) / dt / 1048576 * 8
    _last_net, _last_net_t = c, now
    return round(up, 2), round(down, 2)


# ---------------------------------------------------------------- CPU 温度兜底
_wmi_temp_cache = {"t": 0.0, "v": None}


def _wmi_cpu_temp():
    """没有 AIDA64 时试一下 WMI 热区（很多主板不支持，失败就算了）。缓存 10 秒。"""
    now = time.time()
    if now - _wmi_temp_cache["t"] < 10:
        return _wmi_temp_cache["v"]
    val = None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "try{(Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
             "-EA Stop | Select-Object -First 1).CurrentTemperature}catch{''}"],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        s = out.stdout.strip()
        if s.isdigit():
            k = int(s) / 10.0            # 单位是 0.1 开尔文
            c = k - 273.15
            if -20 < c < 150:
                val = round(c)
    except Exception as e:
        log.debug("WMI 温度失败: %s", e)
    _wmi_temp_cache.update(t=now, v=val)
    return val


# ---------------------------------------------------------------- 磁盘
_SKIP_FS = {"cdrom", "tmpfs", "devfs", "squashfs", "overlay", "none", "udf", "cdfs"}
_SKIP_MOUNT = {"C:\\Windows", "C:\\Windows\\System32"}
# GetDriveTypeW 返回值：0=未知 1=无根目录 2=可移动 3=固定 4=网络 5=光驱 6=内存盘
_SKIP_DRIVE_TYPE = {0, 1, 5}          # 光驱/挂载的 ISO 不该算进存储占用


def _drive_type(mount):
    """返回盘符的 Win32 驱动器类型；非 Windows 或取不到时返回 None。"""
    try:
        import ctypes
        root = mount.rstrip("\\") + "\\"
        t = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        return int(t)
    except Exception:
        return None


# 盘符 -> 物理盘序号（形如 {"C": 1, "D": 1, "E": 2, "F": 0}），基本不变，缓存 5 分钟
_drive_map = {"t": 0.0, "v": {}}


class _STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", wintypes.DWORD),
                ("DeviceNumber", wintypes.DWORD),
                ("PartitionNumber", wintypes.DWORD)]


_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
_GENERIC_READ = 0x80000000
_FILE_SHARE_RW = 0x00000001 | 0x00000002
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _phys_of_drive(letter):
    """用 IOCTL_STORAGE_GET_DEVICE_NUMBER 直接问内核这块卷在哪个物理盘上。
    比走 WMI/PowerShell 快得多，也不用解析字符串。"""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p]
        h = k32.CreateFileW(f"\\\\.\\{letter}:", _GENERIC_READ,
                            _FILE_SHARE_RW, None, _OPEN_EXISTING, 0, None)
        if not h or h == _INVALID_HANDLE:
            # 打不开就退一步，只要 0 权限
            h = k32.CreateFileW(f"\\\\.\\{letter}:", 0,
                                _FILE_SHARE_RW, None, _OPEN_EXISTING, 0, None)
        if not h or h == _INVALID_HANDLE:
            return None
        try:
            sdn = _STORAGE_DEVICE_NUMBER()
            got = wintypes.DWORD(0)
            k32.DeviceIoControl.argtypes = [
                ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            ok = k32.DeviceIoControl(h, _IOCTL_STORAGE_GET_DEVICE_NUMBER,
                                     None, 0, ctypes.byref(sdn),
                                     ctypes.sizeof(sdn), ctypes.byref(got), None)
            if not ok:
                return None
            return int(sdn.DeviceNumber)
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    except Exception as e:
        log.debug("IOCTL 查物理盘失败 %s: %s", letter, e)
        return None


def _logical_to_physical():
    """把每个可见盘符映射到 PhysicalDriveN 序号。整体缓存 5 分钟。"""
    now = time.time()
    if now - _drive_map["t"] < 300 and _drive_map["v"]:
        return _drive_map["v"]
    m = {}
    try:
        import string
        for ch in string.ascii_uppercase:
            if not os.path.exists(f"{ch}:\\"):
                continue
            n = _phys_of_drive(ch)
            if n is not None:
                m[ch] = n
    except Exception as e:
        log.debug("盘符映射失败: %s", e)
    if m:
        _drive_map.update(t=now, v=m)
    return m


# 磁盘 IO 差分状态（import 时就打一次底，避免第一帧全是空）
_disk_io_prev = {"t": 0.0, "v": {}}


def _disk_io():
    """按物理盘算读写速度 / 活动率 / 平均响应时间（两次采样差分）。"""
    now = time.time()
    cur = {}
    try:
        for k, v in psutil.disk_io_counters(perdisk=True).items():
            cur[k] = (v.read_bytes, v.write_bytes, v.read_time, v.write_time,
                      v.read_count, v.write_count)
    except Exception as e:
        log.debug("disk_io_counters 失败: %s", e)
        return {}

    prev, pt = _disk_io_prev["v"], _disk_io_prev["t"]
    _disk_io_prev.update(t=now, v=cur)
    if not prev or not pt:
        return {}

    dt = now - pt
    if dt <= 0.05:
        return {}
    out = {}
    for k, y in cur.items():
        x = prev.get(k)
        if not x:
            continue
        dr = max(y[0] - x[0], 0) / dt
        dw = max(y[1] - x[1], 0) / dt
        dtm = max((y[2] - x[2]) + (y[3] - x[3]), 0)     # 毫秒
        dn = max((y[4] - x[4]) + (y[5] - x[5]), 0)      # 操作次数
        busy = min(dtm / (dt * 1000) * 100, 100.0)
        resp = (dtm / dn) if dn else 0.0
        out[k] = {
            "read_bps": round(dr, 0),
            "write_bps": round(dw, 0),
            "busy": round(busy, 1),
            "resp_ms": round(resp, 2),
        }
    return out


try:      # 打底一次，让第一帧就有差分基准
    _disk_io_prev["v"] = {
        k: (v.read_bytes, v.write_bytes, v.read_time, v.write_time,
            v.read_count, v.write_count)
        for k, v in psutil.disk_io_counters(perdisk=True).items()
    }
    _disk_io_prev["t"] = time.time()
except Exception:
    pass


def _disks():
    out = []
    seen = set()
    dmap = _logical_to_physical()
    io = _disk_io()
    for p in psutil.disk_partitions(all=False):
        if not p.fstype or p.fstype.lower() in _SKIP_FS:
            continue
        if p.mountpoint in _SKIP_MOUNT:
            continue
        dt = _drive_type(p.mountpoint)
        if dt is not None and dt in _SKIP_DRIVE_TYPE:
            log.debug("跳过 %s：驱动器类型 %s（光驱/未知）", p.mountpoint, dt)
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        if u.total <= 0:
            continue
        # 同一个物理盘去重（按总容量+已用）
        key = (u.total, u.used)
        if key in seen:
            continue
        seen.add(key)
        drive = p.mountpoint.replace("\\", "").rstrip(":")
        phys = dmap.get(drive.upper())
        dio = io.get(f"PhysicalDrive{phys}") if phys is not None else None
        out.append({
            "label": drive,
            "mount": p.mountpoint,
            "total_gb": round(u.total / 1073741824, 0),
            "used_gb": round(u.used / 1073741824, 0),
            "free_gb": round(u.free / 1073741824, 0),
            "percent": round(u.percent, 1),
            "phys": phys,
            "read_bps": dio["read_bps"] if dio else None,
            "write_bps": dio["write_bps"] if dio else None,
            "busy": dio["busy"] if dio else None,
            "resp_ms": dio["resp_ms"] if dio else None,
        })
    out.sort(key=lambda d: d["label"])
    return out


# ---------------------------------------------------------------- 内存详情
_mem_spec = {"t": 0.0, "v": None}


class _MEMSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PERFINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t),
                ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t),
                ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
                ("PageSize", ctypes.c_size_t), ("HandleCount", wintypes.DWORD),
                ("ProcessCount", wintypes.DWORD), ("ThreadCount", wintypes.DWORD)]


def _mem_specs():
    """内存条规格（容量/频率/插槽/外形/硬件保留），基本不变，缓存 10 分钟。"""
    now = time.time()
    if now - _mem_spec["t"] < 600 and _mem_spec["v"] is not None:
        return _mem_spec["v"]
    v = {}
    try:
        ps = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$dim=@(Get-CimInstance Win32_PhysicalMemory | ForEach-Object {"
            "  [pscustomobject]@{cap=[int64]$_.Capacity; spd=$_.Speed;"
            "   cfg=$_.ConfiguredClockSpeed; ff=$_.FormFactor;"
            "   bank=$_.BankLabel; loc=$_.DeviceLocator; part=($_.PartNumber -replace '\\s+','')}});"
            "$arr=Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1;"
            "$os=Get-CimInstance Win32_OperatingSystem;"
            "[pscustomobject]@{dims=$dim; slots=$arr.MemoryDevices;"
            " visible_kb=[int64]$os.TotalVisibleMemorySize} | ConvertTo-Json -Depth 4 -Compress"
        )
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=12,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import json as _json
        raw = (p.stdout or "").strip()
        if raw:
            d = _json.loads(raw)
            dims = d.get("dims") or []
            if isinstance(dims, dict):
                dims = [dims]
            GB = 1073741824
            total = sum(int(x.get("cap") or 0) for x in dims) / GB
            speeds = [int(x.get("spd") or 0) for x in dims if x.get("spd")]
            cfgs = [int(x.get("cfg") or 0) for x in dims if x.get("cfg")]
            # FormFactor: 8=DIMM 12=SODIMM 13=SRIMM ...
            FF = {8: "DIMM", 12: "SODIMM", 13: "SRIMM", 11: "RIMM"}
            ff = FF.get(int(dims[0].get("ff") or 0), "") if dims else ""
            used_slots = len([x for x in dims if int(x.get("cap") or 0) > 0])
            slots = int(d.get("slots") or 0) or used_slots
            visible = int(d.get("visible_kb") or 0) * 1024 / GB
            v = {
                "sticks": len(dims),
                "total_gb": round(total, 1),
                "speed": max(speeds) if speeds else None,
                "speed_cfg": max(cfgs) if cfgs else None,
                "form": ff,
                "slots_used": used_slots,
                "slots_total": slots,
                "reserved_gb": round(max(total - visible, 0), 2) if visible else None,
                "bank": ", ".join(sorted({str(x.get("bank") or "") for x in dims if x.get("bank")})),
                "part": dims[0].get("part") if dims else None,
            }
    except Exception as e:
        log.debug("内存规格读取失败: %s", e)
    _mem_spec.update(t=now, v=v)
    return v


def _mem_detail(mem):
    """内存明细：已提交/已缓存/缓冲池/核心内存。失败时只返回 psutil 那几项。"""
    GB = 1073741824
    out = {
        "used_gb": round(mem.used / GB, 1),
        "total_gb": round(mem.total / GB, 1),
        "avail_gb": round(mem.available / GB, 1),
        "usage": round(mem.percent, 1),
    }
    try:
        ms = _MEMSTATUSEX()
        ms.dwLength = ctypes.sizeof(_MEMSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        out["commit_used_gb"] = round((ms.ullTotalPageFile - ms.ullAvailPageFile) / GB, 1)
        out["commit_limit_gb"] = round(ms.ullTotalPageFile / GB, 1)
        out["swap_used_gb"] = round(psutil.swap_memory().used / GB, 1)
    except Exception as e:
        log.debug("GlobalMemoryStatusEx 失败: %s", e)
    try:
        pi = _PERFINFO()
        pi.cb = ctypes.sizeof(_PERFINFO)
        ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb)
        ps_ = pi.PageSize or 4096
        out["cached_gb"] = round(pi.SystemCache * ps_ / GB, 1)
        out["pool_paged_gb"] = round(pi.KernelPaged * ps_ / GB, 2)
        out["pool_nonpaged_gb"] = round(pi.KernelNonpaged * ps_ / GB, 2)
        out["kernel_gb"] = round(pi.KernelTotal * ps_ / GB, 2)
    except Exception as e:
        log.debug("GetPerformanceInfo 失败: %s", e)
    spec = _mem_specs()
    if spec:
        out["spec"] = spec
    return out


# ---------------------------------------------------------------- 主入口
def snapshot():
    a = aida64.read()
    l = lhm.read()
    lv = l["values"]

    # 合并：AIDA64 有值就用 AIDA64，没有的用 LHM 补。
    av = dict(lv)
    if a["ok"]:
        av.update({k: v for k, v in (a["values"] or {}).items() if v is not None})

    cpu_name = _cpu_name()
    freq = psutil.cpu_freq()
    cpu_usage = av.get("cpu_usage")
    if cpu_usage is None:
        cpu_usage = psutil.cpu_percent(interval=None)

    # CPU 利用率细分（user / system / idle / iowait）+ 每核占用
    # 自己算差分，不用 psutil.cpu_times_percent —— 那个第一次调用恒返回 0，
    # 而且和 cpu_percent 共享内部基准，采样节奏一变就容易抖
    cpu_split = {}
    try:
        tp = _cpu_time_delta()
        if tp:
            cpu_split = {
                "user": round(tp["user"] * 100, 1),
                "system": round(tp["system"] * 100, 1),
                "idle": round(tp["idle"] * 100, 1),
                "iowait": round(tp.get("iowait", 0.0) * 100, 1),
            }
    except Exception as e:
        log.debug("CPU 细分失败: %s", e)
    try:
        percpu = _cpu_percpu_delta()
    except Exception as e:
        log.debug("每核占用失败: %s", e)
        percpu = []

    mem = psutil.virtual_memory()

    gpu = _gpu_nvidia() or _gpu_wmi() or {"name": _gpu_name()}

    # LHM 的 GPU 数据补进来（nvidia-smi 给不了的：热点、核心电压、显存负载）
    if lv:
        if gpu.get("temp") is None and lv.get("gpu_temp") is not None:
            gpu["temp"] = lv["gpu_temp"]
        if lv.get("gpu_hotspot") is not None:
            gpu["hotspot"] = lv["gpu_hotspot"]
        if lv.get("gpu_volt") is not None:
            gpu["volt"] = lv["gpu_volt"]
        if lv.get("gpu_mem_load") is not None:
            gpu["mem_load"] = lv["gpu_mem_load"]
        if lv.get("gpu_power") is not None and gpu.get("power") is None:
            gpu["power"] = lv["gpu_power"]
        if lv.get("gpu_mem_clock") is not None:
            gpu["mem_clock"] = lv["gpu_mem_clock"]
        if gpu.get("clock") is None and lv.get("gpu_clock") is not None:
            gpu["clock"] = lv["gpu_clock"]

    # AIDA64 有更细的 GPU 温度（显存温度/风扇），AIDA64 优先
    if a["ok"]:
        if gpu.get("temp") is None and av.get("gpu_temp") is not None:
            gpu["temp"] = av["gpu_temp"]
        if av.get("gpu_hotspot") is not None:
            gpu["hotspot"] = av["gpu_hotspot"]
        if av.get("gpu_mem_temp") is not None:
            gpu["mem_temp"] = av["gpu_mem_temp"]
        if gpu.get("usage") is None and av.get("gpu_usage") is not None:
            gpu["usage"] = av["gpu_usage"]
        if av.get("gpu_power") is not None:
            gpu["power"] = av["gpu_power"]
        if av.get("gpu_volt") is not None:
            gpu["volt"] = av["gpu_volt"]
        if av.get("gpu_clock") is not None and gpu.get("clock") is None:
            gpu["clock"] = av["gpu_clock"]

    # 三层都没有 CPU 温度时，最后试一下 WMI 热区（多数主板会失败）
    if av.get("cpu_temp") is None:
        cpu_temp_fb = _wmi_cpu_temp()
        if cpu_temp_fb is not None:
            av["cpu_temp"] = cpu_temp_fb

    up, down = _net_speed()

    # 温度列表
    temps = []
    if av.get("cpu_temp") is not None:
        temps.append({"label": "CPU", "value": round(av["cpu_temp"]), "unit": "°C"})
    if av.get("cpu_pkg_temp") is not None:
        temps.append({"label": "CPU Package", "value": round(av["cpu_pkg_temp"]), "unit": "°C"})
    if av.get("gpu_temp") is not None:
        temps.append({"label": "GPU", "value": round(av["gpu_temp"]), "unit": "°C"})
    if av.get("gpu_hotspot") is not None:
        temps.append({"label": "GPU Hotspot", "value": round(av["gpu_hotspot"]), "unit": "°C"})
    if av.get("gpu_mem_temp") is not None:
        temps.append({"label": "GPU Memory", "value": round(av["gpu_mem_temp"]), "unit": "°C"})
    if av.get("mem_temp") is not None:
        temps.append({"label": "DIMM", "value": round(av["mem_temp"]), "unit": "°C"})
    if av.get("mb_temp") is not None:
        temps.append({"label": "主板", "value": round(av["mb_temp"]), "unit": "°C"})
    # AMD CPU 的 CCD / SoC 温度（LHM 直接给，AIDA64 不一定有）
    if av.get("cpu_ccd_temp") is not None:
        temps.append({"label": "CPU CCD1", "value": round(av["cpu_ccd_temp"]), "unit": "°C"})
    if av.get("soc_temp") is not None:
        temps.append({"label": "SoC", "value": round(av["soc_temp"]), "unit": "°C"})

    # 风扇：AIDA64 优先，没有再吃 LHM 的
    fans = (a["fans"] or []) if a["ok"] else []
    if not fans:
        fans = l.get("fans") or []

    boot = psutil.boot_time()
    upsec = int(time.time() - boot)

    cpu_obj = {
        "name": cpu_name,
        "usage": round(cpu_usage, 1),
        "temp": round(av["cpu_temp"]) if av.get("cpu_temp") is not None else None,
        "pkg_temp": round(av["cpu_pkg_temp"]) if av.get("cpu_pkg_temp") is not None else None,
        "clock": round((freq.current / 1000) if freq else 0, 2),
        "volt": av.get("cpu_volt"),
        "power": av.get("cpu_power"),
        "cores": psutil.cpu_count(logical=False),   # 物理核心
        "threads": psutil.cpu_count(logical=True),  # 逻辑线程
    }
    cpu_obj.update(cpu_split)
    if percpu:
        cpu_obj["percpu"] = percpu

    return {
        "ts": int(time.time() * 1000),
        "source": {
            "aida64": a["ok"],
            "lhm": l["ok"],
            "pawnio": l.get("pawnio", False),
            "nvidia": bool(_NVIDIA),
        },
        "cpu": cpu_obj,
        "gpu": gpu,
        "mem": _mem_detail(mem),
        "disks": _disks(),
        "temps": temps,
        "fans": fans,
        "net": {"up": up, "down": down},
        "wifi": network.wifi(),
        "weather": network.weather(),
        "uptime": upsec,
    }


def _cpu_name():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except Exception:
        return "CPU"


def _gpu_name():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or "GPU"
    except Exception:
        return "GPU"
