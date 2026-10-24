<#
.SYNOPSIS
  组装安装包用的「自包含 Python 运行时」。

.DESCRIPTION
  GlancePad 的 exe 安装包要自带 Python，用户机器才不用预装。
  官方 embeddable 发行版是**精简版**：没有 tkinter、没有 pip、也没有
  pip 装出来的包。而 launcher.pyw 的设置窗口全靠 tkinter/ttk，
  漏了它就会出现「双击没反应」或「右键设置打不开」——v1.2.1 就踩过这个坑。

  所以这里把三件事一起做掉：
    1. 解压官方 embeddable zip
    2. 从同版本的完整安装补上 tkinter 全家桶（_tkinter.pyd / tk86t.dll /
       tcl86t.dll / Lib\tkinter / tcl\），并在 python311._pth 里加一行 Lib
       （embeddable 的 sys.path 默认不含 Lib，光拷贝还是 import 不到）
    3. 用系统 Python 的 pip 把 requirements.txt 装到 Lib\site-packages

  版本必须对得上：python311.dll 的 FileVersion 要一致，否则 _tkinter.pyd 加载会崩。

.PARAMETER PyEmbedZip
  官方 embeddable zip，例如 python-3.11.9-embed-amd64.zip

.PARAMETER DonorPython
  同版本的完整 Python 安装目录（提供 tkinter 与 pip），
  例如 C:\Users\me\AppData\Local\Programs\Python\Python311

.PARAMETER Target
  输出目录，例如 %TEMP%\gp_build\python

.PARAMETER Requirements
  requirements.txt 路径；省略则只装运行时、不装依赖

