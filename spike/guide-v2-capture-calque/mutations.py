"""Throwaway (spike/guide-v2-capture-calque): each protection of W1, W3, W4 taken out in turn must turn
at least one desktop test red. Run from spike/guide-v2-capture-calque on a Windows VM, at High integrity
(the runner's): the admin probe runs the tests at Medium through moyen.exe."""
import json
import os
import subprocess
import sys
import time

TEMP = os.environ.get("RUNNER_TEMP", os.getcwd())
# windows-2022 or windows-2025: some protections only matter on one of them.
OS = os.environ.get("MATRIX_OS", "")
FW = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")

P = "src/prims.rs"
C = "src/compose.rs"
N = "src/navigateur.rs"
K = "src/calque.rs"
V = "src/veille.rs"
TA = "calque-tauri/src/main.rs"
TE = "src/tests.rs"

APPLI = "bureau_w1_appli_composee"
EDGE = "bureau_w1_edge"
ADMIN = "admin"
CALQUE = "bureau_w3_calque"
TAURI = "bureau_w3_calque_tauri"
CALME = "bureau_w4_calme_60s"
DIALOGUE = "bureau_w4_dialogue"
HOOK = "aucun_hook_clavier_souris"

MUTATIONS = [
    # (name, expected to turn red? (or {os: bool}), file, old, new, tests)
    ("W1 BitBlt de l'écran quand PrintWindow échoue (admin)", True, P,
     "    if !unsafe { PrintWindow(h, canvas.dc, PRINT_WINDOW_FLAGS(PW_RENDERFULLCONTENT)) }.as_bool() {\n        return None;\n    }\n",
     "    if !unsafe { PrintWindow(h, canvas.dc, PRINT_WINDOW_FLAGS(PW_RENDERFULLCONTENT)) }.as_bool() {\n        return screen_copy(outer).map(|s| (s, outer));\n    }\n",
     [ADMIN]),
    ("W1 toutes les fenêtres visibles, pas seulement celles du processus", True, C,
     "!minimized(h) && pid_of(h) == pid)", "!minimized(h) && pid_of(h) != 0)", [APPLI]),
    ("W1 le reste pas noir : l'écran sous l'appli", True, C,
     "let mut img = Rgba::black((b.right - b.left) as u32, (b.bottom - b.top) as u32);",
     "let mut img = crate::prims::screen_copy(b)?;", [APPLI]),
    ("W1 seulement la fenêtre de devant (dialogue, palette et menus perdus)", True, C,
     ".filter(|&h| !nav || h == front || owned_by(h, front))", ".filter(|&h| { let _ = nav; h == front })", [APPLI]),
    ("W1 navigateur : toutes ses fenêtres (autres sites)", True, C,
     ".filter(|&h| !nav || h == front || owned_by(h, front))", ".filter(|&h| { let _ = (nav, owned_by(h, front)); true })", [EDGE]),
    ("W1 navigateur : le site ni avant ni après (tout onglet passe)", True, N,
     [("        if avant.as_deref() != Some(permis) {\n            return Err(Refus::Domaine(avant));\n        }\n", "        let _ = (&avant, permis);\n"),
      ("        if apres.as_deref() != Some(permis) {\n            return Err(Refus::Domaine(apres));\n        }\n", "        let _ = &apres;\n")], None, [EDGE]),
    ("W1 bande des onglets pas noircie", True, N,
     "masquer_bande(&mut c, bande, frame(self.front));", "let _ = (bande, frame(self.front));", [EDGE]),
    ("W1 site revérifié après la capture : retiré", True, N,
     "        let apres = self.domaine();\n        if apres.as_deref() != Some(permis) {\n            return Err(Refus::Domaine(apres));\n        }\n", "", [EDGE]),
    ("W1 site vérifié avant la capture : retiré (attendu : survit, l'après suffit)", False, N,
     "        let avant = self.domaine();\n        if avant.as_deref() != Some(permis) {\n            return Err(Refus::Domaine(avant));\n        }\n", "", [EDGE]),
    ("W3 pas de WDA_EXCLUDEFROMCAPTURE", True, K,
     "    unsafe { SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE) }\n", "    let _ = h;\n    Ok(())\n", [CALQUE]),
    ("W3 Win32 par alpha par pixel (UpdateLayeredWindow) au lieu de la clé de couleur", {"windows-2022": True, "windows-2025": False}, TE,
     "const WIN32_RETENU: Sorte = Sorte::Win32(Mode::Cle);", "const WIN32_RETENU: Sorte = Sorte::Win32(Mode::Ulw);", [CALQUE]),
    ("W3 Win32 clé de couleur fausse (le fond du calque peint)", True, K,
     "SetLayeredWindowAttributes(h, COLORREF(CLE), 0, LWA_COLORKEY)", "SetLayeredWindowAttributes(h, COLORREF(0x00ff_ffff), 0, LWA_COLORKEY)", [CALQUE]),
    ("W3 pas de WS_EX_TRANSPARENT", True, K,
     "WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST", "WS_EX_LAYERED | WS_EX_TOPMOST", [CALQUE]),
    ("W3 activé à l'affichage (ni WS_EX_NOACTIVATE ni SW_SHOWNOACTIVATE)", True, K,
     [("WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;\n", "WS_EX_TOOLWINDOW;\n"),
      ("ShowWindow(h, SW_SHOWNOACTIVATE)", "ShowWindow(h, windows::Win32::UI::WindowsAndMessaging::SW_SHOW)")], None, [CALQUE]),
    ("W3 pas de remise au-dessus toutes les 250 ms", True, K,
     "                SetTimer(Some(h), 1, REHAUSSE_MS, None);\n", "", [CALQUE]),
    ("W3 ni WS_EX_TOPMOST ni remise au-dessus", True, K,
     [("WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW", "WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW"),
      ("                SetTimer(Some(h), 1, REHAUSSE_MS, None);\n", "")], None, [CALQUE]),
    ("W3 Tauri sans set_ignore_cursor_events (ordre retenu)", True, TA,
     "                    win.set_ignore_cursor_events(true)?;\n                    r\n", "                    r\n", [TAURI]),
    ("W3 Tauri sans WDA_EXCLUDEFROMCAPTURE", True, TA,
     "let exclure = || unsafe { SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) }.map_err(|e| e.code());",
     "let exclure = || Ok::<(), windows::core::HRESULT>(());", [TAURI]),
    ("W3 Tauri affinité posée après set_ignore_cursor_events (ordre « apres »)", {"windows-2022": True, "windows-2025": False}, TE,
     'const TAURI_RETENU: &str = "avant";', 'const TAURI_RETENU: &str = "apres";', [TAURI]),
    ("W3 Tauri focusable et activé", True, TA,
     [(".focusable(false)", ".focusable(true)"), (".focused(false)", ".focused(true)")], None, [TAURI]),
    ("W4 tout changement compte (seuil de part retiré)", True, V,
     "if change < SEUIL_PART {", "if change <= 0.0 {", [CALME]),
    ("W4 pas d'attente que l'image tienne", True, V,
     "        if !stable {\n", "        if false && !stable {\n", [DIALOGUE]),
    ("W4 pas d'écart minimal de 5 s", True, V,
     "if self.dernier_envoi.is_some_and(|t| maintenant.duration_since(t) < ECART_MIN) {", "if false {", [DIALOGUE]),
    ("W4 seuil par pixel à 0 (attendu : survit)", False, V,
     "pub const SEUIL_PIXEL: u8 = 12;", "pub const SEUIL_PIXEL: u8 = 0;", [CALME]),
    ("W4 un hook clavier", True, V,
     "/// How long since the last key", "#[allow(dead_code)]\nconst _CROCHET: &str = \"SetWindowsHookExW(WH_KEYBOARD_LL)\";\n\n/// How long since the last key", [HOOK]),
]

