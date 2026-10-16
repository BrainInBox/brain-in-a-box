#!/usr/bin/env bash
# brain-in-a-box — installs a personal second brain (Brain vault + hooks +
# GBrain + gbq + nightly) for the current user, on macOS or Linux (Debian, Ubuntu;
# Windows: install.ps1). Idempotent and non-destructive: it never overwrites an
# existing vault or CLAUDE.md, it merges.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="$HOME"
U="$(whoami)"
OS="$(uname -s)"
ORIG_PATH="$PATH"
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
#   --agents <list>       the AI agents to wire: claude (the default), codex, or claude,codex
#   --prefere <agent>     the one that writes the evening summary when both are chosen
#                         (or BIAB_AGENTS / BIAB_PREFERE; Codex is not open on any system yet)
COMPANY_URL=""
WITH_GSTACK=""
AGENTS_OPT=""
PREFERE_OPT=""
while [ $# -gt 0 ]; do case "$1" in
  --company)     COMPANY_URL="${2:-}"; shift 2;;
  --with-gstack) WITH_GSTACK=1; shift;;
  --agents)      AGENTS_OPT="${2:-}"; shift; [ $# -eq 0 ] || shift;;
  --prefere)     PREFERE_OPT="${2:-}"; shift; [ $# -eq 0 ] || shift;;
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

# >>> agents
# Which AI agents the kit wires: --agents/--prefere, else BIAB_AGENTS/BIAB_PREFERE (how the
# app passes it), else the choice made before (~/.brain-in-a-box/agents.json), else Claude
# alone, as before there was a choice. engine/hooks/_agents.py decides and records a choice
# before anything is installed; with none, it writes nothing. Codex is refused on a system
# until it opens there.
AGENTS=claude
PREFERE=claude
choose_agents() {
  local out label
  out="$(python3 -I "$REPO/engine/hooks/_agents.py" choisir --pose-par=install.sh \
         --agents="${AGENTS_OPT:-${BIAB_AGENTS:-}}" --prefere="${PREFERE_OPT:-${BIAB_PREFERE:-}}")" || true
  case "$out" in
    "agents=claude prefere=claude "*|"agents=codex prefere=codex "*|"agents=claude,codex prefere=claude "*|"agents=claude,codex prefere=codex "*) ;;
    erreur=inconnu) die "--agents takes claude, codex or claude,codex, and --prefere one of the chosen ones (BIAB_AGENTS and BIAB_PREFERE too)." ;;
    erreur=ferme) die "Codex is coming soon on this system. Re-run with --agents claude, or without --agents and BIAB_AGENTS." ;;
    erreur=prefere) die "--prefere (or BIAB_PREFERE) names an agent that is not chosen." ;;
    erreur=fichier:ferme) die "$STATE/agents.json asks for Codex, which is not available on this system yet. Re-run with --agents claude to replace it." ;;
    erreur=fichier:illisible) die "$STATE/agents.json cannot be read, or is a link. It was left untouched: fix it, or re-run with --agents claude (or codex, or claude,codex) to replace it." ;;
    erreur=fichier:droits) die "$STATE/agents.json can be changed by another account. It was left untouched: chmod 600 it, or re-run with --agents claude (or codex, or claude,codex) to replace it." ;;
    erreur=fichier:*) die "$STATE/agents.json is not a choice this kit knows. It was left untouched: re-run with --agents claude (or codex, or claude,codex) to replace it." ;;
    erreur=codex_home) die "CODEX_HOME must be an absolute path." ;;
    erreur=ecriture:*) die "could not write $STATE/agents.json (${out#erreur=ecriture:})." ;;
    *) die "engine/hooks/_agents.py did not answer (see above). Download brain-in-a-box again, then re-run ./install.sh" ;;
  esac
  AGENTS="${out#agents=}"; AGENTS="${AGENTS%% *}"
  PREFERE="${out#* prefere=}"; PREFERE="${PREFERE%% *}"
  case "$AGENTS:$PREFERE" in
    claude:*) label="Claude Code" ;;
    codex:*) label="Codex" ;;
    *:codex) label="Claude Code and Codex (preferred: Codex)" ;;
    *) label="Claude Code and Codex (preferred: Claude Code)" ;;
  esac
  case "$out" in
    *" source=choix ecrit=oui") ok "AI agents: $label, recorded in $STATE/agents.json" ;;
    *" source=choix ecrit=non"|*" source=fichier "*) ok "AI agents: $label, as chosen before ($STATE/agents.json)" ;;
  esac
}
with_agent() { case ",$AGENTS," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
# A new install at a terminal, with nothing chosen, asks: only where Codex can be chosen (one answer
# otherwise), never over a choice made before or an install from before the choice (Claude alone).
agents_question() {
  [ -z "$AGENTS_OPT$PREFERE_OPT${BIAB_AGENTS:-}${BIAB_PREFERE:-}" ] || return 0
  [ ! -e "$STATE/agents.json" ] && [ ! -L "$STATE/agents.json" ] || return 0
  [ ! -e "$STATE/installed.json" ] && [ ! -e "$HOOKS/correction-detector.py" ] || return 0
  python3 -I "$REPO/engine/hooks/_agents.py" ouvert || return 0
  [ -t 0 ] || return 0
  echo "  Your brain works with Claude Code (your Claude subscription), Codex (your ChatGPT subscription), or both."
  if ! ask "Use Codex?"; then AGENTS_OPT=claude; return 0; fi
  if ! ask "Use Claude Code too?"; then AGENTS_OPT=codex; return 0; fi
  AGENTS_OPT=claude,codex
  if ask "Should Codex write the evening summary (no: Claude Code does)?"; then PREFERE_OPT=codex; else PREFERE_OPT=claude; fi
}
# <<< agents

# >>> codex
# Codex, when it is chosen (docs/proposals/codex.md, §3). OpenAI's official installer, for this user
# (no sudo), at the version CI tests, its script checked against a pinned SHA-256 before it runs (it
# then checks the binary's own digest); the kit's block in the global AGENTS.md Codex reads; the
# hooks in its hooks.json, approved for the user, who is told so; gbq's two reads allowed outside its
# sandbox; an AGENTS.md in the vault. What the user has there is merged into or left alone, never
# replaced: hooks.json and config.toml are backed up (0600) first and written in one step.
CODEX_VERSION=0.162.1
CODEX_INSTALLER_URL="https://github.com/openai/codex/releases/download/rust-v$CODEX_VERSION/install.sh"
CODEX_INSTALLER_SHA256=150e3cf675682efeaac115aa3747add3f27887896d04ce6d0b56478d8b428bf6
CODEX_DIR=""   # the CODEX_HOME agents.json records, once Codex is chosen
codex_bin() { if [ -x "$H/.local/bin/codex" ]; then echo "$H/.local/bin/codex"; else command -v codex 2>/dev/null || true; fi; }
codex_older() {  # codex_older <version>: older than CODEX_VERSION, or not a version
  python3 -I -c 'import re, sys
v = lambda s: tuple(int(x) for x in s.split("."))
sys.exit(0 if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]) or v(sys.argv[1]) < v(sys.argv[2]) else 1)' "$1" "$CODEX_VERSION"
}
codex_install() {
  local cb v d from
  cb="$(codex_bin)"
  if [ -n "$cb" ]; then
    v="$("$cb" --version 2>/dev/null | sed -n '1s/^codex-cli \([0-9][0-9.]*\).*/\1/p')"
    if codex_older "$v"; then
      warn "Codex ${v:-of an unknown version} ($cb) is older than the kit needs ($CODEX_VERSION or newer): update it, then re-run ./install.sh"
    else
      ok "Codex already installed ($v)"
    fi
    return 0
  fi
  echo "  … Codex $CODEX_VERSION: OpenAI's official installer (in your home folder, no sudo), checked before it runs"
  d="$(mktemp -d)"
  if ! logged curl -fsSL --retry 2 -o "$d/install.sh" "$CODEX_INSTALLER_URL"; then
    rm -rf "$d"; warn_log "Codex: download failed (network?): re-run ./install.sh"; return 0
  fi
  if [ "$(sha256 "$d/install.sh")" != "$CODEX_INSTALLER_SHA256" ]; then
    rm -rf "$d"; warn "Codex: its installer does not match the pinned SHA-256, nothing run: re-run ./install.sh later"; return 0
  fi
  from=$(( $(wc -c < "$LOG") + 1 ))
  # With the user's own PATH: the installer puts ~/.local/bin in a shell profile when it is not on it.
  if logged env -u VOYAGE_API_KEY -u CODEX_INSTALL_DIR -u CODEX_INSTALL_DAEMON_ONLY -u CODEX_INSTALL_DEFER_SELECTION \
       PATH="$ORIG_PATH" CODEX_HOME="$CODEX_DIR" CODEX_RELEASE="$CODEX_VERSION" CODEX_NON_INTERACTIVE=1 sh "$d/install.sh" \
     && [ -x "$H/.local/bin/codex" ]; then
    ok "Codex $CODEX_VERSION installed ($H/.local/bin/codex)"
    tail -c +"$from" "$LOG" | sed -n -e 's/^==> PATH was added to \(.*\)/    its installer put ~\/.local\/bin on your PATH in \1 (new terminals)/p' \
                                    -e 's/^==> PATH was updated in \(.*\)/    its installer put ~\/.local\/bin on your PATH in \1 (new terminals)/p'
  else
    warn_log "Codex did not install (the log says why): re-run ./install.sh, or see https://developers.openai.com/codex/cli"
  fi
  rm -rf "$d"
}
codex_py() {  # codex_py check|merge|approve|restore|global <codex home> <arg>: hooks.json, config.toml, AGENTS.md
  python3 -I "$REPO/engine/codex/wire.py" "$@"
}
codex_global() {
  local out
  if out="$(codex_py global "$CODEX_DIR" "$REPO/engine/CLAUDE.global.template.md")"; then ok "$out"; else warn "$out"; fi
}
codex_wire() {  # the kit's hooks in hooks.json, then their approval in config.toml, which Codex checks
  local out cb st n bak kept
  out="$(codex_py merge "$CODEX_DIR" "$HOOKS")" || { warn "$out"; return 0; }
  ok "$out"
  python3 -I "$REPO/engine/hooks/_agents.py" pose-hooks >/dev/null || warn "could not note in $STATE when Codex's hooks were wired"
  cb="$(codex_bin)"
  if [ -z "$cb" ]; then
    warn "Codex is not installed, so the kit could not approve its hooks there: re-run ./install.sh once it is (or approve them in Codex: /hooks)"
    return 0
  fi
  if [ -e "$CODEX_DIR/config.toml" ] && ! logged env -u VOYAGE_API_KEY CODEX_HOME="$CODEX_DIR" "$cb" features list; then
    warn "Codex cannot read $CODEX_DIR/config.toml as it is (codex features list fails, see $LOG): the kit did not approve its hooks. Fix it and re-run ./install.sh, or approve them in Codex: /hooks"
    return 0
  fi
  out="$(codex_py approve "$CODEX_DIR" "$HOOKS")" || out="error"
  { read -r st; read -r n; read -r bak; read -r kept; } <<EOF || true
$out
EOF
  case "$st" in
    unchanged)
      ok "Codex: the kit's $n hooks are approved, by the kit for you ($CODEX_DIR/config.toml): see or turn them off with /hooks in Codex" ;;
    written)
      if logged env -u VOYAGE_API_KEY CODEX_HOME="$CODEX_DIR" "$cb" features list; then
        [ "$bak" = - ] && bak="" || bak=", previous version: $bak"
        ok "Codex: the kit approved its $n hooks for you ($CODEX_DIR/config.toml$bak), so Codex runs them without asking. See them, or turn them off, with /hooks in Codex"
      else
        codex_py restore "$CODEX_DIR" "$bak" || true
        warn "Codex refused config.toml with the kit's approval (see $LOG): put back as it was. Approve the kit's hooks in Codex: /hooks"
      fi ;;
    refused)
      warn "Codex: $n, so the kit did not approve its hooks: open Codex, type /hooks and approve them" ;;
    none)
      warn "Codex: none of the kit's hooks is in hooks.json as the kit writes it: nothing approved (see them with /hooks in Codex)" ;;
    *)
      warn "Codex: the kit could not approve its hooks (${out:-no answer}): open Codex, type /hooks and approve them" ;;
  esac
  [ "${kept:--}" = - ] || warn "Codex: the approval you set in /hooks for $kept is kept as you set it"
}
codex_rules() {  # gbq query and gbq search without asking, at the kit's gbq only
  case "$H" in
    *'"'*|*'\'*) warn "Codex: your home folder's path has a quote or a backslash, so gbq is not allowed without asking"; return 0 ;;
  esac
  mkdir -p "$CODEX_DIR/rules"
  place "$REPO/engine/codex/brain-in-a-box.rules" "$CODEX_DIR/rules/brain-in-a-box.rules" \
    "gbq query and gbq search run without asking in Codex ($CODEX_DIR/rules/brain-in-a-box.rules)"
}
codex_vault() {  # codex_vault <vault>: an AGENTS.md at its root when it has none: Codex reads it, never CLAUDE.md
  if [ -e "$1/AGENTS.md" ] || [ -L "$1/AGENTS.md" ]; then ok "$1/AGENTS.md already there (kept as is)"; return 0; fi
  cp "$REPO/engine/codex/AGENTS.vault.md" "$1/AGENTS.md" && ok "$1/AGENTS.md placed: Codex reads the vault's CLAUDE.md through it" \
    || warn "could not write $1/AGENTS.md"
}
# <<< codex

