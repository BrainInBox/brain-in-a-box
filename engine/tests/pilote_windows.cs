// The Windows driver of the field test "app" (engine/tests/terrain_app.py, PiloteWindows).
//
// Compiled on the VM by the csc.exe of the .NET Framework that ships with Windows (C# 5):
// nothing downloaded, nothing installed. It reads windows through UI Automation, the
// accessibility tree Windows gives every program (the page of the app's WebView2 included,
// as a screen reader sees it), presses a button as a screen reader does (Invoke), and
// captures the whole screen.
//
//   pilote-windows.exe lire <pid>                  the windows of <pid>, and the one in front
//   pilote-windows.exe cliquer <hwnd> <label> [souris|invoke]
//                                                  the enabled button (or link) with that label: the mouse
//                                                  on it as a user clicks, or Invoke as a screen reader does
//   pilote-windows.exe cocher <hwnd> <label> 0|1   a check box, unchecked or checked, with the mouse
//   pilote-windows.exe fermer <hwnd>               the window closed, as its X does
//   pilote-windows.exe devant <hwnd>               the window brought to the front
//   pilote-windows.exe capturer <file.png>         the whole screen
//   pilote-windows.exe ranger                      the windows already open minimized (a user's desktop)
//   pilote-windows.exe fenetres                    the visible top-level windows, each with its program
//   pilote-windows.exe raccourci <file.lnk>        where a shortcut points
//
// Every answer is one line of JSON, ASCII only (\uXXXX escapes): the console's code page never
// matters. This file stays ASCII too: csc reads a file without a byte order mark in the ANSI
// code page.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Automation;

static class PiloteWindows
{
    const int NOEUDS_APP = 4000;
    const int NOEUDS_DEVANT = 400;
    const int PROFONDEUR = 80;

