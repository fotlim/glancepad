using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using LibreHardwareMonitor.Hardware;

// LHM 常驻桥接服务：开一次 Computer，后台线程周期 Update，
// 通过本机 HTTP 暴露 /sensors（JSON），供 Python 采集层零成本轮询。
// 用法: lhm_bridge.exe --port 8771 --period 1000
internal sealed class UpdateVisitor : IVisitor
{
    public void VisitComputer(IComputer computer) { computer.Traverse(this); }
    public void VisitHardware(IHardware hardware)
    {
        hardware.Update();
        foreach (IHardware sub in hardware.SubHardware) sub.Accept(this);
    }
    public void VisitSensor(ISensor sensor) { }
    public void VisitParameter(IParameter parameter) { }
}

internal static class Bridge
{
    private static Computer _computer;
    private static volatile bool _running = true;
    private static int _period = 1000;
    private static long _ticks;
    private static string _lastError = "";
    private static readonly object _lock = new object();

    private static string Esc(string s)
    {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder(s.Length + 8);
        foreach (char c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < ' ') sb.Append(' '); else sb.Append(c);
                    break;
            }
        }
        return sb.ToString();
    }

    private static string Num(float? v)
    {
        if (!v.HasValue) return "null";
        double d = v.Value;
        if (double.IsNaN(d) || double.IsInfinity(d)) return "null";
        return d.ToString("0.###", CultureInfo.InvariantCulture);
    }

    private static void Walk(IHardware hw, string parent, StringBuilder sb, ref int count)
    {
        string path = parent.Length == 0 ? hw.Name : parent + "/" + hw.Name;
        foreach (ISensor s in hw.Sensors)
        {
            if (count > 0) sb.Append(",");
            sb.Append("{\"hw\":\"").Append(Esc(hw.Name))
              .Append("\",\"hwType\":\"").Append(Esc(hw.HardwareType.ToString()))
              .Append("\",\"path\":\"").Append(Esc(path))
              .Append("\",\"type\":\"").Append(Esc(s.SensorType.ToString()))
              .Append("\",\"name\":\"").Append(Esc(s.Name))
              .Append("\",\"id\":\"").Append(Esc(s.Identifier.ToString()))
              .Append("\",\"value\":").Append(Num(s.Value))
              .Append("}");
            count++;
        }
        foreach (IHardware sub in hw.SubHardware) Walk(sub, path, sb, ref count);
    }

    private static void UpdaterLoop()
    {
        UpdateVisitor v = new UpdateVisitor();
        while (_running)
        {
            try
            {
                lock (_lock) { _computer.Accept(v); }
                _ticks = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                _lastError = "";
            }
            catch (Exception ex) { _lastError = ex.GetType().Name + ": " + ex.Message; }
            Thread.Sleep(_period);
        }
    }

    private static string BuildSensorsJson()
    {
        StringBuilder sb = new StringBuilder(16384);
        lock (_lock)
        {
            sb.Append("{\"ok\":true,\"ts\":").Append(_ticks)
              .Append(",\"error\":\"").Append(Esc(_lastError)).Append("\",\"sensors\":[");
            int count = 0;
            foreach (IHardware hw in _computer.Hardware) Walk(hw, "", sb, ref count);
            sb.Append("],\"count\":").Append(count).Append("}");
        }
        return sb.ToString();
    }

    private static int Main(string[] args)
    {
        int port = 8771;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--port" && i + 1 < args.Length) int.TryParse(args[++i], out port);
            else if (args[i] == "--period" && i + 1 < args.Length) int.TryParse(args[++i], out _period);
        }
        if (port <= 0) port = 8771;
        if (_period < 250) _period = 250;

        _computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMemoryEnabled = true,
            IsMotherboardEnabled = true,
            IsControllerEnabled = true,
            IsStorageEnabled = true,
            IsNetworkEnabled = true,
            IsPsuEnabled = true,
        };
        _computer.Open();

        // 预热两轮，让差分型传感器（功耗/占用/平均频率）出有效值
        UpdateVisitor v = new UpdateVisitor();
        for (int i = 0; i < 3; i++) { _computer.Accept(v); Thread.Sleep(300); }

        Thread t = new Thread(UpdaterLoop) { IsBackground = true };
        t.Start();

        // HttpListener 在某些 .NET 运行时上会抛 PlatformNotSupportedException，
        // 这里改用裸 TcpListener 实现最小 HTTP 服务，零依赖、绝对可用。
        System.Net.Sockets.TcpListener listener = new System.Net.Sockets.TcpListener(IPAddress.Loopback, port);
        try { listener.Start(); }
        catch (Exception ex)
        {
            Console.Error.WriteLine("listen failed: " + ex.Message);
            return 3;
        }
        Console.WriteLine("lhm-bridge listening on http://127.0.0.1:" + port + "/ period=" + _period + "ms");

        while (_running)
        {
            System.Net.Sockets.TcpClient client;
            try { client = listener.AcceptTcpClient(); }
            catch { break; }
            try { HandleClient(client); }
            catch { }
            finally { try { client.Close(); } catch { } }
        }
        try { listener.Stop(); } catch { }
        try { _computer.Close(); } catch { }
        return 0;
    }

    private static void HandleClient(System.Net.Sockets.TcpClient client)
    {
        client.ReceiveTimeout = 5000;
        client.SendTimeout = 5000;
        using (System.Net.Sockets.NetworkStream ns = client.GetStream())
        {
            // 读请求行（只要第一行就够）
            StringBuilder head = new StringBuilder();
            byte[] one = new byte[1];
            int nl = 0;
            int guard = 0;
            while (nl < 2 && guard++ < 16384)
            {
                int n = ns.Read(one, 0, 1);
                if (n <= 0) break;
                char c = (char)one[0];
                if (c == '\n') nl++; else if (c == '\r') { } else nl = 0;
                if (head.Length < 4096) head.Append(c);
            }

            string firstLine = head.ToString().Split('\n')[0].Trim();
            string[] parts = firstLine.Split(' ');
            string rawPath = parts.Length >= 2 ? parts[1] : "/";
            int q = rawPath.IndexOf('?');
            if (q >= 0) rawPath = rawPath.Substring(0, q);
            string path = rawPath.TrimEnd('/');
            if (path.Length == 0) path = "";

            int code = 200;
            string body;
            if (path == "/sensors" || path == "")
            {
                body = BuildSensorsJson();
            }
            else if (path == "/status")
            {
                body = "{\"ok\":true,\"ts\":" + _ticks + ",\"period\":" + _period +
                       ",\"hardware\":" + HardwareNames() + ",\"error\":\"" + Esc(_lastError) + "\"}";
            }
            else if (path == "/quit")
            {
                body = "{\"ok\":true}";
                _running = false;
            }
            else
            {
                code = 404;
                body = "{\"ok\":false,\"error\":\"not found\"}";
            }

            byte[] buf = Encoding.UTF8.GetBytes(body);
            string header = "HTTP/1.1 " + code + (code == 200 ? " OK" : " Not Found") + "\r\n" +
                            "Content-Type: application/json; charset=utf-8\r\n" +
                            "Cache-Control: no-store\r\n" +
                            "Content-Length: " + buf.Length + "\r\n" +
                            "Connection: close\r\n\r\n";
            byte[] hb = Encoding.ASCII.GetBytes(header);
            ns.Write(hb, 0, hb.Length);
            ns.Write(buf, 0, buf.Length);
            ns.Flush();
        }
    }

    private static string HardwareNames()
    {
        StringBuilder sb = new StringBuilder("[");
        lock (_lock)
        {
            bool first = true;
            foreach (IHardware hw in _computer.Hardware)
            {
                if (!first) sb.Append(",");
                first = false;
                sb.Append("{\"name\":\"").Append(Esc(hw.Name))
                  .Append("\",\"type\":\"").Append(Esc(hw.HardwareType.ToString())).Append("\"}");
            }
        }
        return sb.Append("]").ToString();
    }
}
