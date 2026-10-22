using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using LibreHardwareMonitor.Hardware;

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

internal static class Probe
{
    private static void Walk(IHardware hw, string parentPath, List<string> lines, string typeFilter)
    {
        string path = parentPath.Length == 0 ? hw.Name : parentPath + "/" + hw.Name;
        hw.Update();
        foreach (ISensor s in hw.Sensors)
        {
            string st = s.SensorType.ToString();
            if (typeFilter.Length > 0 && st != typeFilter) continue;
            string val = s.Value.HasValue ? s.Value.Value.ToString("0.###", CultureInfo.InvariantCulture) : "";
            lines.Add(JsonEsc(path) + "|" + JsonEsc(st) + "|" + JsonEsc(s.Name) + "|" + val + "|" + JsonEsc(s.Identifier.ToString()));
        }
        foreach (IHardware sub in hw.SubHardware) Walk(sub, path, lines, typeFilter);
    }

    private static string JsonEsc(string s)
    {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder();
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
                    if (c < ' ') sb.Append(' ');
                    else sb.Append(c);
                    break;
            }
        }
        return sb.ToString();
    }

    private static int Main(string[] args)
    {
        string typeFilter = "";
        bool includeBroken = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--type" && i + 1 < args.Length) typeFilter = args[++i];
            else if (args[i] == "--all") includeBroken = true;
        }

        Computer c = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMemoryEnabled = true,
            IsMotherboardEnabled = true,
            IsControllerEnabled = true,
            IsStorageEnabled = true,
            IsNetworkEnabled = false,
            IsPsuEnabled = true,
        };

        try
        {
            c.Open();
            UpdateVisitor v = new UpdateVisitor();
            c.Accept(v);

            List<string> lines = new List<string>();
            foreach (IHardware hw in c.Hardware) Walk(hw, "", lines, typeFilter);

            Console.OutputEncoding = Encoding.UTF8;
            Console.WriteLine("{\"ok\":true,\"count\":" + lines.Count + ",\"sensors\":[");
            for (int i = 0; i < lines.Count; i++)
            {
                string[] p = lines[i].Split('|');
                if (!includeBroken && p[3].Length == 0) continue;
                Console.WriteLine("{\"path\":\"" + p[0] + "\",\"type\":\"" + p[1] + "\",\"name\":\"" + p[2] + "\",\"value\":\"" + p[3] + "\",\"id\":\"" + p[4] + "\"}" + (i == lines.Count - 1 ? "" : ","));
            }
            Console.WriteLine("]}");
            return 0;
        }
        catch (Exception ex)
        {
            Console.OutputEncoding = Encoding.UTF8;
            Console.WriteLine("{\"ok\":false,\"error\":\"" + JsonEsc(ex.GetType().Name + ": " + ex.Message) + "\"}");
            return 2;
        }
        finally
        {
            try { c.Close(); } catch { }
        }
    }
}
