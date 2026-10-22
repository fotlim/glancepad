# -*- coding: utf-8 -*-
"""探测：音量控制 API / 网易云歌词 API / SMTC 会话"""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

print("=== pycaw ===")
try:
    import pycaw
    print("pycaw ver:", getattr(pycaw, "__version__", "?"))
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    print("dev type:", type(dev))
    print("dev attrs:", [a for a in dir(dev) if not a.startswith("_")][:40])
    vol = None
    if hasattr(dev, "EndpointVolume"):
        vol = dev.EndpointVolume
        print("EndpointVolume OK")
    else:
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        print("Activate OK")
    print("scalar:", vol.GetMasterVolumeLevelScalar(), "mute:", vol.GetMute())
    vol.SetMasterVolumeLevelScalar(0.35, None)
    print("after set:", vol.GetMasterVolumeLevelScalar())
    vol.SetMasterVolumeLevelScalar(0.5, None)
except Exception as e:
    print("pycaw FAIL", type(e).__name__, e)

print()
print("=== netease ===")
try:
    import requests
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Referer": "https://music.163.com/"}
    q = "晴天 周杰伦"
    r = requests.post("https://music.163.com/api/search/get/web",
                      data={"s": q, "type": 1, "limit": 5, "offset": 0},
                      headers=H, timeout=8)
    print("search status", r.status_code, "len", len(r.content))
    j = json.loads(r.content.decode("utf-8", "replace"))
    songs = (j.get("result") or {}).get("songs") or []
    for s in songs[:5]:
        arts = "/".join(a.get("name", "") for a in (s.get("artists") or []))
        print("  ", s.get("id"), s.get("name"), "|", arts, "|", (s.get("album") or {}).get("name"))
    if songs:
        sid = songs[0]["id"]
        r2 = requests.get("https://music.163.com/api/song/lyric",
                          params={"id": sid, "lv": 1, "kv": 1, "tv": -1},
                          headers=H, timeout=8)
        lj = json.loads(r2.content.decode("utf-8", "replace"))
        lrc = (lj.get("lrc") or {}).get("lyric") or ""
        print("lyric len", len(lrc))
        print("head:", lrc[:160].replace("\n", " / "))
except Exception as e:
    print("netease FAIL", type(e).__name__, e)

print()
print("=== SMTC ===")
try:
    import asyncio
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Mgr)

    async def f():
        m = await Mgr.request_async()
        ss = m.get_sessions()
        print("sessions:", len(ss))
        for s in ss:
            print("  app:", s.source_app_user_model_id)
            try:
                p = await s.try_get_media_properties_async()
                print("   title:", p.title, "| artist:", p.artist, "| album:", p.album_title)
            except Exception as e:
                print("   props fail", e)
            try:
                i = s.get_playback_info()
                print("   status:", i.playback_status)
            except Exception as e:
                print("   info fail", e)
        return m

    mgr = asyncio.run(f())
    print("ctrl api:", hasattr(mgr, "get_current_session"))
except Exception as e:
    print("SMTC FAIL", type(e).__name__, e)
