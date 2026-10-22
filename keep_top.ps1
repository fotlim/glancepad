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
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int idx);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int idx, int val);
    [DllImport("user32.dll")] public static extern int SetWindowLongPtr(IntPtr h, int idx, IntPtr val);

    public struct RECT { public int L, T, R, B; }

    public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
    public const uint SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002, SWP_SHOWWINDOW = 0x0040;
    public const int SW_RESTORE = 9;
    public const int GWL_EXSTYLE = -20;
    public const int WS_EX_TOOLWINDOW = 0x00000080;
    public const int WS_EX_APPWINDOW = 0x00040000;

    // 从来不掉队的窗口列表
    static List<IntPtr> Hits(int pid, int minW, int minH) {
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
        return hits;
    }

    // 去掉任务栏图标 + 缩略图预览：把窗口改成工具窗口（WS_EX_TOOLWINDOW），
    // 同时清掉 WS_EX_APPWINDOW。改完任务栏不再留占位，Alt+Tab 也不再出现。
    // Chrome 每帧不一定重置样式，所以每轮都检查一遍、只在缺失时才写，避免闪烁。
    public static int HideFromTaskbar(int pid, int minW, int minH) {
        int n = 0;
        foreach (var h in Hits(pid, minW, minH)) {
            int ex = GetWindowLong(h, GWL_EXSTYLE);
            int want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW;
            if (ex != want) {
                SetWindowLong(h, GWL_EXSTYLE, want);
                n++;
            }
        }
        return n;
    }

    // 把某个进程的大窗口提到 Topmost 最顶层
    public static int Raise(int pid, int minW, int minH) {
        var hits = Hits(pid, minW, minH);
        foreach (var h in hits) {
            ShowWindow(h, SW_RESTORE);
            // 顺手补一下工具窗口样式（Chrome 偶尔会重置扩展样式）
            int ex = GetWindowLong(h, GWL_EXSTYLE);
            int want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW;
            if (ex != want) SetWindowLong(h, GWL_EXSTYLE, want);
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
            # 先从任务栏摘掉（防止 Chrome 重建窗口后图标又回来）
            [KeeperApi]::HideFromTaskbar($p, 400, 400) | Out-Null
            [KeeperApi]::Raise($p, 400, 400) | Out-Null
        }
    }
    Start-Sleep -Milliseconds $IntervalMs
}
