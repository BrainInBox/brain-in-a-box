"""Throwaway (branch spike/guide-v2-ecran-mutations): each protection of the guide v2's whole screen, overlay
and change detection taken out in turn must turn a desk test red on the CI's Windows (capture/vm.rs).
Run from app/src-tauri: python -I ../../spike/ecran/mutations_ecran.py <chunk>, or --check."""
import os
import subprocess
import sys
import time

OS = os.environ.get("MATRIX_OS", "")
TEMP = os.environ.get("RUNNER_TEMP", os.getcwd())
K = "src/calque.rs"
J = "../src/calque.js"
E = "src/capture/ecran.rs"
V = "src/capture/veille.rs"

CAPTURE = "vm_capture_ecran_entier"
CALQUE = "vm_calque"
INACTIF = "vm_inactivite"
CALME = "vm_veille_calme_60s"
DIALOGUE = "vm_veille_dialogue"
VERROU = "vm_verrouille"

AFFINITE = "                let _ = unsafe { SetWindowDisplayAffinity(hwnd(id), WDA_EXCLUDEFROMCAPTURE) };\n"
IGNORE = "                w2.set_ignore_cursor_events(true)\n"
APRES = (AFFINITE + IGNORE,
         "                let r = w2.set_ignore_cursor_events(true);\n" + AFFINITE + "                r\n")
RELUE = ("    lue == EXCLUE\n", "    let _ = (lue, EXCLUE);\n    true\n")
SANS_PAUSE = [("    if !os::bureau_ouvert() {\n        return Err(Refus::Pause);\n    }\n", "", 2),
              ("let pas = if pause(inactif, ecran::bureau_ouvert()) {", "let pas = if pause(inactif, true) {")]

