# -*- coding: utf-8 -*-
"""
媒体播放控制 + 歌词

两条链路：
  1. Windows SMTC（System Media Transport Controls）
     —— 任何走系统媒体的播放器（网易云 / QQ音乐 / Spotify / 浏览器 / PotPlayer）
        都会注册会话，可以读 歌名/歌手/专辑/播放状态/进度，也能控制 播放/暂停/上一首/下一首。
  2. pycaw 读改系统主音量。

歌词：按「歌名 + 歌手」去 lrclib.net 搜（免费无 key，中文库不错），
      失败再退到网易云歌词接口。结果按 (title, artist) 缓存，不重复请求。
"""
from __future__ import annotations

import asyncio
import re
import threading
import time

import requests

# ---------------- 缓存 ----------------
_state = {"t": 0.0, "v": None}
_TTL = 1.2                      # 会话状态缓存秒数
_lyr = {"key": None, "t": 0.0, "lines": []}
_LYR_TTL = 1800

_lock = threading.Lock()

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# SMTC 播放状态：0=Closed 1=Opened 2=Changing 3=Stopped 4=Playing 5=Paused
_PLAYING = 4


# ============================================================
#  SMTC
# ============================================================
def _run(coro):
    """在独立事件循环里跑 winsdk 的 async 调用（Flask 是多线程的，不能共用 loop）"""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


async def _get_session():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager)
    mgr = await Manager.request_async()
    return mgr.get_current_session()


async def _snapshot_async():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PS)
    s = await _get_session()
    if s is None:
        return {"ok": False, "playing": False, "title": "", "artist": "",
                "album": "", "position": 0.0, "duration": 0.0,
                "app": "", "status": "none"}

    out = {"ok": True, "app": "", "title": "", "artist": "", "album": "",
           "playing": False, "position": 0.0, "duration": 0.0, "status": "closed"}
    try:
        out["app"] = s.source_app_user_model_id or ""
    except Exception:
        pass
    try:
        p = await s.try_get_media_properties_async()
        if p is not None:
            out["title"] = p.title or ""
            out["artist"] = p.artist or ""
            out["album"] = p.album_title or ""
    except Exception:
        pass
    try:
        info = s.get_playback_info()
        st = int(info.playback_status)
        out["status"] = {
            0: "closed", 1: "opened", 2: "changing",
            3: "stopped", 4: "playing", 5: "paused",
        }.get(st, "unknown")
        out["playing"] = (st == _PLAYING)
    except Exception:
        pass
    try:
        tl = s.get_timeline_properties()
        out["position"] = tl.position.total_seconds()
        out["duration"] = tl.end_time.total_seconds()
    except Exception:
        pass
    return out


def read(force: bool = False):
    """当前媒体会话状态（带 1.2s 缓存，避免每帧都走 COM）"""
    now = time.time()
    if not force and _state["v"] is not None and now - _state["t"] < _TTL:
        return _state["v"]
    try:
        v = _run(_snapshot_async())
    except Exception as e:
        v = {"ok": False, "playing": False, "title": "", "artist": "", "album": "",
             "position": 0.0, "duration": 0.0, "app": "", "status": "error",
             "error": f"{type(e).__name__}: {e}"}
    _state["t"], _state["v"] = now, v
    return v


# ---------------- 控制 ----------------
async def _control_async(action: str):
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager)
    mgr = await Manager.request_async()
    s = mgr.get_current_session()
    if s is None:
        return False
    fn = {
        "play": s.try_play_async,
        "pause": s.try_pause_async,
        "toggle": s.try_toggle_play_pause_async,
        "next": s.try_skip_next_async,
        "prev": s.try_skip_previous_async,
        "stop": s.try_stop_async,
    }.get(action)
    if fn is None:
        return False
    return bool(await fn())


def control(action: str):
    try:
        ok = _run(_control_async(action))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _state["t"] = 0.0            # 清缓存，下一次立刻反映新状态
    return {"ok": bool(ok), "action": action}


# ============================================================
#  系统音量（pycaw）
# ============================================================
def _vol_iface():
    from pycaw.pycaw import AudioUtilities
    dev = AudioUtilities.GetSpeakers()
    return dev.EndpointVolume     # pycaw 新版直接给 IAudioEndpointVolume


def volume():
    try:
        v = _vol_iface()
        return {"ok": True, "percent": round(v.GetMasterVolumeLevelScalar() * 100),
                "mute": bool(v.GetMute())}
    except Exception as e:
        return {"ok": False, "percent": None, "mute": None,
                "error": f"{type(e).__name__}: {e}"}