# >>> linux
# Linux only. sudo never runs without a yes typed in a terminal: without one, the
# installer says the exact command and stops (prerequisites) or goes on without
# (Obsidian, lingering). The paths below are variables for test-hooks.sh.
OS_RELEASE=/etc/os-release
SYSTEMD_DIR=/run/systemd/system      # present when systemd runs the machine
RUNTIME_ROOT=/run/user               # <uid>/bus: the user's systemd, when it runs
LINGER_DIR=/var/lib/systemd/linger   # <user>: the user's systemd runs without a session
CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
OBSIDIAN_BINS="/opt/Obsidian/obsidian /snap/bin/obsidian"   # the .deb's, the snap's
UNITS="${XDG_CONFIG_HOME:-$H/.config}/systemd/user"
TIMERS="gbrain-nightly brain-reflection gbrain-lint"
OBSIDIAN_VERSION=1.14.4
OBSIDIAN_DEB_URL="https://github.com/obsidianmd/obsidian-releases/releases/download/v$OBSIDIAN_VERSION/obsidian_${OBSIDIAN_VERSION}_amd64.deb"
OBSIDIAN_DEB_SHA256=85b10dcba6edfc1c0460a6d18260cf31c30447a444bd858a6440b9c9c8806d25
ask() {  # ask <question>: yes only when a person typed it in a terminal
  local a
  [ -t 0 ] || return 1
  read -r -p "  $1 [y/N] " a || return 1
  case "$a" in [yYoO]*) return 0;; *) return 1;; esac
}
debian_like() {  # Debian, Ubuntu and their derivatives (Mint, Pop!_OS…): apt-get is there
  local id like
  # shellcheck source=/dev/null
  id="$(. "$OS_RELEASE" 2>/dev/null; printf '%s' "${ID:-}")"
  # shellcheck source=/dev/null
  like="$(. "$OS_RELEASE" 2>/dev/null; printf '%s' "${ID_LIKE:-}")"
  case " $id $like " in *" debian "*|*" ubuntu "*) command -v apt-get >/dev/null 2>&1;; *) return 1;; esac
}
as_root() {  # the command a person types to run "$@" as root
  if [ "$(id -u)" = 0 ]; then echo "$*"; else echo "sudo $*"; fi
}
missing_packages() {  # Debian package names of what the kit needs and this machine lacks
  local p=""
  command -v python3 >/dev/null 2>&1 || p="$p python3"
  command -v git >/dev/null 2>&1 || p="$p git"
  command -v curl >/dev/null 2>&1 || p="$p curl"
  command -v pgrep >/dev/null 2>&1 || p="$p procps"     # gbq and the nightly tell a running gbrain from a stale lock
  command -v bun >/dev/null 2>&1 || [ -x "$H/.bun/bin/bun" ] || command -v unzip >/dev/null 2>&1 || p="$p unzip"  # Bun's installer
  if debian_like && [ ! -s "$CA_BUNDLE" ]; then p="$p ca-certificates"; fi
  echo "${p# }"
}
linux_prereqs() {
  local pkgs cmd
  if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    die "Run ./install.sh without sudo: it installs in your own home folder, and asks before anything that needs root."
  fi
  pkgs="$(missing_packages)"
  if [ -n "$pkgs" ]; then
    if ! debian_like; then
      die "missing: $pkgs. Install them with your distribution's package manager, then re-run ./install.sh"
    fi
    # Waits for another apt (unattended-upgrades holds dpkg's lock after a first start) instead of failing.
    cmd="$(as_root apt-get update) && $(as_root apt-get -o DPkg::Lock::Timeout=600 install -y "$pkgs")"
    warn "missing: $pkgs"
    if ask "Install them now? This runs: $cmd"; then
      env -u VOYAGE_API_KEY sh -c "$cmd" || die "the install of $pkgs failed (see above)"
    else
      die "Install them with: $cmd   then re-run ./install.sh"
    fi
    pkgs="$(missing_packages)"
    [ -z "$pkgs" ] || die "still missing after apt-get: $pkgs"
  fi
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
    || die "python3 is $(python3 -c 'import platform; print(platform.python_version())'), the kit needs 3.9 or newer"
}
user_systemd() {  # does this user's systemd answer? (no: WSL without systemd, a container, no session)
  command -v systemctl >/dev/null 2>&1 && [ -d "$SYSTEMD_DIR" ] || return 1
  if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -S "$RUNTIME_ROOT/$(id -u)/bus" ]; then
    XDG_RUNTIME_DIR="$RUNTIME_ROOT/$(id -u)"   # a shell without a session (su, a script): the manager runs anyway
    export XDG_RUNTIME_DIR
  fi
  systemctl --user show-environment >/dev/null 2>&1
}
enable_linger() {  # only ever after a yes: as the user (a desktop session may), else through sudo
  if ! loginctl --no-ask-password enable-linger "$U" 2>/dev/null \
     && { [ "$(id -u)" = 0 ] || ! command -v sudo >/dev/null 2>&1 || ! sudo loginctl enable-linger "$U"; }; then
    warn "loginctl enable-linger failed: run $(as_root loginctl enable-linger "$U") then re-run ./install.sh"
    return 1
  fi
  for _ in 1 2 3 4 5 6 7 8 9 10; do user_systemd && return 0; sleep 1; done
  return 0
}
crontab_py() {  # crontab_py <action> <current crontab> <new crontab> <recorded sha256> <pending dir>
  python3 - "$@" <<'PY'
import hashlib, os, sys
action, cur, out, recorded, pending = sys.argv[1:6]
BEGIN, END = "# >>> brain-in-a-box", "# <<< brain-in-a-box"
want = "\n".join([
    BEGIN + ": nightly 04:00, reflection 12:00 and 23:00, lint Monday 08:00 (install.sh; a block you change is kept)",
    '0 4 * * * /bin/bash "$HOME/.claude/hooks/brain/gbrain-nightly.sh" >> "$HOME/.gbrain/nightly.cron.out" 2>&1',
    '0 12,23 * * * /usr/bin/env python3 "$HOME/.claude/hooks/brain/daily-reflection.py" >> "$HOME/.claude/logs/daily-reflection.out" 2>&1',
    '0 8 * * 1 /bin/bash "$HOME/.claude/hooks/brain/gbrain-lint.sh" >> "$HOME/.gbrain/lint.cron.out" 2>&1',
    END])
with open(cur, encoding="utf-8", errors="surrogateescape") as f:
    lines = f.read().split("\n")
while lines and lines[-1] == "":
    lines.pop()
start = next((i for i, l in enumerate(lines) if l.startswith(BEGIN)), None)
end = next((i for i, l in enumerate(lines) if start is not None and i > start and l == END), None)
block = "\n".join(lines[start:end + 1]) if end is not None else None
sha = lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()
mine = block is not None and (block == want or sha(block) == recorded)
if action == "remove":
    status = "absent" if block is None else "removed" if mine else "kept"
    if status == "removed":
        del lines[start:end + 1]
        while lines and lines[-1] == "":   # the blank line "added" put before the block
            lines.pop()
elif block is None:
    status = "added"
    lines += [""] * bool(lines and lines[-1]) + want.split("\n")
elif block == want:
    status = "unchanged"
elif mine:
    status = "updated"
    lines[start:end + 1] = want.split("\n")
else:
    status = "kept"
    os.makedirs(pending, exist_ok=True)
    with open(os.path.join(pending, "crontab"), "w", encoding="utf-8") as f:
        f.write(want + "\n")
with open(out, "w", encoding="utf-8", errors="surrogateescape") as f:
    f.write("\n".join(lines) + "\n" if lines else "")
print(status, sha(want))
PY
}
crontab_block() {  # crontab_block write|remove: the kit's lines between two markers, the rest untouched
  local cur rc tmp out status sha
  if cur="$(crontab -l 2>&1)"; then rc=0; else rc=$?; fi
  if [ "$rc" != 0 ]; then
    case "$cur" in
      *"no crontab for"*) cur="" ;;
      *) warn "crontab -l failed ($cur): your crontab is left as it is"; return 1 ;;
    esac
  fi
  tmp="$(mktemp -d)"
  printf '%s\n' "$cur" > "$tmp/current"
  out="$(crontab_py "$1" "$tmp/current" "$tmp/new" "$(manifest get "crontab#brain-in-a-box")" "$PENDING")" \
    || { rm -rf "$tmp"; warn "could not read your crontab: left as it is"; return 1; }
  status="${out%% *}"; sha="${out#* }"
  case "$status" in
    added|updated|removed)
      if ! crontab "$tmp/new"; then rm -rf "$tmp"; warn "crontab refused the new table: left as it was"; return 1; fi ;;
  esac
  rm -rf "$tmp"
  case "$status" in
    added|updated|unchanged) manifest set "crontab#brain-in-a-box" "$sha" ;;
    kept)
      if [ "$1" = remove ]; then
        warn "crontab: you changed the brain-in-a-box lines, so they stay; beside the systemd timers each job would run twice: remove them (crontab -e)"
      else
        KEPT="$KEPT crontab"
        warn "crontab: the brain-in-a-box lines were changed on this machine, kept as is; the new ones are in $PENDING/crontab"
      fi ;;
  esac
  CRON_STATUS="$status"
}
linux_schedule() {  # systemd --user timers, else cron; the scheduler used goes in $SCHEDULER
  local t
  mkdir -p "$H/.gbrain" "$H/.claude/logs"   # where the units and cron append their output
  if ! user_systemd && [ -d "$SYSTEMD_DIR" ] && command -v loginctl >/dev/null 2>&1 && [ ! -e "$LINGER_DIR/$U" ] \
     && ask "Your user's systemd is not running here (no desktop session). Turn on lingering for $U (loginctl enable-linger; sudo may ask for your password), so it runs the nightly even when you are logged out?"; then
    enable_linger || true
  fi
  if user_systemd; then
    SCHEDULER=systemd
    mkdir -p "$UNITS"
    for t in $TIMERS; do
      place "$REPO/engine/nightly/systemd/$t.service" "$UNITS/$t.service"
      place "$REPO/engine/nightly/systemd/$t.timer" "$UNITS/$t.timer"
    done
    systemctl --user daemon-reload || warn "systemctl --user daemon-reload failed"
    for t in $TIMERS; do   # restart: a timer whose hours changed counts from the new ones
      systemctl --user enable "$t.timer" >/dev/null 2>&1 && systemctl --user restart "$t.timer" \
        || warn "$t.timer could not be enabled: systemctl --user status $t.timer"
    done
    if command -v crontab >/dev/null 2>&1; then crontab_block remove || true; fi  # cron from a run without systemd: never twice
    ok "systemd --user timers on (nightly 04:00, reflection 12:00/23:00, lint Monday 08:00); a time missed while the computer was off runs at the next start"
    if [ -e "$LINGER_DIR/$U" ]; then
      ok "lingering is on: they run even when you are logged out"
    elif ask "Run them even when you are logged out, as a server would (loginctl enable-linger $U; sudo may ask for your password)?"; then
      enable_linger && ok "lingering is on: they run even when you are logged out"
    else
      ok "they run while you are logged in (a missed one runs at your next login). To run them logged out too: loginctl enable-linger $U"
    fi
    return 0
  fi
  SCHEDULER=cron
  if [ -e "$UNITS/timers.target.wants/gbrain-nightly.timer" ]; then
    warn "the systemd timers of an earlier install are on, but your user's systemd does not answer from here: cron is left alone, so nothing runs twice. Re-run ./install.sh from your desktop session to update them."
    SCHEDULER=systemd-unreachable
    return 0
  fi
  if ! command -v crontab >/dev/null 2>&1; then
    warn "no systemd for your user here (WSL without systemd? a container?) and no cron: nothing is scheduled. Install cron ($(as_root apt-get install -y cron)) and re-run ./install.sh, or run the nightly by hand: bash ~/.claude/hooks/brain/gbrain-nightly.sh"
    SCHEDULER=none
    return 0
  fi
  CRON_STATUS=""
  crontab_block write || { SCHEDULER=none; return 0; }
  ok "cron, as systemd does not run for your user here (WSL? a container?): nightly 04:00, reflection 12:00/23:00, lint Monday 08:00 ($CRON_STATUS)"
  if [ -d "$SYSTEMD_DIR" ]; then
    warn "cron does not catch up a time the computer was off. systemd runs this machine, but not for $U from here (no session): $(as_root loginctl enable-linger "$U"), then re-run ./install.sh, moves these jobs to systemd timers that do"
  else
    warn "cron does not catch up a time the computer was off; with systemd it would (WSL: systemd=true in /etc/wsl.conf)"
  fi
  if ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
    warn "the cron service is not running: start it ($(as_root service cron start)), or nothing runs"
  fi
}
claude_found() { command -v claude >/dev/null 2>&1 || [ -x "$H/.local/bin/claude" ]; }
linux_claude() {  # Anthropic's official installer, for this user only: it checks the binary's SHA-256 itself
  if claude_found; then ok "Claude Code already installed"; return 0; fi
  echo "  … Claude Code: Anthropic's official installer (in your home folder, no sudo)"
  if logged env -u VOYAGE_API_KEY bash -o pipefail -c 'curl -fsSL https://claude.ai/install.sh | bash' && claude_found; then
    ok "Claude Code installed ($H/.local/bin/claude)"
  else
    warn_log "Claude Code did not install (the log says why): re-run ./install.sh, or see https://code.claude.com/docs/en/setup"
  fi
}
obsidian_found() {  # the .deb, a snap, a Flatpak, an AppImage in ~/Applications or ~/.local/bin
  local f
  command -v obsidian >/dev/null 2>&1 && return 0
  if command -v flatpak >/dev/null 2>&1 && flatpak info md.obsidian.Obsidian >/dev/null 2>&1; then return 0; fi
  for f in $OBSIDIAN_BINS "$H"/Applications/Obsidian*.AppImage "$H"/.local/bin/Obsidian*.AppImage; do [ -e "$f" ] && return 0; done
  return 1
}
obsidian_line() {  # one line a person can paste: download, check, install
  printf 'd=$(mktemp -d) && curl -fL -o "$d/obsidian.deb" %s && echo "%s  $d/obsidian.deb" | sha256sum -c - && %s "$d/obsidian.deb"' \
    "$OBSIDIAN_DEB_URL" "$OBSIDIAN_DEB_SHA256" "$(as_root apt-get install -y)"
}
linux_obsidian() {
  # The official .deb, as root: /opt/Obsidian with its SUID chrome-sandbox, which Ubuntu 24.04's
  # AppArmor profile also expects. The per-user AppImage needs libfuse2 (gone from 24.04) and runs
  # there only with --no-sandbox (unprivileged user namespaces are restricted): not for notes.
  local d f
  if obsidian_found; then ok "Obsidian already installed"; return 0; fi
  if ! debian_like || [ "$(dpkg --print-architecture 2>/dev/null)" != amd64 ]; then
    warn "Obsidian not installed: get it at https://obsidian.md/download, or for you alone from Flathub: flatpak install --user flathub md.obsidian.Obsidian"
    return 0
  fi
  if ! ask "Install Obsidian $OBSIDIAN_VERSION (its official .deb, checked against a pinned SHA-256) with $(as_root apt-get install)?"; then
    warn "Obsidian not installed. Its official package, checked, in one line:"
    echo "      $(obsidian_line)"
    return 0
  fi
  d="$(mktemp -d)"; f="$d/obsidian_${OBSIDIAN_VERSION}_amd64.deb"
  if ! logged curl -fL --retry 2 -o "$f" "$OBSIDIAN_DEB_URL"; then
    rm -rf "$d"; warn_log "Obsidian: download failed (network?)"; return 0
  fi
  if [ "$(sha256 "$f")" != "$OBSIDIAN_DEB_SHA256" ]; then
    rm -rf "$d"; warn "Obsidian: the download does not match the pinned SHA-256, nothing installed"; return 0
  fi
  chmod 755 "$d"; chmod 644 "$f"   # apt reads it as its own user
  if env -u VOYAGE_API_KEY sh -c "$(as_root apt-get -o DPkg::Lock::Timeout=600 install -y) \"\$1\"" sh "$f"; then ok "Obsidian $OBSIDIAN_VERSION installed"; else warn "Obsidian: apt-get failed (see above)"; fi
  rm -rf "$d"
}
# <<< linux

