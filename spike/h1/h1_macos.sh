#!/bin/bash
# Preuve H1 sous macOS (jetable) : install.sh épinglé dans un dossier de Bibo, à côté d'un Hermes
# « de la personne » (sentinelles), puis la même sonde de gateway, et la désinstallation.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$RUNNER_TEMP/h1out"
mkdir -p "$OUT"
ROOT="$HOME/Library/Application Support/Bibo/hermes"
USERHOME="$HOME/.hermes"
APPDATA="$HOME/Library/Application Support/Hermes"
SENT=("$USERHOME" "$APPDATA" "$HOME/.local/bin/hermes" "$HOME/.cache/hermes" "$HOME/.local/share/hermes"
      "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile")
J() { python3 -I "$HERE/h1_disk.py" "$@"; }

mkdir -p "$USERHOME" "$APPDATA" "$HOME/.local/bin" "$HOME/.cache/hermes" "$HOME/.local/share/hermes"
echo "# config Hermes de la personne (sentinelle)" > "$USERHOME/config.yaml"
echo sentinelle > "$USERHOME/sentinel.txt"
echo sentinelle > "$APPDATA/sentinel.txt"
printf '#!/bin/sh\necho hermes de la personne\n' > "$HOME/.local/bin/hermes"; chmod +x "$HOME/.local/bin/hermes"
echo sentinelle > "$HOME/.cache/hermes/sentinel.txt"; echo sentinelle > "$HOME/.local/share/hermes/sentinel.txt"
for f in .zshrc .bashrc .bash_profile .profile; do touch "$HOME/$f"; cp "$HOME/$f" "$OUT/rc0-$f"; done
J hashes "${SENT[@]}" > "$OUT/sent0.json"
export HERMES_HOME="$USERHOME" H1_USER_HERMES_HOME="$USERHOME"

curl -fsSL -o "$OUT/install.sh" "https://raw.githubusercontent.com/NousResearch/hermes-agent/$H1_HERMES_COMMIT/scripts/install.sh"
got=$(shasum -a 256 "$OUT/install.sh" | cut -d' ' -f1)
[ "$got" = "$H1_INSTALL_SH_SHA256" ] || { echo "install.sh SHA-256 mismatch: $got"; exit 1; }
t0=$(python3 -c 'import time; print(time.time())')
s=$(date +%s)
bash "$OUT/install.sh" --commit "$H1_HERMES_COMMIT" --non-interactive --skip-browser --skip-computer-use \
    --hermes-home "$ROOT" > "$OUT/install.out.log" 2>&1
ec=$?
dur=$(( $(date +%s) - s ))
echo "install exit=$ec seconds=$dur"
J size "$ROOT" > "$OUT/size_root.json"
J newfiles "$t0" --root "$HOME" --root /usr/local --exclude "$ROOT" --exclude "$OUT" --exclude "$HOME/work" \
    --exclude "$HOME/hostedtoolcache" --exclude "$HOME/actions-runner" > "$OUT/outside_root.json"
J hashes "${SENT[@]}" > "$OUT/sent1.json"
for f in .zshrc .bashrc .bash_profile .profile; do diff -u "$OUT/rc0-$f" "$HOME/$f" > "$OUT/rcdiff-$f.txt"; done
zsh -lic 'command -v hermes; readlink "$(command -v hermes)"' > "$OUT/which-hermes.txt" 2>&1 || true
[ $ec -eq 0 ] || { tail -40 "$OUT/install.out.log"; exit 1; }

python3 "$HERE/h1_probe.py" --root "$ROOT" --out "$OUT" --scan-home > "$OUT/probe-main.log" 2>&1
pec=$?
tail -30 "$OUT/probe-main.log"

L="$ROOT/hermes-agent/.hermes/bin/hermes"; [ -x "$L" ] || L="$ROOT/bin/hermes"
J hashes "${SENT[@]}" > "$OUT/sent2.json"
HERMES_HOME="$ROOT" "$L" uninstall --full --dry-run > "$OUT/uninstall-dry.txt" 2>&1
HERMES_HOME="$USERHOME" "$L" uninstall --full --dry-run > "$OUT/uninstall-dry-inherited.txt" 2>&1
s=$(date +%s)
HERMES_HOME="$ROOT" "$L" uninstall --full --yes > "$OUT/uninstall-full.txt" 2>&1
uec=$?
udur=$(( $(date +%s) - s ))
J hashes "${SENT[@]}" > "$OUT/sent3.json"
[ -e "$ROOT" ] && J size "$ROOT" > "$OUT/root_left.json"

python3 -I - "$OUT" "$ROOT" "$ec" "$dur" "$pec" "$uec" "$udur" <<'EOF'
import json, re, sys, pathlib
out, root = pathlib.Path(sys.argv[1]), sys.argv[2]
ld = lambda n: json.loads((out / n).read_text()) if (out / n).exists() else None
diff = lambda a, b: {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
s0, s1, s2, s3 = (ld(f"sent{i}.json") for i in range(4))
paths = lambda t: [p.strip() for p in re.findall(r"(/[^\n]*)", re.sub(r"\x1b\[[0-9;]*m", "", t))]
dry = paths((out / "uninstall-dry.txt").read_text())
dryi = paths((out / "uninstall-dry-inherited.txt").read_text())
rep = ld("report-main.json") or {}
res = {"install": {"exit": int(sys.argv[3]), "seconds": int(sys.argv[4]), "tail": (out / "install.out.log").read_text().splitlines()[-25:]},
       "size_root": ld("size_root.json"), "written_outside_root": ld("outside_root.json"),
       "install_sentinel_changes": diff(s0, s1), "rc_diffs": {f: (out / f"rcdiff-{f}.txt").read_text()[-1500:] for f in (".zshrc", ".bashrc", ".bash_profile", ".profile")},
       "which_hermes_new_shell": (out / "which-hermes.txt").read_text(),
       "probe_main_exit": int(sys.argv[5]), "probe_main": rep,
       "uninstall_dry_run": {"bibo_home_outside_root": [p for p in dry if not p.startswith(root)],
                             "inherited_outside_root": [p for p in dryi if not p.startswith(root)]},
       "uninstall_full": {"exit": int(sys.argv[6]), "seconds": int(sys.argv[7]), "root_exists_after": pathlib.Path(root).exists(),
                          "root_left": ld("root_left.json"), "tail": (out / "uninstall-full.txt").read_text().splitlines()[-40:]},
       "uninstall_sentinel_changes": diff(s2, s3), "probe_sentinel_changes": diff(s1, s2)}
(out / "macos.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
print(json.dumps({k: res[k] for k in ("install_sentinel_changes", "uninstall_sentinel_changes", "uninstall_dry_run")}, indent=1)[:4000])
EOF
{ echo "## H1 macOS"; echo "- install: exit $ec, ${dur} s; probe main: exit $pec; uninstall --full: exit $uec"; } >> "$GITHUB_STEP_SUMMARY"
[ $pec -eq 0 ]