def build_tauri():
    out = subprocess.run(["cargo", "build", "--locked", "--manifest-path", "calque-tauri/Cargo.toml"], capture_output=True, text=True)
    return out.returncode == 0, out.stderr[-3000:]


def build():
    out = subprocess.run(["cargo", "test", "--locked", "--no-run", "--message-format=json"], capture_output=True, text=True)
    if out.returncode != 0:
        return None, out.stderr[-3000:]
    exe = None
    for line in out.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("reason") == "compiler-artifact" and msg.get("executable") and msg.get("profile", {}).get("test"):
            exe = msg["executable"]
    return exe, ""


def csc(src, out, winexe):
    args = [FW, "/nologo", "/target:" + ("winexe" if winexe else "exe"), "/out:" + out, src]
    if winexe:
        args[3:3] = ["/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"]
    subprocess.run(args, check=True)


MOYEN = os.path.join(TEMP, "moyen.exe")
ATELIER = os.path.join(TEMP, "atelier-admin.exe")
csc("moyen.cs", MOYEN, False)
csc("atelier.cs", ATELIER, True)


def run(exe, test):
    if test != ADMIN:
        rc = subprocess.run([exe, "--ignored", "--exact", "tests::" + test, "--test-threads=1", "--nocapture"], capture_output=True, text=True, timeout=900)
        return rc.returncode, (rc.stdout + rc.stderr)
    admin = subprocess.Popen([ATELIER, "fenetre", "Atelier Admin", "80", "80", "500", "400", "1e88e5"])
    try:
        time.sleep(3)
        out = os.path.join(TEMP, "sonde.txt")
        cmd = os.path.join(TEMP, "sonde.cmd")
        if os.path.exists(out):
            os.remove(out)
        with open(cmd, "w", encoding="ascii") as f:
            f.write('"%s" --ignored --exact tests::sonde_admin --nocapture --test-threads=1 > "%s" 2>&1\r\n' % (exe, out))
        subprocess.run([MOYEN, 'cmd.exe /c "%s"' % cmd], timeout=300)
        text = open(out, encoding="utf-8", errors="replace").read() if os.path.exists(out) else "no output"
        return (0 if "test result: ok" in text else 1), text
    finally:
        admin.kill()