# ── 1. Preflight ────────────────────────────────────────────────────────────
say "Preflight"
case "$OS" in
  Darwin) ;;
  Linux) linux_prereqs ;;
  *) die "macOS or Linux required (Windows: install.ps1)." ;;
esac
command -v git >/dev/null   || die "git missing."
command -v python3 >/dev/null || die "python3 missing."
agents_question
choose_agents                 # a choice refused stops us before anything is changed
if with_agent claude; then
  merge_settings "$H" --check   # a settings.json we cannot read stops us before anything is changed
fi
if with_agent codex; then       # nor a hooks.json of Codex's
  CODEX_DIR="$(python3 -I "$REPO/engine/hooks/_agents.py" codex-home)" || die "$STATE/agents.json does not say where Codex lives: re-run ./install.sh with --agents"
  out="$(codex_py check "$CODEX_DIR" "$HOOKS")" || die "$out"
fi
mkdir -p "$STATE"
( umask 077; : >> "$LOG" ); chmod 600 "$LOG"
printf '\n===== install.sh %s =====\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
if with_agent claude; then
  [ "$OS" = Linux ] || command -v claude >/dev/null || warn "claude CLI not found — install Claude Code first."
fi
# Bun at the version CI tests with gbrain (engine/gbrain-pin.json), through its official installer.
BUN_PIN="$(python3 -I -c 'import json, re, sys; v = json.load(open(sys.argv[1])).get("bun") or ""; print(v if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", v) else "")' "$REPO/engine/gbrain-pin.json" 2>/dev/null || true)"
export PATH="$H/.bun/bin:$H/.local/bin:$PATH"
if ! command -v bun >/dev/null; then
  warn "bun missing → installing${BUN_PIN:+ $BUN_PIN}…"
  curl -fsSL https://bun.sh/install | env -u VOYAGE_API_KEY bash -s ${BUN_PIN:+"bun-v$BUN_PIN"}
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
sha256() {  # sha256 [file]: coreutils' sha256sum (Linux), else shasum (macOS)
  if command -v sha256sum >/dev/null 2>&1; then sha256sum < "${1:-/dev/stdin}"; else shasum -a 256 < "${1:-/dev/stdin}"; fi | cut -d' ' -f1
}
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
  case "$name" in *.plist|*.service|*.timer|*.rules) mode=644;; esac
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

