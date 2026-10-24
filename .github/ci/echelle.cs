// The CI's Windows screen at a size and a scale, as the Settings app sets them (no sign-out), for the
// guide's capture tests at 125 and 150 % (test.yml, job bureau-echelle). Compiled on the runner by
// Windows' own csc. ASCII only: csc reads a file without a byte order mark in the ANSI code page.
//
//   echelle.exe <width> <height> <percent>
//
// The size by ChangeDisplaySettings, then the scale by DisplayConfigSetDeviceInfo (type -4, the call
// behind Settings > Display > Scale, relative to the recommended one); done once a per-monitor thread
// sees the screen at that DPI.
using System;
using System.Runtime.InteropServices;

static class Echelle
{
    [StructLayout(LayoutKind.Sequential)]
    struct LUID { public uint Low; public int High; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH
    {
        public LUID srcAdapter; public uint srcId; public uint srcMode; public uint srcFlags;
        public LUID tgtAdapter; public uint tgtId; public uint tgtMode; public int tech; public int rot; public int scaling;
        public uint refN; public uint refD; public int scan; public int avail; public uint tgtFlags;
        public uint flags;
    }

    [StructLayout(LayoutKind.Sequential, Size = 64)]
    struct MODE { public int infoType; public uint id; public LUID adapter; }

    [StructLayout(LayoutKind.Sequential)]
    struct HEADER { public int type; public int size; public LUID adapter; public uint id; }

    [StructLayout(LayoutKind.Sequential)]
    struct DPI_GET { public HEADER h; public int min; public int cur; public int max; }

    [StructLayout(LayoutKind.Sequential)]
    struct DPI_SET { public HEADER h; public int rel; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct DEVMODE
    {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public short dmSpecVersion; public short dmDriverVersion; public short dmSize; public short dmDriverExtra; public int dmFields;
        public int dmPositionX; public int dmPositionY; public int dmDisplayOrientation; public int dmDisplayFixedOutput;
        public short dmColor; public short dmDuplex; public short dmYResolution; public short dmTTOption; public short dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public short dmLogPixels; public int dmBitsPerPel; public int dmPelsWidth; public int dmPelsHeight; public int dmDisplayFlags; public int dmDisplayFrequency;
        public int dmICMMethod; public int dmICMIntent; public int dmMediaType; public int dmDitherType; public int dmReserved1; public int dmReserved2;
        public int dmPanningWidth; public int dmPanningHeight;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int x; public int y; }

    [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint paths, out uint modes);
    [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint np, [Out] PATH[] p, ref uint nm, [Out] MODE[] m, IntPtr topo);
    [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref DPI_GET g);
    [DllImport("user32.dll")] static extern int DisplayConfigSetDeviceInfo(ref DPI_SET s);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool EnumDisplaySettings(string dev, int i, ref DEVMODE dm);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int ChangeDisplaySettings(ref DEVMODE dm, int flags);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr ctx);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromPoint(POINT p, uint flags);
    [DllImport("shcore.dll")] static extern int GetDpiForMonitor(IntPtr mon, int type, out uint x, out uint y);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);

    // The steps of Settings > Display > Scale; the device's range is counted from its recommended one.
    static readonly int[] Scales = { 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500 };

    static PATH[] Paths()
    {
        uint np, nm;
        int r = GetDisplayConfigBufferSizes(2, out np, out nm);
        if (r != 0) throw new Exception("GetDisplayConfigBufferSizes " + r);
        PATH[] p = new PATH[np];
        MODE[] m = new MODE[nm];
        r = QueryDisplayConfig(2, ref np, p, ref nm, m, IntPtr.Zero);
        if (r != 0) throw new Exception("QueryDisplayConfig " + r);
        Array.Resize(ref p, (int)np);
        return p;
    }

    // The screen's DPI and size as a per-monitor thread (DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) sees them.
    static uint ScreenDpi()
    {
        IntPtr before = SetThreadDpiAwarenessContext(new IntPtr(-4));
        uint x, y;
        GetDpiForMonitor(MonitorFromPoint(new POINT(), 1), 0, out x, out y);
        int w = GetSystemMetrics(0), h = GetSystemMetrics(1);
        SetThreadDpiAwarenessContext(before);
        Console.WriteLine("screen " + w + "x" + h + ", " + x + " dpi");
        return x;
    }

    static int Main(string[] a)
    {
        int width = int.Parse(a[0]), height = int.Parse(a[1]), percent = int.Parse(a[2]);
        DEVMODE dm = new DEVMODE();
        dm.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE));
        EnumDisplaySettings(null, -1, ref dm);
        dm.dmPelsWidth = width;
        dm.dmPelsHeight = height;
        dm.dmFields = 0x80000 | 0x100000;
        int r = ChangeDisplaySettings(ref dm, 0);
        Console.WriteLine("ChangeDisplaySettings " + width + "x" + height + ": " + r);
        if (r != 0) return 1;

        int want = Array.IndexOf(Scales, percent);
        if (want < 0) { Console.WriteLine("not a scale: " + percent); return 2; }
        foreach (PATH p in Paths())
        {
            DPI_GET g = new DPI_GET();
            g.h.type = -3;
            g.h.size = Marshal.SizeOf(typeof(DPI_GET));
            g.h.adapter = p.srcAdapter;
            g.h.id = p.srcId;
            r = DisplayConfigGetDeviceInfo(ref g);
            if (r != 0) { Console.WriteLine("DisplayConfigGetDeviceInfo " + r); return 1; }
            int rel = want + g.min;
            if (rel > g.max) { Console.WriteLine("source " + p.srcId + ": " + percent + " % is over its max"); return 2; }
            DPI_SET s = new DPI_SET();
            s.h.type = -4;
            s.h.size = Marshal.SizeOf(typeof(DPI_SET));
            s.h.adapter = p.srcAdapter;
            s.h.id = p.srcId;
            s.rel = rel;
            r = DisplayConfigSetDeviceInfo(ref s);
            Console.WriteLine("source " + p.srcId + ": " + percent + " %: " + r);
            if (r != 0) return 1;
        }
        for (int i = 0; i < 50; i++)
        {
            if (ScreenDpi() == (uint)(96 * percent / 100)) return 0;
            System.Threading.Thread.Sleep(200);
        }
        return 3;
    }
}
