# ============================================================
#  副屏监控 - 一键启动脚本
#  用法：
#    .\start_monitor.ps1            启动（服务 + 副屏全屏窗口 + 置顶）
#    .\start_monitor.ps1 -Stop      全部关掉
#    .\start_monitor.ps1 -Status    看现在跑没跑
#    .\start_monitor.ps1 -Screen 2  指定用第几块屏（1=主屏）
#    .\start_monitor.ps1 -NoTopMost 不置顶（默认置顶，防止被别的窗口挡住）
#    .\start_monitor.ps1 -Tray      走托盘模式（起服务+全屏窗口，然后驻留托盘，
#                                   从这里能一键停止 —— kiosk 全屏退不出来的解法）
# ============================================================
param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Tray,              # 兼容旧参数（托盘现在默认启动）
    [switch]$NoTray,            # 不启动托盘程序
    [switch]$Quit,              # 连托盘程序一起退出
    [int]$Port = 8770,
    [int]$Screen = 0,           # 0=自动选副屏
    [switch]$NoBrowser,         # 只起服务，不开窗口
    [switch]$NoTopMost          # 不置顶
)

$ErrorActionPreference = "Stop"
$ROOT   = Split-Path -Parent $MyInvocation.MyCommand.Path

# ------------------------------------------------------------ 依赖探测
# 不写死任何人的安装路径。先问系统（PATH / App Paths 注册表），再退回常见位置。
function Find-Python {
    # 1) PATH 上的 python（排除 WindowsApps 的微软商店占位符）
    foreach ($n in @("python.exe", "python3.exe")) {
        $c = Get-Command $n -CommandType Application -EA SilentlyContinue |
             Where-Object { $_.Source -notlike "*WindowsApps*" } |
             Select-Object -First 1
        if ($c) { return $c.Source }
    }
    # 2) 常见安装位置（取版本号最大的）
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        "C:\Python311", "C:\Python312", "C:\Python310",
        (Join-Path $env:ProgramFiles "Python")
    )
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        $hit = Get-ChildItem $r -Filter "python.exe" -Recurse -Depth 2 -EA SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    # 3) py 启动器
    $py = Get-Command "py.exe" -CommandType Application -EA SilentlyContinue |
          Select-Object -First 1
    if ($py) {
        $p = & $py.Source -c "import sys;print(sys.executable)" 2>$null
        if ($p -and (Test-Path $p)) { return $p.Trim() }
    }
    return $null
}

function Find-Chrome {
    # 1) App Paths 注册表（Chrome 安装时一定会写）
    foreach ($hive in @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")) {
        $v = (Get-ItemProperty $hive -EA SilentlyContinue).'(default)'
        if ($v -and (Test-Path $v)) { return $v }
    }
    # 2) PATH
    $c = Get-Command "chrome.exe" -CommandType Application -EA SilentlyContinue |
         Select-Object -First 1
    if ($c) { return $c.Source }
    # 3) 常见安装位置
    foreach ($p in @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

$PY     = Find-Python
$CHROME = Find-Chrome
if (-not $PY) {
    Write-Host "! 找不到 Python，请先安装 Python 3.9+ 并勾选 Add to PATH" -ForegroundColor Red
    Write-Host "  下载地址：https://www.python.org/downloads/windows/" -ForegroundColor DarkGray
    exit 1
}
if (-not $CHROME) {
    Write-Host "! 找不到 Google Chrome，请先安装：https://www.google.com/chrome/" -ForegroundColor Red
    exit 1
}
$URL    = "http://127.0.0.1:$Port/"
$LOG    = Join-Path $ROOT "monitor.log"
$PIDF   = Join-Path $ROOT ".monitor.pid"
$KEEPER = Join-Path $ROOT "keep_top.ps1"
$KPIDF  = Join-Path $ROOT ".keeper.pid"
$PROF   = Join-Path $ROOT ".chrome-profile"
$LAUNCHER = Join-Path $ROOT "launcher.pyw"
$LPIDF    = Join-Path $ROOT ".launcher.pid"

# LibreHardwareMonitor 采集桥（CPU 电压/功耗、GPU 热点/显存负载等靠它）
$LHMDIR   = Join-Path $ROOT "tools\LHM"
$BRIDGE   = Join-Path $LHMDIR "lhm_bridge.exe"
$BRIDGEP  = 8771
$BPIDF    = Join-Path $ROOT ".bridge.pid"

# 关键：先声明 DPI 感知，否则 Screen.Bounds 拿到的是 125% 缩放后的逻辑尺寸
# （副屏物理 1080x1920，不声明会报成 864x1536，窗口只铺 80% 且画面被拉伸）
if (-not ("DpiApi" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DpiApi {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
}
"@
}
# -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
try { [DpiApi]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null } catch {}

Add-Type -AssemblyName System.Windows.Forms

# ------------------------------------------------------------ Win32
if (-not ("WinApi" -as [type])) {
    Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinApi {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint ppid);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int idx);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int idx, int val);
    public static readonly IntPtr HWND_TOPMOST   = new IntPtr(-1);
    public static readonly IntPtr HWND_NOTOPMOST = new IntPtr(-2);
    public const uint SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002, SWP_SHOWWINDOW = 0x0040;
    public const int SW_RESTORE = 9;
    public const int GWL_EXSTYLE = -20;
    public const int WS_EX_TOOLWINDOW = 0x00000080;
    public const int WS_EX_APPWINDOW  = 0x00040000;
}
"@
}

