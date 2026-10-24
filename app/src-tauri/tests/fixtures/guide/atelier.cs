// A program for the guide's capture tests on a Windows desktop (src/capture/win.rs, the tests named
// "bureau"): plain windows of known colours, nothing else, no data. Compiled there by the csc.exe of
// the .NET Framework that comes with Windows: nothing downloaded. ASCII only: csc reads a file
// without a byte order mark in the ANSI code page.
//
//   atelier.exe fenetre <title> <x> <y> <w> <h> <rrggbb>            a window of that colour
//   atelier.exe dessus  <title> <x> <y> <w> <h> <rrggbb>            the same, always on top, never active
//   atelier.exe modal   <title> <x> <y> <w> <h> <rrggbb> <rrggbb>   a window, then a modal dialog of the
//                                                                   second colour over it
//   atelier.exe message <title> <x> <y> <w> <h> <rrggbb>            a window, then the system's message box
//   atelier.exe protege <title> <x> <y> <w> <h> <rrggbb>            a window Windows keeps out of captures
//   atelier.exe intrus  <title> <x> <y> <w> <h> <rrggbb> <hwnd>     a window owned by another program's
//
// The scale's modes, each "modal" with a green band along the window's right and bottom edges (a capture
// cut short loses it):
//   atelier.exe etiree    ...   a program unaware of the screen's scale: Windows stretches both
//   atelier.exe conscient ...   aware of each screen's (per monitor v2), as most programs are today
//   atelier.exe mixte     ...   the same, its dialog unaware of the scale (made on a thread that is)
//   atelier.exe gdi       ...   unaware, its drawing scaled by GDI ("System (Enhanced)" in Compatibility)
//   atelier.exe systeme   ...   aware of the scale the system had when it opened (SetProcessDPIAware)
using System;
using System.Drawing;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("Atelier Demo")]

static class Atelier
{
    [DllImport("user32.dll")] static extern bool SetWindowDisplayAffinity(IntPtr h, uint affinity);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] static extern IntPtr SetWindowLongPtr(IntPtr h, int index, IntPtr value);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);

    const int WS_EX_NOACTIVATE = 0x08000000;
    const uint WDA_MONITOR = 1;
    // DPI_AWARENESS_CONTEXT_UNAWARE, _PER_MONITOR_AWARE_V2 and _UNAWARE_GDISCALED.
    static readonly IntPtr Unaware = new IntPtr(-1), PerMonitor = new IntPtr(-4), GdiScaled = new IntPtr(-5);

    class Dessus : Form
    {
        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams p = base.CreateParams;
                p.ExStyle |= WS_EX_NOACTIVATE;
                return p;
            }
        }
    }

    static Color Couleur(string hex)
    {
        int v = Convert.ToInt32(hex, 16);
        return Color.FromArgb((v >> 16) & 255, (v >> 8) & 255, v & 255);
    }

    [STAThread]
    static int Main(string[] a)
    {
        string mode = a[0];
        bool scale = mode == "etiree" || mode == "conscient" || mode == "mixte" || mode == "gdi" || mode == "systeme";
        if (mode == "conscient" || mode == "mixte")
        {
            if (!SetProcessDpiAwarenessContext(PerMonitor)) return 3;
        }
        else if (mode == "gdi")
        {
            if (!SetProcessDpiAwarenessContext(GdiScaled)) return 3;
        }
        else if (mode != "etiree")
        {
            SetProcessDPIAware();
        }
        Form f = mode == "dessus" ? new Dessus() : new Form();
        f.Text = a[1];
        f.StartPosition = FormStartPosition.Manual;
        f.Bounds = new Rectangle(int.Parse(a[2]), int.Parse(a[3]), int.Parse(a[4]), int.Parse(a[5]));
        f.BackColor = Couleur(a[6]);
        if (scale)
        {
            foreach (DockStyle side in new[] { DockStyle.Bottom, DockStyle.Right })
            {
                Panel band = new Panel();
                band.Dock = side;
                band.Size = new Size(24, 24);
                band.BackColor = Color.FromArgb(0x2e, 0x7d, 0x32);
                f.Controls.Add(band);
            }
        }
        f.TopMost = mode == "dessus";
        if (mode == "intrus")
        {
            // GWLP_HWNDPARENT: the owner of a top-level window, here another program's, before it shows
            // (an owned window lies over its owner).
            SetWindowLongPtr(f.Handle, -8, new IntPtr(long.Parse(a[7])));
        }
        f.Shown += delegate
        {
            if (mode == "protege")
            {
                SetWindowDisplayAffinity(f.Handle, WDA_MONITOR);
            }
            if (mode == "modal" || scale)
            {
                f.BeginInvoke(new MethodInvoker(delegate
                {
                    Form d = new Form();
                    d.Text = "Nouvelle facture";
                    d.StartPosition = FormStartPosition.CenterParent;
                    d.Size = new Size(320, 220);
                    d.BackColor = Couleur(a[7]);
                    d.ShowInTaskbar = false;
                    d.MinimizeBox = false;
                    d.MaximizeBox = false;
                    if (mode == "mixte")
                    {
                        // Made unaware of the scale, at a place of its own counting: over its window at
                        // 100, 125 and 150 %.
                        d.StartPosition = FormStartPosition.Manual;
                        d.Location = new Point(100, 100);
                        IntPtr before = SetThreadDpiAwarenessContext(Unaware);
                        GC.KeepAlive(d.Handle);
                        SetThreadDpiAwarenessContext(before);
                    }
                    d.ShowDialog(f);
                }));
            }
            if (mode == "message")
            {
                f.BeginInvoke(new MethodInvoker(delegate
                {
                    MessageBox.Show(f, "Enregistrer les modifications ?", "Atelier", MessageBoxButtons.YesNo);
                }));
            }
        };
        Application.Run(f);
        return 0;
    }
}
