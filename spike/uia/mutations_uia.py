"""Each protection of the guide v2's elements taken out in turn must turn at least one test red.

    python -I mutations_uia.py <app/src-tauri> local     the pure rules (capture/uia.rs), unit tests, any OS
    python -I mutations_uia.py <app/src-tauri> vm        the Windows reader (uia_win.rs), desktop tests, a VM at 150 %
"""
import subprocess
import sys

UIA, WIN = "src/capture/uia.rs", "src/capture/uia_win.rs"
U = "capture::uia::tests::"
W = "capture::uia_win::tests::"
ATELIER, UNAWARE, EDGE, MESSAGE, QUESTION, SAISIE, TOILE, CHAUD, COUVERT, DISTANT = (
    "uia_atelier_pmv2", "uia_atelier_unaware", "uia_edge_page", "uia_dialogue_message", "uia_dialogue_question",
    "uia_dialogue_saisie", "uia_sans_arbre", "uia_edge_chaud", "uia_couvert", "uia_distant_par_son_nom")
EXECUTER, DEUX, MENUS, FOULE, AILLEURS, FIGE, PREMIER, ERREUR, HORS_ECRAN, WPF = (
    "uia_executer", "uia_deux_fenetres", "uia_menus", "uia_foule", "uia_mots_de_passe_ailleurs", "uia_fige",
    "uia_edge_premier_contact", "uia_dialogue_erreur", "uia_dialogue_ailleurs", "uia_wpf")
FERME = "capture::uia::tests::once_the_guide_is_closed_nothing_is_read"
ALL = (ATELIER, UNAWARE, EDGE, MESSAGE, QUESTION, SAISIE, TOILE, CHAUD, COUVERT, DISTANT, EXECUTER, DEUX, MENUS, FOULE, AILLEURS, FIGE,
       PREMIER, ERREUR, HORS_ECRAN, WPF)

