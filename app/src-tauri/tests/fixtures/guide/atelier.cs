// A program for the guide's capture tests on a Windows desktop (src/capture/win.rs, the tests named
// "bureau"; src/capture/vm.rs, the guide v2's): plain windows of known colours, nothing else, no data. Compiled there by the csc.exe of
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
//   atelier.exe bouton  <title> <x> <y> <w> <h> <rrggbb>            a grey button "Nouveau"; each click
//                                                                   says where it landed in the title
//   atelier.exe vivant  <title> <x> <y> <w> <h> <rrggbb>            a clock with seconds, a spinner, a
//                                                                   moving bar, a blinking caret; "Nouveau"
//                                                                   opens a 400 x 200 dialog, white for
//                                                                   200 ms, then orange; Escape closes it
//   atelier.exe secret  <title> <x> <y> <w> <h> <rrggbb>            an orange password field (ES_PASSWORD)
//                                                                   and a plain one
//   atelier.exe video   <title> <x> <y> <w> <h> <rrggbb>            grey blocks that change every 40 ms
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

    const int WS_EX_NOACTIVATE = 0x08000000;
    const uint WDA_MONITOR = 1;

    static readonly Color Orange = Color.FromArgb(0xff, 0x98, 0x00);
    static readonly Color Grey = Color.FromArgb(0x78, 0x78, 0x78);

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

    static void Bouton(Form f, string titre)
    {
        int n = 0;
        Button b = new Button();
        b.Text = "Nouveau";
        b.Bounds = new Rectangle(40, 40, 200, 60);
        b.BackColor = Grey;
        b.FlatStyle = FlatStyle.Flat;
        b.Click += delegate { n++; f.Text = titre + " clic-bouton " + n; };
        f.Controls.Add(b);
        f.MouseClick += delegate { n++; f.Text = titre + " clic-fond " + n; };
    }

    static void Vivant(Form f)
    {
        Label horloge = new Label();
        horloge.Bounds = new Rectangle(f.ClientSize.Width - 150, 10, 140, 24);
        horloge.Font = new Font("Segoe UI", 12);
        horloge.ForeColor = Color.White;
        f.Controls.Add(horloge);

        int angle = 0;
        Panel tourne = new Panel();
        tourne.Bounds = new Rectangle(f.ClientSize.Width - 60, 50, 40, 40);
        tourne.Paint += delegate(object s, PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (Pen p = new Pen(Color.White, 5)) e.Graphics.DrawArc(p, 5, 5, 28, 28, angle, 270);
        };
        f.Controls.Add(tourne);

        Panel piste = new Panel();
        piste.Bounds = new Rectangle(20, f.ClientSize.Height - 40, 220, 18);
        piste.BackColor = Color.White;
        Panel barre = new Panel();
        barre.Bounds = new Rectangle(0, 0, 60, 18);
        barre.BackColor = Grey;
        piste.Controls.Add(barre);
        f.Controls.Add(piste);

        TextBox saisie = new TextBox();
        saisie.Bounds = new Rectangle(20, 100, 260, 24);
        f.Controls.Add(saisie);

        Button nouveau = new Button();
        nouveau.Text = "Nouveau";
        nouveau.Bounds = new Rectangle(20, 20, 160, 40);
        nouveau.BackColor = Color.White;
        nouveau.Click += delegate
        {
            Form d = new Form();
            d.Text = "Nouvelle facture";
            d.FormBorderStyle = FormBorderStyle.FixedDialog;
            d.StartPosition = FormStartPosition.Manual;
            d.Bounds = new Rectangle(f.Left + 200, f.Top + 140, 400, 200);
            d.BackColor = Color.White;
            d.ShowInTaskbar = false;
            d.MinimizeBox = false;
            d.MaximizeBox = false;
            Button fermer = new Button();
            fermer.Text = "Fermer";
            fermer.Bounds = new Rectangle(280, 110, 90, 30);
            fermer.BackColor = Color.White;
            fermer.DialogResult = DialogResult.Cancel;
            d.Controls.Add(fermer);
            d.CancelButton = fermer;
            Timer charge = new Timer();
            charge.Interval = 200;
            charge.Tick += delegate { charge.Stop(); d.BackColor = Orange; };
            d.Shown += delegate { charge.Start(); };
            d.ShowDialog(f);
            charge.Dispose();
        };
        f.Controls.Add(nouveau);

        Timer t = new Timer();
        t.Interval = 100;
        t.Tick += delegate
        {
            angle = (angle + 30) % 360;
            tourne.Invalidate();
            barre.Left = (barre.Left + 8) % piste.Width;
            horloge.Text = DateTime.Now.ToString("HH:mm:ss");
        };
        t.Start();
        f.Shown += delegate { saisie.Focus(); };
    }

    static void Secret(Form f)
    {
        TextBox mdp = new TextBox();
        mdp.AutoSize = false;
        mdp.Bounds = new Rectangle(20, 20, 300, 40);
        mdp.BackColor = Orange;
        mdp.UseSystemPasswordChar = true;
        f.Controls.Add(mdp);
        TextBox nom = new TextBox();
        nom.AutoSize = false;
        nom.Bounds = new Rectangle(20, 80, 300, 40);
        nom.BackColor = Grey;
        f.Controls.Add(nom);
    }

    static void Video(Form f)
    {
        Random hasard = new Random(7);
        Bitmap image = new Bitmap(32, 18);
        PictureBox ecran = new PictureBox();
        ecran.Dock = DockStyle.Fill;
        ecran.SizeMode = PictureBoxSizeMode.StretchImage;
        f.Controls.Add(ecran);
        Timer t = new Timer();
        t.Interval = 40;
        t.Tick += delegate
        {
            for (int y = 0; y < image.Height; y++)
                for (int x = 0; x < image.Width; x++)
                {
                    int g = hasard.Next(256);
                    image.SetPixel(x, y, Color.FromArgb(g, g, g));
                }
            ecran.Image = image;
            ecran.Invalidate();
        };
        t.Start();
    }

    [STAThread]
    static int Main(string[] a)
    {
        SetProcessDPIAware();
        string mode = a[0];
        Form f = mode == "dessus" ? new Dessus() : new Form();
        f.Text = a[1];
        f.StartPosition = FormStartPosition.Manual;
        f.Bounds = new Rectangle(int.Parse(a[2]), int.Parse(a[3]), int.Parse(a[4]), int.Parse(a[5]));
        f.BackColor = Couleur(a[6]);
        f.TopMost = mode == "dessus";
        if (mode == "intrus")
        {
            // GWLP_HWNDPARENT: the owner of a top-level window, here another program's, before it shows
            // (an owned window lies over its owner).
            SetWindowLongPtr(f.Handle, -8, new IntPtr(long.Parse(a[7])));
        }
        if (mode == "bouton") Bouton(f, a[1]);
        if (mode == "vivant") Vivant(f);
        if (mode == "secret") Secret(f);
        if (mode == "video") Video(f);
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
