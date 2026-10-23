// Throwaway (spike/guide-v2-capture-calque): runs a command line at Medium integrity, from this process's own
// token lowered, as Bibo runs beside a program started as administrator. ASCII only.
using System;
using System.Runtime.InteropServices;

static class Moyen
{
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr p, uint access, out IntPtr t);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool DuplicateTokenEx(IntPtr t, uint access, IntPtr attrs, int imp, int type, out IntPtr nt);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern bool ConvertStringSidToSid(string s, out IntPtr sid);
    [DllImport("advapi32.dll")] static extern int GetLengthSid(IntPtr sid);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool SetTokenInformation(IntPtr t, int cls, ref TOKEN_MANDATORY_LABEL tml, int len);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcessAsUser(IntPtr t, string app, string cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string dir, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h, uint ms);

    [StructLayout(LayoutKind.Sequential)] struct SID_AND_ATTRIBUTES { public IntPtr Sid; public uint Attributes; }
    [StructLayout(LayoutKind.Sequential)] struct TOKEN_MANDATORY_LABEL { public SID_AND_ATTRIBUTES Label; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct STARTUPINFO
    {
        public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }
    [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }

    static int Main(string[] a)
    {
        IntPtr t, nt, sid;
        if (!OpenProcessToken(GetCurrentProcess(), 0x0001 | 0x0002 | 0x0008 | 0x0080, out t)) { Console.WriteLine("OpenProcessToken " + Marshal.GetLastWin32Error()); return 1; }
        if (!DuplicateTokenEx(t, 0x02000000, IntPtr.Zero, 2, 1, out nt)) { Console.WriteLine("DuplicateTokenEx " + Marshal.GetLastWin32Error()); return 2; }
        ConvertStringSidToSid("S-1-16-8192", out sid);
        TOKEN_MANDATORY_LABEL tml = new TOKEN_MANDATORY_LABEL();
        tml.Label.Sid = sid;
        tml.Label.Attributes = 0x20;
        if (!SetTokenInformation(nt, 25, ref tml, Marshal.SizeOf(tml) + GetLengthSid(sid))) { Console.WriteLine("SetTokenInformation " + Marshal.GetLastWin32Error()); return 3; }
        STARTUPINFO si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(si);
        PROCESS_INFORMATION pi;
        if (!CreateProcessAsUser(nt, null, a[0], IntPtr.Zero, IntPtr.Zero, false, 0, IntPtr.Zero, null, ref si, out pi)) { Console.WriteLine("CreateProcessAsUser " + Marshal.GetLastWin32Error()); return 4; }
        WaitForSingleObject(pi.hProcess, 300000);
        return 0;
    }
}