def apply(path, pairs):
    text = open(path, encoding="utf-8").read()
    new = text
    for old, rep in pairs:
        if new.count(old) != 1:
            return text, None, "%r found %d times" % (old[:70], new.count(old))
        new = new.replace(old, rep)
    return text, new, ""


exe, err = build()
if not exe:
    sys.exit("baseline does not build: " + err)
tests = sorted({t for m in MUTATIONS for t in m[-1]})
red = []
for t in tests:
    rc, text = run(exe, t)
    print("== baseline %s: exit %d" % (t, rc))
    print("\n".join("   " + l for l in text.strip().splitlines()[-15:]))
    if rc != 0:
        red.append(t)
if red:
    sys.exit("baseline red (%s): no mutation means anything" % ", ".join(red))

results = []
for m in MUTATIONS:
    name, expect_red, path = m[0], m[1], m[2]
    if isinstance(expect_red, dict):
        expect_red = expect_red[OS]
    pairs = m[3] if isinstance(m[3], list) else [(m[3], m[4])]
    tests_m = m[-1]
    text, new, why = apply(path, pairs)
    if new is None:
        print("MISSING %s: %s" % (name, why))
        results.append((name, expect_red, "absente"))
        continue
    open(path, "w", encoding="utf-8", newline="\n").write(new)
    try:
        if path.startswith("calque-tauri/"):
            ok, err = build_tauri()
            exe_m = exe if ok else None
        else:
            exe_m, err = build()
        if not exe_m:
            print("== %s: does not build\n%s" % (name, err))
            results.append((name, expect_red, "ne compile pas"))
            continue
        reds = []
        for t in tests_m:
            rc, out = run(exe_m, t)
            print("== %s / %s: exit %d" % (name, t, rc))
            print("\n".join("   " + l for l in out.strip().splitlines()[-14:]))
            if rc != 0:
                reds.append(t)
        results.append((name, expect_red, "ROUGE" if reds else "survit"))
    finally:
        open(path, "w", encoding="utf-8", newline="\n").write(text)
        if path.startswith("calque-tauri/"):
            build_tauri()

print("\nMUTATIONS")
bad = []
for name, expect_red, got in results:
    ok = (got == "ROUGE") == expect_red
    print("MUTATION %s : %s%s" % (name, got, "" if ok else "  <-- inattendu"))
    if not ok:
        bad.append(name)
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write("### Mutations\n\n| Mutation | Résultat |\n|---|---|\n")
        for name, expect_red, got in results:
            f.write("| %s | %s |\n" % (name, got))
sys.exit(1 if bad else 0)
