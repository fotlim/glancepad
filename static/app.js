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

  // ---- 背景图自动对比度 ----
  // 服务端 /api/theme 会返回 bg_contrast（auto/off）与 bg_stats：
  //   {lum, top, mid, bot, std}，都是 0~1 的相对亮度（top/mid/bot 是画面三等分的均值）。
  // 背景越亮，浅色文字越糊，所以按亮度自动：加深遮罩、把卡片压暗、把次要文字提亮。
  function applyContrast(t) {
    const root = document.documentElement.style;
    const reset = () => {
      root.removeProperty("--vp-top");
      root.removeProperty("--vp-mid");
      root.removeProperty("--vp-bot");
      root.removeProperty("--card-shade");
      root.removeProperty("--text-dim");
      root.removeProperty("--text-mute");
    };
    const st = t && t.bg_stats;
    const on = (t && t.bg_contrast !== "off") && t.bg_mode === "image" && st;
    if (!on) { reset(); return; }

    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    // 三等分亮度 → 对应遮罩不透明度（越亮遮得越狠）
    const veil = (l) => +clamp(0.10 + l * 0.62, 0.10, 0.74).toFixed(3);
    root.setProperty("--vp-top", String(veil(st.top)));
    root.setProperty("--vp-mid", String(veil(st.mid)));
    root.setProperty("--vp-bot", String(veil(st.bot)));

    // 卡片附加暗度：背景整体偏亮时压暗卡片，让文字和玻璃底拉开
    const shade = clamp((st.lum - 0.30) * 0.62, 0, 0.42);
    root.setProperty("--card-shade", shade.toFixed(3));

    // 次要文字提亮：亮度高时 --text-dim/--text-mute 会贴在亮背景上，按需往白推
    const lift = clamp((st.lum - 0.34) * 1.15, 0, 1);
    if (lift > 0.01) {
      const mix = (from, to) => {
        const f = parseInt(from.slice(1), 16), g = parseInt(to.slice(1), 16);
        const ch = (sh) => {
          const a = (f >> sh) & 255, b = (g >> sh) & 255;
          return Math.round(a + (b - a) * lift).toString(16).padStart(2, "0");
        };
        return "#" + ch(16) + ch(8) + ch(0);
      };
      root.setProperty("--text-dim", mix("#9fb0c0", "#e2ecf5"));
      root.setProperty("--text-mute", mix("#6f8191", "#c2d2e0"));
    } else {
      root.removeProperty("--text-dim");
      root.removeProperty("--text-mute");
    }
  }

  async function applyTheme() {
    let t = null;
    try {
      const r = await fetch("/api/theme", { cache: "no-store" });
      t = await r.json();
    } catch (e) { return; }
    if (!t || !t.ok) return;

    // 没变就别折腾画布（轮询 20 秒一次，避免无谓重绘）
    const statsKey = t.bg_stats ? [t.bg_stats.lum, t.bg_stats.top, t.bg_stats.mid, t.bg_stats.bot].join(",") : "";
    const key = [t.bg_mode, t.bg_image, t.bg_effect, t.bg_blur, t.bg_opacity,
                 t.font, t.font_file, t.bg_contrast, statsKey].join("|");
    if (key === lastThemeKey) return;
    lastThemeKey = key;

    applyContrast(t);

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

  // ---- 波浪进度条（Material 3 Wavy 风格）----
  // 一条正弦波浪当轨道：底下一根暗的走满全程，上面一根亮的用 dash 只画到进度处。
  // 用 getTotalLength + dashoffset 而不是裁剪矩形，这样进度末端刚好停在波峰波谷上，不会切出直角。
  let wvLen = 0, wvW = 0, wvDur = 0;
  function buildWave() {
    const box = $("muWave");
    if (!box) return;
    const w = Math.max(60, Math.round(box.clientWidth || 400));
    // 注意：设计稿 864 宽会被缩到 0.398 倍，所以这里的「振幅 7px」到屏上只有 2.8px，
    // 振幅必须给大些（7）才看得出波浪；高度 26 同理。
    const h = 32, amp = 9, step = 34;
    wvW = w;
    let d = `M0 ${h / 2}`;
    for (let x = 0, up = true; x < w; x += step, up = !up) {
      // 二次贝塞尔交替上下，圆心在 h/2，控制点高度随 up 翻
      d += ` Q${x + step / 2} ${up ? h / 2 - amp : h / 2 + amp} ${Math.min(x + step, w)} ${h / 2}`;
    }
    box.innerHTML =
      `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">` +
      // 注意：SVG 的 stop-color 属性不认 CSS 变量（var(--pink) 会整段失效变黑），
      // 这里必须写字面色值。
      `<defs><linearGradient id="muWaveGrad" x1="0" y1="0" x2="1" y2="0">` +
      `<stop offset="0%" stop-color="#ff7a9c"/>` +
      `<stop offset="55%" stop-color="#c792ff"/>` +
      `<stop offset="100%" stop-color="#9a7dff"/>` +
      `</linearGradient></defs>` +
      `<path class="wv-base" d="${d}" vector-effect="non-scaling-stroke"/>` +
      `<path class="wv-fill" d="${d}" vector-effect="non-scaling-stroke"/>` +
      `</svg>`;
    const f = box.querySelector(".wv-fill");
    if (f) {
      wvLen = f.getTotalLength() || 0;
      f.style.strokeDasharray = `${wvLen} ${wvLen}`;
      f.style.strokeDashoffset = String(wvLen);
    }
  }
  function setWave(pct, playing, total) {
    const box = $("muWave");
    if (!box) return;
    if (total !== undefined) wvDur = total;
    if (!wvLen || Math.round(box.clientWidth || 0) !== wvW) buildWave();
    const f = box.querySelector(".wv-fill");
    if (f && wvLen) {
      const p = Math.max(0, Math.min(1, (pct || 0) / 100));
      f.style.strokeDashoffset = String(wvLen * (1 - p));
    }
    box.classList.toggle("on", !!playing);
    box.classList.toggle("idle", !playing);
  }
  // 100ms 的本地小 tick 顺带把波浪往前推一点，别让进度条一格一格跳
  function tickWave() {
    if (!wvDur || !pvBase.playing) return;   // 暂停/没歌就别白写样式
    setWave((pvNow() / wvDur) * 100, true);
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
    setWave(pct, playing, dur);
    const p1 = $("muPos"); if (p1) p1.textContent = fmtTime(pos);
    const p2 = $("muDur"); if (p2) p2.textContent = fmtTime(dur);

    const play = $("muPlay");
    if (play) play.textContent = playing ? "⏸" : "▶";

    // ---- 歌词：整首滚动 PV（当前句放大 + 卡拉OK 填充）----
    const box = $("muLyricBox");
    const hasSong = !!(m.ok && m.title);
    if (box) box.classList.toggle("idle", !hasSong);
    if (!hasSong) {
      pvKey = "";
      pvLines = [];
      pvIdx = -1;
      const hint = $("muPvHint");
      if (hint) hint.textContent = "♪ 暂时没有播放 · 打开播放器就会显示歌词";
      const track0 = $("muPvTrack");
      if (track0) track0.innerHTML = "";
    } else {
      renderPv(m);
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

  // ================= 歌词 PV =================
  // 整首歌词一次性从 /api/lyrics 取（只读后端缓存，不会等网络），
  // 之后完全靠本地 position 自己算当前句、自己滚动、自己做卡拉OK 填充。
  let pvLines = [];        // [[秒, 文本], ...]
  let pvKey = "";          // 当前歌的 key，换歌才重取
  let pvIdx = -1;          // 当前句索引
  let pvEls = [];          // 对应的 DOM 行
  let pvFetching = false;
  let pvTimer = 0;         // 后台还没查到时的重试定时器
  let pvTries = 0;         // 换歌后最多重取几次，避免空转
  let pvBase = { pos: 0, at: 0, playing: false };  // 进度基准（轮询点 + 本地上次同步时刻）

  function pvSongKey(m) {
    return [m.title || "", m.artist || "", Math.round((m.duration || 0) / 3)].join("|");
  }

  async function fetchPv(wantKey) {
    if (pvFetching) return;
    pvFetching = true;
    try {
      const r = await fetch("/api/lyrics", { cache: "no-store" });
      if (!r.ok) return;
      const d = await r.json();
      // 取的过程中可能换歌了：key 对不上就丢掉，别把上一首的词贴到新歌上
      if (wantKey !== pvKey) return;
      if (d.lines && d.lines.length) {
        pvLines = d.lines;
        buildPvDom();
        // 拿到词立刻把提示藏掉并定位当前句（截图模式下后面不会再轮询了）
        const hp = $("muPvHint");
        if (hp) hp.style.display = "none";
        tickPv();
      }
    } catch (e) { /* 静默 */ }
    finally { pvFetching = false; }
  }

  function buildPvDom() {
    const track = $("muPvTrack");
    if (!track) return;
    track.innerHTML = pvLines
      .map((ln, i) => `<div class="pv-line" data-i="${i}">${esc(ln[1])}</div>`)
      .join("");
    pvEls = Array.from(track.querySelectorAll(".pv-line"));
    pvIdx = -1;
    centerPv(-1, true);
  }

  // 把当前句滚到可视区中间：track 上移「当前行中心 - 舞台中心」
  // idx<0（还没唱到第一句）就把第一句顶到中间偏下，别让整块空着
  let pvCenterT = 0;
  function pvCurY(track) {
    const m = /translate3d\(\s*[-\d.]+px\s*,\s*(-?[\d.]+)px/.exec(track.style.transform || "");
    return m ? -parseFloat(m[1]) : 0;
  }
  function centerPv(idx, instant) {
    const track = $("muPvTrack");
    const stage = $("muPvStage");
    if (!track || !stage) return;
    const el = pvEls[idx >= 0 ? idx : 0];
    if (!el) return;
    const y = el.offsetTop + el.offsetHeight / 2 - stage.clientHeight / 2;
    track.style.transition = instant ? "none" : "";
    track.style.transform = `translate3d(0,${-y}px,0)`;
    if (instant) requestAnimationFrame(() => { track.style.transition = ""; });
    // 字号是 0.4s 过渡的，触发瞬间量到的高度还是旧值 → 等它稳定后按实测矩形再校一次
    clearTimeout(pvCenterT);
    pvCenterT = setTimeout(() => {
      const st2 = $("muPvStage"), tr2 = $("muPvTrack");
      if (!st2 || !tr2 || pvIdx !== idx) return;
      const el2 = pvEls[idx >= 0 ? idx : 0];
      if (!el2) return;
      const r = el2.getBoundingClientRect();
      const s = st2.getBoundingClientRect();
      const d = (r.top + r.height / 2) - (s.top + s.height / 2);
      const y2 = pvCurY(tr2) + d;   // 偏下(d>0)就继续往上滚，偏上就退回来
      tr2.style.transition = "none";
      tr2.style.transform = `translate3d(0,${-y2}px,0)`;
      requestAnimationFrame(() => { tr2.style.transition = ""; });
    }, 470);
  }

  function renderPv(m) {
    const hint = $("muPvHint");
    // 进度基准每轮都先记下：歌词可能是几秒后才由后台线程补齐的，
    // 那时也要能立刻知道「现在唱到第几句」，不能只有早退分支之外的路径才更新
    pvBase = {
      pos: m.position || 0,
      at: performance.now(),
      playing: !!m.playing,
    };
    const key = pvSongKey(m);
    if (key !== pvKey) {
      pvKey = key;
      pvLines = [];
      pvEls = [];
      pvIdx = -1;
      pvTries = 0;
      const track = $("muPvTrack");
      if (track) track.innerHTML = "";
      fetchPv(key);
    }
    // 后端还没吐出歌词：给个轻提示，别留一片黑（后台在查，隔一会再来拿）
    if (!pvLines.length) {
      if (hint) {
        hint.style.display = "block";
        hint.textContent = m.has_lyric ? "♪ 正在取歌词…" : "♪ 这首歌没找到歌词";
      }
      // 后端在后台查（首次约 1~8s），这里限次重取，别一直空转
      if (!pvTimer && pvTries < 8) {
        pvTimer = setTimeout(() => {
          pvTimer = 0;
          pvTries++;
          fetchPv(key);
        }, 2200);
      }
      return;
    }
    if (hint) hint.style.display = "none";
    tickPv();
  }

  // 本地推算当前进度：播放中按经过时间外推，暂停就停在基准上。
  // 后端 1.5s 才给一次，直接用会让整句切换一顿一顿的，这里补上平滑。
  function pvNow() {
    if (!pvBase.playing) return pvBase.pos;
    return pvBase.pos + (performance.now() - pvBase.at) / 1000;
  }

  function updatePvIndex() {
    if (!pvLines.length) return;
    const pos = pvNow();
    let idx = -1;
    for (let i = 0; i < pvLines.length; i++) {
      if (pvLines[i][0] <= pos + 0.12) idx = i; else break;
    }
    if (idx === pvIdx) return;
    const old = pvIdx;
    pvIdx = idx;
    pvEls.forEach((el, i) => {
      const d = idx < 0 ? 9 : Math.abs(i - idx);
      el.classList.toggle("act", i === idx);
      // 逐行写 inline：CSS 里 calc/var 在 background-clip:text 上会打架
      if (i === idx) {
        el.style.fontSize = "";
        el.style.opacity = "";
        el.style.color = "";
      } else {
        const size = Math.max(13, 18 - d * 0.9);
        const op = Math.max(0.12, 0.52 - d * 0.1);
        el.style.fontSize = size.toFixed(1) + "px";
        el.style.opacity = String(op);
        el.style.color = "";
      }
    });
    const cur = pvEls[idx];
    if (cur) {
      // 这句唱多久 = 下一句开始 - 这句开始，夹在 1.6~6s，避免一句挂太久
      const t0 = pvLines[idx][0];
      const t1 = (pvLines[idx + 1] && pvLines[idx + 1][0]) || t0 + 4;
      let d = t1 - t0;
      if (!isFinite(d) || d <= 0) d = 4;
      d = Math.max(1.6, Math.min(6, d));
      cur.style.setProperty("--dur", d + "s");
      cur.style.animation = "none";   // 换句才重放填充动画
      void cur.offsetWidth;
      cur.style.animation = "";
    }
    centerPv(idx, old === -1);
  }

  function tickPv() { updatePvIndex(); tickWave(); }


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

  // ---------------- 邮箱快捷方式（点一下用默认浏览器打开登录页） ----------------
  // 只用登录页 URL，不碰任何账号密码，也不存 cookie —— 在浏览器里自己登录即可。
  const MAILBOXES = [
    { key: "163",     name: "163",     url: "https://mail.163.com/",        color: "#e8443a" },
    { key: "126",     name: "126",     url: "https://mail.126.com/",        color: "#2f7cd6" },
    { key: "outlook", name: "Outlook", url: "https://outlook.live.com/mail/", color: "#0a84ff" },
    { key: "gmail",   name: "Gmail",   url: "https://mail.google.com/",     color: "#ea4335" },
  ];

  function renderMail() {
    const grid = $("mailGrid");
    if (!grid) return;
    grid.innerHTML = MAILBOXES.map((m) => `
      <div class="mail-item" data-url="${m.url}" title="${m.name} 邮箱 · ${m.url}">
        <span class="mail-logo" style="color:${m.color}">${m.name}</span>
        <span class="mail-name">${m.name}</span>
      </div>`).join("");
    [...grid.querySelectorAll(".mail-item")].forEach((el) => {
      el.addEventListener("click", () => {
        const url = el.dataset.url;
        if (!url) return;
        // 关键：面板是 Chrome --kiosk 全屏（没有地址栏/标签栏/关闭按钮）。
        // 用 window.open 会把新标签开到同一个 kiosk 窗口里，把监控画面顶掉，
        // 用户就再也回不来了。所以改成让后端用系统默认浏览器（主屏普通窗口）打开，
        // 副屏面板原样留在原地。
        el.style.opacity = "0.55";
        fetch("/api/open-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        })
          .then((r) => r.json())
          .then((j) => {
            if (!j.ok) console.warn("打开邮箱失败:", j.error);
          })
          .catch((e) => console.warn("打开邮箱失败:", e))
          .finally(() => { setTimeout(() => { el.style.opacity = ""; }, 400); });
      });
    });
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
    const ids = ["canvas", "head", "chart", "netChart", "netPeak", "muEq", "muPvStage",
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
    document.querySelectorAll(".gpu-extra, .vram-box, .vram-bar, .net-chart-wrap, .wifi-body, .kv-list, .mu-lyric, .wx-col, .wx-weather, .mail-box, .mail-head, .mail-grid").forEach((el) => {
      const r = el.getBoundingClientRect();
      out["el:" + el.className.split(" ")[0]] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    });
    const mg = $("mailGrid");
    if (mg) {
      out["mail:count"] = mg.children.length;
      out["mail:scrollH"] = mg.scrollHeight;
      const first = mg.firstElementChild;
      if (first) {
        const fr = first.getBoundingClientRect();
        out["mail:first"] = [Math.round(fr.x), Math.round(fr.y), Math.round(fr.width), Math.round(fr.height)];
      }
    }
    const fcEl = $("fcList");
    if (fcEl) {
      out["fc:items"] = [...fcEl.children].map((li) => {
        const r = li.getBoundingClientRect();
        const d = li.querySelector(".fc-day"), ic = li.querySelector(".fc-ico"), t = li.querySelector(".fc-tmp");
        const rr = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
        return { txt: li.textContent.trim().slice(0, 16), box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], day: rr(d), ico: rr(ic), tmp: rr(t) };
      });
      out["fc:count"] = fcEl.children.length;
      out["fc:scrollH"] = fcEl.scrollHeight;
      const lastKid = fcEl.lastElementChild;
      if (lastKid) out["fc:lastBottom"] = Math.round(fcEl.getBoundingClientRect().bottom - lastKid.getBoundingClientRect().bottom);
    }
    const wvEl = $("muWave");
    if (wvEl) {
      const b = wvEl.getBoundingClientRect();
      const f = wvEl.querySelector(".wv-fill");
      out["wv:box"] = [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)];
      out["wv:len"] = Math.round(wvLen);
      out["wv:dash"] = f ? f.style.strokeDashoffset : null;
      out["wv:on"] = wvEl.classList.contains("on");
    }
    const pvT = $("muPvTrack");
    if (pvT) {
      const st = $("muPvStage");
      out["pv:track"] = [Math.round(pvT.getBoundingClientRect().x), Math.round(pvT.getBoundingClientRect().y),
                         Math.round(pvT.getBoundingClientRect().width), Math.round(pvT.getBoundingClientRect().height)];
      out["pv:stageH"] = st ? st.clientHeight : null;
      out["pv:idx"] = pvIdx;
      out["pv:count"] = pvEls.length;
      out["pv:items"] = pvEls.slice(Math.max(0, pvIdx - 2), pvIdx + 5).map((el, k) => {
        const b = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return {
          i: Math.max(0, pvIdx - 2) + k,
          txt: el.textContent.slice(0, 12),
          box: [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)],
          fs: cs.fontSize, op: cs.opacity, col: cs.color, act: el.classList.contains("act"),
        };
      });
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
    renderMail();
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
    // 本地 100ms 推一次歌词进度，让换句踩在拍子上（后端 1.5s 一次太粗）
    setInterval(() => { if (!document.hidden) tickPv(); }, 100);
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
