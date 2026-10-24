# 副屏监控 · GlancePad

> 一块闲置的竖屏，变成桌面上最顺眼的硬件仪表盘。

![version](https://img.shields.io/badge/version-1.2.1-4fd1c5)
![platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-5b9dff)
![python](https://img.shields.io/badge/python-3.9%2B-9a7dff)
![license](https://img.shields.io/badge/license-MIT-57d98a)

**GlancePad** 是一款 Windows 桌面监控面板，专门为**竖屏副屏**设计（1080×1920 / 设计稿 864×1536）。
暗色毛玻璃风格，CPU / GPU / 内存 / 存储 / 网络 / 天气 / 音乐播放器 / 快捷启动 Dock 一屏看全，
自动适配任意分辨率，横屏则自动切三列布局。

<p align="center">
  <img src="docs/screenshots/portrait.png" width="330" alt="竖屏效果">
  <img src="docs/screenshots/landscape.png" width="560" alt="横屏效果">
</p>

<p align="center">
  <sub>左：1080×1920 竖屏　|　右：1536×864 横屏（同一份代码，自动切布局）</sub>
</p>

---

## 这个工具能做什么

它把一个网页变成一块**常驻副屏的系统仪表盘**：

| 模块 | 显示内容 |
|---|---|
| **顶部** | 大号时钟、**每日一言**（hitokoto）、右上角日期 + **历史上的今天**（8 条，年份倒序） |
| **CPU** | 占用率圆环、温度、频率、电压、功耗、用户/系统/IO 占比、16 核逐核占用 |
| **GPU** | 占用率圆环、温度、频率、功耗、显存占用条、热点温度、显存负载、核心电压、核心/显存/编码/解码利用率 |
| **性能监控** | CPU / GPU / 内存 三条 60 点滚动折线 |
| **网络 + 天气** | 左半：SSID、适配器、连接类型、频段信道、IPv4/IPv6、收发速率、信号强度、**下载速度折线**；右半上半：**今日天气 + 七天预报**（竖排七列：日期名 / 日期 / 图标 / 高低温，把空间用满），右半下半：**邮箱快捷方式**（163 / 126 / Outlook / Gmail 四宫格，点一下开新标签） |
| **内存** | 已用/可用/已缓存/已提交/分页池/非分页 + 内存条规格（型号/频率/插槽） |
| **温度监控** | CPU、GPU、**风扇 1 / 风扇 2 转速**（主板 SuperIO 读不到时显示 `--`） |
| **存储** | 每块盘一张卡：占用条、已用/总量、剩余、**活动时间、平均响应、读写速度**、物理盘号 |
| **音乐播放器** | 播放/暂停/上一首/下一首、系统音量、歌名歌手、**整首歌词滚动 PV**（当前句放大 + 逐字高亮）、**Material 3 波浪进度条**、待机频谱 |
| **快捷启动 Dock** | 点击即启动应用，悬停显示名称气泡，图标/名称/路径/顺序**全部可自定义** |

配套还带一个**托盘程序兼设置程序**，解决"全屏 kiosk 退不出来"的经典问题，
并且可以在图形界面里直接改 Dock 和天气城市，不用手动编辑 JSON。

---

## 快速开始

### exe 安装包（最省事）

从 [Releases](https://github.com/fotlim/glancepad/releases/latest) 下载 `GlancePad-Setup-x.y.z.exe`，
双击安装即可。安装向导里可以：

- **自定义安装目录**（默认 `D:\GlancePad`）
- 单独勾选 **开机自启** / **开始菜单快捷方式** / **桌面快捷方式**
- 安装包**自带 Python 运行时和全部依赖**，目标机器不用预装 Python、不用联网

静默安装（给批处理/部署用）：

```bat
GlancePad-Setup-1.2.1.exe /VERYSILENT /DIR=D:\GlancePad /TASKS=desktopicon,startmenu
```

卸载走「设置 → 应用」或开始菜单里的「卸载 副屏监控 · GlancePad」，会顺手停掉监控进程。

### 源码一键安装（推荐开发用）

不想碰命令行就用安装脚本，它会复制程序、建桌面快捷方式、问你要不要开机自启：

```powershell
# 在解压后的目录里
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 指定安装目录 + 不问直接决定开机自启
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Dir "D:\GlancePad" -Autostart yes
```

装完会得到：

- 程序在 `D:\GlancePad`（默认，可用 `-Dir` 改）
- 桌面快捷方式「**副屏监控**」，双击 → 托盘出现青绿三角图标
- **开机自启可选**：安装时问你，之后也能随时在「托盘 → 开机自启」或「设置 → 系统」里勾掉
- 自动检查 Python 依赖、Chrome、LHM 桥接、PawnIO 驱动，缺什么直接告诉你怎么补

卸载：`.\install.ps1 -Uninstall`（先停监控，再删快捷方式、自启项和安装目录）

**升级到新版本**：直接用新源码重跑一遍安装脚本即可：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Dir "D:\GlancePad"
```

脚本会先探测并停掉正在运行的旧版本（否则 `lhm_bridge.exe` 锁着 dll 复制会失败），
复制新文件，然后**保留你原来的 `settings.json`** —— Dock 项和天气城市都不丢。
想恢复成默认配置再加 `-ResetSettings`。

> 安装脚本只写**当前用户**的注册表 `HKCU\...\Run`，不需要管理员权限。

### 环境要求

- **Windows 10 / 11**（用了 `winsdk` / `pycaw` / `winreg` / `ctypes.wintypes`，跨平台跑不了）
- **Python 3.9+**（推荐 3.11，安装时请勾选 *Add Python to PATH*）
- **Google Chrome**（用作 kiosk 全屏外壳）

### 手动三步跑起来

```powershell
# 1. 装 Python 依赖
pip install -r requirements.txt

# 2. 部署传感器采集桥（自动下载 LibreHardwareMonitor 并编译，可选但强烈建议）
powershell -ExecutionPolicy Bypass -File .\tools\setup_lhm.ps1

# 3. 启动
powershell -ExecutionPolicy Bypass -File .\start_monitor.ps1
```

脚本会依次：启动 LHM 采集桥 → 启动 Flask 服务（8770）→ 在副屏打开 kiosk 全屏窗口 →
启动置顶守护 → 驻留托盘图标。

> **全屏窗口不会占任务栏**：置顶守护会顺手给 kiosk 窗口加上 `WS_EX_TOOLWINDOW`，
> 所以任务栏里不会留下 Chrome 图标和缩略图预览。窗口重建后 2 秒内会自动补回。

> **想先看看效果？** 只起服务：`.\start_monitor.ps1 -NoBrowser`，
> 然后浏览器打开 <http://127.0.0.1:8770/>。

### 常用命令

```powershell
.\start_monitor.ps1              # 启动全部
.\start_monitor.ps1 -Status      # 看各组件运行状态
.\start_monitor.ps1 -Stop        # 停掉服务/窗口/守护（托盘保留）
.\start_monitor.ps1 -Quit        # 连托盘程序一起退出
.\start_monitor.ps1 -NoBrowser   # 只起后端，不开窗口
.\start_monitor.ps1 -Screen 2    # 指定用第几块屏（0=自动选非主屏）
.\start_monitor.ps1 -NoTopMost   # 不强制置顶
.\start_monitor.ps1 -NoTray      # 不起托盘
```

### 怎么退出全屏

kiosk 全屏没有关闭按钮，这是故意的（避免误触）。退出方式：

- **右键右下角托盘图标 →「停止监控」**（推荐）
- 或命令行 `.\start_monitor.ps1 -Quit`
- 或 `Alt + F4`

---

## 自定义

### 改快捷启动 Dock

**右键托盘图标 →「设置…」**，在「快捷启动 Dock」页里：

- 添加应用：填名称、选程序路径（有文件浏览器）、选图标 PNG、可填启动参数
- 编辑 / 删除 / 上移 / 下移
- 保存后监控页面 **20 秒内自动刷新**，不用重启

也可以直接改 `settings.json`（首次运行自动生成）：

```json
{
  "city": "杭州市",
  "latitude": 30.2741,
  "longitude": 120.1551,
  "weather": true,
  "apps": [
    { "name": "Chrome", "path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "icon": "chrome.png", "args": "" }
  ]
}
```

> `icon` 只写文件名时会去 `static/icons/` 找；也可以填 `/static/icons/xxx.png` 或完整的 http(s) 地址。
> 路径没在 `settings.json` 里登记的程序**无法被 Dock 启动**（`/api/launch` 会返回 403），这是刻意的安全限制。

### 改天气城市与天气源

同一设置窗口的「天气与位置」页，填**城市名**即可（比如「长春市」「杭州市」）—— 名字会拿去查中国天气网的站点码，
也直接显示在天气卡上；下面的经纬度只在回退 open-meteo 时用到，留默认值也没关系（会自动按城市名校正）。

页面上还有「**天气源**」下拉，三档：

| 选项 | 说明 |
| --- | --- |
| **自动**（默认） | 先查中国天气网（中国气象局站点），取不到才回退 open-meteo |
| **中国天气网** | 只用中国气象局数据，站点更贴近本地实况，还带 AQI 与**气象预警** |
| **open-meteo** | 只用国际通用的 open-meteo（网格插值，国内精度略粗） |

> 为什么默认换成中国天气网：open-meteo 在国内是网格插值，和气象局观测站有偏差，体感常常「不准」。
> 中国天气网是气象局官方数据，还会给 AQI 和预警（面板上显示成黄色的 ⚠ 行）。两者都免费、都不用 API key。

> 改完城市如果读数还是不对，保存设置后重启一次监控 —— 后端会按城市名自动校正经纬度。

### 邮箱快捷方式

网络卡右半边被分成上下两块：上半是天气，下半是**邮箱快捷方式**——163、126、Outlook、Gmail 四个按钮，
点一下就在浏览器里打开对应邮箱的网页版：

| 按钮 | 打开地址 |
| --- | --- |
| **163** | https://mail.163.com/ |
| **126** | https://mail.126.com/ |
| **Outlook** | https://outlook.live.com/mail/ |
| **Gmail** | https://mail.google.com/ |

地址列表写死在 `static/app.js` 的 `MAILBOXES` 数组里（`key` / `name` / `url` / `color` 四个字段），
想换成别的邮箱或改顺序，直接改这个数组即可，不需要动后端。

> 用的是 `window.open(url, "_blank")` 新标签打开，浏览器拦截弹窗时会自动退化成当前页跳转。
> 已经在浏览器里登录过的邮箱，点开就是已登录状态。

### 换字体

设置窗口 →「**显示**」→「字体」：

- **系统默认**：用系统字体栈（HarmonyOS Sans SC → 微软雅黑 → PingFang SC → Segoe UI）
- **自定义 TTF**：点「导入字体…」选一个 .ttf / .otf / .ttc，文件会被复制到程序目录的 onts/ 下，
  然后在下面下拉里选中它即可。**只对本程序生效，不安装到系统**。

> 字体靠 /api/theme.css 动态下发 @font-face + --font-family，页面 20 秒内自动应用，不会重排错位。

### 换背景

设置窗口 →「**显示**」→「背景」：

- **默认渐变**：内置的暗色径向渐变
- **自定义图片**：点「导入图片…」选 .jpg / .png / .webp / .bmp，复制到程序目录的 `backgrounds/`

三种处理方式：

| 处理方式 | 效果 |
|---|---|
| **模糊** | 高斯模糊 + 轻微饱和增强（推荐，模拟毛玻璃），「模糊半径」滑块可调 0~60px |
| **压暗** | 不加模糊，只降低亮度，适合深色照片 |
| **原图** | 直接用原图，不加任何滤镜 |

「不透明度」滑块控制背景图本身的不透明度（10%~100%），卡片始终是半透明毛玻璃，不影响可读性。

#### 自动对比度

照片类背景常常一半亮一半暗，白字压在亮处就看不清了。打开「**根据背景亮度自动增强文字对比度**」（默认开）后：

- 后端把图片缩成 32×32，算整体亮度和上/中/下三段的亮度；
- 前端据此自动加深顶部与底部的暗角遮罩（越亮压得越狠）、按整体亮度压暗卡片玻璃层、把次要文字色往亮处提；
- 越亮的图补偿越明显，深色照片几乎不动。

关闭后一切回到固定配色。这个开关写入 `settings.json` 的 `bg_contrast`（`auto` / `off`）。

---

## 数据从哪来

三层数据源，按优先级自动降级，**缺哪层都能跑**，只是对应字段显示 `--`：

| 优先级 | 来源 | 能拿到什么 | 需要什么 |
|---|---|---|---|
| 1 | **AIDA64** | 几乎全部传感器 | AIDA64 里勾选「允许共享内存」或「允许将监测数据写入注册表」 |
| 2 | **LibreHardwareMonitor** | CPU 温度/功耗/电压/频率、GPU 温度/热点/显存负载/核心电压、内存 | **PawnIO** 内核驱动 + `tools/setup_lhm.ps1` 部署桥接 |
| 3 | **psutil / nvidia-smi** | CPU 占用、内存、磁盘、网络、GPU 基础项 | 无，装好 Python 包就能用 |

页面右上角的徽章（`AIDA64` / `LHM` / `psutil`）会告诉你当前实际生效的是哪一层。

### 关于 PawnIO

LibreHardwareMonitor v0.9.6 起改用 [PawnIO](https://github.com/namazso/PawnIO.Setup) 作为 ring0 后端。
**没装它时**：CPU 温度 / 功耗 / 频率、主板温度、风扇转速 全部读不到（显示 `--`），
GPU / 内存 / 网络不受影响。

安装：下载 [PawnIO_setup.exe](https://github.com/namazso/PawnIO.Setup/releases/latest) → 右键「以管理员身份运行」→
装完用 `Get-Service PawnIO` 确认状态是 `Running`。

> 小坑：PawnIO 安装器界面是自绘的，**弹着窗口不代表没装成功**，以服务状态为准。

### 为什么 AIDA64 要手动开

AIDA64 的界面是 Delphi VCL 全自绘，没有任何可靠的 UI 自动化入口（无 Win32 菜单、无 UIAutomation 按钮、
不接受投递的鼠标消息）。本项目**不尝试自动点它**，请手动在 AIDA64 里开：

`文件 → 设置 → 外部应用程序 → 勾选「允许共享内存」或「允许将监测数据写入注册表」`

---

## 目录结构

```
glancepad/
├─ install.ps1             安装/卸载：复制到安装目录 + 桌面快捷方式 + 可选开机自启
├─ app.py                  Flask 服务（8770）
│                            /  /api/snapshot  /api/stream(SSE)  /api/sources
│                            /api/media  /api/lyrics  /api/media/control  /api/media/volume
│                            /api/apps  /api/apps/version  /api/launch  /api/settings
│                            /api/screens  /api/theme  /api/theme.css  /api/files
│                            /fonts/<name>  /backgrounds/<name>
├─ launcher.pyw            托盘程序兼设置程序（pystray + tkinter）
├─ start_monitor.ps1       一键启动：LHM 桥 → Flask → kiosk → 置顶守护 → 托盘
├─ keep_top.ps1            置顶守护：每 2 秒抢回最前 + 摘掉任务栏图标
├─ settings.json           城市/经纬度/Dock/显示设置（首次运行自动生成，不入库）
├─ requirements.txt        Python 依赖
├─ VERSION                 版本号
├─ sensors/
│  ├─ aida64.py            AIDA64 共享内存 + 注册表双通道
│  ├─ lhm.py               LibreHardwareMonitor 桥接（按 sensor id 精确映射）
│  ├─ media.py             SMTC 播放状态/控制 + pycaw 音量 + 歌词检索
│  ├─ network.py           WLAN 信息 + open-meteo 天气/七天预报 + 一言 + 历史上的今天
│  └─ hardware.py          主采集层：三层合并 + psutil/nvidia-smi 兜底
├─ static/
│  ├─ index.html           布局骨架
│  ├─ style.css            毛玻璃暗色主题 + 双设计稿自适应缩放
│  ├─ app.js               SSE 前端、曲线绘制、Dock、播放器
│  ├─ glancepad.ico        应用图标（快捷方式/托盘用）
│  └─ icons/               Dock 图标（64×64 PNG + _default.svg 兜底）
├─ tools/
│  ├─ setup_lhm.ps1        下载 LHM + 编译采集桥（一键）
│  ├─ lhm_bridge.cs        常驻桥接源码（HTTP 暴露传感器 JSON）
│  ├─ lhm_probe.cs         一次性导出全部传感器（调试用）
│  └─ aida_*.py            AIDA64 窗口自动化实验脚本（历史遗留，仅参考）
├─ fonts/                  用户导入的字体（.gitkeep 占位，内容不入库）
├─ backgrounds/            用户导入的背景图（.gitkeep 占位，内容不入库）
└─ docs/screenshots/       README 截图
```

---

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  Chrome kiosk (副屏全屏)                                  │
│  static/index.html + style.css + app.js                  │
│    · SSE 订阅 /api/stream，每秒一帧                       │
│    · Canvas 画三条滚动折线 + 下载速度折线                  │
│    · Dock 点击 → POST /api/launch                        │
└───────────────┬──────────────────────────────────────────┘
                │ HTTP 127.0.0.1:8770
┌───────────────▼──────────────────────────────────────────┐
│  app.py  (Flask)                                         │
│    /api/snapshot ← hardware.snapshot()                   │
└───────────────┬──────────────────────────────────────────┘
                │
     ┌──────────┼──────────────┬───────────────┐
     ▼          ▼              ▼               ▼
 aida64.py   lhm.py        network.py      psutil /
 (winreg +   (HTTP 8771)   (netsh +        nvidia-smi
  共享内存)                  open-meteo)
                │
     ┌──────────▼──────────┐
     │ lhm_bridge.exe      │  ← tools/setup_lhm.ps1 编译
     │ LibreHardwareMonitor│  ← 需要 PawnIO 驱动读 CPU 温度
     └─────────────────────┘
```

---

## 常见问题

**Q：副屏是 125% 缩放，窗口只铺了 80%，画面还被拉伸？**
Windows 会向未声明 DPI 感知的进程报缩放后的逻辑尺寸。本项目所有截屏/坐标脚本开头都调了
`SetProcessDpiAwarenessContext(-4)`（PER_MONITOR_AWARE_V2），`start_monitor.ps1` 里也是。

**Q：监控窗口被任务管理器 / 桌宠挡住？**
`SetWindowPos(HWND_TOPMOST)` 只能进入 Topmost 层，**不能保证在层内最前**。
所以要有 `keep_top.ps1` 常驻，每 2 秒重新抢一次。别把它的守护进程关掉。

**Q：Dock 图标悬停时名字气泡被裁掉？**
`.dock` 必须保持 `overflow: visible`，否则 `::after` 气泡会被整条裁掉。

**Q：进度条看不见，量出来高度是 0？**
flex 容器里的固定高度条必须写 `flex: 0 0 Npx`，只写 `height` 会被父级 `flex: 1` 压成 0。

**Q：CPU 用户/系统占比一直是 0？**
`psutil.cpu_times_percent()` 首次调用恒为 0，且和 `cpu_percent()` 共用内部基准会互相干扰。
本项目自己差分计算（`_cpu_snapshot()` + `_cpu_time_delta()`），并在模块导入时先打底采样一次。

**Q：想在无头环境截图看效果？**
加 `?static=1` 参数（只抓一帧不连 SSE），否则 SSE 长连接会让 `--virtual-time-budget` 永远等不到 load：

```powershell
chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 `
       --window-size=1080,1920 --screenshot=out.png --virtual-time-budget=9000 `
       "http://127.0.0.1:8770/?static=1"
```

**Q：想精确量某个卡片多高？**
访问 `http://127.0.0.1:8770/?static=1&debug=1`，页面底部会输出每个卡片和关键元素的
`getBoundingClientRect()`（包在 `DBGJSON...ENDDBG` 之间），配合
`chrome --dump-dom` 抓出来解析，比看截图猜像素快得多。

**Q：任务栏里多了一个 Chrome 图标和缩略图预览？**
kiosk 窗口默认会占任务栏。`keep_top.ps1` 每 2 秒会给窗口补上 `WS_EX_TOOLWINDOW`
并清掉 `WS_EX_APPWINDOW`，这样它就不出现在任务栏、也没有缩略图预览了。
Chrome 重建窗口后 2 秒内会自动恢复，不需要手动干预。

**Q：怎么让它开机自启？不想要了怎么关？**
安装脚本会问你一次（`-Autostart yes|no`）。装完之后两种改法：
托盘右键 →「开机自启」勾选/取消；或 设置窗口 →「系统」标签页 → 勾选框。
本质是写当前用户的 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\GlancePad`，
不需要管理员权限，也不改动任何系统服务。

**Q：WiFi 一直显示「未检测到 WiFi 连接」，但电脑明明连着？**
Windows 把 SSID 当作**位置信息**。如果系统「定位服务」总开关被关掉，`netsh wlan show interfaces`
会直接返回 `error 5: Access is denied`，连 SSID 都读不到。开一下就好：

```powershell
# 开启定位服务（只需一次，管理员权限）
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\lfsvc\Service\Configuration" -Name Status -Value 1 -Type DWord
Restart-Service lfsvc -Force
```

或者：设置 → 隐私和安全性 → 定位 → 打开「定位服务」。
程序另有 `Get-NetConnectionProfile` 兜底，定位关着时仍能显示 SSID 和网卡，但**信号强度与信道会缺失**。

**Q：怎么让它显示在另一块屏幕上？**
设置窗口 →「显示」→「副屏选择」，下拉里会列出当前所有显示器（含分辨率与主屏标记），选一个保存，
**下次启动**生效。选「自动」则沿用原来的规则：优先挑非主屏。

也可以命令行指定：`.\start_monitor.ps1 -Screen 2`，
或直接改 `settings.json` 的 `"screen"` 字段（`0` = 自动，`1..n` = 按枚举顺序）。
注意枚举顺序由 `EnumDisplayMonitors` 决定，**不是** `\\.\DISPLAYn` 的编号，所以请以设置窗口里显示的列表为准。

**Q：主板温度 / 磁盘温度 / 风扇转速一直是 `--`？**
LHM 需要能识别主板的 SuperIO 芯片。部分主板（比如开发测试用的 `X5`）识别不到，这不是配置问题，
目前没有通用解法。部分显卡（如 RTX 2060）硬件本身也不提供显存温度和风扇转速。

---

## 手工验证

```powershell
# 看各采集层原始数据
python sensors\lhm.py
python sensors\aida64.py
python sensors\network.py
python sensors\media.py

# 看服务输出
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8770/api/snapshot | Select -Expand Content
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8770/api/sources  | Select -Expand Content

# 看 LHM 桥
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8771/status
```

---

## 参与贡献

欢迎二次修改与分发，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。简单说：

1. Fork → 改 → PR
2. 提交前跑一下 `python -m py_compile app.py launcher.pyw sensors\*.py` 和 `node --check static\app.js`
3. PowerShell 脚本请存成 **UTF-8 带 BOM**，否则中文会乱码并报假的语法错

---

## 许可

[MIT License](LICENSE) © 2026 fotlim

你可以自由地使用、修改、合并、发布、分发、再许可和销售本软件的副本，
只需保留版权声明与许可声明。**允许商用，允许闭源二次分发。**

### 第三方组件

- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) — MPL-2.0，**不随仓库分发**，由 `tools/setup_lhm.ps1` 从官方 release 下载
- [PawnIO](https://github.com/namazso/PawnIO.Setup) — 需用户自行安装的内核驱动
- 天气数据：[open-meteo.com](https://open-meteo.com/)（CC BY 4.0）
- 歌词数据：[lrclib.net](https://lrclib.net/) / 网易云音乐公开接口
- 图标：各应用自身的可执行文件图标（由使用者本机提取），仅用于本地启动器展示
