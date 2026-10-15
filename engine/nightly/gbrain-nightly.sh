#!/usr/bin/env bash
# brain-in-a-box nightly maintenance (generic, no Hermes).
# Invoked at 04:00 by launchd on macOS (com.<user>.gbrain-nightly, with /bin/zsh), by
# systemd --user (gbrain-nightly.timer) or cron on Linux (with /bin/bash): written for
# both shells. Absolute paths on purpose: the scheduler starts without the user shell PATH.
#
# Steps: stale-lock recovery → self-update GBrain → night check of the vault
# (controle.py) → commit + push personal vault → sync personal
# (gbrain-sync-vault.py) → [if company vault present: git pull + sync 'company'
# source] → dream cycle.

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/local/bin"
# gbrain is third-party code, updated every week: never the Claude setup-token. The night check
# and the reflection read it from ~/.brain-in-a-box/claude-oauth-token when they ask Claude.
unset CLAUDE_CODE_OAUTH_TOKEN
GBRAIN="$HOME/.bun/bin/gbrain"
VAULT="$HOME/Documents/Brain"
VAULT_CO="$HOME/Documents/BrainCo"
LOG="$HOME/.gbrain/nightly.log"
HERE="$(cd "$(dirname "$0")" >/dev/null && pwd -P)"   # absolute: the steps below cd
SYNC_VAULT="$HERE/gbrain-sync-vault.py"
CONTROLE="$HERE/controle.py"

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

# At 04:00 a laptop is often just waking up: launchd fires before the Wi-Fi is
# back, so every remote step dies on "Could not resolve host" — and since the
# cycle is best-effort, nothing says so. Field case: the vault's origin sat 3
# days behind with no error anywhere. Wait (up to 5 min) for DNS first.
wait_for_net() {
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if python3 -c 'import socket; socket.getaddrinfo("github.com", 443)' 2>/dev/null; then
            [ "$i" -gt 1 ] && echo "[net] github.com resolved after $i attempt(s)" >> "$LOG"
            return 0
        fi
        sleep 30
    done
    echo "[net] github.com still unresolvable after 5 min — remote steps will fail" >> "$LOG"
    return 1
}

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly start =====" >> "$LOG"
wait_for_net; NET_OK=$?

# -1. Stale-lock recovery (a gbrain killed with SIGKILL leaves a .gbrain-lock that blocks everything).
if ! pgrep -f "bun.*gbrain" >/dev/null 2>&1; then
    L="$HOME/.gbrain/brain.pglite/.gbrain-lock"
    [ -d "$L" ] && mv "$L" "${L}.stale-$(date +%s)" 2>/dev/null && echo "[lock] rotated stale lock" >> "$LOG"
fi
sweep_git_lock "$VAULT"
[ -d "$VAULT_CO/.git" ] && sweep_git_lock "$VAULT_CO"

# 0. Update GBrain to its newest release that is at least 7 days old, with a
# full rollback on a broken one: gbrain-selfupdate.sh relays to gbrain_update.py
# (also started by the SessionStart catch-up hook when no check succeeded for
# 36 h). Resilient: a failure must never block the rest of the cycle.
"$HERE/gbrain-selfupdate.sh" "$LOG"

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

# 0quater. Night check of the vault: commits the day's notes, fixes what is safe
# (one commit per fix, each one undoable) and writes Profile/controle.md; the
# SessionStart hook says it the next morning. It never pushes: step 1 does, and
# runs whatever happened here, a crash included.
if [ -f "$CONTROLE" ]; then
    python3 -I "$CONTROLE" nuit --brain "$VAULT" >> "$LOG" 2>&1
    echo "[controle] exit $?" >> "$LOG"
fi

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
# A failed push is retried and logged with its real cause: nothing downstream
# ever notices that origin fell behind.
if cd "$VAULT" 2>/dev/null && git remote get-url origin >/dev/null 2>&1; then
    if [ "$NET_OK" -ne 0 ]; then
        echo "[git] vault push SKIPPED — no network (commits stay local, origin falls behind)" >> "$LOG"
    else
        for attempt in 1 2 3; do
            if GIT_TERMINAL_PROMPT=0 git push origin HEAD >> "$LOG" 2>&1; then
                echo "[git] vault pushed to origin (attempt $attempt)" >> "$LOG"
                break
            fi
            if [ "$attempt" -eq 3 ]; then
                echo "[git] vault push FAILED after 3 attempts — origin is now behind, see errors above" >> "$LOG"
            else
                sleep 60
            fi
        done
    fi
fi
# Import and embed are split on purpose (2026-07-16). `sync` with its built-in
# embed fails on most files with "[embed(zeroentropyai:zembed-1)] Invalid JSON
# response", reported as "N file(s) failed to parse" — a lie: the parse is fine.
# Only sync's inline embed path fails; the same texts embed cleanly on their
# own. Left unsplit, the RAG silently stops ingesting the vault for weeks.
# Re-test `sync` alone after a gbrain upgrade; drop this split once fixed.
# Which source holds the vault depends on the brain's age: gbrain-sync-vault.py
# reads `gbrain sources list`, syncs the right one, and repairs a brain whose
# vault was imported into gbrain's own folder (there `sync --repo` fails).
if [ -f "$SYNC_VAULT" ]; then
    python3 "$SYNC_VAULT" "$VAULT" >> "$LOG" 2>&1
else
    "$GBRAIN" sync --repo "$VAULT" --no-pull --no-embed >> "$LOG" 2>&1
fi
# Since gbrain 0.60.106 a paid run needs approval: without one, embed exits 3
# ("confirmation_required") and nothing is embedded. The cap covers a normal
# day of notes many times over (about 1.6M tokens at list price).
"$GBRAIN" embed --stale --max-usd 0.10 >> "$LOG" 2>&1

# 2. COMPANY vault (team mode): pull teammates' contributions + sync the 'company' source.
if [ -d "$VAULT_CO/.git" ]; then
    git -C "$VAULT_CO" pull --ff-only >> "$LOG" 2>&1 && echo "[git] company vault pull OK" >> "$LOG" || echo "[git] company vault pull skip (conflict?)" >> "$LOG"
    "$GBRAIN" sync --source company >> "$LOG" 2>&1 && echo "[sync] company source OK" >> "$LOG"
fi

# 3. Dream cycle (dedup, facts, consolidation, embed, purge).
"$GBRAIN" dream >> "$LOG" 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly done =====" >> "$LOG"
