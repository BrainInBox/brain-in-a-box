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

# ── 2. Search by meaning: Voyage AI key (optional) ──────────────────────────
# A brain that already has embeddings keeps them (ZeroEntropy installs stay on
# ZeroEntropy). Without a key, search works on keywords; re-run to add one.
say "Search by meaning (Voyage AI key, optional)"
EMBED_MODEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("embedding_model") or "")' "$GDATA/config.json" 2>/dev/null || true)"
VOYAGE="${VOYAGE_API_KEY:-}"
if [ -n "$EMBED_MODEL" ]; then
  ok "already set up ($EMBED_MODEL), kept as is"
elif [ -n "$VOYAGE" ]; then
  ok "key taken from VOYAGE_API_KEY"
elif [ -t 0 ]; then
  cat <<'EOF'
  Your brain finds notes by keywords out of the box. To also find them by
  meaning, it uses Voyage AI (5 minutes):
    1. Sign up at https://dashboard.voyageai.com
    2. Add a card (Organization > Billing). Without one, Voyage allows 3
       requests a minute: too slow. 200M tokens are free and a personal brain
       uses well under 1% of that; beyond, Voyage bills monthly.
    3. Create an API key (it starts with pa-) and paste it below.
  Press Enter to skip. Re-run ./install.sh any time to add it.
EOF
  read -rs -p "  Voyage key: " VOYAGE || true; echo
else
  ok "no terminal: keyword search for now (re-run with VOYAGE_API_KEY set to add it)"
fi

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
# A file already there that differs (an edited hook, a local tweak) is copied to
# ~/.brain-in-a-box/backups/<run>/ before it is replaced: re-running the
# installer never loses a change.
BACKUP="$H/.brain-in-a-box/backups/$(date +%Y%m%d-%H%M%S)"
BACKED_UP=0
# >>> place
place() {  # place <source> <destination>
  local tmp
  tmp="$(mktemp)"
  sed "s#__HOME__#$H#g" "$1" > "$tmp"
  if [ -f "$2" ] && ! cmp -s "$tmp" "$2"; then
    mkdir -p "$BACKUP"
    cp -p "$2" "$BACKUP/$(basename "$2")"
    BACKED_UP=$((BACKED_UP + 1))
  fi
  mv "$tmp" "$2"
  chmod +x "$2"
}
# <<< place
say "Brain hooks ($HOOKS)"
mkdir -p "$HOOKS"
for f in "$REPO"/engine/hooks/*.py; do
  base="$(basename "$f")"
  place "$f" "$HOOKS/$base"
  ok "$base"
done

# ── 6. gbq + nightly ────────────────────────────────────────────────────────
say "gbq + nightly"
mkdir -p "$BIN"
place "$REPO/engine/bin/gbq" "$BIN/gbq"; ok "gbq → $BIN/gbq"
for s in gbrain-nightly gbrain-lint gbrain-selfupdate; do
  place "$REPO/engine/nightly/$s.sh" "$HOOKS/$s.sh"; ok "$s.sh"
done
if [ "$BACKED_UP" -gt 0 ]; then warn "$BACKED_UP file(s) you had changed were replaced; your versions are in $BACKUP"; fi

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

# ── 9bis. Claude Code skills (never overwrites one of yours, see the script) ─
say "Claude Code skills"
python3 "$REPO/engine/install_skills.py" --src "$REPO/skills" --dest "$H/.claude/skills" \
  && ok "skills up to date" || warn "skills: see above"

# ── 10. git init vault ──────────────────────────────────────────────────────
say "git init vault"
if [ ! -d "$BRAIN/.git" ]; then
  ( cd "$BRAIN" && printf '.obsidian/\n.DS_Store\n' > .gitignore && git init -q \
    && git add -A && git -c user.email="brain@local" -c user.name="brain" commit -q -m "brain init" )
  ok "git repo created"
else ok "already a git repo"; fi

# ── 11. GBrain init + key + import + embed ──────────────────────────────────
say "GBrain init + import"
mkdir -p "$GDATA"
# One-token request. The key reaches curl through stdin (printf is a builtin), never argv.
voyage_check() {
  printf 'header = "Authorization: Bearer %s"\n' "$1" | curl -s -m 20 -o /dev/null -w '%{http_code}' -K - \
    -H 'Content-Type: application/json' -d '{"input":["ok"],"model":"voyage-4"}' https://api.voyageai.com/v1/embeddings || true
}
EMBED=""
if [ -n "$EMBED_MODEL" ]; then
  EMBED=1; ok "brain already initialized ($EMBED_MODEL)"
elif [ -n "$VOYAGE" ] && [ "$(voyage_check "$VOYAGE")" = 200 ]; then
  # Same path for the key: stdin, and the file is born 0600.
  printf '%s' "$VOYAGE" | python3 -c '
import json, os, sys
p = os.path.join(sys.argv[1], "config.json")
d = json.load(open(p)) if os.path.exists(p) else {}
d["voyage_api_key"] = sys.stdin.read().strip()
fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(d, f)
os.replace(p + ".tmp", p)' "$GDATA"
  if "$GBQ_BIN" init --pglite --embedding-model voyage:voyage-4 --embedding-dimensions 1024 --search-mode balanced >/dev/null 2>&1; then
    EMBED=1; ok "brain initialized (Voyage: search by meaning)"
    # rerank-3-lite is inside Voyage's free tokens; gbrain's default (rerank-2.5) is billed from the first search.
    "$GBQ_BIN" config set search.reranker.model voyage:rerank-3-lite >/dev/null 2>&1 || true
  else
    warn "gbrain init failed: run gbrain doctor"
  fi
else
  [ -n "$VOYAGE" ] && warn "Voyage did not accept this key (wrong key, or no network): keyword search for now. Fix it, then re-run ./install.sh"
  if [ -d "$GDATA/brain.pglite" ]; then
    ok "brain already initialized (keyword search)"
  else
    "$GBQ_BIN" init --pglite --no-embedding --search-mode balanced >/dev/null 2>&1 && ok "brain initialized (keyword search)" || warn "gbrain init failed: run gbrain doctor"
  fi
fi
"$GBQ_BIN" config set search.mode balanced >/dev/null 2>&1 || true
# Link graph: gbrain's extractor only auto-recognizes English "entity" dirs
# (people/, companies/, projects/…). EVERY skeleton dir (Team/, Agents/,
# Decisions/, Skills/, Journal/…) is outside that whitelist, so without this
# flag all wikilinks are silently dropped and the graph stays empty forever.
# Basename resolution links [[Page-Name]] to the page whose filename matches
# (case-insensitive), regardless of folder.
"$GBQ_BIN" config set link_resolution.global_basename true >/dev/null 2>&1 || true
"$GBQ_BIN" import "$BRAIN" --no-embed >/dev/null 2>&1 && ok "vault imported"
if [ -n "$EMBED" ]; then
  say "Embedding (may take 1-2 min)…"
  "$GBQ_BIN" embed --stale --max-usd 1 >/dev/null 2>&1 && ok "embedded" \
    || warn "embedding did not finish (no card on Voyage yet? it allows 3 requests a minute without one). Re-run: gbrain embed --stale --max-usd 1"
fi

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
if [ "$BACKED_UP" -gt 0 ]; then
  printf "\033[1;33m⚠ Your previous versions of %s replaced file(s) are in %s\033[0m\n" "$BACKED_UP" "$BACKUP"
fi
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
