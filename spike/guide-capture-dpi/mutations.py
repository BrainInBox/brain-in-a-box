# Throwaway (branch spike/guide-capture-dpi-mutations): each protection of the capture at another scale
# (src/capture/win.rs) taken out, the desktop tests named echelle must fail (not fail to build).
# Run from app/src-tauri on a Windows runner whose screen is at 125 or 150 % (BIBO_ECHELLE).
import re
import subprocess
import sys

WIN = "src/capture/win.rs"
MUTATIONS = [
    ("decoupe_sans_le_rapport", "    let mine = |v: i32| in_image(v, r);\n    let keep", "    let mine = |v: i32| in_image(v, 1.0);\n    let keep"),
    ("dialogue_au_pas_de_l_ecran",
     "lay(&mut image, &dialog, in_image(its.left - at.left, r).into(), in_image(its.top - at.top, r).into());",
     "lay(&mut image, &dialog, (its.left - at.left).into(), (its.top - at.top).into());"),
    ("rapport_toujours_1", "    if own == 0 || read.is_err() || screen == 0 {", "    if own == 0 || read.is_err() || screen == 0 || own > 0 {"),
    ("dpi_de_la_fenetre_ignore", "(GetDpiForWindow(h), GetDpiForMonitor(", "(96, GetDpiForMonitor("),
    ("toile_a_la_taille_de_l_ecran", "    let room = |v: i32| (f64::from(v) * r.max(1.0)).ceil() as i32;", "    let room = |v: i32| v;"),
    ("dialogue_a_sa_propre_echelle", "        if own != r && w <= MAX_SIDE", "        if false && own != r && w <= MAX_SIDE"),
    ("dialogue_30_px_trop_haut", "in_image(its.top - at.top, r).into());", "(in_image(its.top - at.top, r) - 30).into());"),
]

source = open(WIN, encoding="utf-8").read()
survived = []
for name, old, new in MUTATIONS:
    assert source.count(old) == 1, name
    open(WIN, "w", encoding="utf-8").write(source.replace(old, new))
    try:
        r = subprocess.run(["cargo", "test", "--locked", "--lib", "--", "--ignored", "echelle", "--test-threads=1", "--nocapture"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        open(WIN, "w", encoding="utf-8").write(source)
    out = r.stdout + r.stderr
    ran = re.search(r"running \d+ tests?", out) is not None
    failed = r.returncode != 0 and ran and "test result: FAILED" in out
    for l in out.splitlines():
        if " at 1" in l and "window " in l or "panicked" in l or l.strip().startswith(("nothing left", "its ", "the dialog")) or l.startswith("test capture"):
            print("  " + l.strip())
    print(f"MUTATION {name}: {'rouge' if failed else ('NE COMPILE PAS' if not ran else 'SURVIT')}", flush=True)
    if not failed:
        survived.append(name)
print(f"{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} rouges" + (f", survivantes : {survived}" if survived else ""))
sys.exit(1 if survived else 0)
