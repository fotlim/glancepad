# ============================================================
#  副屏监控 - 置顶守护进程
#  别的窗口（任务管理器/桌宠/浏览器）也是 Topmost 时，单次置顶压不住。
#  这个脚本每 2 秒把监控窗口重新提到 Topmost 层最顶，保持可见。
#  由 start_monitor.ps1 自动拉起，不手动运行。
# ============================================================
param(
    [int]$Port = 8770,
    [int]$IntervalMs = 2000
)

$ErrorActionPreference = "SilentlyContinue"

Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class KeeperApi {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint ppid);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);

    public struct RECT { public int L, T, R, B; }

    public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
    public const uint SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002, SWP_SHOWWINDOW = 0x0040;
    public const int SW_RESTORE = 9;

    // 把某个进程的大窗口提到 Topmost 最顶层
    public static int Raise(int pid, int minW, int minH) {
        var hits = new List<IntPtr>();
        EnumWindows((h, l) => {
            uint pp; GetWindowThreadProcessId(h, out pp);
            if ((int)pp != pid) return true;
            if (!IsWindowVisible(h)) return true;
            RECT r; GetWindowRect(h, out r);
            if ((r.R - r.L) < minW || (r.B - r.T) < minH) return true;
            hits.Add(h);
            return true;
        }, IntPtr.Zero);

        foreach (var h in hits) {
            ShowWindow(h, SW_RESTORE);
            SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW);
            BringWindowToTop(h);
        }
        return hits.Count;
    }
}
"@

function Get-MonitorPids {
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
        Where-Object { $_.CommandLine -like "*127.0.0.1:$Port*" -and $_.CommandLine -like "*kiosk*" } |
        Select-Object -ExpandProperty ProcessId
}

while ($true) {
    $pids = @(Get-MonitorPids)
    if ($pids.Count -gt 0) {
        foreach ($p in $pids) {
            [KeeperApi]::Raise($p, 400, 400) | Out-Null
        }
    }
    Start-Sleep -Milliseconds $IntervalMs
}
