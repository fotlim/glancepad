# 参与贡献

欢迎任何形式的贡献：提 Issue、提 PR、改 UI、加新的数据源、适配别的主板/显卡。

## 开发环境

```powershell
git clone https://github.com/fotlim/glancepad.git
cd glancepad
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\tools\setup_lhm.ps1
powershell -ExecutionPolicy Bypass -File .\start_monitor.ps1 -NoBrowser
# 浏览器打开 http://127.0.0.1:8770/
```

调界面时不用每次都开 kiosk，普通浏览器窗口改窗口尺寸就能看到自适应效果；
按 `F12` 改 CSS 更快。

想测安装脚本（会真的复制文件、写注册表自启项，测完记得卸载）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Dir "$env:TEMP\glancepad_test" -Autostart no -NoPrompt -NoDesktop
# 测完
powershell -ExecutionPolicy Bypass -File "$env:TEMP\glancepad_test\install.ps1" -Dir "$env:TEMP\glancepad_test" -Uninstall
```

## 代码结构约定

- **数据采集层**在 `sensors/`，每个模块对外只暴露 `read()` / `status()` 两个函数，
  返回结构保持一致：`{ok, values, raw, fans, active}`。
- **`sensors/hardware.py` 是唯一的合并点**，需要新字段就往 `snapshot()` 返回值里加，
  前端只吃这一份 JSON。
- **前端不预计算数据**，所有格式化在 `app.js` 的 `render()` 里做。
- **CSS 用设计稿绝对像素**（`static/style.css` 里 `body.portrait` 是 864×1536、
  `body.landscape` 是 1536×864），缩放只靠 `--s` 这一个 CSS 变量。不要改成百分比布局，
  否则文字在非整数缩放下会糊。

## 提交前自检

```powershell
# Python 语法
python -m py_compile app.py launcher.pyw sensors\aida64.py sensors\hardware.py sensors\lhm.py sensors\media.py sensors\network.py

# JS 语法
node --check static\app.js

# PowerShell 语法（注意必须 UTF-8 带 BOM）
powershell -Command "$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('.\start_monitor.ps1',[ref]$null,[ref]$e); if($e){$e|%%{$_.Message}}else{'OK'}"
```

## 加一个「显示设置」（字体 / 背景 / 选屏）

这三类设置的链路是固定的，照着抄就行：

1. `sensors/network.py` 的 `_defaults` 里加默认键，并在 `settings()` 里透传（已有 13 个键，直接复用）。
2. `app.py` 的 `SETTING_KEYS` 元组里加键名 —— **不加会被 `POST /api/settings` 静默丢掉**。
3. `app.py` 的 `_ensure_settings()` 会自动给老 `settings.json` 补齐缺失键，不需要手动迁移。
4. 前端读设置走 `/api/theme`（JSON）或 `/api/theme.css`（CSS 变量），**`app.js:applyTheme()` 每 20 秒轮询一次并做 key 去重**。
5. 设置界面在 `launcher.pyw` 的 `SettingsWindow._build()`，记得同步改 `save()` 里的 `d.update(...)`。

> `@font-face` 这类**顶层 CSS 规则不能写进 `:root{}` 里面**，会被整条丢弃（v1.1.0 踩过这个坑）。
> 正确写法是先拼 `@font-face{...}`，再拼 `:root{--font-family:...}`。

## 踩过的坑（改代码前值得看一眼）

1. **PowerShell 脚本必须存 UTF-8 带 BOM**
   否则中文乱码，而且会报一个**假的** `Missing closing '}' in statement block`，
   排查半天以为是语法错。转码：

   ```powershell
   $p = ".\start_monitor.ps1"
   $txt = [System.IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
   [System.IO.File]::WriteAllText($p, $txt, (New-Object System.Text.UTF8Encoding($true)))
   ```

2. **`$pid` / `$PID` 是只读自动变量**，不能赋值，换个名字（我用 `$pp`）。

3. **判断进程存在别按命令行模糊匹配**：`Get-CimInstance ... | Where-Object { $_.CommandLine -like "*keep_top*" }`
   会匹配到「正在执行这条查询的 powershell 自己」。用 pid 文件更靠谱。

4. **flex 容器里的固定高度条必须 `flex: 0 0 Npx`**
   只写 `height: 6px` 会被父级 `flex: 1` 压成 0 高，肉眼看不见但 DOM 里确实存在。
   用 `?debug=1` 量一下 `getBoundingClientRect()` 就知道。

5. **Dock 的气泡要 `overflow: visible`**
   `.dock` 一旦设成 `overflow: hidden/auto`，`::after` 的气泡会被整条裁掉。

6. **`--kiosk=$URL` 是错的**，Chrome 不认，会开空白新标签页。
   必须 `"--kiosk", $URL` 两个独立参数。

7. **`chrome --dump-dom` 不加参数会输出 0 字节**
   必须带 `--hide-scrollbars --force-device-scale-factor=1 --window-size=W,H`。

8. **给 Delphi/VCL 程序（AIDA64）发 `SendMessageW` 会让本进程卡死**
   必须用 `SendMessageTimeoutW` 且显式设 `argtypes`（`LPARAM` 用 `ctypes.c_ssize_t`，否则 64 位指针溢出。
   另外这类程序的 UI 基本没法自动化：没有 Win32 菜单、UIAutomation 拿不到按钮、不接受投递的鼠标消息）。
   所以本项目不自动点 AIDA64，改成手把手教用户勾选项。

9. **`HttpListener` 在 .NET Framework 上会抛 `PlatformNotSupportedException`**
   `tools/lhm_bridge.cs` 改用 `TcpListener` + 手写极简 HTTP，反而更省事。

10. **`Add-Type -Path LibreHardwareMonitorLib.dll` 会失败（`0x80131515`）**
    dll 是从网上下载的，带「来自其他计算机」标记。
    `Unblock-File` 有时不管用，**直接用 `csc.exe` 引用编译成独立 exe** 最稳。

11. **`psutil.cpu_times_percent()` 首次调用恒为 0**
    而且和 `cpu_percent()` 共用内部基准会互相干扰。自己用 `cpu_times()` 差分算。

## 想加新数据源

在 `sensors/` 下新建模块，实现：

```python
def read():
    """返回 {ok: bool, values: {字段名: 数值}, raw: {...}, fans: [...], active: [...]}"""

def status():
    """返回 {ok: bool, ...诊断信息...}  # 供 /api/sources 展示"""
```

然后在 `sensors/hardware.py` 的 `snapshot()` 里按优先级合并即可。
字段名请复用现有命名（`cpu_temp` / `gpu_hotspot` / `mem_usage` …），前端已经按这些名字渲染。
