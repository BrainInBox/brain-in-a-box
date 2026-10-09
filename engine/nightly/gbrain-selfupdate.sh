#!/usr/bin/env zsh
# gbrain self-update: pull -> install -> migrate -> smoke-test, with automatic
# rollback on a broken build. Extracted from gbrain-nightly.sh so it can also
# be invoked by the SessionStart catch-up hook (gbrain-update-check.py) on a
# machine that missed the 04:00 nightly run. Resilient: a failed pull/install
# just gives up silently (git pull --ff-only won't touch a diverged repo);
# only a *broken* update (fails its smoke test) triggers the rollback path.
#
# Usage: gbrain-selfupdate.sh [log-file]   (default: ~/.gbrain/nightly.log)
#
# Writes:
#   ~/.gbrain/last-update.json         machine-readable result of the last run
#   <vault>/Profile/memory.md          human-readable block, ONLY when the
#                                       version actually changed (updated or
#                                       rolled back) — no daily "up to date"
#                                       noise on every check.

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin"
GBRAIN="$HOME/.bun/bin/gbrain"
BUN="$HOME/.bun/bin/bun"
GREPO="$HOME/DEV/gbrain"
VAULT="$HOME/Documents/Brain"
STATUS="$HOME/.gbrain/last-update.json"
LOG="${1:-$HOME/.gbrain/nightly.log}"

mkdir -p "$HOME/.gbrain"

write_status() {  # <status> <before> <after> <changelog-json-array>
    python3 - "$STATUS" "$1" "$2" "$3" "$4" <<'PY'
import json, sys, time
path, status, before, after, changelog = sys.argv[1:6]
json.dump({
    "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "status": status,
    "before": before,
    "after": after,
    "changelog": json.loads(changelog) if changelog else [],
}, open(path, "w"))
PY
}

update_memory_block() {  # <emoji> <message>
    [ -d "$VAULT/Profile" ] || return 0
    python3 - "$VAULT/Profile/memory.md" "$1" "$2" <<'PY'
import re, sys
from pathlib import Path
path, emoji, msg = sys.argv[1], sys.argv[2], sys.argv[3]
p = Path(path)
block = f"<!-- GBRAIN-UPDATE:BEGIN -->\n> **{emoji} gbrain self-update** — {msg}\n<!-- GBRAIN-UPDATE:END -->"
mem = p.read_text() if p.exists() else "# Memory\n"
if "<!-- GBRAIN-UPDATE:BEGIN -->" in mem:
    mem = re.sub(r"<!-- GBRAIN-UPDATE:BEGIN -->.*?<!-- GBRAIN-UPDATE:END -->", block, mem, flags=re.S)
else:
    # Frontmatter-aware insert (same rule as the weekly lint block — never
    # land inside a YAML frontmatter block, it corrupts it).
    fm = re.match(r"^---\n.*?\n---\n", mem, re.S)
    at = fm.end() if fm else 0
    mem = mem[:at] + "\n" + block + "\n" + mem[at:]
p.write_text(mem)
PY
}

# Prints the Bun version gbrain's package.json requires ("engines.bun") when the
# installed Bun is older; prints nothing when it is recent enough or unreadable.
bun_shortfall() {
    python3 - "$GREPO/package.json" "$("$BUN" --version 2>/dev/null)" <<'PY'
import json, re, sys
try:
    spec = json.load(open(sys.argv[1])).get("engines", {}).get("bun", "")
except Exception:
    sys.exit(0)
need = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", spec)
have = re.match(r"(\d+)\.(\d+)\.(\d+)", sys.argv[2])
if need and have:
    need = tuple(int(x or 0) for x in need.groups())
    if tuple(map(int, have.groups())) < need:
        print(".".join(map(str, need)))
PY
}

[ -d "$GREPO/.git" ] || exit 0
cd "$GREPO" || exit 0

before=$(git rev-parse --short HEAD 2>/dev/null)
git pull --ff-only >> "$LOG" 2>&1 || exit 0
after=$(git rev-parse --short HEAD 2>/dev/null)

[ "$before" = "$after" ] && exit 0
[ -z "$after" ] && exit 0

echo "[update] gbrain $before -> $after" >> "$LOG"

# A gbrain that needs a newer Bun refuses to start: the smoke test fails, the
# update is rolled back, and the next night pulls the same commit again —
# stuck forever, since nothing upgraded Bun. Upgrade it first, keeping the old
# binary so a rollback returns to the exact state that worked.
need=$(bun_shortfall)
bun_prev=""
bun_stuck=""
if [ -n "$need" ]; then
    echo "[update] gbrain $after needs Bun >= $need, found $("$BUN" --version 2>/dev/null) — running bun upgrade" >> "$LOG"
    bun_prev="$HOME/.gbrain/bun.pre-upgrade"
    cp -p "$BUN" "$bun_prev"
    "$BUN" upgrade >> "$LOG" 2>&1
    bun_stuck=$(bun_shortfall)
    [ -n "$bun_stuck" ] && echo "[update] bun upgrade did not reach $need" >> "$LOG"
fi

"$BUN" install >> "$LOG" 2>&1
"$BUN" link >> "$LOG" 2>&1
"$GBRAIN" apply-migrations --yes >> "$LOG" 2>&1 || echo "[update] migrations non-fatal" >> "$LOG"

# Smoke test: the same health check the weekly lint already trusts to mean
# "gbrain works" (gbrain-lint.sh). If the new version fails it, revert.
if "$GBRAIN" doctor --fast --json >/dev/null 2>>"$LOG"; then
    echo "[update] smoke test OK" >> "$LOG"
    changelog=$(awk '/^- /{print; c++} c==3{exit}' CHANGELOG.md 2>/dev/null)
    cl_json=$(printf '%s\n' "$changelog" | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')
    write_status "updated" "$before" "$after" "$cl_json"
    flat=$(printf '%s' "$changelog" | tr '\n' ' ' | sed 's/  */ /g')
    bun_note=""
    if [ -n "$bun_prev" ]; then
        rm -f "$bun_prev"
        bun_note=" Bun upgraded to $("$BUN" --version 2>/dev/null)."
    fi
    update_memory_block "🔄" "\`$before\` → \`$after\`.$bun_note $flat"
else
    echo "[update] SMOKE TEST FAILED — rolling back $after -> $before" >> "$LOG"
    git reset --hard "$before" >> "$LOG" 2>&1
    if [ -n "$bun_prev" ]; then
        cp -p "$bun_prev" "$BUN" && rm -f "$bun_prev" \
            && echo "[update] Bun restored to $("$BUN" --version 2>/dev/null)" >> "$LOG"
    fi
    "$BUN" install >> "$LOG" 2>&1
    "$BUN" link >> "$LOG" 2>&1
    write_status "rolled_back" "$before" "$after" "[]"
    if [ -n "$bun_stuck" ]; then
        update_memory_block "⚠️" "\`$after\` needs Bun >= $bun_stuck and \`bun upgrade\` could not get there — run \`bun upgrade\` by hand; still on \`$before\`. Check \`~/.gbrain/nightly.log\`."
    else
        update_memory_block "⚠️" "tried \`$before\` → \`$after\`, broke \`gbrain doctor\` — rolled back to \`$before\` automatically. Check \`~/.gbrain/nightly.log\`."
    fi
fi