function Get-WindowHandlesByPid {
    param([int]$ProcId)
    $script:hwnds = @()
    $cb = [WinApi+EnumWindowsProc]{
        param($h, $l)
        if ([WinApi]::IsWindowVisible($h)) {
            $sb = New-Object System.Text.StringBuilder 512
            [WinApi]::GetWindowTextW($h, $sb, 512) | Out-Null
            if ($sb.ToString()) {
                $pp = [uint32]0
                [WinApi]::GetWindowThreadProcessId($h, [ref]$pp) | Out-Null
                if ($pp -eq $ProcId) { $script:hwnds += $h }
            }
        }
        return $true
    }
    [WinApi]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    return $script:hwnds
}

function Set-TopMost {
    param([int]$ProcId, [switch]$Off)
    $target = if ($Off) { [WinApi]::HWND_NOTOPMOST } else { [WinApi]::HWND_TOPMOST }
    $n = 0
    foreach ($h in (Get-WindowHandlesByPid -ProcId $ProcId)) {
        # 顺手摘掉任务栏图标（WS_EX_TOOLWINDOW，清 WS_EX_APPWINDOW）
        # 否则 kiosk 窗口会在任务栏留一个占位和缩略图预览
        if (-not $Off) {
            $ex = [WinApi]::GetWindowLong($h, [WinApi]::GWL_EXSTYLE)
            $want = ($ex -bor [WinApi]::WS_EX_TOOLWINDOW) -band (-bnot [WinApi]::WS_EX_APPWINDOW)
            if ($ex -ne $want) {
                [WinApi]::SetWindowLong($h, [WinApi]::GWL_EXSTYLE, $want) | Out-Null
            }
        }
        [WinApi]::SetWindowPos($h, $target, 0, 0, 0, 0,
            ([WinApi]::SWP_NOSIZE -bor [WinApi]::SWP_NOMOVE -bor [WinApi]::SWP_SHOWWINDOW)) | Out-Null
        $n++
    }
    return $n
}

# ------------------------------------------------------------ 屏幕
function Get-TargetScreen {
    $screens = [System.Windows.Forms.Screen]::AllScreens
    if ($Screen -ge 1 -and $Screen -le $screens.Count) {
        $s = $screens[$Screen - 1]
    } else {
        # 自动：优先非主屏
        $s = $screens | Where-Object { -not $_.Primary } | Select-Object -First 1
        if (-not $s) { $s = $screens | Where-Object { $_.Primary } | Select-Object -First 1 }
    }
    return @{
        X = $s.Bounds.X; Y = $s.Bounds.Y
        W = $s.Bounds.Width; H = $s.Bounds.Height
        Name = $s.DeviceName; Primary = $s.Primary
    }
}

