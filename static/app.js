/* ============================================================
   副屏监控 - 前端逻辑
   SSE 实时接收 + 环形进度 + 双缓冲曲线
   ============================================================ */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ---------------- 环形进度 ----------------
  const CIRC_SM = 2 * Math.PI * 52;   // r=52
  const CIRC_BIG = 2 * Math.PI * 88;  // r=88

  function setRing(el, pct, circ) {
    if (!el) return;
    const p = Math.max(0, Math.min(100, pct || 0));
    el.style.strokeDashoffset = String(circ * (1 - p / 100));
  }

  // ---------------- 颜色（按数值渐变） ----------------
  function heatColor(v, warn, crit) {
    if (v === null || v === undefined) return "#6f8191";
    if (v >= crit) return "#ff6b6b";
    if (v >= warn) return "#ffbe5c";
    return "#4fd1c5";
  }

  // ---------------- 自适应缩放 ----------------
  // 设计稿两套：竖屏 864x1536 / 横屏 1536x864
  // 用 transform:scale 等比缩放，「填满」而不是「塞满」——保证不变形
  const DESIGN = { portrait: [864, 1536], landscape: [1536, 864] };
  let mode = "portrait";

  function pickMode() {
    const w = window.innerWidth, h = window.innerHeight;
    return h >= w ? "portrait" : "landscape";
  }

  function fitScale() {
    mode = pickMode();
    document.body.classList.toggle("portrait", mode === "portrait");
    document.body.classList.toggle("landscape", mode === "landscape");

    const [dw, dh] = DESIGN[mode];
    const w = window.innerWidth, h = window.innerHeight;
    // 留一点安全边距，避免圆角/边框被屏幕边缘切掉
    const pad = 8;
    const s = Math.min((w - pad * 2) / dw, (h - pad * 2) / dh);
    document.documentElement.style.setProperty("--s", String(s));

    // 画布物理尺寸变了，重算 canvas 后重绘
    requestAnimationFrame(() => {
      fitCanvas();
      drawChart();
      fitNetCanvas();
      drawNetChart();
    });
  }

  // ---------------- 时钟 ----------------
  const WEEK = ["星期日","星期一","星期二","星期三","星期四","星期五","星期六"];
  function tickClock() {
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    $("clock").textContent = `${hh}:${mm}`;
    $("date").textContent =
      `${d.getMonth() + 1}月${d.getDate()}日 ${WEEK[d.getDay()]}`;
  }
  tickClock();
  setInterval(tickClock, 1000);

  // ---------------- 外观（字体 / 背景）----------------
  // 字体与背景模糊/透明度走 /api/theme.css（服务端按 settings.json 生成 CSS 变量），
  // 这里只需要按 bg_mode / bg_effect 切 body class，并给背景图设 src。
  const BG_CLASSES = ["bg-image", "bg-dim", "bg-none"];
  let lastThemeKey = "";
  async function applyTheme() {
    let t = null;
    try {
      const r = await fetch("/api/theme", { cache: "no-store" });
      t = await r.json();
    } catch (e) { return; }
    if (!t || !t.ok) return;

    // 没变就别折腾画布（轮询 20 秒一次，避免无谓重绘）
    const key = [t.bg_mode, t.bg_image, t.bg_effect, t.bg_blur, t.bg_opacity,
                 t.font, t.font_file].join("|");
    if (key === lastThemeKey) return;
    lastThemeKey = key;

    BG_CLASSES.forEach((c) => document.body.classList.remove(c));
    const img = $("bgImg");
    const hasImg = !!(t.bg_mode === "image" && t.bg_image);
    if (hasImg) {
      const eff = t.bg_effect || "blur";
      document.body.classList.add(eff === "none" ? "bg-none" : (eff === "dim" ? "bg-dim" : "bg-image"));
      if (img) {
        img.src = "/backgrounds/" + encodeURIComponent(t.bg_image);
        img.onerror = () => { if (img) img.style.display = "none"; };
      }
    } else if (img) {
      img.removeAttribute("src");
    }

    // 字体：/api/theme.css 是动态生成的，改完设置要让页面真的用上新字体。
    // <link> 不会自己重新请求，所以这里把它换成一份即时抓下来、塞进 <style> 的副本。
    try {
      const cr = await fetch("/api/theme.css", { cache: "no-store" });
      const css = await cr.text();
      let st = document.getElementById("themeCssLive");
      if (!st) {
        st = document.createElement("style");
        st.id = "themeCssLive";
        document.head.appendChild(st);
      }
      st.textContent = css;
      const lk = document.querySelector('link[href="/api/theme.css"]');
      if (lk) lk.disabled = true;   // 避免两份规则打架
    } catch (e) { /* 取不到就沿用 <link> 的默认样式 */ }

    setTimeout(() => { fitScale(); drawChart(); fitNetCanvas(); drawNetChart(); }, 60);
  }

  function fmtUptime(sec) {    if (!sec) return "已运行 --";
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return `已运行 ${d} 天 ${h} 小时`;
    if (h > 0) return `已运行 ${h} 小时 ${m} 分`;
    return `已运行 ${m} 分`;
  }

  // ---------------- 曲线 ----------------
  const N = 60;                                  // 保留 60 个采样点
  const hist = { cpu: [], gpu: [], mem: [] };
  const cv = $("chart");
  const ctx = cv.getContext("2d");
  let dpr = 1;

  function fitCanvas() {
    // 用 offsetWidth（布局尺寸，不受 transform 缩放影响）
    // 背板尺寸 = 布局尺寸 × dpr × 缩放比，保证放大后依然清晰
    const s = parseFloat(getComputedStyle(document.documentElement)
                          .getPropertyValue("--s")) || 1;
    dpr = (window.devicePixelRatio || 1) * s;
    const w = cv.offsetWidth, h = cv.offsetHeight;
    cv.width = Math.max(1, Math.round(w * dpr));
    cv.height = Math.max(1, Math.round(h * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function push(arr, v) {
    arr.push(typeof v === "number" ? Math.max(0, Math.min(100, v)) : 0);
    if (arr.length > N) arr.shift();
  }

  function drawSeries(arr, W, H, color, fill, lineW) {
    if (arr.length < 2) return;
    // 铺满整宽：点数不足 60 时也拉伸到全宽，避免左侧留一大块空白
    const stepX = W / (arr.length - 1);
    const x0 = 0;

    ctx.beginPath();
    arr.forEach((v, i) => {
      const x = x0 + i * stepX;
      const y = H - (v / 100) * (H - 6) - 3;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = lineW;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    if (!fill) return;
    ctx.lineTo(x0 + (arr.length - 1) * stepX, H);
    ctx.lineTo(x0, H);
    ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, fill);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fill();
  }

  function drawChart() {
    // 用布局尺寸（offsetWidth/Height），不受 transform 缩放影响
    const W = cv.offsetWidth, H = cv.offsetHeight;
    if (W <= 0 || H <= 0) return;

    ctx.clearRect(0, 0, W, H);

    // 网格
    ctx.strokeStyle = "rgba(255,255,255,0.055)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 5]);
    for (let i = 1; i <= 4; i++) {
      const y = (H / 4) * i;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // 系列：先画填充，线叠上面
    drawSeries(hist.mem, W, H, "rgba(154,125,255,0.95)", "rgba(154,125,255,0.14)", 1.4);
    drawSeries(hist.gpu, W, H, "rgba(91,157,255,0.95)", "rgba(91,157,255,0.16)", 1.6);
    drawSeries(hist.cpu, W, H, "rgba(79,209,197,0.98)", "rgba(79,209,197,0.20)", 1.8);

    // 当前点
    const last = (arr, color) => {
      if (!arr.length) return;
      const v = arr[arr.length - 1];
      const x = W;
      const y = H - (v / 100) * (H - 6) - 3;
      ctx.beginPath();
      ctx.arc(x - 2, y, 2.6, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0;
    };
    last(hist.cpu, "#4fd1c5");
    last(hist.gpu, "#5b9dff");
    last(hist.mem, "#9a7dff");
  }

  // ---------------- 下载速度折线 ----------------
  const NET_N = 60;
  const netHist = [];                 // 最近 60 个下载速度（Mbps）
  const ncv = $("netChart");
  const nctx = ncv ? ncv.getContext("2d") : null;
  let netDpr = 1;
  let netPeak = 0;

  function fitNetCanvas() {
    if (!ncv) return;
    const s = parseFloat(getComputedStyle(document.documentElement)
                          .getPropertyValue("--s")) || 1;
    netDpr = (window.devicePixelRatio || 1) * s;
    const w = ncv.offsetWidth, h = ncv.offsetHeight;
    if (w <= 0 || h <= 0) return;
    ncv.width = Math.max(1, Math.round(w * netDpr));
    ncv.height = Math.max(1, Math.round(h * netDpr));
    nctx.setTransform(netDpr, 0, 0, netDpr, 0, 0);
  }

  function drawNetChart() {
    if (!nctx) return;
    const W = ncv.offsetWidth, H = ncv.offsetHeight;
    if (W <= 0 || H <= 0) return;
    nctx.clearRect(0, 0, W, H);

    // 动态上限：家用带宽常态 0.1~5 Mbps，线性刻度会把曲线压成一条贴底的细线。
    // 这里用 1) 友好取整上限 2) 幂次(0.62)放大低值，让波动看得清。
    const rawMax = Math.max(0.6, ...netHist);
    const niceMax = (() => {
      const m = rawMax * 1.08;
      const p = Math.pow(10, Math.floor(Math.log10(m)));
      const n = m / p;
      return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
    })();
    // 对数刻度：宽带空闲时下载量常只有峰值几十分之一，线性刻度会把曲线压成贴底细线。
    // 用 log10 映射能把「安静时的细小波动」也画出来，观感更像真实的网速仪表。
    const LN_FLOOR = 0.2;   // Mbps，对数底数（低于它的变化被压平，避免零点噪声乱跳）
    const LOG_BASE = Math.log10(1 + niceMax / LN_FLOOR);
    const norm = (v) => (LOG_BASE > 0
      ? Math.min(1, Math.max(0, Math.log10(1 + Math.max(0, v) / LN_FLOOR) / LOG_BASE))
      : 0);

    // 网格（3 条）
    nctx.strokeStyle = "rgba(255,255,255,0.05)";
    nctx.lineWidth = 1;
    nctx.setLineDash([2, 4]);
    for (let i = 1; i <= 3; i++) {
      const y = (H / 3) * i - 1;
      nctx.beginPath();
      nctx.moveTo(0, y);
      nctx.lineTo(W, y);
      nctx.stroke();
    }
    nctx.setLineDash([]);

    if (netHist.length < 2) return;

    const stepX = W / (NET_N - 1);
    const x0 = W - (netHist.length - 1) * stepX;
    const yOf = (v) => H - 2 - norm(v) * (H - 6);

    // 面积
    nctx.beginPath();
    netHist.forEach((v, i) => {
      const x = x0 + i * stepX;
      const y = yOf(v);
      i === 0 ? nctx.moveTo(x, y) : nctx.lineTo(x, y);
    });
    nctx.lineTo(x0 + (netHist.length - 1) * stepX, H);
    nctx.lineTo(x0, H);
    nctx.closePath();
    const g = nctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "rgba(91,157,255,0.42)");
    g.addColorStop(0.6, "rgba(91,157,255,0.14)");
    g.addColorStop(1, "rgba(91,157,255,0.02)");
    nctx.fillStyle = g;
    nctx.fill();

    // 折线
    nctx.beginPath();
    netHist.forEach((v, i) => {
      const x = x0 + i * stepX;
      const y = yOf(v);
      i === 0 ? nctx.moveTo(x, y) : nctx.lineTo(x, y);
    });
    nctx.strokeStyle = "rgba(125,190,255,0.98)";
    nctx.lineWidth = 1.7;
    nctx.lineJoin = "round";
    nctx.lineCap = "round";
    nctx.stroke();

    // 末端亮点
    const lv = netHist[netHist.length - 1];
    nctx.beginPath();
    nctx.arc(W - 1, yOf(lv), 2.6, 0, Math.PI * 2);
    nctx.fillStyle = "#7dbeff";
    nctx.shadowColor = "#7dbeff";
    nctx.shadowBlur = 8;
    nctx.fill();
    nctx.shadowBlur = 0;

    // 峰值
    const pk = $("netPeak");
    if (pk) pk.textContent = netPeak.toFixed(1);
  }

  function pushNet(v) {
    const x = typeof v === "number" && isFinite(v) ? Math.max(0, v) : 0;
    netHist.push(x);
    if (netHist.length > NET_N) netHist.shift();
    if (x > netPeak) netPeak = x;
  }

  // ---------------- 音乐播放器 ----------------
  const fmtTime = (sec) => {
    if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  let mediaVolumeLock = 0;   // 拖动音量条时短暂锁住，不让轮询覆盖

  // 播放器待机频谱（纯装饰：没在播时用一条缓慢起伏的波形填满歌词区）
  const EQ_N = 32;
  let eqBars = [];
  function buildEq() {
    const box = $("muEq");
    if (!box || eqBars.length) return;
    box.innerHTML = Array.from({ length: EQ_N }, () => "<i></i>").join("");
    eqBars = Array.from(box.querySelectorAll("i"));
  }
  let eqTimer = 0;
  function tickEq() {
    if (!eqBars.length) return;
    const box = $("muLyricBox");
    const idle = box && box.classList.contains("idle");
    if (!idle) return;
    const t = Date.now() / 1000;
    eqBars.forEach((el, i) => {
      const v = 0.82
        + 0.11 * Math.sin(t * 0.9 + i * 0.55)
        + 0.05 * Math.sin(t * 2.1 + i * 1.27)
        + 0.02 * Math.sin(t * 4.7 + i * 2.3);
      el.style.height = Math.max(56, Math.min(100, v * 100)) + "%";
    });
  }
  function startEq() {
    buildEq();
    if (eqTimer) return;
    eqTimer = setInterval(tickEq, 220);
    tickEq();
  }

  function renderMedia(m) {
    if (!m) return;
    const badge = $("muBadge");
    const app = $("muApp");
    if (app) app.textContent = m.app ? m.app : (m.ok ? "媒体" : "--");

    // 有会话但没在放
    const playing = !!m.playing;
    if (badge) {
      badge.textContent = !m.ok ? "无播放器" : (playing ? "播放中" : "已暂停");
      badge.classList.toggle("on", playing);
    }

    const t = $("muTitle"), ar = $("muArtist");
    if (t) t.textContent = m.ok && m.title ? m.title : "未在播放";
    if (ar) {
      ar.textContent = m.ok
        ? [m.artist, m.album].filter(Boolean).join(" · ") || "未知艺术家"
        : "打开网易云 / QQ 音乐放一首歌";
    }

    const pos = m.position || 0, dur = m.duration || 0;
    const pct = dur > 0 ? Math.min(100, (pos / dur) * 100) : 0;
    const prog = $("muProg");
    if (prog) prog.style.width = pct + "%";
    const p1 = $("muPos"); if (p1) p1.textContent = fmtTime(pos);
    const p2 = $("muDur"); if (p2) p2.textContent = fmtTime(dur);

    const play = $("muPlay");
    if (play) play.textContent = playing ? "⏸" : "▶";

    // ---- 歌词：上一句 / 当前句 / 下一句 ----
    const box = $("muLyricBox");
    const ly = $("muLyric"), lyP = $("muLyricPrev"), lyN = $("muLyricNext");
    const hasSong = !!(m.ok && m.title);
    if (box) box.classList.toggle("idle", !hasSong);
    if (!hasSong) {
      if (ly) ly.textContent = "♪ 暂时没有播放 · 打开播放器就会显示歌词";
      if (lyP) lyP.textContent = "";
      if (lyN) lyN.textContent = "";
    } else {
      if (ly) {
        if (m.lyric) {
          ly.textContent = m.lyric;
          ly.style.opacity = "1";
        } else {
          ly.textContent = m.has_lyric ? "♪ 纯音乐 / 前奏" : "♪ 这首歌没找到歌词";
          ly.style.opacity = "0.7";
        }
      }
      if (lyP) lyP.textContent = m.lyric_prev || "";
      if (lyN) lyN.textContent = m.lyric_next || "";
    }

    // 音量（后端可能给 {ok,percent,mute} 也可能直接给数字，统一归一化）
    const volObj = (m.volume && typeof m.volume === "object")
      ? m.volume
      : { ok: m.volume != null, percent: (typeof m.volume === "number" ? m.volume : null), mute: !!m.mute };
    const vnum = Number(volObj.percent);
    const vOk = volObj.ok !== false && isFinite(vnum);
    const v = vOk ? Math.round(vnum) : 50;
    const mute = !!volObj.mute;
    if (Date.now() - mediaVolumeLock > 1200) {
      const sl = $("muVol");
      if (sl && document.activeElement !== sl) sl.value = String(mute ? 0 : v);
      const vv = $("muVolVal");
      if (vv) vv.textContent = vOk ? String(mute ? 0 : v) : "--";
    }
    const mb = $("muMute");
    if (mb) mb.textContent = mute || (vOk && v === 0) ? "🔇" : (!vOk || v < 40 ? "🔉" : "🔊");
  }

  async function mediaControl(action) {
    try {
      await fetch("/api/media/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      pollMedia();
    } catch (e) { /* 忽略 */ }
  }

  async function setVolume(pct) {
    mediaVolumeLock = Date.now();
    try {
      await fetch("/api/media/volume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ percent: pct }),
      });
    } catch (e) { /* 忽略 */ }
  }

  async function pollMedia() {
    try {
      const r = await fetch("/api/media", { cache: "no-store" });
      if (!r.ok) return;
      renderMedia(await r.json());
    } catch (e) { /* 静默 */ }
  }

  function bindMedia() {
    document.querySelectorAll("[data-act]").forEach((el) => {
      el.addEventListener("click", () => mediaControl(el.dataset.act));
    });
    const sl = $("muVol");
    if (sl) {
      let t = 0;
      sl.addEventListener("input", () => {
        mediaVolumeLock = Date.now();
        const vv = $("muVolVal");
        if (vv) vv.textContent = sl.value;
      });
      sl.addEventListener("change", () => setVolume(parseInt(sl.value, 10)));
      // 拖完 1.5 秒才允许轮询覆盖
      sl.addEventListener("pointerup", () => {
        mediaVolumeLock = Date.now();
        setTimeout(() => { mediaVolumeLock = 0; }, 1500);
      });
      void t;
    }
    const mb = $("muMute");
    if (mb) {
      mb.addEventListener("click", async () => {
        const cur = mb.textContent;
        const mute = cur !== "🔇";
        try {
          await fetch("/api/media/volume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mute }),
          });
        } catch (e) { /* 忽略 */ }
        pollMedia();
      });
    }
  }

  // ---------------- 渲染一帧 ----------------
  let lastGood = 0;

  const num = (v, d = 0) => (v === null || v === undefined ? "--" : (typeof v === "number" ? v.toFixed(d) : v));

  // 历史上的今天 / 天气文案都是外部数据，塞进 innerHTML 前必须转义
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  function setBar(el, pct, color) {
    if (!el) return;
    const p = Math.max(0, Math.min(100, pct || 0));
    el.style.width = p + "%";
    if (color) el.style.background = color;
  }

  function render(d) {
    if (!d || d.error) return;
    lastGood = Date.now();

    const cpu = d.cpu || {};
    const gpu = d.gpu || {};
    const mem = d.mem || {};

    // 数据源标记（AIDA64 > LibreHardwareMonitor > psutil/nvidia-smi）
    const S = d.source || {};
    const src = $("cpuSrc");
    if (src) {
      src.textContent = S.aida64 ? "AIDA64" : (S.lhm ? "LHM" : "psutil");
      src.classList.toggle("on", !!(S.aida64 || S.lhm));
      src.title = S.aida64 ? "AIDA64 共享内存/注册表"
        : S.lhm ? (S.pawnio ? "LibreHardwareMonitor（PawnIO 已装）"
                            : "LibreHardwareMonitor（PawnIO 未装）")
        : "系统 API（psutil）";
    }
    const gsrc = $("gpuSrc");
    if (gsrc) {
      gsrc.textContent = S.lhm ? "LHM" : (S.nvidia ? "nvidia-smi" : "WMI");
      gsrc.classList.toggle("on", !!(S.lhm || S.nvidia));
    }

    // ---- 顶部一言（时间下面）----
    const qt = d.quote || {};
    const qText = $("quoteText");
    const qFrom = $("quoteFrom");
    if (qText) {
      qText.textContent = qt.text || "心之所向，素履以往。";
      if (qFrom) {
        const who = (qt.from || qt.who || "").trim();
        qFrom.textContent = who ? `— ${who}` : "";
        qFrom.style.display = who ? "" : "none";
      }
    }

    // ---- 右上：历史上的今天（日期由 tickClock 写）----
    const hs = d.history || {};
    const hl = $("histList");
    if (hl) {
      const items = (hs.items || []).slice(0, 5);
      if (!items.length) {
        hl.innerHTML = '<li class="empty">历史上的今天暂无数据</li>';
      } else {
        hl.innerHTML = items.map((it) =>
          `<li title="${esc(it.title)}"><span class="y">${it.year}</span>` +
          `<span class="t">${esc(it.title)}</span></li>`).join("");
      }
    }

    // ---- 网络卡右侧：今日天气 + 七天预报 ----
    const wx = d.weather || {};
    if (wx.ok) {
      $("wxIcon").textContent = wx.icon || "🌡️";
      $("wxTemp").textContent = num(wx.temp, 0);
      $("wxText").textContent = wx.text || "--";
      $("wxCity").textContent = wx.city || "--";
      $("wxRange").textContent =
        (wx.high != null && wx.low != null) ? `${wx.high}° / ${wx.low}°` : "--";
      $("wxHum").textContent = wx.humidity != null ? `湿度 ${wx.humidity}%` : "湿度 --";
      $("wxWind").textContent = wx.wind != null ? `风 ${wx.wind.toFixed(1)}m/s` : "风 --";
      // AQI（有值才显示，颜色按等级）
      const aq = $("wxAqi"), aqs = $("wxAqiSep");
      if (aq && aqs) {
        if (wx.aqi != null && wx.aqi !== "") {
          const a = Number(wx.aqi);
          aq.textContent = `AQI ${a}`;
          aq.style.display = "";
          aqs.style.display = "";
          aq.style.color = a <= 50 ? "#57d98a" : a <= 100 ? "#ffbe5c"
                        : a <= 150 ? "#ff9f5c" : "#e05c5c";
        } else {
          aq.style.display = "none";
          aqs.style.display = "none";
        }
      }
      // 气象预警（中国天气网会给，open-meteo 没有）
      const al = $("wxAlarm");
      if (al) {
        const txt = (wx.alarm || "").trim();
        if (txt) { al.textContent = "⚠ " + txt; al.style.display = ""; }
        else { al.style.display = "none"; }
      }
      const fc = $("fcList");
      const days = wx.days || [];
      if (fc) {
        fc.innerHTML = days.length
          ? days.map((x) => `<li title="${esc(x.text || "")}${x.pop != null ? " 降水 " + x.pop + "%" : ""}">
              <span class="fc-day">${esc(x.label)}<b>${esc(x.date || "")}</b></span>
              <span class="fc-ico">${x.icon || ""}</span>
              <span class="fc-tmp"><b>${x.max ?? "--"}°</b><i>/</i>${x.min ?? "--"}°</span>
            </li>`).join("")
          : '<li class="empty">暂无预报</li>';
      }
    } else {
      $("wxText").textContent = wx.hint || "天气不可用";
      $("wxIcon").textContent = "🌡️";
      const fc = $("fcList");
      if (fc) fc.innerHTML = '<li class="empty">暂无预报</li>';
    }
    // 顶部时间 / 日期由 tickClock 负责

    // ---- CPU ----
    $("cpuName").textContent = cpu.name || "CPU";
    $("cpuUsage").textContent = (cpu.usage ?? 0).toFixed(0);
    setRing($("cpuRing"), cpu.usage, CIRC_SM);
    $("cpuTemp").textContent = cpu.temp ?? "--";
    $("cpuClock").textContent = cpu.clock ? cpu.clock.toFixed(2) : "--";
    $("cpuVolt").textContent = cpu.volt != null ? cpu.volt.toFixed(3) : "--";
    $("cpuPower").textContent = cpu.power != null ? cpu.power.toFixed(0) : "--";

    // CPU 利用率细分
    const cu = cpu.user ?? 0, cs = cpu.system ?? 0, ci = cpu.iowait ?? 0;
    $("cpuUser").textContent = cu.toFixed(0) + "%";
    $("cpuSys").textContent = cs.toFixed(0) + "%";
    $("cpuIo").textContent = ci.toFixed(0) + "%";
    setBar($("cpuUserBar"), cu, "#4fd1c5");
    setBar($("cpuSysBar"), cs, "#5b9dff");
    setBar($("cpuIoBar"), ci, "#ffbe5c");

    // 每核占用
    const pc = cpu.percpu || [];
    const coresEl = $("cpuCores");
    if (coresEl) {
      if (pc.length && coresEl.children.length !== pc.length) {
        coresEl.innerHTML = pc.map(() => '<span class="c"><i></i></span>').join("");
      }
      pc.forEach((v, i) => {
        const cell = coresEl.children[i];
        if (!cell) return;
        const bar = cell.firstElementChild;
        bar.style.height = Math.max(4, Math.min(100, v)) + "%";
        bar.style.background = heatColor(v, 60, 85);
      });
    }

    // ---- GPU ----
    $("gpuName").textContent = gpu.name || "GPU";
    $("gpuUsage").textContent = (gpu.usage ?? 0).toFixed(0);
    setRing($("gpuRing"), gpu.usage, CIRC_SM);
    $("gpuTemp").textContent = gpu.temp != null ? Math.round(gpu.temp) : "--";
    $("gpuClock").textContent = gpu.clock != null ? Math.round(gpu.clock) : "--";
    $("gpuPower").textContent = gpu.power != null ? gpu.power.toFixed(0) : "--";
    $("gpuVram").textContent = gpu.vram_used_mb != null
      ? (gpu.vram_used_mb / 1024).toFixed(1) : "--";
    $("gpuHot").textContent = gpu.hotspot != null ? Math.round(gpu.hotspot) : "--";

    // GPU 四路利用率
    const gCore = gpu.usage ?? 0;
    const gMemU = gpu.mem_util ?? gpu.mem_load ?? 0;
    const gEnc = gpu.enc_util ?? 0;
    const gDec = gpu.dec_util ?? 0;
    $("gpuCoreU").textContent = gCore.toFixed(0) + "%";
    $("gpuMemU").textContent = gMemU.toFixed(0) + "%";
    $("gpuEncU").textContent = gEnc.toFixed(0) + "%";
    $("gpuDecU").textContent = gDec.toFixed(0) + "%";
    setBar($("gpuCoreBar"), gCore, "#5b9dff");
    setBar($("gpuMemBar"), gMemU, "#9a7dff");
    setBar($("gpuEncBar"), gEnc, "#57d98a");
    setBar($("gpuDecBar"), gDec, "#ffbe5c");

    const gm = $("gpuMemLoad");
    if (gm) gm.textContent = gpu.mem_load != null ? Math.round(gpu.mem_load) : "--";
    const gv = $("gpuVolt");
    if (gv) gv.textContent = gpu.volt != null ? gpu.volt.toFixed(3) : "--";

    // 显存条
    const vu = gpu.vram_used_mb, vt = gpu.vram_total_mb;
    const hasVram = vu != null && vt != null && vt > 0;
    const vpct = hasVram ? Math.min(100, (vu / vt) * 100) : 0;
    const elVu = $("vramUsed"); if (elVu) elVu.textContent = hasVram ? (vu / 1024).toFixed(1) : "--";
    const elVt = $("vramTotal"); if (elVt) elVt.textContent = hasVram ? (vt / 1024).toFixed(0) : "--";
    const elVp = $("vramPct"); if (elVp) elVp.textContent = hasVram ? vpct.toFixed(0) : "--";
    const elVb = $("vramBar"); if (elVb) elVb.style.width = vpct + "%";
    const elVc = $("gpuMemClock");
    if (elVc) elVc.textContent = gpu.mem_clock != null ? Math.round(gpu.mem_clock) : "--";

    // ---- 内存 ----
    $("memUsage").textContent = (mem.usage ?? 0).toFixed(0);
    setRing($("memRing"), mem.usage, CIRC_SM);
    $("memUsed").textContent = mem.used_gb ?? 0;
    $("memAvail").textContent = mem.avail_gb ?? 0;
    $("memCached").textContent = mem.cached_gb ?? "--";
    $("memCommit").textContent = mem.commit_used_gb ?? "--";
    $("memPaged").textContent = mem.pool_paged_gb ?? "--";
    $("memNonPaged").textContent = mem.pool_nonpaged_gb ?? "--";
    $("memTotal").textContent = mem.total_gb ?? 0;
    $("memReserved").textContent = mem.spec?.reserved_gb ?? 0;
    $("memSlots").textContent = mem.spec
      ? `${mem.spec.slots_used}/${mem.spec.slots_total}` : "--";
    const sp = $("memSpec");
    if (sp) {
      sp.textContent = mem.spec
        ? `${mem.spec.form || ""} ${mem.spec.speed_cfg || mem.spec.speed || ""}MHz ×${mem.spec.sticks}`
        : "";
      sp.title = mem.spec ? `型号 ${mem.spec.part || "?"}  插槽 ${mem.spec.bank || ""}` : "";
    }

    // ---- WiFi ----
    const wf = d.wifi || {};
    const net = d.net || {};
    $("netDown").textContent = (net.down ?? 0).toFixed(1);
    $("netUp").textContent = (net.up ?? 0).toFixed(1);
    if (wf.ok) {
      $("wifiDesc").textContent = wf.desc || wf.adapter || "";
      $("wifiAdapter").textContent = wf.adapter || "--";
      $("wifiSsid").textContent = wf.ssid || "--";
      $("wifiRadio").textContent = wf.radio || "--";
      $("wifiBand").textContent =
        [wf.band, wf.channel ? `CH${wf.channel}` : null].filter(Boolean).join(" / ") || "--";
      $("wifiV4").textContent = wf.ipv4 || "--";
      $("wifiV6").textContent = wf.ipv6 || "--";
      $("wifiRate").textContent =
        (wf.rx_mbps != null || wf.tx_mbps != null)
          ? `${wf.rx_mbps != null ? wf.rx_mbps.toFixed(0) : "--"} / ${wf.tx_mbps != null ? wf.tx_mbps.toFixed(0) : "--"} Mbps`
          : "--";
      $("wifiSig").textContent = wf.signal != null ? wf.signal + "%" : "--";
      const bars = $("wifiBars");
      if (bars) {
        const n = wf.bars || 0;
        const cls = wf.signal >= 60 ? "" : (wf.signal >= 35 ? "warn" : "bad");
        [...bars.children].forEach((el, i) => {
          el.className = i < n ? ("on " + cls).trim() : "";
        });
      }
    } else {
      $("wifiSsid").textContent = wf.hint || "未连接";
      ["wifiAdapter", "wifiRadio", "wifiBand", "wifiV4", "wifiV6", "wifiRate", "wifiSig"]
        .forEach((k) => { const e = $(k); if (e) e.textContent = "--"; });
    }

    $("uptime").textContent = fmtUptime(d.uptime);

    // ---- 温度列表（温度 + 两个风扇位）----
    const tl = $("tempList");
    const temps = d.temps || [];
    const fans = d.fans || [];
    const rows = [];
    temps.forEach((t) => {
      const p = Math.max(0, Math.min(100, (t.value / 100) * 100));
      const c = heatColor(t.value, 65, 85);
      rows.push(`<li>
          <span class="t-name">${t.label}</span>
          <span class="t-bar"><i style="width:${p}%;background:${c}"></i></span>
          <span class="t-val" style="color:${c}">${t.value}${t.unit}</span>
        </li>`);
    });
    // 风扇：读不到就显示 -- RPM（这块主板的 SuperIO 不被 LHM 识别）
    fans.forEach((f) => {
      const has = f.value !== null && f.value !== undefined;
      const v = has ? `${Math.round(f.value)}` : "--";
      rows.push(`<li title="${has ? "" : "本机主板 SuperIO 未被 LibreHardwareMonitor 识别，读不到风扇转速"}">
          <span class="t-name">${f.label}</span>
          <span class="t-bar"><i style="width:${has ? Math.min(100, f.value / 30) : 0}%;background:#5b9dff"></i></span>
          <span class="t-val" style="color:${has ? "#9ec8ff" : "var(--text-mute)"}">${v}<i style="font-size:9px;margin-left:1px">RPM</i></span>
        </li>`);
    });
    if (tl) {
      tl.innerHTML = rows.length ? rows.join("") : '<li class="empty">暂无温度源</li>';
    }

    // ---- 存储：每盘一块 ----
    const dl = $("diskList");
    const disks = d.disks || [];
    const ds = $("diskSrc");
    if (ds) ds.textContent = disks.length ? `${disks.length} 块` : "--";
    if (disks.length === 0) {
      dl.innerHTML = '<div class="empty">未检测到磁盘</div>';
    } else {
      dl.innerHTML = disks.map((k) => {
        const c = k.percent >= 90 ? "#ff6b6b"
                : k.percent >= 75 ? "#ffbe5c" : "#57d98a";
        const fmtSpd = (v) => {
          if (v === null || v === undefined) return "--";
          if (v >= 1048576) return (v / 1048576).toFixed(1) + " MB/s";
          if (v >= 1024) return (v / 1024).toFixed(0) + " KB/s";
          return v.toFixed(0) + " B/s";
        };
        return `<div class="disk-item">
          <div class="disk-head">
            <span class="d-name">${k.label}:</span>
            ${k.phys != null ? `<span class="d-phys">盘${k.phys}</span>` : ""}
            <span class="d-pct" style="color:${c}">${k.percent.toFixed(0)}%</span>
          </div>
          <div class="disk-bar"><i style="width:${k.percent}%;background:${c}"></i></div>
          <div class="disk-meta">
            <span><b>${k.used_gb}</b> / ${k.total_gb} GB</span>
            <span>剩余 <b>${k.free_gb}</b> GB</span>
          </div>
          <div class="disk-io">
            <span>活动 <b>${k.busy != null ? k.busy.toFixed(0) + "%" : "--"}</b></span>
            <span>响应 <b>${k.resp_ms != null ? k.resp_ms.toFixed(2) + "ms" : "--"}</b></span>
            <span>读 <b>${fmtSpd(k.read_bps)}</b></span>
            <span>写 <b>${fmtSpd(k.write_bps)}</b></span>
          </div>
        </div>`;
      }).join("");
    }

    // 曲线
    push(hist.cpu, cpu.usage);
    push(hist.gpu, gpu.usage);
    push(hist.mem, mem.usage);
    drawChart();

    // 下载速度折线
    pushNet(net.down);
    drawNetChart();
  }

  // ---------------- 底部 Dock（快捷启动，可从设置程序自定义） ----------------
  let dockApps = [];
  let dockVersion = null;

  function renderDock(apps) {
    dockApps = Array.isArray(apps) ? apps : [];
    const dock = $("dock");
    if (!dock) return;
    if (dockApps.length === 0) {
      dock.innerHTML = '<div class="dock-empty">没有快捷启动项，打开设置程序添加</div>';
      return;
    }
    dock.innerHTML = dockApps.map((a, i) => `
      <div class="dock-item" data-i="${i}" data-name="${a.name || ""}">
        <img src="${a.icon || "/static/icons/chrome.png"}" alt="${a.name || ""}">
      </div>`).join("");

    [...dock.querySelectorAll(".dock-item")].forEach((el) => {
      el.addEventListener("click", async () => {
        const i = parseInt(el.dataset.i, 10);
        const a = dockApps[i];
        if (!a) return;
        // 点击反馈
        el.style.borderColor = "rgba(79,209,197,0.85)";
        setTimeout(() => { el.style.borderColor = ""; }, 500);
        try {
          const r = await fetch("/api/launch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: a.name, path: a.path }),
          });
          const j = await r.json();
          if (!j.ok) console.warn("启动失败", j.error);
        } catch (e) {
          console.warn("启动请求失败", e);
        }
      });
    });
  }

  async function loadApps() {
    try {
      const r = await fetch("/api/apps", { cache: "no-store" });
      const j = await r.json();
      renderDock(j.apps || []);
    } catch (e) {
      renderDock([]);
    }
  }

  // 设置改了之后（设置程序保存 settings.json），立刻刷新 Dock
  window.addEventListener("focus", () => {
    if (!STATIC_MODE) loadApps();
  });
  setInterval(() => {
    if (STATIC_MODE) return;
    if (document.visibilityState !== "visible") return;
    // 每 20 秒对比一次版本号，变了才重渲染，避免闪烁
    fetch("/api/apps/version", { cache: "no-store" })
      .then((r) => r.json())
      .then((j) => {
        if (j.version !== dockVersion) { dockVersion = j.version; loadApps(); }
      })
      .catch(() => {});
  }, 20000);

  // ---------------- SSE ----------------
  let es = null;
  let retryMs = 1000;

  function connect() {
    if (es) { try { es.close(); } catch (e) {} }
    es = new EventSource("/api/stream");

    es.onopen = () => { retryMs = 1000; };

    es.onmessage = (ev) => {
      try {
        render(JSON.parse(ev.data));
      } catch (e) {
        console.warn("解析失败", e);
      }
    };

    es.onerror = () => {
      try { es.close(); } catch (e) {}
      const wait = retryMs;
      retryMs = Math.min(retryMs * 1.8, 15000);
      $("uptime").textContent = `连接断开，${(wait / 1000).toFixed(0)}s 后重试`;
      setTimeout(connect, wait);
    };
  }

  // ---------------- 兜底轮询 ----------------
  // SSE 断了或者后端不支持时，保证画面不会冻死
  setInterval(async () => {
    if (Date.now() - lastGood < 4000) return;
    try {
      const r = await fetch("/api/snapshot", { cache: "no-store" });
      if (r.ok) render(await r.json());
    } catch (e) { /* 静默 */ }
  }, 3000);

  // ---------------- 启动 ----------------
  const QUERY = new URLSearchParams(location.search);
  const STATIC_MODE = QUERY.has("static");   // 截图/调试用：不连 SSE，只抓一帧

  async function staticOnce() {
    try {
      const r = await fetch("/api/snapshot", { cache: "no-store" });
      const d = await r.json();
      // 灌几个假点在曲线里，让图看起来有走势
      const base = d.cpu && d.cpu.usage || 0;
      for (let i = 0; i < 40; i++) {
        push(hist.cpu, base + (Math.random() - 0.5) * 22);
        push(hist.gpu, (d.gpu && d.gpu.usage || 0) + (Math.random() - 0.5) * 20);
        push(hist.mem, (d.mem && d.mem.usage || 0) + (Math.random() - 0.5) * 6);
        pushNet((d.net && d.net.down || 2) * (0.4 + Math.random() * 1.4));
      }
      render(d);
    } catch (e) {
      console.warn("静态模式取数失败", e);
    }
  }

  // ---------------- 调试：把关键元素矩形打进 DOM（?debug=1，配 chrome --dump-dom 用） ----------------
  function dumpRects() {
    const ids = ["canvas", "head", "chart", "netChart", "netPeak", "muEq", "muLyric",
                 "tempList", "diskList", "dock", "uptime", "muBody", "fcList", "wxCol",
                 "clock", "quote", "quoteText", "quoteFrom", "date", "histList"];
    const out = {};
    ids.forEach((id) => {
      const el = $(id);
      if (!el) { out[id] = null; return; }
      const r = el.getBoundingClientRect();
      out[id] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    });
    document.querySelectorAll(".card").forEach((el, i) => {
      const r = el.getBoundingClientRect();
      const cls = el.className.replace("card", "").trim().replace(/\s+/g, ".");
      out["card:" + (cls || i)] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
      // 卡内最后一个子元素的底边，用来判断底部空白
      const kids = [...el.children];
      if (kids.length) {
        const last = kids[kids.length - 1].getBoundingClientRect();
        out["last:" + (cls || i)] = Math.round(r.bottom - last.bottom);
      }
    });
    document.querySelectorAll(".gpu-extra, .vram-box, .vram-bar, .net-chart-wrap, .wifi-body, .kv-list, .mu-lyric").forEach((el) => {
      const r = el.getBoundingClientRect();
      out["el:" + el.className.split(" ")[0]] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    });
    const fcEl = $("fcList");
    if (fcEl) {
      out["fc:count"] = fcEl.children.length;
      out["fc:scrollH"] = fcEl.scrollHeight;
      const lastKid = fcEl.lastElementChild;
      if (lastKid) out["fc:lastBottom"] = Math.round(fcEl.getBoundingClientRect().bottom - lastKid.getBoundingClientRect().bottom);
    }
    const pre = document.createElement("pre");
    pre.id = "dbg";
    pre.textContent = "DBGJSON" + JSON.stringify(out) + "ENDDBG";
    document.body.appendChild(pre);
  }

  function boot() {
    fitScale();
    drawChart();
    fitNetCanvas();
    drawNetChart();
    loadApps();
    bindMedia();
    startEq();
    pollMedia();
    applyTheme();
    // 设置里改了字体/背景，副屏不用刷新就能跟着变
    setInterval(() => { if (!document.hidden) applyTheme(); }, 20000);
    if (STATIC_MODE) {
      staticOnce();
      // 截图模式下也读一次媒体/音量状态
      pollMedia();
      setTimeout(() => { if (QUERY.has("debug")) dumpRects(); }, 900);
      return;
    }
    connect();
    setInterval(pollMedia, 1500);
    if (QUERY.has("debug")) setTimeout(dumpRects, 1500);
  }

  // 窗口尺寸变化：重新算缩放 + 重画画布（防抖）
  window.addEventListener("resize", () => {
    clearTimeout(boot._t);
    boot._t = setTimeout(fitScale, 120);
  });
  // 从副屏拖到主屏这种跨屏 DPI 变化也能跟上
  window.addEventListener("orientationchange", () => setTimeout(fitScale, 150));

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