# ── 7. Scheduled jobs (nightly maintenance + daily reflection + weekly lint) ──
# macOS: launchd. Linux: systemd --user timers, else cron (WSL without systemd, containers).
load_agent() {  # <label> <template>; a plist you changed (another hour) is kept, like the hooks
  local label="$1" plist="$H/Library/LaunchAgents/$1.plist"
  place "$REPO/engine/nightly/$2" "$plist" "$label"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || true
}
SCHEDULER=launchd
if [ "$OS" = Linux ]; then
  say "Scheduled jobs (nightly 04:00 + reflection 12:00/23:00 + lint Monday 08:00)"
  linux_schedule
else
  say "launchd (nightly 04:00 + reflection 12:00/23:00 + lint Monday 08:00)"
  mkdir -p "$H/Library/LaunchAgents"
  load_agent "com.$U.gbrain-nightly"   "com.USER.gbrain-nightly.plist.template"
  load_agent "com.$U.brain-reflection" "com.USER.brain-reflection.plist.template"
  load_agent "com.$U.gbrain-lint"      "com.USER.gbrain-lint.plist.template"
fi
if [ "$BACKED_UP" -gt 0 ]; then warn "$BACKED_UP file(s) you had changed were replaced; your versions are in $BACKUP"; fi

# ── 8-9bis. Claude Code: its hooks, CLAUDE.md and skills, when it is chosen ─────
if with_agent claude; then
  # ── 8. settings.json (merge hooks, non-destructive) ───────────────────────
  say "Registering hooks (~/.claude/settings.json)"
  merge_settings "$H"

  # ── 9. Global CLAUDE.md (append, never overwrite) ──────────────────────────
  say "Global CLAUDE.md"
  GC="$H/.claude/CLAUDE.md"
  MARK="<!-- brain-in-a-box -->"
  if [ -f "$GC" ] && grep -q "$MARK" "$GC"; then
    ok "already present (skip)"
  else
    { echo; echo "$MARK"; cat "$REPO/engine/CLAUDE.global.template.md"; } >> "$GC"
    ok "appended (your existing content preserved)"
  fi

  # ── 9bis. Claude Code skills (never overwrites one of yours, see the script)
  say "Claude Code skills"
  python3 "$REPO/engine/install_skills.py" --src "$REPO/skills" --dest "$H/.claude/skills" \
    && ok "skills up to date" || warn "skills: see above"