# (name, chunk, red expected: True, False, {os: bool} or None (measured only), [(file, old, new[, count])], tests)
MUTATIONS = [
    ("T0a témoin : rien de retiré", "a", False, [], [CALQUE]),
    ("T0b témoin : rien de retiré", "b", False, [], [CAPTURE, INACTIF, CALME, DIALOGUE]),
    ("M0 rien de protégé (le « rouge avant » : la version naïve)", "b", True, [
        (K, AFFINITE, ""), (K, *RELUE),
        (E, "    noircir(&mut image, &ecran.rect, &os::fenetres_de_bibo(ecran));\n", ""),
        (E, "    noircir(&mut image, &ecran.rect, &mots_de_passe.champs(ecran));\n", ""),
        (E, "    if present_dans(&brute, &ecran.rect, &traces) {", "    if false && present_dans(&brute, &ecran.rect, &traces) {"),
        (E, "pub fn inactif() -> std::time::Duration {\n    os::inactif()\n}", "pub fn inactif() -> std::time::Duration {\n    std::time::Duration::ZERO\n}"),
        (V, "if part < SEUIL_PART || (!gestes && share(brut) < SANS_GESTE) {", "if part <= 0.0 {"),
        (V, "if !stable || maintenant.duration_since(self.depuis) < ECART_MIN {", "if false {"),
    ], [CAPTURE, CALQUE, INACTIF, CALME, DIALOGUE]),
    ("K1 pas de WDA_EXCLUDEFROMCAPTURE", "a", True, [(K, AFFINITE, "")], [CALQUE]),
    ("K2 affinité posée après set_ignore_cursor_events (2022 la refuse, attendu : survit sur 2025)", "a",
     {"windows-2022": True, "windows-2025": False}, [(K, *APRES)], [CALQUE]),
    ("K3 après set_ignore_cursor_events et non relue (attendu : survit sur 2025)", "a",
     {"windows-2022": True, "windows-2025": False}, [(K, *APRES), (K, *RELUE)], [CALQUE]),
    ("K4 pas de set_ignore_cursor_events (les clics ne traversent pas)", "a", True,
     [(K, IGNORE, "                let _ = &w2;\n                Ok::<(), tauri::Error>(())\n")], [CALQUE]),
    ("K5 pas de remise au-dessus toutes les 250 ms", "a", True,
     [(K, "            std::thread::sleep(REHAUSSE);", "            std::thread::sleep(REHAUSSE * 14_400);")], [CALQUE]),
    ("K6 focusable et activé", "a", True,
     [(K, "            .focusable(false)\n            .focused(false)\n", "            .focusable(true)\n            .focused(true)\n")], [CALQUE]),
    ("K7 la page dessinée depuis le coin de l'écran, pas le sien (2025 à 125 % la décale, attendu : survit sur 2022)", "a",
     {"windows-2025": True, "windows-2022": False},
     [(K, "w.eval(plan.script((zone.x, zone.y)))", "w.eval(plan.script((r.x, r.y)))")], [CALQUE]),
    ("K9 pas de SetWindowPos à l'affichage (mesure : tao suffit-il à la placer ?)", "a", None,
     [(K, "            let _ = unsafe { SetWindowPos(hwnd(id), Some(HWND_TOPMOST), r.x, r.y, r.largeur as i32, r.hauteur as i32, SWP_NOACTIVATE | SWP_NOOWNERZORDER) };\n", "")],
     [CALQUE]),
    ("J1 l'échelle ignorée par la page du calque", "a", True,
     [(J, "const css = (v) => `${v / window.devicePixelRatio}px`;", "const css = (v) => `${v}px`;")], [CALQUE]),
    ("E1 la pastille de Bibo pas noircie", "b", True,
     [(E, "    noircir(&mut image, &ecran.rect, &os::fenetres_de_bibo(ecran));\n", "")], [CAPTURE]),
    ("E2 le mot de passe pas noirci", "b", True,
     [(E, "    noircir(&mut image, &ecran.rect, &mots_de_passe.champs(ecran));\n", "")], [CAPTURE]),
    ("E3 le calque noirci comme une fenêtre de Bibo (l'écran entier noir)", "a", True,
     [(E, ".filter(|&h| Some(id_of(h)) != calque && shown(h) && de_bibo(h) && !exclue(h))",
       ".filter(|&h| { let _ = (calque, id_of(h), exclue(h)); shown(h) && de_bibo(h) })")], [CALQUE]),
    ("E4 une image avec le calque envoyée", "a", True,
     [(E, "    if present_dans(&brute, &ecran.rect, &traces) {", "    if false && present_dans(&brute, &ecran.rect, &traces) {")], [CALQUE]),
    ("E5 pas de pause écran verrouillé (attendu : survit si Windows ne verrouille pas la VM)", "verrou", True,
     [(E, *SANS_PAUSE[0]), (V, *SANS_PAUSE[1])], [VERROU]),
    ("W1 tout changement compte", "b", True,
     [(V, "if part < SEUIL_PART || (!gestes && share(brut) < SANS_GESTE) {", "if part <= 0.0 {")], [CALME]),
    ("W2 pas d'attente que l'image tienne", "b", True,
     [(V, "if !stable || maintenant.duration_since(self.depuis) < ECART_MIN {", "if maintenant.duration_since(self.depuis) < ECART_MIN {")], [DIALOGUE]),
    ("W3 pas d'écart de 5 s entre deux envois", "b", True,
     [(V, "if !stable || maintenant.duration_since(self.depuis) < ECART_MIN {", "if !stable {")], [DIALOGUE]),
    ("W4 ce qui bouge sans cesse compte (la vidéo bloque le dialogue)", "b", True,
     [(V, "let agite = self.histoire.iter().filter(|h| h[i]).count() >= AGITE;", "let agite = self.histoire.is_empty();")], [DIALOGUE]),
    ("W5 la même image deux fois (référence jamais renouvelée)", "b", True,
     [(V, "        self.reference = self.precedente.clone();\n", "")], [DIALOGUE]),
    ("W6 l'inactivité sans GetLastInputInfo", "b", True,
     [(E, "pub fn inactif() -> std::time::Duration {\n    os::inactif()\n}", "pub fn inactif() -> std::time::Duration {\n    std::time::Duration::ZERO\n}")], [INACTIF]),
]

