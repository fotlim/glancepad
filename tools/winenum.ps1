# 列出所有可见且面积够大的顶层窗口，带扩展样式标志
# 用法: powershell -NoProfile -File winenum.ps1 [-Pid N] [-MinSize 300]
param([int]$ProcId = 0, [int]$MinSize = 300)

$src = @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WEnum {
  delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
  [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  struct RECT { public int L,T,R,B; }
  public static string Dump(int wantPid, int minSize) {
    var sb = new StringBuilder();
    EnumWindows((h,p) => {
      if (!IsWindowVisible(h)) return true;
      int pid; GetWindowThreadProcessId(h, out pid);
      if (wantPid > 0 && pid != wantPid) return true;
      var r = new RECT(); GetWindowRect(h, out r);
      int w = r.R-r.L, hh = r.B-r.T;
      if (w < minSize || hh < minSize) return true;
      int ex = GetWindowLong(h, -20);
      var t = new StringBuilder(256); GetWindowTextW(h, t, 256);
      sb.AppendLine(string.Format("hwnd={0} pid={1} {2}x{3} @({4},{5}) ex=0x{6:X8} TOOL={7} APP={8} title={9}",
        h, pid, w, hh, r.L, r.T, ex, (ex & 0x80)!=0, (ex & 0x40000)!=0, t.ToString()));
      return true;
    }, IntPtr.Zero);
    return sb.ToString();
  }
}
'@
Add-Type -TypeDefinition $src
[WEnum]::Dump($ProcId, $MinSize)
