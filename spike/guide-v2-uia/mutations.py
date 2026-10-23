"""Throwaway (spike/guide-v2-uia): each protection of the UIA list taken out in turn must turn at
least one desktop test red. Run from app/src-tauri on a Windows VM, the screen at 150 %."""
import subprocess
import sys

UIA = "src/capture/uia.rs"
P = "capture::win::uia::tests::"
ATELIER, EDGE, TOILE, UNAWARE = "uia_atelier_pmv2", "uia_edge_imitation", "uia_sans_arbre", "uia_atelier_unaware"

VALUE = ("            let name = unsafe { el.CachedName() }.map(|b| short(&b.to_string())).unwrap_or_default();",
         "            let name = unsafe { el.GetCachedPropertyValue(UIA_ValueValuePropertyId) }.ok()"
         ".and_then(|v| windows::core::BSTR::try_from(&v).ok()).map(|b| b.to_string()).filter(|v| !v.is_empty())"
         ".map(|v| short(&v)).unwrap_or_else(|| unsafe { el.CachedName() }.map(|b| short(&b.to_string())).unwrap_or_default());")

MUTATIONS = [
    ("commands only: texts kept too (C2)",
     [("        UIA_EditControlTypeId => \"champ\",", "        UIA_EditControlTypeId => \"champ\",\n        UIA_TextControlTypeId => \"texte\","),
      ("[UIA_DocumentControlTypeId, UIA_TitleBarControlTypeId].iter()", "[UIA_DocumentControlTypeId, UIA_TitleBarControlTypeId, UIA_TextControlTypeId].iter()")],
     [EDGE]),
    ("a table's rows read and kept (C2)",
     [("    let data = in_data || DATA.contains(&t);", "    let data = in_data && DATA.contains(&t);"),
      ("            if kind(t).is_some() || DATA.contains(&t) {", "            if kind(t).is_some() {")], [EDGE, ATELIER]),
    ("a browser's own bar kept: its tabs (C1)",
     [("(!w.browser || doc)", "(w.browser || !doc || doc)")], [EDGE]),
    ("a field's value instead of its label (C2)",
     [VALUE, ("UIA_IsPasswordPropertyId] {", "UIA_IsPasswordPropertyId, UIA_ValueValuePropertyId] {")], [ATELIER, EDGE]),
    ("names not cut to 40 characters",
     [(".join(\" \").chars().take(NOM_MAX).collect()", ".join(\" \").chars().collect()")], [ATELIER, EDGE]),
    ("what is out of view kept",
     [("if on && !offscreen && !data", "if on && !data")], [EDGE]),
    ("a hidden zone ignored",
     [(" && !w.hidden.iter().any(|h| overlaps(h, &rect))", "")], [ATELIER]),
    ("password fields not given apart",
     [("        w.passwords.push(rect);", "        let _ = rect;")], [ATELIER, EDGE]),
    ("password fields not blacked",
     [("                img.px[i..i + 4].copy_from_slice(&[0, 0, 0, 255]);", "                let _ = i;")], [ATELIER, EDGE]),
    ("boxes in logical pixels (no per-monitor context)",
     [("    let _physical = Physical::enter();\n    UIA.with", "    UIA.with")], [ATELIER, EDGE]),
    ("a stretched window's image taken at the screen's scale",
     [("            own as f64 / screen as f64", "            1.0")], [UNAWARE]),
    ("other programs' windows read",
     [("    all.into_iter().filter(|&h| pid_of(h) == pid && visible(h)", "    all.into_iter().filter(|&h| visible(h)")], [ATELIER]),
    ("the title bar's buttons count as a tree",
     [("    liste.elements.iter().all(|e| e.titlebar) || is_remote(app)", "    liste.elements.is_empty() || is_remote(app)")], [TOILE]),
    ("a remote desktop's program not recognized (expected to survive: its empty tree suffices)",
     [("    DISTANTS.contains(&exe.as_str())", "    DISTANTS.is_empty()")], ["uia_bureau_distance"]),
]
REDUNDANT = {"a remote desktop's program not recognized (expected to survive: its empty tree suffices)"}


def run(tests):
    cmd = ["cargo", "test", "--locked", "--lib", "--", "--ignored", "--exact", "--test-threads=1"] + [P + t for t in tests]
    return subprocess.run(cmd, capture_output=True, text=True)


only = sys.argv[1:]
base = run([ATELIER, UNAWARE, EDGE, TOILE] + (["uia_bureau_distance"] if "--distance" in only else []))
print("\n".join((base.stdout + base.stderr).strip().splitlines()[-30:]))
if base.returncode != 0:
    sys.exit("baseline red: no mutation means anything")

survived = []
for name, edits, tests in MUTATIONS:
    if "uia_bureau_distance" in tests and "--distance" not in only:
        continue
    text = open(UIA, encoding="utf-8").read()
    changed = text
    missing = [old for old, _ in edits if changed.count(old) != 1]
    if missing:
        print("MISSING %s: %r" % (name, [m[:60] for m in missing]))
        survived.append(name)
        continue
    for old, new in edits:
        changed = changed.replace(old, new)
    open(UIA, "w", encoding="utf-8", newline="\n").write(changed)
    try:
        reds = []
        for t in tests:
            rc = run([t])
            out = rc.stdout + rc.stderr
            print("== %s / %s: exit %d" % (name, t, rc.returncode))
            if "error[E" in out:
                print("   does not build:\n" + "\n".join("   " + l for l in out.splitlines() if "error" in l)[:2000])
                continue
            print("\n".join("   " + l for l in out.strip().splitlines() if "panicked" in l or "MESURE" in l and ("noms" in l or "ecart_max" in l or "orange" in l))[:3000])
            if rc.returncode != 0:
                reds.append(t)
                break
        if not reds and name not in REDUNDANT:
            survived.append(name)
        print("%s: %s" % ("RED" if reds else "SURVIVED", name))
    finally:
        open(UIA, "w", encoding="utf-8", newline="\n").write(text)

print("\nsurvived: %s" % (survived or "none"))
sys.exit(1 if survived else 0)