VM = ["cargo", "test", "--locked", "--release", "--lib"]


def build():
    t = time.time()
    out = subprocess.run(VM + ["--no-run"], capture_output=True, encoding="utf-8", errors="replace")
    return out.returncode == 0, out.stderr[-2500:], time.time() - t


def run_test(name):
    t = time.time()
    try:
        out = subprocess.run(VM + ["--", "--ignored", "--exact", f"capture::vm::{name}", "--test-threads=1", "--nocapture"],
                             capture_output=True, encoding="utf-8", errors="replace", timeout=1200)
        text = out.stdout + out.stderr
        green = out.returncode == 0 and "1 passed" in text
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        green = False
        text += "\nTIMEOUT"
    why = [l for l in text.splitlines() if l.startswith("FAUTE") or "panicked" in l or "TIMEOUT" in l][:4]
    return green, why, time.time() - t


def check():
    """Every mutation's text found where it is expected, as many times, without running anything."""
    bad = 0
    for name, _, _, edits, _ in MUTATIONS:
        files = {}
        for edit in edits:
            path, old, new = edit[:3]
            files.setdefault(path, open(path, encoding="utf-8").read())
            n = edit[3] if len(edit) > 3 else 1
            if files[path].count(old) != n:
                print(f"{name}: {path} {old[:50]!r} found {files[path].count(old)} times, not {n}")
                bad += 1
            files[path] = files[path].replace(old, new)
    print(f"{len(MUTATIONS)} mutations, {bad} not found")
    sys.exit(1 if bad else 0)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1] == "--check":
        check()
    chunk = sys.argv[1]
    rows, wrong = [], 0
    for name, c, expected, edits, tests in MUTATIONS:
        if c != chunk:
            continue
        want = expected.get(OS, True) if isinstance(expected, dict) else expected
        saved = {}
        problem = None
        for edit in edits:
            path, old, new = edit[:3]
            n = edit[3] if len(edit) > 3 else 1
            cur = open(path, encoding="utf-8").read()
            saved.setdefault(path, cur)
            if cur.count(old) != n:
                problem = f"{path}: {old[:50]!r} found {cur.count(old)} times, not {n}"
                break
            open(path, "w", encoding="utf-8").write(cur.replace(old, new))
        try:
            if problem:
                verdict, detail = "INTROUVABLE", problem
            else:
                ok, err, secs = build()
                if not ok:
                    verdict, detail = "NE COMPILE PAS", err.splitlines()[-5:]
                else:
                    reds, detail = [], []
                    for t in tests:
                        green, why, secs = run_test(t)
                        print(f"  {name} / {t}: {'vert' if green else 'ROUGE'} en {secs:.0f} s {why}", flush=True)
                        if not green:
                            reds.append(t)
                            detail += why
                    verdict = "rouge" if reds else "survit"
                    detail = f"{reds} {detail[:3]}"
        finally:
            for path, text in saved.items():
                open(path, "w", encoding="utf-8").write(text)
        good = want is None and verdict in ("rouge", "survit") or (verdict == "rouge") == want
        wrong += 0 if good else 1
        attendu = "mesure" if want is None else "rouge" if want else "survit"
        rows.append(f"| {name} | {attendu} | {verdict} | {'ok' if good else '**NON**'} | {detail} |")
        print(rows[-1], flush=True)
    table = "\n".join([f"### Mutations, chunk {chunk} ({OS})", "", "| Mutation | Attendu | Vu | | Détail |", "|---|---|---|---|---|", *rows, ""])
    print(table)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as f:
        f.write(table + "\n")
    sys.exit(1 if wrong else 0)


main()
