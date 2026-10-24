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

LOCAL = [
    ("in a browser, its own bar and tabs kept", UIA, [("    (!navigateur || ctx.page) && (genre != Genre::Lien || ctx.barre)", "    (genre != Genre::Lien || ctx.barre)")]),
    ("a link kept out of a bar", UIA, [("    (!navigateur || ctx.page) && (genre != Genre::Lien || ctx.barre)", "    (!navigateur || ctx.page)")]),
    ("what is out of view kept", UIA, [("&& !hors_vue && actif", "&& actif")]),
    ("a disabled command kept", UIA, [("&& !hors_vue && actif", "&& !hors_vue")]),
    ("a hidden zone ignored", UIA, [(" && !cachees.iter().any(|z| z.touche(c))", "")]),
    ("another screen's commands kept", UIA, [("    c.touche(fenetre) && c.touche(ecran) &&", "    c.touche(fenetre) &&")]),
    ("outside its window kept", UIA, [("    c.touche(fenetre) && c.touche(ecran) &&", "    c.touche(ecran) &&")]),
    ("a table walked", UIA, [("    !matches!(s, Sorte::Commande(_) | Sorte::Donnees)", "    !matches!(s, Sorte::Commande(_))")]),
    ("a command walked", UIA, [("    !matches!(s, Sorte::Commande(_) | Sorte::Donnees)", "    !matches!(s, Sorte::Donnees)")]),
    ("a page not inherited", UIA, [("page: self.page || s == Sorte::Page", "page: s == Sorte::Page")]),
    ("the system's message box no dialog", UIA, [('    classe == "#32770" || proprietaire_attend || modale', "    proprietaire_attend || modale")]),
    ("a window its owner waits on no dialog", UIA, [('    classe == "#32770" || proprietaire_attend || modale', '    classe == "#32770" || modale')]),
    ("UIA's modal no dialog", UIA, [('    classe == "#32770" || proprietaire_attend || modale', '    classe == "#32770" || proprietaire_attend')]),
    ("a form taken for a choice", UIA, [("    dialogue && boutons >= 2 && !elements.iter().any(|e| matches!(e.genre, Genre::Champ | Genre::Liste))", "    dialogue && boutons >= 2")]),
    ("one button taken for a choice", UIA, [("    dialogue && boutons >= 2 &&", "    dialogue && boutons >= 1 &&")]),
    ("the title bar's close counted as a choice", UIA, [("e.genre == Genre::Bouton && !e.barre_titre).count()", "e.genre == Genre::Bouton).count()")]),
    ("names not cut", UIA, [('.join(" ").chars().take(NOM_MAX).collect()', '.join(" ").chars().collect()')]),
    ("a quote kept in a name", UIA, [(".map(|c| if c == '\"' { '\\'' } else { c })", "")]),
    ("hidden marks kept in a name", UIA, [(".filter(|&c| !invisible(c) || c.is_whitespace())", "")]),
    ("a title bar's button pointed at", UIA, [("    if e.barre_titre || e.choix {", "    if e.choix {")]),
    ("a ring in a dialog that waits on a choice", UIA, [("    if e.barre_titre || e.choix {", "    if e.barre_titre {")]),
    ("a name that begins with the label beats the label itself", UIA, [("        Accord::Prefixe => (!liste.elements.iter().any(|o| !o.barre_titre && accord(libelle, &o.nom) == Some(Accord::Egal))).then_some(e),", "        Accord::Prefixe => Some(e),")]),
    ("a prefix by letters, not words", UIA, [("    (b.starts_with(&a) || coupe).then_some(Accord::Prefixe)", "    (b.join(\" \").starts_with(&a.join(\" \")) || coupe).then_some(Accord::Prefixe)")]),
    ("a short name taken for a cut one", UIA, [("    let coupe = nom.chars().count() >= NOM_MAX && a.join", "    let coupe = a.join")]),
    ("icon words kept", UIA, [(".filter(|w| !w.contains('_'))", "")]),
    ("accents compared", UIA, [(".flat_map(char::to_lowercase).map(sans_accent)", ".flat_map(char::to_lowercase)")]),
    ("a canvas's title bar taken for a tree", UIA, [("    liste.elements.iter().all(|e| e.barre_titre)", "    liste.elements.is_empty()")]),
    ("a remote desktop's program not known", UIA, [("    DISTANTS.contains(&exe(app).as_str())", "    DISTANTS.is_empty()")]),
    ("a browser not known", UIA, [("    NAVIGATEURS.contains(&exe(app).as_str())", "    NAVIGATEURS.is_empty()")]),
    ("no read as the guide opens", UIA, [("        let _ = envoi.send(Travail { cible: None, cachees: Vec::new(), reponse: None, echeance: None });\n", "")]),
    ("a list nobody waits for read all the same", UIA, [("                if t.echeance.is_some_and(|e| Instant::now() >= e) {\n                    continue;\n                }\n", "")]),
    ("a slow list waited for", UIA, [("    recu.recv_timeout(delai).map_err(|_| Rate::Indisponible)?", "    recu.recv().map_err(|_| Rate::Indisponible)?")]),
    ("password zones at the screen's place, not the capture's", UIA, [("    let at = |v: i32, o: i32| v.saturating_sub(o).max(0) as u32;", "    let at = |v: i32, _o: i32| v.max(0) as u32;")]),
    ("no identity checked before", UIA, [("    if !toujours() {\n        return Err(Rate::Introuvable);\n    }\n    let lu = lire()?;", "    let lu = lire()?;")]),
    ("no identity checked after", UIA, [("    let lu = lire()?;\n    if !toujours() {\n        return Err(Rate::Introuvable);\n    }\n", "    let lu = lire()?;\n")]),
    ("the reader kept after the guide", UIA, [("            for t in recu {", "            for t in recu.iter().chain(std::iter::from_fn(|| { std::thread::park(); None })) {")]),
]