LOCAL = [
    ("in a browser, its own bar and tabs kept", UIA, [("    (!navigateur || ctx.page) && (genre != Genre::Lien || ctx.barre)", "    (genre != Genre::Lien || ctx.barre)")]),
    ("a link kept out of a bar", UIA, [("    (!navigateur || ctx.page) && (genre != Genre::Lien || ctx.barre)", "    (!navigateur || ctx.page)")]),
    ("what is out of view kept", UIA, [("&& !hors_vue && actif", "&& actif")]),
    ("a disabled command kept", UIA, [("&& !hors_vue && actif", "&& !hors_vue")]),
    ("a hidden zone ignored", UIA, [(" && !cachees.iter().any(|z| z.touche(c))", "")]),
    ("another screen's commands kept", UIA, [("    c.touche(fenetre) && c.touche(ecran) &&", "    c.touche(fenetre) &&")]),
    ("outside its window kept", UIA, [("    c.touche(fenetre) && c.touche(ecran) &&", "    c.touche(ecran) &&")]),
    ("a table walked", UIA, [("    !matches!(s, Sorte::Commande(g) if g != Genre::Onglet) && s != Sorte::Donnees", "    !matches!(s, Sorte::Commande(g) if g != Genre::Onglet)")]),
    ("a command walked", UIA, [("    !matches!(s, Sorte::Commande(g) if g != Genre::Onglet) && s != Sorte::Donnees", "    s != Sorte::Donnees")]),
    ("a tab not walked", UIA, [("    !matches!(s, Sorte::Commande(g) if g != Genre::Onglet) && s != Sorte::Donnees", "    !matches!(s, Sorte::Commande(_) | Sorte::Donnees)")]),
    ("a page not inherited", UIA, [("page: self.page || s == Sorte::Page", "page: s == Sorte::Page")]),
    ("the system's message box no dialog", UIA, [('    classe == "#32770" || proprietaire_attend || modale', "    proprietaire_attend || modale")]),
    ("a window its owner waits on no dialog", UIA, [('    classe == "#32770" || proprietaire_attend || modale', '    classe == "#32770" || modale')]),
    ("UIA's modal no dialog", UIA, [('    classe == "#32770" || proprietaire_attend || modale', '    classe == "#32770" || proprietaire_attend')]),
    ("a form taken for a choice", UIA, [("    dialogue && boutons >= 2 && !elements.iter().any(|e| matches!(e.genre, Genre::Champ | Genre::Liste))", "    dialogue && boutons >= 2")]),
    ("one button taken for a choice", UIA, [("    dialogue && boutons >= 2 &&", "    dialogue && boutons >= 1 &&")]),
    ("the title bar's close counted as a choice", UIA, [("e.genre == Genre::Bouton && !e.barre_titre).count()", "e.genre == Genre::Bouton).count()")]),
    ("a read-only field makes a form", UIA, [("(actif && !hors_vue && !lecture_seule)", "(actif && !hors_vue)")]),
    ("a field folded away makes a form", UIA, [("(actif && !hors_vue && !lecture_seule)", "(actif && !lecture_seule)")]),
    ("a disabled field makes a form", UIA, [("(actif && !hors_vue && !lecture_seule)", "(!hors_vue && !lecture_seule)")]),
    ("no field makes a form", UIA, [("    !matches!(genre, Genre::Champ | Genre::Liste) || (actif && !hors_vue && !lecture_seule)", "    !matches!(genre, Genre::Champ | Genre::Liste)")]),
    ("names not cut", UIA, [("    net(nom).chars().take(NOM_MAX).collect()", "    net(nom).chars().collect()")]),
    ("a name of 40 taken for a cut one", UIA, [("    net(nom).chars().count() > NOM_MAX", "    net(nom).chars().count() >= NOM_MAX")]),
    ("a quote kept in a name", UIA, [(".map(|c| if c == '\"' { '\\'' } else { c })", "")]),
    ("hidden marks kept in a name", UIA, [(".filter(|&c| !invisible(c) || c.is_whitespace())", "")]),
    ("tags kept in a name", UIA, [("                | '\\u{e0000}'..='\\u{e0fff}'\n", "")]),
    ("the soft hyphen kept in a name", UIA, [("            '\\u{ad}'\n                | '\\u{34f}'", "            '\\u{34f}'")]),
    ("variation selectors kept in a name", UIA, [("                | '\\u{fe00}'..='\\u{fe0f}'\n", "")]),
    ("a title bar's button pointed at", UIA, [("    if e.barre_titre || e.choix {", "    if e.choix {")]),
    ("a ring in a dialog that waits on a choice", UIA, [("    if e.barre_titre || e.choix {", "    if e.barre_titre {")]),
    ("a name that begins with the label beats the label itself", UIA, [("        Accord::Prefixe => (!liste.elements.iter().any(|o| !o.barre_titre && accord(libelle, o) == Some(Accord::Egal))).then_some(e),", "        Accord::Prefixe => Some(e),")]),
    ("a prefix by letters, not words", UIA, [("    (b.starts_with(&a) || suite).then_some(Accord::Prefixe)", "    (b.join(\" \").starts_with(&a.join(\" \")) || suite).then_some(Accord::Prefixe)")]),
    ("a short name taken for a cut one", UIA, [("    let suite = e.coupe && a.join", "    let suite = a.join")]),
    ("icon words kept", UIA, [(".filter(|w| !w.contains('_'))", "")]),
    ("accents compared", UIA, [(".flat_map(char::to_lowercase).map(sans_accent)", ".flat_map(char::to_lowercase)")]),
    ("a canvas's title bar taken for a tree", UIA, [("liste.choix.is_empty() && liste.elements.iter().all(|e| e.barre_titre)", "liste.choix.is_empty() && liste.elements.is_empty()")]),
    ("pixels on a dialog that waits on a choice", UIA, [("    !liste.attend && liste.choix.is_empty() && ", "    !liste.attend && ")]),
    ("pixels on a window that waits on a dialog", UIA, [("    !liste.attend && liste.choix.is_empty() && ", "    liste.choix.is_empty() && ")]),
    ("a remote desktop's program not known", UIA, [("    DISTANTS.contains(&exe(app).as_str())", "    DISTANTS.is_empty()")]),
    ("a browser not known", UIA, [("    NAVIGATEURS.contains(&exe(app).as_str())", "    NAVIGATEURS.is_empty()")]),
    ("Yandex not known", UIA, [('"thorium.exe", "browser.exe", "iexplore.exe",\n];', '"thorium.exe", "iexplore.exe", "x",\n];')]),
    ("no read as the guide opens", UIA, [("        let _ = envoi.send(Travail { faire: chauffe, echeance: None });\n", "")]),
    ("a list nobody waits for read all the same", UIA, [("                if t.echeance.is_some_and(|e| Instant::now() >= e) {\n                    continue;\n                }\n", "")]),
    ("a slow list waited for", UIA, [("    recu.recv_timeout(delai).map_err(|_| Rate::Indisponible)?", "    recu.recv().map_err(|_| Rate::Indisponible)?")]),
    ("password zones at the screen's place, not the capture's", UIA, [("    let at = |v: i32, o: i32| v.saturating_sub(o).max(0) as u32;", "    let at = |v: i32, _o: i32| v.max(0) as u32;")]),
    ("no margin around a password field", UIA, [("const MARGE: i32 = 4;", "const MARGE: i32 = 0;")]),
    ("no identity checked before", UIA, [("    if !toujours() {\n        return Err(Rate::Introuvable);\n    }\n    let lu = lire()?;", "    let lu = lire()?;")]),
    ("no identity checked after", UIA, [("    let lu = lire()?;\n    if !toujours() {\n        return Err(Rate::Introuvable);\n    }\n", "    let lu = lire()?;\n")]),
    ("the reader kept after the guide", UIA, [("            for t in recu {", "            for t in recu.iter().chain(std::iter::from_fn(|| { std::thread::park(); None })) {")]),
]

