# Throwaway (branch spike/guide-capture-dpi-mutations): each protection of the capture at another scale
# (src/capture/win.rs and mod.rs) taken out, the desktop tests named echelle must fail (not fail to build).
# Run from app/src-tauri on a Windows runner whose screen is at 125 or 150 % (BIBO_ECHELLE).
import re
import subprocess
import sys

WIN, MOD = "src/capture/win.rs", "src/capture/mod.rs"
MUTATIONS = [
    ("decoupe_sans_le_rapport", WIN, "    let mine = |v: i32| in_image(v, r);\n    let keep", "    let mine = |v: i32| in_image(v, 1.0);\n    let keep"),
    ("decoupe_decalee_de_2_px", WIN, "    let mine = |v: i32| in_image(v, r);\n    let keep", "    let mine = |v: i32| in_image(v, r) + 2;\n    let keep"),
    ("dialogue_au_pas_de_l_ecran", WIN,
     "lay(&mut image, &dialog, in_image(its.left - at.left, r).into(), in_image(its.top - at.top, r).into());",
     "lay(&mut image, &dialog, (its.left - at.left).into(), (its.top - at.top).into());"),
    ("rapport_toujours_1", MOD, "    if per_monitor || !physical || dpi == 0 || screen == 0 {", "    if true {"),
    ("dpi_de_la_fenetre_ignore", WIN, "(GetDpiForWindow(h), read, ", "(96, read, "),
    ("fil_suppose_physique", WIN, "per_monitor(window), per_monitor(thread))", "per_monitor(window), true)"),
    ("toile_a_la_taille_de_l_ecran", MOD, "        let side = |v: i32| (f64::from(v) * r.max(1.0)).ceil() as i32;", "        let side = |v: i32| v;"),
    ("dialogue_a_sa_propre_echelle", WIN, "        if own != r {", "        if false && own != r {"),
    ("dialogue_30_px_trop_haut", WIN, "in_image(its.top - at.top, r).into());", "(in_image(its.top - at.top, r) - 30).into());"),
]

survived = []
for name, path, old, new in MUTATIONS:
    source = open(path, encoding="utf-8").read()
    assert source.count(old) == 1, name
    open(path, "w", encoding="utf-8").write(source.replace(old, new))
    try:
        r = subprocess.run(["cargo", "test", "--locked", "--lib", "--", "--ignored", "echelle", "--test-threads=1", "--nocapture"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        open(path, "w", encoding="utf-8").write(source)
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