fi

# ── 9ter. Codex: installed when missing, its AGENTS.md, hooks and skills, when it is chosen ──
if with_agent codex; then
  say "Codex ($CODEX_DIR)"
  ( umask 077; mkdir -p "$CODEX_DIR" ) || die "could not create $CODEX_DIR"   # where auth.json goes: private
  codex_install
  codex_global
  codex_wire
  codex_rules
  codex_vault "$BRAIN"
  say "Codex skills (~/.agents/skills)"
  python3 "$REPO/engine/install_skills.py" --src "$REPO/skills" --dest "$H/.agents/skills" --manifest "$STATE/skills-codex.json" \
    && ok "skills up to date" || warn "skills: see above"
fi

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
if [ "$OS" = Linux ]; then
  if with_agent claude; then
    say "Claude Code + Obsidian (to browse your brain)"
    linux_claude
  else
    say "Obsidian (to browse your brain)"
  fi
  linux_obsidian
elif [ -d "/Applications/Obsidian.app" ] || [ -d "$H/Applications/Obsidian.app" ]; then
  say "Obsidian (to browse your brain)"
  ok "already installed"
elif command -v brew >/dev/null; then
  say "Obsidian (to browse your brain)"
  env -u VOYAGE_API_KEY brew install --cask obsidian >/dev/null 2>&1 && ok "installed via brew" || warn "brew install failed — get it free at https://obsidian.md"