FAMILLE = "en_ordre().into_iter().filter(|&w| en_vue(w) && (w == h || (!coquille(w) && (racine.is_some_and(|r| racine_de(w) == r) || (fil_de(w) == fil && menu(w)))))).collect()"
VM = [
    # A list's row lies in its list in Edge and WinForms (spike: a listitem or an option without its list
    # is a Group), and a list is never walked: the two are taken out together.
    ("a list walked and its rows taken for commands", WIN, [("    (UIA_ListControlTypeId, Sorte::Donnees),", "    (UIA_ListControlTypeId, Sorte::Autre),"),
                                                           ("    (UIA_ListItemControlTypeId, Sorte::Donnees),", "    (UIA_ListItemControlTypeId, Sorte::Commande(Genre::Bouton)),")], [EDGE, ATELIER]),
    ("a row out of a table taken for a command", WIN, [("    (UIA_DataItemControlTypeId, Sorte::Donnees),", "    (UIA_DataItemControlTypeId, Sorte::Commande(Genre::Bouton)),")], [EDGE]),
    ("texts kept", WIN, [("const SORTES: [(UIA_CONTROLTYPE_ID, Sorte); 21] = [", "const SORTES: [(UIA_CONTROLTYPE_ID, Sorte); 22] = [\n    (UIA_TextControlTypeId, Sorte::Commande(Genre::Champ)),")], [EDGE]),
    ("a page's <nav> no bar", WIN, [("        None if repere == UIA_NavigationLandmarkTypeId.0 => Sorte::Barre,\n", "")], [EDGE]),
    ("no depth limit", WIN, [("        if profondeur > PROFONDEUR_MAX {", "        if profondeur > usize::MAX - 1 {")], [EDGE]),
    ("password fields not given", WIN, [("                Some(champs) => s.champs.extend(champs.into_iter().filter(|c| c.touche(&f) && c.touche(ecran))),", "                Some(_) => {}")], [ATELIER, EDGE]),
    ("password fields only one level down", WIN, [("FindAllBuildCache(TreeScope_Descendants,", "FindAllBuildCache(TreeScope_Children,")], [EDGE, ATELIER]),
    ("a window waiting on a dialog: its password field not read", WIN, [("            if !en_vue(w) || pid_of(w) == std::process::id() {", "            if !en_vue(w) || pid_of(w) == std::process::id() || !active(w) {")], [MESSAGE]),
    ("other programs' password fields not read", WIN, [("            if !en_vue(w) || pid_of(w) == std::process::id() {", "            if !en_vue(w) || pid_of(w) == std::process::id() || foreground().is_some_and(|f| f.window.pid != pid_of(w)) {")], [AILLEURS]),
    ("a remote desktop's fields taken for known", WIN, [("                Some(i) if !distant(&i.app) => self.champs(w),", "                Some(_) => self.champs(w),")], [DISTANT]),
    ("a tree that does not answer, or a page not built, taken for none", WIN, [("                Some(i) if !distant(&i.app) => self.champs(w),", "                Some(i) if !distant(&i.app) => self.champs(w).or(Some(Vec::new())),")], [FIGE, PREMIER]),
    ("a page not built yet taken for none", WIN, [("&& montre_une_page(w) {", "&& false {")], [PREMIER]),
    ("a window that waits on a dialog read", WIN, [("            if !active(w) {\n                continue;\n            }", "            let _ = active(w);")], [MESSAGE, QUESTION]),
    ("boxes in logical pixels", WIN, [("        let _physical = Physical::enter();\n        let cible = match cible {", "        let cible = match cible {")], [UNAWARE, ATELIER, EDGE]),
    ("the program's other windows read", WIN, [(FAMILLE, "en_ordre().into_iter().filter(|&w| en_vue(w) && pid_of(w) == pid_of(h)).collect()")], [EXECUTER, DEUX]),
    ("other programs' windows read", WIN, [(FAMILLE, "en_ordre().into_iter().filter(|&w| en_vue(w)).collect()")], [ATELIER, DEUX]),
    ("the shell's windows kept", WIN, [("(w == h || (!coquille(w) && (", "(w == h || (true && (")], [EXECUTER]),
    ("a family hung on the shell kept", WIN, [("    let racine = (!coquille(racine)).then_some(racine);", "    let racine = Some(racine);")], [EXECUTER]),
    ("the dialogs left out", WIN, [("racine.is_some_and(|r| racine_de(w) == r) || ", "")], [QUESTION, MESSAGE]),
    ("the owners' chain by GA_ROOTOWNER", WIN, [("fn racine_de(h: HWND) -> HWND {\n    let mut r = h;", "fn racine_de(h: HWND) -> HWND {\n    #[allow(clippy::needless_return)]\n    return unsafe { GetAncestor(h, windows::Win32::UI::WindowsAndMessaging::GA_ROOTOWNER) };\n    #[allow(unreachable_code)]\n    let mut r = h;")], [QUESTION, ERREUR, SAISIE]),
    ("the thread's menus left out", WIN, [("(fil_de(w) == fil && menu(w))", "false")], [MENUS]),
    ("the system's menus left out", WIN, [('    class(w) == "#32768" || (popup && ', "    (popup && ")], [MENUS]),
    ("a toolkit's menu hung on a hidden window left out", WIN, [("(popup && (racine_de(w) == w || !en_vue(racine_de(w))))", "(popup && racine_de(w) == w)")], [MENUS]),
    ("a tab not walked (WPF)", UIA, [("    !matches!(s, Sorte::Commande(g) if g != Genre::Onglet) && s != Sorte::Donnees", "    !matches!(s, Sorte::Commande(_) | Sorte::Donnees)")], [WPF]),
    ("the window's identity not checked", WIN, [("            || same_window(&cible, identity(h).as_ref()),", "            || true,")], [TOILE]),
    ("a dialog's choice not said", WIN, [("                p.elements.iter_mut().for_each(|e| e.choix = true);\n                liste.choix.push(f);", "                let _ = f;")], [MESSAGE, QUESTION]),
    ("the dialog's guard counts what is seen only", WIN, [("attend, modale), &p.toutes) {", "attend, modale), &p.elements) {")], [MESSAGE]),
    ("the guard counts every field", WIN, [("        if compte(genre, hors_vue, actif, lecture_seule) {", "        if true {")], [ERREUR]),
    ("a field's being read only not read", WIN, [("        let lecture_seule = unsafe { el.GetCachedPropertyValue(UIA_ValueIsReadOnlyPropertyId) }.ok().and_then(|v| bool::try_from(&v).ok()).unwrap_or(false);", "        let lecture_seule = false;")], [ERREUR]),
    ("a dialog on another screen not guarded", WIN, [("            if !f.touche(&ecran) && !dialogue(&class(w), attend, false) {", "            if !f.touche(&ecran) {")], [HORS_ECRAN]),
    ("the window asked not said waiting", WIN, [("attend: !active(h)", "attend: false")], [MESSAGE, HORS_ECRAN]),
    ("no cap on the list", WIN, [("        liste.elements.truncate(ELEMENTS_MAX);\n", "")], [FOULE]),
    ("a cut name not said so", WIN, [("element(court(&brut), coupe(&brut))", "element(court(&brut), false)")], [ATELIER]),
    ("every name said cut", WIN, [("element(court(&brut), coupe(&brut))", "element(court(&brut), true)")], [ATELIER]),
    ("not a browser to the walk", WIN, [("self.arbre(h, navigateur(&cible.app), cachees)", "self.arbre(h, false, cachees)")], [EDGE]),
    ("a field's value for its name", WIN, [("            let brut = unsafe { el.CachedName() }.map(|b| b.to_string()).unwrap_or_default();",
                                          "            let brut = unsafe { el.GetCurrentPropertyValue(UIA_ValueValuePropertyId) }.ok().and_then(|v| windows::core::BSTR::try_from(&v).ok()).map(|b| b.to_string()).filter(|v| !v.is_empty()).unwrap_or_else(|| unsafe { el.CachedName() }.map(|b| b.to_string()).unwrap_or_default());")], [ATELIER, EDGE]),
    ("no read as the guide opens (on Edge)", UIA, [("        let _ = envoi.send(Travail { faire: chauffe, echeance: None });\n", "")], [CHAUD]),
    ("a command under another window listed", WIN, [(" && montre(self.hwnd, &c) {", " {")], [COUVERT]),
    ("a remote desktop's list not emptied", WIN, [("                if distant(&cible.app) {\n                    return Ok(Liste::default());\n                }\n", "")], [DISTANT]),
    ("a read after the guide makes a reader again", UIA, [("    let envoi = lecteur().as_ref().map(|t| t.envoi.clone()).ok_or(Rate::Indisponible)?;", "    ouvrir();\n    let envoi = lecteur().as_ref().map(|t| t.envoi.clone()).ok_or(Rate::Indisponible)?;")], [FERME]),
    ("UIA's transaction timeout left at its default", WIN, [("                let _ = a2.SetTransactionTimeout(2000);\n", "")], [FIGE]),
    ("UIA's connection timeout left at its default", WIN, [("                let _ = a2.SetConnectionTimeout(2000);\n", "")], [FIGE]),
    # Expected to survive, said in the PR: redundant on these VMs.
    ("a window on another screen walked (one screen on a VM)", WIN, [("            if !f.touche(&ecran) && !dialogue(&class(w), attend, false) {\n                continue;\n            }\n", "")], [ATELIER]),
    ("UIA's modal not read (its owner waits too)", WIN, [("            let modale = unsafe {", "            let modale = false && unsafe {")], [QUESTION]),
]
EXPECTED = {"a window on another screen walked (one screen on a VM)", "UIA's modal not read (its owner waits too)"}


