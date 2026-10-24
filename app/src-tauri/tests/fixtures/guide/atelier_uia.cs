// An ERP-like program for the tests of the guide v2's elements on a Windows desktop (src/capture/uia_win.rs,
// the tests named "uia_"). Made-up customers only. Compiled there by the csc.exe of the .NET Framework
// that comes with Windows: nothing downloaded. ASCII only: csc reads a file without a byte order mark
// in the ANSI code page.
//
//   atelier_uia.exe erp      <title> <x> <y> <w> <h> <unaware|system|pmv2>
//   atelier_uia.exe message  <title> <x> <y> <w> <h>   then the system's message box: Yes, No, Cancel
//   atelier_uia.exe question <title> <x> <y> <w> <h>   then a modal window of Oui, Non, Annuler
//   atelier_uia.exe ailleurs <title> <x> <y> <w> <h>   the same, out of every screen
//   atelier_uia.exe saisie   <title> <x> <y> <w> <h>   then a modal form: an amount, OK, Annuler
//   atelier_uia.exe erreur   <title> <x> <y> <w> <h>   then a modal error: its details read only, three buttons
//   atelier_uia.exe deux     <title> <x> <y> <w> <h>   and a second window of its own beside, owned by none
//   atelier_uia.exe foule    <title> <x> <y> <w> <h>   three hundred buttons
//   atelier_uia.exe fige     <title> <x> <y> <w> <h>   "Figer" hangs it for 15 s
//
// erp: buttons of known colours (their boxes are found again on the screen), a disabled one, a password
// field of a known colour, a menu, tabs, a check box, radio buttons, a field and a list showing a
// customer, a link, a grid, a list view and a tree of customers (data: none of it may be listed). The
// other modes: "Nouveau" and a password field of the same colour.
using System;
using System.Drawing;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("Atelier UIA")]