VM = [
    # A list's row lies in its list in Edge and WinForms (spike: a listitem or an option without its list
    # is a Group), and a list is never walked: the two are taken out together.
    ("a list walked and its rows taken for commands", WIN, [("    (UIA_ListControlTypeId, Sorte::Donnees),", "    (UIA_ListControlTypeId, Sorte::Autre),"),
                                                           ("    (UIA_ListItemControlTypeId, Sorte::Donnees),", "    (UIA_ListItemControlTypeId, Sorte::Commande(Genre::Bouton)),")], [EDGE, ATELIER]),
    ("a row out of a table taken for a command", WIN, [("    (UIA_DataItemControlTypeId, Sorte::Donnees),", "    (UIA_DataItemControlTypeId, Sorte::Commande(Genre::Bouton)),")], [EDGE]),
    ("texts kept", WIN, [("const SORTES: [(UIA_CONTROLTYPE_ID, Sorte); 21] = [", "const SORTES: [(UIA_CONTROLTYPE_ID, Sorte); 22] = [\n    (UIA_TextControlTypeId, Sorte::Commande(Genre::Champ)),")], [EDGE]),
    ("a page's <nav> no bar", WIN, [("        None if repere == UIA_NavigationLandmarkTypeId.0 => Sorte::Barre,\n", "")], [EDGE]),
    ("no depth limit", WIN, [("        if profondeur > PROFONDEUR_MAX {", "        if profondeur > usize::MAX - 1 {")], [EDGE]),
    ("password fields not given", WIN, [("            self.secrets.push(c);", "            let _ = c;")], [ATELIER, EDGE]),
    ("a window that waits on a dialog read", WIN, [("            if !active(w) {\n                continue;\n            }", "            let _ = active(w);")], [MESSAGE, QUESTION]),
    ("boxes in logical pixels", WIN, [("        let _physical = Physical::enter();\n        let cible = match cible {", "        let cible = match cible {")], [UNAWARE, ATELIER, EDGE]),
    ("other programs' windows read", WIN, [("    en_ordre().into_iter().filter(|&h| pid_of(h) == pid && shown(h)", "    en_ordre().into_iter().filter(|&h| shown(h)")], [ATELIER, EDGE]),
    ("the window's identity not checked", WIN, [("            || same_window(&cible, identity(h).as_ref()),", "            || true,")], [TOILE]),
    ("a dialog's choice not said", WIN, [("                p.elements.iter_mut().for_each(|e| e.choix = true);\n                liste.choix.push(f);", "                let _ = f;")], [MESSAGE, QUESTION]),
    ("the dialog's guard counts what is seen only", WIN, [("attend, modale), &p.toutes) {", "attend, modale), &p.elements) {")], [MESSAGE]),
    ("not a browser to the walk", WIN, [("navigateur: navigateur(&cible.app), cachees", "navigateur: false, cachees")], [EDGE]),
    ("a field's value for its name", WIN, [("            let nom = unsafe { el.CachedName() }.map(|b| court(&b.to_string())).unwrap_or_default();",
                                          "            let nom = unsafe { el.GetCurrentPropertyValue(UIA_ValueValuePropertyId) }.ok().and_then(|v| windows::core::BSTR::try_from(&v).ok()).map(|b| b.to_string()).filter(|v| !v.is_empty()).map(|v| court(&v)).unwrap_or_else(|| unsafe { el.CachedName() }.map(|b| court(&b.to_string())).unwrap_or_default());")], [ATELIER, EDGE]),
    ("no read as the guide opens (on Edge)", UIA, [("        let _ = envoi.send(Travail { cible: None, cachees: Vec::new(), reponse: None, echeance: None });\n", "")], [CHAUD]),
    ("a command under another window listed", WIN, [(" && montre(self.hwnd, &c) {", " {")], [COUVERT]),
    ("a remote desktop's list not emptied", WIN, [("                if distant(&cible.app) {\n                    return Ok(Liste::default());\n                }\n", "")], [DISTANT]),
    # Expected to survive, said in the PR: redundant on these VMs.
    ("a window on another screen read (one screen on a VM)", WIN, [(".filter(|f| f.touche(&ecran))", "")], [ATELIER]),
    ("UIA's modal not read (its owner waits too)", WIN, [("            let modale = unsafe {", "            let modale = false && unsafe {")], [QUESTION]),
]
EXPECTED = {"a window on another screen read (one screen on a VM)", "UIA's modal not read (its owner waits too)"}


def run(root, args):
    return subprocess.run(["cargo", "test", "--locked", "--lib", "--"] + args, cwd=root, capture_output=True, text=True)


def main():
    root, which = sys.argv[1], sys.argv[2]
    local = which == "local"
    only = sys.argv[3:]
    base = run(root, [U] if local else ["--ignored", "--exact", "--test-threads=1"] + [W + t for t in (ATELIER, UNAWARE, EDGE, MESSAGE, QUESTION, SAISIE, TOILE, CHAUD, COUVERT, DISTANT)]
               + ([W + "uia_remoteapp"] if "--distance" in only else []))
    print("\n".join((base.stdout + base.stderr).strip().splitlines()[-12:]))
    if base.returncode != 0:
        sys.exit("baseline red: no mutation means anything")
    survived, red = [], 0
    for m in (LOCAL if local else VM):
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
                    rc = run(root, [t] if local else ["--ignored", "--exact", "--test-threads=1", W + t])
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