def args_of(t):
    return ["--include-ignored", "--exact", "--test-threads=1", t] if t.startswith("capture::") else ["--ignored", "--exact", "--test-threads=1", W + t]


def run(root, args):
    return subprocess.run(["cargo", "test", "--locked", "--lib", "--"] + args, cwd=root, capture_output=True, text=True)


def main():
    root, which = sys.argv[1], sys.argv[2]
    local = which == "local"
    only = sys.argv[3:]
    # --shard=k/n: the mutations k, k+n, k+2n... of the list, for n VMs at once.
    k, n = map(int, next((a.split("=", 1)[1] for a in only if a.startswith("--shard=")), "0/1").split("/"))
    base = run(root, [U] if local else ["--include-ignored", "--exact", "--test-threads=1", FERME] + [W + t for t in ALL]
               + ([W + "uia_remoteapp"] if "--distance" in only else []))
    print("\n".join((base.stdout + base.stderr).strip().splitlines()[-12:]))
    if base.returncode != 0:
        sys.exit("baseline red: no mutation means anything")
    survived, red = [], 0
    for m in [m for i, m in enumerate(LOCAL if local else VM) if i % n == k]:
        name, path, edits = m[0], m[1], m[2]
        tests = [U] if local else m[3]
        if not local and "uia_remoteapp" in tests and "--distance" not in only:
            continue
        full = root + "/" + path
        text = open(full, encoding="utf-8").read()
        changed = text
        for old, new in edits:
            if changed.count(old) != 1:
                print("MISSING %s: %r (%d)" % (name, old[:70], changed.count(old)))
                survived.append(name)
                break
            changed = changed.replace(old, new)
        else:
            open(full, "w", encoding="utf-8", newline="\n").write(changed)
            try:
                reds = []
                for t in tests:
                    rc = run(root, [t] if local else args_of(t))
                    out = rc.stdout + rc.stderr
                    if "error[E" in out or "error: could not compile" in out:
                        print("   %s does not build:\n%s" % (name, "\n".join(l for l in out.splitlines() if "error" in l)[:1500]))
                        break
                    failed = [l for l in out.splitlines() if l.endswith("FAILED") or "panicked at" in l]
                    if rc.returncode != 0:
                        reds.append(t)
                        print("   %s / %s: %s" % (name, t, "; ".join(failed[:3])[:400]))
                        break
                if reds:
                    red += 1
                elif name not in EXPECTED:
                    survived.append(name)
                print("%s: %s" % ("RED" if reds else ("SURVIVED (expected)" if name in EXPECTED else "SURVIVED"), name))
            finally:
                open(full, "w", encoding="utf-8", newline="\n").write(text)
    print("\n%d red; survived: %s" % (red, survived or "none"))
    sys.exit(1 if survived else 0)


main()