.EXAMPLE
  .\make_embed_python.ps1 -PyEmbedZip "$env:TEMP\pyembed.zip" `
      -DonorPython "$env:LOCALAPPDATA\Programs\Python\Python311" `
      -Target "$env:TEMP\gp_build\python" `
      -Requirements ..\requirements.txt
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PyEmbedZip,
    [Parameter(Mandatory = $true)][string]$DonorPython,
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$Requirements = ""
)

$ErrorActionPreference = "Stop"

function Say($m) { Write-Host "[embed-python] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[embed-python] ERROR: $m" -ForegroundColor Red; exit 1 }

# ---- 前置检查 ----
if (-not (Test-Path $PyEmbedZip))   { Fail "找不到 embeddable zip: $PyEmbedZip" }
if (-not (Test-Path $DonorPython))  { Fail "找不到 donor Python: $DonorPython" }

$donorExe = Join-Path $DonorPython "python.exe"
if (-not (Test-Path $donorExe)) { Fail "donor 里没有 python.exe: $donorExe" }

$donorTk = Join-Path $DonorPython "Lib\tkinter"
$donorTcl = Join-Path $DonorPython "tcl"
foreach ($p in @($donorTk, $donorTcl)) {
    if (-not (Test-Path $p)) { Fail "donor 缺少 tkinter 组件: $p" }
}

# ---- 版本一致性：embeddable 与 donor 的 python311.dll 必须是同一版 ----
$zipTmp = Join-Path ([System.IO.Path]::GetTempPath()) ("gpz_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $zipTmp -Force | Out-Null
try {
    Say "解压 $PyEmbedZip"
    Expand-Archive -LiteralPath $PyEmbedZip -DestinationPath $zipTmp -Force

    $zipDll = Join-Path $zipTmp "python311.dll"
    if (-not (Test-Path $zipDll)) { Fail "embeddable zip 里没有 python311.dll，版本不对？" }

    $donorDll = Join-Path $DonorPython "python311.dll"
    if (Test-Path $donorDll) {
        $v1 = (Get-Item $zipDll).VersionInfo.FileVersion
        $v2 = (Get-Item $donorDll).VersionInfo.FileVersion
        Say "python311.dll 版本：zip=$v1  donor=$v2"
        if ($v1 -ne $v2) {
            Fail "版本不一致！tkinter 的 pyd 与 dll 必须同版本，否则加载会崩。请换同版本的 donor。"
        }
    }

    # ---- 落地到 Target ----
    if (Test-Path $Target) {
        Say "清空已存在的 $Target"
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    Say "复制运行时 -> $Target"
    Copy-Item -Path (Join-Path $zipTmp "*") -Destination $Target -Recurse -Force

    # ---- 补 tkinter ----
    Say "补 tkinter（pyd / dll / Lib\tkinter / tcl）"
    foreach ($f in @("_tkinter.pyd", "tk86t.dll", "tcl86t.dll")) {
        $s = Join-Path $DonorPython "DLLs\$f"
        if (-not (Test-Path $s)) { $s = Join-Path $DonorPython $f }
        if (-not (Test-Path $s)) { Fail "donor 里找不到 $f" }
        # embeddable 没有 DLLs 目录，pyd/dll 必须和 python.exe 同级
        Copy-Item $s (Join-Path $Target $f) -Force
    }
    Copy-Item $donorTk (Join-Path $Target "Lib\tkinter") -Recurse -Force
    Copy-Item $donorTcl (Join-Path $Target "tcl") -Recurse -Force

    # ---- patch _pth：embeddable 的 sys.path 里没有 Lib / site-packages，必须自己加 ----
    $pth = Join-Path $Target "python311._pth"
    if (-not (Test-Path $pth)) { Fail "找不到 python311._pth" }
    $lines = Get-Content $pth -Encoding UTF8
    Say "原 _pth: $($lines -join ' | ')"

    # 只保留实际生效的路径行（跳过空行与 # 注释），再补上缺的两条。
    # 注意别把 "#import site" 那类注释行当路径留下，也别把注释弄丢——直接重建更稳。
    $keep = @()
    foreach ($l in $lines) {
        $t = $l.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        if ($keep -notcontains $t) { $keep += $t }
    }
    foreach ($need in @("Lib", "Lib\site-packages")) {
        if ($keep -notcontains $need) { $keep += $need }
    }
    $final = New-Object System.Collections.Generic.List[string]
    foreach ($k in $keep) { $final.Add($k) }
    $final.Add("import site")
    [System.IO.File]::WriteAllLines($pth, $final, (New-Object System.Text.UTF8Encoding($false)))
    Say "新 _pth: $($keep -join ' | ') | import site"

    # ---- pip 装依赖 ----
    if ($Requirements -and (Test-Path $Requirements)) {
        Say "安装依赖: $Requirements"
        $sitePackages = Join-Path $Target "Lib\site-packages"
        New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
        & $donorExe -m pip install --no-warn-script-location --disable-pip-version-check `
            --target $sitePackages -r $Requirements
        if ($LASTEXITCODE -ne 0) { Fail "pip install 失败 (exit $LASTEXITCODE)" }
    }

    # ---- 自检：tkinter 与依赖必须真的能用 ----
    Say "自检 tkinter 与依赖"
    $probe = Join-Path $Target "_tkcheck.py"
    $probeBody = @(
        'import tkinter',
        'from tkinter import ttk, filedialog, messagebox',
        'r = tkinter.Tk(); r.withdraw()',
        'print("TK_OK", ttk.__file__)',
        'print("TCL", tkinter.Tcl().eval("info library"))',
        'r.destroy()',
        'import flask, psutil, requests, PIL, pystray, winsdk, pycaw, comtypes',
        'print("DEPS_OK")'
    )
    [System.IO.File]::WriteAllLines($probe, $probeBody, (New-Object System.Text.UTF8Encoding($false)))
    try {
        $check = & (Join-Path $Target "python.exe") $probe 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) { Fail "tkinter 自检失败：`n$check" }
    Say ($check -join "`n")
    Say "完成 -> $Target"
}
finally {
    if (Test-Path $zipTmp) { Remove-Item $zipTmp -Recurse -Force -ErrorAction SilentlyContinue }
}
