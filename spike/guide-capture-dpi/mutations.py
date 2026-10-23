# Throwaway (branch spike/guide-capture-dpi-mutations): each protection of a stretched window's
# capture (src/capture/win.rs) taken out, the desktop test bureau_echelle must fail (not fail to build).
# Run from app/src-tauri on a Windows runner whose screen is at 125 or 150 %.
import subprocess
import sys

WIN = "src/capture/win.rs"
MUTATIONS = [
    ("decoupe_a_la_taille_de_l_ecran", "    let r = image_ratio(h);\n    let mine", "    let r = 1.0;\n    let mine"),
    ("dialogue_au_pas_de_l_ecran", "        let r = image_ratio(h);\n        lay(", "        let r = 1.0;\n        lay("),
    ("rapport_toujours_1", "    if own == 0 || read.is_err() || screen == 0 {", "    if own == 0 || read.is_err() || screen == 0 || own > 0 {"),
]

source = open(WIN, encoding="utf-8").read()
survived = []
for name, old, new in MUTATIONS:
    assert source.count(old) == 1, name
    open(WIN, "w", encoding="utf-8").write(source.replace(old, new))
    try:
        r = subprocess.run(["cargo", "test", "--locked", "--lib", "--", "--ignored", "bureau_echelle", "--test-threads=1", "--nocapture"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        open(WIN, "w", encoding="utf-8").write(source)
    out = r.stdout + r.stderr
    ran = "running 1 test" in out
    failed = r.returncode != 0 and ran and "test result: FAILED" in out
    for l in out.splitlines():
        if l.startswith("stretched ") or "panicked" in l or l.strip().startswith(("nothing left", "its own size", "the dialog at")):
            print("  " + l.strip())
    print(f"MUTATION {name}: {'rouge' if failed else ('NE COMPILE PAS' if not ran else 'SURVIT')}", flush=True)
    if not failed:
        survived.append(name)
print(f"{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} rouges" + (f", survivantes : {survived}" if survived else ""))
sys.exit(1 if survived else 0)
