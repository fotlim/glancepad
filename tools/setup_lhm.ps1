# ============================================================
#  副屏监控 - LibreHardwareMonitor 采集桥一键部署
#
#  做什么：
#    1. 下载 LibreHardwareMonitor v0.9.6（官方 release）
#    2. 解压到 tools\LHM\
#    3. 用 .NET Framework 自带的 csc.exe 编译 lhm_bridge.exe / lhm_probe.exe
#    4. 检查 PawnIO 内核驱动（没有就提示怎么装）
#
#  用法：
#     .\tools\setup_lhm.ps1              正常部署
#     .\tools\setup_lhm.ps1 -Force       重下重编译
#     .\tools\setup_lhm.ps1 -SkipDownload  只用现有 tools\LHM 里的 dll 编译
#
#  说明：
#    - 仓库里不放第三方二进制（体积大、且 LHM 有自己的 MPL-2.0 许可）。
#    - LHM 只用于「读传感器」，本脚本只下官方 zip，不修改它。
# ============================================================
param(
    [switch]$Force,
    [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LHMDIR = Join-Path $ROOT "tools\LHM"
$CSC = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$LHM_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"
$LIB = Join-Path $LHMDIR "LibreHardwareMonitorLib.dll"

Write-Host "== 副屏监控 · LHM 采集桥部署 ==" -ForegroundColor Cyan
Write-Host "   目标目录: $LHMDIR"
Write-Host ""

# ---------- 0. 前置检查 ----------
if (-not (Test-Path $CSC)) {
    Write-Host "! 找不到 .NET Framework 编译器：" -ForegroundColor Red
    Write-Host "    $CSC"
    Write-Host "  请安装 .NET Framework 4.7.2 或更高版本（Win10/11 一般自带）。"
    exit 1
}

# ---------- 1. 下载并解压 LHM ----------
if ($SkipDownload) {
    Write-Host "  跳过下载（-SkipDownload）" -ForegroundColor Yellow
} elseif ((Test-Path $LIB) -and (-not $Force)) {
    Write-Host "  LibreHardwareMonitorLib.dll 已存在，跳过下载（要重下加 -Force）" -ForegroundColor Green
} else {
    $zip = Join-Path $env:TEMP "LibreHardwareMonitor-0.9.6.zip"
    Write-Host "  下载 LibreHardwareMonitor v0.9.6 ..." -ForegroundColor Yellow
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $LHM_URL -OutFile $zip -UseBasicParsing
    $size = (Get-Item $zip).Length
    Write-Host "  下载完成: $([math]::Round($size/1MB,2)) MB" -ForegroundColor Green

    if (Test-Path $LHMDIR) { Remove-Item $LHMDIR -Recurse -Force }
    New-Item -ItemType Directory -Path $LHMDIR -Force | Out-Null
    Expand-Archive -Path $zip -DestinationPath $LHMDIR -Force
    Remove-Item $zip -Force -EA SilentlyContinue
    Write-Host "  已解压到 tools\LHM\" -ForegroundColor Green
}

if (-not (Test-Path $LIB)) {
    Write-Host "! 没找到 $LIB，无法编译桥接" -ForegroundColor Red
    exit 1
}

# ---------- 2. 编译 ----------
# 注意：LHM 的 dll 是从网络下载的，Windows 会打上「来自其他计算机」标记，
#       Add-Type -Path 会抛 FileLoadException(0x80131515)。
#       用 csc 直接引用编译出独立 exe 不会被这个标记影响。
Get-ChildItem $LHMDIR -Filter "*.exe" -EA SilentlyContinue | Unblock-File -EA SilentlyContinue
Get-ChildItem $LHMDIR -Filter "*.dll" -EA SilentlyContinue | Unblock-File -EA SilentlyContinue

foreach ($pair in @(@("lhm_bridge.cs", "lhm_bridge.exe"), @("lhm_probe.cs", "lhm_probe.exe"))) {
    $src = Join-Path (Join-Path $ROOT "tools") $pair[0]
    $out = Join-Path $LHMDIR $pair[1]
    if (-not (Test-Path $src)) { continue }
    if ((Test-Path $out) -and (-not $Force)) {
        Write-Host "  $($pair[1]) 已存在，跳过编译（要重编加 -Force）" -ForegroundColor Green
        continue
    }
    Write-Host "  编译 $($pair[1]) ..." -ForegroundColor Yellow
    & $CSC /nologo /target:exe /platform:anycpu /optimize+ `
        "/out:$out" "/r:$LIB" $src
    if ($LASTEXITCODE -ne 0) {
        Write-Host "! 编译 $($pair[1]) 失败（exit $LASTEXITCODE）" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK $out" -ForegroundColor Green
}

# ---------- 3. PawnIO 驱动检查 ----------
Write-Host ""
$pawnio = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\PawnIO" -EA SilentlyContinue
if ($pawnio) {
    $svc = Get-Service PawnIO -EA SilentlyContinue
    Write-Host "  PawnIO 驱动: 已安装 ($($svc.Status))" -ForegroundColor Green
} else {
    Write-Host "  PawnIO 驱动: 未安装" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  LHM v0.9.6 用 PawnIO 作为 ring0 后端。没装驱动时：" -ForegroundColor Yellow
    Write-Host "    · CPU 温度 / 功耗 / 频率、主板温度、风扇转速 全部读不到（显示 --）"
    Write-Host "    · GPU / 内存 / 网络 数据不受影响（走 NVAPI、WMI、PDH）"
    Write-Host ""
    Write-Host "  安装方法：" -ForegroundColor Cyan
    Write-Host "    1. 打开 https://github.com/namazso/PawnIO.Setup/releases/latest"
    Write-Host "    2. 下载 PawnIO_setup.exe，右键「以管理员身份运行」"
    Write-Host "    3. 安装完重启本脚本（或直接跑 start_monitor.ps1）"
    Write-Host ""
    Write-Host "  提示：PawnIO 安装器界面是自绘的，弹窗不代表没装；" -ForegroundColor DarkGray
    Write-Host "        装完用  Get-Service PawnIO  看是否 Running。"
}

Write-Host ""
Write-Host "== 完成 ==" -ForegroundColor Cyan
Write-Host "  下一步: .\start_monitor.ps1" -ForegroundColor White
