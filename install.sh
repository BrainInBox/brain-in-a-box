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
STATE="$H/.brain-in-a-box"
LOG="$STATE/install.log"

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

# The output of clone, bun and gbrain goes to $LOG (0600, appended run after run);
# when one of them fails, its last 15 lines are shown. The Voyage key never
# reaches a command line, and is masked in case a tool prints it. What is not
# gbrain (bun's installer, dependency scripts, gstack's setup) runs without
# VOYAGE_API_KEY in its environment: env -u VOYAGE_API_KEY.
# >>> log
mask_key() {
  local line k
  [ -n "${VOYAGE:-}${VOYAGE_STORED:-}" ] || { cat; return; }
  while IFS= read -r line || [ -n "$line" ]; do
    for k in "${VOYAGE:-}" "${VOYAGE_STORED:-}"; do
      [ -n "$k" ] && line="${line//"$k"/[voyage key]}"
    done
    printf '%s\n' "$line"
  done
}
logged() {  # logged <command...>
  local rc
  printf '[%s] $ %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | mask_key >> "$LOG"
  "$@" 2>&1 | mask_key >> "$LOG"
  rc=${PIPESTATUS[0]}
  [ "$rc" = 0 ] || printf '[%s] exit %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$rc" >> "$LOG"
  return "$rc"
}
log_tail() { printf '    last lines of %s:\n' "$LOG"; tail -n 15 "$LOG" | sed 's/^/    │ /'; }
warn_log() { warn "$*"; log_tail; }
die_log() { log_tail; die "$*"; }
# <<< log