# ------------------------------------------------------------ 服务
function Test-Server {
    try {
        $r = Invoke-WebRequest ($URL + "api/snapshot") -TimeoutSec 2 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Get-MonitorChromePids {
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -EA SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Port*" -or $_.CommandLine -like "*$PROF*" } |
        Select-Object -ExpandProperty ProcessId
}

function Get-KeeperPids {
    # 用 pid 文件来判断，不要按命令行模糊匹配 powershell.exe
    # （模糊匹配会把「正在执行这条查询的 powershell 自己」也算进去，踩过坑）
    if (-not (Test-Path $KPIDF)) { return @() }
    $k = (Get-Content $KPIDF -Raw -EA SilentlyContinue)
    if (-not $k) { return @() }
    $k = $k.Trim()
    $proc = Get-Process -Id $k -EA SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "powershell") { return @([int]$k) }
    return @()
}

# ------------------------------------------------------------ LHM 采集桥
function Test-Bridge {
    try {
        $r = Invoke-WebRequest ("http://127.0.0.1:$BRIDGEP/status") -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Get-BridgePids {
    if (-not (Test-Path $BPIDF)) { return @() }
    $k = (Get-Content $BPIDF -Raw -EA SilentlyContinue)
    if (-not $k) { return @() }
    $k = $k.Trim()
    $proc = Get-Process -Id $k -EA SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "lhm_bridge") { return @([int]$k) }
    return @()
}

function Start-Bridge {
    if (-not (Test-Path $BRIDGE)) {
        Write-Host "  ! 没找到 LHM 桥接：$BRIDGE（跳过，CPU 电压/功耗会缺）" -ForegroundColor Yellow
        return
    }
    if (Test-Bridge) {
        Write-Host "  LHM 桥接已在运行，跳过" -ForegroundColor Green
        return
    }
    Write-Host "  启动 LHM 采集桥..." -ForegroundColor Yellow
    $b = Start-Process -FilePath $BRIDGE `
        -ArgumentList @("--port", "$BRIDGEP", "--period", "1000") `
        -WorkingDirectory $LHMDIR -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $ROOT "bridge.log") `
        -RedirectStandardError  (Join-Path $ROOT "bridge.err.log")
    $b.Id | Set-Content $BPIDF -Encoding ASCII
    # Computer.Open() 首次要 3~5 秒，慢慢等
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Bridge) {
            Write-Host "  OK LHM 桥接就绪 (PID $($b.Id)，端口 $BRIDGEP)" -ForegroundColor Green
            return
        }
    }
    Write-Host "  ! LHM 桥接启动超时，看看 bridge.err.log" -ForegroundColor Yellow
}

function Get-LauncherPids {
    if (-not (Test-Path $LPIDF)) { return @() }
    $k = (Get-Content $LPIDF -Raw -EA SilentlyContinue)
    if (-not $k) { return @() }
    $k = $k.Trim()
    $proc = Get-Process -Id $k -EA SilentlyContinue
    if ($proc -and ($proc.ProcessName -eq "pythonw" -or $proc.ProcessName -eq "python")) { return @([int]$k) }
    return @()
}

function Start-Tray {
    if (-not (Test-Path $LAUNCHER)) {
        Write-Host "  ! 没找到托盘程序：$LAUNCHER（跳过）" -ForegroundColor Yellow
        return
    }
    if (Get-LauncherPids) {
        Write-Host "  托盘程序已在运行，跳过" -ForegroundColor Green
        return
    }
    # 用 pythonw.exe 起，不出现控制台窗口
    $pyw = Join-Path (Split-Path -Parent $PY) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $PY }
    $lp = Start-Process -FilePath $pyw `
        -ArgumentList @("`"$LAUNCHER`"") `
        -WorkingDirectory $ROOT -WindowStyle Hidden -PassThru
    $lp.Id | Set-Content $LPIDF -Encoding ASCII
    Write-Host "  OK 托盘程序已启动 (PID $($lp.Id))，看右下角托盘图标" -ForegroundColor Green
}

function Stop-All {
    foreach ($kp in (Get-KeeperPids)) {
        Stop-Process -Id $kp -Force -EA SilentlyContinue
        Write-Host "  已停止置顶守护 PID=$kp" -ForegroundColor Yellow
    }
    Remove-Item $KPIDF -Force -EA SilentlyContinue
    foreach ($bp in (Get-BridgePids)) {
        Stop-Process -Id $bp -Force -EA SilentlyContinue
        Write-Host "  已停止 LHM 桥接 PID=$bp" -ForegroundColor Yellow
    }
    Remove-Item $BPIDF -Force -EA SilentlyContinue
    if (Test-Path $PIDF) {
        $oldPid = (Get-Content $PIDF -Raw).Trim()
        if ($oldPid) {
            Get-Process -Id $oldPid -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
            Write-Host "  已停止服务进程 PID=$oldPid" -ForegroundColor Yellow
        }
        Remove-Item $PIDF -Force -EA SilentlyContinue
    }
    foreach ($mp in (Get-MonitorChromePids)) {
        Stop-Process -Id $mp -Force -EA SilentlyContinue
        Write-Host "  已关闭监控窗口 PID=$mp" -ForegroundColor Yellow
    }
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue
    if ($conn) {
        $conn | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            Stop-Process -Id $_ -Force -EA SilentlyContinue
        }
        Write-Host "  已释放端口 $Port" -ForegroundColor Yellow
    }
    $bconn = Get-NetTCPConnection -LocalPort $BRIDGEP -State Listen -EA SilentlyContinue
    if ($bconn) {
        $bconn | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            Stop-Process -Id $_ -Force -EA SilentlyContinue
        }
        Write-Host "  已释放端口 $BRIDGEP" -ForegroundColor Yellow
    }
}

