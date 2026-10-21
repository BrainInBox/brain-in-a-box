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

    const int WS_EX_NOACTIVATE = 0x08000000;
    const uint WDA_MONITOR = 1;

    class Intrus : Form
    {
        public IntPtr Proprietaire;

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams p = base.CreateParams;
                p.Parent = Proprietaire;
                return p;
            }
        }
    }

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
        SetProcessDPIAware();
        string mode = a[0];
        Form f;
        if (mode == "dessus")
        {
            f = new Dessus();
        }
        else if (mode == "intrus")
        {
            Intrus i = new Intrus();
            i.Proprietaire = new IntPtr(long.Parse(a[7]));
            f = i;
        }
        else
        {
            f = new Form();
        }
        f.Text = a[1];
        f.StartPosition = FormStartPosition.Manual;
        f.Bounds = new Rectangle(int.Parse(a[2]), int.Parse(a[3]), int.Parse(a[4]), int.Parse(a[5]));
        f.BackColor = Couleur(a[6]);
        f.TopMost = mode == "dessus";
        f.Shown += delegate
        {
            if (mode == "protege")
            {
                SetWindowDisplayAffinity(f.Handle, WDA_MONITOR);
            }
            if (mode == "modal")
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