static class AtelierUia
{
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);

    public static readonly string[] Clients = {
        "Boulangerie Martin", "Garage Dupont", "Pharmacie Leroy", "Plomberie Moreau", "Cafe Lefebvre",
        "Fleuriste Garnier", "Librairie Fournier", "Menuiserie Girard", "Coiffure Bonnet", "Epicerie Lambert",
        "Optique Fontaine", "Traiteur Rousseau", "Cordonnerie Vincent", "Pressing Muller", "Quincaillerie Faure",
        "Boucherie Andre", "Fromagerie Mercier", "Auto-ecole Blanc", "Imprimerie Guerin", "Serrurerie Boyer",
        "Electricite Chevalier", "Toiture Francois", "Jardinerie Legrand", "Cave Gauthier", "Patisserie Perrin",
        "Bijouterie Robin", "Opticien Clement", "Tabac Morin", "Brasserie Nicolas", "Atelier Henry"
    };

    static Color C(int rgb) { return Color.FromArgb((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255); }

    static Button Flat(string text, int rgb, int x, int y, int w, int h)
    {
        Button b = new Button();
        b.Text = text;
        b.FlatStyle = FlatStyle.Flat;
        b.FlatAppearance.BorderSize = 0;
        b.BackColor = C(rgb);
        b.ForeColor = Color.White;
        b.TabStop = false;
        b.Bounds = new Rectangle(x, y, w, h);
        return b;
    }

    static Label Lab(string text, int x, int y)
    {
        Label l = new Label();
        l.Text = text;
        l.AutoSize = true;
        l.Location = new Point(x, y);
        return l;
    }

    static void Erp(Form f)
    {
        MenuStrip menu = new MenuStrip();
        foreach (string m in new[] { "Fichier", "Edition", "Aide" }) menu.Items.Add(new ToolStripMenuItem(m));
        ((ToolStripMenuItem)menu.Items[0]).DropDownItems.Add("Imprimer la facture");
        f.MainMenuStrip = menu;

        f.Controls.AddRange(new Control[] {
            Flat("Nouveau", 0x2e7d32, 16, 40, 120, 36),
            Flat("Enregistrer", 0x8e24aa, 150, 40, 120, 36),
            Flat("Supprimer", 0xe53935, 284, 40, 120, 36),
            Flat("Exporter toutes les factures du trimestre vers le cabinet comptable", 0x6d4c41, 418, 40, 220, 36),
        });
        Button archive = Flat("Archiver", 0x9e9e9e, 760, 128, 110, 28);
        archive.Enabled = false;
        f.Controls.Add(archive);

        CheckBox paid = new CheckBox();
        paid.Text = "Payee";
        paid.Bounds = new Rectangle(660, 46, 80, 24);
        RadioButton ht = new RadioButton();
        ht.Text = "HT";
        ht.Bounds = new Rectangle(750, 46, 50, 24);
        ht.Checked = true;
        RadioButton ttc = new RadioButton();
        ttc.Text = "TTC";
        ttc.Bounds = new Rectangle(800, 46, 60, 24);
        f.Controls.AddRange(new Control[] { paid, ht, ttc });

        f.Controls.Add(Lab("Client :", 16, 98));
        TextBox client = new TextBox();
        client.Bounds = new Rectangle(90, 94, 200, 24);
        client.Text = "Garage Dupont";
        f.Controls.Add(client);

        f.Controls.Add(Lab("Mot de passe :", 310, 98));
        f.Controls.Add(Secret(410, 94));

        f.Controls.Add(Lab("Facturer a :", 590, 98));
        ComboBox who = new ComboBox();
        who.DropDownStyle = ComboBoxStyle.DropDownList;
        who.Bounds = new Rectangle(670, 94, 200, 24);
        who.Items.AddRange(Clients);
        who.SelectedIndex = 0;
        f.Controls.Add(who);

        LinkLabel open = new LinkLabel();
        open.Text = "Ouvrir la fiche";
        open.AutoSize = true;
        open.Location = new Point(16, 132);
        f.Controls.Add(open);

        TabControl tabs = new TabControl();
        tabs.Bounds = new Rectangle(16, 160, 860, 420);
        TabPage invoices = new TabPage("Factures");
        TabPage people = new TabPage("Clients");
        tabs.TabPages.Add(invoices);
        tabs.TabPages.Add(people);

        DataGridView grid = new DataGridView();
        grid.Bounds = new Rectangle(4, 4, 420, 380);
        grid.AllowUserToAddRows = false;
        grid.RowHeadersVisible = false;
        grid.Columns.Add("client", "Client");
        grid.Columns.Add("montant", "Montant");
        for (int i = 0; i < Clients.Length; i++) grid.Rows.Add(Clients[i], (120 + 37 * i) + ",00");
        invoices.Controls.Add(grid);

        ListView list = new ListView();
        list.View = View.Details;
        list.Bounds = new Rectangle(432, 4, 412, 380);
        list.Columns.Add("Client", 220);
        list.Columns.Add("Ville", 160);
        for (int i = 0; i < Clients.Length; i++) list.Items.Add(new ListViewItem(new[] { Clients[i], "Ville " + i }));
        invoices.Controls.Add(list);

        TreeView tree = new TreeView();
        tree.Bounds = new Rectangle(4, 4, 400, 380);
        foreach (string c in Clients) tree.Nodes.Add(c);
        people.Controls.Add(tree);

        f.Controls.Add(tabs);
        f.Controls.Add(menu);
        f.Shown += delegate { client.Focus(); };
    }

    static TextBox Secret(int x, int y)
    {
        TextBox secret = new TextBox();
        secret.Bounds = new Rectangle(x, y, 160, 24);
        secret.BorderStyle = BorderStyle.FixedSingle;
        secret.BackColor = C(0xff9800);
        secret.UseSystemPasswordChar = true;
        secret.Text = "invente-0000";
        return secret;
    }

    // A modal window of the program's own, its owner waiting: a choice, a form, or an error.
    static void Dialog(Form owner, string mode)
    {
        bool form = mode == "saisie";
        Form d = new Form();
        d.Text = form ? "Nouveau reglement" : "Atelier";
        d.StartPosition = FormStartPosition.CenterParent;
        if (mode == "ailleurs")
        {
            d.StartPosition = FormStartPosition.Manual;
            d.Location = new Point(-3000, 100);
        }
        d.Size = new Size(360, 200);
        d.ShowInTaskbar = false;
        d.MinimizeBox = false;
        d.MaximizeBox = false;
        if (mode == "erreur")
        {
            // As .NET's own error window: what happened in a box one reads and copies, never types in.
            d.Size = new Size(420, 260);
            d.Controls.Add(Lab("Une erreur est survenue.", 16, 12));
            TextBox details = new TextBox();
            details.Multiline = true;
            details.ReadOnly = true;
            details.Bounds = new Rectangle(16, 36, 370, 100);
            details.Text = "System.InvalidOperationException: exemple invente\r\n   a Atelier.Enregistrer()";
            d.Controls.Add(details);
            string[] actions = { "Details", "Continuer", "Quitter" };
            for (int i = 0; i < actions.Length; i++)
            {
                Button b = new Button();
                b.Text = actions[i];
                b.Bounds = new Rectangle(16 + 124 * i, 160, 116, 30);
                d.Controls.Add(b);
            }
        }
        else if (form)
        {
            d.Controls.Add(Lab("Montant :", 16, 24));
            TextBox amount = new TextBox();
            amount.Bounds = new Rectangle(100, 20, 160, 24);
            d.Controls.Add(amount);
            Button ok = new Button();
            ok.Text = "OK";
            ok.Bounds = new Rectangle(100, 100, 90, 30);
            Button cancel = new Button();
            cancel.Text = "Annuler";
            cancel.Bounds = new Rectangle(200, 100, 90, 30);
            d.Controls.AddRange(new Control[] { ok, cancel });
        }
        else
        {
            d.Controls.Add(Lab("Enregistrer les modifications ?", 16, 24));
            string[] choices = { "Oui", "Non", "Annuler" };
            for (int i = 0; i < choices.Length; i++)
            {
                Button b = new Button();
                b.Text = choices[i];
                b.Bounds = new Rectangle(16 + 104 * i, 100, 96, 30);
                d.Controls.Add(b);
            }
        }
        d.ShowDialog(owner);
    }

    // Another window of the same program and thread, owned by none: as Edge's second window.
    static void Autre(Form f)
    {
        Form o = new Form();
        o.Text = f.Text + " (autre)";
        o.StartPosition = FormStartPosition.Manual;
        o.AutoScaleMode = AutoScaleMode.None;
        o.Bounds = new Rectangle(f.Right + 20, f.Top, 400, 300);
        o.BackColor = C(0x787878);
        o.Controls.Add(Flat("Autre fenetre", 0x1e88e5, 16, 16, 160, 36));
        o.Controls.Add(Flat("Garage Dupont", 0x1e88e5, 16, 64, 160, 36));
        o.Show();
    }

    static void Foule(Form f)
    {
        FlowLayoutPanel p = new FlowLayoutPanel();
        p.Bounds = new Rectangle(0, 60, f.ClientSize.Width, f.ClientSize.Height - 60);
        for (int i = 1; i <= 300; i++)
        {
            Button b = new Button();
            b.Text = "B" + i.ToString("000");
            b.Size = new Size(46, 22);
            b.Margin = new Padding(1);
            p.Controls.Add(b);
        }
        f.Controls.Add(p);
    }

    [STAThread]
    static int Main(string[] a)
    {
        string mode = a[0];
        if (mode != "erp" || a[6] == "system") SetProcessDPIAware();
        if (mode == "erp" && a[6] == "pmv2") SetProcessDpiAwarenessContext(new IntPtr(-4));
        Application.EnableVisualStyles();
        Form f = new Form();
        f.Text = a[1];
        f.StartPosition = FormStartPosition.Manual;
        f.AutoScaleMode = AutoScaleMode.None;
        f.Bounds = new Rectangle(int.Parse(a[2]), int.Parse(a[3]), int.Parse(a[4]), int.Parse(a[5]));
        f.BackColor = C(0x787878);
        if (mode == "erp")
        {
            Erp(f);
        }
        else
        {
            f.Controls.Add(Flat("Nouveau", 0x2e7d32, 16, 16, 120, 36));
            f.Controls.Add(Secret(150, 22));
            if (mode == "fige")
            {
                Button hang = Flat("Figer", 0x6d4c41, 330, 16, 120, 36);
                hang.Click += delegate { System.Threading.Thread.Sleep(15000); };
                f.Controls.Add(hang);
            }
            if (mode == "foule") Foule(f);
            f.Shown += delegate
            {
                f.BeginInvoke(new MethodInvoker(delegate
                {
                    if (mode == "message") MessageBox.Show(f, "Enregistrer les modifications ?", "Atelier", MessageBoxButtons.YesNoCancel);
                    if (mode == "question" || mode == "ailleurs" || mode == "saisie" || mode == "erreur") Dialog(f, mode);
                    if (mode == "deux") Autre(f);
                }));
            };
        }
        Application.Run(f);
        return 0;
    }
}