# >>> settings
# Adds our hooks to ~/.claude/settings.json. A file that does not parse is left
# untouched and stops the install. Otherwise the previous version is kept as
# settings.json.bak-<date> (0600) and the new one is written atomically.
merge_settings() {  # merge_settings <home> [--check]
  local out
  out="$(settings_py "$@")" || die "${out:-could not update $1/.claude/settings.json (see above)}"
  [ -z "$out" ] || ok "$out"
}
settings_py() {
  python3 - "$@" <<'PY'
import json, os, stat, sys, tempfile, time
H, check = sys.argv[1], sys.argv[2:] == ["--check"]
p = os.path.join(H, ".claude", "settings.json")
target = os.path.realpath(p)  # a symlinked settings.json stays a symlink
def stop(why):
    print("%s %s. It was left untouched: fix it, then re-run ./install.sh" % (p, why))
    sys.exit(1)
raw, d = None, {}
if os.path.exists(target):
    with open(target, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
        d = json.loads(text) if text.strip() else {}
    except UnicodeDecodeError as e:
        stop("is not UTF-8 text (line %d)" % (raw[:e.start].count(b"\n") + 1))
    except json.JSONDecodeError as e:
        stop("is not valid JSON (line %d, column %d: %s)" % (e.lineno, e.colno, e.msg))
    if not isinstance(d, dict) or not isinstance(d.get("hooks", {}), dict):
        stop("is JSON, but not a settings object with a \"hooks\" object")
    for evt in ("UserPromptSubmit", "Stop", "SessionStart"):  # the lists ensure() walks, checked before anything is installed
        arr = d.get("hooks", {}).get(evt, [])
        if not (isinstance(arr, list) and all(isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                                              and all(isinstance(h, dict) for h in g.get("hooks", [])) for g in arr)):
            stop("has a \"hooks\".\"%s\" that is not a list of hook groups" % evt)
if check:
    sys.exit(0)

before = json.dumps(d, sort_keys=True)
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
ensure("SessionStart", ["gbrain-update-check.py", "controle-sessionstart.py"])
if json.dumps(d, sort_keys=True) == before:
    print("settings.json: hooks already registered")
    sys.exit(0)

bak = None
if raw is not None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak, n = "%s.bak-%s" % (p, stamp), 1
    while os.path.lexists(bak):
        bak, n = "%s.bak-%s-%d" % (p, stamp, n), n + 1
    with os.fdopen(os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(raw)
os.makedirs(os.path.dirname(target), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".settings.json.")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    if raw is not None:
        os.chmod(tmp, stat.S_IMODE(os.stat(target).st_mode))
    os.replace(tmp, target)
except BaseException:
    os.unlink(tmp)
    raise
print("settings.json: hooks registered" + (" (previous version: %s)" % bak if bak else ""))
PY
}
# <<< settings

# ── 1. Preflight ────────────────────────────────────────────────────────────
say "Preflight"
[ "$(uname)" = "Darwin" ] || die "macOS required (launchd)."
command -v git >/dev/null   || die "git missing."
command -v python3 >/dev/null || die "python3 missing."
merge_settings "$H" --check   # a settings.json we cannot read stops us before anything is changed
mkdir -p "$STATE"
( umask 077; : >> "$LOG" ); chmod 600 "$LOG"
printf '\n===== install.sh %s =====\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
command -v claude >/dev/null || warn "claude CLI not found — install Claude Code first."
if ! command -v bun >/dev/null; then
  warn "bun missing → installing…"
  curl -fsSL https://bun.sh/install | env -u VOYAGE_API_KEY bash
  export PATH="$H/.bun/bin:$PATH"
fi
command -v bun >/dev/null || die "bun unavailable after install."
ok "deps OK"

# ── 2. Search by meaning: Voyage AI key (optional) ──────────────────────────
# A brain that already has embeddings keeps them (ZeroEntropy installs stay on
# ZeroEntropy). Without a key, search works on keywords; re-run to add one.
say "Search by meaning (Voyage AI key, optional)"
EMBED_MODEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("embedding_model") or "")' "$GDATA/config.json" 2>/dev/null || true)"
VOYAGE_STORED="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("voyage_api_key") or "")' "$GDATA/config.json" 2>/dev/null || true)"  # only to mask it in the log
VOYAGE="$(printf '%s' "${VOYAGE_API_KEY:-}" | tr -d '[:space:]')"  # trimmed as config.json stores it: the log mask then matches what gbrain prints
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
# A new clone starts at the release CI tested (engine/gbrain-pin.json), never at
# master. An existing clone keeps its version: the nightly moves it to the newest
# release that is at least 7 days old (gbrain_update.py), never lower.
say "GBrain"
mkdir -p "$H/DEV"
FRESH_GBRAIN=0
if [ -d "$GREPO/.git" ]; then ok "already cloned (its version is left to the nightly update)"; else
  FRESH_GBRAIN=1
  if [ -L "$GREPO" ] || { [ -e "$GREPO" ] && [ -n "$(ls -A "$GREPO" 2>/dev/null)" ]; }; then
    die "$GREPO exists but is not a gbrain clone: move it away, then re-run ./install.sh"
  fi
  PIN="$(python3 -I -c '
import json, re, sys
d = json.load(open(sys.argv[1]))
if not (re.fullmatch(r"v[0-9]+(\.[0-9]+){2,3}", d["tag"]) and re.fullmatch(r"[0-9a-f]{40}", d["sha"])):
    sys.exit(1)
print(d["tag"], d["sha"])' "$REPO/engine/gbrain-pin.json" 2>/dev/null)" || die "engine/gbrain-pin.json is unreadable: download brain-in-a-box again"
  PIN_TAG="${PIN% *}"; PIN_SHA="${PIN#* }"
  # Cloned aside and moved into place once at the pinned commit: an interrupted
  # clone never leaves a gbrain at master that a re-run would then keep.
  CLONE="$(mktemp -d "$H/DEV/.gbrain-clone.XXXXXX")"
  if logged git clone https://github.com/garrytan/gbrain.git "$CLONE/gbrain" \
     && logged git -C "$CLONE/gbrain" -c advice.detachedHead=false checkout -q --detach "$PIN_SHA"; then
    rmdir "$GREPO" 2>/dev/null || true   # an empty folder, which git clone used to accept
    mv "$CLONE/gbrain" "$GREPO" && rmdir "$CLONE" || die "could not move the gbrain clone to $GREPO (left in $CLONE)"
  else
    rm -rf "$CLONE"
    die_log "could not clone gbrain $PIN_TAG (network?)"
  fi
  ok "cloned at $PIN_TAG (the version CI tested)"
fi
# --frozen-lockfile: the dependencies are exactly the ones gbrain's lockfile names.
# A clone from an older install can sit on a master whose lockfile lags behind
# package.json: it gets the plain install it always had, until the nightly
# moves it to a release.
if ! ( cd "$GREPO" && logged env -u VOYAGE_API_KEY bun install --frozen-lockfile ); then
  [ "$FRESH_GBRAIN" = 0 ] || die_log "dependency install failed in $GREPO"
  warn "gbrain's lockfile does not match its dependencies here: installing without it"
  ( cd "$GREPO" && logged env -u VOYAGE_API_KEY bun install ) || die_log "bun install failed in $GREPO"
fi
( cd "$GREPO" && logged env -u VOYAGE_API_KEY bun link ) || die_log "bun link failed in $GREPO"
ok "deps + link"
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
# $MANIFEST records the sha256 of every file written here. A file changed since
# then (another vault folder, other messages) is kept as is and the new version
# waits in $PENDING. A file from an install older than the manifest is replaced
# only when it is a version the repo's git history knows; without that history
# (a ZIP), one that differs is copied to $BACKUP before it is replaced. A link or
# a folder in the way is never replaced.
BACKUP="$STATE/backups/$(date +%Y%m%d-%H%M%S)"
BACKED_UP=0
MANIFEST="$STATE/installed.json"
PENDING="$STATE/pending"
KEPT=""
# >>> place
sha256() { shasum -a 256 < "${1:-/dev/stdin}" | cut -d' ' -f1; }
fill() { sed -e "s#__HOME__#$H#g" -e "s#__USER__#$U#g" "$@"; }
manifest() {  # manifest get <path> | manifest set <path> <sha256>; never fails the install
  python3 - "$MANIFEST" "$@" <<'PY'
import json, os, re, sys, tempfile
m, op, key = sys.argv[1:4]
sha = re.compile("[0-9a-f]{64}")
try:
    with open(m, encoding="utf-8") as f:
        d = json.load(f)
    d = d if isinstance(d, dict) else {}
except Exception:
    d = {}  # unreadable: as if there were none
if op == "get":
    v = d.get(key)
    print(v if isinstance(v, str) and sha.fullmatch(v) else "")  # a damaged entry counts as none
elif sha.fullmatch(sys.argv[4]) and d.get(key) != sys.argv[4]:
    d[key] = sys.argv[4]
    try:
        os.makedirs(os.path.dirname(m), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(m), prefix=".installed.json.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, m)
    except OSError as e:
        print("  could not record %s in %s: %s" % (key, m, e), file=sys.stderr)
PY
}
shipped() {  # shipped <source>: the sha256 of each version of it in the repo's git history, as place() writes it
  local rel="${1#"$REPO"/}" c
  [ "$(git -C "$REPO" rev-parse --is-shallow-repository 2>/dev/null)" = false ] || return 0
  git -C "$REPO" log --format=%H -- "$rel" 2>/dev/null | while read -r c; do
    git -C "$REPO" cat-file -e "$c:$rel" 2>/dev/null || continue  # the commit that deleted it
    git -C "$REPO" cat-file blob "$c:$rel" | fill | sha256
  done
}
replaceable() {  # replaceable <source> <destination>: may the new version of <source> replace <destination>?
  local cur rec known
  [ -f "$2" ] && [ ! -L "$2" ] || return 1
  cur="$(sha256 "$2" 2>/dev/null)" || return 1  # unreadable: cannot tell
  rec="$(manifest get "$2")"
  if [ -n "$rec" ]; then [ "$cur" = "$rec" ]; return; fi
  known="$(shipped "$1")" || known=""
  if [ -n "$known" ]; then grep -qxF -- "$cur" <<<"$known"; return; fi
  # Neither manifest nor history to tell an old version from an edit: back it up first.
  # Called as a condition, so errexit is off here: a failed backup has to say no itself.
  mkdir -p "$BACKUP" && cp -p "$2" "$BACKUP/$(basename "$2")" || return 1
  BACKED_UP=$((BACKED_UP + 1))
}
place() {  # place <source> <destination> [label]
  local tmp name label mode=755
  name="$(basename "$2")"; label="${3:-$name}"
  case "$name" in *.plist) mode=644;; esac
  tmp="$(mktemp)"
  fill "$1" > "$tmp"
  chmod "$mode" "$tmp"  # before it lands: a hook that fires meanwhile finds it whole and executable
  if cmp -s "$tmp" "$2"; then
    rm -f "$tmp"
  elif { [ -e "$2" ] || [ -L "$2" ]; } && ! replaceable "$1" "$2"; then
    mkdir -p "$PENDING"
    mv "$tmp" "$PENDING/$name"
    KEPT="$KEPT $name"
    warn "$label: changed on this machine, kept as is; the new version is in $PENDING/$name"
    return 0
  else
    mv "$tmp" "$2"
  fi
  manifest set "$2" "$(sha256 "$2")"
  ok "$label"
}
# <<< place
say "Brain hooks ($HOOKS)"
mkdir -p "$HOOKS"
for f in "$REPO"/engine/hooks/*.py; do
  place "$f" "$HOOKS/$(basename "$f")"
done

# ── 6. gbq, controle + nightly ──────────────────────────────────────────────
say "gbq, controle + nightly"
mkdir -p "$BIN"
place "$REPO/engine/bin/gbq" "$BIN/gbq" "gbq → $BIN/gbq"
place "$REPO/engine/bin/controle" "$BIN/controle" "controle → $BIN/controle"
for s in gbrain-nightly gbrain-lint gbrain-selfupdate; do
  place "$REPO/engine/nightly/$s.sh" "$HOOKS/$s.sh"
done
place "$REPO/engine/nightly/gbrain-sync-vault.py" "$HOOKS/gbrain-sync-vault.py"

# ── 7. launchd jobs (nightly maintenance + daily reflection + weekly lint) ───
say "launchd (nightly 04:00 + reflection 12:00/23:00 + lint Monday 08:00)"
mkdir -p "$H/Library/LaunchAgents"
load_agent() {  # <label> <template>; a plist you changed (another hour) is kept, like the hooks
  local label="$1" plist="$H/Library/LaunchAgents/$1.plist"
  place "$REPO/engine/nightly/$2" "$plist" "$label"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || true
}
load_agent "com.$U.gbrain-nightly"   "com.USER.gbrain-nightly.plist.template"
load_agent "com.$U.brain-reflection" "com.USER.brain-reflection.plist.template"
load_agent "com.$U.gbrain-lint"      "com.USER.gbrain-lint.plist.template"
if [ "$BACKED_UP" -gt 0 ]; then warn "$BACKED_UP file(s) you had changed were replaced; your versions are in $BACKUP"; fi

# ── 8. settings.json (merge hooks, non-destructive) ─────────────────────────
say "Registering hooks (~/.claude/settings.json)"
merge_settings "$H"

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
if [ -d "$BRAIN/.git" ]; then ok "already a git repo"
# A vault that existed without git keeps its own .gitignore. A commit refused
# (signing, a global pre-commit hook) is reported instead of ending the install.
elif ( cd "$BRAIN" && { [ -e .gitignore ] || printf '.obsidian/\n.DS_Store\n' > .gitignore; } && git init -q \
    && git add -A && git -c user.email="brain@local" -c user.name="brain" commit -q -m "brain init" ); then
  ok "git repo created"
else
  warn "vault: git init or first commit failed (see above); your notes are untouched"
fi

# ── 11. GBrain init + key + vault indexing + embed ──────────────────────────
say "GBrain init + indexing the vault"
mkdir -p "$GDATA"
# One-token request. The key reaches curl through stdin (printf is a builtin), never argv;
# -q first, so a ~/.curlrc (trace, proxy…) never applies to it.
voyage_check() {
  printf 'header = "Authorization: Bearer %s"\n' "$1" | curl -q -s -m 20 -o /dev/null -w '%{http_code}' -K - \
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
  if logged "$GBQ_BIN" init --pglite --embedding-model voyage:voyage-4 --embedding-dimensions 1024 --search-mode balanced; then
    EMBED=1; ok "brain initialized (Voyage: search by meaning)"
    # rerank-3-lite is inside Voyage's free tokens; gbrain's default (rerank-2.5) is billed from the first search.
    "$GBQ_BIN" config set search.reranker.model voyage:rerank-3-lite >/dev/null 2>&1 || true
  else
    warn_log "gbrain init failed: run gbrain doctor"
  fi
else
  [ -n "$VOYAGE" ] && warn "Voyage did not accept this key (wrong key, or no network): keyword search for now. Fix it, then re-run ./install.sh"
  if [ -d "$GDATA/brain.pglite" ]; then
    ok "brain already initialized (keyword search)"
  else
    logged "$GBQ_BIN" init --pglite --no-embedding --search-mode balanced && ok "brain initialized (keyword search)" || warn_log "gbrain init failed: run gbrain doctor"
  fi
fi
"$GBQ_BIN" config set search.mode balanced >/dev/null 2>&1 || true
# The version is chosen by the 7-day update: `gbrain upgrade` (what gbrain suggests by default) would go to master.
"$GBQ_BIN" config set self_upgrade.mode off >/dev/null 2>&1 || true
# Link graph: gbrain's extractor only auto-recognizes English "entity" dirs
# (people/, companies/, projects/…). EVERY skeleton dir (Team/, Agents/,
# Decisions/, Skills/, Journal/…) is outside that whitelist, so without this
# flag all wikilinks are silently dropped and the graph stays empty forever.
# Basename resolution links [[Page-Name]] to the page whose filename matches
# (case-insensitive), regardless of folder.
"$GBQ_BIN" config set link_resolution.global_basename true >/dev/null 2>&1 || true
# gbrain reads the vault through git: notes written since the last run are committed first.
if [ -n "$(git -C "$BRAIN" status --porcelain 2>/dev/null)" ]; then
  git -C "$BRAIN" add -A && git -C "$BRAIN" -c user.email="brain@local" -c user.name="brain" commit -q -m "brain-in-a-box: notes before indexing" \
    || warn "vault: commit refused (see above); notes not committed yet are indexed by the nightly"
fi
# The vault is the 'default' source on an older brain, a source of its own
# ('vault') on a newer one, where 'default' is gbrain's own folder; a brain that
# imported the vault into that folder is repaired. See engine/nightly/gbrain-sync-vault.py.
# Its "[vault] …" lines are shown; gbrain's own output stays in the log.
from=$(( $(wc -c < "$LOG") + 1 ))
if logged python3 "$REPO/engine/nightly/gbrain-sync-vault.py" "$BRAIN"; then rc=0; else rc=$?; fi
tail -c +"$from" "$LOG" | sed -n 's/^\[vault\] /    /p'
[ "$rc" = 0 ] && ok "vault indexed" || warn_log "vault not indexed (exit $rc): re-run ./install.sh, the nightly also retries"
if [ -n "$EMBED" ]; then
  say "Embedding (may take 1-2 min)…"
  logged "$GBQ_BIN" embed --stale --max-usd 1 && ok "embedded" \
    || warn_log "embedding did not finish (no card on Voyage yet? it allows 3 requests a minute without one). Re-run: gbrain embed --stale --max-usd 1"
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
  env -u VOYAGE_API_KEY brew install --cask obsidian >/dev/null 2>&1 && ok "installed via brew" || warn "brew install failed — get it free at https://obsidian.md"
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
    logged git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git "$GS" && ok "cloned" || warn_log "clone failed — try manually: git clone https://github.com/garrytan/gstack.git $GS"
  fi
  if [ -x "$GS/setup" ]; then
    ( cd "$GS" && logged env -u VOYAGE_API_KEY ./setup ) && ok "setup complete" || warn_log "gstack setup reported issues — run 'cd $GS && ./setup' manually"
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
if [ -n "$KEPT" ]; then
  printf "\033[1;33m⚠ Kept as you changed them:%s\n  The new versions are in %s: compare (diff), keep what you want.\033[0m\n" "$KEPT" "$PENDING"
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
  tail ~/.brain-in-a-box/install.log     # what clone, bun and gbrain said during this install
EOF
