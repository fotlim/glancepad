# -*- coding: utf-8 -*-
"""
媒体播放控制 + 歌词

两条链路：
  1. Windows SMTC（System Media Transport Controls）
     —— 任何走系统媒体的播放器（网易云 / QQ音乐 / Spotify / 浏览器 / PotPlayer）
        都会注册会话，可以读 歌名/歌手/专辑/播放状态/进度，也能控制 播放/暂停/上一首/下一首。
  2. pycaw 读改系统主音量。

歌词：按「歌名 + 歌手 + 时长」去**网易云**搜（中文库最全、简体），
      查不到再退到 lrclib.net（免费无 key，但华语歌常只有繁体）。两条链路都按
      时长/歌名/歌手 打分挑最像原曲的那一条，结果按 (title, artist, duration) 缓存。

      网易云注意两点（2026-10 实测）：
        1. 老接口 /api/search/get/web 现在恒返回 405「操作频繁」，必须用
           /api/cloudsearch/pc 搜歌。
        2. 取歌词时 lv=1 对大量歌曲直接返回空串，要用 lv=-1 kv=-1 tv=-1
           才会带上逐行歌词。
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from difflib import SequenceMatcher

import requests

# ---------------- 缓存 ----------------
_state = {"t": 0.0, "v": None}
_TTL = 1.2                      # 会话状态缓存秒数
_lyr = {"key": None, "t": 0.0, "lines": []}
_LYR_TTL = 1800          # 命中歌词缓存 30 分钟
_LYR_MISS_TTL = 180      # 没歌词也别每帧重查（否则每 1.5s 卡 10 秒），3 分钟后再试

_lock = threading.Lock()
_pending = set()          # 正在后台查歌词的 key，防止同一首并发起一堆线程
_pos = {"key": None, "v": 0.0}   # 上次外推出来的播放进度（暂停时沿用）

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
        raw_pos = tl.position.total_seconds()
        out["duration"] = tl.end_time.total_seconds()
        out["position"] = raw_pos
        # 有些播放器（Foliamajor 这类第三方壳）注册了 SMTC 却**从不推进 position**，
        # 每次都停在 0.0x 秒，歌词就永远卡在「前奏」。这里用 last_updated_time
        # 自己外推出当前进度：播放器真在推 position 时，last_updated_time 也会跟着变，
        # delta ≈ 0，不会重复累加。
        key = (out["title"], out["artist"], out["duration"])
        if out["playing"]:
            pos2 = raw_pos
            last = getattr(tl, "last_updated_time", None)
            if last is not None:
                import datetime as _dt
                try:
                    delta = (_dt.datetime.now(_dt.timezone.utc) - last).total_seconds()
                except Exception:
                    delta = 0.0
                if 0 < delta < 3600:
                    pos2 = raw_pos + delta
            if out["duration"] and pos2 > out["duration"] + 2:
                pos2 = out["duration"]
            out["position"] = pos2
            _pos["key"], _pos["v"] = key, pos2
        else:
            # 暂停时 position 不再推进，沿用最后一次算出来的位置，别跳回 0
            if _pos["key"] == key:
                out["position"] = _pos["v"]
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


def _norm(s):
    """归一化：小写、去括号后缀、去空白与标点，用于歌名/歌手比对"""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[\(（\[【].*?[\)）\]】]", "", s)
    s = re.sub(r"[\s\-_·・,，.。!！?？'\"“”‘’/\\]+", "", s)
    return s


def _score(name, artist, dur, want_title, want_artist, want_dur):
    """给候选歌曲打分：歌名 > 歌手 > 时长。时长越接近原曲越可信（能排掉翻唱/伴奏/钢琴版）"""
    sc = 0.0
    nt, nt2 = _norm(name), _norm(want_title)
    if nt and nt == nt2:
        sc += 60
    elif nt2:
        sc += 40 * SequenceMatcher(None, nt, nt2).ratio()
        if nt2 in nt:
            sc += 8
    na, na2 = _norm(artist), _norm(want_artist)
    if na2:
        if na == na2:
            sc += 25
        elif na2 and (na2 in na or na in na2):
            sc += 15
    if want_dur and dur:
        d = abs(dur - want_dur)
        if d <= 3:
            sc += 25
        elif d <= 8:
            sc += 12
        elif d <= 20:
            sc += 3
        elif d > 60:
            sc -= 30
    return sc


_CREDIT_RE = re.compile(r"^([^:：]{1,12})[:：]\s*(.+)$")
_CREDIT_KW = re.compile(
    r"作词|作曲|编曲|制作|监制|混音|母带|和声|合声|录音|配唱|吉他|贝斯|鼓|键盘|弦乐|"
    r"出品|发行|统筹|企划|策划|人声|版权|词|曲|Lyrics?|Composer|Arranger|Producer|"
    r"Written|Performed|Mixed|Mastered",
    re.IGNORECASE)


def _strip_credits(lines, title=""):
    """去掉署名行、纯音乐占位行、以及开头重复的歌名行。

    署名行的两种长相：
      「作词 : Chris Martin/Guy Berryman/...」—— 标签是关键词，内容再长也删；
      「词版权管理方：北京梦织音传媒有限公司」—— 标签短、内容也短，一并删。
    正常歌词里带冒号的句子（「我说：……」）标签长或内容长，不会被误删。
    """
    out = []
    nt = _norm(title)
    for i, (t, txt) in enumerate(lines):
        s = txt.strip()
        if not s:
            continue
        if "纯音乐" in s and len(s) <= 12:
            continue
        m = _CREDIT_RE.match(s)
        if m:
            label, body = m.group(1), m.group(2)
            if _CREDIT_KW.search(label):
                continue
            if len(label.strip()) <= 8 and " " not in label.strip() and len(body) <= 30:
                continue
        if i < 3 and nt and nt in _norm(s) and ("-" in s or "(" in s or "（" in s):
            continue
        out.append((t, s))
    return out


def _lyr_lrclib(title, artist, dur=0.0):
    """lrclib：一次搜多条，按时长挑最接近原曲的那条（避免拿成翻唱/现场版）"""
    try:
        r = requests.get("https://lrclib.net/api/search",
                         params={"track_name": title, "artist_name": artist},
                         headers={"User-Agent": "subscreen-monitor/1.0"},
                         timeout=8)
        if r.status_code != 200:
            return []
        best, best_sc = [], -1e9
        for it in (r.json() or []):
            sl = it.get("syncedLyrics")
            if not sl:
                continue
            lines = _strip_credits(_parse_lrc(sl), title)
            if len(lines) < 3:
                continue
            sc = _score(it.get("trackName") or "", it.get("artistName") or "",
                        float(it.get("duration") or 0), title, artist, dur)
            if sc > best_sc:
                best, best_sc = lines, sc
        return best
    except Exception:
        pass
    return []


def _lyr_netease(title, artist, dur=0.0):
    """网易云：搜歌用 cloudsearch/pc（老的 search/get/web 已 405），
    取词用 lv=-1 kv=-1（lv=1 很多歌返回空串）"""
    H = {"User-Agent": _UA, "Referer": "https://music.163.com/",
         "Cookie": "appver=2.0.2; os=pc"}
    try:
        r = requests.post("https://music.163.com/api/cloudsearch/pc",
                          data={"s": f"{title} {artist}".strip(),
                                "type": 1, "limit": 10, "offset": 0},
                          headers=H, timeout=8)
        j = r.json()
        songs = (j.get("result") or {}).get("songs") or []
        ranked = []
        for s in songs:
            sid = s.get("id")
            if not sid:
                continue
            nm = s.get("name") or ""
            ar = "/".join((a or {}).get("name") or "" for a in (s.get("ar") or []))
            ln = float(s.get("dt") or 0) / 1000.0
            ranked.append((_score(nm, ar, ln, title, artist, dur), sid))
        ranked.sort(reverse=True)
        for _, sid in ranked[:3]:
            for lv in (-1, 1):
                try:
                    r2 = requests.get("https://music.163.com/api/song/lyric",
                                      params={"id": sid, "lv": lv, "kv": lv, "tv": -1},
                                      headers=H, timeout=8)
                    lrc = ((r2.json().get("lrc") or {}).get("lyric")) or ""
                except Exception:
                    continue
                lines = _strip_credits(_parse_lrc(lrc), title)
                if len(lines) >= 3 and lines[-1][0] > 20:
                    return lines
    except Exception:
        pass
    return []


def _lyrics_lookup(title, artist, dur):
    """真正去网上找：网易云优先，lrclib 兜底。

    为什么网易云优先：中文库最全，而且给的是**简体**歌词；lrclib 对周杰伦这类
    华语老歌经常只有繁体版（「故事的小黃花」），面板上看着别扭。
    """
    tl = _clean(title)
    ar = _clean(artist)
    lines = _lyr_netease(tl, ar, dur)
    if not lines:
        lines = _lyr_lrclib(tl, ar, dur)
    return lines


def _lyrics_bg(key, title, artist, dur):
    """后台线程跑检索，查完写缓存；失败也要记一笔，避免反复重试卡住"""
    try:
        lines = _lyrics_lookup(title, artist, dur)
        _lyr.update(key=key, t=time.time(), lines=lines)
    except Exception:
        _lyr.update(key=key, t=time.time(), lines=[])
    finally:
        with _lock:
            _pending.discard(key)


def lyrics(title, artist, dur=0.0, blocking=False):
    """拿歌词行。

    默认**绝不在请求线程里等网络**：命中缓存立刻返回；没命中就起一个后台线程去搜，
    本次先返回 []（前端显示「这首歌没找到歌词」，几百毫秒后下一轮轮询就有了）。
    旧实现同步请求，一首冷门歌要 10 秒，而前端 1.5s 轮询一次 → 请求堆积、面板卡住。
    """
    title = (title or "").strip()
    artist = (artist or "").strip()
    if not title:
        return []
    dur = float(dur or 0)
    key = (_clean(title).lower(), _clean(artist).lower(), int(round(dur / 5.0)))
    now = time.time()

    if _lyr["key"] == key:
        fresh = _lyr["lines"] and (now - _lyr["t"] < _LYR_TTL)
        miss_ok = (not _lyr["lines"]) and (now - _lyr["t"] < _LYR_MISS_TTL)
        if fresh or miss_ok:
            return _lyr["lines"]

    if blocking:
        lines = _lyrics_lookup(title, artist, dur)
        _lyr.update(key=key, t=time.time(), lines=lines)
        return lines

    with _lock:
        if key not in _pending:
            _pending.add(key)
            threading.Thread(target=_lyrics_bg,
                             args=(key, title, artist, dur),
                             daemon=True).start()
    return _lyr["lines"] if _lyr["key"] == key else []


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


def lyric_lines(title="", artist="", dur=0.0):
    """整首歌的歌词（[[秒, 文本], ...]），给前端做滚动 PV。
    只读缓存：没查好就返回空，前端下一轮再要，绝不在请求线程里等网络。"""
    if not title:
        return []
    lines = lyrics(title, artist, dur or 0.0)
    return [[round(float(t), 2), s] for t, s in lines]


# ============================================================
#  给前端的一体化状态
# ============================================================
def status():
    st = read()
    vol = volume()
    lines = []
    idx, prev_, cur, nxt = -1, "", "", ""
    if st.get("ok") and st.get("title"):
        lines = lyrics(st["title"], st.get("artist", ""), st.get("duration") or 0)
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
        lines = lyrics(s["title"], s.get("artist", ""), s.get("duration") or 0, blocking=True)
        print("lyric lines:", len(lines))
        for t, txt in lines[:8]:
            print(f"  {t:7.2f}  {txt}")
    else:
        print("没有媒体会话（先打开网易云/QQ音乐并放一首歌）")
    st = status()
    print("status:", json.dumps(st, ensure_ascii=False))
