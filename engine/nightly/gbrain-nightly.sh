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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly start =====" >> "$LOG"

# -1. Stale-lock recovery (a gbrain killed with SIGKILL leaves a .gbrain-lock that blocks everything).
if ! pgrep -f "bun.*gbrain" >/dev/null 2>&1; then
    L="$HOME/.gbrain/brain.pglite/.gbrain-lock"
    [ -d "$L" ] && mv "$L" "${L}.stale-$(date +%s)" 2>/dev/null && echo "[lock] rotated stale lock" >> "$LOG"
fi

# 0. Self-update GBrain (resilient: a failure must never block the cycle).
if cd "$GREPO" 2>/dev/null; then
    before=$(git rev-parse --short HEAD 2>/dev/null)
    if git pull --ff-only >> "$LOG" 2>&1; then
        after=$(git rev-parse --short HEAD 2>/dev/null)
        if [ "$before" != "$after" ]; then
            echo "[update] gbrain $before -> $after" >> "$LOG"
            "$BUN" install >> "$LOG" 2>&1 && "$BUN" link >> "$LOG" 2>&1
            "$GBRAIN" apply-migrations --yes >> "$LOG" 2>&1 || echo "[update] migrations non-fatal" >> "$LOG"
        fi
    fi
fi

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

# 1. Commit the personal vault first (sync is git-diff based → without a commit, edits are invisible).
if cd "$VAULT" 2>/dev/null && [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    git add -A >> "$LOG" 2>&1
    git -c user.email="brain@local" -c user.name="brain" commit -q -m "nightly $(date '+%Y-%m-%d')" >> "$LOG" 2>&1 \
        && echo "[git] personal vault committed" >> "$LOG"
fi
"$GBRAIN" sync --repo "$VAULT" --no-pull >> "$LOG" 2>&1

# 2. COMPANY vault (team mode): pull teammates' contributions + sync the 'company' source.
if [ -d "$VAULT_CO/.git" ]; then
    git -C "$VAULT_CO" pull --ff-only >> "$LOG" 2>&1 && echo "[git] company vault pull OK" >> "$LOG" || echo "[git] company vault pull skip (conflict?)" >> "$LOG"
    "$GBRAIN" sync --source company >> "$LOG" 2>&1 && echo "[sync] company source OK" >> "$LOG"
fi

# 3. Dream cycle (dedup, facts, consolidation, embed, purge).
"$GBRAIN" dream >> "$LOG" 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly done =====" >> "$LOG"
