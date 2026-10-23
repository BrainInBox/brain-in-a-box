// Throwaway (spike/guide-v2-capture-calque): windows of known colours for the P0 proofs W1, W3, W4 of the
// guide v2. The modes of app/src-tauri/tests/fixtures/guide/atelier.cs, plus four. Compiled on the VM by
// the csc.exe of the .NET Framework that comes with Windows. ASCII only.
//
//   atelier.exe fenetre <title> <x> <y> <w> <h> <rrggbb>        a window of that colour
//   atelier.exe dessus  <title> <x> <y> <w> <h> <rrggbb>        the same, always on top, never active
//   atelier.exe appli   <title> <x> <y> <w> <h> <rrggbb>        a window, its palette (teal, owned, partly
//                                                               outside it) and a modal dialog (orange); on
//                                                               the dialog, a right click opens the system's
//                                                               menu, a left click a strip menu (purple)
//   atelier.exe bouton  <title> <x> <y> <w> <h> <rrggbb>        a window with a grey button; each click says
//                                                               where it landed in the title
//   atelier.exe vivant  <title> <x> <y> <w> <h> <rrggbb>        a clock, a spinner, a marquee bar, a blinking
//                                                               caret; « Nouveau » opens a dialog, white for
//                                                               200 ms, then orange; Escape closes it
using System;
using System.Drawing;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("Atelier Demo")]

static class Atelier
{
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    const int WS_EX_NOACTIVATE = 0x08000000;

    static readonly Color Teal = Color.FromArgb(0x00, 0x89, 0x7b);
    static readonly Color Purple = Color.FromArgb(0x8e, 0x24, 0xaa);
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

    static void Appli(Form f)
    {
        f.Shown += delegate
        {
            Form palette = new Form();
            palette.Text = "Palette";
            palette.StartPosition = FormStartPosition.Manual;
            palette.Bounds = new Rectangle(f.Right - 120, f.Bottom - 120, 300, 220);
            palette.BackColor = Teal;
            palette.ShowInTaskbar = false;
            palette.Show(f);
            f.BeginInvoke(new MethodInvoker(delegate
            {
                Form d = new Form();
                d.Text = "Nouvelle facture";
                d.StartPosition = FormStartPosition.Manual;
                d.Bounds = new Rectangle(f.Left + 40, f.Top + 60, 320, 220);
                d.BackColor = Orange;
                d.ShowInTaskbar = false;
                d.MinimizeBox = false;
                d.MaximizeBox = false;
                ContextMenu natif = new ContextMenu();
                natif.MenuItems.Add("Copier");
                natif.MenuItems.Add("Coller");
                natif.MenuItems.Add("Supprimer la ligne");
                ContextMenuStrip bande = new ContextMenuStrip();
                bande.BackColor = Purple;
                bande.ShowImageMargin = false;
                for (int i = 0; i < 4; i++)
                {
                    ToolStripMenuItem item = new ToolStripMenuItem("Action " + i);
                    item.BackColor = Purple;
                    item.ForeColor = Purple;
                    item.AutoSize = false;
                    item.Size = new Size(180, 30);
                    bande.Items.Add(item);
                }
                d.MouseClick += delegate(object s, MouseEventArgs e)
                {
                    if (e.Button == MouseButtons.Right) natif.Show(d, e.Location);
                    else bande.Show(d, e.Location);
                };
                d.ShowDialog(f);
            }));
        };
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

        ProgressBar barre = new ProgressBar();
        barre.Style = ProgressBarStyle.Marquee;
        barre.MarqueeAnimationSpeed = 30;
        barre.Bounds = new Rectangle(20, f.ClientSize.Height - 40, 220, 18);
        f.Controls.Add(barre);

        TextBox saisie = new TextBox();
        saisie.Bounds = new Rectangle(20, 100, 260, 24);
        saisie.Name = "saisie";
        f.Controls.Add(saisie);

        Button nouveau = new Button();
        nouveau.Text = "Nouveau";
        nouveau.Bounds = new Rectangle(20, 20, 160, 40);
        nouveau.BackColor = Color.White;
        nouveau.Click += delegate
        {
            Form d = new Form();
            d.Text = "Nouvelle facture";
            d.StartPosition = FormStartPosition.Manual;
            d.Bounds = new Rectangle(f.Left + 200, f.Top + 140, 360, 240);
            d.BackColor = Color.White;
            d.ShowInTaskbar = false;
            d.MinimizeBox = false;
            d.MaximizeBox = false;
            Button fermer = new Button();
            fermer.Text = "Fermer";
            fermer.Bounds = new Rectangle(240, 160, 90, 30);
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
            horloge.Text = DateTime.Now.ToString("HH:mm:ss");
        };
        t.Start();
        f.Shown += delegate { saisie.Focus(); };
    }

    [STAThread]
    static int Main(string[] a)
    {
        SetProcessDPIAware();
        Application.EnableVisualStyles();
        string mode = a[0];
        Form f = mode == "dessus" ? new Dessus() : new Form();
        f.Text = a[1];
        f.StartPosition = FormStartPosition.Manual;
        f.Bounds = new Rectangle(int.Parse(a[2]), int.Parse(a[3]), int.Parse(a[4]), int.Parse(a[5]));
        f.BackColor = Couleur(a[6]);
        f.TopMost = mode == "dessus";
        if (mode == "appli") Appli(f);
        if (mode == "bouton") Bouton(f, a[1]);
        if (mode == "vivant") Vivant(f);
        Application.Run(f);
        return 0;
    }
}
