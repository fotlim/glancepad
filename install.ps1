<#
  GlancePad 副屏监控  ·  Windows 安装脚本
  ========================================
  做什么：
    1. 把程序复制到安装目录（默认 D:\GlancePad）
    2. 在桌面创建启动快捷方式
    3. 询问是否开机自启（--autostart yes / no / ask，默认 ask）
    4. 顺手检查 Python 依赖、LHM 桥接与 PawnIO 驱动，缺什么就提示怎么补

  用法（在解压后的目录里右键「使用 PowerShell 运行」，或命令行）：
    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -Dir "D:\Tools\GlancePad"
    powershell -ExecutionPolicy Bypass -File install.ps1 -Autostart yes -NoPrompt
    powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall

  卸载：install.ps1 -Uninstall（会先停掉监控，再删安装目录和快捷方式；不删 D 盘外的任何东西）
#>
[CmdletBinding()]
param(
  [string]$Dir = "D:\GlancePad",
  [ValidateSet("ask", "yes", "no")]
  [string]$Autostart = "ask",
  [switch]$NoPrompt,
  [switch]$NoDesktop,
  [switch]$Uninstall,
  [switch]$KeepSettings
)

$ErrorActionPreference = "Stop"
$SRC = Split-Path -Parent $MyInvocation.MyCommand.Path
$APP = "GlancePad"
$EXE_LNK = "副屏监控.lnk"
$ICO = Join-Path $SRC "static\glancepad.ico"

# ---------- 输出小工具
function Info($m) { Write-Host "  $m" -ForegroundColor Gray }
function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Bad($m) { Write-Host "  [x] $m" -ForegroundColor Red }
function Step($m) { Write-Host "`n$m" -ForegroundColor Cyan }

function Get-DesktopDir {
  $d = [Environment]::GetFolderPath("Desktop")
  if ($d -and (Test-Path $d)) { return $d }
  $d = Join-Path $env:USERPROFILE "Desktop"
  if (Test-Path $d) { return $d }
  return $null
}

function Find-Python {
  foreach ($n in @("python.exe", "python3.exe")) {
    $c = Get-Command $n -CommandType Application -ErrorAction SilentlyContinue |
         Where-Object { $_.Source -notlike "*WindowsApps*" } | Select-Object -First 1
    if ($c) { return $c.Source }
  }
  $cands = @()
  if ($env:LOCALAPPDATA) { $cands += (Join-Path $env:LOCALAPPDATA "Programs\Python") }
  $cands += @("C:\Python313", "C:\Python312", "C:\Python311", "C:\Python310")
  if ($env:ProgramFiles) { $cands += (Join-Path $env:ProgramFiles "Python") }
  foreach ($c in $cands) {
    if (Test-Path $c) {
      $f = Get-ChildItem -Path $c -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
           Sort-Object FullName -Descending | Select-Object -First 1
      if ($f) { return $f.FullName }
    }
  }
  $py = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
  if ($py) {
    try {
      $p = & $py.Source -c "import sys;print(sys.executable)" 2>$null
      if ($p -and (Test-Path ($p.Trim()))) { return $p.Trim() }
    } catch {}
  }
  return $null
}

# ---------- 卸载
if ($Uninstall) {
  Write-Host "`n=== 卸载 $APP ===" -ForegroundColor Cyan
  $ps1 = Join-Path $Dir "start_monitor.ps1"
  if (Test-Path $ps1) {
    Info "先停止正在运行的监控…"
    try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -Quit 2>&1 | Out-Null } catch {}
    Start-Sleep -Milliseconds 800
  }
  # 开机自启
  $run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
  if (Test-Path $run) {
    if ((Get-ItemProperty -Path $run -Name $APP -ErrorAction SilentlyContinue)) {
      Remove-ItemProperty -Path $run -Name $APP -Force
      Ok "已移除开机自启"
    }
  }
  # 桌面快捷方式
  $desk = Get-DesktopDir
  if ($desk) {
    $lnk = Join-Path $desk $EXE_LNK
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Ok "已删除桌面快捷方式" }
  }
  # 安装目录
  if (Test-Path $Dir) {
    Remove-Item -Path $Dir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $Dir) { Bad "没能完全删除 $Dir，可能有文件被占用，请手动删" }
    else { Ok "已删除安装目录 $Dir" }
  }
  Write-Host "`n卸载完成。`n" -ForegroundColor Green
  return
}

