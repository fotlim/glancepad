# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.2.2] - 2026-10-24

修复一个**让安装包整体装不活**的严重问题：1.2.1 及更早的安装包里，随包的 Python
运行时是官方 embeddable 精简版，**不含 tkinter**，而托盘程序和设置窗口全靠它。

### 修复

- **装完双击没反应 / 右键「设置…」打不开**：`launcher.pyw` 一启动就
  `import tkinter`，缺了直接抛 `ModuleNotFoundError`；因为用的是 `pythonw.exe`
  （无控制台），错误只写进 `launcher.err.log`，用户侧看到的就是「双击了没动静」。
  安装包现已补齐 `_tkinter.pyd`、`tk86t.dll`、`tcl86t.dll`、`Lib\tkinter`、`tcl\`，
  并在 `python311._pth` 里补上 `Lib` 与 `Lib\site-packages`——embeddable 的
  `sys.path` 默认不含 `Lib`，光拷文件仍然 `import` 不到。
- **补齐依赖**：运行时现在由 `tools/make_embed_python.ps1` 用同版本 Python 的 pip
  按 `requirements.txt` 装齐（Flask / psutil / requests / Pillow / pystray / winsdk /
  pycaw / comtypes），不再手工拼装，避免漏包。
- **失败时给一句人话**：`launcher.pyw` 现在捕获 `ModuleNotFoundError: tkinter`，
  弹 MessageBox 说明「缺少图形界面组件，请重新下载最新安装包」，而不是静默退出。

### 新增

- `tools/make_embed_python.ps1`：一键组装带 tkinter 的自包含 Python 运行时，
  含版本一致性校验（比对 embeddable 与 donor 的 `python311.dll` FileVersion，
  不一致直接报错——版本错配会让 `_tkinter.pyd` 加载崩溃）与装完自检
  （真开一个 Tk 窗口 + 导入全部依赖）。

## [1.2.1] - 2026-10-24

波浪进度条真的会流动了，今日天气把右半边的空白填满、气温放大一倍。

### 修复

- **波浪进度条播放时纹丝不动**：`muWaveFlow` 那条 CSS `@keyframes` 给 SVG `<g>` 做
  `transform:translateX()`，在 Chromium 里跑不起来——屏上看到的是固定的一条静止波纹
  （排查时还发现无头 Chrome 里 `requestAnimationFrame` 一帧都不走，所以最初几次截图
  对比全都一模一样，误判成「transform 对 SVG 元素无效」）。
  现在改由 `setWaveRun()` 用 `setInterval(70ms)` 逐帧写 `<g transform="translate(..)">`，
  1.55s 走完一个周期；波形左右各多画两个周期，回绕那一刻画面完全重合，接缝看不出来。
  每个 tick 现查节点而不缓存引用——`buildWave()` 会重建整个 SVG，缓存的老节点会变成游离节点。
- **进度边界跟着波浪抖动**：原来用 `stroke-dasharray/dashoffset` 画「已播放」段，
  dash 端点会跟着波形一起平移，前进边界就在抖。改成 `<clipPath>` 里一个宽度由 JS 按百分比写的
  矩形，裁剪框固定不动、波形在框里自己流，两者互不干扰（宽度带 `.16s` 过渡，进度滑得顺）。

### 变更

- **今日天气重排，气温放大到 52px**：原来是「图标 + 气温 + 状况」一行、再把
  城市/湿度/风/AQI 横着铺一条，右半边一大片空的。现在整块改成两列 grid：
  左边只有图标和当前气温，右边竖排「天气状况 + 高低温」和「城市/湿度/风/AQI」两行；
  竖屏下这 4 项用 `grid` + `space-between` 均匀摊满整条卡片宽度（分隔点「·」隐藏），
  气温 22→52px 并加字重和投影。横屏布局走同一套 grid，未受影响。

## [1.2.0] - 2026-10-24

音乐卡片从「三行歌词 + 细进度条」升级成**整首歌词 PV + Material 3 波浪进度条**，
同时把七天预报改成竖排、歌词检索整体重写（网易云优先、异步不阻塞）。

### 新增

- **动态歌词 PV**：音乐卡片中部的三行滚动歌词换成**整首歌的滚动文字 PV**。
  全部歌词组成一条纵向轨道，当前句放大到 22px、加粗、居中，并带 `pvFill` 的**逐字高亮**扫过动画
  （`background-clip:text` + `background-position` 位移，纯 CSS，无 canvas）；
  上下用渐隐遮罩 `mask-image` 收边，换句时整条轨道平滑滚到中央（含 470ms 二次校正，
  因为行高有字号过渡，一次量必然偏）。歌词只拉一次（`/api/lyrics`），
  前端按播放进度本地外推换句，100ms 一个 tick，比 1.5s 轮询跟拍得多。
- **波浪进度条（Material 3 Wavy）**：原 `.mu-bar` 细条换成 SVG 正弦带 ——
  二次贝塞尔交替上下生成波形，用 `stroke-dasharray/dashoffset` 做进度裁剪，
  走过的一段是粉→紫渐变并带 `drop-shadow` 呼吸发光，没走到的部分是低对比灰白底线；
  播放时波浪整体左右缓慢漂移。振幅按**设计稿缩放到 0.398 倍**反推给到 9px（屏上才 3.6px），
  高度 26px —— 小振幅在副屏上根本看不出起伏。

### 变更

- **七天预报改竖排**：原来是「七行、行内左中右（日期 / 图标 / 温度）」，
  竖屏下改成**七列并排、每列内部自上而下堆叠**（日期名 → 日期 → 图标 → 高温 → 低温），
  列间用 1px 竖分割线，`display:grid; grid-template-columns:repeat(7,minmax(0,1fr))`。
  日期字号 10→13px 加粗并加 `text-shadow` 压住照片背景，图标 12→24px，温度 13→15px。
  改版后七天预报把天气区下半的空间用满（`fc:scrollH` 113 < 容器 130，无裁切）。
- **歌词检索重写**（`sensors/media.py`）：搜索接口从已废弃的 `api/search/get/web`（恒返回
  `code:405 操作频繁`）换成 `api/cloudsearch/pc`；取词参数从 `lv=1` 改为 `lv=-1` 优先
  （`lv=1` 对大量歌曲只给空串）；新增 `_score()` 按歌名相似度 + 歌手 + **时长差**打分，
  排掉翻唱 / 伴奏 / 钢琴版（`起风了 (伴奏)` 与 `起风了` 同分时靠时长分辨）。
  **网易云优先、lrclib 兜底**（中文库更全且给简体，lrclib 的周杰伦常只有繁体）。
- **歌词检索不再阻塞请求线程**：`lyrics()` 默认只读缓存，未命中就起后台线程查、本次返回空，
  下一轮轮询自然就有；缓存命中 30 分钟、未命中 3 分钟，同曲并发有 `_pending` 去重。
  旧实现同步等网络（冷门歌 ~10s），会把 1.5s 一次的 `/api/media` 轮询堆死。

### 修复

- **第三方播放器歌词永远卡在前奏**：Foliamajor 等播放器注册了 SMTC 但 `position` 永远停在
  `0.0x` 秒。现在用 `last_updated_time` 做**进度外推**（`position = raw + delta`，
  上限 `duration+2`），暂停时沿用记住的位置不跳回 0。
- **波浪进度条只有灰白底线、走过的地方不亮**：`linearGradient` 的 `stop-color` 里写了
  `var(--pink,#ff7a9c)` —— **SVG 的 stop-color 属性不认 CSS 变量**（不是降级到 fallback，
  是整段渐变失效），填充色被算成无效值。改成字面色值 `#ff7a9c / #c792ff / #9a7dff` 后正常。
- **歌词 PV 当前句不居中**：`pvBase`（播放进度基准）原本在 `renderPv()` 尾部才更新，
  而歌词常常是几秒后才由后台线程补齐的，那时 `pvIdx` 一直是 -1、没有高亮行；改为在函数开头就更新。

## [1.1.3] - 2026-10-23

网络卡片三处可读性修复，并把右半空间重新分配成「天气 + 邮箱快捷方式」。

### 新增

- **邮箱快捷方式**：网络卡右半下半新增四个邮箱按钮 —— **163 / 126 / Outlook / Gmail**，
  点一下在新标签打开对应网页版邮箱（弹窗被拦时退化为当前页跳转）。
  地址列表在 `static/app.js` 的 `MAILBOXES` 数组里，改哪家、改顺序都只动这一处。
- 布局上把右半拆成 `.wx-weather`（天气）+ `.mail-box`（邮箱）两块，
  竖屏用 `grid-template-rows:minmax(0,1fr) 76px`、横屏用 `minmax(0,1fr) 52px`，天气区自动吃掉剩余高度。

### 变更

- **天气区高度压一半**：右半让出一半给邮箱后，天气的图标 / 温度 / 文字 / 七天预报逐级降字号，
  七天预报 7 行在竖屏与横屏下都完整显示（`fc:lastBottom` 竖屏 8、横屏 2，均未裁切）。

### 修复

- **WiFi 列表「文字不对版」**：`.kv-list li` 从 flex 改网格 `grid-template-columns:5.8em minmax(0,1fr)`，
  数值由 `text-align:right` 改为 `text-align:left` —— 之前标签宽度随文字变、数值又右对齐，
  短值（WLAN）会飘到最右，看起来像「值配错了标签」。
- **竖屏收发速率 / 信号强度与折线图重叠**：`.net-chart-wrap` 从列表下方移出，与 `.kv-list` 左右并排（`minmax(0,1.15fr) minmax(0,1fr)`）。
- **第 8 行「信号强度」被裁**：并排后 WiFi 列表获得独立列宽，8 行全部完整可见。

> 排查记录：邮箱按钮曾连续多轮被判「不可见」，实际是 `?static=1` 静态模式下
> `getBoundingClientRect()` 与 `.canvas{transform:scale()}` 的实际绘制位置有 60~70px 偏差，
> 按 DOM 坐标裁图当然裁不到。最终用「临时背景色探针 + 全图品牌色扫描」取真实 bbox 才定位清楚。
> 判断元素是否可见时别信 DOM 矩形。

## [1.1.4] - 2026-10-23

使用体验修复：邮箱不再顶掉面板、从托盘启动不再误报失败、七天预报不再出现 `--°`。
这一版同时提供 **exe 安装包**（`GlancePad-Setup-1.1.4.exe`）。

### 新增

- **exe 安装程序**：基于 Inno Setup，安装向导可**自定义安装目录**，并单独勾选
  **开机自启** / **开始菜单快捷方式** / **桌面快捷方式**。
  安装包自带 Python 运行时与全部依赖，目标机器无需预装 Python。
  静默安装：`GlancePad-Setup.exe /VERYSILENT /DIR=D:\GlancePad`。

### 修复

- **点邮箱后回不到监控画面**：旧版用 `window.open` 打开，新标签会开在**同一个 kiosk 窗口**里，
  而且没有地址栏、没有关闭按钮 —— 面板就被邮箱页顶掉了。
  现在改为 `fetch("/api/open-url")`，后端白名单校验后用 `os.startfile` 交给**系统默认浏览器**
  （主屏普通窗口），副屏 kiosk 原样留在监控画面。
  另在托盘菜单加了一项「重新显示监控画面」，万一面板被顶掉可一键恢复。
- **托盘「启动监控」误判为失败**：`launcher.pyw` 的 `server_alive()` 原来探 `/api/snapshot` 并只给 1.5 秒超时，
  而这个接口要串行采 AIDA64 / LHM / psutil / nvidia-smi / 天气 / 一言，实测 4.5~5.6 秒，
  必然超时 → 服务明明在跑也一律判「没起来」，点启动永远弹「启动超时」。
  改为探 `/api/theme`、`/api/apps/version` 这类轻量端点（20~40ms），超时放宽到 3 秒。
  同理 `start_monitor.ps1` 的 `Test-Server` 也换掉了重端点，并把「端口在监听但 HTTP 探活失败」从
  「重启服务」改成「等它就绪」，避免误杀正在启动的进程。
- **从托盘启动时任务栏闪一下 Chrome 占位**：原来只在置顶阶段 400ms 轮一次 `WS_EX_TOOLWINDOW`，
  现在窗口一出来就 60ms × 30 次抢着补样式，几乎看不到闪烁。
- **七天预报最高温全显示 `--°`**：中国天气网的节点是 `<span>15℃</span>`（度符号在数字后面），
  原正则要求 `</span>` 紧跟数字，永远匹配不到；另外「今天」那行不在列表里、要从 `cityDZ` 补。

## [1.1.2] - 2026-10-23

自定义背景图下的可读性大改，顶部时钟与一言继续放大。

### 新增

- **自动对比度**：导入背景图后，后端用 32×32 缩略图算背景亮度（整体 / 上中下三段 / 标准差），
  前端据此自动调节——按区块加深顶部与底部的暗角遮罩、按整体亮度压暗卡片玻璃层、
  提亮次要文字色（`--text-dim` / `--text-mute`）。照片类背景不会再出现「白字糊在亮背景上」。
  设置窗口「背景」页新增开关「根据背景亮度自动增强文字对比度」，写入 `settings.json` 的 `bg_contrast`（`auto` / `off`）。
- **新路由字段**：`/api/theme` 返回 `bg_contrast` 与 `bg_stats`（`lum` / `top` / `mid` / `bot` / `std`，0~1）。

### 变更

- **时钟继续放大**：`.clock` 由 `clamp(52px,8.6vh,78px)` 改为 `clamp(64px,14.2vh,130px)`，
  把 head 行（右侧日期 + 历史列表撑起的高度）填满，时钟上方不再留大片死空白。
- **一言放大提亮**：`.quote` 13.5px → **17px**，正文色由 `--text-dim` 改为 `--text` 并加投影，
  来源字号 12px → 13.5px。此前一言与 CPU 卡之间已收紧到 3px 间隙，现在文字更大也更贴。
- **历史上的今天**：`.hist-list` 13px → **14px**，条目色由 `--text-mute` 改为 `--text-dim` 加投影，年份 11px → 12px。

### 修复

- **WiFi 卡「内容重叠」观感**：逐块量过 DOM 矩形（`kv-list` 底 917 / 折线图顶 955，间隔 38px），
  实际并无重叠，真正原因是背景图下的文字对比度不足导致视觉粘连；随本次自动对比度一并解决。

### 已知限制

- 自动对比度只对「自定义图片」背景生效；默认渐变背景本身就是深色，不做调整。

## [1.1.1] - 2026-10-23

天气数据源换成气象局官方站点，顺带把顶部的时间/日期/一言排版重新收了收。

### 新增

- **天气源切换**：设置窗口「天气与位置」页新增「天气源」下拉，三档 —— **自动**（默认，先中国天气网、失败回退 open-meteo）、**中国天气网**（中国气象局站点）、**open-meteo**（国际通用）。写入 `settings.json` 的 `weather_src` 字段。
- **中国天气网主源**：新增 `_cn_weather()`，用城市名查站点码 → `d1.weather.com.cn/sk_2d`（实时温湿度/风向/AQI）→ `www.weather.com.cn/weather/<code>.shtml`（七天预报）→ `d1.weather.com.cn/dingzhi`（气象预警）。带 `Referer` 请求头（缺了会被 403）。
- **AQI 展示**：天气卡湿度/风速后方显示 AQI，按等级着色（≤50 绿 / ≤100 黄 / ≤150 橙 / 更高红）。
- **气象预警展示**：有预警时在天气卡下方显示一行黄色 `⚠ 预警内容`，单行省略。
- **坐标自动校正**：回退 open-meteo 时用城市名反查经纬度，与 `settings.json` 里的坐标偏差超过 1.5° 就自动按城市名校正并写日志。
- **自定义一言源**：设置窗口新增「一言」页签 —— 接口地址（支持任意返回 JSON 的一言 API，字段名兼容 `hitokoto/text/content/sentence`）、hitokoto 分类（`d/i/k/a/b`）、刷新间隔（0~1440 分钟，0 = 每天只换一次）、「立即换一句」按钮。`settings.json` 新增 `quote_api` / `quote_cats` / `quote_refresh` 三键。
- **新路由**：`POST /api/quote/refresh`（立即换一句，并清 settings 缓存让新地址立刻生效）。

### 变更

- **settings.json 默认坐标修正**：原来是杭州的 `30.2741/120.1551`，改成按城市名（长春）对应的 `43.88/125.3228`。
- **顶部排版收紧**：`head` 行的 `grid-template-rows` 从 `fr` 改为 **`auto`**，让顶部高度由内容决定，消掉时钟与卡片之间 67px 的空白；`.clock` 放大到 `clamp(44px,6.2vh,62px)`，日期从 15.5px 放大到 **32px** 并加粗，历史列表从 10.5px/1.34 放宽到 **13px/1.62**，`.head-right` 宽度从 48% 放宽到 56%。
- `settings.json` 新增 `weather_src` 键；改城市/天气源后自动清掉 settings、weather、站点码三个缓存，不用等 10 分钟。

### 修复

- **气象预警永远为空**：`dingzhi` 文件末尾没有分号，原正则 `alarmDZ\d*\s*=\s*(\{.*?\});` 强制要求分号导致匹配失败。改成 `(\{.*\})` 贪婪匹配。
- **天气读数不准**：`city="长春市"` 配的却是杭州坐标，面板一直在报杭州天气。已修坐标并加坐标自动校正。
- **`.wx-alarm` 缺样式**：新增预警行会挤乱天气列，补了 `font-size:10.5px / 橙色 / 单行省略`。

## [1.1.0] - 2026-10-23

外观与信息密度的一次大改版。

### 新增

- **每日一言**：顶部时钟下方显示一言（[hitokoto](https://hitokoto.cn/) 的 `d/i/k` 分类），按天缓存 30 分钟，取不到时用内置句子兜底。
- **历史上的今天**：右上角日期下方列出当天 8 条历史事件（数据来自百度百科 `eventsOnHistory`），年份倒序，标题自动剥掉 HTML 标签。
- **七天天气预报**：网络卡右半新增今日天气（图标 / 温度 / 天气现象 / 高低温 / 城市 / 湿度 / 风速）+ 逐日预报列表（日期 / 图标 / 高低温 / 降水概率 title）。
- **副屏选择**：设置窗口新增「显示」页签，下拉列出 `EnumDisplayMonitors` 枚举到的全部显示器（含分辨率、坐标、主屏标记），可手动指定哪一块当副屏。写入 `settings.json` 的 `screen` 字段（`0` = 自动挑非主屏），下次启动生效。`start_monitor.ps1 -Screen N` 仍可临时覆盖。
- **自定义字体**：设置窗口可导入 `.ttf / .otf / .ttc`，文件复制到程序目录 `fonts/`，通过 `/api/theme.css` 动态下发 `@font-face` + `--font-family`，**只对本程序生效、不装进系统**。
- **自定义背景**：可导入图片到 `backgrounds/`，三种处理方式（高斯模糊 0~60px / 压暗 / 原图）+ 不透明度 10%~100%。
- **新路由**：`/api/theme`、`/api/theme.css`、`/api/files`、`/api/screens`、`/fonts/<name>`、`/backgrounds/<name>`。

### 变更

- **顶部的天气框整体移除**，天气搬到网络卡右半，与 WiFi 信息左右分栏。
- **网络卡改左右分栏**：左半是原来的 WiFi 信息 + 下载速度折线，右半是今日天气 + 七天预报。
- **温度监控瘦身**：去掉 GPU Hotspot / CPU CCD1 / SoC 三项，改为显示 **风扇 1 / 风扇 2 转速**。本机主板 SuperIO 未被 LHM 识别时显示 `-- RPM` 并在 title 里说明原因；将来驱动能读到会自动填上。
- `settings.json` 新增 8 个键（`screen` / `font` / `font_file` / `bg_mode` / `bg_image` / `bg_effect` / `bg_blur` / `bg_opacity`）。老配置**自动补齐缺失键**，用户已有的值不动。
- 横屏布局重排：`grid-template-rows` 改为 `0.62fr 1.46fr 1.30fr 1.46fr 0.46fr`，存储卡从 4 列改 **2 列 × 2 行**，温度/内存卡加高，网络卡加高。

### 修复

- **自定义字体完全不生效**：`@font-face` 被拼进了 `:root{}` 内部，CSS 规则非法被整条丢弃。改为把 `@font-face` 放在 `:root{}` 之前作为顶层规则。
- **横屏 GPU 卡内容溢出 45px**：四路利用率 + 显存条 + 附加信息挤不进 234px 高的卡片。横屏下隐藏 `.gpu-extra`（热点/显存负载/核心电压，这三项与上方统计信息重复），圆环缩到 82px。
- **横屏 WiFi 8 项被压成 22px 高、文字重叠**：`card-wifi` 行高不足。行高从 0.86fr 提到 1.30fr，`.kv-list` 横屏改 2 列排布。
- **横屏存储卡「剩余」数值被右边界截断**：4 列时单卡只有 ~126px 宽。改 2 列 × 2 行，字号压一档。
- **横屏七天预报只显示 5 行**（应为 7 行，最后两行被裁）：`.fc-list` 固定行高 + 字号压一档，实测 `scrollHeight 131 → 110`、`lastBottom -47 → 0`。
- **WiFi 显示「未检测到 WiFi 连接」**（连接的网正常时）：Windows 把 SSID 当位置信息，定位服务总开关关闭时 `netsh` 直接 `error 5: Access is denied`。文档里给了开启方法；同时新增 `Get-NetConnectionProfile` 兜底（定位关着也能显示 SSID 与网卡，只缺信号强度与信道）。
- **中文 `netsh` 字段名不兼容**：`AP BSSID`（原来只匹配 `BSSID`）、`波段`（原来只匹配 `频带`）导致 BSSID 与频段恒为空。

### 已知限制

- 风扇转速依赖主板 SuperIO 被 LibreHardwareMonitor 识别，部分主板（如开发测试用的 `X5`）读不到，显示 `-- RPM`。
- 副屏选择需要**重启监控**才生效（kiosk 窗口创建时就要确定位置与尺寸）。

## [1.0.3] - 2026-10-22

### 修复

- **CPU / GPU 大圆环百分比偏高**：`.ring-text` 原本是 `flex-wrap:wrap`，数字和 `%` 在 104px 圆环里挤成两行时，多行 flex 的 `align-content` 默认 `stretch`，两行各摊一半高度，数字那行被顶到上半区，视觉上比环心高 20 多 px。改为 `flex-wrap:nowrap` 让「数字 + %」保持单行，`align-items:center` 整体居中；行内小标签（占用 / 频率）从原来的 `width:100%` 折行改成 `position:absolute; bottom:9px` 压在环底，不再参与主轴布局。`%` 用 `translateY(5px)` 微沉到数字基线。headless 实测（1080×1920 与 864×1536 两种视口）：环 810.5–914.5（中心 862.5），数字+% 行 849–876（中心 862.5），**偏移 0**（修复前约 -24px）。

## [1.0.2] - 2026-10-22

### 修复

- **内存卡百分比跑到圆环上沿**：v1.0.1 用 `align-items:baseline` 修 `%` 折行，但 flex 的基线对齐会把整行贴到 cross-start，导致「75%」整块顶在 70px 圆环的上边缘，看着像压在环上、明显不居中。改为 `align-items:center` + `justify-content:center`，数字与 `%` 整体垂直居中，`%` 再用 `translateY(3px)` 微沉到数字基线位置。实测（副屏实拍，2x DPR）：圆环竖直范围 1160–1239px、中心 1199.5，文字竖直范围 1190–1209、中心 1199.5，**偏移 0**（修复前偏上约 25 CSS px）。

## [1.0.1] - 2026-10-22

安装体验与显示细节修复。

### 新增

- **安装脚本 `install.ps1`**：一键装到指定目录（默认 `D:\GlancePad`），自动创建桌面快捷方式「副屏监控」、询问是否开机自启，并在安装前检查 Python 依赖 / Chrome / LHM 桥接 / PawnIO 驱动，缺什么给出具体补装命令。支持 `-Dir` / `-Autostart ask|yes|no` / `-NoPrompt` / `-NoDesktop` / `-ResetSettings` / `-Uninstall`。
  - **升级默认保留 `settings.json`**（Dock 项和天气城市不会丢），只有显式 `-ResetSettings` 才会用安装包里的覆盖。
  - **覆盖安装前会自动停掉旧版本**：否则运行中的 `lhm_bridge.exe` 锁着 `tools\LHM\*.dll`，复制会抛 `IOException` 让安装半途而废。单个文件被占用时也只提示、不中断。
- **可选开机自启**：只写当前用户的 `HKCU\...\Run\GlancePad`，不需要管理员权限。三种开关入口 —— 安装脚本参数、托盘右键「开机自启 / 取消开机自启」、设置窗口新增的「系统」标签页（含勾选框与「创建桌面快捷方式」按钮）。
- **应用图标** `static/glancepad.ico`（16~256 多尺寸，青绿三角 + 蓝点，与托盘图标一致），桌面快捷方式与托盘共用。
- **VERSION 文件**。

### 修复

- **任务栏占位**：Chrome kiosk 窗口会在任务栏留下图标和缩略图预览。`keep_top.ps1` 现在每 2 秒给窗口补 `WS_EX_TOOLWINDOW` 并清掉 `WS_EX_APPWINDOW`；`start_monitor.ps1` 的置顶步骤也会做一次。窗口重建后自动恢复，无需手动处理。
- **横屏布局**（m03305）：`body.landscape .canvas` 的 `grid-template-rows` 原先让 `row2` 只占 1.14fr，内存/温度子卡仅约 90px 高，70px 圆环直接压住底部「共 15.9 GB / 硬件保留 / 插槽」。改为 `0.38fr 1.54fr 0.90fr 1.86fr 0.46fr`，`row2` 拿到 1.86fr（子卡约 111px、播放器 231px），同时保证 GPU 卡（4 条 ubar + 显存条）内容不再溢出 17px。
- **内存卡百分比 `%` 折行**：`.ring-text` 默认 `flex-wrap: wrap`，70px 的 `.ring-xs` 里 27px 数字 + `%` 会折成两行，`%` 掉到数字下方像误放的标点。新增 `.ring-xs .ring-text{flex-wrap:nowrap;align-items:baseline}` 并单独调小字号（数字 22px / `%` 11px），现在同基线并列。
- **安装脚本依赖检查**：`python -c @"..."@` 的 here-string 会被 PowerShell 二次解析，抛 `NativeCommandError` 假错误。改为写临时 `.py` 文件再执行。

### 优化

- **顶部时钟放大加粗**（m03330）：`.clock` 改为 `clamp(44px, 6.4vh, 64px)` + `font-weight:700` + 字距 1.5px + 柔和蓝色外发光；日期 12px → 14.5px。
- **右上角天气放大**：天气图标 24 → 30px、主温度 26 → 34px（加粗）、天气现象 13 → 15.5px、副信息 10.5 → 12px。
- 安装脚本默认不复制 `assets/` 调试截图目录与 `__pycache__` / `.chrome-profile` / `*.log` / `*.pid`，成品更干净（约 118 个文件 / 17 MB，其中 LHM 桥 11.7 MB）。

## [1.0.0] - 2026-10-22

首个公开版本。

### 新增

- **暗色毛玻璃监控面板**：竖屏优先（设计稿 864×1536），横屏自动切三列布局，任意分辨率等比缩放不变形。
- **三层传感器数据源**，按优先级自动降级：
  1. AIDA64（共享内存 / 注册表 `SensorValues` 双通道）
  2. LibreHardwareMonitor（常驻桥接，HTTP 8771，按 sensor id 精确映射）
  3. psutil / nvidia-smi（无依赖兜底）
- **CPU 卡片**：占用率圆环、温度、频率、电压、功耗、用户/系统/IO 三段占比、16 核逐核占用小柱。
- **GPU 卡片**：占用率圆环、温度、频率、功耗、显存条（已用/总量/占比/显存频率）、热点温度、显存负载、核心电压、核心/显存/编码/解码四路利用率。
- **性能监控折线**：CPU / GPU / 内存 三条 60 点滚动曲线。
- **网络卡片**：SSID、适配器、连接类型（802.11ac）、频段与信道、IPv4、IPv6、收发速率、信号强度柱、**下载速度对数刻度折线**（相对线性刻度在低带宽下可读性更好）。
- **天气**：open-meteo 免费接口，无需 API key，显示温度/体感/天气现象/最高最低/湿度/风速。
- **内存卡片**：已用/可用/已缓存/已提交/分页池/非分页，以及内存条规格（型号、容量、频率、SODIMM/DIMM、插槽占用、硬件保留）。
- **温度监控**：CPU、GPU、GPU 热点、CPU CCD1、SoC 等（有 AIDA64 时字段更多）。
- **存储卡片**：每块盘独立卡片，显示占用条、已用/总量、剩余、**活动时间 %、平均响应 ms、读写速度**，并标注所属物理盘号。
- **快捷启动 Dock**：点击图标启动应用；悬停显示名称气泡（位于图标上方，不被裁剪）；图标、名称、路径、启动参数、顺序**全部可自定义**。
- **音乐播放器卡片**：通过 Windows SMTC 读取当前播放器，支持 播放/暂停/上一首/下一首、系统音量滑杆与静音、歌名与歌手、**三行滚动歌词**（lrclib 优先，网易云兜底）；无播放时显示待机频谱动画。
- **托盘程序兼设置程序**（`launcher.pyw`）：图形界面增删 Dock 应用、调整顺序、改天气城市与经纬度；托盘右键可启动/停止监控。解决 kiosk 全屏没有关闭按钮的问题。
- **一键启动脚本**（`start_monitor.ps1`）：自动探测 Python 与 Chrome、自动选副屏、kiosk 全屏、启动置顶守护与托盘，支持 `-Status` / `-Stop` / `-Quit`。
- **置顶守护**（`keep_top.ps1`）：每 2 秒把监控窗口抢回最前，防止任务管理器 / 桌宠等 Topmost 窗口遮挡。
- **自适应缩放**：`fitScale()` 按设计稿计算 `--s` 写入 CSS 变量，canvas 内所有尺寸用设计稿绝对像素。
- **静态模式** `?static=1`：只抓一帧不连 SSE，供 headless 截图使用。
- **元素尺寸调试** `?debug=1`：把所有卡片与关键元素的 `getBoundingClientRect()` 输出到页面，便于像素级调布局。

### 已知限制

- 仅支持 Windows 10/11。
- **主板温度、磁盘温度、风扇转速**依赖 LHM 能否识别主板的 SuperIO 芯片；部分主板（如测试机的 `X5`）识别不到，相关字段显示 `--`。
- **部分显卡**（如 RTX 2060）硬件本身不提供显存温度与风扇转速，`nvidia-smi` 返回 `N/A`，面板用「显存负载」代替显存温度。
- CPU 电压/功耗需要 **PawnIO** 内核驱动（LHM v0.9.6 起用它作为 ring0 后端），未安装时这些字段为 `--`，其余功能不受影响。
- AIDA64 通道需要手动在「设置 → 外部应用程序」里勾选「允许共享内存」或「允许将监测数据写入注册表」；AIDA64 的界面是 Delphi 自绘，无法可靠自动化，故本版本不做自动开启。
- 歌词中文匹配依赖 lrclib / 网易云曲库，冷门歌曲可能搜不到。

[1.1.3]: https://github.com/fotlim/glancepad/releases/tag/v1.1.3
[1.1.2]: https://github.com/fotlim/glancepad/releases/tag/v1.1.2
[1.1.1]: https://github.com/fotlim/glancepad/releases/tag/v1.1.1
[1.1.0]: https://github.com/fotlim/glancepad/releases/tag/v1.1.0
[1.0.3]: https://github.com/fotlim/glancepad/releases/tag/v1.0.3
[1.0.2]: https://github.com/fotlim/glancepad/releases/tag/v1.0.2
[1.0.1]: https://github.com/fotlim/glancepad/releases/tag/v1.0.1
[1.0.0]: https://github.com/fotlim/glancepad/releases/tag/v1.0.0