    delegate bool EnumProc(IntPtr h, IntPtr l);

    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X, Y; }

    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a, uint b, bool attach);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int attr, out int value, int size);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool QueryFullProcessImageName(IntPtr h, uint flags, StringBuilder s, ref uint size);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

    // aria-live: .NET 4.7.1 and later know it, older ones do not (read by reflection).
    static AutomationProperty Live = Propriete("LiveSettingProperty");

    static int Main(string[] args)
    {
        SetProcessDPIAware();
        Dictionary<string, object> res;
        try
        {
            res = Faire(args);
        }
        catch (Exception e)
        {
            res = Echec(e.GetType().Name + ": " + e.Message);
        }
        Console.Out.Write(Json(res));
        Console.Out.Write("\n");
        Console.Out.Flush();
        return 0;
    }

    static Dictionary<string, object> Faire(string[] a)
    {
        string op = a.Length > 0 ? a[0] : "";
        if (op == "lire" && a.Length == 2) return Lire(uint.Parse(a[1], CultureInfo.InvariantCulture));
        if (op == "cliquer" && (a.Length == 3 || a.Length == 4)) return Cliquer(Fenetre(a[1]), a[2], a.Length == 4 ? a[3] : "souris");
        if (op == "cocher" && a.Length == 4) return Cocher(Fenetre(a[1]), a[2], a[3] == "1");
        if (op == "fermer" && a.Length == 2) return Fermer(Fenetre(a[1]));
        if (op == "devant" && a.Length == 2) return Devant(Fenetre(a[1]));
        if (op == "capturer" && a.Length == 2) return Capturer(a[1]);
        if (op == "raccourci" && a.Length == 2) return Raccourci(a[1]);
        if (op == "ranger" && a.Length == 1) return Ranger();
        if (op == "fenetres" && a.Length == 1) return Fenetres();
        return Echec("usage : lire <pid> | cliquer <hwnd> <libelle> | cocher <hwnd> <libelle> 0|1 | fermer <hwnd> | "
                     + "devant <hwnd> | capturer <png> | raccourci <lnk> | ranger");
    }

    // ------------------------------------------------------------------ windows

    static IntPtr Fenetre(string s)
    {
        IntPtr h = new IntPtr(long.Parse(s, CultureInfo.InvariantCulture));
        if (!IsWindow(h)) throw new ArgumentException("fenetre " + s + " absente");
        return h;
    }

    static string Texte(IntPtr h)
    {
        StringBuilder s = new StringBuilder(512);
        GetWindowText(h, s, s.Capacity);
        return s.ToString();
    }

    static string Classe(IntPtr h)
    {
        StringBuilder s = new StringBuilder(256);
        GetClassName(h, s, s.Capacity);
        return s.ToString();
    }

    static string Image(uint pid)
    {
        IntPtr p = OpenProcess(0x1000, false, pid);   // PROCESS_QUERY_LIMITED_INFORMATION
        if (p == IntPtr.Zero) return "";
        try
        {
            StringBuilder s = new StringBuilder(1024);
            uint n = (uint)s.Capacity;
            return QueryFullProcessImageName(p, 0, s, ref n) ? s.ToString() : "";
        }
        finally
        {
            CloseHandle(p);
        }
    }

    static Dictionary<string, object> Infos(IntPtr h, uint pid, IntPtr devant)
    {
        Dictionary<string, object> d = new Dictionary<string, object>();
        RECT r;
        GetWindowRect(h, out r);
        d["h"] = h.ToInt64();
        d["pid"] = (long)pid;
        d["titre"] = Texte(h);
        d["classe"] = Classe(h);
        d["devant"] = h == devant;
        d["rect"] = new List<object> { r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top };
        return d;
    }

    // The visible top-level windows of pid, each with its tree, and the window in front when it is
    // another program's (a system dialog over the app), with a smaller tree.
    static Dictionary<string, object> Lire(uint cible)
    {
        IntPtr devant = GetForegroundWindow();
        List<object> app = new List<object>();
        Dictionary<string, object> autre = null;
        List<IntPtr> vues = new List<IntPtr>();
        EnumWindows(delegate (IntPtr h, IntPtr l) { if (IsWindowVisible(h)) vues.Add(h); return true; }, IntPtr.Zero);
        string mode = "";
        foreach (IntPtr h in vues)
        {
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            if (pid == cible)
            {
                Dictionary<string, object> f = Infos(h, pid, devant);
                string m;
                f["arbre"] = Arbre(h, NOEUDS_APP, out m);
                mode = m;
                app.Add(f);
            }
            else if (h == devant)
            {
                autre = Infos(h, pid, devant);
                autre["exe"] = Image(pid);
                string m;
                autre["arbre"] = Arbre(h, NOEUDS_DEVANT, out m);
            }
        }
        Dictionary<string, object> res = Ok();
        res["fenetres"] = app;
        res["devant"] = autre;
        res["mode"] = mode;
        return res;
    }

    // ------------------------------------------------------------------ the accessibility tree

    static AutomationProperty Propriete(string nom)
    {
        foreach (Type t in new Type[] { typeof(AutomationElement), typeof(AutomationElementIdentifiers) })
        {
            FieldInfo f = t.GetField(nom, BindingFlags.Public | BindingFlags.Static);
            if (f != null) return f.GetValue(null) as AutomationProperty;
        }
        return null;
    }

    static CacheRequest Requete()
    {
        CacheRequest cr = new CacheRequest();
        cr.TreeScope = TreeScope.Element | TreeScope.Descendants;
        cr.TreeFilter = Automation.RawViewCondition;
        cr.Add(AutomationElement.NameProperty);
        cr.Add(AutomationElement.ControlTypeProperty);
        cr.Add(AutomationElement.LocalizedControlTypeProperty);
        cr.Add(AutomationElement.IsEnabledProperty);
        cr.Add(AutomationElement.AutomationIdProperty);
        cr.Add(AutomationElement.ClassNameProperty);
        cr.Add(TogglePattern.ToggleStateProperty);
        if (Live != null) cr.Add(Live);
        return cr;
    }

    // The whole tree in one request (the cache), else element by element when the page changed
    // under the request.
    static Dictionary<string, object> Arbre(IntPtr h, int budget, out string mode)
    {
        for (int essai = 0; essai < 2; essai++)
        {
            try
            {
                AutomationElement racine;
                using (Requete().Activate())
                {
                    racine = AutomationElement.FromHandle(h);
                }
                mode = "cache";
                return Noeud(racine, true, 0, new int[] { budget });
            }
            catch (Exception)
            {
                Thread.Sleep(150);
            }
        }
        mode = "pas-a-pas";
        return Noeud(AutomationElement.FromHandle(h), false, 0, new int[] { budget });
    }

    static object Valeur(AutomationElement e, AutomationProperty p, bool cache)
    {
        if (p == null) return null;
        try
        {
            object v = cache ? e.GetCachedPropertyValue(p, true) : e.GetCurrentPropertyValue(p, true);
            return v == AutomationElement.NotSupported ? null : v;
        }
        catch (Exception)
        {
            return null;
        }
    }

    static Dictionary<string, object> Noeud(AutomationElement e, bool cache, int profondeur, int[] reste)
    {
        reste[0]--;
        Dictionary<string, object> d = new Dictionary<string, object>();
        ControlType ct = Valeur(e, AutomationElement.ControlTypeProperty, cache) as ControlType;
        string nom = ct == null ? "" : ct.ProgrammaticName;
        d["t"] = nom.StartsWith("ControlType.") ? nom.Substring("ControlType.".Length) : nom;
        d["lt"] = Valeur(e, AutomationElement.LocalizedControlTypeProperty, cache) as string ?? "";
        d["n"] = Valeur(e, AutomationElement.NameProperty, cache) as string ?? "";
        object on = Valeur(e, AutomationElement.IsEnabledProperty, cache);
        d["e"] = !(on is bool) || (bool)on;
        string id = Valeur(e, AutomationElement.AutomationIdProperty, cache) as string ?? "";
        if (id.Length > 0) d["a"] = id;
        string classe = Valeur(e, AutomationElement.ClassNameProperty, cache) as string ?? "";
        if (classe.Length > 0) d["c"] = classe;
        object coche = Valeur(e, TogglePattern.ToggleStateProperty, cache);
        if (coche is ToggleState) d["x"] = (int)(ToggleState)coche;
        object live = Valeur(e, Live, cache);
        if (live != null)
        {
            try
            {
                int l = Convert.ToInt32(live, CultureInfo.InvariantCulture);
                if (l != 0) d["l"] = l;
            }
            catch (Exception) { }
        }
        List<object> enfants = new List<object>();
        d["k"] = enfants;
        if (profondeur >= PROFONDEUR) return d;
        if (cache)
        {
            AutomationElementCollection kids;
            try { kids = e.CachedChildren; } catch (InvalidOperationException) { kids = null; }
            if (kids == null) return d;
            foreach (AutomationElement k in kids)
            {
                if (reste[0] <= 0) break;
                enfants.Add(Noeud(k, true, profondeur + 1, reste));
            }
        }
        else
        {
            TreeWalker w = TreeWalker.RawViewWalker;
            AutomationElement k = null;
            try { k = w.GetFirstChild(e); } catch (Exception) { }
            while (k != null && reste[0] > 0)
            {
                enfants.Add(Noeud(k, false, profondeur + 1, reste));
                try { k = w.GetNextSibling(k); } catch (Exception) { k = null; }
            }
        }
        return d;
    }

    // ------------------------------------------------------------------ acting

    // The words as terrain_app.norm() makes them comparable; a Win32 label's & (its access key) dropped.
    static string Norm(string s)
    {
        string t = (s ?? "").Normalize(NormalizationForm.FormC);
        t = t.Replace('\u00a0', ' ').Replace('\u202f', ' ').Replace('\u2007', ' ')
             .Replace('\u2018', '\'').Replace('\u2019', '\'').Replace("\u2026", "...").Replace("&", "");
        StringBuilder b = new StringBuilder();
        bool blanc = false;
        foreach (char c in t)
        {
            if (char.IsWhiteSpace(c)) { blanc = true; continue; }
            if (blanc && b.Length > 0) b.Append(' ');
            blanc = false;
            b.Append(c);
        }
        return b.ToString();
    }

    static AutomationElement Trouver(IntPtr h, string libelle, ControlType type)
    {
        AutomationElement racine = AutomationElement.FromHandle(h);
        string voulu = Norm(libelle);
        AutomationElementCollection tous = racine.FindAll(TreeScope.Descendants,
            new PropertyCondition(AutomationElement.ControlTypeProperty, type));
        foreach (AutomationElement e in tous)
        {
            string nom;
            try { nom = e.Current.Name; } catch (ElementNotAvailableException) { continue; }
            if (Norm(nom) == voulu) return e;
        }
        return null;
    }

    // The window made active first, as a click makes it: a dialog that is not active may ignore a
    // button's click (BM_CLICK), which a GitHub VM showed on the installer's last page.
    static Dictionary<string, object> Cliquer(IntPtr h, string libelle, string mode)
    {
        AutomationElement b = Trouver(h, libelle, ControlType.Button) ?? Trouver(h, libelle, ControlType.Hyperlink);
        if (b == null) return Echec("absent");
        if (!b.Current.IsEnabled) return Echec("inactif");
        bool actif = Activer(h);
        string sans = "";
        if (mode != "invoke")
        {
            string pourquoi = Souris(h, b);
            if (pourquoi == null)
            {
                Dictionary<string, object> clic = Ok();
                clic["comment"] = "clic souris";
                clic["active"] = actif;
                return clic;
            }
            sans = " (pas de clic souris : " + pourquoi + ")";
        }
        object p;
        if (!b.TryGetCurrentPattern(InvokePattern.Pattern, out p)) return Echec("ni souris ni Invoke" + sans);
        try
        {
            ((InvokePattern)p).Invoke();
        }
        catch (ElementNotAvailableException)
        {
            // The window went away with the click.
        }
        Dictionary<string, object> ok = Ok();
        ok["comment"] = "Invoke" + sans;
        ok["active"] = actif;
        return ok;
    }

    // The mouse on the middle of the element, pressed and released, when nothing else covers it there.
    static string Souris(IntPtr h, AutomationElement e)
    {
        System.Windows.Point pt;
        if (!e.TryGetClickablePoint(out pt))
        {
            System.Windows.Rect r = e.Current.BoundingRectangle;
            if (r.IsEmpty || r.Width <= 0 || r.Height <= 0) return "pas de point cliquable";
            pt = new System.Windows.Point(r.Left + r.Width / 2, r.Top + r.Height / 2);
        }
        POINT q;
        q.X = (int)pt.X;
        q.Y = (int)pt.Y;
        IntPtr dessous = WindowFromPoint(q);
        if (dessous == IntPtr.Zero || GetAncestor(dessous, 2) != h)   // GA_ROOT
            return "une autre fenetre est sur le bouton (" + Classe(GetAncestor(dessous, 2)) + ")";
        SetCursorPos(q.X, q.Y);
        Thread.Sleep(80);
        mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);   // left down
        Thread.Sleep(60);
        mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);   // left up
        return null;
    }

    static Dictionary<string, object> Cocher(IntPtr h, string libelle, bool voulu)
    {
        AutomationElement c = Trouver(h, libelle, ControlType.CheckBox);
        if (c == null) return Echec("absent");
        object p;
        if (!c.TryGetCurrentPattern(TogglePattern.Pattern, out p)) return Echec("pas une case a cocher");
        Activer(h);
        TogglePattern tp = (TogglePattern)p;
        string comment = "clic souris";
        for (int i = 0; i < 3 && (tp.Current.ToggleState == ToggleState.On) != voulu; i++)
        {
            string pourquoi = Souris(h, c);
            if (pourquoi != null)
            {
                comment = "Toggle (pas de clic souris : " + pourquoi + ")";
                tp.Toggle();
            }
            Thread.Sleep(200);
        }
        bool coche = tp.Current.ToggleState == ToggleState.On;
        Dictionary<string, object> res = coche == voulu ? Ok() : Echec("la case ne change pas");
        res["coche"] = coche;
        res["comment"] = comment;
        return res;
    }

    static Dictionary<string, object> Fermer(IntPtr h)
    {
        object p;
        AutomationElement f = AutomationElement.FromHandle(h);
        if (f.TryGetCurrentPattern(WindowPattern.Pattern, out p))
        {
            ((WindowPattern)p).Close();
            Dictionary<string, object> ok = Ok();
            ok["comment"] = "WindowPattern.Close";
            return ok;
        }
        PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero);   // WM_CLOSE
        Dictionary<string, object> res = Ok();
        res["comment"] = "WM_CLOSE";
        return res;
    }

    // A window a program started in the background may open behind others: the user would see it in
    // front. Windows lets a program hand the foreground over only from the thread that has it.
    static Dictionary<string, object> Devant(IntPtr h)
    {
        Dictionary<string, object> res = Ok();
        res["devant"] = Activer(h);
        return res;
    }

    static bool Activer(IntPtr h)
    {
        if (IsIconic(h)) ShowWindow(h, 9);   // SW_RESTORE
        for (int essai = 0; essai < 2 && GetForegroundWindow() != h; essai++)
        {
            if (essai == 1)
            {
                // Windows hands the foreground to the program that sent the last input: an Alt tap.
                keybd_event(0x12, 0, 0, UIntPtr.Zero);
                keybd_event(0x12, 0, 2, UIntPtr.Zero);
            }
            IntPtr avant = GetForegroundWindow();
            uint pid;
            uint lui = avant == IntPtr.Zero ? 0 : GetWindowThreadProcessId(avant, out pid);
            uint moi = GetCurrentThreadId();
            bool lie = lui != 0 && lui != moi && AttachThreadInput(moi, lui, true);
            try
            {
                BringWindowToTop(h);
                SetForegroundWindow(h);
            }
            finally
            {
                if (lie) AttachThreadInput(moi, lui, false);
            }
            Thread.Sleep(150);
        }
        return GetForegroundWindow() == h;
    }

    // The windows a user would not have on a fresh desktop (on a GitHub VM: the runner's console),
    // minimized: they would hide the app in the captures. Not the shell, nor a cloaked or owned window.
    static Dictionary<string, object> Ranger()
    {
        List<IntPtr> vues = new List<IntPtr>();
        EnumWindows(delegate (IntPtr h, IntPtr l) { if (IsWindowVisible(h)) vues.Add(h); return true; }, IntPtr.Zero);
        string[] coquille = { "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd" };
        List<object> rangees = new List<object>();
        foreach (IntPtr h in vues)
        {
            int cache;
            if (Array.IndexOf(coquille, Classe(h)) >= 0 || Texte(h).Length == 0 || IsIconic(h)
                || GetWindow(h, 4) != IntPtr.Zero   // GW_OWNER
                || (DwmGetWindowAttribute(h, 14, out cache, 4) == 0 && cache != 0)) continue;   // DWMWA_CLOAKED
            ShowWindow(h, 6);   // SW_MINIMIZE
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["titre"] = Texte(h);
            d["classe"] = Classe(h);
            rangees.Add(d);
        }
        Dictionary<string, object> res = Ok();
        res["fenetres"] = rangees;
        return res;
    }

    // Whatever shows on the screen, in front or not (SmartScreen's question may not hold the
    // foreground): no tree, the program's file when Windows tells it.
    static Dictionary<string, object> Fenetres()
    {
        IntPtr devant = GetForegroundWindow();
        List<object> toutes = new List<object>();
        EnumWindows(delegate (IntPtr h, IntPtr l)
        {
            if (!IsWindowVisible(h)) return true;
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            Dictionary<string, object> d = Infos(h, pid, devant);
            d["exe"] = Image(pid);
            toutes.Add(d);
            return true;
        }, IntPtr.Zero);
        Dictionary<string, object> res = Ok();
        res["fenetres"] = toutes;
        return res;
    }

    static Dictionary<string, object> Capturer(string chemin)
    {
        int x = GetSystemMetrics(76), y = GetSystemMetrics(77), w = GetSystemMetrics(78), hauteur = GetSystemMetrics(79);
        if (w <= 0 || hauteur <= 0) return Echec("pas d'ecran (" + w + "x" + hauteur + ")");
        using (Bitmap bmp = new Bitmap(w, hauteur, PixelFormat.Format32bppArgb))
        {
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.CopyFromScreen(x, y, 0, 0, new Size(w, hauteur), CopyPixelOperation.SourceCopy);
            }
            bmp.Save(chemin, ImageFormat.Png);
        }
        Dictionary<string, object> res = Ok();
        res["taille"] = new List<object> { w, hauteur };
        return res;
    }

    static Dictionary<string, object> Raccourci(string lnk)
    {
        Type t = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(t);
        try
        {
            object s = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnk });
            Type st = s.GetType();
            Dictionary<string, object> res = Ok();
            res["cible"] = st.InvokeMember("TargetPath", BindingFlags.GetProperty, null, s, null) as string ?? "";
            res["dossier"] = st.InvokeMember("WorkingDirectory", BindingFlags.GetProperty, null, s, null) as string ?? "";
            res["arguments"] = st.InvokeMember("Arguments", BindingFlags.GetProperty, null, s, null) as string ?? "";
            return res;
        }
        finally
        {
            Marshal.FinalReleaseComObject(shell);
        }
    }

    // ------------------------------------------------------------------ answers

    static Dictionary<string, object> Ok()
    {
        Dictionary<string, object> d = new Dictionary<string, object>();
        d["ok"] = true;
        return d;
    }

    static Dictionary<string, object> Echec(string pourquoi)
    {
        Dictionary<string, object> d = new Dictionary<string, object>();
        d["ok"] = false;
        d["pourquoi"] = pourquoi;
        return d;
    }

    static string Json(object v)
    {
        StringBuilder b = new StringBuilder();
        Ecrire(b, v);
        return b.ToString();
    }

    static void Ecrire(StringBuilder b, object v)
    {
        if (v == null) { b.Append("null"); return; }
        if (v is bool) { b.Append((bool)v ? "true" : "false"); return; }
        if (v is int || v is long) { b.Append(Convert.ToInt64(v, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture)); return; }
        string s = v as string;
        if (s != null)
        {
            b.Append('"');
            foreach (char c in s)
            {
                if (c == '"') b.Append("\\\"");
                else if (c == '\\') b.Append("\\\\");
                else if (c < 0x20 || c > 0x7e) b.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                else b.Append(c);
            }
            b.Append('"');
            return;
        }
        IDictionary d = v as IDictionary;
        if (d != null)
        {
            b.Append('{');
            bool first = true;
            foreach (DictionaryEntry kv in d)
            {
                if (!first) b.Append(',');
                first = false;
                Ecrire(b, kv.Key.ToString());
                b.Append(':');
                Ecrire(b, kv.Value);
            }
            b.Append('}');
            return;
        }
        IEnumerable l = v as IEnumerable;
        if (l != null)
        {
            b.Append('[');
            bool first = true;
            foreach (object x in l)
            {
                if (!first) b.Append(',');
                first = false;
                Ecrire(b, x);
            }
            b.Append(']');
            return;
        }
        Ecrire(b, v.ToString());
    }
}