def set_volume(percent):
    try:
        p = max(0, min(100, float(percent)))
        _vol_iface().SetMasterVolumeLevelScalar(p / 100.0, None)
        return {"ok": True, "percent": round(p)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def set_mute(flag):
    try:
        _vol_iface().SetMute(1 if flag else 0, None)
        return {"ok": True, "mute": bool(flag)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ============================================================
#  歌词
# ============================================================
_LRC_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def _parse_lrc(text):
    """LRC -> [(秒, 文本)]，按时间排序，去掉空行"""
    out = []
    if not text:
        return out
    for raw in text.splitlines():
        stamps = list(_LRC_RE.finditer(raw))
        if not stamps:
            continue
        body = _LRC_RE.sub("", raw).strip()
        if not body:
            continue
        for m in stamps:
            mi = int(m.group(1)) * 60 + int(m.group(2))
            frac = m.group(3) or "0"
            frac = (frac + "00")[:3] if len(frac) < 3 else frac[:3]
            sec = mi + int(frac) / 1000.0 if m.group(3) else mi
            out.append((sec, body))
    out.sort(key=lambda x: x[0])
    return out


def _clean(s):
    """去掉 (Live) (伴奏) 这类后缀，提高搜索命中率"""
    if not s:
        return ""
    s = re.sub(r"[\(（\[【].*?[\)）\]】]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _lyr_lrclib(title, artist):
    try:
        r = requests.get("https://lrclib.net/api/search",
                         params={"track_name": title, "artist_name": artist},
                         headers={"User-Agent": "subscreen-monitor/1.0"},
                         timeout=8)
        if r.status_code != 200:
            return []
        for it in (r.json() or []):
            sl = it.get("syncedLyrics")
            if sl:
                lines = _parse_lrc(sl)
                if lines:
                    return lines
    except Exception:
        pass
    return []


def _lyr_netease(title, artist):
    H = {"User-Agent": _UA, "Referer": "https://music.163.com/"}
    try:
        r = requests.post("https://music.163.com/api/search/get/web",
                          data={"s": f"{title} {artist}".strip(),
                                "type": 1, "limit": 5, "offset": 0},
                          headers=H, timeout=8)
        j = r.json()
        songs = (j.get("result") or {}).get("songs") or []
        for s in songs:
            sid = s.get("id")
            if not sid:
                continue
            r2 = requests.get("https://music.163.com/api/song/lyric",
                              params={"id": sid, "lv": 1, "kv": 1, "tv": -1},
                              headers=H, timeout=8)
            lrc = ((r2.json().get("lrc") or {}).get("lyric")) or ""
            lines = _parse_lrc(lrc)
            if len(lines) >= 3 and lines[-1][0] > 20:
                return lines
    except Exception:
        pass
    return []


def lyrics(title, artist):
    """拿歌词行。优先 lrclib（干净），退到网易云。结果缓存 30 分钟。"""
    title = (title or "").strip()
    artist = (artist or "").strip()
    if not title:
        return []
    key = (_clean(title).lower(), _clean(artist).lower())
    now = time.time()
    if _lyr["key"] == key and now - _lyr["t"] < _LYR_TTL and _lyr["lines"]:
        return _lyr["lines"]

    lines = _lyr_lrclib(_clean(title), _clean(artist))
    if not lines:
        lines = _lyr_netease(_clean(title), _clean(artist))

    _lyr.update(key=key, t=now, lines=lines)
    return lines


def lyric_at(lines, pos):
    """按播放进度取当前行索引；返回 (idx, prev, cur, next)"""
    if not lines:
        return -1, "", "", ""
    idx = -1
    for i, (t, _) in enumerate(lines):
        if t <= pos:
            idx = i
        else:
            break
    prev_ = lines[idx - 1][1] if idx - 1 >= 0 else ""
    cur = lines[idx][1] if idx >= 0 else ""
    nxt = lines[idx + 1][1] if idx + 1 < len(lines) else ""
    return idx, prev_, cur, nxt


# ============================================================
#  给前端的一体化状态
# ============================================================
def status():
    st = read()
    vol = volume()
    lines = []
    idx, prev_, cur, nxt = -1, "", "", ""
    if st.get("ok") and st.get("title"):
        lines = lyrics(st["title"], st.get("artist", ""))
        idx, prev_, cur, nxt = lyric_at(lines, st.get("position") or 0.0)

    # 会话已关但歌词还在飞的情况：也算 ok
    return {
        "ok": bool(st.get("ok")),
        "playing": bool(st.get("playing")),
        "title": st.get("title") or "",
        "artist": st.get("artist") or "",
        "album": st.get("album") or "",
        "app": st.get("app") or "",
        "status": st.get("status") or "none",
        "position": round(st.get("position") or 0, 1),
        "duration": round(st.get("duration") or 0, 1),
        "volume": vol.get("percent"),
        "mute": vol.get("mute"),
        "lyric": cur,
        "lyric_prev": prev_,
        "lyric_next": nxt,
        "lyric_index": idx,
        "lyric_count": len(lines),
        "has_lyric": bool(lines),
        "error": st.get("error"),
    }


if __name__ == "__main__":
    import json
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("volume:", json.dumps(volume(), ensure_ascii=False))
    s = read()
    print("session:", json.dumps(s, ensure_ascii=False))
    if s.get("ok") and s.get("title"):
        lines = lyrics(s["title"], s.get("artist", ""))
        print("lyric lines:", len(lines))
        for t, txt in lines[:8]:
            print(f"  {t:7.2f}  {txt}")
    else:
        print("没有媒体会话（先打开网易云/QQ音乐并放一首歌）")
    st = status()
    print("status:", json.dumps(st, ensure_ascii=False))