function Show-Status {
    $alive = Test-Server
    $conn  = Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue
    $scr   = Get-TargetScreen
    $wins  = Get-MonitorChromePids
    Write-Host ""
    Write-Host "  服务:    " -NoNewline
    if ($alive) { Write-Host "运行中 ($URL)" -ForegroundColor Green }
    else        { Write-Host "未运行" -ForegroundColor DarkGray }
    Write-Host "  端口:    " -NoNewline
    if ($conn) { Write-Host "$Port 被 PID $($conn[0].OwningProcess) 占用" -ForegroundColor Green }
    else       { Write-Host "$Port 空闲" -ForegroundColor DarkGray }
    Write-Host "  监控窗口: " -NoNewline
    if ($wins) { Write-Host "已开 ($($wins.Count) 个进程)" -ForegroundColor Green }
    else       { Write-Host "未开" -ForegroundColor DarkGray }
    Write-Host "  置顶守护: " -NoNewline
    if (Get-KeeperPids) { Write-Host "运行中" -ForegroundColor Green }
    else                { Write-Host "未运行" -ForegroundColor DarkGray }
    Write-Host "  托盘程序: " -NoNewline
    if (Get-LauncherPids) { Write-Host "运行中（右下角托盘图标）" -ForegroundColor Green }
    else                  { Write-Host "未运行" -ForegroundColor DarkGray }
    Write-Host "  LHM 采集桥: " -NoNewline
    if (Test-Bridge) {
        $src = ""
        try {
            $j = (Invoke-WebRequest ("http://127.0.0.1:$BRIDGEP/sensors") -TimeoutSec 3 -UseBasicParsing).Content | ConvertFrom-Json
            $src = " ($($j.count) 个传感器)"
        } catch {}
        Write-Host "运行中 端口 $BRIDGEP$src" -ForegroundColor Green
    } else {
        Write-Host "未运行（CPU 电压/功耗会缺）" -ForegroundColor DarkGray
    }
    Write-Host "  PawnIO 驱动: " -NoNewline
    if (Test-Path "HKLM:\SYSTEM\CurrentControlSet\Services\PawnIO") {
        Write-Host "已安装（CPU 温度/功耗/风扇可读）" -ForegroundColor Green
    } else {
        Write-Host "未安装（CPU 温度走 WMI 兜底，风扇/主板温度读不到）" -ForegroundColor Yellow
    }
    Write-Host "  目标屏:  " -NoNewline
    Write-Host "$($scr.Name)  $($scr.W)x$($scr.H)  @($($scr.X),$($scr.Y))" -ForegroundColor Cyan
    Write-Host ""
}

# ------------------------------------------------------------ 主流程
if ($Stop)   { Write-Host "`n正在停止副屏监控..." -ForegroundColor Cyan; Stop-All; Write-Host "  完成`n"; return }
if ($Quit)   {
    Write-Host "`n正在全部退出（含托盘程序）..." -ForegroundColor Cyan
    foreach ($lp in (Get-LauncherPids)) {
        Stop-Process -Id $lp -Force -EA SilentlyContinue
        Write-Host "  已退出托盘程序 PID=$lp" -ForegroundColor Yellow
    }
    Remove-Item $LPIDF -Force -EA SilentlyContinue
    Stop-All
    Write-Host "  完成`n"; return
}
if ($Status) { Show-Status; return }

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "        副屏监控  ·  大肥鱼特制版" -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

