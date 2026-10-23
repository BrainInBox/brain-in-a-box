// Preuve M1 (guide v2, P0) : des ecrans qui imitent Sage 100 Gestion commerciale, en WinForms, sur la
// VM de CI. Chaque ecran est montre sur le bureau de la VM, capture (sa fenetre seulement), puis
// l'arbre UIA de toutes les fenetres du processus est lu comme Bibo le lirait (spec v2 3, C2) :
// commandes seulement (Button, MenuItem, TabItem, Hyperlink, CheckBox, RadioButton, ComboBox, Edit),
// jamais une ligne ni une cellule. Tout est invente. Compile la-bas par le csc du .NET Framework :
//   csc /codepage:65001 /target:winexe /r:UIAutomationClient.dll /r:UIAutomationTypes.dll /r:WindowsBase.dll sage.cs
//   sage.exe <dossier>     ecrit <id>.png et <id>.json pour chaque ecran
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Forms;

static class Sage
{
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT r, int size);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }

    static Control pump;
    static string dir;
    static Font police = new Font("Segoe UI", 9f);

    class Ecran
    {
        public string Id;
        public Func<Form> Construire;
        public Action<Form> Apres;          // ouvre un menu, un dialogue... sur le fil de l'interface
        public string VeriteType, VeriteNom; // l'element attendu (type UIA, nom)
        public Func<Form, Rectangle> Zone;   // ou une zone (une ligne de tableau), en coordonnees d'ecran
        public bool Bloquant;                // Apres ouvre un dialogue modal
    }

    static Bitmap Icone(Color c, string lettre)
    {
        Bitmap b = new Bitmap(24, 24);
        using (Graphics g = Graphics.FromImage(b))
        {
            g.Clear(Color.Transparent);
            using (Brush br = new SolidBrush(c)) g.FillRectangle(br, 2, 2, 20, 20);
            using (Font f = new Font("Segoe UI", 9f, FontStyle.Bold)) g.DrawString(lettre, f, Brushes.White, 5, 3);
        }
        return b;
    }

    static ToolStripButton Bouton(string texte, Color c)
    {
        ToolStripButton b = new ToolStripButton(texte, Icone(c, texte.Substring(0, 1)));
        b.TextImageRelation = TextImageRelation.ImageAboveText;
        b.ImageScaling = ToolStripItemImageScaling.None;
        return b;
    }

    static ToolStripMenuItem traitement;

    static Form Principale(string titre)
    {
        Form f = new Form();
        f.Text = "Sage 100cloud Gestion commerciale - BOULANGERIE EXEMPLE" + (titre == null ? "" : " - [" + titre + "]");
        f.IsMdiContainer = true;
        f.StartPosition = FormStartPosition.Manual;
        // The runner's screen is 1024x768 at 100 %.
        f.Bounds = new Rectangle(8, 8, 1000, 720);
        f.Font = police;
        f.TopMost = true;
        MenuStrip m = new MenuStrip();
        foreach (string s in new[] { "Fichier", "Edition", "Structure", "Traitement", "Documents des ventes", "Documents des achats", "Documents des stocks", "Etat", "Fenêtre", "?" })
        {
            ToolStripMenuItem it = new ToolStripMenuItem(s.Replace("Edition", "Édition").Replace("Etat", "État"));
            m.Items.Add(it);
            if (s == "Traitement")
            {
                traitement = it;
                foreach (string t in new[] { "Transformer des documents", "Saisie des règlements clients", "Mise à jour des tarifs", "Inventaire", "Clôture des documents", "Contrôle de l'encours" })
                    it.DropDownItems.Add(new ToolStripMenuItem(t));
            }
            else
            {
                it.DropDownItems.Add(new ToolStripMenuItem("..."));
            }
        }
        ToolStrip outils = new ToolStrip();
        outils.ImageScalingSize = new Size(24, 24);
        outils.Items.Add(Bouton("Clients", Color.SteelBlue));
        outils.Items.Add(Bouton("Fournisseurs", Color.SeaGreen));
        outils.Items.Add(Bouton("Articles", Color.DarkOrange));
        outils.Items.Add(new ToolStripSeparator());
        outils.Items.Add(Bouton("Ventes", Color.IndianRed));
        outils.Items.Add(Bouton("Achats", Color.MediumPurple));
        outils.Items.Add(Bouton("Règlements", Color.Teal));
        outils.Items.Add(Bouton("Statistiques", Color.SlateGray));
        f.Controls.Add(outils);
        f.Controls.Add(m);
        f.MainMenuStrip = m;
        return f;
    }

    static Form Fille(Form parent, string titre, int w, int h)
    {
        Form c = new Form();
        c.MdiParent = parent;
        c.Text = titre;
        c.Font = police;
        c.StartPosition = FormStartPosition.Manual;
        c.Location = new Point(10, 10);
        c.Size = new Size(w, h);
        return c;
    }

    static ToolStrip OutilsFille(params string[] noms)
    {
        ToolStrip t = new ToolStrip();
        t.ImageScalingSize = new Size(24, 24);
        Color[] cs = { Color.SeaGreen, Color.SteelBlue, Color.Firebrick, Color.DimGray, Color.DarkOrange, Color.MediumPurple };
        int i = 0;
        foreach (string n in noms)
        {
            if (n.StartsWith("?"))
            {
                ToolStripTextBox tb = new ToolStripTextBox();
                tb.AccessibleName = n.Substring(1);
                tb.TextBox.AccessibleName = n.Substring(1);
                tb.Size = new Size(200, 25);
                t.Items.Add(new ToolStripLabel(n.Substring(1)));
                t.Items.Add(tb);
            }
            else
            {
                t.Items.Add(Bouton(n, cs[i++ % cs.Length]));
            }
        }
        return t;
    }

    static DataGridView Grille(string[] colonnes, string[][] lignes)
    {
        DataGridView g = new DataGridView();
        g.Dock = DockStyle.Fill;
        g.ReadOnly = true;
        g.AllowUserToAddRows = false;
        g.RowHeadersVisible = false;
        g.BackgroundColor = Color.White;
        g.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
        g.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
        foreach (string c in colonnes) g.Columns.Add(c, c);
        foreach (string[] l in lignes) g.Rows.Add(l);
        g.ClearSelection();
        return g;
    }

    static string[][] Documents()
    {
        return new[] {
            new[] { "Facture", "FA00115", "14/10/2026", "Boulangerie Martin", "1 240,00", "Comptabilisée" },
            new[] { "Facture", "FA00116", "15/10/2026", "Atelier Durand", "380,50", "Imprimée" },
            new[] { "Devis", "DE00042", "15/10/2026", "Fleurs de Lise", "2 015,00", "Saisi" },
            new[] { "Bon de livraison", "BL00077", "16/10/2026", "Garage du Port", "96,00", "Imprimé" },
            new[] { "Facture", "FA00117", "17/10/2026", "Café des Arts", "1 108,80", "Saisie" },
            new[] { "Facture", "FA00118", "18/10/2026", "Pressing Lumière", "64,90", "Comptabilisée" },
            new[] { "Facture", "FA00119", "19/10/2026", "Imprimerie Vogel", "3 450,00", "Imprimée" },
            new[] { "Devis", "DE00043", "20/10/2026", "Boucherie Noël", "512,00", "Saisi" },
            new[] { "Facture", "FA00120", "21/10/2026", "Librairie Ombre", "88,40", "Saisie" },
            new[] { "Bon de commande", "BC00031", "22/10/2026", "Épicerie Sana", "640,00", "Confirmé" },
        };
    }

    static readonly string[] ColDocs = { "Type", "N° pièce", "Date", "Client", "Total TTC", "Statut" };

    static string[][] Clients(bool deuxMartin)
    {
        List<string[]> l = new List<string[]> {
            new[] { "CATELIER", "Atelier Durand", "Villeurbanne", "04 78 00 10 02" },
            new[] { "CBOUCH", "Boucherie Noël", "Lyon", "04 78 00 10 09" },
            new[] { "CBOULMA", "Boulangerie Martin", "Lyon", "04 78 00 10 01" },
            new[] { "CCAFE", "Café des Arts", "Lyon", "04 78 00 10 06" },
            new[] { "CDUPONT", "Dupont Fleurs", "Écully", "04 78 00 10 05" },
            new[] { "CEPICE", "Épicerie Sana", "Bron", "04 78 00 10 11" },
            new[] { "CFLEURS", "Fleurs de Lise", "Bron", "04 78 00 10 03" },
            new[] { "CGARAGE", "Garage du Port", "Lyon", "04 78 00 10 04" },
        };
        if (deuxMartin) l.Insert(5, new[] { "CMARTPL", "Martin Plomberie", "Caluire", "04 78 00 10 12" });
        return l.ToArray();
    }

    static readonly string[] ColClients = { "Numéro", "Intitulé", "Ville", "Téléphone" };

    static Form ListeDocs(out Form fille)
    {
        Form f = Principale("Documents des ventes");
        fille = Fille(f, "Documents des ventes", 960, 520);
        fille.Controls.Add(Grille(ColDocs, Documents()));
        fille.Controls.Add(OutilsFille("Nouveau", "Ouvrir", "Supprimer", "Imprimer", "Transformer"));
        return f;
    }

    static Form ListeClients(bool deuxMartin, bool recherche)
    {
        Form f = Principale("Clients");
        Form c = Fille(f, "Clients", 960, 520);
        c.Controls.Add(Grille(ColClients, Clients(deuxMartin)));
        if (recherche) c.Controls.Add(OutilsFille("Nouveau", "Ouvrir", "Supprimer", "Imprimer", "?Rechercher"));
        else c.Controls.Add(OutilsFille("Nouveau", "Ouvrir", "Supprimer", "Imprimer"));
        return f;
    }

    static void Champ(Control parent, string libelle, Control champ, int x, int y)
    {
        Label l = new Label();
        l.Text = libelle;
        l.Location = new Point(x, y + 3);
        l.AutoSize = true;
        parent.Controls.Add(l);
        champ.Location = new Point(x + 95, y);
        champ.Width = 180;
        champ.AccessibleName = libelle;
        parent.Controls.Add(champ);
    }

    static TextBox Texte(string v)
    {
        TextBox t = new TextBox();
        t.Text = v;
        return t;
    }

    static ComboBox Liste(string v)
    {
        ComboBox c = new ComboBox();
        c.DropDownStyle = ComboBoxStyle.DropDownList;
        c.Items.Add(v);
        if (v.Length > 0) c.SelectedIndex = 0;
        return c;
    }

    static Form Piece(string type, bool remplie, bool devis)
    {
        Form f = Principale(type);
        Form c = Fille(f, type + (remplie ? (devis ? " : DE00044" : " : FA00121") : " : Nouveau document"), 960, 540);
        Panel entete = new Panel();
        entete.Dock = DockStyle.Top;
        entete.Height = 110;
        ComboBox client = new ComboBox();
        client.DropDownStyle = ComboBoxStyle.DropDown;
        if (remplie) client.Text = "CDUPONT";
        Champ(entete, "Client", client, 12, 12);
        Champ(entete, "Date", Texte("23/10/2026"), 12, 44);
        Champ(entete, "N° pièce", Texte(devis ? "DE00044" : "FA00121"), 330, 12);
        Champ(entete, "Référence", Texte(""), 330, 44);
        Champ(entete, "Dépôt", Liste("Bièvres"), 650, 12);
        Champ(entete, "Affaire", Liste(""), 650, 44);
        Panel bas = new Panel();
        bas.Dock = DockStyle.Bottom;
        bas.Height = 64;
        Label tot = new Label();
        tot.Text = remplie ? "Total HT 350,00     TVA 19,25     Net à payer 369,25 EUR" : "Total HT 0,00     TVA 0,00     Net à payer 0,00 EUR";
        tot.Location = new Point(12, 22);
        tot.AutoSize = true;
        bas.Controls.Add(tot);
        Button enr = new Button();
        enr.Text = "Enregistrer";
        enr.Location = new Point(700, 16);
        enr.Size = new Size(110, 32);
        Button fer = new Button();
        fer.Text = "Fermer";
        fer.Location = new Point(820, 16);
        fer.Size = new Size(100, 32);
        bas.Controls.Add(enr);
        bas.Controls.Add(fer);
        string[][] lignes = remplie ? new[] {
            new[] { "PAIN-CAMP", "Pain de campagne 1 kg", "40", "4,20", "168,00" },
            new[] { "CROIS-PB", "Croissant pur beurre", "120", "1,10", "132,00" },
            new[] { "LIVR-MAT", "Livraison matinale", "4", "12,50", "50,00" } } : new string[0][];
        c.Controls.Add(Grille(new[] { "Référence article", "Désignation", "Quantité", "P.U. HT", "Montant HT" }, lignes));
        c.Controls.Add(bas);
        c.Controls.Add(entete);
        if (devis) c.Controls.Add(OutilsFille("Nouveau", "Supprimer", "Imprimer", "Transformer", "Dupliquer"));
        return f;
    }

    static Form FicheClient()
    {
        Form f = Principale("Client");
        Form c = Fille(f, "Client : CDUPONT - Dupont Fleurs", 900, 520);
        TabControl tabs = new TabControl();
        tabs.Dock = DockStyle.Fill;
        foreach (string t in new[] { "Identification", "Complément", "Contacts", "Banques", "Champs libres", "Statistiques" })
            tabs.TabPages.Add(t);
        TabPage p = tabs.TabPages[0];
        Champ(p, "Numéro", Texte("CDUPONT"), 16, 20);
        Champ(p, "Intitulé", Texte("Dupont Fleurs"), 16, 52);
        Champ(p, "Adresse", Texte("12 rue des Lilas"), 16, 84);
        Champ(p, "Code postal", Texte("69130"), 16, 116);
        Champ(p, "Ville", Texte("Écully"), 16, 148);
        Champ(p, "Catégorie tarifaire", Liste("Tarif professionnels"), 420, 20);
        Champ(p, "Mode de règlement", Liste("Virement 30 jours"), 420, 52);
        c.Controls.Add(tabs);
        Panel bas = new Panel();
        bas.Dock = DockStyle.Bottom;
        bas.Height = 50;
        Button ok = new Button(); ok.Text = "OK"; ok.Location = new Point(680, 10); ok.Size = new Size(90, 30);
        Button an = new Button(); an.Text = "Annuler"; an.Location = new Point(780, 10); an.Size = new Size(90, 30);
        bas.Controls.Add(ok); bas.Controls.Add(an);
        c.Controls.Add(bas);
        return f;
    }

    static Form Options()
    {
        Form f = Principale("Paramètres");
        Form c = Fille(f, "Paramètres société - Options de saisie", 760, 460);
        string[] cases = { "Contrôler l'encours client", "Afficher les articles en sommeil", "Proposer la saisie des contacts",
                           "Afficher les totaux en devise", "Bloquer les documents imprimés", "Saisir les numéros de série" };
        for (int i = 0; i < cases.Length; i++)
        {
            CheckBox cb = new CheckBox();
            cb.Text = cases[i];
            cb.AutoSize = true;
            cb.Location = new Point(30, 30 + i * 36);
            cb.Checked = i == 0 || i == 4;
            c.Controls.Add(cb);
        }
        Button ok = new Button(); ok.Text = "OK"; ok.Location = new Point(520, 360); ok.Size = new Size(90, 30);
        Button an = new Button(); an.Text = "Annuler"; an.Location = new Point(620, 360); an.Size = new Size(90, 30);
        c.Controls.Add(ok); c.Controls.Add(an);
        return f;
    }

    static Rectangle LigneDe(Form f, string piece)
    {
        foreach (Form c in f.MdiChildren)
            foreach (Control x in c.Controls)
            {
                DataGridView g = x as DataGridView;
                if (g == null) continue;
                foreach (DataGridViewRow r in g.Rows)
                    if ((string)r.Cells[1].Value == piece)
                        return g.RectangleToScreen(g.GetRowDisplayRectangle(r.Index, false));
            }
        throw new Exception("ligne " + piece);
    }

    static List<Ecran> Ecrans()
    {
        Form tmp;
        List<Ecran> l = new List<Ecran>();
        l.Add(new Ecran { Id = "s01", Construire = () => ListeDocs(out tmp), VeriteType = "Button", VeriteNom = "Nouveau" });
        l.Add(new Ecran { Id = "s02", Construire = () => Piece("Facture", false, false), VeriteType = "ComboBox", VeriteNom = "Client" });
        l.Add(new Ecran { Id = "s03", Construire = () => ListeClients(false, false), VeriteType = "Button", VeriteNom = "Imprimer" });
        l.Add(new Ecran { Id = "s04", Construire = FicheClient, VeriteType = "TabItem", VeriteNom = "Contacts" });
        l.Add(new Ecran { Id = "s05", Construire = () => ListeDocs(out tmp), Apres = f => traitement.ShowDropDown(), VeriteType = "MenuItem", VeriteNom = "Saisie des règlements clients" });
        l.Add(new Ecran { Id = "s06", Construire = Options, VeriteType = "CheckBox", VeriteNom = "Afficher les articles en sommeil" });
        l.Add(new Ecran { Id = "s07", Construire = () => Piece("Facture", true, false), VeriteType = "Button", VeriteNom = "Enregistrer" });
        l.Add(new Ecran { Id = "s08", Construire = () => ListeClients(false, true), VeriteType = "Edit", VeriteNom = "Rechercher" });
        l.Add(new Ecran { Id = "s09", Construire = () => Piece("Devis", true, true), VeriteType = "Button", VeriteNom = "Transformer" });
        l.Add(new Ecran { Id = "s10", Construire = () => ListeDocs(out tmp), Zone = f => LigneDe(f, "FA00119") });
        l.Add(new Ecran { Id = "n_absent", Construire = () => ListeDocs(out tmp) });
        l.Add(new Ecran { Id = "n_ambigu", Construire = () => ListeClients(true, false) });
        l.Add(new Ecran
        {
            Id = "n_dialogue", Construire = () => Piece("Devis", true, true), Bloquant = true,
            Apres = Dialogue
        });
        return l;
    }

    // Sage's question before printing a changed document, as a French Windows shows it (the runner's
    // MessageBox would say Yes, No, Cancel).
    static void Dialogue(Form f)
    {
        Form d = new Form();
        d.Text = "Sage 100cloud";
        d.FormBorderStyle = FormBorderStyle.FixedDialog;
        d.MinimizeBox = false;
        d.MaximizeBox = false;
        d.ShowInTaskbar = false;
        d.StartPosition = FormStartPosition.CenterParent;
        d.ClientSize = new Size(440, 140);
        d.Font = police;
        d.TopMost = true;
        PictureBox ic = new PictureBox();
        ic.Image = SystemIcons.Question.ToBitmap();
        ic.Location = new Point(20, 22);
        ic.Size = new Size(32, 32);
        Label q = new Label();
        q.Text = "Voulez-vous enregistrer les modifications apportées au document ?";
        q.Location = new Point(66, 28);
        q.Size = new Size(360, 40);
        d.Controls.Add(ic);
        d.Controls.Add(q);
        string[] noms = { "Oui", "Non", "Annuler" };
        for (int i = 0; i < 3; i++)
        {
            Button b = new Button();
            b.Text = noms[i];
            b.Location = new Point(130 + i * 100, 92);
            b.Size = new Size(90, 30);
            b.DialogResult = i == 0 ? DialogResult.Yes : i == 1 ? DialogResult.No : DialogResult.Cancel;
            d.Controls.Add(b);
            if (i == 2) d.CancelButton = b;
        }
        d.ShowDialog(f);
    }

    // What a French Windows calls the title bars' buttons (the runner's is English).
    static readonly Dictionary<string, string> Systeme = new Dictionary<string, string> {
        { "Minimize", "Réduire" }, { "Maximize", "Agrandir" }, { "Restore", "Restaurer" }, { "Close", "Fermer" }, { "System", "Système" } };

    static Rectangle Cadre(IntPtr h)
    {
        RECT r;
        DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT))); // DWMWA_EXTENDED_FRAME_BOUNDS
        return Rectangle.FromLTRB(r.L, r.T, r.R, r.B);
    }

    static readonly ControlType[] Commandes = { ControlType.Button, ControlType.MenuItem, ControlType.TabItem, ControlType.Hyperlink,
                                                ControlType.CheckBox, ControlType.RadioButton, ControlType.ComboBox, ControlType.Edit };
    static readonly Dictionary<ControlType, string> Types = new Dictionary<ControlType, string> {
        { ControlType.Button, "bouton" }, { ControlType.MenuItem, "élément de menu" }, { ControlType.TabItem, "onglet" },
        { ControlType.Hyperlink, "lien" }, { ControlType.CheckBox, "case à cocher" }, { ControlType.RadioButton, "bouton radio" },
        { ControlType.ComboBox, "liste déroulante" }, { ControlType.Edit, "champ" } };

    class El { public int N, X, Y, L, H; public string Type, Nom, Uia; }

    static void Parcourir(AutomationElement e, Rectangle cadre, List<El> out_)
    {
        TreeWalker w = TreeWalker.ControlViewWalker;
        for (AutomationElement c = w.GetFirstChild(e); c != null; c = w.GetNextSibling(c))
        {
            ControlType ct;
            AutomationElement.AutomationElementInformation i;
            try { i = c.Current; ct = i.ControlType; } catch (ElementNotAvailableException) { continue; }
            if (Array.IndexOf(Commandes, ct) >= 0 && !i.IsOffscreen)
            {
                System.Windows.Rect b = i.BoundingRectangle;
                string nom = (i.Name ?? "").Trim();
                if (Systeme.ContainsKey(nom)) nom = Systeme[nom];
                if (!b.IsEmpty && nom.Length > 0)
                {
                    Rectangle r = Rectangle.Intersect(cadre, new Rectangle((int)b.X, (int)b.Y, (int)b.Width, (int)b.Height));
                    if (r.Width >= 2 && r.Height >= 2)
                    {
                        if (nom.Length > 40) nom = nom.Substring(0, 40);
                        out_.Add(new El { N = out_.Count + 1, X = r.X - cadre.X, Y = r.Y - cadre.Y, L = r.Width, H = r.Height, Type = Types[ct], Nom = nom, Uia = ct.ProgrammaticName });
                    }
                }
            }
            // A combo box's own button and list, a scroll bar's buttons, a grid's rows and cells: never.
            if (ct == ControlType.ComboBox || ct == ControlType.ScrollBar || ct == ControlType.DataGrid || ct == ControlType.Table) continue;
            Parcourir(c, cadre, out_);
        }
    }

    static string J(string s)
    {
        StringBuilder b = new StringBuilder("\"");
        foreach (char c in s)
        {
            if (c == '"' || c == '\\') b.Append('\\').Append(c);
            else if (c < ' ') b.AppendFormat("\\u{0:x4}", (int)c);
            else b.Append(c);
        }
        return b.Append('"').ToString();
    }

    static void Faire(Ecran e)
    {
        Form f = null;
        IntPtr h = IntPtr.Zero;
        pump.Invoke((MethodInvoker)delegate
        {
            f = e.Construire();
            f.Show();
            foreach (Form c in f.MdiChildren) c.Show();
            f.Activate();
            h = f.Handle;
        });
        Thread.Sleep(1200);
        if (e.Apres != null)
        {
            Form ff = f;
            pump.BeginInvoke((MethodInvoker)delegate { e.Apres(ff); });
            Thread.Sleep(1500);
        }
        Rectangle cadre = Cadre(h);
        using (Bitmap bmp = new Bitmap(cadre.Width, cadre.Height, PixelFormat.Format24bppRgb))
        {
            using (Graphics g = Graphics.FromImage(bmp)) g.CopyFromScreen(cadre.Location, Point.Empty, cadre.Size);
            bmp.Save(Path.Combine(dir, e.Id + ".png"), ImageFormat.Png);
        }
        int pid = System.Diagnostics.Process.GetCurrentProcess().Id;
        List<El> els = new List<El>();
        AutomationElementCollection tops = AutomationElement.RootElement.FindAll(TreeScope.Children,
            new PropertyCondition(AutomationElement.ProcessIdProperty, pid));
        List<AutomationElement> ordre = new List<AutomationElement>();
        foreach (AutomationElement t in tops) if (new IntPtr(t.Current.NativeWindowHandle) == h) ordre.Insert(0, t); else ordre.Add(t);
        foreach (AutomationElement t in ordre) Parcourir(t, cadre, els);
        List<int> verite = new List<int>();
        foreach (El x in els)
            if (e.VeriteNom != null && x.Nom == e.VeriteNom && x.Uia == "ControlType." + e.VeriteType) verite.Add(x.N);
        if (e.VeriteNom != null && verite.Count == 0) throw new Exception(e.Id + " : verite introuvable");
        string zones = "";
        if (e.Zone != null)
        {
            Rectangle z = Rectangle.Empty;
            Form ff = f;
            pump.Invoke((MethodInvoker)delegate { z = e.Zone(ff); });
            zones = string.Format("{{\"x\":{0},\"y\":{1},\"l\":{2},\"h\":{3}}}", z.X - cadre.X, z.Y - cadre.Y, z.Width, z.Height);
        }
        StringBuilder sb = new StringBuilder();
        sb.AppendFormat("{{\"largeur\":{0},\"hauteur\":{1},\"elements\":[", cadre.Width, cadre.Height);
        for (int k = 0; k < els.Count; k++)
        {
            El x = els[k];
            sb.AppendFormat("{0}{{\"n\":{1},\"type\":{2},\"nom\":{3},\"x\":{4},\"y\":{5},\"l\":{6},\"h\":{7},\"uia\":{8}}}",
                k > 0 ? "," : "", x.N, J(x.Type), J(x.Nom), x.X, x.Y, x.L, x.H, J(x.Uia));
        }
        sb.AppendFormat("],\"verite\":[{0}],\"zones\":[{1}]}}", string.Join(",", verite), zones);
        File.WriteAllText(Path.Combine(dir, e.Id + ".json"), sb.ToString(), new UTF8Encoding(false));
        Console.WriteLine(e.Id + " " + els.Count + " elements, verite " + string.Join(",", verite) + " " + zones);
        if (e.Bloquant)
        {
            // The dialog: its « Annuler », by UIA, from this thread.
            foreach (AutomationElement t in AutomationElement.RootElement.FindAll(TreeScope.Children, new PropertyCondition(AutomationElement.ProcessIdProperty, pid)))
            {
                if (new IntPtr(t.Current.NativeWindowHandle) == h) continue;
                AutomationElement b = t.FindFirst(TreeScope.Descendants, new AndCondition(
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button), new PropertyCondition(AutomationElement.NameProperty, "Annuler")));
                if (b != null) ((InvokePattern)b.GetCurrentPattern(InvokePattern.Pattern)).Invoke();
            }
            Thread.Sleep(500);
        }
        pump.Invoke((MethodInvoker)delegate { f.Close(); f.Dispose(); });
        Thread.Sleep(300);
    }

    [STAThread]
    static int Main(string[] a)
    {
        SetProcessDPIAware();
        Application.EnableVisualStyles();
        dir = a[0];
        Directory.CreateDirectory(dir);
        pump = new Control();
        pump.CreateControl();
        int code = 0;
        Thread worker = new Thread(delegate ()
        {
            try
            {
                foreach (Ecran e in Ecrans()) Faire(e);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.ToString());
                code = 1;
            }
            pump.BeginInvoke((MethodInvoker)Application.ExitThread);
        });
        worker.Start();
        Application.Run();
        return code;
    }
}