else
  say "Obsidian (to browse your brain)"
  warn "not installed — download free at https://obsidian.md"
fi

# ── 14. GStack (optional — 23 AI specialists for Claude Code) ───────────────
if [ -n "$WITH_GSTACK" ] && ! with_agent claude; then
  warn "--with-gstack: gstack is made for Claude Code, which is not chosen here: skipped"
elif [ -n "$WITH_GSTACK" ]; then
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
BIAB_FROM_INSTALL=1 bash "$REPO/test-hooks.sh" >/dev/null 2>&1 && ok "hooks + gbrain self-update healthy" || warn "hook check reported issues — run ./test-hooks.sh to see them"

# ── Done ─────────────────────────────────────────────────────────────────────
printf "\n\033[1;32m✅ brain-in-a-box installed.\033[0m\n"
if [ "$BACKED_UP" -gt 0 ]; then
  printf "\033[1;33m⚠ Your previous versions of %s replaced file(s) are in %s\033[0m\n" "$BACKED_UP" "$BACKUP"
fi
if [ -n "$KEPT" ]; then
  printf "\033[1;33m⚠ Kept as you changed them:%s\n  The new versions are in %s: compare (diff), keep what you want.\033[0m\n" "$KEPT" "$PENDING"
fi
case "$SCHEDULER" in
  launchd)  SCHED_CHECK="launchctl list | grep $U          # the nightly, reflection and lint jobs" ;;
  systemd)  SCHED_CHECK="systemctl --user list-timers         # when the nightly, reflection and lint run next" ;;
  cron)     SCHED_CHECK="crontab -l                           # the nightly, reflection and lint (cron)" ;;
  *)        SCHED_CHECK="(nothing scheduled: see the warning above)" ;;