# ---------- 安装
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "   GlancePad 副屏监控  ·  安装程序" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Info "来源目录：$SRC"
Info "安装目录：$Dir"

# 0. 环境检查
Step "[1/5] 检查运行环境"
$PY = Find-Python
if (-not $PY) {
  Bad "没找到 Python 3。请先装 Python 3.10+ 并勾选 Add to PATH"
  Info "下载：https://www.python.org/downloads/windows/"
  if (-not $NoPrompt) { Read-Host "`n按回车退出" }
  exit 1
}
Ok "Python: $PY"

$ver = & $PY -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
Info "版本 $ver（需要 3.10+）"

# 依赖检查：把探测脚本写成临时文件再跑。
# 不要用 `python -c @"..."@` —— PowerShell 的 here-string 传给 -c 时引号/换行会被
# 二次解析，throw 出 NativeCommandError 假错误，让人误以为依赖检查失败。
$chkPy = Join-Path $env:TEMP "glancepad_chk_deps.py"
@'
import importlib.util
mods = {"flask": "Flask", "psutil": "psutil", "requests": "requests", "PIL": "Pillow",
        "pystray": "pystray", "winsdk": "winsdk", "pycaw": "pycaw"}
miss = [pkg for mod, pkg in mods.items() if importlib.util.find_spec(mod) is None]
print(",".join(miss))
'@ | Set-Content -Path $chkPy -Encoding UTF8
$missing = ""
try { $missing = (& $PY $chkPy 2>$null) -join "" } catch { $missing = "" }
Remove-Item $chkPy -Force -ErrorAction SilentlyContinue

if ($missing -and $missing.Trim()) {
  Warn "缺少依赖：$($missing.Trim())"
  if ($NoPrompt) { $yn = "y" } else {
    $yn = Read-Host "现在用 pip 安装吗？(Y/n)"
    if (-not $yn) { $yn = "y" }
  }
  if ($yn -match "^[Yy]") {
    Info "安装中，请稍候…"
    & $PY -m pip install --upgrade pip --quiet 2>&1 | Out-Null
    & $PY -m pip install -r (Join-Path $SRC "requirements.txt") 2>&1 | Out-Null
    Ok "依赖安装完成"
  } else {
    Warn "跳过。程序可能起不来，之后可手动执行：$PY -m pip install -r requirements.txt"
  }
} else {
  Ok "Python 依赖齐全"
}

$chromeCands = @(
  (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
  (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
)
if (-not ($chromeCands | Where-Object { Test-Path $_ })) {
  Warn "没找到 Chrome。监控面板要用 Chrome 全屏显示，请先装 Chrome"
} else { Ok "Chrome 已找到" }

# 1. 复制文件
Step "[2/5] 复制程序到 $Dir"
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }

$skipDirs = @(".git", "__pycache__", ".chrome-profile", "node_modules", "assets")
$skipFiles = @("settings.json")   # 老设置不覆盖
$copied = 0
function CopyTree($from, $to) {
  Get-ChildItem -Path $from -Force | ForEach-Object {
    $name = $_.Name
    if ($_.PSIsContainer) {
      if ($skipDirs -contains $name) { return }
      $dest = Join-Path $to $name
      if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }
      CopyTree $_.FullName $dest
    } else {
      if ($skipFiles -contains $name) { return }
      if ($_.Extension -in @(".log", ".pid")) { return }
      if ($name -like "*.log.err") { return }
      Copy-Item $_.FullName (Join-Path $to $name) -Force
      $script:copied++
    }
  }
}
CopyTree $SRC $Dir
Ok "已复制 $copied 个文件"

# 保留用户原有 settings.json
$sNew = Join-Path $Dir "settings.json"
$sOld = Join-Path $SRC "settings.json"
if ((-not (Test-Path $sNew)) -and (Test-Path $sOld)) {
  Copy-Item $sOld $sNew -Force
  Info "已迁移现有 settings.json"
}

