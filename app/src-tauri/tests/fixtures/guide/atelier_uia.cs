// Throwaway (spike/guide-v2-uia): an ERP-like window for the UIA list of the guide v2 (W2). Invented
// clients only. Compiled on the VM by Windows' own csc. ASCII only: csc reads a file without a byte
// order mark in the ANSI code page.
//
//   atelier_uia.exe <title> <x> <y> <w> <h> <unaware|system|pmv2>
//
// Buttons of known colours (their boxes are found again in the window's image), a password field of a
// known colour, a menu, tabs, a check box, radio buttons, a field and a list showing a client, a grid,
// a list view and a tree of clients (data: none of it may reach the guide's list).
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

    [STAThread]
    static int Main(string[] a)
    {
        if (a[5] == "system") SetProcessDPIAware();
        if (a[5] == "pmv2") SetProcessDpiAwarenessContext(new IntPtr(-4));
        Application.EnableVisualStyles();
        Form f = new Form();
        f.Text = a[0];
        f.StartPosition = FormStartPosition.Manual;
        f.AutoScaleMode = AutoScaleMode.None;
        f.Bounds = new Rectangle(int.Parse(a[1]), int.Parse(a[2]), int.Parse(a[3]), int.Parse(a[4]));
        f.BackColor = C(0x787878);

        MenuStrip menu = new MenuStrip();
        foreach (string m in new[] { "Fichier", "Edition", "Aide" }) menu.Items.Add(new ToolStripMenuItem(m));
        f.MainMenuStrip = menu;

        Control[] top = {
            Flat("Nouveau", 0x2e7d32, 16, 40, 120, 36),
            Flat("Enregistrer", 0x8e24aa, 150, 40, 120, 36),
            Flat("Supprimer", 0xe53935, 284, 40, 120, 36),
            Flat("Exporter toutes les factures du trimestre vers le cabinet comptable", 0x6d4c41, 418, 40, 220, 36),
        };
        f.Controls.AddRange(top);

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
        TextBox secret = new TextBox();
        secret.Bounds = new Rectangle(410, 94, 160, 24);
        secret.BorderStyle = BorderStyle.FixedSingle;
        secret.BackColor = C(0xff9800);
        secret.UseSystemPasswordChar = true;
        secret.Text = "invente-0000";
        f.Controls.Add(secret);

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
        Application.Run(f);
        return 0;
    }
}