esac
LOCAL_TOOLS="gbq, controle"
if with_agent codex; then LOCAL_TOOLS="codex, $LOCAL_TOOLS"; fi
if with_agent claude; then LOCAL_TOOLS="claude, $LOCAL_TOOLS"; fi
case ":$ORIG_PATH:" in
  *":$H/.local/bin:"*) ;;
  *) [ "$OS" = Linux ] && printf "\n\033[1;33m⚠ ~/.local/bin ($LOCAL_TOOLS) is not on your PATH yet: log out and back in (it is added at login once it exists), or run: export PATH=\"\$HOME/.local/bin:\$HOME/.bun/bin:\$PATH\"\033[0m\n" ;;
esac
# The onboarding opens the preferred agent in the vault.
if with_agent claude && [ "$PREFERE" = claude ]; then
  cat <<EOF

Last step — the ONBOARDING (15 min) that fills in YOUR profile:
  cd $BRAIN && claude
  then paste:  "Read $REPO/onboarding/ONBOARDING.md and run it to personalize me."
EOF
fi
if with_agent codex; then
  cat <<EOF

Sign in to Codex with your ChatGPT account (the kit never uses an API key: it would be billed):
  codex login
EOF
fi
if [ "$PREFERE" = codex ]; then
  cat <<EOF