# 2. 桌面快捷方式
Step "[3/5] 创建桌面快捷方式"
$desk = Get-DesktopDir
if ($NoDesktop) {
  Info "按参数跳过"
} elseif (-not $desk) {
  Warn "找不到桌面目录，跳过"
} else {
  $lnkPath = Join-Path $desk $EXE_LNK
  $pw = Join-Path (Split-Path -Parent $PY) "pythonw.exe"
  if (-not (Test-Path $pw)) { $pw = $PY }
  $target = Join-Path $Dir "launcher.pyw"
  $sh = New-Object -ComObject WScript.Shell
  $lnk = $sh.CreateShortcut($lnkPath)
  $lnk.TargetPath = $pw
  $lnk.Arguments = "`"$target`""
  $lnk.WorkingDirectory = $Dir
  $lnk.Description = "副屏监控 GlancePad - 托盘启动器"
  if (Test-Path $ICO) { $lnk.IconLocation = "$ICO,0" }
  $lnk.Save()
  Ok "桌面快捷方式：$lnkPath"
}

# 3. 开机自启
Step "[4/5] 开机自启"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (-not (Test-Path $runKey)) { New-Item -Path $runKey -Force | Out-Null }
$pwPath = Join-Path (Split-Path -Parent $PY) "pythonw.exe"
if (-not (Test-Path $pwPath)) { $pwPath = $PY }
$autoCmd = "`"$pwPath`" `"$(Join-Path $Dir 'launcher.pyw')`""

$doAuto = $false
switch ($Autostart) {
  "yes" { $doAuto = $true }
  "no" { $doAuto = $false }
  default {
    if ($NoPrompt) { $doAuto = $false }
    else {
      Write-Host ""
      Info "开机自启会在登录后自动在托盘启动监控（不弹窗，不占任务栏）。"
      $a = Read-Host "要开启吗？(y/N)"
      $doAuto = ($a -match "^[Yy]")
    }
  }
}

if ($doAuto) {
  Set-ItemProperty -Path $runKey -Name $APP -Value $autoCmd
  Ok "已开启开机自启（注册表 HKCU\...\Run\$APP）"
  Info "想关掉：设置程序里点「取消开机自启」，或运行 install.ps1 -Autostart no"
} else {
  if ((Get-ItemProperty -Path $runKey -Name $APP -ErrorAction SilentlyContinue)) {
    Remove-ItemProperty -Path $runKey -Name $APP -Force -ErrorAction SilentlyContinue
    Ok "已关闭开机自启"
  } else {
    Info "不开机自启（随时可在托盘右键启动，或双击桌面图标）"
  }
}

# 4. 可选组件
Step "[5/5] 可选硬件传感器组件"
$lhmBridge = Join-Path $Dir "tools\LHM\lhm_bridge.exe"
if (Test-Path $lhmBridge) {
  Ok "LibreHardwareMonitor 桥接已就绪"
} else {
  Warn "缺少 LHM 桥接（GPU 热点/功耗/显存、CPU 温度、内存详情靠它）"
  Info "补装：在安装目录执行 powershell -ExecutionPolicy Bypass -File tools\setup_lhm.ps1"
}
$pawn = "HKLM:\SYSTEM\CurrentControlSet\Services\PawnIO"
if (Test-Path $pawn) {
  Ok "PawnIO 驱动已安装（CPU 温度/功耗可读）"
} else {
  Warn "未安装 PawnIO 驱动 —— CPU 温度/功耗会显示 --"
  Info "下载：https://github.com/namazso/PawnIO.Setup/releases （装完重启一次更稳）"
}

# ---------- 完成
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "   安装完成！" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Info "安装目录：$Dir"
if ($desk) { Info "启动方式：桌面「$EXE_LNK」 或 右下角托盘图标" }
Info "首次启动：双击桌面图标 → 托盘出现青绿三角 → 右键可 启动/停止/设置"
Write-Host ""
Info "直接开始监控（不开托盘）："
Info "  powershell -ExecutionPolicy Bypass -File `"$(Join-Path $Dir 'start_monitor.ps1')`""
Write-Host ""
if (-not $NoPrompt) { Read-Host "按回车关闭" }
