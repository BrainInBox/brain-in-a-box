#!/bin/zsh
# brain-in-a-box nightly maintenance (generic, no Hermes).
# Invoked by launchd (com.<user>.gbrain-nightly) at 04:00. Absolute paths on
# purpose: launchd starts without the user shell PATH.
#
# Steps: stale-lock recovery → self-update GBrain → commit personal vault →
# sync personal → [if company vault present: git pull + sync 'company' source] → dream cycle.

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin"
GBRAIN="$HOME/.bun/bin/gbrain"
BUN="$HOME/.bun/bin/bun"
VAULT="$HOME/Documents/Brain"
VAULT_CO="$HOME/Documents/BrainCo"
GREPO="$HOME/DEV/gbrain"
LOG="$HOME/.gbrain/nightly.log"

# Sweep an orphan .git/index.lock that silently breaks `git commit` (a crashed
# git leaves it behind → the vault never commits → gbrain sync stalls on "No
# commits in repo" → the index freezes, unnoticed). Only remove it when no git
# runs in the repo AND the lock is >5 min old (a fresh one may be a legit
# concurrent commit). Root-caused 2026-06-28: a 14-day-old lock froze the index.
sweep_git_lock() {
    local repo="$1" lock="$1/.git/index.lock"
    [ -f "$lock" ] || return 0
    if ! pgrep -f "[g]it .*$repo" >/dev/null 2>&1 && [ -z "$(find "$lock" -mmin -5 2>/dev/null)" ]; then
        rm -f "$lock" && echo "[lock] removed stale .git/index.lock in $repo" >> "$LOG"
    fi
}

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly start =====" >> "$LOG"

# -1. Stale-lock recovery (a gbrain killed with SIGKILL leaves a .gbrain-lock that blocks everything).
if ! pgrep -f "bun.*gbrain" >/dev/null 2>&1; then
    L="$HOME/.gbrain/brain.pglite/.gbrain-lock"
    [ -d "$L" ] && mv "$L" "${L}.stale-$(date +%s)" 2>/dev/null && echo "[lock] rotated stale lock" >> "$LOG"
fi
sweep_git_lock "$VAULT"
[ -d "$VAULT_CO/.git" ] && sweep_git_lock "$VAULT_CO"

# 0. Self-update GBrain — pull, install, migrate, smoke-test, auto-rollback on
# a broken build. Extracted to gbrain-selfupdate.sh (also invoked by the
# SessionStart catch-up hook on days the machine missed this 04:00 run).
# Resilient: a failure must never block the rest of the cycle.
"$(dirname "$0")/gbrain-selfupdate.sh" "$LOG"

# 0bis. Self-update gstack (if installed via --with-gstack). Same shape: pull + re-setup if HEAD moved.
GSTACK="$HOME/.claude/skills/gstack"
if [ -d "$GSTACK/.git" ] && cd "$GSTACK" 2>/dev/null; then
    g_before=$(git rev-parse --short HEAD 2>/dev/null)
    if git pull --ff-only >> "$LOG" 2>&1; then
        g_after=$(git rev-parse --short HEAD 2>/dev/null)
        if [ "$g_before" != "$g_after" ]; then
            echo "[update] gstack $g_before -> $g_after" >> "$LOG"
            ./setup >> "$LOG" 2>&1 || echo "[update] gstack setup non-fatal" >> "$LOG"
        fi
    fi
fi

# 0ter. Link-graph resolution: idempotent, upgrades installs that predate the
# flag (see install.sh — without it every skeleton dir is outside gbrain's
# entity-dir whitelist and wikilinks are silently dropped: empty graph).
"$GBRAIN" config set link_resolution.global_basename true >> "$LOG" 2>&1

# 1. Commit the personal vault first (sync is git-diff based → without a commit, edits are invisible).
if cd "$VAULT" 2>/dev/null && [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    git add -A >> "$LOG" 2>&1
    git -c user.email="brain@local" -c user.name="brain" commit -q -m "nightly $(date '+%Y-%m-%d')" >> "$LOG" 2>&1 \
        && echo "[git] personal vault committed" >> "$LOG"
fi
# 1bis. Back up the vault off-machine. The vault is the only copy otherwise
# (local commits aren't worth much if the disk dies). Best-effort: a remote may
# be absent and a cron may lack creds — never block the cycle.
# GIT_TERMINAL_PROMPT=0 so a missing credential fails fast instead of hanging.
if cd "$VAULT" 2>/dev/null && git remote get-url origin >/dev/null 2>&1; then
    GIT_TERMINAL_PROMPT=0 git push origin HEAD >> "$LOG" 2>&1 \
        && echo "[git] vault pushed to origin" >> "$LOG" \
        || echo "[git] vault push skipped (no remote creds in cron?)" >> "$LOG"
fi
# Import and embed are split on purpose (2026-07-16). `sync` with its built-in
# embed fails on most files with "[embed(zeroentropyai:zembed-1)] Invalid JSON
# response", reported as "N file(s) failed to parse" — a lie: the parse is fine.
# Only sync's inline embed path fails; the same texts embed cleanly on their
# own. Left unsplit, the RAG silently stops ingesting the vault for weeks.
# Re-test `sync` alone after a gbrain upgrade; drop this split once fixed.
"$GBRAIN" sync --repo "$VAULT" --no-pull --no-embed >> "$LOG" 2>&1
"$GBRAIN" embed --stale >> "$LOG" 2>&1

# 2. COMPANY vault (team mode): pull teammates' contributions + sync the 'company' source.
if [ -d "$VAULT_CO/.git" ]; then
    git -C "$VAULT_CO" pull --ff-only >> "$LOG" 2>&1 && echo "[git] company vault pull OK" >> "$LOG" || echo "[git] company vault pull skip (conflict?)" >> "$LOG"
    "$GBRAIN" sync --source company >> "$LOG" 2>&1 && echo "[sync] company source OK" >> "$LOG"
fi

# 3. Dream cycle (dedup, facts, consolidation, embed, purge).
"$GBRAIN" dream >> "$LOG" 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly done =====" >> "$LOG"