Last step — the ONBOARDING (15 min) that fills in YOUR profile:
  cd $BRAIN && codex
  then paste:  "Read $REPO/onboarding/ONBOARDING.md and run it to personalize me."
EOF
fi
# An agent chosen before and not any more: what the kit put in place for it stays (it has no uninstall).
# Codex's, only once the kit has wired it (codex-hooks-poses.json): a Codex of the user's own is not read.
if ! with_agent codex && [ -f "$STATE/codex-hooks-poses.json" ] && grep -qF "/.claude/hooks/brain/" "${CODEX_HOME:-$H/.codex}/hooks.json" 2>/dev/null; then
  printf "\n\033[1;33m⚠ Codex is not chosen any more: what the kit put in place for it stays, and its hooks keep quiet. To remove it: /hooks in Codex, the block after <!-- brain-in-a-box --> in its AGENTS.md, and the skills %s lists in ~/.agents/skills.\033[0m\n" "$STATE/skills-codex.json"
fi
if ! with_agent claude && grep -qF "/.claude/hooks/brain/" "$H/.claude/settings.json" 2>/dev/null; then
  printf "\n\033[1;33m⚠ Claude Code is not chosen any more: its hooks in ~/.claude/settings.json, the block after <!-- brain-in-a-box --> in ~/.claude/CLAUDE.md and the skills %s lists in ~/.claude/skills stay, and run if you open Claude Code. To remove them: /hooks in Claude Code, that block, those skills.\033[0m\n" "$STATE/skills.json"
fi
cat <<EOF

Browse your brain visually in Obsidian:
  open it → "Open folder as vault" → $BRAIN

Checks:
  ./test-hooks.sh                         # verify all hooks + gbrain self-update work
  ~/.local/bin/gbq query "test"          # the memory answers
  gbrain doctor --fast                    # health (aim for 85+)
  cat ~/.gbrain/nightly.log              # tomorrow morning: the nightly ran
  $SCHED_CHECK
  tail ~/.brain-in-a-box/install.log     # what clone, bun and gbrain said during this install
EOF
