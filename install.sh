#!/usr/bin/env bash
# brain-in-a-box — installs a personal second brain (Brain vault + hooks +
# GBrain + gbq + nightly) for the current user. Idempotent and non-destructive:
# it never overwrites an existing vault or CLAUDE.md, it merges.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="$HOME"
U="$(whoami)"
BRAIN="$H/Documents/Brain"
BRAIN_CO="$H/Documents/BrainCo"
HOOKS="$H/.claude/hooks/brain"
BIN="$H/.local/bin"
GREPO="$H/DEV/gbrain"
GDATA="$H/.gbrain"

# Flags:
#   --company <git-url>   join a team (clone company vault as 2nd GBrain source)
#   --with-gstack         also install gstack (Garry Tan's 23 AI specialists for Claude Code)
COMPANY_URL=""
WITH_GSTACK=""
while [ $# -gt 0 ]; do case "$1" in
  --company)     COMPANY_URL="${2:-}"; shift 2;;
  --with-gstack) WITH_GSTACK=1; shift;;
  *) shift;;
esac; done

say() { printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()  { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn(){ printf "  \033[1;33m⚠\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# ── 1. Preflight ────────────────────────────────────────────────────────────
say "Preflight"
[ "$(uname)" = "Darwin" ] || die "macOS required (launchd)."
command -v git >/dev/null   || die "git missing."
command -v python3 >/dev/null || die "python3 missing."
command -v claude >/dev/null || warn "claude CLI not found — install Claude Code first."
if ! command -v bun >/dev/null; then
  warn "bun missing → installing…"
  curl -fsSL https://bun.sh/install | bash
  export PATH="$H/.bun/bin:$PATH"
fi
command -v bun >/dev/null || die "bun unavailable after install."
ok "deps OK"

# ── 2. ZeroEntropy key ──────────────────────────────────────────────────────
say "ZeroEntropy key (embeddings — free account at dashboard.zeroentropy.dev)"
ZE=""
read -rs -p "  Paste your ZeroEntropy key (ze_...) : " ZE; echo
[ -n "$ZE" ] || die "empty key."

# ── 3. GBrain (clone + deps + link) ─────────────────────────────────────────
say "GBrain"
mkdir -p "$H/DEV"
if [ -d "$GREPO/.git" ]; then ok "already cloned"; else
  git clone https://github.com/garrytan/gbrain.git "$GREPO"; ok "cloned"
fi
( cd "$GREPO" && bun install >/dev/null 2>&1 && bun link >/dev/null 2>&1 ) ; ok "deps + link"
GBQ_BIN="$H/.bun/bin/gbrain"

# ── 4. Vault skeleton (never overwrite) ─────────────────────────────────────
say "Brain vault ($BRAIN)"
if [ -d "$BRAIN" ]; then
  warn "vault already exists → skeleton NOT copied (keeping your content)"
else
  mkdir -p "$BRAIN"
  cp -R "$REPO/vault-skeleton/." "$BRAIN/"
  ok "skeleton placed"
fi

# ── 5. Hooks (copy + make paths portable) ───────────────────────────────────
say "Brain hooks ($HOOKS)"
mkdir -p "$HOOKS"
for f in "$REPO"/engine/hooks/*.py; do
  base="$(basename "$f")"
  sed "s#__HOME__#$H#g" "$f" > "$HOOKS/$base"
  chmod +x "$HOOKS/$base"
  ok "$base"
done

# ── 6. gbq + nightly ────────────────────────────────────────────────────────
say "gbq + nightly"
mkdir -p "$BIN"
sed "s#__HOME__#$H#g" "$REPO/engine/bin/gbq" > "$BIN/gbq" && chmod +x "$BIN/gbq"; ok "gbq → $BIN/gbq"
sed "s#__HOME__#$H#g" "$REPO/engine/nightly/gbrain-nightly.sh" > "$HOOKS/gbrain-nightly.sh" && chmod +x "$HOOKS/gbrain-nightly.sh"; ok "gbrain-nightly.sh"
sed "s#__HOME__#$H#g" "$REPO/engine/nightly/gbrain-lint.sh" > "$HOOKS/gbrain-lint.sh" && chmod +x "$HOOKS/gbrain-lint.sh"; ok "gbrain-lint.sh"
sed "s#__HOME__#$H#g" "$REPO/engine/nightly/gbrain-selfupdate.sh" > "$HOOKS/gbrain-selfupdate.sh" && chmod +x "$HOOKS/gbrain-selfupdate.sh"; ok "gbrain-selfupdate.sh"

# ── 7. launchd jobs (nightly maintenance + daily reflection + weekly lint) ───
say "launchd (nightly 04:00 + reflection 12:00/23:00 + lint Monday 08:00)"
mkdir -p "$H/Library/LaunchAgents"
load_agent() {  # <label> <template>
  local label="$1" tpl="$2" plist="$H/Library/LaunchAgents/$1.plist"
  sed -e "s#__HOME__#$H#g" -e "s#__USER__#$U#g" "$REPO/engine/nightly/$tpl" > "$plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || true
  ok "loaded: $label"
}
load_agent "com.$U.gbrain-nightly"   "com.USER.gbrain-nightly.plist.template"
load_agent "com.$U.brain-reflection" "com.USER.brain-reflection.plist.template"
load_agent "com.$U.gbrain-lint"      "com.USER.gbrain-lint.plist.template"

# ── 8. settings.json (merge hooks, non-destructive) ─────────────────────────
say "Registering hooks (~/.claude/settings.json)"
python3 - "$H" <<'PY'
import json, os, sys
H = sys.argv[1]
p = os.path.join(H, ".claude", "settings.json")
os.makedirs(os.path.dirname(p), exist_ok=True)
d = {}
if os.path.exists(p):
    try: d = json.load(open(p))
    except Exception: d = {}
hooks = d.setdefault("hooks", {})
def cmd(name): return {"type":"command","command":f"{H}/.claude/hooks/brain/{name}"}
def ensure(evt, names):
    arr = hooks.setdefault(evt, [])
    grp = next((g for g in arr if "matcher" not in g), None)
    if grp is None:
        grp = {"hooks":[]}; arr.append(grp)
    have = {h.get("command") for h in grp.setdefault("hooks", [])}
    for n in names:
        c = cmd(n)
        if c["command"] not in have:
            grp["hooks"].append(c)
ensure("UserPromptSubmit", ["correction-detector.py"])
ensure("Stop", ["session-logger.py","session-indexer.py","session-recap.py"])
ensure("SessionStart", ["gbrain-update-check.py"])
json.dump(d, open(p,"w"), indent=2, ensure_ascii=False)
print("  ok settings.json")
PY

# ── 9. Global CLAUDE.md (append, never overwrite) ────────────────────────────
say "Global CLAUDE.md"
GC="$H/.claude/CLAUDE.md"
MARK="<!-- brain-in-a-box -->"
if [ -f "$GC" ] && grep -q "$MARK" "$GC"; then
  ok "already present (skip)"
else
  { echo; echo "$MARK"; cat "$REPO/engine/CLAUDE.global.template.md"; } >> "$GC"
  ok "appended (your existing content preserved)"
fi

# ── 10. git init vault ──────────────────────────────────────────────────────
say "git init vault"
if [ ! -d "$BRAIN/.git" ]; then
  ( cd "$BRAIN" && printf '.obsidian/\n.DS_Store\n' > .gitignore && git init -q \
    && git add -A && git -c user.email="brain@local" -c user.name="brain" commit -q -m "brain init" )
  ok "git repo created"
else ok "already a git repo"; fi

# ── 11. GBrain init + key + import + embed ──────────────────────────────────
say "GBrain init + import"
if [ ! -d "$GDATA/brain.pglite" ]; then
  "$GBQ_BIN" init --pglite --embedding-model zeroentropyai:zembed-1 --embedding-dimensions 1280 >/dev/null 2>&1
  ok "brain initialized (ZE/1280)"
fi
mkdir -p "$GDATA"
python3 - "$GDATA" "$ZE" <<'PY'
import json, os, sys
gdata, ze = sys.argv[1], sys.argv[2]
p = os.path.join(gdata, "config.json")
d = json.load(open(p)) if os.path.exists(p) else {}
d["zeroentropy_api_key"] = ze
json.dump(d, open(p,"w"))
os.chmod(p, 0o600)
print("  ok ZE key written (config.json, 600)")
PY
"$GBQ_BIN" config set search.mode balanced >/dev/null 2>&1 || true
# Link graph: gbrain's extractor only auto-recognizes English "entity" dirs
# (people/, companies/, projects/…). EVERY skeleton dir (Team/, Agents/,
# Decisions/, Skills/, Journal/…) is outside that whitelist, so without this
# flag all wikilinks are silently dropped and the graph stays empty forever.
# Basename resolution links [[Page-Name]] to the page whose filename matches
# (case-insensitive), regardless of folder.
"$GBQ_BIN" config set link_resolution.global_basename true >/dev/null 2>&1 || true
"$GBQ_BIN" import "$BRAIN" --no-embed >/dev/null 2>&1 && ok "vault imported"
say "Embedding (may take 1-2 min)…"
"$GBQ_BIN" embed --stale >/dev/null 2>&1 && ok "embedded" || warn "re-run embed: gbrain embed --stale"

# ── 12. TEAM mode: company vault as a 2nd source (federated, git-pull nightly)
if [ -n "$COMPANY_URL" ]; then
  say "Team mode — company vault ($COMPANY_URL)"
  if [ -d "$BRAIN_CO/.git" ]; then ok "already cloned"; else
    git clone "$COMPANY_URL" "$BRAIN_CO" && ok "cloned → $BRAIN_CO"
  fi
  if "$GBQ_BIN" sources list 2>/dev/null | grep -q "^  company "; then
    ok "source 'company' already registered"
  else
    "$GBQ_BIN" sources add company --path "$BRAIN_CO" >/dev/null 2>&1 && ok "source 'company' added"
  fi
  "$GBQ_BIN" sync --source company >/dev/null 2>&1 && ok "company vault indexed" || warn "re-run: gbrain sync --source company"
  echo "  → query the company brain: gbq query \"...\"  (federated) · contribute: cd $BRAIN_CO && git add+commit+push"
fi

# ── 13. Obsidian (to browse/visualize the vault) ────────────────────────────
say "Obsidian (to browse your brain)"
if [ -d "/Applications/Obsidian.app" ] || [ -d "$H/Applications/Obsidian.app" ]; then
  ok "already installed"
elif command -v brew >/dev/null; then
  brew install --cask obsidian >/dev/null 2>&1 && ok "installed via brew" || warn "brew install failed — get it free at https://obsidian.md"
else
  warn "not installed — download free at https://obsidian.md"
fi

# ── 14. GStack (optional — 23 AI specialists for Claude Code) ───────────────
if [ -n "$WITH_GSTACK" ]; then
  say "GStack (23 AI specialists — gstack.com)"
  GS="$H/.claude/skills/gstack"
  if [ -d "$GS/.git" ]; then
    ok "already cloned"
  else
    mkdir -p "$(dirname "$GS")"
    git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git "$GS" >/dev/null 2>&1 && ok "cloned" || warn "clone failed — try manually: git clone https://github.com/garrytan/gstack.git $GS"
  fi
  if [ -x "$GS/setup" ]; then
    ( cd "$GS" && ./setup >/dev/null 2>&1 ) && ok "setup complete" || warn "gstack setup reported issues — run 'cd $GS && ./setup' manually"
  fi
  echo "    slash commands now available: /office-hours · /plan-ceo-review · /review · /qa · /retro · /investigate · /cso · /learn (+15 more)"
fi

# ── 15. Verify (hooks healthy) ──────────────────────────────────────────────
say "Verifying hooks"
bash "$REPO/test-hooks.sh" >/dev/null 2>&1 && ok "hooks + gbrain self-update healthy" || warn "hook check reported issues — run ./test-hooks.sh to see them"

# ── Done ─────────────────────────────────────────────────────────────────────
printf "\n\033[1;32m✅ brain-in-a-box installed.\033[0m\n"
cat <<EOF

Last step — the ONBOARDING (15 min) that fills in YOUR profile:
  cd $BRAIN && claude
  then paste:  "Read $REPO/onboarding/ONBOARDING.md and run it to personalize me."

Browse your brain visually in Obsidian:
  open it → "Open folder as vault" → $BRAIN

Checks:
  ./test-hooks.sh                         # verify all hooks + gbrain self-update work
  ~/.local/bin/gbq query "test"          # the memory answers
  gbrain doctor --fast                    # health (aim for 85+)
  cat ~/.gbrain/nightly.log              # tomorrow morning: the nightly ran
EOF