# 0. 先起 LHM 采集桥（传感器数据源，CPU 电压/功耗/GPU 热点都靠它）
Start-Bridge

# 1. 起服务
if (Test-Server) {
    Write-Host "  服务已在运行，跳过启动" -ForegroundColor Green
} else {
    if (-not (Test-Path $PY)) {
        Write-Host "  X 找不到 Python: $PY" -ForegroundColor Red
        Read-Host "  回车退出"; return
    }
    Write-Host "  启动采集服务..." -ForegroundColor Yellow
    $p = Start-Process -FilePath $PY `
        -ArgumentList @("app.py", "--port", "$Port") `
        -WorkingDirectory $ROOT `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LOG `
        -RedirectStandardError "$LOG.err" `
        -PassThru
    $p.Id | Set-Content $PIDF -Encoding ASCII

    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Server) { $ok = $true; break }
    }
    if (-not $ok) {
        Write-Host "  X 服务启动失败，看看日志：$LOG.err" -ForegroundColor Red
        Read-Host "  回车退出"; return
    }
    Write-Host "  OK 服务就绪 (PID $($p.Id))" -ForegroundColor Green
}

# 2. 开副屏窗口
$scr = Get-TargetScreen
Write-Host "  目标屏: $($scr.Name)  $($scr.W)x$($scr.H) @($($scr.X),$($scr.Y))" -ForegroundColor Cyan

if (-not $NoBrowser) {
    if (-not (Test-Path $CHROME)) {
        Write-Host "  ! 没找到 Chrome，请手动打开 $URL" -ForegroundColor Yellow
    } else {
        # 先关掉旧的监控窗口，避免开一堆
        foreach ($mp in (Get-MonitorChromePids)) { Stop-Process -Id $mp -Force -EA SilentlyContinue }
        Start-Sleep -Milliseconds 400

        # 注意：--kiosk 和 URL 必须分开成两个独立参数。
        # 写成 "--kiosk=$URL" Chrome 不认，会开一个空白新标签页（踩过这个坑）。
        $chromeArgs = @(
            "--kiosk",
            $URL,
            "--window-position=$($scr.X),$($scr.Y)",
            "--window-size=$($scr.W),$($scr.H)",
            "--user-data-dir=$PROF",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
            "--autoplay-policy=no-user-gesture-required",
            "--hide-scrollbars",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            "--force-device-scale-factor=1"
        )
        $cp = Start-Process -FilePath $CHROME -ArgumentList $chromeArgs -PassThru
        Write-Host "  OK 监控窗口已打开 (PID $($cp.Id))" -ForegroundColor Green

        # 3. 等窗口出来再置顶（Chrome 是异步创建窗口的）
        if (-not $NoTopMost) {
            $done = $false
            for ($i = 0; $i -lt 20; $i++) {
                Start-Sleep -Milliseconds 400
                $n = Set-TopMost -ProcId $cp.Id
                if ($n -gt 0) { $done = $true; break }
            }
            if ($done) { Write-Host "  OK 已置顶" -ForegroundColor Green }
            else       { Write-Host "  ! 置顶失败，窗口可能还没出来" -ForegroundColor Yellow }

            # 4. 起置顶守护：任务管理器/桌宠同样是 Topmost 时，一次性置顶压不住
            foreach ($kp in (Get-KeeperPids)) { Stop-Process -Id $kp -Force -EA SilentlyContinue }
            $kw = Start-Process -FilePath "powershell.exe" `
                -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                                "-File", "`"$KEEPER`"", "-Port", "$Port") `
                -WindowStyle Hidden -PassThru
            $kw.Id | Set-Content $KPIDF -Encoding ASCII
            Write-Host "  OK 置顶守护已启动 (PID $($kw.Id)，每 2 秒压一次)" -ForegroundColor Green
        }
    }
}

Write-Host ""
# 托盘程序默认伴随启动：kiosk 全屏没有退出入口，只能靠托盘图标右键「停止监控」
if (-not $NoTray) {
    Start-Tray
    Write-Host "  完成！托盘图标在右下角，右键可 停止监控 / 设置 / 退出" -ForegroundColor DarkGray
} else {
    Write-Host "  完成！停止请运行： .\start_monitor.ps1 -Stop" -ForegroundColor DarkGray
}
Write-Host ""
